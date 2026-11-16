"""Session 8 gates, original compact sidecar readings, isolated storage only."""
import copy
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

from dance_results.library_service import LibraryService
from dance_results.monitor import Monitor
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.monitor_ocr import image_hash
from dance_results.ocr_matching import same_song_title_key, normalize
from dance_results.ocr_temporal import ResultConsistency, issues
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request
import test_result_review as review_fixture
import test_submissions as submission_fixture
from test_monitor import selected, scene

FIXTURE = json.loads((Path(__file__).parent/'fixtures/round32_session8.json').read_text(encoding='utf8'))


class HandoffTests(unittest.TestCase):
    def test_compact_key_preserves_letters_and_punctuation(self):
        for punctuation in '&-~:・!?':
            self.assertEqual(same_song_title_key('Ａ ' + punctuation + ' B'),
                same_song_title_key('a' + punctuation + 'b'))
        self.assertNotEqual(normalize('MAGIC & LOVE'), normalize('MAGIC &LOVE'))
        self.assertNotEqual(same_song_title_key('A&B'), same_song_title_key('A-B'))

    def handoff(self, preview, fast=False):
        with tempfile.TemporaryDirectory() as temp:
            tracker = Tracker(Path(temp))
            tracker.current_pack = dict(status='confirmed', pack_id=14)
            observation = selected('MAGIC & LOVE')
            observation['matching'].update(confirmed_song_id=457)
            observation['fields']['game_level']['value'] = 31
            tracker.update_selection(observation, 1)
            if not fast:
                tracker.update_selection(observation, 2)
            monitor = object.__new__(Monitor)
            monitor.tracker = tracker
            captured = Mock()
            # If a spacing difference wrongly forces re-reading, this newer
            # captured title must still not invalidate the selection.
            captured.before.return_value = []
            playing = scene('PLAYING', playing_preview=dict(title=preview, game_level=31))
            monitor._recover_fast_selection(Mock(), captured, playing, 3, Mock())
            tracker.consume(playing, now=3, sample_id=1)
            tracker.consume(playing, now=4, sample_id=2)
            return tracker.session

    def test_spacing_only_keeps_song457_through_recovery_and_playing(self):
        self.assertEqual(self.handoff('MAGIC &LOVE')['song_id'], 457)

    def test_single_captured_selection_also_survives_spacing(self):
        self.assertEqual(self.handoff('MAGIC &LOVE', fast=True)['song_id'], 457)

    def test_different_playing_title_still_invalidates(self):
        self.assertIsNone(self.handoff('Another Song')['song_id'])

    def test_newer_captured_different_title_invalidates_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor = object.__new__(Monitor)
            monitor.tracker = Tracker(Path(temp))
            monitor.tracker.selection = dict(last_seen=0, fields=dict(title=dict(value='Old')))
            frames = Mock()
            frames.before.return_value = [(1, Image.new('RGB', (10, 10)))]
            analyzer = Mock()
            analyzer.analyze.return_value = dict(state='SONG_SELECT', selection=dict(
                fields=dict(title=dict(value='Different', reason=None))))
            monitor._recover_fast_selection(analyzer, frames,
                scene('PLAYING', playing_preview=dict(title='MAGIC &LOVE')), 2, Mock())
            self.assertIsNone(monitor.tracker.selection)
            self.assertEqual(monitor.tracker.events[-1]['reason'], 'newer_captured_selection_differs_from_playing')


class SessionPairTests(unittest.TestCase):
    def pair(self, prefix='0216118f', change=None):
        samples = copy.deepcopy(FIXTURE[prefix]['samples'])
        if change:
            change(samples)
        consistency = ResultConsistency()
        for index, fields in enumerate(samples):
            image = Image.new('RGB', (1024, 800), ('gray', 'navy')[index])
            ocr = dict(fields=fields, row=dict(y=250), multiplayer=False, local_player_status='CONFIRMED')
            observation = dict(state='RESULT', result_ocr=ocr, frame_hash=image_hash(image))
            consistency.observe(observation, 'session8', index + 1, image)
        return consistency, observation

    def test_one_strong_orange_grade_resolves_and_revalidates_original_frames(self):
        consistency, observation = self.pair()
        ocr = observation['result_ocr']
        self.assertEqual(ocr['fields']['grade']['value'], 'A++')
        self.assertEqual(ocr['fields']['grade']['confidence'], .96197)
        self.assertEqual(ocr['temporal_validation']['samples'][0]['fields']['grade']['value'], 'A+')
        self.assertEqual(ocr['temporal_validation']['status'], 'confirmed')
        with tempfile.TemporaryDirectory() as temp:
            consistency.save(temp, ocr)
            self.assertEqual(issues(ocr, dict(session_id='session8', result=dict(
                frame_hash=observation['frame_hash'])), temp), [])

    def test_both_low_and_both_high_different_grades_stay_pending(self):
        for confidence in (.94, .97):
            def change(samples):
                for sample in samples:
                    sample['grade']['confidence'] = confidence
            _, observation = self.pair(change=change)
            self.assertEqual(observation['result_ocr']['temporal_validation']['status'], 'pending_review')

    def test_grade_resolution_does_not_rescue_other_disagreement(self):
        _, observation = self.pair(change=lambda samples: samples[0]['cool'].update(value=3))
        self.assertEqual(observation['result_ocr']['temporal_validation']['status'], 'pending_review')

    def test_strong_first_frame_grade_is_retained_with_original_confidence(self):
        def change(samples):
            samples.reverse()
        _, observation = self.pair(change=change)
        self.assertEqual(observation['result_ocr']['fields']['grade']['value'], 'A++')
        self.assertEqual(observation['result_ocr']['temporal_validation']['status'], 'confirmed')

    def test_title_097_09366_agreement_is_confirmed(self):
        _, observation = self.pair('a2f67ad5')
        self.assertEqual(observation['result_ocr']['temporal_validation']['status'], 'confirmed')
        self.assertEqual(observation['result_ocr']['fields']['title']['confidence'], .9366)

    def test_title_spacing_agrees_but_below_floor_or_different_stays_pending(self):
        for update, expected in ((dict(value='MAGIC &LOVE'), 'confirmed'),
                (dict(confidence=.899), 'pending_review'),
                (dict(value='Other'), 'pending_review')):
            _, observation = self.pair('a2f67ad5', lambda samples: samples[1]['title'].update(update))
            self.assertEqual(observation['result_ocr']['temporal_validation']['status'], expected)


