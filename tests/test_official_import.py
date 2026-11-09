from __future__ import annotations

import base64
import csv
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import Request,urlopen
from unittest.mock import patch

import test_gui as gui_fixture
from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryService,LibraryError
from dance_results.migrations_v4 import migrate_v3
from dance_results.migrations_v5 import migrate_v4
from dance_results.official_import import read_catalog,preview_catalog,import_catalog,REQUIRED,_insert_song,main
from dance_results.scanner import scan_library
from dance_results.sqlite_safety import audit,projection,row_limits


def csv_bytes(rows):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=[*REQUIRED,'enc','hidden'])
    writer.writeheader();writer.writerows(rows);return b'\xef\xbb\xbf'+stream.getvalue().encode('utf-8')


def raw_row(**changes):
    return {'fileId':'10026','title':'同名 JOINT☆','artist':'川田まみ','bpm':'130.50','mode':'K','gn':'a.gn',
            'lvEasy':'5','notesEasy':'279','lvNormal':'8','notesNormal':'399','lvHard':'9','notesHard':'501',
            'enc':'sdom','hidden':'',**changes}


def sample_catalog():
    return read_catalog(csv_bytes([raw_row(),raw_row(gn='b.gn',artist='Kawada Mami',enc='rewu'),
        raw_row(fileId='12140',gn='c.gn',title='動畫歌曲串燒',lvEasy='26',notesEasy='3417',lvNormal='0',notesNormal='0',lvHard='0',notesHard='0',hidden='1'),
        raw_row(mode='T',fileId='26',bpm='invalid',lvHard='broken'),raw_row(mode='',fileId='1',bpm='')]),'song_table.csv')


class OfficialImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.path=self.base/'library.sqlite3'
        self.root=self.base/'SONG'
        for folder in (self.root/'Pack'/'Song',self.root/'connect'/'Download'):
            folder.mkdir(parents=True);(folder/'song.sm').write_text('#TITLE:同名 JOINT☆;#ARTIST:作者;',encoding='utf-8')
        with LibraryDatabase(self.path) as library:
            report=scan_library(self.root);library.sync(report);self.root_id=report.database['root_id']
            self.limits=row_limits(library.connection);self.before=audit(library.connection,limits=self.limits)
        self.catalog=sample_catalog()

    def tearDown(self):self.temp.cleanup()

    def test_only_k_duplicate_file_ids_separate_ids_unicode_bpm_and_absent_difficulties(self):
        result=import_catalog(self.path,self.catalog)
        self.assertEqual((result['keyboard_rows'],result['excluded_rows'],result['valid_difficulties']),(3,2,7))
        self.assertEqual(result['changes']['new_songs'],3)
        with LibraryDatabase(self.path) as library:
            db=library.connection
            self.assertEqual(audit(db,limits=self.limits),self.before)
            self.assertEqual(db.execute('SELECT count(*) FROM packs').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM song_sources').fetchone()[0],2)
            sources=db.execute('SELECT file_id,gn,song_id,bpm_raw,mode FROM official_song_sources ORDER BY official_source_id').fetchall()
            self.assertEqual([r['file_id'] for r in sources],['10026','10026','12140'])
            self.assertEqual(len({r['song_id'] for r in sources}),3)
            self.assertTrue(all(r['bpm_raw']=='130.50' and r['mode']=='K' for r in sources))
            self.assertEqual(db.execute('SELECT count(*) FROM game_difficulties WHERE song_id=?',(sources[2]['song_id'],)).fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_repeat_same_csv_reordered_rows_and_new_download_preserve_existing_values(self):
        first=import_catalog(self.path,self.catalog)
        with LibraryDatabase(self.path) as library:before=audit(library.connection)
        second=import_catalog(self.path,self.catalog)
        reordered=read_catalog(csv_bytes([r['raw'] for r in reversed(self.catalog.rows)]),'moved.csv')
        third=import_catalog(self.path,reordered)
        self.assertEqual((second['changes']['new_songs'],third['changes']['new_difficulties']),(0,0))
        with LibraryDatabase(self.path) as library:self.assertEqual(audit(library.connection),before)
        folder=self.root/'connect'/'New';folder.mkdir();(folder/'song.sm').write_text('#TITLE:New;',encoding='utf-8')
        with LibraryDatabase(self.path) as library:
            report=scan_library(self.root);library.sync(report)
            self.assertEqual(report.database['changes']['new_songs'],1)
            self.assertEqual(library.connection.execute('SELECT count(*) FROM official_song_sources').fetchone()[0],3)
            self.assertEqual(library.connection.execute('SELECT count(*) FROM game_difficulties').fetchone()[0],7)
            self.assertEqual(library.connection.execute("SELECT count(*) FROM songs WHERE source_type='official' AND is_present=0").fetchone()[0],0)

    def test_source_changes_and_manual_values_cannot_be_overwritten(self):
        import_catalog(self.path,self.catalog)
        changed=read_catalog(csv_bytes([raw_row(title='Changed')]),'changed.csv')
        self.assertTrue(preview_catalog(self.path,changed)['changes']['conflicts'])
        with self.assertRaisesRegex(ValueError,'衝突'):import_catalog(self.path,changed)
        with LibraryDatabase(self.path) as library:
            library.connection.execute("UPDATE game_difficulties SET game_level=77,level_source='manual' WHERE difficulty_id=(SELECT min(difficulty_id) FROM game_difficulties)")
            library.connection.commit();before=audit(library.connection)
        with self.assertRaisesRegex(ValueError,'既有難度資料不同'):import_catalog(self.path,self.catalog)
        with LibraryDatabase(self.path) as library:self.assertEqual(audit(library.connection),before)

    def test_new_gn_replacing_old_identity_rejected(self):
        import_catalog(self.path,self.catalog)
        replaced=read_catalog(csv_bytes([raw_row(gn='changed.gn')]),'changed.csv')
        with self.assertRaisesRegex(ValueError,'身份變動'):import_catalog(self.path,replaced)

    def test_halfway_failure_rolls_back_all_import_tables_and_keeps_verified_backup(self):
        def fail_second(db,root,row,import_id):
            if row['raw']['gn']=='b.gn':raise RuntimeError('injected import failure')
            return _insert_song(db,root,row,import_id)
        with patch('dance_results.official_import._insert_song',side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError,'injected'):import_catalog(self.path,self.catalog)
        with LibraryDatabase(self.path) as library:self.assertEqual(audit(library.connection),self.before)
        backups=list((self.base/'backups').glob('*.pre-official-import.*.sqlite3'));self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:self.assertEqual(audit(db),self.before)

    def test_parser_rejects_duplicate_identity_bad_pairs_bad_encoding_and_malformed_csv(self):
        for data in (csv_bytes([raw_row(),raw_row()]),csv_bytes([raw_row(lvHard='0')]),csv_bytes([raw_row(notesHard='-2')]),
                     csv_bytes([raw_row(lvHard='1.5')]),csv_bytes([raw_row(bpm='NaN')]),csv_bytes([raw_row(title='')]),
                     b'fileId,title\n1,Song\n',b'\xff\xff',csv_bytes([raw_row(mode='T')]),b'\x00'):
            with self.subTest(data=data[:30]):
                with self.assertRaises(ValueError):read_catalog(data,'invalid.csv')
        missing=read_catalog(csv_bytes([raw_row(lvNormal='',notesNormal='',lvHard='0',notesHard='0')]),'blank.csv')
        self.assertEqual((missing.summary['valid_difficulties'],len(missing.summary['unprovided_difficulties'])),(1,2))

    def test_official_library_scope_max_readonly_and_manual_custom_still_works(self):
        import_catalog(self.path,self.catalog);service=LibraryService(self.path)
        packs=service.packs(self.root_id)
        self.assertEqual(packs['categories'],{'official':3,'custom':1,'download':1,'unknown':0})
        self.assertEqual((packs['pack_count'],packs['file_source_count'],packs['official_source_count']),(2,2,3))
        songs=service.songs(self.root_id,query='同名 JOINT☆')['songs'];self.assertEqual(len(songs),4)
        official=next(s for s in songs if s['source_type']=='official')
        detail=service.detail(self.root_id,official['song_id'])
        self.assertIsNone(detail['original_path']);self.assertIsNone(detail['folder_name'])
        self.assertEqual(detail['official']['file_id'],'10026');self.assertEqual(detail['difficulties'][0]['max_score'],558)
        body=dict(root_id=detail['root_id'],song_id=detail['song_id'],song_folder=detail['relative_folder'],difficulty='EASY',exists=True,game_level=5,total_notes=279,token=detail['difficulties'][0]['token'])
        with self.assertRaisesRegex(LibraryError,'來源檢視'):service.save_difficulty(body)
        with self.assertRaisesRegex(LibraryError,'CSV'):service.start_scan(detail['root_id'])
        single=service.songs(self.root_id,query='動畫歌曲串燒')['songs'][0];info=service.detail(self.root_id,single['song_id'])
        self.assertEqual(info['difficulties'][0]['max_score'],6834)
        self.assertTrue(info['difficulties'][1]['not_provided']);self.assertIsNone(info['difficulties'][1]['max_score'])
        custom=next(s for s in songs if s['source_type']=='custom');info=service.detail(self.root_id,custom['song_id'])
        service.save_difficulty(dict(root_id=info['root_id'],song_id=info['song_id'],song_folder=info['relative_folder'],difficulty='HARD',exists=True,game_level=20,total_notes=929,token=info['difficulties'][2]['token']))
        import_catalog(self.path,self.catalog)
        self.assertEqual(service.detail(self.root_id,custom['song_id'])['difficulties'][2]['total_notes'],929)

    def test_independent_import_without_custom_library_and_restart(self):
        path=self.base/'official-only.sqlite3';import_catalog(path,self.catalog)
        service=LibraryService(path);root=service.bootstrap()['roots'][0]
        self.assertEqual(root['root_kind'],'official_catalog')
        self.assertEqual(service.packs(root['root_id'])['pack_count'],0)
        server=create_server(path)
        try:self.assertEqual(server.service.songs(root['root_id'],category='official')['total'],3)
        finally:server.server_close()

    def test_database_inside_song_is_rejected_before_writing(self):
        target=self.root/'inside.sqlite3'
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(target)) as copy:source.backup(copy)
        with self.assertRaisesRegex(ValueError,'SONG'):import_catalog(target,self.catalog)
        with closing(sqlite3.connect(target)) as db:self.assertEqual(audit(db),self.before)

    def test_http_preview_import_idempotence_large_payload_and_expected_sha(self):
        server=create_server(self.path);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        data=csv_bytes([raw_row(title='長'*12000)])
        body={'file_name':'song_table.csv','csv_base64':base64.b64encode(data).decode('ascii')}
        def request(route,payload):
            req=Request(url+route,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Dance-Token':server.session_token})
            with urlopen(req,timeout=10) as response:return json.load(response)
        try:
            preview=request('/api/official/preview',body);self.assertEqual(preview['changes']['new_songs'],1)
            with LibraryDatabase(self.path) as library:self.assertEqual(audit(library.connection),self.before)
            result=request('/api/official/import',{**body,'expected_sha256':preview['csv_sha256']});self.assertEqual(result['changes']['new_songs'],1)
            result=request('/api/official/import',{**body,'expected_sha256':preview['csv_sha256']});self.assertEqual(result['changes']['new_songs'],0)
            with self.assertRaises(LibraryError):server.service.official_catalog({**body,'expected_sha256':'stale'},apply=True)
        finally:server.shutdown();server.server_close();thread.join()


class OfficialMigrationTests(unittest.TestCase):
    def setUp(self):
        gui_fixture.ManualMigrationTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db:
            migrate_v3(db,self.path,new_database=True);self.columns=projection(db);self.before=audit(db)

    def tearDown(self):gui_fixture.ManualMigrationTests.tearDown(self)

    def test_schema5_and_import_preserve_nonempty_difficulties_history_views_and_source_ids(self):
        result=import_catalog(self.path,sample_catalog())
        self.assertEqual(result['migrations'][0]['from_version'],4)
        with LibraryDatabase(self.path) as library:
            db=library.connection
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertEqual(db.execute('SELECT * FROM user_difficulties WHERE difficulty_id=301').fetchone()[0],301)
            self.assertEqual(db.execute('SELECT difficulty_id FROM extra_history').fetchone()[0],301)
            self.assertEqual(db.execute('SELECT difficulty_id FROM play_records WHERE record_id=401').fetchone()[0],301)
            self.assertEqual(db.execute('SELECT count(*) FROM song_sources').fetchone()[0],2)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
        with closing(sqlite3.connect(result['migrations'][0]['backup_path'])) as backup:
            self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(audit(backup),self.before)

    def test_late_schema5_failure_rolls_back_ddl_and_restores_foreign_keys(self):
        calls=0
        def fail_late(db):
            nonlocal calls
            calls+=1
            if calls==2:raise ValueError('injected late failure')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            with patch('dance_results.migrations_v5.check_integrity',side_effect=fail_late):
                with self.assertRaisesRegex(ValueError,'late failure'):migrate_v4(db,self.path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(audit(db),self.before)
            self.assertEqual(db.execute('SELECT * FROM user_difficulties').fetchone()[0],301)

    def test_preview_schema4_is_readonly_and_does_not_migrate(self):
        result=preview_catalog(self.path,sample_catalog());self.assertEqual(result['keyboard_rows'],3)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(audit(db),self.before);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)


if __name__=='__main__':unittest.main()
