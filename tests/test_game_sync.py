"""Game folder mode: song lists and results read straight from the game, no OCR."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from dance_results import app_paths, song_cache
from dance_results.database import LibraryDatabase
from dance_results.game_folder import GameFolderError, character_results, resolve
from dance_results.game_sync import GameSync, parse_line, ResultLineError, song_folder_key, _split_root
from dance_results.official_import import REQUIRED


# ---------------------------------------------------------------- fixtures


def _s(text: str) -> bytes:
    data = text.encode('utf-8')
    length, out = len(data), bytearray()
    while True:
        byte = length & 0x7F
        length >>= 7
        out.append(byte | (0x80 if length else 0))
        if not length:
            break
    return bytes(out) + data


def _path(value: str, relative: bool = True) -> bytes:
    return bytes([1 if relative else 0]) + _s(value)


def cache_bytes(roots, folders, version=35) -> bytes:
    """Same layout as ExternalScanCache.Write in the game (v35)."""
    out = bytearray(struct.pack('<Ii', 0x53444F43, version))
    out += _s('parse') + _s('diff') + struct.pack('<i', len(roots))
    for root in roots:
        out += _s(root)
    out += struct.pack('<i', len(folders))
    for root_index, relative, group, songs in folders:
        out += struct.pack('<i', root_index) + _s(relative) + _s('sig') + _s(group) + struct.pack('<i', len(songs))
        for song in songs:
            out += _s(song['key']) + _s(song['title']) + _s(song['title']) + _s(song['artist']) + _s('')
            for _ in range(7):
                out += _path('')
            out += _s(song['pack_id']) + struct.pack('<dd', 150.0, 150.0)
            out += struct.pack('<iiiii', song.get('format', 2), 0, 0, 90, 0) + struct.pack('<q', 0)
            out += struct.pack('<i', len(song['charts']))
            for slot, level, notes in song['charts']:
                out += struct.pack('<i', slot) + _path('\\song.sm') + struct.pack('<iiii', slot, notes, level, 90)
                out += struct.pack('<f', 1.0) + struct.pack('<d', 1.0) + struct.pack('<f', 1.0)
                out += struct.pack('<ddd', 0, 0, 0) + struct.pack('<ii', notes, notes) + _s('')
    return bytes(out)


def csv_bytes(rows) -> bytes:
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=[*REQUIRED, 'hidden'])
    writer.writeheader()
    writer.writerows(rows)
    return b'\xef\xbb\xbf' + stream.getvalue().encode('utf-8')


def k_row(**changes):
    return {
        'fileId': '15023', 'title': 'Silly-Go-Round', 'artist': 'Yuuka', 'bpm': '163', 'mode': 'K', 'gn': 'sdom5023k.gn',
        'lvEasy': '7', 'notesEasy': '363', 'lvNormal': '9', 'notesNormal': '436', 'lvHard': '18', 'notesHard': '747',
        'hidden': '', **changes,
    }


def official_line(**changes):
    line = {
        'v': 1, 'at': '2026-11-17T12:42:16Z', 'player': 'Eithwa',
        'song': {'kind': 'official', 'title': 'Silly-Go-Round', 'artist': 'Yuuka', 'gn': 'sdom5023k.gn', 'fileId': 15023},
        'difficulty': 'hard', 'level': 18, 'chartJudged': 747,
        'perfect': 735, 'cool': 12, 'bad': 0, 'miss': 0, 'maxCombo': 747, 'score': 52703,
        'accuracy': 100, 'grade': 'A++', 'failed': False, 'online': True, 'host': False, 'mode': 1, 'players': 2,
    }
    line.update(changes)
    return line


class GameFixture:
    """A fake installation: dance.exe, song_table.csv, DATA via data_root.txt, one character."""

    def __init__(self, base: Path):
        self.base = base
        self.game = base / 'game'
        self.data = base / 'elsewhere' / 'DATA'
        self.songs_root = base / 'Songs'
        (self.game / 'dance_Data' / 'StreamingAssets').mkdir(parents=True)
        (self.game / 'dance.exe').write_bytes(b'')
        (self.game / 'data_root.txt').write_text(str(self.data) + '\n', encoding='utf-8')
        (self.data / 'CACHE').mkdir(parents=True)
        self.character = self.data / 'PROFILE' / 'accounts' / 'acc' / '656974687761'
        self.character.mkdir(parents=True)
        (self.character / 'profile.json').write_text(json.dumps({'name': 'Eithwa'}), encoding='utf-8')
        self.write_csv([k_row()])
        self.external = self.songs_root / 'Pack' / 'Song'
        self.write_cache([
            (0, '\\Pack\\Song', 'Pack', [
                {'key': '', 'title': '外部曲', 'artist': '作者', 'pack_id': 'sha256:abc', 'charts': [(0, 3, 200), (2, 12, 640)]},
            ]),
            (0, '\\Pack\\Bundle', 'Pack', [
                {'key': 'a.gn', 'title': '包裡第一首', 'artist': 'A', 'pack_id': 'sha256:a', 'format': 3, 'charts': [(2, 9, 500)]},
                {'key': 'b.gn', 'title': '包裡第二首', 'artist': 'B', 'pack_id': 'sha256:b', 'format': 3, 'charts': [(2, 10, 520)]},
            ]),
        ])
        self.database = base / 'tool' / 'data' / 'library.sqlite3'
        self.database.parent.mkdir(parents=True)
        with LibraryDatabase(self.database, []):
            pass
        self.sync = GameSync(self.database, self.database.parent)
        self.sync.configure(str(self.game))

    def write_csv(self, rows):
        (self.game / 'dance_Data' / 'StreamingAssets' / 'song_table.csv').write_bytes(csv_bytes(rows))

    def write_cache(self, folders):
        (self.data / 'CACHE' / 'external_song_cache.json').write_bytes(cache_bytes([str(self.songs_root)], folders))

    def append(self, *lines, newline=True):
        with open(self.character / 'results.jsonl', 'ab') as handle:
            for i, line in enumerate(lines):
                text = line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)
                handle.write(text.encode('utf-8') + (b'\n' if newline or i < len(lines) - 1 else b''))

    def db(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def records(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM play_records ORDER BY record_id')]


def external_line(fixture, **changes):
    song = {'kind': 'external', 'title': '外部曲', 'artist': '作者', 'packId': 'sha256:abc',
            'folder': str(fixture.external), 'songKey': '', 'chart': str(fixture.external / 'song.sm'),
            'chartIndex': 0, 'format': 2}
    song.update(changes.pop('song', {}))
    return official_line(song=song, difficulty='hard', level=12, chartJudged=640,
                         perfect=300, cool=20, bad=5, miss=15, maxCombo=90, score=20000,
                         failed=True, online=False, host=True, mode=0, players=1, **changes)


# ---------------------------------------------------------------- cache reader


class SongCacheTests(unittest.TestCase):
    def test_reads_every_song_with_its_folder_and_charts(self):
        cache = song_cache.parse(cache_bytes(['C:\\Songs'], [
            (0, '\\Pack\\Song', 'Pack', [
                {'key': '', 'title': '歌', 'artist': '人', 'pack_id': 'sha256:1', 'charts': [(0, 3, 100), (2, 11, 480)]},
            ]),
            (0, '\\Pack\\Bundle', 'Pack', [
                {'key': 'x.gn', 'title': 'X', 'artist': '', 'pack_id': 'sha256:x', 'format': 3, 'charts': [(1, 5, 200)]},
                {'key': 'y.gn', 'title': 'Y', 'artist': '', 'pack_id': 'sha256:y', 'format': 3, 'charts': []},
            ]),
        ]))
        self.assertEqual(35, cache.version)
        self.assertEqual(['C:\\Songs'], cache.roots)
        self.assertEqual(3, len(cache.songs))
        first = cache.songs[0]
        self.assertEqual('C:\\Songs\\Pack\\Song', first.folder)
        self.assertEqual('Pack\\Song', first.relative_folder)
        self.assertEqual(('歌', '人', 'sha256:1', 'Pack'), (first.title, first.artist, first.pack_id, first.group))
        self.assertEqual([(0, 3, 100), (2, 11, 480)], [(c.slot, c.level, c.notes) for c in first.charts])
        self.assertEqual('C:\\Songs\\Pack\\Song\\song.sm', first.charts[0].file)
        self.assertEqual(['x.gn', 'y.gn'], [s.song_key for s in cache.songs[1:]])

    def test_refuses_an_unknown_version_instead_of_guessing(self):
        with self.assertRaisesRegex(song_cache.CacheFormatError, '版本 36'):
            song_cache.parse(cache_bytes(['C:\\Songs'], [], version=36))

    def test_refuses_other_files_and_truncated_or_padded_data(self):
        good = cache_bytes(['C:\\Songs'], [(0, '\\P\\S', 'P', [
            {'key': '', 'title': 'A', 'artist': '', 'pack_id': '', 'charts': [(0, 1, 10)]}])])
        for broken in (b'{"songs":[]}', good[:-3], good + b'\x00'):
            with self.subTest(size=len(broken)), self.assertRaises(song_cache.CacheFormatError):
                song_cache.parse(broken)


# ---------------------------------------------------------------- game folder


class GameFolderTests(unittest.TestCase):
    def test_data_root_override_and_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = GameFixture(Path(temp))
            offline = fixture.data / 'PROFILE' / '00000000'
            offline.mkdir(parents=True)
            retired = fixture.data / 'PROFILE' / 'accounts' / 'acc' / '_retired' / 'old'
            retired.mkdir(parents=True)
            folder = resolve(str(fixture.game / 'dance.exe'))  # pointing at the exe works too
            self.assertEqual(fixture.game, folder.game_dir)
            self.assertEqual(fixture.data.resolve(), folder.data_dir.resolve())
            names = [(c.name, c.account) for c in character_results(folder.profile_dir)]
            self.assertEqual([('00000000', None), ('Eithwa', 'acc')], names)

    def test_a_folder_without_the_game_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(GameFolderError, 'dance.exe'):
                resolve(temp)
            with self.assertRaises(GameFolderError):
                resolve('')

    def test_default_data_folder_without_override(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            (game / 'dance.exe').write_bytes(b'')
            self.assertEqual(game.resolve() / 'DATA', resolve(str(game)).data_dir)


# ---------------------------------------------------------------- results


class GameSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = GameFixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_official_result_becomes_a_formal_record_with_lamp_and_best(self):
        self.fixture.append(official_line())
        status = self.fixture.sync.run_once()
        self.assertEqual('watching', status['state'], status)
        self.assertEqual(1, status['imported_total'])
        [record] = self.fixture.records()
        self.assertEqual(('COMPLETED', 'AC', 1482, 1494, 'verified_auto'),
                         (record['completion_status'], record['lamp_status'], record['ex_score'],
                          record['max_score'], record['confirmation_method']))
        self.assertEqual((747, 52703, 'A++', 18), (record['max_combo'], record['game_score'], record['grade'], record['game_level']))
        with self.fixture.db() as db:
            best = db.execute("SELECT highest_ex_score,best_clear_lamp FROM difficulty_bests WHERE difficulty='HARD' "
                              "AND highest_ex_score IS NOT NULL").fetchone()
            session = db.execute('SELECT * FROM play_sessions').fetchone()
        self.assertEqual((1482, 'AC'), tuple(best))
        self.assertEqual(('Eithwa', 'multiplayer', 0, 'official'),
                         (session['player_name'], session['play_mode'], session['is_host'], session['source_type']))
        evidence = json.loads(record['evidence_json'])
        self.assertEqual('FIRST AC', evidence['achievement_feedback']['primary'])

    def test_each_line_is_imported_once(self):
        self.fixture.append(official_line())
        self.fixture.sync.run_once()
        self.fixture.sync.run_once()
        # Losing the read position (state file deleted) must not duplicate anything.
        self.fixture.sync.state_path.unlink()
        self.fixture.sync.run_once()
        self.assertEqual(1, len(self.fixture.records()))

    def test_a_line_still_being_written_waits_for_its_newline(self):
        text = json.dumps(official_line(), ensure_ascii=False)
        self.fixture.append(text[:40], newline=False)
        self.fixture.sync.run_once()
        self.assertEqual([], self.fixture.records())
        with open(self.fixture.character / 'results.jsonl', 'ab') as handle:
            handle.write(text[40:].encode('utf-8') + b'\n')
        self.fixture.sync.run_once()
        self.assertEqual(1, len(self.fixture.records()))

    def test_game_over_is_saved_as_failed_with_the_whole_chart_as_max(self):
        self.fixture.append(external_line(self.fixture))
        status = self.fixture.sync.run_once()
        self.assertEqual([], status['rejected'])
        [record] = self.fixture.records()
        self.assertEqual(('FAILED', 'FAILED', 620, 1280), (record['completion_status'], record['lamp_status'],
                                                            record['ex_score'], record['max_score']))

    def test_external_songs_come_from_the_game_cache_one_row_per_song(self):
        self.fixture.sync.run_once()
        with self.fixture.db() as db:
            rows = db.execute("SELECT s.latest_title,s.folder_key,p.name FROM songs s JOIN packs p USING(pack_id) "
                              "WHERE s.source_type='custom' ORDER BY s.folder_key").fetchall()
            hard = db.execute("SELECT d.game_level,d.total_notes,d.notes_source FROM game_difficulties d JOIN songs s USING(song_id) "
                              "WHERE s.latest_title='外部曲' AND d.difficulty='HARD'").fetchone()
        self.assertEqual(['包裡第一首', '包裡第二首', '外部曲'], [r['latest_title'] for r in rows])
        self.assertEqual({'Pack'}, {r['name'] for r in rows})
        self.assertEqual((12, 640, 'game_screen'), tuple(hard))

    def test_a_moved_song_is_found_by_its_pack_id(self):
        line = external_line(self.fixture, song={'folder': str(self.fixture.songs_root / 'Old' / 'Place')})
        self.fixture.append(line)
        self.fixture.sync.run_once()
        [record] = self.fixture.records()
        with self.fixture.db() as db:
            folder_key = db.execute('SELECT s.folder_key FROM game_difficulties d JOIN songs s USING(song_id) '
                                    'WHERE d.difficulty_id=?', (record['difficulty_id'],)).fetchone()[0]
            copies = db.execute("SELECT count(*) FROM songs WHERE latest_title='外部曲'").fetchone()[0]
        self.assertEqual('pack/song', folder_key, '要記在原本那首歌上,不是另外新建一首')
        self.assertEqual(1, copies)

    def test_an_external_song_unknown_to_the_cache_is_still_saved(self):
        folder = self.fixture.songs_root / 'Downloads' / 'New Song'
        self.fixture.append(external_line(self.fixture, song={'folder': str(folder), 'packId': 'sha256:new', 'title': '剛下載'}))
        status = self.fixture.sync.run_once()
        self.assertEqual(1, status['imported_total'], status)
        with self.fixture.db() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM songs WHERE latest_title='剛下載'").fetchone()[0])

    def test_unknown_official_song_waits_until_song_table_lists_it(self):
        self.fixture.append(official_line(song={'kind': 'official', 'title': 'New', 'gn': 'sdom9999k.gn', 'fileId': 19999},
                                          level=5, chartJudged=300, perfect=300, cool=0, maxCombo=300))
        status = self.fixture.sync.run_once()
        self.assertEqual(1, len(status['pending']))
        self.assertEqual([], self.fixture.records())
        self.fixture.write_csv([k_row(), k_row(fileId='19999', gn='sdom9999k.gn', title='New', lvHard='5', notesHard='300')])
        status = self.fixture.sync.run_once()
        self.assertEqual([], status['pending'])
        self.assertEqual('ACP', self.fixture.records()[0]['lamp_status'])

    def test_a_bad_line_is_reported_and_does_not_block_the_next(self):
        self.fixture.append('{not json', official_line(maxCombo=9999), official_line(at='2026-11-17T12:50:00Z'))
        status = self.fixture.sync.run_once()
        self.assertEqual(2, len(status['rejected']))
        self.assertEqual(1, len(self.fixture.records()))

    def test_the_game_is_trusted_when_its_lv_or_note_count_differs_from_the_csv(self):
        self.fixture.append(official_line(level=17, chartJudged=748, perfect=736))
        self.fixture.sync.run_once()
        [record] = self.fixture.records()
        self.assertEqual((17, 748, 'AC'), (record['game_level'], record['total_notes'], record['lamp_status']))

    def test_song_table_edits_update_the_catalog_in_place(self):
        self.fixture.sync.run_once()
        self.fixture.write_csv([k_row(title='Silly-Go-Round (new)', notesHard='750')])
        self.fixture.sync.run_once()
        with self.fixture.db() as db:
            song = db.execute("SELECT song_id,latest_title FROM songs WHERE source_type='official'").fetchall()
            notes = db.execute("SELECT total_notes FROM game_difficulties d JOIN songs s USING(song_id) "
                               "WHERE s.source_type='official' AND d.difficulty='HARD'").fetchone()[0]
        self.assertEqual(['Silly-Go-Round (new)'], [r['latest_title'] for r in song])
        self.assertEqual(750, notes)

    def test_backups_are_taken_once_per_batch_and_pruned(self):
        for minute in range(12):
            self.fixture.append(official_line(at=f'2026-11-17T13:{minute:02d}:00Z'))
            self.fixture.sync.run_once()
        backups = list((self.fixture.database.parent / 'backups').glob('*.pre-game-sync.*.sqlite3'))
        self.assertEqual(10, len(backups))
        self.assertEqual(12, len(self.fixture.records()))


class CharacterChoiceTests(unittest.TestCase):
    """Bests and lamps are per library, so only one character's results are followed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = GameFixture(Path(self.temp.name))
        (self.fixture.character / 'profile.json').write_text(
            json.dumps({'name': 'Eithwa', 'lastPlayedAt': '2026-11-17T13:27:23Z'}), encoding='utf-8')
        self.other = self.fixture.data / 'PROFILE' / '00000001'
        self.other.mkdir(parents=True)
        (self.other / 'profile.json').write_text(
            json.dumps({'name': '淡藍x', 'lastPlayedAt': '2026-10-22T23:21:20Z'}), encoding='utf-8')
        (self.other / 'results.jsonl').write_text(
            json.dumps(official_line(player='淡藍x', at='2026-10-22T23:00:00Z'), ensure_ascii=False) + '\n', encoding='utf-8')
        self.fixture.append(official_line())
        self.fixture.sync.configure(str(self.fixture.game))  # re-pick now that both have results

    def tearDown(self):
        self.temp.cleanup()

    def players(self):
        with self.fixture.db() as db:
            return sorted(r[0] for r in db.execute('SELECT player_name FROM play_sessions'))

    def test_defaults_to_the_most_recently_played_character_and_imports_only_it(self):
        status = self.fixture.sync.run_once()
        self.assertEqual('accounts/acc/656974687761', status['character'])
        self.assertEqual('Eithwa（線上 · acc）', status['character_label'])
        self.assertEqual(['Eithwa'], self.players())
        labels = {c['label']: c['selected'] for c in status['characters']}
        self.assertEqual({'淡藍x（單機）': False, 'Eithwa（線上 · acc）': True}, labels)

    def test_switching_character_follows_the_other_file(self):
        self.fixture.sync.run_once()
        self.fixture.sync.configure(str(self.fixture.game), '00000001')
        self.fixture.sync.run_once()
        self.assertEqual(['Eithwa', '淡藍x'], self.players())

    def test_saving_runs_one_pass_and_says_what_it_did(self):
        status = self.fixture.sync.sync_now()
        self.assertEqual(1, status['last_run']['imported'])
        status = self.fixture.sync.sync_now()
        self.assertEqual(0, status['last_run']['imported'], '第二次按:沒有新成績')

    def test_the_click_counts_results_a_background_pass_stored_first(self):
        # 按下「儲存並同步」的瞬間,背景那一輪剛好先跑完、把成績存掉了:回報仍要算進去,不能說「沒有新成績」。
        sync = self.fixture.sync
        configure = sync.configure

        def configure_then_background_pass(*args, **kwargs):
            result = configure(*args, **kwargs)
            sync.run_once()
            return result

        with patch.object(sync, 'configure', configure_then_background_pass):
            status = sync.save_and_sync(str(self.fixture.game))
        self.assertEqual(1, status['last_run']['imported'])
        self.assertTrue(status['last_run']['catalog_changed'])

    def test_switching_to_another_game_clears_what_was_shown_for_the_old_one(self):
        self.fixture.sync.sync_now()
        other = GameFixture(Path(self.temp.name) / 'second')
        status = self.fixture.sync.save_and_sync(str(other.game))
        self.assertEqual([], status['last_imported'])
        self.assertEqual(0, status['last_run']['imported'])

    def test_saving_the_same_folder_keeps_the_chosen_character(self):
        self.fixture.sync.configure(str(self.fixture.game), '00000001')
        status = self.fixture.sync.configure(str(self.fixture.game))
        self.assertEqual('00000001', status['character'])

    def test_an_unknown_character_is_refused(self):
        with self.assertRaisesRegex(GameFolderError, '沒有這個角色'):
            self.fixture.sync.configure(str(self.fixture.game), 'accounts/acc/nobody')