class ChartlessReviewTests(unittest.TestCase):
    setUp = review_fixture.ReviewPersistenceTests.setUp
    tearDown = review_fixture.ReviewPersistenceTests.tearDown
    body = review_fixture.ReviewPersistenceTests.body

    def automatic_row(self, prefix='a2f67ad5', ambiguous=False, floor=None):
        self.service.scoring_policy = 'custom-judged-v1'
        samples = copy.deepcopy(FIXTURE[prefix]['samples'])
        if floor is not None:
            samples[1]['title']['confidence'] = floor
        title = samples[0]['title']['value']
        with closing(sqlite3.connect(self.db)) as db:
            for song_id in ((1, 2) if ambiguous else (1,)):
                db.execute("INSERT INTO song_title_aliases(song_id,display_title,source,confirmed) VALUES(?,?,'game_screen',1)",
                    (song_id, title))
            db.commit()
        fields = dict(title=dict(value=title, confidence=.99),
            game_level=dict(value=samples[0]['game_level']['value'], confidence=.99),
            difficulty=dict(value=None, confidence=0))
        self.session.update(song_id=1, matching_status='unique_consistent_candidate',
            selection=dict(fields=fields, ocr_fields=copy.deepcopy(fields), source_type='custom',
                pack=dict(pack_id=1, status='confirmed')))
        consistency = ResultConsistency()
        for index, sample in enumerate(samples):
            image = Image.new('RGB', (1024, 800), ('gray', 'navy')[index])
            ocr = dict(fields=sample, row=dict(y=250), multiplayer=False,
                local_player_status='CONFIRMED', requested_player_name='TC', errors=[],
                footer_level_scope='local_result_display', player_rows=[dict(row_id='row-0',
                    name='TC', fields=copy.deepcopy(sample))])
            observation = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=ocr)
            consistency.observe(observation, self.sid, index+1, image)
        folder = self.output/'sessions'/self.sid
        image.save(folder/'result.png')
        self.session['result']['frame_hash'] = observation['frame_hash']
        atomic_json(folder/'session.json', self.session)
        consistency.save(folder, ocr)
        (folder/'result-review.json').unlink()
        return self.service.ingest(self.session, ocr)

    def test_agreed_title_and_missing_chart_can_auto_save_and_retry(self):
        row = self.automatic_row()
        self.assertEqual(automatic_request(self.service, row)[1], [])
        store = SubmissionService(LibraryService(self.db), self.service)
        store.auto_allowed = lambda session_id: True
        record = store.submit_verified(self.sid)['record']
        self.assertEqual((record['perfect'], record['cool'], record['grade'], record['game_level']),
            (1375, 6, 'A++', 31))
        self.assertIsNone(record['difficulty'])
        self.assertEqual(store.submit_verified(self.sid)['record']['record_id'], record['record_id'])

    def test_grade_resolution_reaches_real_verified_auto_transaction(self):
        row = self.automatic_row('0216118f')
        self.assertEqual(automatic_request(self.service, row)[1], [])
        store = SubmissionService(LibraryService(self.db), self.service)
        store.auto_allowed = lambda session_id: True
        record = store.submit_verified(self.sid)['record']
        self.assertEqual((record['perfect'], record['cool'], record['grade']), (1186, 2, 'A++'))

    def test_later_explicit_chart_metadata_preserves_prior_song_result_progress(self):
        from dance_results.achievements import progress_for_songs
        row = self.automatic_row('0216118f')
        # Use a known difficulty to test later catalog maintenance. Result
        # identity and chart metadata remain separate throughout.
        row['locked_session']['selection']['fields']['difficulty'] = dict(value='HARD', confidence=.99)
        row['locked_session']['selection']['ocr_fields']['difficulty'] = dict(value='HARD', confidence=.99)
        self.session = row['locked_session']
        folder = self.output/'sessions'/self.sid
        atomic_json(folder/'session.json', self.session)
        atomic_json(folder/'result-review.json', row)
        store = SubmissionService(LibraryService(self.db), self.service)
        store.auto_allowed = lambda session_id: True
        record = store.submit_verified(self.sid)['record']
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory = sqlite3.Row
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source) VALUES(1,'HARD',23,'manual')")
            db.commit()
            progress = next(p for p in progress_for_songs(db, [1])[1] if p['difficulty'] == 'HARD')
            self.assertEqual(progress['record_count'], 1)
            self.assertEqual(progress['best_score_record']['record_id'], record['record_id'])

    def test_title_agreement_cannot_auto_save_ambiguous_namesake(self):
        row = self.automatic_row(ambiguous=True)
        self.assertIn('automatic_unique_result_title_required', automatic_request(self.service, row)[1])

    def test_below_title_floor_cannot_auto_save(self):
        row = self.automatic_row(floor=.899)
        self.assertIn('automatic_temporal_confirmation_required', automatic_request(self.service, row)[1])

    def test_uncharted_missing_notes_requires_complete_combo(self):
        row = self.automatic_row()
        row['ocr']['fields']['max_combo']['value'] -= 1
        self.assertIn('automatic_uncharted_complete_combo_required', automatic_request(self.service, row)[1])

    def test_draft_then_real_confirm_without_chart_rows_and_history_filter(self):
        self.service.scoring_policy = 'custom-judged-v1'
        body = self.body(confirm=False)
        body['values'].update(difficulty=None, total_notes=None)
        body.pop('max_basis', None)
        draft = self.service.save(body)
        self.assertEqual(draft['status'], 'draft')
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM play_records').fetchone()[0], 0)
        body.update(revision=draft['revision'], confirm=True)
        confirmed = self.service.save(body)
        body.update(revision=confirmed['revision'], play_mode='single')
        store = SubmissionService(LibraryService(self.db), self.service)
        preview = store.prepare(body)
        self.assertTrue(preview['can_submit'], preview['issues'])
        receipt = store.submit(dict(body, submission_token=preview['submission_token'], submit_acknowledge=True))
        record = receipt['record']
        self.assertEqual(record['song_id'], 1)
        self.assertIsNone(record['difficulty'])
        self.assertIsNone(record['difficulty_id'])
        self.assertEqual(record['game_level'], 17)
        self.assertEqual(record['total_notes'], 11)
        self.assertEqual(store.history(song_id=1)['total'], 1)
        self.assertEqual(store.submit(dict(body, submission_token=preview['submission_token'],
            submit_acknowledge=True))['record']['record_id'], record['record_id'])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM game_difficulties WHERE song_id=1').fetchone()[0], 0)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('DELETE FROM songs WHERE song_id=1')


