"""Round 2.9 identity safety, complete frame authority and read-only receipts."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image

from dance_results.monitor_tracking import atomic_json
from dance_results.ocr_matching import stylized_title_key
from dance_results.ocr_temporal import ResultConsistency, issues, reliable_result_field
from dance_results.result_ocr import refine_footer_title
from dance_results.result_review import ResultReviews
from dance_results.verified_auto import automatic_request
from test_monitor import selected, scene
from test_round26 import catalog
import test_phase5f1 as temporal_fixture
import test_round26 as retention_fixture
import test_round28 as receipt_fixture


class ConfusableKanaTests(unittest.TestCase):
    def fixture(self, title='ぼなべてぃーと♡s'):
        c = catalog()
        c.index = {'ぼなぺてぃーと♡s': {10, 20}}
        fields = selected()['fields']
        fields['title'].update(value=title, confidence=.89532)
        return c, fields

    def test_voiced_semivoiced_kana_and_script_variants_bind_review_only(self):
        for text in ('ぼなべてぃーと♡s', 'ぼなペてぃーと♡S', 'ボナペティート♡S'):
            with self.subTest(text=text):
                c, fields = self.fixture(text)
                match = c.match_identity(fields, 1, 'custom')
                self.assertEqual(match['confirmed_song_id'], 10)
                self.assertTrue(match['review_only'])
                self.assertEqual(fields['title']['confidence'], .89532)

    def test_heart_case_and_symbol_spacing_are_harmless(self):
        c, fields = self.fixture('ぼなぺてぃーと ♥ S')
        self.assertEqual(c.match_identity(fields, 1)['bound_song_id'], 10)

    def test_latin_confusables_in_mixed_title_are_retained(self):
        self.assertEqual(stylized_title_key('Il1ı ~そして僕らは~'),
                         stylized_title_key('llll～そして僕らは～'))

    def test_latin_only_confusables_need_pack_and_reliable_chart_notes(self):
        c, fields = self.fixture('1GNlTE')
        c.index = {'ignite': {10, 20}}
        self.assertEqual(c.match_identity(fields, 1)['bound_song_id'], 10)
        self.assertIsNone(c.match_identity(fields)['bound_song_id'])
        fields['total_notes']['confidence'] = .94
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_latin_only_arbitrary_spelling_change_cannot_bind(self):
        c, fields = self.fixture('1GNETE')
        c.index = {'ignite': {10}}
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_no_confirmed_pack_cannot_fuzzy_bind(self):
        c, fields = self.fixture()
        self.assertIsNone(c.match_identity(fields)['bound_song_id'])

    def test_wrong_pack_never_falls_back_to_another_pack(self):
        c, fields = self.fixture()
        self.assertIsNone(c.match_identity(fields, 99)['bound_song_id'])

    def test_confirmed_pack_excludes_cross_pack_copy(self):
        c, fields = self.fixture()
        self.assertEqual(c.match_identity(fields, 1)['bound_song_id'], 10)
        self.assertEqual(c.match_identity(fields, 2)['bound_song_id'], 20)

    def test_same_pack_namesake_with_unknown_chart_stays_ambiguous(self):
        c, fields = self.fixture()
        c.songs[20]['pack_id'] = 1
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_real_different_kana_cannot_match(self):
        for text in ('ぼなめてぃーと♡S', 'ほなぺてぃーと♡S', 'ぼなへてぃーと♡S'):
            c, fields = self.fixture(text)
            self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'], text)

    def test_exact_real_title_wins_over_a_confusable_title(self):
        c, fields = self.fixture()
        c.index['ぼなべてぃーと♡s'] = {11}
        fields['title']['confidence'] = .99
        self.assertEqual(c.match_identity(fields, 1)['bound_song_id'], 11)

    def test_known_notes_level_and_duration_conflicts_block(self):
        for key, value in (('game_level', 999), ('total_notes', 999), ('duration_seconds', 999)):
            c, fields = self.fixture()
            chart = dict(difficulty_id=10, game_level=17, total_notes=821, duration_seconds=96,
                         level_source='game_screen', notes_source='game_screen', notes_verified=1,
                         duration_verified=1)
            chart[key] = value
            c.difficulties[(10, 'HARD')] = chart
            self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'], key)

    def test_unreliable_chart_cannot_enable_fuzzy_retrieval(self):
        for key in ('difficulty', 'game_level'):
            c, fields = self.fixture()
            fields[key]['confidence'] = .94
            self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_contextual_homographic_kana_keeps_confidence_and_raw_reading(self):
        original = dict(value='ぼなペてぃーと♡S', confidence=.90449, reason=None)
        result, _ = refine_footer_title(None, object(), original)
        self.assertEqual(result['value'], 'ぼなぺてぃーと♡S')
        self.assertEqual(result['confidence'], .90449)
        self.assertEqual(result['original_reading'], original)
        self.assertEqual(original['value'], 'ぼなペてぃーと♡S')

    def test_contextual_script_normalization_does_not_change_voicing_or_katakana_words(self):
        for before, after in (('たベもの', 'たべもの'), ('ペン', 'ペン'), ('ヘビ', 'ヘビ'),
                              ('ぺベぺ', 'ぺべぺ'), ('ぼなべてぃーと', 'ぼなべてぃーと')):
            result, _ = refine_footer_title(None, object(), dict(value=before, confidence=.8))
            self.assertEqual(result['value'], after)


class MinorFrameTests(unittest.TestCase):
    observation = temporal_fixture.TemporalTests.observation

    def pair(self, changes=None, first_changes=None):
        consistency = ResultConsistency()
        a, image = self.observation()
        b, image2 = self.observation('navy')
        for ocr, updates in ((a['result_ocr'], first_changes), (b['result_ocr'], changes)):
            for key, field in (updates or {}).items():
                ocr['fields'][key].update(field)
        consistency.observe(a, 'session', 1, image)
        consistency.observe(b, 'session', 2, image2)
        return consistency, b

    def test_minor_second_frame_confirms_without_confidence_boost(self):
        c, b = self.pair({k: dict(confidence=.94458) for k in ('player_name', 'completion_status')})
        ocr = b['result_ocr']
        self.assertEqual(ocr['temporal_validation']['status'], 'confirmed')
        self.assertEqual(ocr['fields']['player_name']['confidence'], .94458)
        with tempfile.TemporaryDirectory() as tmp:
            c.save(tmp, ocr)
            self.assertEqual(issues(ocr, dict(session_id='session', result=dict(frame_hash=b['frame_hash'])), tmp), [])

    def test_low_confidence_reason_on_minor_field_can_agree_with_anchor(self):
        _, b = self.pair(dict(player_name=dict(confidence=.94, reason='low_confidence')))
        self.assertEqual(b['result_ocr']['temporal_validation']['status'], 'confirmed')

    def test_both_minor_readings_low_have_no_strong_anchor(self):
        change = dict(player_name=dict(confidence=.94))
        _, b = self.pair(change, change)
        self.assertIn('unreliable_player_name', b['result_ocr']['temporal_validation']['reasons'])

    def test_unreadable_minor_still_blocks(self):
        for field in (dict(confidence=.89), dict(value=None), dict(reason='conflicting_player_row')):
            _, b = self.pair(dict(player_name=field))
            self.assertNotEqual(b['result_ocr']['temporal_validation']['status'], 'confirmed')

    def test_different_player_or_completion_still_blocks(self):
        for key, value in (('player_name', 'Other'), ('completion_status', 'FAILED')):
            _, b = self.pair({key: dict(value=value, confidence=.94)})
            self.assertIn('conflicting_'+key, b['result_ocr']['temporal_validation']['reasons'])

    def test_low_confidence_score_and_identity_fields_still_block(self):
        for key in ('perfect', 'cool', 'bad', 'miss', 'max_combo', 'game_score', 'grade', 'game_level'):
            _, b = self.pair({key: dict(confidence=.944)})
            self.assertIn('unreliable_'+key, b['result_ocr']['temporal_validation']['reasons'])

    def test_missing_local_row_cannot_supply_second_sample(self):
        c = ResultConsistency()
        a, image = self.observation()
        c.observe(a, 'session', 1, image)
        b, image = self.observation('navy')
        b['result_ocr']['row'] = None
        c.observe(b, 'session', 2, image)
        self.assertEqual(len(c.samples), 1)
        self.assertTrue(b['result_ocr']['temporal_validation']['incomplete'])

    def test_missing_judgments_cannot_supply_second_sample(self):
        c = ResultConsistency()
        a, image = self.observation()
        c.observe(a, 'session', 1, image)
        b, image = self.observation('navy')
        b['result_ocr']['fields']['miss']['value'] = None
        c.observe(b, 'session', 2, image)
        self.assertEqual(len(c.samples), 1)

    def test_late_grade_disagreement_keeps_confirmed_pair_and_images(self):
        c, b = self.pair()
        accepted = copy.deepcopy(c.confirmed)
        late, image = self.observation('red')
        late['result_ocr']['fields']['grade']['reason'] = 'page_and_grade_roi_disagree_requires_review'
        c.observe(late, 'session', 14, image)
        self.assertEqual(c.confirmed, accepted)
        self.assertEqual(late['result_ocr']['temporal_validation'], accepted)
        self.assertEqual(len(c.images), 2)
        self.assertFalse(reliable_result_field(late['result_ocr'], 'grade'))
        with tempfile.TemporaryDirectory() as tmp:
            c.save(tmp, b['result_ocr'])
            self.assertEqual(issues(b['result_ocr'], dict(session_id='session', result=dict(frame_hash=b['frame_hash'])), tmp), [])
            self.assertIn('automatic_temporal_result_frame_conflict',
                          issues(late['result_ocr'], dict(session_id='session', result=dict(frame_hash=late['frame_hash'])), tmp))

    def test_grade_disagreement_before_confirmation_blocks(self):
        _, b = self.pair(dict(grade=dict(reason='page_and_grade_roi_disagree_requires_review')))
        self.assertIn('unreliable_grade', b['result_ocr']['temporal_validation']['reasons'])


class MonitorFrameTests(unittest.TestCase):
    setUp = retention_fixture.RetentionTests.setUp
    send = retention_fixture.RetentionTests.send
    result = retention_fixture.RetentionTests.result

    def test_half_drawn_missing_local_player_does_not_advance_debounce(self):
        half = self.result()
        half['result_ocr']['row'] = None
        half['result_ocr']['local_player_status'] = 'UNKNOWN'
        half['result_ocr']['tokens'] = []
        half['result_ocr']['fields']['player_name']['value'] = None
        self.send(half, 'black')
        self.send(self.result(), 'gray')
        self.assertIsNotNone(self.monitor.tracker.session)
        self.assertIsNone(self.monitor.tracker.last_session)
        second = self.result()
        for key in ('completion_status', 'player_name'):
            second['result_ocr']['fields'][key]['confidence'] = .94458
        self.send(second, 'navy')
        row = self.monitor.reviews._read(self.monitor.tracker.last_session['session_id'])[1]
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'confirmed')

    def test_late_disagree_cannot_rewrite_confirmed_review(self):
        self.send(self.result(), 'gray')
        self.send(self.result(), 'navy')
        folder = self.monitor.directory/'sessions'/self.monitor.tracker.last_session['session_id']
        before = (folder/'result-review.json').read_bytes()
        late = self.result()
        late['result_ocr']['fields']['grade']['reason'] = 'page_and_grade_roi_disagree_requires_review'
        self.send(late, 'red')
        self.assertEqual((folder/'result-review.json').read_bytes(), before)


class MissingSelectionNotesTests(unittest.TestCase):
    setUp = receipt_fixture.AutomaticReceiptTests.setUp
    tearDown = receipt_fixture.AutomaticReceiptTests.tearDown
    temporal_row = receipt_fixture.AutomaticReceiptTests.temporal_row
    official_auto = receipt_fixture.AutomaticReceiptTests.official_auto
    store = receipt_fixture.AutomaticReceiptTests.store
    custom_without_chart = receipt_fixture.AutomaticReceiptTests.custom_without_chart
    submit = receipt_fixture.AutomaticReceiptTests.submit

    def custom(self, single=False):
        self.service.scoring_policy = 'custom-judged-v1'
        row, sid = self.custom_without_chart(notes=None)
        if single:
            with closing(sqlite3.connect(self.db)) as db:
                db.execute('UPDATE songs SET pack_id=1 WHERE song_id=?', (sid,))
                db.commit()
            self.session['selection'].update(pack=dict(pack_id=1, status='confirmed'),
                                             handoff='single_observation', observed_frames=1)
            self.persist(row)
        return row, sid

    def persist(self, row):
        from test_phase5c import attach_auto_frames
        attach_auto_frames(self, row)

    def test_null_selection_notes_completed_sum_submits(self):
        row, sid = self.custom()
        body, errors = automatic_request(self.service, row)
        self.assertEqual(errors, [])
        self.assertEqual(body['values']['total_notes'], 11)
        record = self.submit()['record']
        self.assertEqual((record['song_id'], record['total_notes']), (sid, 11))
        self.assertEqual(record['notes_source'], 'completed_judgment_sum')

    def test_single_selection_confirmed_pack_unique_binding_two_results_submits(self):
        row, sid = self.custom(single=True)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        self.assertEqual(self.submit()['record']['song_id'], sid)

    def test_single_selection_without_confirmed_pack_still_blocks(self):
        row, _ = self.custom()
        row['locked_session']['selection']['handoff'] = 'single_observation'
        self.assertIn('automatic_selection_single_observation_requires_review', automatic_request(self.service, row)[1])

    def test_single_selection_locked_identity_must_agree(self):
        row, _ = self.custom(single=True)
        row['locked_session']['song_id'] = None
        self.assertIn('automatic_selection_single_observation_requires_review', automatic_request(self.service, row)[1])

    def test_null_notes_unbound_title_cannot_save(self):
        row, _ = self.custom()
        self.session['song_id'] = None
        self.session['selection']['fields']['title']['value'] = 'Missing title'
        row['ocr']['fields']['title']['value'] = 'Missing title'
        self.persist(row)
        self.assertIn('automatic_unique_verified_identity_required', automatic_request(self.service, row)[1])

    def test_null_notes_ambiguous_namesake_cannot_save(self):
        row, _ = self.custom(single=True)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO song_title_aliases(song_id,display_title,confirmed,source) VALUES(1,'Auto Official',1,'game_screen')")
            db.commit()
        self.assertIn('automatic_unique_verified_identity_required', automatic_request(self.service, row)[1])

    def test_null_notes_one_result_stays_pending(self):
        row, _ = self.custom(single=True)
        row['ocr']['temporal_validation']['samples'].pop()
        self.assertIn('automatic_two_result_frames_required', automatic_request(self.service, row)[1])

    def test_null_notes_low_score_confidence_still_blocks(self):
        row, _ = self.custom(single=True)
        row['ocr']['fields']['perfect']['confidence'] = .94
        self.persist(row)
        self.assertIn('automatic_unreliable_perfect', automatic_request(self.service, row)[1])

    def test_null_notes_result_total_conflict_blocks(self):
        row, _ = self.custom()
        row['ocr']['fields']['total_notes'] = dict(value=12, confidence=.99)
        self.assertIn('automatic_consistency_judgment_sum_result', automatic_request(self.service, row)[1])

    def test_null_notes_official_catalog_change_does_not_block_result(self):
        row = self.temporal_row()
        row['locked_session']['selection']['fields']['total_notes']['value'] = None
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE game_difficulties SET total_notes=13, notes_source='game_screen', notes_verified=1 WHERE song_id=?", (self.session['song_id'],))
            db.commit()
        self.assertEqual(automatic_request(self.service, row)[1], [])

    def test_minor_second_frame_can_pass_actual_submit(self):
        row, _ = self.custom(single=True)
        consistency = ResultConsistency()
        for index, color in enumerate(('gray', 'navy'), 1):
            image = Image.new('RGB', (1024, 800), color)
            from dance_results.monitor_ocr import image_hash
            observed = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=copy.deepcopy(row['ocr']))
            if index == 2:
                for key in ('completion_status', 'player_name'):
                    observed['result_ocr']['fields'][key]['confidence'] = .94458
                    observed['result_ocr']['player_rows'][0]['fields'][key]['confidence'] = .94458
            consistency.observe(observed, self.sid, index, image)
        row['ocr'] = observed['result_ocr']
        folder = self.output/'sessions'/self.sid
        consistency.save(folder, row['ocr'])
        atomic_json(folder/'result-review.json', row)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        self.assertEqual(self.submit()['record']['confirmation_method'], 'verified_auto')

    def test_upgrade_does_not_auto_submit_historical_pending_reviews(self):
        from dance_results.verified_auto import AutomaticStorage, STORAGE_POLICY
        row, _ = self.custom(single=True)
        atomic_json(self.output/'storage-mode.json', dict(
            mode='VERIFIED_AUTO', catalog_path=str(self.db.resolve()), excluded_sessions=[],
            enabled_at='2026-11-10T00:00:00+00:00', ocr_policy='round28-screen-v1'))
        mode = AutomaticStorage(self.store())
        try:
            self.assertEqual(mode.state['ocr_policy'], STORAGE_POLICY)
            self.assertIn(self.sid, mode.state['excluded_sessions'])
            self.assertFalse(mode.allowed(self.sid))
            self.assertEqual(self.service._read(self.sid)[1]['status'], row['status'])
        finally:
            mode.close()


class StaleSnapshotTests(unittest.TestCase):
    def test_four_committed_stale_sidecars_hidden_without_any_write(self):
        root = Path(__file__).resolve().parents[1]
        database = root/'data/library.sqlite3'
        snapshot = root/'data/stale_autos_snapshot'
        paths = sorted((snapshot/'sessions').glob('*/result-review.json'))
        if not database.exists() or len(paths) != 4:
            self.skipTest('Local session-4 snapshot not available')
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in [database, *paths]}
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            receipts = db.execute('SELECT record_id,session_id,confirmation_method FROM play_records WHERE record_id BETWEEN 33 AND 36').fetchall()
        self.assertEqual(len(receipts), 4)
        self.assertTrue(all(r[2] == 'verified_auto' for r in receipts))
        self.assertEqual({p.parent.name for p in paths}, {r[1] for r in receipts})
        self.assertTrue(all(json.loads(p.read_text(encoding='utf8'))['status'] == 'pending_review' for p in paths))
        self.assertEqual(ResultReviews(database, snapshot).list()['results'], [])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})


if __name__ == '__main__':
    unittest.main()