class CombinedLibraryTests(unittest.TestCase):
    """The song list shows every location together (official, ADDON\\SONG, StepMania, osu...)."""

    def test_root_labels(self):
        from dance_results.library_service import root_label
        self.assertEqual('', root_label('D:\\Dance v2.0.0\\DATA\\ADDON\\SONG'))
        self.assertEqual('StepMania3.9', root_label('D:\\program\\StepMania3.9\\Songs'))
        self.assertEqual('osu!', root_label('C:\\Users\\u\\AppData\\Local\\osu!\\Songs'))
        self.assertEqual('MySongs', root_label('E:\\MySongs'))
        self.assertEqual('', root_label('official://dance_online/K'))

    def test_every_location_in_one_list(self):
        from dance_results.library_service import LibraryService
        with tempfile.TemporaryDirectory() as temp:
            fixture = GameFixture(Path(temp))
            second = Path(temp) / 'osu!' / 'Songs'
            (fixture.data / 'CACHE' / 'external_song_cache.json').write_bytes(cache_bytes(
                [str(fixture.songs_root), str(second)],
                [(0, '\\Pack\\Song', 'Pack', [{'key': '', 'title': '甲', 'artist': '', 'pack_id': 'a', 'charts': [(2, 5, 100)]}]),
                 (1, '\\Set\\Map', 'Set', [{'key': '', 'title': '乙', 'artist': '', 'pack_id': 'b', 'format': 1, 'charts': [(2, 6, 120)]}])]))
            fixture.sync.run_once()
            service = LibraryService(fixture.database)
            packs = service.packs(None)
            self.assertEqual(3, packs['song_count'])  # 1 official + 2 external from two locations
            # <x>\Songs is named after its parent folder, like StepMania3.9\Songs and osu!\Songs.
            self.assertEqual({('Pack', Path(temp).name), ('Set', 'osu!')}, {(p['name'], p['root_label']) for p in packs['packs']})
            songs = service.songs(None)['songs']
            self.assertEqual({'Silly-Go-Round', '甲', '乙'}, {s['display_title'] for s in songs})
            external = next(s for s in songs if s['display_title'] == '乙')
            self.assertEqual('乙', service.detail(None, external['song_id'])['latest_title'])
            root_ids = {s['root_id'] for s in songs if s['source_type'] == 'custom'}
            self.assertEqual(2, len(root_ids))
            for root_id in root_ids:  # asking for one location still works as before
                self.assertEqual(2, service.packs(root_id)['song_count'])


