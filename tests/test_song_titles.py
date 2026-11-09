from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from dance_results.models import GAME_DIFFICULTIES
from dance_results.parsers import parse_file
from dance_results.report import write_report
from dance_results.scanner import scan_library

SM = """#TITLE:EOS;#SUBTITLE:THE BMS OF FIGHTERS 2012;
#ARTIST:ginkiha;#BPMS:invalid BPM;#OFFSET:not a number;
#NOTES:unknown-mode::Challenge:9999:garbage:
NOT A VALID CHART
;"""
DWI = "#TITLE:EOS THE BMS OF FIGHTERS 2012;#SINGLE:SMANIAC:9999:<not valid>;"
OSU = """osu file format v14
[General]
Mode: 0
[Metadata]
Title:Song
TitleUnicode:歌曲
Artist:unused
Version:Not Getting Thru These SVs
[Difficulty]
OverallDifficulty:99
CircleSize:invalid
[TimingPoints]
broken timing
[HitObjects]
broken objects
"""


class SongTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "SONG"
        self.song = self.root / "Pack A" / "歌曲[方括號]"
        self.song.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, text, folder=None):
        path = (folder or self.song) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_sm_and_dwi_only_read_title_not_chart_data(self):
        sm = parse_file(self.put("a.sm", SM))
        dwi = parse_file(self.put("a.dwi", DWI))
        self.assertEqual(sm.title, "EOS")
        self.assertEqual(sm.subtitle, "THE BMS OF FIGHTERS 2012")
        self.assertEqual(dwi.title, "EOS THE BMS OF FIGHTERS 2012")
        self.assertEqual((sm.status, dwi.status), ("ok", "ok"))
        self.assertEqual(set(sm.title_fields), {"TITLE", "SUBTITLE"})
        self.assertFalse(hasattr(sm, "charts"))

    def test_osu_ignores_version_mode_difficulty_and_corrupt_notes(self):
        source = parse_file(self.put("a.osu", OSU))
        self.assertEqual(source.title, "歌曲")
        self.assertEqual(source.status, "ok")
        self.assertEqual(set(source.title_fields), {"Title", "TitleUnicode"})
        exported = source.to_dict()
        for key in ("charts", "events", "timing", "source_level", "metadata", "parsed_object_count"):
            self.assertNotIn(key, exported)

    def test_dwi_basic_bpm_preserves_raw_value_and_ignores_notes_and_tempo_changes(self):
        source = parse_file(self.put('basic.dwi', '// #BPM:999;\n#TITLE:歌曲;#ARTIST:作者;#BPM:0153.500;\n#CHANGEBPM:not valid;#SINGLE:SMANIAC:9999:broken;'))
        self.assertEqual((source.title, source.artist, source.source_bpm), ('歌曲', '作者', '0153.500'))
        self.assertEqual(source.source_bpm_fields, ['0153.500'])
        self.assertEqual(source.metadata_encodings['BPM'], ['utf-8-sig'])
        self.assertEqual(source.status, 'ok')
        self.assertFalse(hasattr(source, 'charts'))

    def test_dwi_bpm_conflict_and_unterminated_tag_do_not_make_title_unreliable(self):
        for raw, values in (('#BPM:120;#BPM:150;', ['120', '150']), ('#BPM:120', ['120'])):
            source = parse_file(self.put('conflict.dwi', '#TITLE:Song;\n'+raw))
            self.assertIsNone(source.source_bpm)
            self.assertEqual(source.source_bpm_fields, values)
            self.assertTrue(source.title_reliable)
            self.assertEqual(source.status, 'ok')

    def test_dwi_basic_bpm_survives_legacy_metadata_recovery(self):
        path = self.song/'legacy.dwi'
        raw = '#TITLE:中文;\n#ARTIST:作者;\n#BPM:153.5;\n#CHANGEBPM:bad;'.encode('cp950')
        path.write_bytes(raw)
        source = parse_file(path, legacy_encoding='cp950')
        self.assertEqual((source.title, source.artist, source.source_bpm), ('中文', '作者', '153.5'))
        self.assertEqual(source.metadata_encodings['BPM'], ['utf-8'])
        self.assertEqual(source.raw_data, raw)

    def test_two_osu_versions_are_one_song_folder_not_difficulties(self):
        self.put("getting.osu", OSU)
        self.put("not_getting.osu", OSU.replace("Not Getting Thru These SVs", "Getting Thru These SVs"))
        report = scan_library(self.root)
        self.assertEqual(len(report.files), 2)
        self.assertEqual(len(report.songs), 1)
        song = report.songs[0]
        self.assertEqual(song.title, "歌曲")
        self.assertIsNone(song.available_difficulties)
        self.assertEqual(song.game_levels, {})
        self.assertEqual(song.game_total_notes, {})
        self.assertEqual(GAME_DIFFICULTIES, ("EASY", "NORMAL", "HARD"))
        self.assertFalse(report.to_dict()["rules"]["file_chart_deduplication"])

    def test_sm_dwi_title_subtitle_remain_one_song(self):
        self.put("a.sm", SM)
        self.put("a.dwi", DWI)
        report = scan_library(self.root)
        self.assertEqual(len(report.songs), 1)
        self.assertEqual(report.songs[0].title, "EOS THE BMS OF FIGHTERS 2012")
        self.assertEqual(report.songs[0].status, "ok")
        self.assertEqual(report.songs[0].format_status, "mixed")
        self.assertIsNone(report.songs[0].source_format)
        self.assertEqual(len(report.songs[0].source_paths), 2)

    def test_cross_pack_names_are_never_merged(self):
        self.put("a.sm", SM)
        second = self.root / "Pack B" / self.song.name
        second.mkdir(parents=True)
        self.put("a.sm", SM, second)
        report = scan_library(self.root)
        self.assertEqual(len(report.songs), 2)
        self.assertNotEqual(report.songs[0].song_folder, report.songs[1].song_folder)

    def test_conflicting_source_titles_not_chosen_arbitrarily(self):
        self.put("a.sm", SM)
        self.put("b.sm", SM.replace("#TITLE:EOS", "#TITLE:Different Song"))
        song = scan_library(self.root).songs[0]
        self.assertIsNone(song.title)
        self.assertEqual(len(song.title_candidates), 2)
        self.assertEqual(song.status, "partial")

    def test_comments_and_escaped_delimiters(self):
        source = parse_file(self.put("a.sm", '// #TITLE:Wrong;\n#TITLE:Part\\: One\\; Two;\n#NOTES:broken'))
        self.assertEqual(source.title, "Part: One; Two")
        self.assertEqual(source.status, "ok")

    def test_non_title_structure_errors_do_not_create_warnings(self):
        source = parse_file(self.put("a.sm", "#BPMS:broken\n#TITLE:Known;\n#NOTES:not terminated"))
        self.assertEqual(source.title, "Known")
        self.assertEqual(source.status, "ok")
        self.assertFalse(source.diagnostics)

    def test_missing_title_terminator_remains_visible(self):
        source = parse_file(self.put("a.sm", "#TITLE:Known\n#NOTES:broken;"))
        self.assertEqual(source.title, "Known")
        self.assertEqual(source.status, "partial")
        self.assertEqual(source.diagnostics[0].code, "UNTERMINATED_TITLE_TAG")

    def test_duplicate_title_fields_are_not_chosen(self):
        source = parse_file(self.put("a.sm", "#TITLE:One;#TITLE:Two;"))
        self.assertIsNone(source.title)
        self.assertEqual(source.title_fields["TITLE"], ["One", "Two"])
        self.assertEqual(source.status, "failed")

    def test_missing_title_does_not_use_folder_or_version(self):
        source = parse_file(self.put("a.osu", "osu file format v14\n[Metadata]\nVersion:HARD\n"))
        self.assertIsNone(source.title)
        self.assertEqual(source.status, "failed")

    def test_utf16_and_explicit_legacy_encoding(self):
        path = self.song / "a.sm"
        path.write_bytes("#TITLE:測試;".encode("utf-16"))
        self.assertEqual(parse_file(path).title, "測試")
        path.write_bytes("#TITLE:測試;".encode("cp950"))
        self.assertEqual(parse_file(path).status, "failed")
        self.assertEqual(parse_file(path, "cp950").title, "測試")

    def test_backup_sidecars_nested_directories_are_excluded(self):
        self.put("a.sm", SM)
        self.put("a.dwi.old", DWI)
        self.put("a.osb", "storyboard")
        self.put("dance_net.dps", "// untrusted content")
        self.put("sdoinfo.dat", "#DPS:dance_net.dps;")
        backup = self.song / "FileBackup"
        backup.mkdir()
        self.put("old.sm", "#TITLE:Old;", backup)
        report = scan_library(self.root)
        self.assertEqual(len(report.files), 1)
        self.assertEqual(len(report.auxiliary_files), 4)
        self.assertTrue(any(d.code == "NESTED_DIRECTORY_SKIPPED" for d in report.diagnostics))

    def test_connect_storage_is_download_management_pack(self):
        folder = self.root / "connect" / "downloaded"
        folder.mkdir(parents=True)
        self.put("a.sm", SM, folder)
        report = scan_library(self.root)
        self.assertEqual(report.songs[0].pack_name, "connect")
        self.assertEqual(report.songs[0].pack_type, "multiplayer_download")
        self.assertEqual(report.songs[0].storage_group, "connect")

    def test_partial_sources_do_not_hide_known_title(self):
        self.put("valid.sm", SM)
        self.put("no_title.dwi", "#SINGLE:BASIC:1:00;")
        song = scan_library(self.root).songs[0]
        self.assertEqual(song.title, "EOS THE BMS OF FIGHTERS 2012")
        self.assertEqual(song.status, "partial")
        self.assertEqual(len(song.source_paths), 2)

    def test_rescan_determinism_and_read_only(self):
        source = self.put("a.sm", SM)
        original = source.read_bytes()
        first, second = scan_library(self.root), scan_library(self.root)
        self.assertEqual(first.files[0].to_dict(), second.files[0].to_dict())
        self.assertEqual(source.read_bytes(), original)

    def test_json_html_escape_and_output_protection(self):
        self.put("a.sm", '#TITLE:<script>alert("x")</script>;')
        report = scan_library(self.root)
        with self.assertRaises(ValueError):
            write_report(report, self.song / "reports")
        output = write_report(report, self.base / "reports")
        rendered = output.read_text(encoding="utf-8")
        self.assertNotIn('<script>alert("x")</script>', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("解析物件數", rendered)
        self.assertNotIn("原始難度", rendered)
        exported = json.loads(output.with_name("report.json").read_text(encoding="utf-8"))
        self.assertEqual(exported["schema_version"], 5)
        self.assertNotIn("source_chart_entry_count", exported["summary"])
        self.assertEqual(exported["songs"][0]["game_levels"], {})

    def test_scope_overlap_and_escape(self):
        self.put("a.sm", SM)
        self.assertEqual(len(scan_library(self.root, [self.song, self.root]).files), 1)
        with self.assertRaises(ValueError):
            scan_library(self.root, [self.base])

    def test_cli_output_protection_and_cancel(self):
        from dance_results.__main__ import main
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch("dance_results.__main__.scan_library") as scanner:
            self.assertEqual(main(["--root", str(self.root), "--output", str(self.song / "reports")]), 2)
            scanner.assert_not_called()
        with redirect_stdout(StringIO()), patch("dance_results.__main__.choose_directory", return_value=None):
            self.assertEqual(main(["--choose-directory"]), 0)


class ProvidedSampleTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("DANCE_SAMPLE_ROOT"), "設定 DANCE_SAMPLE_ROOT 才讀取外部樣本")
    def test_provided_samples_are_two_songs_with_no_file_difficulties(self):
        root = Path(os.environ["DANCE_SAMPLE_ROOT"])
        folders = [Path("Blue's 1st step/eos[Blue]"), Path("osu音樂/Gotta Get Thru This (Jauz RetroFuture Remix) (Nightcore [2b5a993e]")]
        files = [p for folder in folders for p in (root / folder).iterdir() if p.is_file()]
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        report = scan_library(root, folders)
        self.assertEqual(len(report.files), 4)
        self.assertEqual(len(report.songs), 2)
        self.assertTrue(all(f.status == "ok" for f in report.files))
        self.assertEqual(report.songs[0].title, "EOS THE BMS OF FIGHTERS 2012")
        self.assertEqual(report.songs[1].title, "Gotta Get Thru This (Jauz RetroFuture Remix) (Nightcore Mix)")
        for song in report.songs:
            self.assertIsNone(song.available_difficulties)
            self.assertEqual(song.game_levels, {})
            self.assertEqual(song.game_total_notes, {})
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


if __name__ == "__main__":
    unittest.main()
