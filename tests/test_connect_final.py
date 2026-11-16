from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dance_results.database import APPLICATION_ID, SCHEMA, LibraryDatabase, path_key, source_payload, song_payload
from dance_results.migrations import migrate_v1
from dance_results.migrations_v3 import V3_SQL, audit_v3
from dance_results.parsers import parse_file
from dance_results.scanner import scan_library


class ConnectFinalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name).resolve()
        self.root=self.base/'SONG'
        self.normal=self.root/'Original Pack'/'同名歌'
        self.download=self.root/'connect'/'下載歌'
        for folder in (self.normal,self.download):
            folder.mkdir(parents=True)
            (folder/'song.sm').write_text('#TITLE:中文 日本語 Song☆;#SUBTITLE:副標題;#ARTIST:作者 Artist;',encoding='utf-8')
        self.path=self.base/'library.sqlite3'
        report=scan_library(self.root)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(SCHEMA)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=1')
            db.execute('INSERT INTO library_roots VALUES(7,?,?,?)',(str(self.root),path_key(self.root),'old'))
            db.execute("INSERT INTO packs VALUES(11,7,'Original Pack',?,'Original Pack')",(path_key(Path('Original Pack')),))
            db.execute("INSERT INTO scan_runs VALUES(17,7,'old','old','[]',1,'{\"original\":true}')")
            for s in report.songs:
                sid=101 if s.storage_group=='connect' else 23
                pack_id=None if sid==101 else 11
                if sid==101:s.pack_name=None;s.pack_type=None
                relative=Path(s.song_folder).relative_to(self.root)
                db.execute('INSERT INTO songs(song_id,root_id,pack_id,relative_folder,folder_key,storage_group,latest_title,saved_title,latest_status,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (sid,7,pack_id,str(relative),path_key(relative),s.storage_group,s.title,s.title,s.status,song_payload(s),'old'))
                f=next(f for f in report.files if f.song_folder==s.song_folder)
                if sid==101:f.pack_name=None
                db.execute('INSERT INTO song_sources(source_id,song_id,relative_path,path_key,format,latest_sha256,saved_title,saved_subtitle,latest_status,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (201 if sid==101 else 31,sid,str(Path(f.path).relative_to(self.root)),path_key(Path(f.path).name),'sm',f.sha256,f.title,f.subtitle,f.status,source_payload(f)))
            db.commit()
            migrate_v1(db,self.path,new_database=True)
            db.execute("INSERT INTO game_difficulties(difficulty_id,song_id,difficulty) VALUES(301,101,'HARD')")
            db.execute("INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) VALUES('history','TC','single','result','completed')")
            db.execute("INSERT INTO play_records(record_id,session_id,difficulty_id,played_at,completion_status) VALUES(401,'history',301,'old','COMPLETED')")
            db.commit()
            self.before=audit_v3(db,{11},{101})

    def tearDown(self):
        self.temp.cleanup()

    def test_schema3_migration_preserves_ids_names_paths_history_and_backup(self):
        with LibraryDatabase(self.path) as library:
            db=library.connection; result=library.migration_results[0]
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],11)
            self.assertEqual(result['before'],result['after'])
            self.assertEqual(audit_v3(db,{11},{101}),self.before)
            self.assertEqual(tuple(db.execute('SELECT pack_id,name,pack_type FROM packs WHERE pack_id=11').fetchone()),(11,'Original Pack','normal'))
            self.assertEqual(db.execute("SELECT count(*) FROM songs s JOIN packs p ON p.pack_id=s.pack_id WHERE p.pack_type='multiplayer_download'").fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT song_id,difficulty_id FROM game_difficulties').fetchone()),(101,301))
            self.assertEqual(db.execute('SELECT record_id FROM play_records').fetchone()[0],401)
            self.assertEqual(db.execute('SELECT saved_artist FROM song_sources WHERE source_id=201').fetchone()[0],'作者 Artist')
            cached=library.load_report(self.root)
            song=next(s for s in cached.songs if s.song_id==101)
            self.assertEqual((song.pack_name,song.pack_type),('connect','multiplayer_download'))
            self.assertEqual(next(f for f in cached.files if f.source_id==201).pack_name,'connect')
            with closing(sqlite3.connect(result['backup_path'])) as backup:
                self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],2)
                self.assertEqual(audit_v3(backup,{11},{101}),self.before)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        with LibraryDatabase(self.path) as library:
            self.assertIsNone(library.migration_result)
            self.assertEqual(library.connection.execute('SELECT count(*) FROM packs').fetchone()[0],2)

    def test_migration_failure_rolls_back_pack_links_and_ddl(self):
        with patch('dance_results.migrations_v3.V3_SQL',V3_SQL+'\nCREATE TABLE songs(bad TEXT);\n'):
            with self.assertRaises(sqlite3.OperationalError):LibraryDatabase(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(audit_v3(db,{11},{101}),self.before)
            self.assertNotIn('pack_type',[r[1] for r in db.execute('PRAGMA table_info(packs)')])
        self.assertEqual(len(list(self.base.glob('backups/*.pre-v3.*.sqlite3'))),1)

    def test_read_only_schema2_does_not_migrate(self):
        with self.assertRaisesRegex(ValueError,'migration'):LibraryDatabase(self.path,read_only=True)
        self.assertFalse((self.base/'backups').exists())

    def test_late_audit_failure_rolls_back_new_pack_and_associations(self):
        calls=0
        def audit_with_failure(*args):
            nonlocal calls
            calls+=1
            value=audit_v3(*args)
            return {**value,'injected_failure':True} if calls==3 else value
        with patch('dance_results.migrations_v3.audit_v3',side_effect=audit_with_failure):
            with self.assertRaisesRegex(ValueError,'回滾'):LibraryDatabase(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM packs').fetchone()[0],1)
            self.assertIsNone(db.execute('SELECT pack_id FROM songs WHERE song_id=101').fetchone()[0])
            self.assertEqual(audit_v3(db,{11},{101}),self.before)

    def test_existing_connect_pack_reused_without_duplicate(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO packs VALUES(50,7,'connect','connect','connect')")
            db.commit()
        with LibraryDatabase(self.path) as library:
            db=library.connection
            self.assertEqual(tuple(db.execute('SELECT pack_id,pack_type FROM packs WHERE pack_id=50').fetchone()),(50,'multiplayer_download'))
            self.assertEqual(db.execute('SELECT pack_id FROM songs WHERE song_id=101').fetchone()[0],50)
            self.assertEqual(db.execute('SELECT count(*) FROM packs').fetchone()[0],2)

    def test_two_rescans_and_new_download_keep_all_existing_ids(self):
        with LibraryDatabase(self.path) as library:
            for _ in range(2):
                r=scan_library(self.root);library.sync(r)
                self.assertEqual(r.database['changes']['new_songs'],0)
                self.assertEqual(r.database['changes']['new_sources'],0)
                self.assertEqual({s.song_id for s in r.songs},{23,101})
                self.assertEqual({f.source_id for f in r.files},{31,201})
            new=self.download.parent/'另一個同名歌';new.mkdir()
            (new/'other.osu').write_text('osu file format v14\n[Metadata]\nTitle:中文 日本語 Song☆\nArtist:作者\n',encoding='utf-8')
            r=scan_library(self.root);library.sync(r)
            self.assertEqual(r.database['changes']['new_songs'],1)
            self.assertEqual(r.database['changes']['new_sources'],1)
            r=scan_library(self.root);library.sync(r)
            self.assertEqual(r.database['changes']['new_songs'],0)
            self.assertEqual(r.database['changes']['new_sources'],0)
            self.assertEqual(r.database['totals']['songs'],3)
            self.assertEqual(library.connection.execute("SELECT count(*) FROM songs s JOIN packs p ON p.pack_id=s.pack_id WHERE p.pack_type='multiplayer_download'").fetchone()[0],2)

    def test_missing_connect_restores_same_identity_and_history(self):
        with LibraryDatabase(self.path) as library:
            file=self.download/'song.sm'; raw=file.read_bytes();file.unlink()
            r=scan_library(self.root);library.sync(r)
            self.assertEqual(library.connection.execute('SELECT is_present FROM songs WHERE song_id=101').fetchone()[0],0)
            self.assertEqual(library.connection.execute('SELECT count(*) FROM play_records WHERE record_id=401').fetchone()[0],1)
            file.write_bytes(raw)
            r=scan_library(self.root);library.sync(r)
            self.assertEqual(next(s.song_id for s in r.songs if s.storage_group=='connect'),101)
            self.assertEqual(next(f.source_id for f in r.files if f.storage_group=='connect'),201)
            self.assertEqual(library.connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_artist_and_title_retained_when_later_decode_is_ambiguous(self):
        with LibraryDatabase(self.path) as library:
            library.sync(scan_library(self.root))
            file=self.download/'song.sm'
            file.write_bytes(b'#TITLE:'+bytes.fromhex('a1b8')+b';\n#ARTIST:'+bytes.fromhex('a1b8')+b';')
            r=scan_library(self.root);library.sync(r)
            source=next(f for f in r.files if f.source_id==201)
            self.assertIsNone(source.title)
            self.assertTrue(source.name_review_required)
            self.assertIn('cp932',source.metadata_candidates['TITLE'])
            self.assertIn('big5',source.metadata_candidates['TITLE'])
            row=library.connection.execute('SELECT saved_title,saved_subtitle,saved_artist FROM song_sources WHERE source_id=201').fetchone()
            self.assertEqual(tuple(row),('中文 日本語 Song☆','副標題','作者 Artist'))
            self.assertEqual(library.connection.execute('SELECT saved_title FROM songs WHERE song_id=101').fetchone()[0],'中文 日本語 Song☆ 副標題')

    def test_strict_utf8_name_survives_other_fields_legacy_bytes(self):
        file=self.download/'song.sm'
        raw='#TITLE:新名稱 日本語☆;\n#SUBTITLE:副標題;\n#ARTIST:新作者;\n'.encode('utf-8')+b'#MUSIC:'+bytes.fromhex('a1b8')+b'.mp3;'
        file.write_bytes(raw)
        with LibraryDatabase(self.path) as library:
            r=scan_library(self.root);library.sync(r)
            source=next(f for f in r.files if f.source_id==201)
            self.assertEqual((source.title,source.artist),('新名稱 日本語☆','新作者'))
            self.assertTrue(source.title_reliable)
            self.assertEqual(source.metadata_encodings['TITLE'],['utf-8'])
            self.assertEqual(source.raw_data,raw)
            self.assertTrue(any(d.code=='FILE_ENCODING_PARTIAL' for d in source.diagnostics))
            self.assertEqual(library.connection.execute('SELECT saved_title,saved_artist FROM song_sources WHERE source_id=201').fetchone()[:],('新名稱 日本語☆','新作者'))

    def test_suspected_utf8_mojibake_preserves_previous_good_name(self):
        with LibraryDatabase(self.path) as library:
            library.sync(scan_library(self.root))
            (self.download/'song.sm').write_text('#TITLE:VOY@GER?\uefab;#ARTIST:作者;',encoding='utf-8')
            r=scan_library(self.root);library.sync(r)
            source=next(f for f in r.files if f.source_id==201)
            self.assertEqual(source.title,'VOY@GER?\uefab')
            self.assertFalse(source.title_reliable)
            self.assertTrue(source.raw_data_preserved)
            self.assertEqual(library.connection.execute('SELECT saved_title FROM song_sources WHERE source_id=201').fetchone()[0],'中文 日本語 Song☆')

    def test_sm_dwi_neutral_and_triple_format_manual_review(self):
        (self.download/'song.dwi').write_text('#TITLE:中文 日本語 Song☆ 副標題;#ARTIST:作者 Artist;',encoding='utf-8')
        r=scan_library(self.root)
        s=next(s for s in r.songs if s.storage_group=='connect')
        self.assertEqual(s.status,'ok')
        self.assertFalse(any(d.code=='SOURCE_FORMAT_REVIEW' for d in s.diagnostics))
        (self.download/'song.osu').write_text('[TimingPoints]\n0,500\n[HitObjects]\n',encoding='utf-8')
        r=scan_library(self.root);s=next(s for s in r.songs if s.storage_group=='connect')
        self.assertTrue(any(d.code=='SOURCE_FORMAT_REVIEW' for d in s.diagnostics))
        self.assertEqual(len(r.songs),2)

    def test_artist_unicode_osu_and_explicit_legacy_preserve_known_utf8_title(self):
        file=self.download/'unicode.osu'
        file.write_text('osu file format v14\n[Metadata]\nTitle:English\nTitleUnicode:日本語 中文☆\nArtist:Romaji\nArtistUnicode:日本語作者\n',encoding='utf-8-sig')
        source=parse_file(file)
        self.assertEqual((source.title,source.artist),('日本語 中文☆','日本語作者'))
        self.assertEqual(source.artist_fields,{'Artist':['Romaji'],'ArtistUnicode':['日本語作者']})
        raw=b'osu file format v14\n[General]\nAudioFilename:'+bytes.fromhex('a1b8')+b'.mp3\n[Metadata]\nTitle:English\nTitleUnicode:'+ '椿姫'.encode('utf-8')+b'\nArtist:Artist\n'
        file.write_bytes(b'\xef\xbb\xbf'+raw)
        source=parse_file(file,'cp932')
        self.assertEqual(source.title,'椿姫')
        self.assertEqual(source.metadata_encodings['TitleUnicode'],['utf-8'])
        self.assertTrue(source.title_reliable)


if __name__=='__main__':unittest.main()
