"""Song-wide owner totals and ranking, exercised through the token API."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dance_results.gui import create_server
from dance_results import play_counts
from dance_results.library_service import LibraryService
from test_game_sync import GameFixture, external_line, official_line


class PlayCountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fixture = GameFixture(self.base)
        self.fixture.append(
            official_line(at='2026-11-17T12:00:00Z', online=False),
            official_line(at='2026-11-17T12:01:00Z', online=True),
            official_line(at='2026-11-17T12:02:00Z', difficulty='normal',
                          chartJudged=436, perfect=400, cool=36, maxCombo=436),
            external_line(self.fixture, at='2026-11-17T13:00:00Z'),
            external_line(self.fixture, at='2026-11-17T13:01:00Z'),
        )
        self.fixture.sync.run_once()
        self.server = create_server(
            self.fixture.database, monitor_directory=self.base / 'monitor', game_sync=False,
        )
        self.server.daemon_threads = False
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            self.server.shutdown()
            self.server.server_close()
            thread.join(3)

        self.addCleanup(cleanup)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.song_id = self.get('/api/songs', category='official')['songs'][0]['song_id']

    def get(self, route, **args):
        request = Request(self.url + route + '?' + urlencode(args),
                          headers={'X-Dance-Token': self.server.session_token})
        with urlopen(request) as response:
            return json.load(response)

    def legacy(self, session, *, song_id=None, chart_id=None, title='未入庫歌曲', artist='',
               snapshot=None, difficulty='EASY', at='2026-11-18T12:00:00Z'):
        evidence = dict(values=dict(difficulty=difficulty))
        if snapshot is not None or song_id is not None:
            with self.fixture.db() as db:
                evidence['song'] = dict(db.execute(
                    'SELECT * FROM songs WHERE song_id=?',
                    (snapshot if snapshot is not None else song_id,),
                ).fetchone())
        elif artist:
            evidence['song'] = dict(artist=artist)
        with self.fixture.db() as db:
            db.execute(
                'INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,'
                'player_status,local_difficulty,display_song_title) '
                "VALUES(?,'舊暱稱','multiplayer','result','completed',?,?)",
                (session, difficulty, title),
            )
            db.execute(
                'INSERT INTO play_records(session_id,song_id,difficulty_id,played_at,'
                'completion_status,evidence_json) VALUES(?,?,?,?,?,?)',
                (session, song_id, chart_id, at, 'COMPLETED', json.dumps(evidence)),
            )
            db.commit()

    def test_null_game_ids_total_all_difficulties_and_saved_ocr_records_once(self):
        self.assertTrue(all(r['song_id'] is None for r in self.fixture.records()))
        chart_id = self.fixture.records()[0]['difficulty_id']
        self.legacy('old-chart', chart_id=chart_id, difficulty='HARD')
        self.legacy('old-snapshot', snapshot=self.song_id)
        custom = self.get('/api/songs', q='包裡第一首')['songs'][0]['song_id']
        self.legacy('old-direct', song_id=custom)
        song = self.get('/api/songs', category='official')['songs'][0]
        self.assertEqual(song['play_count'], 5)
        self.assertEqual(song['play_counts'], {'EASY': 1, 'NORMAL': 1, 'HARD': 3})
        detail = self.get('/api/songs/' + str(self.song_id))
        self.assertEqual(detail['play_count'], 5)
        self.assertEqual(detail['play_counts'], song['play_counts'])
        history = self.get('/api/history', song_id=self.song_id, difficulty='NORMAL', lamp='AC')
        self.assertEqual(history['total'], 1)
        self.assertEqual(history['play_count'], 5)
        self.assertEqual(self.get('/api/history', song_id=self.song_id)['total'], 4)
        self.assertEqual(self.get('/api/plays/top')['songs'][0]['play_count'], 5)
        self.assertEqual(self.get('/api/songs/' + str(custom))['play_count'], 1)

    def test_only_saved_owner_records_not_other_character_or_session_rows(self):
        other = self.fixture.character.parent / 'other'
        other.mkdir()
        (other / 'profile.json').write_text(json.dumps({'name': 'Other'}), encoding='utf8')
        (other / 'results.jsonl').write_text(
            json.dumps(official_line(player='Other')) + '\n', encoding='utf8',
        )
        self.fixture.sync.run_once()
        # A multiplayer session / unsaved opponent is not a saved owner play.
        with self.fixture.db() as db:
            db.execute(
                'INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) '
                "VALUES('opponent','Other','multiplayer','result','completed')"
            )
            db.commit()
        self.assertEqual(self.get('/api/history')['total'], 3)
        top = self.get('/api/plays/top')['songs']
        self.assertEqual(sum(s['play_count'] for s in top), 5)
        self.assertEqual(top[0]['play_count'], 3)

    def test_no_update_play_increments_counts_without_adding_a_history_row(self):
        before = self.get('/api/history', song_id=self.song_id)
        self.assertEqual((before['total'], before['play_count']), (2, 3))
        self.fixture.append(official_line(at='2026-11-19T12:00:00Z'))
        self.fixture.sync.run_once()
        record_id = self.fixture.records()[-1]['record_id']
        detail = self.get('/api/history/detail', record_id=record_id)
        self.assertEqual(detail['update_summary']['kind'], 'NO_UPDATE')

        history = self.get('/api/history', song_id=self.song_id)
        self.assertEqual(history['total'], before['total'])
        self.assertEqual([r['record_id'] for r in history['records']],
                         [r['record_id'] for r in before['records']])
        self.assertEqual([r['update_summary'] for r in history['records']],
                         [r['update_summary'] for r in before['records']])
        self.assertEqual(history['play_count'], 4)
        self.assertEqual(history['play_counts'], {'NORMAL': 1, 'HARD': 3})
        song = self.get('/api/songs/' + str(self.song_id))
        self.assertEqual(song['play_count'], 4)
        self.assertEqual(song['play_counts'], history['play_counts'])
        listed = self.get('/api/songs', category='official')['songs'][0]
        self.assertEqual(listed['play_count'], 4)
        ranked = next(s for s in self.get('/api/plays/top')['songs']
                      if s['song_id'] == self.song_id)
        self.assertEqual(ranked['play_count'], 4)

    def test_count_breakdowns_are_bounded_to_page_and_detail(self):
        service = LibraryService(self.fixture.database)
        with patch('dance_results.library_service.counts_for_songs',
                   wraps=play_counts.counts_for_songs) as counts, \
                patch('dance_results.library_service.totals_for_songs',
                      wraps=play_counts.totals_for_songs) as totals:
            for order in ('asc', 'desc', 'plays_asc', 'plays_desc'):
                with self.subTest(order=order):
                    counts.reset_mock()
                    totals.reset_mock()
                    page = service.songs(None, order=order, offset=1, limit=1)
                    counts.assert_called_once()
                    self.assertEqual(counts.call_args.args[1],
                                     [song['song_id'] for song in page['songs']])
                    self.assertEqual(totals.call_count, int(order.startswith('plays_')))
            counts.reset_mock()
            service.songs(None, offset=999, limit=1)
            self.assertEqual(counts.call_args.args[1], [])
        with patch('dance_results.play_counts.counts_for_songs',
                   wraps=play_counts.counts_for_songs) as counts:
            detail = service.detail(None, self.song_id)
            counts.assert_called_once()
            self.assertEqual(counts.call_args.args[1], [self.song_id])
            self.assertEqual(detail['play_count'], 3)

    def test_competition_ties_recent_first_limit_and_independent_bests(self):
        self.fixture.append(external_line(self.fixture, at='2026-11-19T12:00:00Z'))
        self.fixture.sync.run_once()
        self.legacy('unknown')
        data = self.get('/api/plays/top')
        self.assertEqual([s['rank'] for s in data['songs']], [1, 1, 3])
        self.assertEqual([s['play_count'] for s in data['songs']], [3, 3, 1])
        self.assertEqual(data['songs'][0]['title'], '外部曲')
        known = next(s for s in data['songs'] if s['song_id'] == self.song_id)
        self.assertEqual(known['best_ex_score'], 1482)
        self.assertEqual(known['best_lamp'], 'AC')
        self.assertNotIn('play_counts', known)
        self.assertEqual(self.get('/api/plays/top', limit=1)['songs'], data['songs'][:1])
        self.assertEqual(self.get('/api/plays/top', limit=100)['songs'], data['songs'])

    def test_unbound_groups_use_exact_display_title_and_artist_without_fuzzy_binding(self):
        for session, title, artist in [
            ('a', 'Silly-Go-Round', 'X'), ('b', 'Silly-Go-Round', 'X'),
            ('c', 'Silly-Go-Round', 'Y'), ('d', 'silly go round', 'X'),
        ]:
            self.legacy(session, title=title, artist=artist)
        with self.fixture.db() as db:
            db.execute(
                "UPDATE play_records SET evidence_json=json_set("
                "json_remove(evidence_json,'$.song.artist'),'$.values.artist','Y') "
                "WHERE session_id='c'"
            )
            db.commit()
        top = self.get('/api/plays/top')['songs']
        unknown = [s for s in top if s['song_id'] is None]
        self.assertEqual(len(unknown), 3)
        row = next(s for s in unknown if s['title'] == 'Silly-Go-Round' and s['artist'] == 'X')
        self.assertEqual(row['play_count'], 2)
        history = self.get('/api/history', title=row['title'], artist=row['artist'], limit=1)
        self.assertEqual(history['total'], 2)
        self.assertEqual(history['play_count'], 2)
        self.assertEqual(history['records'][0]['title'], row['title'])
        self.assertEqual(self.get('/api/songs/' + str(self.song_id))['play_count'], 3)

    def test_unbound_title_history_keeps_play_count_when_no_update_is_hidden(self):
        self.legacy('title-first', title='未入庫歌曲', artist='原作者')
        self.legacy('title-tie', title='未入庫歌曲', artist='原作者')
        with self.fixture.db() as db:
            db.execute(
                "UPDATE play_records SET evidence_json=json_set("
                "evidence_json,'$.achievement_feedback',json('{}')) "
                "WHERE session_id='title-tie'"
            )
            db.commit()
        history = self.get('/api/history', title='未入庫歌曲', artist='原作者', limit=1)
        self.assertEqual(history['total'], 1)
        self.assertEqual(history['play_count'], 2)
        self.assertEqual(history['records'][0]['session_id'], 'title-first')
        self.assertEqual(history['records'][0]['update_summary']['kind'], 'UNAVAILABLE')
        last = self.get('/api/history', title='未入庫歌曲', artist='原作者', offset=1, limit=1)
        self.assertEqual(last['total'], 1)
        self.assertEqual(last['records'], [])
        self.assertEqual(last['play_count'], 2)

    def test_library_count_sort_before_pagination_with_stable_tiebreak(self):
        descending = self.get('/api/songs', order='plays_desc', limit=1)
        self.assertEqual(descending['songs'][0]['song_id'], self.song_id)
        self.assertEqual(self.get('/api/songs', order='plays_desc', offset=1, limit=1)
                         ['songs'][0]['play_count'], 2)
        ascending = self.get('/api/songs', order='plays_asc')['songs']
        self.assertEqual([s['play_count'] for s in ascending], [0, 0, 2, 3])
        self.assertEqual([s['song_id'] for s in ascending],
                         [s['song_id'] for s in self.get('/api/songs', order='plays_asc')['songs']])
        self.assertEqual(self.get('/api/songs/' + str(ascending[0]['song_id']))['play_count'], 0)

    def test_limit_validation_token_and_page_assets(self):
        for limit in ('0', '-1', '101', '1.5', 'bad', ''):
            with self.subTest(limit=limit), self.assertRaises(HTTPError) as error:
                self.get('/api/plays/top', limit=limit)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + '/api/plays/top')
        self.assertEqual(error.exception.code, 403)
        for route, mime in [('/plays', 'text/html'), ('/plays.js', 'text/javascript')]:
            with urlopen(self.url + route) as response:
                self.assertIn(mime, response.headers['Content-Type'])
                self.assertNotIn('SESSION_TOKEN', response.read().decode('utf8'))

    def test_empty_ranking_returns_no_songs(self):
        server = create_server(
            self.base / 'empty.sqlite3', monitor_directory=self.base / 'empty-monitor',
            game_sync=False,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f'http://127.0.0.1:{server.server_port}/api/plays/top',
                headers={'X-Dance-Token': server.session_token},
            )
            with urlopen(request) as response:
                self.assertEqual(json.load(response), dict(songs=[], total=0, limit=30))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_ranking_uses_saved_source_artist_for_older_scanned_songs(self):
        custom = next(s for s in self.get('/api/plays/top')['songs'] if s['title'] == '外部曲')
        with self.fixture.db() as db:
            db.execute('UPDATE songs SET artist=NULL WHERE song_id=?', (custom['song_id'],))
            db.execute(
                'INSERT INTO song_sources(song_id,relative_path,path_key,format,latest_status,'
                'payload_json,saved_artist) VALUES(?,?,?,?,?,?,?)',
                (custom['song_id'], 'Pack/Song/song.sm', 'pack/song/song.sm', 'sm', 'ok',
                 json.dumps(dict(artist='原始作者')), '已確認作者'),
            )
            db.commit()
        actual = next(s for s in self.get('/api/plays/top')['songs'] if s['song_id'] == custom['song_id'])
        self.assertEqual(actual['artist'], '已確認作者')
        self.assertEqual(actual['play_count'], 2)

    def test_browser_ranking_navigation_live_refresh_and_empty_state(self):
        runtime = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')).parent.parent / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = os.environ.get('DANCE_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
        playwright = Path(os.environ.get('DANCE_PLAYWRIGHT') or runtime / 'node_modules/playwright')
        if not Path(node).is_file() or not playwright.exists():
            self.skipTest('Node/Playwright unavailable')
        self.legacy('unknown-dom', title='未入庫歌曲', artist='原作者')
        result = subprocess.run(
            [node, str(Path(__file__).with_name('round37_plays_dom.cjs')), str(playwright),
             self.url, str(self.song_id), str(self.base)],
            capture_output=True, text=True, encoding='utf8', timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
