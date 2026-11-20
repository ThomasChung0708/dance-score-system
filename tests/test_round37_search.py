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
from urllib.request import Request, urlopen

from dance_results.gui import ASSETS, create_server
from test_game_sync import GameFixture, official_line


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


if __name__ == '__main__':
    unittest.main()