class DownloadFolderTests(unittest.TestCase):
    def test_only_the_game_song_folder_connect_holds_multiplayer_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = GameFixture(Path(temp))
            other = Path(temp) / 'StepMania' / 'Songs'
            song = {'key': '', 'title': '曲', 'artist': '', 'pack_id': 'x', 'charts': [(2, 5, 100)]}
            (fixture.data / 'CACHE' / 'external_song_cache.json').write_bytes(cache_bytes(
                [str(fixture.songs_root), str(other)],
                [(0, '\\connect\\Downloaded', 'connect', [song]), (1, '\\connect\\Just A Pack', 'connect', [song])]))
            # A library from before this rule: every 'connect' was typed as downloads.
            fixture.sync.run_once()
            with fixture.db() as db:
                db.execute("UPDATE packs SET pack_type='multiplayer_download'")
                db.commit()
            state = json.loads(fixture.sync.state_path.read_text(encoding='utf-8'))
            state['catalog']['version'] = 1
            fixture.sync.state_path.write_text(json.dumps(state), encoding='utf-8')
            fixture.sync.run_once()
            with fixture.db() as db:
                types = {r['path']: r['pack_type'] for r in db.execute(
                    'SELECT r.path,p.pack_type FROM packs p JOIN library_roots r USING(root_id)')}
        self.assertEqual({str(fixture.songs_root): 'multiplayer_download', str(other): 'normal'}, types)


