"""Round 2.8: review retention, constrained title recovery and saved receipts."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.monitor import Monitor
from dance_results.monitor_ocr import SelectionFrameBuffer
from dance_results.ocr_matching import stylized_title_key
from dance_results.result_ocr import complete_reliable_result, refine_mixed_title_prefix
from dance_results.verified_auto import automatic_request
from test_monitor import selected, scene
import test_round26 as retention_fixture
import test_round27 as authority_fixture
from test_round26 import catalog


class CompleteResultTests(unittest.TestCase):
    setUp = retention_fixture.RetentionTests.setUp
    send = retention_fixture.RetentionTests.send
    result = retention_fixture.RetentionTests.result

    def retained(self, title, confidence, reason=None):
        observation = self.result()
        observation['result_ocr']['fields']['title'].update(value=title, confidence=confidence, reason=reason)
        self.send(observation)
        self.send(scene('UNKNOWN'), 'black')
        self.send(scene('UNKNOWN'), 'black')
        self.send(selected())
        self.send(selected())
        session = self.monitor.tracker.last_session
        self.assertEqual(session['status'], 'pending_result')
        return self.monitor.reviews._read(session['session_id'])[1]

    def test_million_title_below_gate_is_retained_for_review(self):
        row = self.retained('Million of Bravery', .93692)
        self.assertEqual(row['status'], 'pending_review')
        self.assertEqual(row['ocr']['fields']['title']['value'], 'Million of Bravery')
        self.assertEqual(row['ocr']['temporal_validation']['reasons'], ['result_left_before_confirmation'])
        self.assertEqual(row['locked_session']['result']['recovery_reason'], 'result_left_before_confirmation')

    def test_absent_title_and_selection_do_not_drop_complete_result(self):
        row = self.retained(None, 0, 'unreadable_title')
        self.assertIsNone(row['draft']['song_id'])
        self.assertFalse(row['writes_play_record'])

    def test_unreliable_title_reason_is_review_evidence_not_auto_authority(self):
        row = self.retained('Unreadable title', .7, 'footer_title_page_roi_conflict_requires_review')
        self.assertEqual(row['ocr']['fields']['title']['reason'], 'footer_title_page_roi_conflict_requires_review')
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'pending_review')

    def test_two_low_title_frames_still_cannot_confirm_automatic_save(self):
        for color in ('gray', 'navy'):
            observation = self.result()
            observation['result_ocr']['fields']['title']['confidence'] = .937
            self.send(observation, color)
        row = self.monitor.reviews._read(self.monitor.tracker.last_session['session_id'])[1]
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'pending_review')
        self.assertIn('unreliable_title', row['ocr']['temporal_validation']['reasons'])

    def test_missing_digits_bad_grade_and_wrong_local_row_are_not_retained(self):
        for key in ('perfect', 'grade', 'game_level'):
            ocr = self.result()['result_ocr']
            ocr['fields'][key]['confidence'] = .94
            self.assertFalse(complete_reliable_result(ocr), key)
        ocr = self.result()['result_ocr']
        ocr['local_player_status'] = 'MISSING'
        self.assertFalse(complete_reliable_result(ocr))


class SelectionHandoffTests(unittest.TestCase):
    def tracker(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Tracker(tmp.name)

    def observations(self, tracker):
        first = selected()
        second = selected(notes=None, seconds=90)
        second['fields']['artist']['value'] = 'Changing preview'
        tracker.consume(first, now=1, sample_id=1)
        tracker.consume(second, now=4, sample_id=2)
        return first, second

    def play(self, tracker, when=25, **preview):
        for i in (0, 1):
            tracker.consume(scene('PLAYING', playing_preview=preview), now=when+i, sample_id=when+i)

    def test_optional_metadata_changes_do_not_reset_same_chart_confirmation(self):
        tracker = self.tracker()
        self.observations(tracker)
        self.assertIsNotNone(tracker.selection)
        self.assertEqual(tracker.selection_count, 2)

    def test_loading_handoff_consumes_just_observed_selection_once(self):
        tracker = self.tracker()
        self.observations(tracker)
        tracker.invalidate_selection('black_capture')
        self.play(tracker)
        self.assertIsNotNone(tracker.session['selection'])
        self.assertIsNone(tracker.session['pending_reason'])
        self.assertIsNone(tracker.selection)

    def test_loading_does_not_refresh_a_stale_selection(self):
        tracker = self.tracker()
        self.observations(tracker)
        tracker.invalidate_selection('black_capture')
        self.play(tracker, 100)
        self.assertIsNone(tracker.session['selection'])
        self.assertEqual(tracker.session['pending_reason'], 'selection_expired')

    def test_readable_different_title_invalidates_old_identity_immediately(self):
        tracker = self.tracker()
        self.observations(tracker)
        tracker.consume(selected(title='Other'), now=5, sample_id=3)
        self.play(tracker)
        self.assertIsNone(tracker.session['selection'])

    def test_difficulty_and_level_changes_each_require_new_confirmation(self):
        for key, value in (('difficulty', 'NORMAL'), ('game_level', 18)):
            tracker = self.tracker()
            self.observations(tracker)
            changed = selected()
            changed['fields'][key]['value'] = value
            tracker.consume(changed, now=5, sample_id=3)
            self.assertIsNone(tracker.selection)

    def test_playing_title_conflict_cannot_consume_recent_selection(self):
        tracker = self.tracker()
        self.observations(tracker)
        self.play(tracker, title='Different song')
        self.assertIsNone(tracker.session['selection'])


class FastSelectionTests(unittest.TestCase):
    tracker = SelectionHandoffTests.tracker

    def observation(self, tracker, title='JOINT'):
        tracker.current_pack = dict(pack_id=1, root_id=1, name='Pack 1', status='confirmed')
        observation = selected(title=title)
        c = catalog()
        c.index['joint'] = {10}
        observation['matching'] = c.match_identity(observation['fields'], 1, 'custom')
        return observation

    def playing(self, tracker, when, title='JOINT', level=17):
        tracker.consume(scene('PLAYING', playing_preview=dict(title=title, game_level=level)),
                        now=when, sample_id=when)

    def test_single_brief_pack_and_title_then_black_then_play_consumes_selection(self):
        tracker = self.tracker()
        tracker.consume(self.observation(tracker), now=10, sample_id=10)
        self.assertIsNone(tracker.selection)
        tracker.invalidate_selection('black_capture')
        self.playing(tracker, 10.2)
        self.assertIsNone(tracker.session)
        self.playing(tracker, 10.4)
        selection = tracker.session['selection']
        self.assertEqual(selection['fields']['title']['value'], 'JOINT')
        self.assertEqual(selection['pack']['pack_id'], 1)
        self.assertEqual(selection['handoff'], 'single_observation')
        self.assertEqual(tracker.session['song_id'], 10)
        self.assertIsNone(tracker.selection_candidate)

    def test_two_selection_frames_in_50ms_do_not_require_long_dwell(self):
        tracker = self.tracker()
        for now in (10, 10.05):
            tracker.consume(self.observation(tracker), now=now, sample_id=now)
        tracker.invalidate_selection('black_capture')
        self.playing(tracker, 10.2)
        self.playing(tracker, 10.4)
        self.assertEqual(tracker.session['song_id'], 10)
        self.assertNotIn('handoff', tracker.session['selection'])

    def test_single_selection_expiration_is_checked_on_first_playing_sample(self):
        tracker = self.tracker()
        tracker.consume(self.observation(tracker), now=10, sample_id=10)
        self.playing(tracker, 19.9)
        self.playing(tracker, 22.9)
        self.assertIsNotNone(tracker.session['selection'])

    def test_stale_single_selection_and_unconfirmed_pack_cannot_handoff(self):
        for stale in (True, False):
            tracker = self.tracker()
            observation = self.observation(tracker)
            if not stale:
                tracker.current_pack = None
            tracker.consume(observation, now=10, sample_id=10)
            self.playing(tracker, 21 if stale else 10.2)
            self.playing(tracker, 22 if stale else 10.4)
            self.assertIsNone(tracker.session['selection'])

    def test_first_playing_conflict_cannot_be_hidden_by_unreadable_second_preview(self):
        tracker = self.tracker()
        tracker.consume(self.observation(tracker), now=10, sample_id=10)
        self.playing(tracker, 10.2, title='Different song')
        self.playing(tracker, 10.4, title=None, level=None)
        self.assertIsNone(tracker.session['selection'])

    def test_newer_partial_other_song_does_not_resurrect_confirmed_old_song(self):
        tracker = self.tracker()
        for now in (9, 9.1):
            tracker.consume(self.observation(tracker), now=now, sample_id=now)
        other = self.observation(tracker, title='Other')
        other['fields']['game_level']['value'] = None
        other['matching'] = dict(status='pending_manual_review')
        tracker.consume(other, now=10, sample_id=10)
        self.playing(tracker, 10.2, title='Other', level=None)
        self.playing(tracker, 10.4, title='Other', level=None)
        self.assertEqual(tracker.session['selection']['fields']['title']['value'], 'Other')
        self.assertIsNone(tracker.session['song_id'])
        # Even an uncertain reading of a different title must stop old identity
        # reuse when the subsequent playing footer cannot corroborate anything.
        tracker = self.tracker()
        for now in (9, 9.1):
            tracker.consume(self.observation(tracker), now=now, sample_id=now)
        other['fields']['title']['reason'] = 'title_clipped'
        tracker.consume(other, now=10, sample_id=10)
        self.playing(tracker, 10.2, title=None, level=None)
        self.playing(tracker, 10.4, title=None, level=None)
        self.assertIsNone(tracker.session['selection'])

    def test_window_change_and_room_identity_change_clear_single_candidate(self):
        for reason in ('window_identity_changed', 'room_preview_changed', 'pack_changed_or_uncertain'):
            tracker = self.tracker()
            tracker.consume(self.observation(tracker), now=10, sample_id=10)
            tracker.invalidate_selection(reason)
            self.playing(tracker, 10.2)
            self.playing(tracker, 10.4)
            self.assertIsNone(tracker.session['selection'])

    def test_pixel_buffer_retains_subsecond_selection_overwritten_by_latest_frame(self):
        # The selected blue row appears for 100ms while normal OCR is busy.
        image = Image.new('RGB', (1024, 800), (200, 0, 0))
        ImageDraw.Draw(image).rectangle((770, 250, 790, 275), fill=(0, 0, 200))
        buffer = SelectionFrameBuffer()
        buffer.observe([(10, image), (10.1, Image.new('RGB', (1024, 800), 'black'))], 10.1)
        self.assertEqual(len(buffer.before(10.2)), 1)
        tracker = self.tracker()
        monitor = Monitor(Path(tracker.directory)/'unused.sqlite3', tracker.directory)
        monitor.tracker = tracker
        monitor.config = {}
        c = catalog()
        c.index['joint'] = {10}
        raw = dict(state='SONG_SELECT', frame_hash='captured-selection', selection=dict(
            fields=selected()['fields'], pack_list_visible=False,
            song_title_tokens=[dict(text='JOINT', confidence=.99), dict(text='Other', confidence=.99)]))
        analyzer = type('Analyzer', (), {'analyze': lambda self, *args, **kwargs: copy.deepcopy(raw)})()
        playing = scene('PLAYING', playing_preview=dict(title='JOINT', game_level=17))
        monitor._recover_fast_selection(analyzer, buffer, playing, 10.2, c)
        tracker.consume(playing, now=10.2, sample_id=10.2)
        tracker.consume(playing, now=10.4, sample_id=10.4)
        self.assertEqual(tracker.session['selection']['frame_hash'], 'captured-selection')
        self.assertEqual(tracker.session['song_id'], 10)

    def test_pixel_buffer_expires_and_is_bounded(self):
        buffer = SelectionFrameBuffer()
        image = Image.new('RGB', (1024, 800), (200, 0, 0))
        ImageDraw.Draw(image).rectangle((770, 250, 790, 275), fill=(0, 0, 200))
        buffer.observe([(i/10, image) for i in range(100)], 10)
        self.assertLessEqual(len(buffer.frames), 6)
        self.assertEqual(buffer.before(21), [])

    def test_buffer_cannot_resurrect_same_title_from_pack_before_a_new_pack_observation(self):
        tracker = self.tracker()
        tracker.current_pack = dict(pack_id=2, status='confirmed')
        tracker.pack_observed_at = 10.1
        monitor = Monitor(Path(tracker.directory)/'unused.sqlite3', tracker.directory)
        monitor.tracker = tracker
        monitor.config = {}
        c = catalog()
        c.index['joint'] = {10}
        image = Image.new('RGB', (1024, 800), 'gray')
        buffer = SelectionFrameBuffer()
        buffer.frames.append((10, image))
        raw = dict(state='SONG_SELECT', frame_hash='old-pack', selection=dict(
            fields=selected()['fields'], pack_list_visible=False,
            song_title_tokens=[dict(text='JOINT', confidence=.99), dict(text='Other', confidence=.99)]))
        analyzer = type('Analyzer', (), {'analyze': lambda self, *args, **kwargs: copy.deepcopy(raw)})()
        monitor._recover_fast_selection(analyzer, buffer,
            scene('PLAYING', playing_preview=dict(title='JOINT', game_level=17)), 10.2, c)
        self.assertIsNone(tracker.selection_candidate)
        self.assertEqual(tracker.current_pack['pack_id'], 2)


class StylizedIdentityTests(unittest.TestCase):
    def setup_catalog(self, title='1u11〜そして僕らは～'):
        c = catalog()
        c.index = {'lull～そして僕らは～': {10}}
        fields = selected()['fields']
        fields['title'].update(value=title, confidence=.86474)
        return c, fields

    def test_latin_confusables_and_wave_dashes_bind_with_confirmed_pack(self):
        for prefix in ('1u11', 'IuIl', '|u|l', 'lull'):
            c, fields = self.setup_catalog(prefix+' ~そして僕らは~')
            match = c.match_identity(fields, 1, 'custom')
            self.assertEqual(match['confirmed_song_id'], 10)
            self.assertTrue(match['review_only'])
            self.assertEqual(fields['title']['confidence'], .86474)

    def test_fuzzy_without_pack_never_binds(self):
        c, fields = self.setup_catalog()
        self.assertIsNone(c.match_identity(fields)['bound_song_id'])

    def test_fuzzy_cannot_cross_pack_constraint(self):
        c, fields = self.setup_catalog()
        self.assertIsNone(c.match_identity(fields, 2)['bound_song_id'])

    def test_different_japanese_suffix_cannot_match(self):
        c, fields = self.setup_catalog('1u11～そして彼らは～')
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_unknown_metadata_namesake_keeps_fuzzy_ambiguous(self):
        c, fields = self.setup_catalog()
        c.songs[11]['pack_id'] = 1
        c.index['lull～そして僕らは～'].add(11)
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_known_chart_conflict_cannot_bind_wrong_song(self):
        c, fields = self.setup_catalog()
        c.difficulties[(10, 'HARD')] = dict(difficulty_id=1, game_level=99, level_source='game_screen',
            total_notes=None, notes_source=None, notes_verified=0, duration_seconds=None)
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_unreliable_chart_and_arbitrary_latin_edits_do_not_match(self):
        c, fields = self.setup_catalog('full～そして僕らは～')
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])
        c, fields = self.setup_catalog()
        fields['game_level']['confidence'] = .94
        self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])
        self.assertIsNone(stylized_title_key('ALIVE'))


class MixedFooterTests(unittest.TestCase):
    def fixture(self):
        image = Image.new('RGB', (200, 40), 'gray')
        drawing = ImageDraw.Draw(image)
        for box in ((8, 8, 10, 20), (14, 12, 19, 20), (23, 8, 25, 20), (28, 8, 30, 20), (45, 8, 60, 20)):
            drawing.rectangle(box, fill='white')
        field = dict(value='ıl~そして僕らは~', confidence=.93,
                     evidence=[dict(confidence=.93, box=[[0, 2], [180, 2], [180, 30], [0, 30]])])
        return image, field

    def test_pixel_prefix_reread_keeps_lower_original_confidence(self):
        image, field = self.fixture()
        def reader(view, **kwargs):
            return [dict(text='lull', confidence=.98, box=[[0, 0], [30, 0], [30, 30], [0, 30]])]
        result, evidence = refine_mixed_title_prefix(image, reader, field)
        self.assertEqual(result['value'], 'lull ~そして僕らは~')
        self.assertEqual(result['confidence'], .93)
        self.assertEqual(field['value'], 'ıl~そして僕らは~')
        self.assertEqual(evidence[0]['preprocessing'], 'isolated_latin_word_before_japanese')

    def test_unreliable_prefix_reread_does_not_replace_title(self):
        image, field = self.fixture()
        result, _ = refine_mixed_title_prefix(image, lambda view, **kwargs: [dict(text='Other', confidence=.89)], field)
        self.assertEqual(result, field)

    def test_unrelated_latin_title_does_not_trigger_prefix_reread(self):
        image, field = self.fixture()
        field['value'] = 'Million of Bravery'
        def reader(*args, **kwargs):
            self.fail('Plain titles must not use mixed-title recovery')
        self.assertEqual(refine_mixed_title_prefix(image, reader, field), (field, []))


class AutomaticReceiptTests(unittest.TestCase):
    setUp = authority_fixture.ResultAuthorityTests.setUp
    tearDown = authority_fixture.ResultAuthorityTests.tearDown
    temporal_row = authority_fixture.ResultAuthorityTests.temporal_row
    official_auto = authority_fixture.ResultAuthorityTests.official_auto
    store = authority_fixture.ResultAuthorityTests.store

    def submit(self):
        store = self.store()
        store.auto_allowed = lambda sid: True
        return store.submit_verified(self.sid)

    def test_committed_auto_sidecar_records_saved_status_and_flags(self):
        self.temporal_row()
        record = self.submit()['record']
        row = self.service._read(self.sid)[1]
        self.assertEqual(row['status'], 'saved_verified_auto')
        self.assertTrue(row['writes_play_record'])
        self.assertTrue(row['generates_play_record'])
        self.assertEqual(row['record_id'], record['record_id'])
        self.assertEqual(row['confirmed_at'], record['confirmed_at'])
        self.assertEqual(self.service.list()['results'], [])

    def test_pending_page_hides_old_pending_sidecar_using_receipt_without_rewrite(self):
        row = self.temporal_row()
        self.submit()
        path = self.output/'sessions'/self.sid/'result-review.json'
        atomic_json(path, row)
        before = path.read_bytes()
        self.assertEqual(self.service.list()['results'], [])
        self.assertEqual(path.read_bytes(), before)

    def test_idempotent_retry_reconciles_sidecar_after_commit_gap(self):
        row = self.temporal_row()
        first = self.submit()['record']
        atomic_json(self.output/'sessions'/self.sid/'result-review.json', row)
        second = self.submit()
        self.assertTrue(second['idempotent'])
        self.assertEqual(second['record']['record_id'], first['record_id'])
        self.assertEqual(self.service._read(self.sid)[1]['status'], 'saved_verified_auto')

    def test_failed_transaction_does_not_mark_review_saved(self):
        self.temporal_row()
        service = self.store()
        service.auto_allowed = lambda sid: True
        with patch.object(service, '_checkpoint', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                service.submit_verified(self.sid)
        self.assertEqual(self.service._read(self.sid)[1]['status'], 'pending_review')
        self.assertEqual(len(self.service.list()['results']), 1)

    def test_unique_custom_song_without_any_chart_metadata_keeps_catalog_empty(self):
        row, sid = self.custom_without_chart()
        body, issues = automatic_request(self.service, row)
        self.assertEqual(issues, [])
        self.assertEqual(body['song_id'], sid)
        record = self.submit()['record']
        self.assertEqual((record['song_id'], record['difficulty'], record['game_level']), (sid, 'HARD', 17))
        self.assertIsNone(record['difficulty_id'])

    def custom_without_chart(self, notes=11):
        row = self.temporal_row()
        sid = self.session['song_id']
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE songs SET source_type='custom', saved_title=NULL, latest_title=NULL WHERE song_id=?", (sid,))
            db.execute('DELETE FROM game_difficulties WHERE song_id=?', (sid,))
            db.execute("INSERT INTO song_title_aliases(song_id,display_title,confirmed,source) VALUES(?,?,1,'game_screen')",
                       (sid, 'Auto Official'))
            db.commit()
        self.session['selection'].update(source_type='custom', pack=None)
        self.session['selection']['fields']['total_notes']['value'] = notes
        self.session['matching_status'] = 'unique_consistent_candidate'
        row['locked_session'] = copy.deepcopy(self.session)
        from test_phase5c import attach_auto_frames
        attach_auto_frames(self, row)
        return row, sid

    def test_custom_completed_selection_difference_is_preserved_and_saves(self):
        self.service.scoring_policy = 'custom-judged-v1'
        row, sid = self.custom_without_chart(notes=14)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        record = self.submit()['record']
        self.assertEqual(record['total_notes'], 11)
        self.assertEqual(record['evidence']['session']['selection']['fields']['total_notes']['value'], 14)
        self.assertEqual(record['evidence']['screen_consistency']['warnings'], ['custom_selection_judgment_count_difference'])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertIsNone(db.execute('SELECT total_notes,notes_verified,level_source FROM game_difficulties WHERE song_id=?', (sid,)).fetchone())

    def test_independent_max_keeps_selection_sum_difference_mandatory(self):
        row, _ = self.custom_without_chart(notes=14)
        self.assertIn('automatic_consistency_judgment_sum_selection', automatic_request(self.service, row)[1])

    def test_custom_result_total_conflict_still_blocks_automatic_save(self):
        self.service.scoring_policy = 'custom-judged-v1'
        row, _ = self.custom_without_chart(notes=14)
        row['ocr']['fields']['total_notes'] = dict(value=14, confidence=.99)
        self.assertIn('automatic_consistency_judgment_sum_result', automatic_request(self.service, row)[1])

    def test_custom_selection_low_confidence_still_blocks_automatic_save(self):
        self.service.scoring_policy = 'custom-judged-v1'
        row, _ = self.custom_without_chart(notes=14)
        row['locked_session']['selection']['fields']['total_notes']['confidence'] = .94
        self.assertIn('automatic_consistency_screen_total_selection', automatic_request(self.service, row)[1])

    def test_custom_screen_catalog_conflict_still_requires_review(self):
        self.service.scoring_policy = 'custom-judged-v1'
        row, sid = self.custom_without_chart(notes=14)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(?,'HARD',17,'game_screen',11,'game_screen',1)", (sid,))
            db.commit()
        self.assertIn('automatic_screen_catalog_notes_conflict', automatic_request(self.service, row)[1])

    def test_custom_unknown_chart_namesake_prevents_auto_save(self):
        self.service.scoring_policy = 'custom-judged-v1'
        row, _ = self.custom_without_chart()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO song_title_aliases(song_id,display_title,confirmed,source) VALUES(1,'Auto Official',1,'game_screen')")
            db.commit()
        self.assertIn('automatic_unique_verified_identity_required', automatic_request(self.service, row)[1])

    def test_single_observed_selection_stays_review_only_with_two_good_results(self):
        row = self.temporal_row()
        row['locked_session']['selection']['handoff'] = 'single_observation'
        self.assertIn('automatic_selection_single_observation_requires_review', automatic_request(self.service, row)[1])

    def test_low_confidence_selection_recovers_review_binding_from_clear_result(self):
        self.temporal_row()
        self.session['song_id'] = None
        self.session['matching_status'] = 'pending_manual_review'
        self.session['selection']['fields']['title']['confidence'] = .932
        folder = self.output/'sessions'/self.sid
        atomic_json(folder/'session.json', self.session)
        (folder/'result-review.json').unlink()
        row = self.service.ingest(self.session, self.ocr)
        self.assertIsNotNone(row['draft']['song_id'])
        self.assertEqual(row['status'], 'pending_review')
        self.assertFalse(row['writes_play_record'])
        self.assertIn('automatic_selection_title_required', automatic_request(self.service, row)[1])

