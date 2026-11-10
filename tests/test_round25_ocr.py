"""Round 2.5 diagnostics, animation handling, grade evidence and exam timing."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from dance_results.ocr_failures import FailureCapture, failure_details
from dance_results.ocr_policy import AUTO_CONFIDENCE, RESULT_REQUIRED, SELECTION_REQUIRED
from dance_results.ocr_temporal import ResultConsistency, issues
from dance_results.result_ocr import incomplete_result, refine_grade, cell
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.monitor import Monitor
from dance_results.verified_auto import automatic_request
from tools import ocr_regression as exam
from tools.replay_ocr_failures import replay
import test_phase5f1 as temporal_fixture
from test_result_review import token


class DiagnosticRulesTests(unittest.TestCase):
    def observation(self, screen='song_select', **fields):
        wrapped = {key: dict(value=value, confidence=confidence, reason=reason)
                   for key, (value, confidence, reason) in fields.items()}
        return dict(screen_type=screen, **(
            {'result_ocr': dict(fields=wrapped)} if screen == 'results' else {'ocr_fields': wrapped}))

    def test_ignored_metadata_and_occlusion_even_for_custom_required_field(self):
        observation = self.observation(
            artist=(None, 0, 'not_readable'), bpm=(123, .5, 'low_confidence'),
            total_notes=(None, 0, 'pack_list_occludes_metadata'))
        observation['source_type'] = 'custom'
        self.assertEqual(failure_details(observation)['reason'], [])

    def test_required_fields_use_the_actual_automatic_threshold(self):
        for screen, required in (('song_select', SELECTION_REQUIRED), ('results', RESULT_REQUIRED)):
            for key in required:
                with self.subTest(screen=screen, key=key):
                    self.assertIn(key + ': missing', failure_details(
                        self.observation(screen, **{key: (None, 0, None)}))['reason'])
                    self.assertIn(key + ': low_confidence', failure_details(
                        self.observation(screen, **{key: (0, AUTO_CONFIDENCE - .001, None)}))['reason'])
                    self.assertEqual(failure_details(self.observation(
                        screen, **{key: (0, AUTO_CONFIDENCE, None)}))['reason'], [])

    def test_custom_only_metadata_depends_on_known_source(self):
        observation = self.observation(total_notes=(None, 0, None), duration_seconds=(150, .94, None))
        self.assertEqual(failure_details(observation)['reason'], [])
        observation['source_type'] = 'custom'
        self.assertEqual(failure_details(observation)['reason'], [
            'duration_seconds: low_confidence', 'total_notes: missing'])
        observation['source_type'] = 'official'
        self.assertEqual(failure_details(observation)['reason'], [])

    def test_host_footer_is_nonfatal_and_local_level_still_gates_single_player(self):
        observation = self.observation('results', game_level=(None, 0, 'not_readable'))
        observation['result_ocr'].update(multiplayer=True, errors=['footer_level_is_host_not_local'])
        self.assertEqual(failure_details(observation)['reason'], [])
        observation['result_ocr']['multiplayer'] = False
        self.assertIn('game_level: missing', failure_details(observation)['reason'])

    def test_gate_errors_pack_disagreement_and_exceptions_survive(self):
        observation = dict(screen_type='song_select', selection=dict(
            fields={}, pack_list_visible=True, pack_reason='selected_pack_disagreement'))
        self.assertIn('selected_pack_disagreement', failure_details(observation)['reason'])
        observation = self.observation('results')
        observation['result_ocr'].update(errors=['local_player_missing'], temporal_validation=dict(
            status='pending_review', reasons=['conflicting_perfect']))
        reasons = failure_details(observation, ValueError())['reason']
        for reason in ('local_player_missing', 'conflicting_perfect', 'recognition_exception: ValueError'):
            self.assertIn(reason, reasons)

    def test_unknown_cooldown_survives_alternating_reasons_and_preserves_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            capture = FailureCapture(directory, clock=lambda: now[0])
            image = Image.new('RGB', (2, 2))
            unknown = dict(screen_type='unknown')
            self.assertTrue(capture.capture(image, unknown))
            now[0] = 6
            self.assertTrue(capture.capture(image, error=ValueError()))
            now[0] = 31
            self.assertFalse(capture.capture(image, unknown))
            now[0] = 59
            self.assertTrue(capture.capture(image, unknown, RuntimeError()))
            metadata = [json.loads(p.read_text(encoding='utf-8')) for p in Path(directory).glob('*.json')]
            self.assertEqual(sum('screen_unrecognized' in r['reason'] for r in metadata), 1)
            now[0] = 64
            self.assertTrue(capture.capture(image, unknown))

    def test_all_real_failure_jsons_replay_with_production_unknown_cooldown(self):
        directory = Path(__file__).resolve().parents[1] / 'data' / 'ocr_failures_snapshot'
        if not directory.is_dir():
            self.skipTest('Read-only real-play failure snapshots are not available')
        counts = replay(directory)
        # The supplied read-only snapshot can be a different play session.
        # Verify all of its captures, retaining the original cooldown bound.
        snapshots = [json.loads(p.read_text(encoding='utf8')) for p in directory.glob('*.json')]
        self.assertGreater(len(snapshots), 0)
        self.assertEqual(counts['total'], len(snapshots))
        self.assertEqual(counts['before'], sum(bool(s.get('reason')) for s in snapshots))
        self.assertLessEqual(counts['after'], 30, counts)


class AnimationTests(unittest.TestCase):
    observation = temporal_fixture.TemporalTests.observation

    def test_monitor_waits_for_b_and_c_before_immutable_result_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = Monitor.__new__(Monitor)
            monitor.tracker = Tracker(Path(directory))
            for capture_id in (1, 2):
                monitor._consume_observation(dict(state='PLAYING', frame_hash='play'), capture_id, None, capture_id)
            session_id = monitor.tracker.session['session_id']
            consistency = ResultConsistency()
            for capture_id, color in ((3, 'black'), (4, 'gray'), (5, 'navy')):
                observation, image = self.observation(color)
                if capture_id == 3:
                    observation['result_ocr']['row'] = None
                consistency.observe(observation, session_id, capture_id, image)
                monitor._consume_observation(observation, capture_id, image, capture_id)
                if capture_id < 5:
                    self.assertEqual(monitor.tracker.session['status'], 'playing')
                    self.assertIsNone(monitor.tracker.last_result)
            self.assertEqual(monitor.tracker.last_session['status'], 'pending_result')
            self.assertEqual(monitor.tracker.last_session['result']['frame_hash'], observation['frame_hash'])
            self.assertEqual(observation['result_ocr']['temporal_validation']['status'], 'confirmed')

    def test_incomplete_then_two_good_frames_confirm_and_save_only_good_images(self):
        for mode in ('missing_row', 'missing_number'):
            consistency = ResultConsistency()
            a, image_a = self.observation('black')
            if mode == 'missing_row':
                a['result_ocr']['row'] = None
            else:
                a['result_ocr']['fields']['bad']['value'] = None
            consistency.observe(a, 'session', 1, image_a)
            self.assertTrue(incomplete_result(a['result_ocr']))
            self.assertEqual(consistency.samples, [])
            self.assertTrue(a['result_ocr']['temporal_validation']['incomplete'])
            b, image_b = self.observation('gray')
            c, image_c = self.observation('navy')
            consistency.observe(b, 'session', 2, image_b)
            self.assertEqual(b['result_ocr']['temporal_validation']['status'], 'pending_review')
            consistency.observe(c, 'session', 3, image_c)
            self.assertEqual(c['result_ocr']['temporal_validation']['status'], 'confirmed')
            self.assertEqual([s['capture_id'] for s in consistency.samples], [2, 3])
            with tempfile.TemporaryDirectory() as directory:
                consistency.save(directory, c['result_ocr'])
                self.assertEqual(issues(c['result_ocr'], dict(
                    session_id='session', result=dict(frame_hash=c['frame_hash'])), directory), [])

    def test_complete_conflicts_and_low_confidence_are_still_pending(self):
        for field, change in (('bad', dict(value=3)), ('grade', dict(confidence=.94))):
            consistency = ResultConsistency()
            a, image_a = self.observation('gray')
            b, image_b = self.observation('navy')
            b['result_ocr']['fields'][field].update(change)
            self.assertFalse(incomplete_result(b['result_ocr']))
            consistency.observe(a, 's', 1, image_a)
            consistency.observe(b, 's', 2, image_b)
            self.assertEqual(len(consistency.samples), 2)
            self.assertEqual(b['result_ocr']['temporal_validation']['status'], 'pending_review')

    def test_incomplete_frame_does_not_replace_good_evidence_or_extend_freshness(self):
        consistency = ResultConsistency()
        a, image = self.observation()
        consistency.observe(a, 's', 1, image)
        incomplete, image_b = self.observation('black')
        incomplete['result_ocr']['fields']['miss']['value'] = None
        consistency.observe(incomplete, 's', 10, image_b)
        self.assertEqual([s['capture_id'] for s in consistency.samples], [1])
        b, image_c = self.observation('navy')
        consistency.observe(b, 's', 20, image_c)
        self.assertEqual(b['result_ocr']['temporal_validation']['status'], 'pending_review')
        self.assertEqual([s['capture_id'] for s in consistency.samples], [20])


class AnimationAutomaticTests(unittest.TestCase):
    setUp = temporal_fixture.AutomaticIntegrationTests.setUp
    tearDown = temporal_fixture.AutomaticIntegrationTests.tearDown
    official_auto = temporal_fixture.AutomaticIntegrationTests.official_auto

    def test_incomplete_a_then_good_b_c_is_automatic_eligible_with_all_other_gates(self):
        self.official_auto()
        consistency = ResultConsistency()
        for capture_id, color in enumerate(('black', 'gray', 'navy')):
            image = Image.new('RGB', (1024, 800), color)
            from dance_results.monitor_ocr import image_hash
            observation = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=copy.deepcopy(self.ocr))
            if capture_id == 0:
                observation['result_ocr']['row'] = None
            consistency.observe(observation, self.sid, capture_id, image)
        self.session['result']['frame_hash'] = observation['frame_hash']
        folder = self.output / 'sessions' / self.sid
        atomic_json(folder / 'session.json', self.session)
        consistency.save(folder, observation['result_ocr'])
        row = self.service._read(self.sid)[1]
        row.update(locked_session=copy.deepcopy(self.session), ocr=observation['result_ocr'])
        self.assertEqual(automatic_request(self.service, row)[1], [])


class GradeViewsTests(unittest.TestCase):
    def refine(self, readings, page='A++'):
        crop = Image.new('RGB', (84, 64), '#555566')
        ImageDraw.Draw(crop).text((10, 15), 'A++', fill='#ff9900')

        class Engine:
            def recognize_line(self, image, **kwargs):
                text, confidence = next(answers)
                return [token(text, 980, 250, confidence)]

        answers = iter(readings)
        return refine_grade(crop, Engine(), cell([token('A++', 980, 250, .79)], numeric=False),
                            cell([token(page, 980, 250, .94)], numeric=False), (940, 215))

    def test_superscript_views_keep_the_actual_minimum_and_original_evidence(self):
        field, views = self.refine([('A++', .99), ('A++', .96)])
        self.assertEqual((field['value'], field['confidence']), ('A++', .96))
        self.assertIsNone(field['reason'])
        self.assertEqual(field['original_reading']['confidence'], .79)
        self.assertEqual(len(views), 2)

    def test_low_confidence_or_invalid_views_never_gain_confidence(self):
        for reading in ('A++', 'A**'):
            field, _ = self.refine([(reading, .94), ('A++', .99)])
            self.assertEqual(field['confidence'], .79)

    def test_conflicting_views_or_page_are_not_resolved_by_confidence(self):
        for readings, page in (([('A++', .99), ('A+', .99)], 'A++'),
                               ([('A++', .99), ('A++', .99)], 'A+')):
            field, _ = self.refine(readings, page)
            self.assertEqual(field['reason'], 'page_and_grade_roi_disagree_requires_review')
            self.assertEqual(field['confidence'], .79)


class ExamExtensionsTests(unittest.TestCase):
    def test_incomplete_annotation_requires_explicit_parser_evidence_and_screen(self):
        label = dict(sha256='a' * 64, fields={}, screen_type='results', expect_incomplete=True)
        for actual, wanted in ((dict(screen_type='results', incomplete_result=True), 'PASS'),
                               (dict(screen_type='results', incomplete_result=False), 'FAIL'),
                               (dict(screen_type='results'), 'FAIL'),
                               (dict(screen_type='unknown', incomplete_result=True), 'FAIL')):
            self.assertEqual(exam.score_sample('animation.png', label, actual)['status'], wanted)

    def test_incomplete_label_schema_and_merge_conflict_are_checked(self):
        label = dict(sha256='a' * 64, fields={}, screen_type='results', expect_incomplete=True)
        labels = {}
        exam.merge_label(labels, 'a.png', label, 'first')
        for invalid in (dict(label, expect_incomplete='true'), dict(label, screen_type='song_select'),
                        dict(label, expect_incomplete=False)):
            with self.assertRaises(ValueError):
                exam.merge_label(labels, 'a.png', invalid, 'second')

    def test_timing_is_printed_and_summarized_but_never_enters_baseline(self):
        label = dict(sha256='a' * 64, fields={}, screen_type='results')
        samples = [dict(exam.score_sample('a.png', label, dict(screen_type='results')), ocr_ms=n)
                   for n in (10, 20, 30, 40)]
        summary = exam.timing_summary(samples + [dict(status='SKIPPED')])
        self.assertEqual((summary['median_ms'], summary['p90_ms']), (25, 40))
        self.assertIsNone(exam.timing_summary([])['median_ms'])
        baseline = exam.make_baseline(samples, exam.summarize(samples))
        samples[0]['ocr_ms'] = 100000
        self.assertEqual(exam.compare_baseline(samples, exam.summarize(samples), baseline), [])
        self.assertNotIn('ocr_ms', json.dumps(baseline))
        stream = io.StringIO()
        with redirect_stdout(stream):
            exam.print_sample(samples[0])
        self.assertIn('OCR 100000.00 ms', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
