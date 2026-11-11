"""Display fallbacks never replace identity evidence or require a migration."""
import copy
from dataclasses import asdict
import sqlite3
from contextlib import closing
import unittest

import test_result_review as fixture
import test_submissions as submissions_fixture
import test_reviews_dom as dom_fixture
from dance_results.database import LibraryDatabase
from dance_results.download_tracking import download_receipt
from dance_results.library_service import LibraryService
from dance_results.models import ParsedFile
from dance_results.monitor import Monitor
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.recent_updates import recent_updates
from dance_results.report import render_html
from dance_results.scanner import scan_library
from dance_results.songs import display_title, group_songs
from dance_results.sqlite_safety import audit


def conflict_sources(sm='Watch Out Pt.2', dwi='32.watch out pt.2',
                     subtitle='beatmania IIDX 18 Resort Anthem'):
    return [
        ParsedFile('watch.dwi', 'dwi', title=dwi, song_folder='Pack/watch[Blue]'),
        ParsedFile('watch.sm', 'sm', title=sm, subtitle=subtitle,
                   song_folder='Pack/watch[Blue]'),
    ]


class DisplayTitleTests(unittest.TestCase):
    def test_sm_main_title_with_conflict_warning_and_unchanged_evidence(self):
        sources = conflict_sources()
        song = group_songs(sources)[0]
        before = copy.deepcopy(song)
        self.assertEqual(display_title(asdict(song), [s.to_dict() for s in sources]),
                         'Watch Out Pt.2')
        self.assertEqual(song, before)
        self.assertIsNone(song.title)
        self.assertFalse(song.title_reliable)
        conflict = next(d for d in song.diagnostics if d.code == 'SONG_TITLE_CONFLICT')
        self.assertEqual(conflict.severity, 'warning')

    def test_other_catalog_conflict_styles(self):
        for sm, dwi, subtitle in [
            ('Like+it!', 'Like it! REFLEC BEAT colette', 'REFLEC BEAT colette'),
            ('Flower Dance 花のダンス', 'flower dance', None),
        ]:
            with self.subTest(sm=sm):
                sources = conflict_sources(sm, dwi, subtitle)
                song = group_songs(sources)[0]
                self.assertEqual(display_title(asdict(song), [s.to_dict() for s in sources]), sm)
                self.assertIn('SONG_TITLE_CONFLICT', [d.code for d in song.diagnostics])

    def test_fallback_order_ocr_then_dwi_then_candidates_then_folder(self):
        song = dict(song_id=1, folder_name='  32.watch out pt.2  ')
        dwi = [dict(format='dwi', saved_title='32.watch out pt.2')]
        self.assertEqual(display_title(song, dwi, observed_titles=['', 'Watch Out Pt.2']),
                         'Watch Out Pt.2')
        self.assertEqual(display_title(song, dwi), 'watch out pt.2')
        self.assertEqual(display_title(dict(song, title_candidates=['32.Watch Out Pt.2'])),
                         'Watch Out Pt.2')
        self.assertEqual(display_title(song), 'watch out pt.2')
        self.assertEqual(display_title(dict(relative_folder=r'Pack\Watch Out Pt.2')),
                         'Watch Out Pt.2')
        self.assertEqual(display_title(dict(song_id=1)), 'Song #1')

    def test_existing_catalog_titles_and_numeric_song_names_are_preserved(self):
        for key in ('saved_title', 'latest_title', 'title'):
            self.assertEqual(display_title({key: 'Original title with subtitle'},
                                           [s.to_dict() for s in conflict_sources()]),
                             'Original title with subtitle')
        self.assertEqual(display_title(dict(folder_name='3.14')), '3.14')

    def test_unbound_item_has_no_derived_identity(self):
        self.assertIsNone(display_title(None, observed_titles=['Watch Out Pt.2']))


