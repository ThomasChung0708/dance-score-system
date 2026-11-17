from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryError, LibraryService
from dance_results.migrations_v3 import migrate_v2
from dance_results.migrations_v4 import data_audit, migrate_v3
from dance_results.scanner import scan_library
import test_connect_final as fixture


class ManualMigrationTests(unittest.TestCase):
    def setUp(self):
        fixture.ConnectFinalTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db:
            migrate_v2(db,self.path,new_database=True)
            db.execute('CREATE TABLE extra_history(id INTEGER PRIMARY KEY,difficulty_id INTEGER REFERENCES game_difficulties(difficulty_id))')
            db.execute('INSERT INTO extra_history VALUES(501,301)')
            db.execute('CREATE INDEX user_level_index ON game_difficulties(game_level)')
            db.execute('CREATE VIEW user_difficulties AS SELECT difficulty_id FROM game_difficulties')
            db.commit();self.before=data_audit(db)

    def tearDown(self):fixture.ConnectFinalTests.tearDown(self)

    def test_schema4_preserves_nonempty_history_views_indexes_and_backup(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');result=migrate_v3(db,self.path)
            self.assertEqual(result['before'],result['after'])
            self.assertEqual(data_audit(db),self.before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT * FROM user_difficulties').fetchone()[0],301)
            self.assertEqual(db.execute('SELECT difficulty_id FROM extra_history').fetchone()[0],301)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='user_level_index'").fetchone()[0],1)
            with closing(sqlite3.connect(result['backup_path'])) as backup:
                self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],3)
                self.assertEqual(data_audit(backup),self.before)
            db.execute("UPDATE game_difficulties SET game_level=12,level_source='manual',total_notes=153,notes_source='manual',notes_verified=1 WHERE difficulty_id=301")
            db.commit()
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(db.execute('SELECT record_id FROM play_records').fetchone()[0],401)

    def test_schema4_late_failure_rolls_back_and_restores_fk_enforcement(self):
        calls=0
        def broken(*args):
            nonlocal calls
            calls+=1
            value=data_audit(*args)
            return {**value,'failure':True} if calls==3 else value
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            with patch('dance_results.migrations_v4.data_audit',side_effect=broken):
                with self.assertRaisesRegex(ValueError,'回滾'):migrate_v3(db,self.path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(data_audit(db),self.before)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT * FROM user_difficulties').fetchone()[0],301)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE game_difficulties SET game_level=12,level_source='manual'")


class GuiServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.root=self.base/'SONG'
        self.folders=[self.root/'Pack A'/'一號',self.root/'Pack A'/'二號',self.root/'connect'/'日文歌']
        for i,folder in enumerate(self.folders):
            folder.mkdir(parents=True)
            title='中文 日本語 English☆' if i<2 else '日本語の歌曲'
            (folder/'song.sm').write_text('#TITLE:'+title+';#ARTIST:作者;',encoding='utf-8')
        self.path=self.base/'library.sqlite3'
        with LibraryDatabase(self.path) as library:
            self.report=scan_library(self.root);library.sync(self.report)
        self.root_id=self.report.database['root_id'];self.ids=[s.song_id for s in self.report.songs]
        self.service=LibraryService(self.path)

    def tearDown(self):self.temp.cleanup()

    def edit_body(self,song_id=None,label='HARD',**values):
        detail=self.service.detail(self.root_id,song_id or self.ids[0]);d=next(d for d in detail['difficulties'] if d['difficulty']==label)
        return dict(root_id=self.root_id,song_id=detail['song_id'],song_folder=detail['relative_folder'],difficulty=label,
                    exists=True,game_level=25,total_notes=153,token=d['token'],**values)

    def test_packs_categories_counts_connect_and_no_inferred_official(self):
        packs=self.service.packs(self.root_id)
        self.assertEqual((packs['pack_count'],packs['song_count'],packs['source_count']),(2,3,3))
        # Songs downloaded in multiplayer rooms (connect) list as ordinary custom songs; only the pack keeps
        # its multiplayer_download type, which the OCR monitor uses to follow downloads.
        self.assertEqual(packs['categories'],{'official':0,'custom':3,'download':0,'unknown':0})
        connect=next(p for p in packs['packs'] if p['name']=='connect')
        self.assertEqual('multiplayer_download',connect['pack_type'])
        result=self.service.songs(self.root_id,pack_id=connect['pack_id'])
        self.assertEqual(result['total'],1)
        self.assertTrue(result['songs'][0]['relative_folder'].startswith('connect'))
        self.assertEqual(self.service.songs(self.root_id,category='official')['total'],0)

    def test_multilingual_same_names_separate_ids_paths_search_sort_pagination(self):
        for query in ('中文','日本語 English','english☆'):
            result=self.service.songs(self.root_id,query=query)
            self.assertEqual(result['total'],2)
            self.assertEqual(len({s['song_id'] for s in result['songs']}),2)
            self.assertEqual(len({s['relative_folder'] for s in result['songs']}),2)
        first=self.service.songs(self.root_id,limit=1)
        second=self.service.songs(self.root_id,offset=1,limit=1)
        self.assertNotEqual(first['songs'][0]['song_id'],second['songs'][0]['song_id'])
        asc=[s['song_id'] for s in self.service.songs(self.root_id)['songs']]
        desc=[s['song_id'] for s in self.service.songs(self.root_id,order='desc')['songs']]
        self.assertEqual(asc,list(reversed(desc)))

    def test_detail_original_fields_and_three_unset_cards_without_creating_rows(self):
        detail=self.service.detail(self.root_id,self.ids[0])
        self.assertEqual([d['difficulty'] for d in detail['difficulties']],['EASY','NORMAL','HARD'])
        self.assertTrue(all(not d['exists'] and d['game_level'] is None and d['total_notes'] is None for d in detail['difficulties']))
        self.assertEqual(detail['artists'],['作者'])
        self.assertIn('song.sm',detail['sources'][0]['original_path'])
        with self.service.open() as library:self.assertEqual(library.connection.execute('SELECT count(*) FROM game_difficulties').fetchone()[0],0)

    def test_manual_save_restart_edit_clear_and_independent_difficulties(self):
        saved=self.service.save_difficulty(self.edit_body())
        self.assertEqual((saved['game_level'],saved['total_notes'],saved['level_source'],saved['notes_source'],saved['notes_verified']),(25,153,'manual','manual',1))
        self.service=LibraryService(self.path)
        body=self.edit_body();body['game_level']=30;self.service.save_difficulty(body)
        self.service.save_difficulty(self.edit_body(label='EASY'))
        body=self.edit_body();body['game_level']=None;body['total_notes']=None;self.service.save_difficulty(body)
        detail=self.service.detail(self.root_id,self.ids[0]);hard=detail['difficulties'][2]
        self.assertTrue(hard['exists']);self.assertIsNone(hard['game_level']);self.assertIsNone(hard['total_notes'])
        self.assertIsNone(hard['level_source']);self.assertIsNone(hard['notes_source']);self.assertEqual(hard['notes_verified'],0)
        self.assertEqual(detail['difficulties'][0]['game_level'],25)
        self.assertFalse(detail['difficulties'][1]['exists'])

    def test_same_name_wrong_id_folder_root_and_stale_token_rejected(self):
        body=self.edit_body();stale=dict(body)
        for change in ({'song_id':self.ids[1]},{'song_folder':'Pack A\\wrong'},{'root_id':999},{'token':'stale'}):
            with self.assertRaises(LibraryError):self.service.save_difficulty({**body,**change})
        self.service.save_difficulty(body)
        with self.assertRaisesRegex(LibraryError,'重新載入'):self.service.save_difficulty(stale)
        self.assertTrue(all(not d['exists'] for d in self.service.detail(self.root_id,self.ids[1])['difficulties']))

    def test_numeric_label_and_existence_validation_no_fabricated_values(self):
        for key,value in (('game_level',-1),('game_level',True),('game_level',1.5),('game_level','12'),('total_notes',0),('total_notes',-1),('total_notes',1.2),('exists',False),('difficulty','Challenge')):
            with self.subTest(key=key,value=value):
                with self.assertRaises(LibraryError):self.service.save_difficulty({**self.edit_body(),key:value})
        with self.service.open() as library:self.assertEqual(library.connection.execute('SELECT count(*) FROM game_difficulties').fetchone()[0],0)

    def wait_scan(self):
        deadline=time.monotonic()+10
        while self.service.scan_status()['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
        status=self.service.scan_status();self.assertEqual(status['status'],'completed',status);return status

    def test_background_incremental_rescan_preserves_manual_values_and_ids(self):
        self.service.save_difficulty(self.edit_body())
        self.service.start_scan(self.root_id);first=self.wait_scan()
        self.service.start_scan(self.root_id);second=self.wait_scan()
        self.assertEqual(first['changes']['new_songs'],0);self.assertEqual(second['changes']['new_sources'],0)
        self.assertIn('warning_count',second)
        self.assertEqual(self.service.detail(self.root_id,self.ids[0])['difficulties'][2]['total_notes'],153)
        folder=self.root/'connect'/'new';folder.mkdir();(folder/'new.sm').write_text('#TITLE:New;',encoding='utf-8')
        self.service.start_scan(self.root_id);status=self.wait_scan()
        self.assertEqual((status['changes']['new_songs'],status['changes']['new_sources']),(1,1))
        self.assertEqual(self.service.packs(self.root_id)['song_count'],4)

    def test_write_lock_prevents_edit_during_scan(self):
        self.service._write_lock.acquire()
        try:
            with self.assertRaisesRegex(LibraryError,'更新'):self.service.save_difficulty(self.edit_body())
            with self.assertRaises(LibraryError):self.service.start_scan(self.root_id)
        finally:self.service._write_lock.release()

    def test_missing_root_does_not_run_or_mark_missing(self):
        with patch('pathlib.Path.is_dir',return_value=False):
            with self.assertRaises(LibraryError):self.service.start_scan(self.root_id)
        self.assertEqual(self.service.packs(self.root_id)['song_count'],3)

    def test_http_local_api_save_reload_static_and_reject_unauthorized_writes(self):
        server=create_server(self.path);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        def request(path,body=None,headers=None):
            h={'X-Dance-Token':server.session_token,**(headers or {})}
            if body is not None:h['Content-Type']='application/json'
            req=Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
            with urlopen(req,timeout=5) as response:return response.status,response.read()
        try:
            _,html=request('/');self.assertIn(server.session_token.encode(),html)
            self.assertIn(b'SongFolder',request('/app.js')[1])
            self.assertIn(b'--purple',request('/style.css')[1])
            packs=json.loads(request('/api/packs?root_id='+str(self.root_id))[1]);self.assertEqual(packs['pack_count'],2)
            body=self.edit_body();self.assertEqual(json.loads(request('/api/difficulty',body)[1])['notes_source'],'manual')
            detail=json.loads(request('/api/songs/'+str(body['song_id'])+'?root_id='+str(self.root_id))[1]);self.assertEqual(detail['difficulties'][2]['total_notes'],153)
            for headers in ({'X-Dance-Token':'wrong'},{'Origin':'https://example.invalid'},{'Host':'not-local.invalid'}):
                with self.assertRaises(HTTPError) as failure:request('/api/difficulty',body,headers)
                self.assertEqual(failure.exception.code,403);failure.exception.close()
            with self.assertRaises(HTTPError) as failure:request('/api/songs?root_id=invalid')
            self.assertEqual(failure.exception.code,400);failure.exception.close()
        finally:server.shutdown();server.server_close();thread.join()

        # A new HTTP server has a new session token and must read the persisted row.
        server=create_server(self.path);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            detail=json.loads(request('/api/songs/'+str(body['song_id'])+'?root_id='+str(self.root_id))[1])
            self.assertEqual((detail['difficulties'][2]['game_level'],detail['difficulties'][2]['total_notes']),(25,153))
            self.assertTrue(all(not d['exists'] for d in server.service.detail(self.root_id,self.ids[1])['difficulties']))
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
