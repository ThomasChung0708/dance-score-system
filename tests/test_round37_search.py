"""Search controls and paginated API totals, using only a temporary game."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dance_results.gui import ASSETS, create_server
from dance_results.achievements import best_lamps_for_songs, progress_for_songs
from dance_results.library_service import LibraryService
from test_game_sync import GameFixture, external_line, official_line
import test_round28 as receipt_fixture


class Buttons(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.buttons = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'button' and attrs.get('id'):
            self.buttons[attrs['id']] = attrs


class SearchControlsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture = GameFixture(self.base)
        self.fixture.append(
            official_line(at='2026-11-17T12:00:00Z'),
            official_line(at='2026-11-17T12:01:00Z'),
            official_line(at='2026-11-17T12:02:00Z', player='別人'),
            official_line(difficulty='normal', chartJudged=436,
                          perfect=400, cool=36, maxCombo=436),
        )
        self.fixture.sync.run_once()
        self.server = create_server(
            self.fixture.database, monitor_directory=self.base / 'monitor', game_sync=False,
        )
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            self.server.shutdown()
            self.server.server_close()
            thread.join(3)

        self.addCleanup(cleanup)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def get(self, route, **args):
        request = Request(
            self.url + route + '?' + urlencode(args),
            headers={'X-Dance-Token': self.server.session_token},
        )
        with urlopen(request) as response:
            return json.load(response)

    def test_clear_controls_are_real_keyboard_accessible_buttons(self):
        for name, button_id in [('index.html', 'clear-search'), ('history.html', 'history-clear')]:
            with self.subTest(page=name):
                html = (ASSETS / name).read_text(encoding='utf8')
                attrs = Buttons(html).buttons[button_id]
                self.assertEqual(attrs['type'], 'button')
                self.assertNotIn('disabled', attrs)
                self.assertNotEqual(attrs.get('tabindex'), '-1')
                self.assertIn('清除條件', html)

    def test_library_total_uses_all_filtered_rows_before_pagination(self):
        packs = self.get('/api/packs')['packs']
        pack_id = packs[0]['pack_id']
        for filters, expected in [
            ({}, 4),
            ({'category': 'custom'}, 3),
            ({'pack_id': pack_id, 'q': '包裡'}, 2),
            ({'category': 'official', 'q': 'Silly'}, 1),
            ({'q': '找不到'}, 0),
        ]:
            for offset in (0, 1, 99):
                with self.subTest(filters=filters, offset=offset):
                    data = self.get('/api/songs', **filters, offset=offset, limit=1)
                    self.assertEqual(data['total'], expected)
                    self.assertEqual(len(data['songs']), int(offset < expected))

    def test_history_count_uses_same_combined_filters_as_paginated_rows(self):
        song_id = self.get('/api/songs', category='official')['songs'][0]['song_id']
        for filters, expected in [
            ({}, 4),
            ({'song_id': song_id, 'difficulty': 'HARD'}, 3),
            ({'song_id': song_id, 'difficulty': 'HARD', 'player_name': 'Eithwa'}, 2),
            ({'difficulty': 'NORMAL'}, 1),
            ({'player_name': '找不到'}, 0),
        ]:
            for offset in (0, 1, 99):
                with self.subTest(filters=filters, offset=offset):
                    data = self.get('/api/history', **filters, offset=offset, limit=1)
                    self.assertEqual(data['total'], expected)
                    self.assertEqual(len(data['records']), int(offset < expected))

    def add_lamp_records(self):
        self.fixture.append(
            official_line(at='2026-11-18T12:00:00Z', perfect=747, cool=0),
            official_line(at='2026-11-18T12:01:00Z', perfect=730, cool=12,
                          bad=4, miss=1, maxCombo=742),
            official_line(at='2026-11-18T12:02:00Z', perfect=10, cool=2,
                          bad=1, miss=1, maxCombo=12, failed=True),
            official_line(at='2026-11-18T12:03:00Z', difficulty='normal',
                          chartJudged=436, perfect=400, cool=30, bad=5,
                          miss=1, maxCombo=430),
            external_line(self.fixture, at='2026-11-18T12:04:00Z'),
        )
        self.fixture.sync.run_once()
        # An unclassified historical row has no lamp. Do not invent a DB lamp
        # named UNPLAYED/PENDING or relax the verified-record insertion gates.
        with self.fixture.db() as db:
            db.execute(
                'INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,'
                'player_status,local_difficulty) '
                "VALUES('lamp-pending','Eithwa','single','result','completed','HARD')"
            )
            db.execute(
                'INSERT INTO play_records(session_id,difficulty_id,played_at,completion_status) '
                "SELECT 'lamp-pending',difficulty_id,'2026-11-18T12:05:00Z','COMPLETED' "
                'FROM play_records ORDER BY record_id LIMIT 1'
            )
            db.commit()

    def test_history_lamp_filter_values_combined_filters_and_pagination(self):
        self.add_lamp_records()
        song_id = self.get('/api/songs', category='official')['songs'][0]['song_id']
        for lamp, expected, combined, statuses in [
            ('', 10, 6, {'ACP', 'AC', 'CLEAR', 'FAILED', None}),
            ('ACP', 1, 1, {'ACP'}),
            ('AC', 4, 2, {'AC'}),
            ('CLEAR', 2, 1, {'CLEAR'}),
            ('FAILED', 2, 1, {'FAILED'}),
        ]:
            for filters, total in [
                ({}, expected),
                ({'song_id': song_id, 'difficulty': 'HARD', 'player_name': 'Eithwa'}, combined),
            ]:
                ids = []
                for offset in range(total + 2):
                    with self.subTest(lamp=lamp, filters=filters, offset=offset):
                        data = self.get('/api/history', lamp=lamp, **filters, offset=offset, limit=1)
                        self.assertEqual(data['total'], total)
                        self.assertEqual(len(data['records']), int(offset < total))
                        for record in data['records']:
                            self.assertIn(record['lamp_status'], statuses)
                            ids.append(record['record_id'])
                self.assertEqual(len(set(ids)), total)

    def test_library_lamp_filter_uses_chart_bests_before_pagination(self):
        self.add_lamp_records()
        # A legacy import remains eligible until a verified record supersedes
        # that chart. This also supplies a positive best-CLEAR case.
        legacy_song = self.get('/api/songs', q='包裡第一首')['songs'][0]
        with self.fixture.db() as db:
            db.execute(
                'INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) '
                "VALUES('lamp-legacy','Eithwa','single','result','completed')"
            )
            db.execute(
                'INSERT INTO play_records(session_id,difficulty_id,played_at,completion_status,'
                'perfect,cool,bad,miss,ex_score,lamp_status,match_status,validation_status) '
                "SELECT 'lamp-legacy',difficulty_id,'2026-11-18T13:00:00Z','COMPLETED',"
                "480,10,5,5,970,'CLEAR','confirmed','verified' FROM game_difficulties "
                "WHERE song_id=? AND difficulty='HARD'", (legacy_song['song_id'],),
            )
            db.commit()
        for lamp, total in [('', 4), ('ACP', 1), ('AC', 1), ('CLEAR', 1),
                            ('FAILED', 1), ('UNPLAYED', 1)]:
            for offset in (0, 1, 4):
                with self.subTest(lamp=lamp, offset=offset):
                    data = self.get('/api/songs', lamp=lamp, offset=offset, limit=1)
                    self.assertEqual(data['total'], total)
                    self.assertEqual(len(data['songs']), int(offset < total))
                    if lamp == 'UNPLAYED':
                        for song in data['songs']:
                            self.assertEqual(song['play_count'], 0)
                            self.assertEqual(song['display_title'], '包裡第二首')
        for filters, total in [
            ({'lamp': 'ACP', 'q': 'Silly', 'category': 'official'}, 1),
            ({'lamp': 'ACP', 'q': 'Silly', 'category': 'custom'}, 0),
            ({'lamp': 'FAILED', 'q': '外部曲', 'category': 'custom'}, 1),
            ({'lamp': 'FAILED', 'q': 'Silly'}, 0),
            ({'lamp': 'UNPLAYED', 'q': 'Silly'}, 0),
            ({'lamp': 'UNPLAYED', 'q': '包裡第一首'}, 0),
            ({'lamp': 'UNPLAYED', 'q': '外部曲'}, 0),
            ({'lamp': 'UNPLAYED', 'q': '包裡第二首', 'category': 'custom'}, 1),
            ({'lamp': 'UNPLAYED', 'q': '包裡第二首', 'category': 'official'}, 0),
        ]:
            with self.subTest(filters=filters):
                self.assertEqual(self.get('/api/songs', **filters)['total'], total)

    def test_unknown_lamp_filters_are_rejected_with_400(self):
        for route in ('/api/history', '/api/songs'):
            invalid = ['UNCLEARED', 'CLEARED', 'assist', 'acp', 'PENDING', "ACP' OR 1=1", ' ']
            if route == '/api/history':
                invalid.append('UNPLAYED')
            for lamp in invalid:
                with self.subTest(route=route, lamp=lamp):
                    with self.assertRaises(HTTPError) as error:
                        self.get(route, lamp=lamp)
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual(json.load(error.exception)['error'], '通關狀態不正確。')

    def test_library_unplayed_excludes_any_saved_play(self):
        # Catalog charts alone and missing E/N slots are never saved plays.
        self.assertEqual(self.get('/api/songs', lamp='UNPLAYED')['total'], 3)
        song = self.get('/api/songs', q='包裡第二首')['songs'][0]
        self.assertEqual(
            [s['song_id'] for s in self.get('/api/songs', q='包裡第二首', lamp='UNPLAYED')['songs']],
            [song['song_id']],
        )
        for lamp in ('ACP', 'AC', 'CLEAR', 'FAILED'):
            with self.subTest(lamp=lamp, saved_play=False):
                self.assertEqual(self.get('/api/songs', q='包裡第二首', lamp=lamp)['total'], 0)
        with self.fixture.db() as db:
            db.execute(
                'INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) '
                "VALUES('unclassified','Eithwa','single','result','completed')"
            )
            db.execute(
                'INSERT INTO play_records(session_id,played_at,completion_status,evidence_json) '
                "VALUES('unclassified','2026-11-19T12:00:00Z','COMPLETED',?)",
                (json.dumps(dict(song=dict(song_id=song['song_id']),
                                 values=dict(difficulty='EASY'))),),
            )
            db.commit()
        self.assertEqual(self.get('/api/songs', lamp='UNPLAYED')['total'], 2)
        self.assertNotIn(
            song['song_id'],
            [s['song_id'] for s in self.get('/api/songs', lamp='UNPLAYED')['songs']],
        )
        for lamp in ('ACP', 'AC', 'CLEAR', 'FAILED'):
            with self.subTest(lamp=lamp):
                self.assertEqual(self.get('/api/songs', q='包裡第二首', lamp=lamp)['total'], 0)

        self.add_lamp_records()
        # A FAILED-only song is played even with unplayed difficulty slots.
        self.assertEqual(self.get('/api/songs', q='外部曲', lamp='FAILED')['total'], 1)
        self.assertEqual(self.get('/api/songs', q='外部曲', lamp='UNPLAYED')['total'], 0)
        # Specific lamps still match any chart's best after EASY is cleared.
        cleared = external_line(self.fixture)
        cleared.update(
            at='2026-11-19T13:00:00Z', difficulty='easy', level=3,
            chartJudged=200, perfect=190, cool=8, bad=1, miss=1,
            maxCombo=198, failed=False,
        )
        self.fixture.append(cleared)
        self.fixture.sync.run_once()
        for lamp, total in [('FAILED', 1), ('CLEAR', 1), ('UNPLAYED', 0)]:
            with self.subTest(lamp=lamp):
                self.assertEqual(self.get('/api/songs', q='外部曲', lamp=lamp)['total'], total)

    def test_browser_clear_resets_filters_page_url_and_pending_search(self):
        runtime = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')).parent.parent / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = os.environ.get('DANCE_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
        playwright = Path(os.environ.get('DANCE_PLAYWRIGHT') or runtime / 'node_modules/playwright')
        if not Path(node).is_file() or not playwright.exists():
            self.skipTest('Node/Playwright unavailable; set DANCE_NODE to the node executable '
                          'and DANCE_PLAYWRIGHT to the playwright node package directory')
        result = subprocess.run(
            [node, str(Path(__file__).with_name('round37_search_dom.cjs')), str(playwright), self.url],
            capture_output=True, text=True, encoding='utf8', timeout=75,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LampBestPolicyTests(unittest.TestCase):
    setUp = receipt_fixture.AutomaticReceiptTests.setUp
    tearDown = receipt_fixture.AutomaticReceiptTests.tearDown
    temporal_row = receipt_fixture.AutomaticReceiptTests.temporal_row
    official_auto = receipt_fixture.AutomaticReceiptTests.official_auto
    store = receipt_fixture.AutomaticReceiptTests.store
    submit = receipt_fixture.AutomaticReceiptTests.submit
    custom_without_chart = receipt_fixture.AutomaticReceiptTests.custom_without_chart

    def test_library_filter_includes_verified_lamp_without_catalog_chart(self):
        self.service.scoring_policy = 'custom-judged-v1'
        _, song_id = self.custom_without_chart()
        record = self.submit()['record']
        self.assertIsNone(record['difficulty_id'])
        library = LibraryService(self.db)
        with library.open() as opened:
            db = opened.connection
            lamps = best_lamps_for_songs(db, [song_id])[song_id]
            progress = progress_for_songs(db, [song_id])[song_id]
            self.assertEqual(lamps, {p['difficulty']: p['best_lamp'] for p in progress
                                     if p['record_count'] and p['best_lamp'] != 'UNPLAYED'})
            self.assertEqual(lamps['HARD'], record['lamp_status'])
        self.assertIn(song_id, [s['song_id'] for s in
                               library.songs(None, lamp=record['lamp_status'])['songs']])
        self.assertNotIn(song_id, [s['song_id'] for s in library.songs(None, lamp='UNPLAYED')['songs']])
        self.assertNotIn(song_id, [s['song_id'] for s in library.songs(None, lamp='FAILED')['songs']])


if __name__ == '__main__':
    unittest.main()