class BoundTitleTests(unittest.TestCase):
    tearDown = fixture.ReviewPersistenceTests.tearDown
    body = fixture.ReviewPersistenceTests.body
    store = submissions_fixture.SubmissionTests.store
    approved = submissions_fixture.SubmissionTests.approved
    commit = submissions_fixture.SubmissionTests.commit
    clone_session = submissions_fixture.SubmissionTests.clone_session
    run_browser = dom_fixture.ReviewDOMTests.run_browser

    def setUp(self):
        fixture.ReviewPersistenceTests.setUp(self)
        folder = self.base/'SONG'/'Pack A'/'Folder A'
        (folder/'song.sm').write_text(
            '#TITLE:Watch Out Pt.2;#SUBTITLE:beatmania IIDX 18 Resort Anthem;', encoding='utf8')
        (folder/'song.dwi').write_text('#TITLE:32.watch out pt.2;', encoding='utf8')
        self.scan = scan_library(self.base/'SONG')
        with LibraryDatabase(self.db) as lib:
            lib.sync(self.scan)
            # Reproduce an existing conflict with no saved consensus name.
            lib.connection.execute('UPDATE songs SET saved_title=NULL,latest_title=NULL WHERE song_id=1')
            lib.connection.commit()
        self.session['selection']['fields']['title']['value'] = 'Watch Out Pt.2'
        self.ocr['fields']['title']['value'] = 'Watch Out Pt.2'
        self.reingest()

    def reingest(self):
        folder = self.output/'sessions'/self.sid
        atomic_json(folder/'session.json', self.session)
        (folder/'result-review.json').unlink(missing_ok=True)
        self.row = self.service.ingest(self.session, self.ocr)

    def test_catalog_review_and_scan_report_display_keep_conflict_diagnostics(self):
        library = LibraryService(self.db)
        self.assertEqual(next(s for s in library.songs(1)['songs'] if s['song_id'] == 1)
                         ['display_title'], 'Watch Out Pt.2')
        detail = library.detail(1, 1)
        self.assertEqual(detail['display_title'], 'Watch Out Pt.2')
        self.assertIn('SONG_TITLE_CONFLICT', [d['code'] for d in detail['diagnostics']])
        self.assertEqual(self.service.candidates('Watch Out Pt.2')['candidates'][0]['title'],
                         'Watch Out Pt.2')
        html = render_html(scan_library(self.base/'SONG'))
        self.assertIn('<strong>Watch Out Pt.2</strong>', html)
        self.assertIn('SONG_TITLE_CONFLICT', html)

    def test_new_session_history_and_recent_titles(self):
        record = self.commit(self.approved())['record']
        self.assertEqual(record['title'], 'Watch Out Pt.2')
        with self.store().library.open() as lib:
            db = lib.connection
            self.assertEqual(db.execute('SELECT display_song_title FROM play_sessions').fetchone()[0],
                             'Watch Out Pt.2')
            self.assertEqual(recent_updates(db)['updates'][0]['title'], 'Watch Out Pt.2')
        self.assertEqual(self.store().history()['records'][0]['title'], 'Watch Out Pt.2')
        self.assertEqual(download_receipt(self.db, self.sid)['title'], 'Watch Out Pt.2')

    def test_existing_null_session_and_catalog_render_without_writes_or_ocr(self):
        self.ocr['fields']['title']['value'] = None
        self.session['selection']['fields']['title']['value'] = None
        self.reingest()
        record = self.commit(self.approved())['record']
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('UPDATE play_sessions SET display_song_title=NULL')
            db.commit()
            before = audit(db)
        self.assertEqual(self.store().detail(record['record_id'])['title'], 'Watch Out Pt.2')
        self.assertEqual(self.store().history()['records'][0]['title'], 'Watch Out Pt.2')
        with self.store().library.open() as lib:
            self.assertEqual(recent_updates(lib.connection)['updates'][0]['title'], 'Watch Out Pt.2')
            self.assertEqual(audit(lib.connection), before)

    def hide_source_titles(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE song_sources SET saved_title=NULL,payload_json='{}' WHERE song_id=1")
            db.commit()

    def assert_ocr_fallback(self):
        self.hide_source_titles()
        record = self.commit(self.approved())['record']
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('UPDATE play_sessions SET display_song_title=NULL')
            db.commit()
        self.assertEqual(self.store().detail(record['record_id'])['title'], 'Watch Out Pt.2')

    def test_result_ocr_fallback_for_saved_record(self):
        self.session['selection']['fields']['title']['value'] = None
        self.reingest()
        self.assert_ocr_fallback()

    def test_consumed_selection_fallback_for_saved_record(self):
        self.ocr['fields']['title']['value'] = None
        self.reingest()
        self.assert_ocr_fallback()

    def test_monitor_public_fallback_does_not_modify_tracker_or_matching(self):
        monitor = Monitor(self.db, self.output)
        monitor.tracker = Tracker(self.output)
        monitor.tracker.session = copy.deepcopy(self.session)
        monitor.tracker.session['selection']['fields']['title']['value'] = None
        before = monitor.tracker.snapshot()
        status = monitor.status()
        self.assertEqual(status['tracking']['session']['display_song_title'], 'Watch Out Pt.2')
        self.assertEqual(monitor.tracker.snapshot(), before)
        catalog = ReadOnlyCatalog(self.db)
        try:
            self.assertIsNone(catalog.songs[1]['latest_title'])
            self.assertNotIn('Watch Out Pt.2 beatmania IIDX 18 Resort Anthem', catalog.index)
        finally:
            catalog.close()

    def test_bound_pending_list_can_fall_back_but_unbound_review_stays_unbound(self):
        self.session['selection']['fields']['title']['value'] = None
        self.ocr['fields']['title']['value'] = None
        self.reingest()
        self.assertEqual(self.service.list()['results'][0]['title'], 'Watch Out Pt.2')
        self.session['song_id'] = None
        self.reingest()
        self.assertIsNone(self.service.list()['results'][0]['title'])
        self.assertIsNone(self.service.detail(self.sid)['preview']['selected_song'])

    def configure_server(self, server):
        server.monitor.tracker = Tracker(self.output)
        server.monitor.tracker.state = 'PLAYING'
        session = copy.deepcopy(self.session)
        session['song_id'] = 1
        session['selection']['fields']['title']['value'] = None
        server.monitor.tracker.session = session

    def test_rendered_bound_pending_uses_derived_catalog_title(self):
        self.run_browser('title-bound')

    def test_rendered_history_home_recent_monitor_and_unbound_pending(self):
        self.commit(self.approved())
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('UPDATE play_sessions SET display_song_title=NULL')
            db.commit()
        self.clone_session()
        self.session['song_id'] = None
        self.session['selection']['fields']['title']['value'] = None
        self.ocr['fields']['title']['value'] = None
        self.reingest()
        self.run_browser('title-fallback')


if __name__ == '__main__':
    unittest.main()