class SongResultMigrationTests(unittest.TestCase):
    tearDown = submission_fixture.SubmissionMigrationTests.tearDown

    def setUp(self):
        submission_fixture.SubmissionMigrationTests.setUp(self)
        from dance_results.migrations_v6 import migrate_v5
        from dance_results.migrations_v7 import migrate_v6
        from dance_results.migrations_v8 import migrate_v7
        from dance_results.migrations_v9 import migrate_v8
        from dance_results.migrations_v10 import migrate_v9
        with closing(sqlite3.connect(self.path)) as db:
            for migrate in (migrate_v5, migrate_v6, migrate_v7, migrate_v8, migrate_v9):
                migrate(db, self.path, new_database=True)

    def test_migration_preserves_rows_views_external_record_fk_and_backup(self):
        from dance_results.migrations_v11 import migrate_v10
        from dance_results.sqlite_safety import audit, projection, check_integrity
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('CREATE TABLE record_reference(id INTEGER REFERENCES play_records(record_id))')
            db.execute('INSERT INTO record_reference SELECT record_id FROM play_records LIMIT 1')
            db.execute('CREATE VIEW record_reference_view AS SELECT * FROM play_records')
            db.commit()
            columns = projection(db)
            before = audit(db)
            migration = migrate_v10(db, self.path)
            self.assertEqual(before['play_records'], audit(db, columns)['play_records'])
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 11)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM record_reference_view').fetchone()[0],
                before['play_records']['count'])
            check_integrity(db)
        with closing(sqlite3.connect(migration['backup_path'])) as db:
            self.assertEqual(audit(db), before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)

    def test_failed_migration_rolls_back_schema_rows_and_restores_foreign_keys(self):
        from dance_results.migrations_v11 import migrate_v10
        from dance_results.sqlite_safety import audit
        from unittest.mock import patch
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            before = audit(db)
            schema = db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            with patch('dance_results.migrations_v11.check_integrity',
                    side_effect=[None, RuntimeError('rollback')]):
                with self.assertRaisesRegex(RuntimeError, 'rollback'):
                    migrate_v10(db, self.path)
            self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
            self.assertEqual(audit(db), before)
            self.assertEqual(schema, db.execute(
                'SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall())


if __name__ == '__main__':
    unittest.main()