class LineParsingTests(unittest.TestCase):
    def test_rejects_unknown_versions_and_missing_numbers(self):
        for line, reason in ((dict(official_line(), v=2), '版本'), (dict(official_line(), perfect=-1), 'perfect'),
                             (dict(official_line(), difficulty='extra'), '難度'), (dict(official_line(), at='yesterday'), '時間')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ResultLineError, reason):
                parse_line(json.dumps(line).encode('utf-8'))

    def test_folder_keys_match_the_song_scanner(self):
        self.assertEqual(song_folder_key('connect\\Some Song'), 'connect/some song')
        self.assertNotEqual(song_folder_key('Pack\\Bundle', 'a.gn'), song_folder_key('Pack\\Bundle', 'b.gn'))
        self.assertEqual((1, 'Pack\\Song'), _split_root('C:\\Songs\\Pack\\Song', [(1, 'C:\\Songs'), (2, 'C:\\Other')]))
        self.assertIsNone(_split_root('C:\\Songs', [(1, 'C:\\Songs')]))


class AppPathsTests(unittest.TestCase):
    def test_packaged_exe_keeps_data_next_to_itself(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:\\Tools\\DanceScore\\DanceScore.exe'):
            self.assertEqual(Path('C:\\Tools\\DanceScore'), app_paths.app_home())
            self.assertEqual(Path('C:\\Tools\\DanceScore\\data\\library.sqlite3'), app_paths.default_database())

    def test_source_checkout_keeps_data_in_the_repo(self):
        self.assertEqual(Path(__file__).resolve().parent.parent, app_paths.app_home())


if __name__ == '__main__':
    unittest.main()
