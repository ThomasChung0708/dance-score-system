"""Committed game receipts reach the existing API and both list pages."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from dance_results.gui import create_server
from dance_results.record_events import RecordEventFeed
from test_game_sync import GameFixture, external_line, official_line


class GameEventTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture = GameFixture(self.base)
        self.fixture.sync.run_once()

    def start_server(self):
        server = create_server(
            self.fixture.database, monitor_directory=self.base / 'monitor', game_sync=False,
        )
        # Drain browser requests before removing the temporary SQLite database.
        server.daemon_threads = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(3)

        self.addCleanup(cleanup)
        return server

    def storage(self, server):
        request = Request(
            f'http://127.0.0.1:{server.server_port}/api/reviews/storage',
            headers={'X-Dance-Token': server.session_token},
        )
        with urlopen(request) as response:
            return json.load(response)

    def test_single_and_multiplayer_commit_one_receipt_each_and_duplicate_none(self):
        server = self.start_server()
        self.assertIs(server.game.event_feed, server.automatic.event_feed)
        self.assertEqual(self.storage(server)['events'], [])
        for index, online in enumerate([False, True], start=1):
            with self.subTest(online=online):
                line = official_line(online=online, at=f'2026-11-17T12:0{index}:00Z')
                self.fixture.append(line)
                server.game.run_once()
                status = self.storage(server)
                self.assertTrue({'mode', 'enabled_at', 'diagnostics', 'events'} <= status.keys())
                self.assertEqual(len(status['events']), index)
                receipt = status['events'][-1]
                self.assertEqual(receipt['record_id'], self.fixture.records()[-1]['record_id'])
                self.assertEqual(receipt['play_mode'], 'multiplayer' if online else 'single')
                self.assertEqual(receipt['catalog_path'], str(self.fixture.database.resolve()))
                self.assertEqual(receipt['title'], 'Silly-Go-Round')
                self.assertEqual(receipt['difficulty'], 'HARD')
                self.assertEqual(receipt['lamp_status'], 'AC')
                self.assertIsInstance(receipt['song_id'], int)
                self.assertEqual(receipt['ex_score'], 1482)
                self.assertEqual(receipt['score_status']['ex_score'], 1482)
                self.assertIn('kind', receipt['update_summary'])
                self.assertIn('events', receipt['achievement_feedback'])
                self.assertTrue(receipt['session_id'])
                self.assertTrue(receipt['review_id'])
                self.fixture.append(line)
                server.game.run_once()
                self.assertEqual(self.storage(server)['events'], status['events'])
                self.assertEqual(server.game.status()['imported_total'], index)

    def test_server_start_and_reimported_historical_lines_do_not_replay(self):
        self.fixture.append(official_line())
        self.fixture.sync.run_once()
        server = self.start_server()
        self.assertEqual(self.storage(server)['events'], [])
        self.fixture.append(official_line())
        server.game.run_once()
        self.assertEqual(self.storage(server)['events'], [])
        self.fixture.append(official_line(at='2026-11-17T12:50:00Z'))
        server.game.run_once()
        self.assertEqual(len(self.storage(server)['events']), 1)
        restarted = self.start_server()
        restarted.game.run_once()
        self.assertEqual(self.storage(restarted)['events'], [])
        self.assertEqual(len(self.fixture.records()), 2)

    def test_rolled_back_batch_never_publishes_a_receipt(self):
        server = self.start_server()
        self.fixture.append(official_line())
        with patch('dance_results.game_sync.check_integrity', side_effect=RuntimeError('rollback')):
            with self.assertRaisesRegex(RuntimeError, 'rollback'):
                server.game.run_once()
        self.assertEqual(self.fixture.records(), [])
        self.assertEqual(self.storage(server)['events'], [])
        server.game.run_once()
        self.assertEqual(len(self.storage(server)['events']), 1)

    def test_feed_is_bounded_and_thread_safe(self):
        feed = RecordEventFeed()
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(feed.publish, range(200)))
        records = feed.snapshot()
        self.assertEqual(len(records), 20)
        self.assertEqual(len(set(records)), 20)
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(feed.publish, [records[-1]] * 100))
        self.assertEqual(feed.snapshot(), records)
        records.clear()
        self.assertEqual(len(feed.snapshot()), 20)

    def test_browser_live_receipts_keep_filters_page_selection_and_edits(self):
        runtime = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')).parent.parent / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = os.environ.get('DANCE_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
        playwright = Path(os.environ.get('DANCE_PLAYWRIGHT') or runtime / 'node_modules/playwright')
        if not Path(node).is_file() or not playwright.exists():
            self.skipTest('Node/Playwright unavailable; set DANCE_NODE to the node executable '
                          'and DANCE_PLAYWRIGHT to the playwright node package directory')
        # Improvements keep enough visible rows to exercise history's next page;
        # tied plays remain available only in the separate growth history.
        self.fixture.append(*[
            {
                **external_line(self.fixture, at=f'2026-11-17T11:{minute:02}:00Z'),
                'perfect': 240 + minute,
            }
            for minute in range(60)
        ], external_line(self.fixture, at='2026-11-17T12:00:00Z'))
        self.fixture.sync.run_once()
        with self.fixture.db() as db:
            song_id = db.execute("SELECT song_id FROM songs WHERE latest_title='外部曲'").fetchone()[0]
        server = self.start_server()
        self.fixture.append(
            {
                **external_line(self.fixture, at='2026-11-17T12:01:00Z'),
                'perfect': 620,
                'cool': 20,
                'bad': 0,
                'miss': 0,
                'maxCombo': 640,
                'failed': False,
            },
            official_line(at='2026-11-17T12:02:00Z'),
        )
        result = subprocess.run(
            [node, str(Path(__file__).with_name('round37_events_dom.cjs')), str(playwright),
             f'http://127.0.0.1:{server.server_port}', str(song_id)],
            capture_output=True, text=True, encoding='utf8', timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.storage(server)['events']), 2)


if __name__ == '__main__':
    unittest.main()
