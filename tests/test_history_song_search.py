"""History suggestions use disposable catalogs, never the user's database."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryError, LibraryService
from dance_results.scanner import scan_library
from dance_results.submissions import normalize_song_search, SubmissionService


class NormalizationTests(unittest.TestCase):
    def test_width_case_whitespace_and_punctuation(self):
        for value in ('ＭＡＧＩＣ ＆ＬＯＶＥ', 'magic & Love', 'Magic&\tLove',
                      'Magic\u3000&\nLove', 'MAGIC—LOVE'):
            with self.subTest(value=value):
                self.assertEqual(normalize_song_search(value), 'magiclove')
        self.assertEqual(normalize_song_search('Straße'), 'strasse')
        self.assertEqual(normalize_song_search('ｶﾀｶﾅ・日本語'), 'カタカナ日本語')
        self.assertEqual(normalize_song_search('中文 ☆ 曲'), '中文☆曲')
        self.assertEqual(normalize_song_search(None), '')


class HistorySongSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'library.sqlite3'
        root = self.base / 'SONG'
        songs = [
            ('Exact', 'MAGIC &LOVE', 'Composer'),
            ('Recorded', 'Magic & Love', 'Composer'),
            ('Prefix', 'Magic &Love remix', 'Other'),
            ('Substring', 'The magic &love', 'Other'),
            ('Artist', 'Another song', 'MAGIC &LOVE'),
            ('CJK', '桜の日本語歌曲', '初音ミク'),
            ('Kana', 'カタカナの曲', 'ＡＲＴＩＳＴ'),
            ('Official', 'Official melody', 'Unused'),
            ('Markup', '<img src=x onerror=alert(1)>', '<b>Artist</b>'),
        ] + [(f'Limit{i:02}', f'Limit song {i:02}', 'Batch') for i in range(25)]
        for folder, title, artist in songs:
            directory = root / 'Pack A' / folder
            directory.mkdir(parents=True)
            (directory / 'song.sm').write_text(
                f'#TITLE:{title};#ARTIST:{artist};', encoding='utf8'
            )
        with LibraryDatabase(self.db) as library:
            library.sync(scan_library(root))
            db = library.connection
            self.ids = {row['relative_folder'].replace('\\', '/').rsplit('/', 1)[-1]:
                        row['song_id'] for row in db.execute('SELECT * FROM songs')}
            db.execute("UPDATE songs SET source_type='official',artist='公式歌手',pack_id=NULL "
                       'WHERE song_id=?', (self.ids['Official'],))
            db.commit()
        self.library = LibraryService(self.db)
        self.store = SubmissionService(self.library, None)

    def search_ids(self, query, **kwargs):
        return [song['song_id'] for song in self.store.song_search(query, **kwargs)['songs']]

    def record(self, song_id, *, direct=False):
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            song = dict(db.execute('SELECT song_id,root_id,relative_folder FROM songs WHERE song_id=?',
                                   (song_id,)).fetchone())
            db.execute(
                'INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) '
                "VALUES(?, 'TC', 'single', 'result', 'completed')", (str(song_id),)
            )
            difficulty_id = None
            if not direct:
                difficulty_id = db.execute(
                    "INSERT INTO game_difficulties(song_id,difficulty) VALUES(?,'HARD')",
                    (song_id,),
                ).lastrowid
            db.execute(
                'INSERT INTO play_records(session_id,song_id,difficulty_id,played_at,completion_status,evidence_json) '
                "VALUES(?,?,?,'2026-01-01T00:00:00Z','COMPLETED',?)",
                (str(song_id), song_id if direct else None, difficulty_id, json.dumps(dict(song=song))),
            )
            db.commit()

    def test_case_width_spaces_and_punctuation_search(self):
        expected = self.search_ids('MAGIC &LOVE')
        self.assertEqual(len(expected), 5)
        for query in ('ｍａｇｉｃ＆ｌｏｖｅ', 'Magic& Love', 'MAGIC\t &\n LOVE', 'magic-love'):
            with self.subTest(query=query):
                self.assertEqual(self.search_ids(query), expected)

    def test_cjk_substring_kana_and_artist(self):
        self.assertEqual(self.search_ids('日本語'), [self.ids['CJK']])
        self.assertEqual(self.search_ids('初音 ミク'), [self.ids['CJK']])
        self.assertEqual(self.search_ids('ｶﾀｶﾅ'), [self.ids['Kana']])
        self.assertEqual(self.search_ids('artist'), [self.ids['Kana'], self.ids['Markup']])
        self.assertEqual(self.search_ids('公式歌手'), [self.ids['Official']])

    def test_title_exact_prefix_substring_then_artist_and_recorded_ties(self):
        self.record(self.ids['Recorded'])
        self.record(self.ids['Artist'], direct=True)
        self.assertEqual(self.search_ids('magic &love'), [
            self.ids['Recorded'], self.ids['Exact'], self.ids['Prefix'],
            self.ids['Substring'], self.ids['Artist'],
        ])

    def test_artist_exact_prefix_substring_ranking(self):
        with closing(sqlite3.connect(self.db)) as db:
            for folder, artist in [('Exact', 'Needle extended'), ('Prefix', 'Needle'),
                                   ('Substring', 'A needle artist')]:
                db.execute('UPDATE song_sources SET saved_artist=? WHERE song_id=?',
                           (artist, self.ids[folder]))
            db.commit()
        self.assertEqual(self.search_ids('needle'), [
            self.ids['Prefix'], self.ids['Exact'], self.ids['Substring'],
        ])

    def test_source_titles_subtitles_japanese_fields_and_aliases(self):
        song_id = self.ids['CJK']
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(
                'UPDATE song_sources SET saved_title=?,saved_subtitle=?,payload_json=? '
                'WHERE song_id=?',
                ('Stored—Title', 'ＳＵＢ・ＴＩＴＬＥ', json.dumps({
                    'title_fields': {'TITLE': ['日本語の別名'], 'TITLETRANSLIT': ['Romaji title']},
                }), song_id),
            )
            db.execute(
                'INSERT INTO song_title_aliases(song_id,display_title,source,confirmed) '
                "VALUES(?,?,'game_screen',1)", (song_id, '別名・Alias'),
            )
            # An artist exact match must follow even a title substring match.
            db.execute('UPDATE songs SET artist=? WHERE song_id=?',
                       ('oredtitle', self.ids['Artist']))
            db.commit()
        for query in ('stored title', 'sub title', '日本語の別名', 'romaji', '別名alias'):
            with self.subTest(query=query):
                self.assertEqual(self.search_ids(query), [song_id])
        self.assertEqual(self.search_ids('oredtitle'), [song_id, self.ids['Artist']])

    def test_artist_queries_tolerate_catalog_changes_between_reads(self):
        catalog = self.library._catalog(None)
        # The song query predates an inserted source, while the catalog still
        # contains songs no longer returned by either artist query.
        with patch.object(self.library, '_catalog', return_value=catalog), \
                patch.object(self.library, 'open') as opened:
            opened.return_value.__enter__.return_value.connection.execute.side_effect = [
                [],
                [{'song_id': self.ids['Prefix'], 'artist': 'New artist'}],
                [],
            ]
            songs = {song['song_id']: song for song in self.store._searchable_songs()}
        self.assertEqual(songs[self.ids['Prefix']]['artist'], 'New artist')
        self.assertEqual(songs[self.ids['Exact']]['artist'], '')

    def test_default_explicit_limit_and_empty_query(self):
        self.assertEqual(len(self.search_ids('Limit')), 20)
        self.assertEqual(len(self.search_ids('Limit', limit=3)), 3)
        self.assertEqual(len(self.search_ids('Limit', limit=100)), 25)
        with patch.object(self.library, 'open', side_effect=AssertionError('unneeded DB read')):
            for query in ('', ' \t\u3000', '---'):
                self.assertEqual(self.search_ids(query), [])
        self.assertEqual(self.search_ids('no such song'), [])

    def test_id_resolution_source_fields_and_validation(self):
        custom = self.store.song_search(song_id=self.ids['Exact'])['songs'][0]
        self.assertEqual(custom['title'], 'MAGIC &LOVE')
        self.assertEqual(custom['artist'], 'Composer')
        self.assertEqual((custom['source_type'], custom['pack_name']), ('custom', 'Pack A'))
        self.assertIn('Exact', custom['relative_folder'])
        self.assertFalse(any(key.startswith('_') for key in custom))
        official = self.store.song_search('ignored', song_id=self.ids['Official'])['songs'][0]
        self.assertEqual((official['source_type'], official['pack_name']), ('official', None))
        self.assertIn('公式歌手', official['artist'])
        for kwargs in (dict(limit=0), dict(limit=101), dict(limit=True), dict(song_id=-1),
                       dict(song_id='1'), dict(query='x' * 501), dict(query=None)):
            with self.subTest(kwargs=kwargs), self.assertRaises(LibraryError):
                self.store.song_search(**kwargs)
        with self.assertRaises(LibraryError) as missing:
            self.store.song_search(song_id=999999)
        self.assertEqual(missing.exception.status, 404)

    def test_cached_search_refreshes_after_catalog_and_record_changes(self):
        self.search_ids('magic')
        with patch.object(self.library, '_catalog', side_effect=AssertionError('cache miss')):
            self.search_ids('magic')
        self.record(self.ids['Recorded'], direct=True)
        self.assertEqual(self.search_ids('magic')[0], self.ids['Recorded'])
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE songs SET saved_title='Changed title' WHERE song_id=?",
                       (self.ids['Exact'],))
            db.execute("UPDATE song_sources SET saved_artist=NULL,"
                       "payload_json=json_set(payload_json,'$.artist','Fallback Artist') WHERE song_id=?",
                       (self.ids['Exact'],))
            db.commit()
        self.assertEqual(self.search_ids('changed title'), [self.ids['Exact']])
        self.assertEqual(self.search_ids('fallback artist'), [self.ids['Exact']])

    def start_server(self):
        server = create_server(self.db, monitor_directory=self.base / 'monitor', game_sync=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(3)

        self.addCleanup(cleanup)
        return server, f'http://127.0.0.1:{server.server_port}'

    def test_http_search_auth_unicode_limits_id_and_errors(self):
        server, base = self.start_server()

        def request(**args):
            return Request(base + '/api/history/song-search?' + urlencode(args),
                           headers={'X-Dance-Token': server.session_token})

        with self.assertRaises(HTTPError) as denied:
            urlopen(base + '/api/history/song-search?q=magic')
        self.assertEqual(denied.exception.code, 403)
        with urlopen(request(q='日本 語')) as response:
            self.assertEqual(json.load(response)['songs'][0]['song_id'], self.ids['CJK'])
        with urlopen(request(q='limit', limit=2)) as response:
            data = json.load(response)
            self.assertEqual((len(data['songs']), data['limit']), (2, 2))
        with urlopen(request(q='limit')) as response:
            self.assertEqual(len(json.load(response)['songs']), 20)
        with urlopen(request(song_id=self.ids['Exact'])) as response:
            self.assertEqual(json.load(response)['songs'][0]['title'], 'MAGIC &LOVE')
        with urlopen(request(q='')) as response:
            self.assertEqual(json.load(response)['songs'], [])
        for args, status in [(dict(limit=101), 400), (dict(limit='oops'), 400),
                             (dict(song_id=0), 400), (dict(song_id=999999), 404),
                             (dict(q='x' * 501), 400)]:
            with self.subTest(args=args), self.assertRaises(HTTPError) as error:
                urlopen(request(**args))
            self.assertEqual(error.exception.code, status)

    def test_browser_song_selection_keyboard_clear_deep_link_and_stale_results(self):
        runtime = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')).parent.parent / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = shutil.which('node') or str(runtime / 'bin/node.exe')
        playwright = runtime / 'node_modules/playwright'
        if not Path(node).is_file() or not playwright.exists():
            self.skipTest('Existing Node/Playwright runtime unavailable')
        self.record(self.ids['Exact'])
        _, base = self.start_server()
        result = subprocess.run([
            node, str(Path(__file__).with_name('history_song_search_dom.cjs')), str(playwright),
            base, str(self.ids['Exact']), str(self.ids['Markup']),
        ], capture_output=True, text=True, encoding='utf8', timeout=75)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['passed'])


if __name__ == '__main__':
    unittest.main()
