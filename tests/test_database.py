from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from dance_results.database import LibraryDatabase
from dance_results.models import Diagnostic
from dance_results.report import render_html
from dance_results.scanner import scan_library


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "SONG"
        self.folder = self.root / "Pack A" / "Song [方括號]"
        self.folder.mkdir(parents=True)
        self.file = self.folder / "song.sm"
        self.file.write_text("#TITLE:Original;", encoding="utf-8")
        self.path = self.base / "data" / "library.sqlite3"
        self.library = LibraryDatabase(self.path, [self.root])

    def tearDown(self):
        self.library.connection.close()
        self.temp.cleanup()

    def sync(self, folders=None):
        report = scan_library(self.root, folders)
        self.library.sync(report)
        return report

    def test_repeated_scan_has_stable_ids_and_no_duplicate_rows(self):
        first = self.sync()
        second = self.sync()
        self.assertEqual(first.songs[0].song_id, second.songs[0].song_id)
        self.assertEqual(first.files[0].source_id, second.files[0].source_id)
        self.assertEqual(second.database["changes"]["new_songs"], 0)
        self.assertEqual(second.database["changes"]["new_sources"], 0)
        self.assertEqual(second.database["changes"]["unchanged_songs"], 1)
        self.assertEqual(second.database["totals"]["songs"], 1)
        self.assertEqual(second.database["totals"]["game_difficulties"], 0)
        self.assertEqual(second.database["totals"]["scan_runs"], 2)

    def test_title_update_preserves_song_id_and_previous_scan_snapshot(self):
        original = self.sync()
        self.file.write_text("#TITLE:Changed;", encoding="utf-8")
        changed = self.sync()
        self.assertEqual(changed.songs[0].song_id, original.songs[0].song_id)
        self.assertEqual(changed.songs[0].saved_title, "Changed")
        self.assertEqual(changed.database["changes"]["updated_songs"], 1)
        self.assertEqual(changed.database["changes"]["updated_sources"], 1)
        snapshot = self.library.connection.execute("SELECT report_json FROM scan_runs ORDER BY scan_id LIMIT 1").fetchone()[0]
        self.assertEqual(json.loads(snapshot)["songs"][0]["title"], "Original")

    def test_dwi_bpm_round_trip_stable_ids_without_game_values(self):
        from dance_results.library_service import LibraryService
        first = self.sync()
        (self.folder/'song.dwi').write_text('#TITLE:Original;#ARTIST:Artist;#BPM:153.50;#CHANGEBPM:broken;#SINGLE:invalid;', encoding='utf-8')
        second = self.sync()
        third = self.sync()
        self.assertEqual(first.songs[0].song_id, third.songs[0].song_id)
        self.assertEqual(second.files[0].source_id, third.files[0].source_id)
        self.assertEqual(third.database['changes']['new_sources'], 0)
        loaded = next(f for f in self.library.load_report(self.root).files if f.format == 'dwi')
        self.assertEqual(loaded.source_bpm, '153.50')
        detail = LibraryService(self.path).detail(third.database['root_id'], third.songs[0].song_id)
        self.assertEqual(next(s for s in detail['sources'] if s['format'] == 'dwi')['source_bpm'], '153.50')
        self.assertTrue(all(not d['exists'] and d['game_level'] is None and d['total_notes'] is None for d in detail['difficulties']))

    def test_failed_encoding_does_not_overwrite_saved_title(self):
        original = self.sync()
        self.file.write_bytes("#TITLE:測試;".encode("cp950"))
        failed = self.sync()
        self.assertIsNone(failed.songs[0].title)
        self.assertEqual(failed.songs[0].saved_title, "Original")
        self.assertTrue(failed.songs[0].is_present)
        self.assertEqual(failed.songs[0].song_id, original.songs[0].song_id)
        cached = self.library.load_report(self.root)
        self.assertIsNone(cached.songs[0].title)
        self.assertEqual(cached.songs[0].saved_title, "Original")
        self.assertIn("上次完整讀取歌名", render_html(cached))
        row = self.library.connection.execute("SELECT saved_title FROM song_sources").fetchone()
        self.assertEqual(row[0], "Original")

    def test_new_conflict_keeps_saved_title_and_all_evidence(self):
        first = self.sync()
        (self.folder / "another.dwi").write_text("#TITLE:Other;", encoding="utf-8")
        report = self.sync()
        self.assertEqual(report.songs[0].song_id, first.songs[0].song_id)
        self.assertIsNone(report.songs[0].title)
        self.assertEqual(report.songs[0].saved_title, "Original")
        self.assertEqual(len(report.songs[0].title_candidates), 2)
        self.assertEqual(report.database["totals"]["sources"], 2)

    def test_missing_source_marks_only_and_restores_same_ids(self):
        first = self.sync()
        db = self.library.connection
        difficulty_id = db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,notes_source) VALUES(?,?,?,?,?)",
                              (first.songs[0].song_id, "HARD", 25, 153, "game_screen")).lastrowid
        # Stand in for a later history table: rescanning must preserve its foreign key.
        db.execute("CREATE TABLE history_guard(id INTEGER PRIMARY KEY,difficulty_id INTEGER REFERENCES game_difficulties(difficulty_id))")
        db.execute("INSERT INTO history_guard(difficulty_id) VALUES(?)", (difficulty_id,))
        db.commit()
        self.file.unlink()
        missing = self.sync()
        self.assertEqual(missing.database["changes"]["missing_songs"], 1)
        self.assertEqual(missing.database["totals"]["missing_songs"], 1)
        self.assertEqual(db.execute("SELECT difficulty_id FROM history_guard").fetchone()[0], difficulty_id)
        self.assertEqual(db.execute("SELECT game_level,total_notes FROM game_difficulties").fetchone()[:], (25, 153))
        cached = self.library.load_report(self.root)
        self.assertFalse(cached.songs[0].is_present)
        self.assertIn("資料保留中", render_html(cached))
        self.file.write_text("#TITLE:Original;", encoding="utf-8")
        restored = self.sync()
        self.assertEqual(restored.songs[0].song_id, first.songs[0].song_id)
        self.assertEqual(restored.files[0].source_id, first.files[0].source_id)
        self.assertEqual(restored.database["changes"]["restored_songs"], 1)

    def test_partial_folder_scan_does_not_mark_other_pack_missing(self):
        other = self.root / "Pack AA" / "Song"
        other.mkdir(parents=True)
        other_file = other / "other.osu"
        other_file.write_text("osu file format v14\n[Metadata]\nTitle:Other\n", encoding="utf-8")
        self.sync()
        other_file.unlink()
        report = self.sync([self.folder])
        self.assertEqual(report.database["totals"]["present_songs"], 2)
        self.assertEqual(report.database["changes"]["missing_sources"], 0)

    def test_incomplete_scan_never_marks_absent_entries_missing(self):
        self.sync()
        self.file.unlink()
        for diagnostic in (Diagnostic("WALK_ERROR", "blocked", "error"), Diagnostic("SKIPPED_LINK", "not observed", "info")):
            report = scan_library(self.root)
            report.diagnostics.append(diagnostic)
            self.library.sync(report)
            self.assertFalse(report.database["complete_scan"])
            self.assertEqual(report.database["totals"]["present_songs"], 1)
        cancelled = scan_library(self.root)
        cancelled.cancelled = True
        self.library.sync(cancelled)
        self.assertEqual(cancelled.database["totals"]["present_songs"], 1)

    def test_full_scan_does_not_mark_deliberately_unvisited_deep_folder_missing(self):
        deeper = self.folder / "Explicit folder"
        deeper.mkdir()
        (deeper / "deep.sm").write_text("#TITLE:Deep;", encoding="utf-8")
        first = self.sync([deeper])
        self.assertEqual(first.database["totals"]["songs"], 1)
        report = self.sync()
        self.assertEqual(report.database["totals"]["present_songs"], 2)

    def test_two_roots_and_same_named_packs_songs_do_not_merge(self):
        first = self.sync()
        second_root = self.base / "SONG2"
        folder = second_root / "Pack A" / self.folder.name
        folder.mkdir(parents=True)
        (folder / "song.sm").write_text("#TITLE:Original;", encoding="utf-8")
        second = scan_library(second_root)
        self.library.sync(second)
        self.assertNotEqual(first.songs[0].song_id, second.songs[0].song_id)
        self.assertEqual(len(self.library.load_report(self.root).songs), 1)
        self.assertEqual(len(self.library.load_report(second_root).songs), 1)

    def test_foreign_keys_and_allowed_difficulties_are_enforced(self):
        report = self.sync()
        db = self.library.connection
        self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        for song_id, difficulty in ((999999, "HARD"), (report.songs[0].song_id, "Challenge")):
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO game_difficulties(song_id,difficulty) VALUES(?,?)", (song_id, difficulty))
            db.rollback()
        db.execute("INSERT INTO game_difficulties(song_id,difficulty) VALUES(?,?)", (report.songs[0].song_id, "HARD"))
        db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO game_difficulties(song_id,difficulty) VALUES(?,?)", (report.songs[0].song_id, "HARD"))
        db.rollback()

    def test_transaction_rolls_back_on_source_constraint_failure(self):
        self.sync()
        self.file.write_text("#TITLE:Changed;", encoding="utf-8")
        report = scan_library(self.root)
        report.files[0].format = "unsupported"
        with self.assertRaises(sqlite3.IntegrityError):
            self.library.sync(report)
        self.assertIsNone(report.database)
        self.assertEqual(self.library.connection.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0], 1)
        self.assertEqual(self.library.load_report(self.root).songs[0].saved_title, "Original")

    def test_unsafe_paths_and_unrelated_database_are_rejected(self):
        forbidden = self.root / "database.sqlite3"
        with self.assertRaises(ValueError):
            LibraryDatabase(forbidden, [self.root])
        self.assertFalse(forbidden.exists())
        unrelated = self.base / "other.sqlite3"
        with closing(sqlite3.connect(unrelated)) as db:
            db.execute("CREATE TABLE unrelated(data TEXT)")
            db.execute("INSERT INTO unrelated VALUES('keep')")
            db.commit()
        with self.assertRaises(ValueError):
            LibraryDatabase(unrelated)
        with closing(sqlite3.connect(unrelated)) as db:
            self.assertEqual(db.execute("SELECT data FROM unrelated").fetchone()[0], "keep")

    def test_read_only_library_view_works_without_song_files(self):
        first = self.sync()
        self.file.unlink()
        with LibraryDatabase(self.path, [self.root], read_only=True) as read_only:
            cached = read_only.load_report(self.root)
            self.assertEqual(cached.songs[0].song_id, first.songs[0].song_id)
            self.assertEqual(cached.view_mode, "database")
            self.assertEqual(cached.files[0].title, "Original")
            with self.assertRaises(ValueError):
                read_only.sync(cached)

    def test_cached_view_includes_retired_sources_and_cannot_be_resynced(self):
        self.sync()
        self.file.unlink()
        replacement = self.folder / "replacement.dwi"
        replacement.write_text("#TITLE:Original;", encoding="utf-8")
        self.sync()
        cached = self.library.load_report(self.root)
        self.assertEqual(len(cached.songs[0].source_paths), 2)
        self.assertEqual(sum(source.is_present is False for source in cached.files), 1)
        self.assertIn("上次完整掃描未找到此來源", render_html(cached))
        with self.assertRaises(ValueError):
            self.library.sync(cached)

    def test_cli_library_view_does_not_scan_and_database_output_is_protected(self):
        from dance_results.__main__ import main
        self.sync()
        arguments = ["--root", str(self.root), "--database", str(self.path), "--output", str(self.base / "reports")]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch("dance_results.__main__.scan_library") as scanner:
            self.assertEqual(main(arguments + ["--library-only"]), 0)
            scanner.assert_not_called()
            self.assertEqual(main(["--root", str(self.root), "--database", str(self.root / "bad.sqlite3")]), 2)
            scanner.assert_not_called()
        self.assertFalse((self.root / "bad.sqlite3").exists())

    def test_connect_is_download_pack_and_no_game_difficulties_are_invented(self):
        folder = self.root / "connect" / "downloaded"
        folder.mkdir(parents=True)
        (folder / "song.sm").write_text("#TITLE:Downloaded;", encoding="utf-8")
        report = self.sync()
        row = self.library.connection.execute("SELECT p.name,p.pack_type FROM songs s JOIN packs p ON p.pack_id=s.pack_id WHERE s.storage_group='connect'").fetchone()
        self.assertEqual(tuple(row), ("connect", "multiplayer_download"))
        self.assertEqual(report.database["totals"]["game_difficulties"], 0)
        self.assertTrue(all(song.available_difficulties is None for song in report.songs))

    def test_titles_with_quotes_are_data_not_sql(self):
        title = "Name'); DROP TABLE songs; --"
        escaped_title = title.replace(";", "\\;")
        self.file.write_text(f"#TITLE:{escaped_title};", encoding="utf-8")
        report = self.sync()
        self.assertEqual(report.songs[0].title, title)
        self.assertEqual(self.library.connection.execute("SELECT COUNT(*) FROM songs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
