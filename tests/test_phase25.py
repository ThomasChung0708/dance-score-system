from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dance_results.database import APPLICATION_ID, SCHEMA, LibraryDatabase
from dance_results.migrations import V2_SQL, identity_audit
from dance_results.parsers import parse_file
from dance_results.scanner import scan_library


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name).resolve() / 'library.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(SCHEMA)
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            db.execute('PRAGMA user_version=1')
            db.execute("INSERT INTO library_roots VALUES(7,'D:/SONG','d:/song','before')")
            db.execute("INSERT INTO packs VALUES(11,7,'Pack','pack','Pack')")
            db.execute("INSERT INTO scan_runs VALUES(17,7,'before','after','[]',1,'{\"historic\":true}')")
            db.execute("INSERT INTO songs(song_id,root_id,pack_id,relative_folder,folder_key,latest_title,saved_title,latest_status,payload_json,last_seen_scan_id,created_at) VALUES(23,7,11,'Pack\\Song','pack/song','歌名☆','歌名☆','ok','{}',17,'before')")
            db.execute("INSERT INTO song_sources(source_id,song_id,relative_path,path_key,format,saved_title,latest_status,payload_json,last_seen_scan_id) VALUES(31,23,'Pack/Song/a.sm','a.sm','sm','歌名☆','ok','{}',17)")
            db.execute("INSERT INTO charts VALUES(41,23,'HARD',25,153,'game_screen')")
            db.execute('CREATE TABLE history_guard(id INTEGER PRIMARY KEY,chart_id INTEGER REFERENCES charts(chart_id))')
            db.execute('INSERT INTO history_guard VALUES(51,41)')
            db.commit()
            self.before = identity_audit(db)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_preserves_nonempty_ids_history_and_verified_backup(self):
        with LibraryDatabase(self.path) as library:
            result = library.migration_results[0]
            self.assertEqual(library.migration_results[1]['before'],library.migration_results[1]['after'])
            self.assertEqual(result['before'], result['after'])
            self.assertEqual(identity_audit(library.connection, migrated=True), self.before)
            self.assertEqual(library.connection.execute('PRAGMA user_version').fetchone()[0], 10)
            row = library.connection.execute('SELECT difficulty_id,game_level,total_notes,notes_verified,level_source FROM game_difficulties').fetchone()
            self.assertEqual(tuple(row), (41,25,153,0,None))
            self.assertEqual(library.connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(library.connection.execute('SELECT chart_id FROM history_guard').fetchone()[0], 41)
            self.assertEqual(tuple(library.connection.execute('SELECT folder_name,source_format,format_status FROM songs').fetchone()), ('Song','sm','single'))
            with closing(sqlite3.connect(result['backup_path'])) as backup:
                self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0], 1)
                self.assertEqual(identity_audit(backup), self.before)
        with LibraryDatabase(self.path) as reopened:
            self.assertIsNone(reopened.migration_result)
            self.assertEqual(reopened.connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 9)
        self.assertEqual(len(list(self.path.parent.glob('backups/*.sqlite3'))), 9)

    def test_failure_rolls_back_all_ddl_and_keeps_backup(self):
        with patch('dance_results.migrations.V2_SQL', V2_SQL + '\nCREATE TABLE songs(bad TEXT);\n'):
            with self.assertRaises(sqlite3.OperationalError):
                LibraryDatabase(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(identity_audit(db), self.before)
            self.assertNotIn('folder_name', [r[1] for r in db.execute('PRAGMA table_info(songs)')])
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='game_difficulties'").fetchone()[0], 0)
        self.assertEqual(len(list(self.path.parent.glob('backups/*.sqlite3'))), 1)

    def test_read_only_old_database_never_migrates(self):
        with self.assertRaisesRegex(ValueError, 'migration'):
            LibraryDatabase(self.path, read_only=True)
        self.assertFalse((self.path.parent / 'backups').exists())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(identity_audit(db), self.before)

    def test_mixed_format_migration_never_selects_priority(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO song_sources(source_id,song_id,relative_path,path_key,format,latest_status,payload_json) VALUES(32,23,'Pack/Song/a.dwi','a.dwi','dwi','ok','{}')")
            db.commit()
        with LibraryDatabase(self.path) as library:
            self.assertEqual(tuple(library.connection.execute('SELECT source_format,format_status FROM songs').fetchone()), (None,'mixed'))
            self.assertEqual(library.connection.execute('SELECT count(*) FROM songs').fetchone()[0], 1)

    def test_invalid_existing_foreign_keys_stop_before_migration(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO charts(song_id,difficulty) VALUES(999,'EASY')")
            db.commit()
        with self.assertRaisesRegex(ValueError, '完整性'):
            LibraryDatabase(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)


class Phase25Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'SONG'
        self.folder = self.root / 'Pack' / 'Song'
        self.folder.mkdir(parents=True)
        self.file = self.folder / 'a.sm'
        self.file.write_text('#TITLE:歌曲 日本語 ☆ English;#NOTES:wrong::Challenge:9999:0:1111;', encoding='utf-8')
        self.library = LibraryDatabase(self.base / 'library.sqlite3')
        self.db = self.library.connection
        report = self.sync()
        self.song_id = report.songs[0].song_id

    def tearDown(self):
        self.library.connection.close()
        self.temp.cleanup()

    def sync(self, encoding=None):
        report = scan_library(self.root, legacy_encoding=encoding)
        self.library.sync(report)
        return report

    def difficulty(self, label):
        difficulty_id = self.db.execute('INSERT INTO game_difficulties(song_id,difficulty) VALUES(?,?)', (self.song_id,label)).lastrowid
        self.db.commit()
        return difficulty_id

    def record(self, session, difficulty_id=None, *, status='COMPLETED', p=50, c=0, b=0, m=0, lamp='ACP', verified=True, matched=True, player='TC', host=0):
        self.db.execute("INSERT INTO play_sessions(session_id,player_name,play_mode,is_host,room_status,player_status) VALUES(?,?,'multiplayer',?,'result',?)",
                        (session,player,host,'completed' if status=='COMPLETED' else 'failed'))
        self.db.execute('INSERT INTO play_records(session_id,difficulty_id,played_at,completion_status,perfect,cool,bad,miss,ex_score,lamp_status,match_status,validation_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (session,difficulty_id,'2026-10-22',status,p,c,b,m,2*p+c,lamp,'confirmed' if matched else 'pending','verified' if verified else 'pending'))
        self.db.commit()

    def test_same_title_same_pack_different_folders_remain_separate_on_rescan(self):
        other = self.folder.parent / 'DifferentFolder'
        other.mkdir()
        (other / 'a.sm').write_bytes(self.file.read_bytes())
        first, second = self.sync(), self.sync()
        self.assertEqual(len(first.songs), 2)
        self.assertEqual(len({s.song_id for s in first.songs}), 2)
        self.assertEqual({s.song_folder:s.song_id for s in first.songs}, {s.song_folder:s.song_id for s in second.songs})
        self.assertEqual(second.database['changes']['new_songs'], 0)

    def test_only_observed_difficulties_and_null_values_no_file_level(self):
        self.assertEqual(self.db.execute('SELECT count(*) FROM game_difficulties').fetchone()[0], 0)
        hard = self.difficulty('HARD')
        self.assertEqual(tuple(self.db.execute('SELECT game_level,total_notes,notes_verified FROM game_difficulties').fetchone()), (None,None,0))
        self.difficulty('EASY')
        cached = self.library.load_report(self.root)
        self.assertEqual(set(cached.songs[0].available_difficulties), {'EASY','HARD'})
        self.assertEqual(cached.songs[0].game_levels, {})
        self.assertEqual(cached.songs[0].game_total_notes, {})
        self.sync()
        self.assertEqual(self.db.execute('SELECT count(*) FROM game_difficulties').fetchone()[0], 2)
        self.assertEqual(self.db.execute('SELECT difficulty_id FROM game_difficulties WHERE difficulty_id=?', (hard,)).fetchone()[0], hard)

    def test_game_values_and_history_survive_source_changes_and_rescan(self):
        hard = self.difficulty('HARD')
        self.db.execute("UPDATE game_difficulties SET game_level=25,level_source='game_screen',total_notes=153,notes_source='game_screen',notes_verified=1 WHERE difficulty_id=?", (hard,))
        self.db.commit()
        self.record('first',hard)
        self.file.write_text('#TITLE:Renamed;#NOTES:wrong::HARD:888:0:0000;', encoding='utf-8')
        self.sync()
        cached = self.library.load_report(self.root).songs[0]
        self.assertEqual(cached.game_levels, {'HARD':25})
        self.assertEqual(cached.game_total_notes, {'HARD':153})
        self.assertEqual(self.db.execute('SELECT count(*) FROM play_records').fetchone()[0], 1)

    def test_independent_bests_lamps_and_accounts(self):
        hard, easy = self.difficulty('HARD'), self.difficulty('EASY')
        self.record('acp',hard,p=20)
        self.record('higher',hard,p=100,b=1,lamp='CLEAR',player='Another account')
        self.record('failed',hard,status='FAILED',p=500,lamp='FAILED')
        self.record('easy',easy,p=10,c=5,lamp='AC')
        bests = {r['difficulty']:tuple(r)[3:] for r in self.db.execute('SELECT * FROM difficulty_bests')}
        self.assertEqual(bests, {'HARD':(200,'ACP'),'EASY':(25,'AC')})

    def test_pending_guest_is_saved_without_changing_arbitrary_song_best(self):
        hard = self.difficulty('HARD')
        self.record('unknown',None,p=1000,matched=False)
        self.assertEqual(tuple(self.db.execute('SELECT highest_ex_score,best_clear_lamp FROM difficulty_bests').fetchone()), (None,'UNPLAYED'))
        self.assertEqual(self.db.execute('SELECT difficulty_id FROM play_records').fetchone()[0], None)
        self.db.execute("UPDATE play_records SET difficulty_id=?,match_status='confirmed'", (hard,))
        self.db.commit()
        self.assertEqual(self.db.execute('SELECT highest_ex_score FROM difficulty_bests').fetchone()[0], 2000)

    def test_unverified_record_not_in_best(self):
        hard = self.difficulty('HARD')
        self.record('uncertain',hard,p=900,verified=False)
        self.assertEqual(tuple(self.db.execute('SELECT highest_ex_score,best_clear_lamp FROM difficulty_bests').fetchone()), (None,'UNPLAYED'))

    def test_guest_local_difficulty_and_host_level_are_separate_evidence(self):
        self.db.execute("INSERT INTO play_sessions(session_id,player_name,play_mode,is_host,difficulty_mode,local_difficulty,local_game_level,host_result_level,display_song_title) VALUES('guest','TC','multiplayer',0,'free','NORMAL',12,45,'遊戲顯示歌名')")
        self.db.commit()
        self.assertEqual(tuple(self.db.execute('SELECT source_type,local_difficulty,local_game_level,host_result_level FROM play_sessions').fetchone()), ('unknown','NORMAL',12,45))
        self.sync()
        self.assertEqual(self.db.execute('SELECT count(*) FROM game_difficulties').fetchone()[0], 0)

    def test_own_failure_waits_for_room_result_and_session_deduplicates(self):
        self.db.execute("INSERT INTO play_sessions(session_id,player_name,play_mode,is_host,player_status) VALUES('wait','TC','multiplayer',0,'failed_waiting')")
        self.db.commit()
        sql = "INSERT INTO play_records(session_id,played_at,completion_status) VALUES('wait','now','FAILED')"
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql)
        self.db.rollback()
        self.db.execute("UPDATE play_sessions SET room_status='result',player_status='failed' WHERE session_id='wait'")
        self.db.execute(sql)
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql)
        self.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE play_sessions SET room_status='playing' WHERE session_id='wait'")
        self.db.rollback()

    def test_quit_disconnect_interruption_never_become_failed_result(self):
        for status in ('quit','disconnected','interrupted'):
            self.db.execute('INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) VALUES(?,?,?,?,?)', (status,'TC','single',status,status))
            self.db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute("INSERT INTO play_records(session_id,played_at,completion_status) VALUES(?,'now','FAILED')", (status,))
            self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM play_records').fetchone()[0], 0)

    def test_failed_partial_judgments_cannot_be_complete_note_count(self):
        self.record('failed',None,status='FAILED',p=10,c=2,b=1,m=1,lamp='FAILED',matched=False)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE play_records SET total_notes=14,notes_source='completed_judgment_sum'")
        self.db.rollback()
        self.record('completed',None,p=10,c=2,b=1,m=1,lamp='CLEAR',matched=False)
        self.db.execute("UPDATE play_records SET total_notes=14,notes_source='completed_judgment_sum',max_score=28,percentage=100.0*22/28 WHERE session_id='completed'")
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT total_notes FROM play_records WHERE session_id='completed'").fetchone()[0], 14)

    def test_invalid_difficulty_verified_notes_and_record_values_rejected(self):
        hard = self.difficulty('HARD')
        for sql in ("INSERT INTO game_difficulties(song_id,difficulty) VALUES(%d,'Challenge')" % self.song_id,
                    "UPDATE game_difficulties SET notes_verified=1 WHERE difficulty_id=%d" % hard):
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)
            self.db.rollback()
        self.record('score',hard)
        for change in ('ex_score=12345', 'max_score=0', 'perfect=-1', "lamp_status='FAILED'", 'percentage=95', "lamp_status='AC'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute('UPDATE play_records SET ' + change)
            self.db.rollback()

    def test_display_aliases_keep_original_unicode_and_are_not_global_identity(self):
        title = self.db.execute('SELECT latest_title FROM songs').fetchone()[0]
        for alias in ('遊戲歌名☆', '日本語 / MIX 中文!'):
            self.db.execute("INSERT INTO song_title_aliases(song_id,display_title,source,confirmed) VALUES(?,?,'game_screen',1)", (self.song_id,alias))
        self.db.commit()
        self.sync()
        self.assertEqual(self.db.execute('SELECT latest_title FROM songs').fetchone()[0], title)
        self.assertEqual(self.db.execute("SELECT count(*) FROM song_title_aliases WHERE display_title LIKE '%中文%'").fetchone()[0], 1)

    def test_unknown_encoding_archives_exact_bytes_and_rescan_deduplicates(self):
        raw = '#TITLE:測試歌曲;'.encode('big5')
        self.file.write_bytes(raw)
        first, second = self.sync(), self.sync()
        self.assertIsNone(first.files[0].title)
        self.assertTrue(first.files[0].raw_data_preserved)
        self.assertIn('big5', first.files[0].encoding_candidates)
        self.assertNotIn('raw_data', first.to_dict()['files'][0])
        self.assertEqual(second.database['changes']['new_sources'], 0)
        row = self.db.execute('SELECT raw_data,sha256 FROM source_raw_snapshots').fetchone()
        self.assertEqual(tuple(row), (raw,hashlib.sha256(raw).hexdigest()))
        self.assertEqual(self.db.execute('SELECT count(*) FROM source_raw_snapshots').fetchone()[0], 1)
        confirmed = self.sync('big5')
        self.assertEqual(confirmed.songs[0].title, '測試歌曲')
        self.assertEqual(confirmed.songs[0].song_id, self.song_id)
        self.assertEqual(self.db.execute('SELECT count(*) FROM source_raw_snapshots').fetchone()[0], 1)
        json.dumps(self.library.load_report(self.root).to_dict(), ensure_ascii=False)

    def test_utf8_bom_cp932_big5_titles_remain_exact(self):
        cases = [('utf-8','繁體中文 日本語 English ☆ e\u0301'),('utf-8-sig','歌名【日本語】★'),('cp932','日本語の曲名☆'),('big5','繁體中文歌曲★')]
        for encoding,title in cases:
            with self.subTest(encoding=encoding):
                self.file.write_bytes(('#TITLE:' + title + ';').encode(encoding))
                result = parse_file(self.file, None if encoding.startswith('utf-8') else encoding)
                self.assertEqual(result.title, title)
                report = self.sync(None if encoding.startswith('utf-8') else encoding)
                self.assertEqual(report.songs[0].saved_title, title)
                self.assertEqual(self.db.execute('SELECT saved_title FROM songs').fetchone()[0], title)

    def test_same_format_multiple_sources_no_extra_song_or_game_difficulty(self):
        (self.folder / 'b.sm').write_bytes(self.file.read_bytes())
        report = self.sync()
        self.assertEqual(len(report.songs), 1)
        self.assertEqual(report.songs[0].source_format, 'sm')
        self.assertEqual(report.songs[0].format_status, 'single')
        self.assertEqual(report.database['totals']['game_difficulties'], 0)
        self.assertEqual(report.database['totals']['sources'], 2)


if __name__ == '__main__':
    unittest.main()
