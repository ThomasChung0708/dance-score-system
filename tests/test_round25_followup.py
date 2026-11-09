"""Real-play follow-up: grade fragments, selection gaps and absent players."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image, ImageDraw

from dance_results.monitor import Monitor
from dance_results.monitor_ocr import apply_selection, image_hash
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_policy import AUTO_CONFIDENCE, SELECTION_REQUIRED
from dance_results.ocr_screens import result_context
from dance_results.ocr_temporal import ResultConsistency
from dance_results.result_ocr import cell, parse_result, recognize_result, refine_grade
from test_monitor import selected, scene
from test_result_review import result_tokens, token


def grade_ink(pluses=2, raised=True):
    crop = Image.new('RGB', (84, 64), '#555566')
    draw = ImageDraw.Draw(crop)
    draw.polygon([(8, 49), (20, 14), (29, 14), (42, 49), (33, 49),
                  (29, 37), (20, 37), (16, 49)], fill='#ff9900')
    draw.polygon([(23, 25), (21, 32), (27, 32)], fill='#555566')
    top = 19 if raised else 37
    for index in range(pluses):
        left = 46+index*18
        draw.rectangle((left, top+5, left+13, top+8), fill='#ff9900')
        draw.rectangle((left+5, top, left+8, top+13), fill='#ff9900')
    return crop


class GradeFragmentTests(unittest.TestCase):
    def refine(self, page='A', original='A++', pluses=2, raised=True,
               readings=(('A++', .97922), ('A++', .96197))):
        answers = iter(readings)

        def read(image, **kwargs):
            text, confidence = next(answers)
            return [token(text, 980, 250, confidence)]

        original_field = cell([token(original, 980, 250, .95378)], numeric=False)
        original_field['reason'] = 'page_and_grade_roi_disagree_requires_review'
        return refine_grade(grade_ink(pluses, raised), SimpleNamespace(recognize_line=read),
                            original_field, cell([token(page, 960, 250)], numeric=False), (940, 217))

    def test_page_or_full_cell_prefix_requires_two_strong_views_and_raised_ink(self):
        for page, original in (('A', 'A++'), ('A+', 'A++'), ('A', 'A+')):
            with self.subTest(page=page, original=original):
                field, views = self.refine(page, original)
                self.assertEqual(field['value'], 'A++')
                self.assertEqual(field['confidence'], .96197)
                self.assertGreaterEqual(field['confidence'], AUTO_CONFIDENCE)
                self.assertIsNone(field['reason'])
                self.assertEqual(field['superscript_plus_count'], 2)
                self.assertEqual(field['page_reading']['value'], page)
                self.assertEqual(field['original_reading']['value'], original)
                self.assertEqual(len(views), 2)

    def test_page_b_and_roi_app_remain_a_disagreement(self):
        field, _ = self.refine(page='B')
        self.assertEqual(field['reason'], 'page_and_grade_roi_disagree_requires_review')
        self.assertEqual(field['confidence'], .95378)

    def test_other_letter_in_full_cell_remains_a_disagreement(self):
        field, _ = self.refine(page='A', original='B')
        self.assertEqual(field['reason'], 'page_and_grade_roi_disagree_requires_review')

    def test_clear_a_plus_without_second_superscript_cannot_be_upgraded(self):
        for pluses, raised in ((0, True), (1, True), (2, False)):
            with self.subTest(pluses=pluses, raised=raised):
                field, _ = self.refine(page='A+', pluses=pluses, raised=raised)
                self.assertEqual(field['reason'], 'page_and_grade_roi_disagree_requires_review')
                self.assertEqual(field['confidence'], .95378)

    def test_low_or_disagreeing_dedicated_views_cannot_clear_page_fragment_reason(self):
        for readings in ((('A++', .949), ('A++', .99)), (('A++', .99), ('A+', .99))):
            field, _ = self.refine(readings=readings)
            self.assertEqual(field['reason'], 'page_and_grade_roi_disagree_requires_review')
            self.assertEqual(field['confidence'], .95378)

    def test_recognizer_propagates_grade_to_own_row_and_temporal_evidence(self):
        observations = []
        consistency = ResultConsistency()
        for capture_id, color in ((1, 'gray'), (2, 'navy')):
            image = Image.new('RGB', (1024, 800), color)
            image.paste(grade_ink(), (940, 215))
            tokens = result_tokens()
            tokens = [dict(t, text='A', confidence=.90226) if t['text'] == 'A+' else t for t in tokens]
            answers = iter((('A++', .95378), ('A++', .97922), ('A++', .96197)))

            def read(image, **kwargs):
                text, confidence = next(answers)
                return [token(text, 980, 250, confidence)]

            engine = SimpleNamespace(recognize=read, recognize_line=read)
            ocr = recognize_result(image, engine, tokens=tokens)
            self.assertEqual(ocr['raw_fields']['grade']['value'], 'A')
            self.assertEqual(ocr['fields']['grade']['value'], 'A++')
            self.assertIsNone(ocr['fields']['grade']['reason'])
            self.assertEqual(ocr['fields']['grade'], ocr['player_rows'][0]['fields']['grade'])
            observation = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=ocr)
            consistency.observe(observation, 'session', capture_id, image)
            observations.append(observation)
        self.assertEqual(observations[-1]['result_ocr']['temporal_validation']['status'], 'confirmed')


class SelectionGapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tracker = Tracker(Path(self.temp.name))
        self.sample = 0

    def send(self, observation, count=2):
        for _ in range(count):
            self.sample += 1
            self.tracker.consume(copy.deepcopy(observation), now=self.sample, sample_id=self.sample)

    def test_good_null_title_then_playing_retains_good_selection(self):
        self.send(selected())
        good = copy.deepcopy(self.tracker.selection)
        unreadable = selected()
        unreadable['fields']['title'].update(value=None, confidence=0, reason='not_readable')
        catalog = SimpleNamespace(packs=[], match=lambda *args: copy.deepcopy(unreadable['matching']))
        observation = dict(state='SONG_SELECT', frame_hash='transition', selection=dict(
            fields=unreadable['fields'], pack_list_visible=False))
        observation = apply_selection(observation, Image.new('RGB', (1024, 800)), self.tracker, catalog)
        self.send(observation)
        self.assertEqual(self.tracker.selection, good)
        self.send(scene('PLAYING'))
        self.assertEqual(self.tracker.session['selection'], good)
        self.assertIsNone(self.tracker.session['pending_reason'])

    def test_missing_core_identity_or_not_readable_keeps_selection_and_debounce(self):
        self.send(selected())
        before = (copy.deepcopy(self.tracker.selection), self.tracker.selection_key, self.tracker.selection_count)
        for key in SELECTION_REQUIRED:
            for missing in (True, False):
                with self.subTest(key=key, missing=missing):
                    unreadable = selected()
                    unreadable['fields'][key]['reason'] = 'not_readable'
                    if missing:
                        unreadable['fields'][key]['value'] = None
                    self.send(unreadable)
                    self.assertEqual((self.tracker.selection, self.tracker.selection_key,
                                      self.tracker.selection_count), before)

    def test_readable_different_title_with_missing_level_does_not_keep_a(self):
        self.send(selected(title='A'))
        partial = selected(title='B')
        partial['fields']['game_level'].update(value=None, confidence=0, reason='not_readable')
        self.send(partial)
        kept = ((self.tracker.selection or {}).get('fields') or {}).get('title', {}).get('value')
        self.assertNotEqual(kept, 'A')

    def test_readable_different_selection_b_replaces_a(self):
        self.send(selected(title='A'))
        self.send(selected(title='B'))
        self.send(scene('PLAYING'))
        self.assertEqual(self.tracker.session['selection']['fields']['title']['value'], 'B')

    def test_null_transition_does_not_resurrect_a_after_readable_b(self):
        self.send(selected(title='A'))
        self.send(selected(title='B'), count=1)
        unreadable = selected()
        unreadable['fields']['title']['value'] = None
        self.send(unreadable)
        self.send(scene('PLAYING'))
        self.assertIsNone(self.tracker.session['selection'])

    def test_unreadable_frames_do_not_extend_ttl(self):
        self.send(selected())
        unreadable = selected()
        unreadable['fields']['title']['value'] = None
        self.sample = 100
        self.send(unreadable)
        self.send(scene('PLAYING'))
        self.assertIsNone(self.tracker.session['selection'])
        self.assertEqual(self.tracker.session['pending_reason'], 'selection_expired')

    def test_explicit_invalidation_still_wins_after_unreadable_selection(self):
        for mode in ('window_minimized', 'UNKNOWN', 'LOBBY', 'pack'):
            with self.subTest(mode=mode):
                self.send(selected())
                unreadable = selected()
                unreadable['fields']['title']['value'] = None
                self.send(unreadable)
                if mode == 'window_minimized':
                    self.tracker.interrupt(mode)
                elif mode == 'pack':
                    self.tracker.current_pack = dict(pack_id=1, status='confirmed')
                    self.tracker.pack_observation(None)
                else:
                    self.send(scene(mode))
                self.assertIsNone(self.tracker.selection)


class AbsentPlayerMonitorTests(unittest.TestCase):
    def test_spectator_and_not_listed_result_match_original_tracker_behavior(self):
        for previous in ('PLAYING', 'SPECTATING'):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as directory:
                monitor = Monitor.__new__(Monitor)
                monitor.tracker = Tracker(Path(directory) / 'monitor')
                original = Tracker(Path(directory) / 'original')
                consistency = ResultConsistency()
                tokens = [dict(t, text='Other') if t['text'] == 'TC' else t for t in result_tokens()]
                ocr = parse_result(tokens)
                ocr['tokens'] = tokens
                self.assertIsNone(ocr['row'])
                observation = dict(state='RESULT', frame_hash='absent', result_ocr=ocr,
                                   context=result_context(tokens, 'TC'))
                image = Image.new('RGB', (1024, 800))
                for capture_id in (1, 2):
                    monitor._consume_observation(scene(previous), capture_id, None, capture_id)
                    original.consume(scene(previous), now=capture_id, sample_id=capture_id)
                for capture_id in (3, 4, 5):
                    consistency.observe(observation, (monitor.tracker.session or {}).get('session_id'),
                                        capture_id, image)
                    monitor._consume_observation(observation, capture_id, image, capture_id)
                    original.consume(observation, now=capture_id, image=image, sample_id=capture_id)
                self.assertEqual(monitor.tracker.state, original.state)
                self.assertEqual([e['event'] for e in monitor.tracker.events],
                                 [e['event'] for e in original.events])
                self.assertIsNone(monitor.tracker.session)
                if previous == 'PLAYING':
                    self.assertEqual(monitor.tracker.last_session['status'], 'pending_result')
                    folder = Path(directory) / 'monitor' / 'sessions' / monitor.tracker.last_session['session_id']
                    self.assertTrue((folder / 'result.png').is_file())
                    self.assertEqual(monitor.tracker.last_session['result']['frame_hash'], 'absent')
                else:
                    self.assertIsNone(monitor.tracker.last_result)
                    self.assertEqual(sum(e['event'] == 'unassociated_result' for e in monitor.tracker.events), 1)

    def test_own_name_before_combo_is_drawing_but_absent_name_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = Monitor.__new__(Monitor)
            monitor.tracker = Tracker(directory)
            for capture_id in (1, 2):
                monitor._consume_observation(scene('PLAYING'), capture_id, None, capture_id)
            tokens = [t for t in result_tokens() if t['text'] != '11']
            ocr = parse_result(tokens)
            ocr['tokens'] = tokens
            self.assertIsNone(ocr['row'])
            for capture_id in (3, 4):
                monitor._consume_observation(dict(state='RESULT', frame_hash='drawing', result_ocr=ocr),
                                             capture_id, None, capture_id)
            self.assertEqual(monitor.tracker.session['status'], 'playing')
            self.assertIsNone(monitor.tracker.last_result)

    def test_own_row_with_missing_number_waits_for_complete_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = Monitor.__new__(Monitor)
            monitor.tracker = Tracker(directory)
            for capture_id in (1, 2):
                monitor._consume_observation(scene('PLAYING'), capture_id, None, capture_id)
            ocr = parse_result(result_tokens())
            ocr['fields']['miss']['value'] = None
            for capture_id in (3, 4):
                monitor._consume_observation(dict(state='RESULT', frame_hash='drawing', result_ocr=ocr),
                                             capture_id, None, capture_id)
            self.assertEqual(monitor.tracker.session['status'], 'playing')
            self.assertIsNone(monitor.tracker.last_result)


if __name__ == '__main__':
    unittest.main()
