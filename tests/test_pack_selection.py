from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.pack_selection import infer_selected_pack, row_hint, pack_rows, analyze_pack_crop
from dance_results.ocr_failures import failure_details
from dance_results.monitor_ocr import apply_selection
from dance_results.monitor_tracking import Tracker
from dance_results.result_ocr import recognize_result
from tools import ocr_regression as exam
from test_result_review import result_tokens, token


def catalog():
    result = ReadOnlyCatalog.__new__(ReadOnlyCatalog)
    result.packs = [dict(pack_id=1, root_id=1, name='Pack A'),
                    dict(pack_id=2, root_id=1, name='Pack B')]
    result.songs = {sid: dict(pack_id=pid, source_type='custom')
                    for sid, pid in [(10, 1), (11, 1), (20, 2), (21, 2)]}
    result.index = {'one': {10}, 'two': {11}, 'shared': {11, 21},
                    'other': {20}, 'alias': {10}}
    return result


def selection(*titles, hint=None, visible=('Pack A', 'Pack B')):
    return dict(song_title_tokens=[token(t, 500, 250+i*30) for i, t in enumerate(titles)],
                visible_packs=[dict(text=t) for t in visible], pack_row_hint=hint)


class InferenceTests(unittest.TestCase):
    def test_two_distinct_visible_titles_choose_unique_pack(self):
        s = infer_selected_pack(selection('one', 'shared'), catalog())
        self.assertEqual((s['selected_pack'], s['selected_pack_id']), ('Pack A', 1))
        self.assertTrue(s['selected_pack_evidence']['diagnostic_only'])

    def test_one_title_duplicate_tokens_and_aliases_do_not_count_twice(self):
        for titles in [('one',), ('one', 'one'), ('one', 'alias'), ('shared', 'shared')]:
            self.assertIsNone(infer_selected_pack(selection(*titles), catalog())['selected_pack'])

    def test_duplicate_song_ids_for_one_title_do_not_prove_two_titles(self):
        c = catalog()
        c.index['one'] = {10, 11}
        self.assertIsNone(infer_selected_pack(selection('one'), c)['selected_pack'])

    def test_low_confidence_and_no_mapping_abstain(self):
        s = selection('one', 'two')
        s['song_title_tokens'][1]['confidence'] = .7
        self.assertIsNone(infer_selected_pack(s, catalog())['selected_pack'])
        c = catalog()
        c.index = {}
        self.assertIsNone(infer_selected_pack(selection('one', 'two'), c)['selected_pack'])

    def test_visible_left_list_is_a_consistency_constraint(self):
        self.assertIsNone(infer_selected_pack(selection('one', 'two', visible=('Pack B',)), catalog())['selected_pack'])
        self.assertEqual(infer_selected_pack(selection('one', 'two', visible=()), catalog())['selected_pack_id'], 1)
        c = catalog()
        c.packs = []
        s = infer_selected_pack(selection('one', 'two'), c)
        self.assertIsNone(s['selected_pack'])
        self.assertEqual(s['selected_pack_evidence']['uncertainty'], 'visible_packs_missing_from_catalog')

    def test_disagreement_clears_previous_inference(self):
        s = selection('one', 'two', hint='Pack B')
        s.update(selected_pack='Pack A', selected_pack_id=1)
        s = infer_selected_pack(s, catalog())
        self.assertIsNone(s['selected_pack'])
        self.assertIsNone(s['selected_pack_id'])
        self.assertEqual(s['pack_reason'], 'selected_pack_disagreement')

    def test_tie_requires_consistent_secondary_hint(self):
        s = selection('one', 'two', 'other', 'shared')
        self.assertIsNone(infer_selected_pack(copy.deepcopy(s), catalog())['selected_pack'])
        s['pack_row_hint'] = 'Pack B'
        self.assertEqual(infer_selected_pack(s, catalog())['selected_pack_id'], 2)

    def test_hint_alone_never_selects(self):
        self.assertIsNone(infer_selected_pack(selection(hint='Pack A'), catalog())['selected_pack'])

    def test_failure_capture_distinguishes_uncertain_and_disagreement(self):
        for hint, titles, expected in [('Pack B', ('one', 'two'), 'selected_pack_disagreement'),
                                       (None, ('one',), 'selected_pack_uncertain')]:
            s = infer_selected_pack(selection(*titles, hint=hint), catalog())
            s['pack_list_visible'] = True
            reasons = failure_details(dict(screen_type='song_select', selection=s))['reason']
            if expected == 'selected_pack_disagreement':
                self.assertIn(expected, reasons)
            else:
                self.assertNotIn(expected, reasons)

    def test_failure_capture_ignores_pack_uncertainty_without_pack_list(self):
        s = infer_selected_pack(selection('one'), catalog())
        s['pack_list_visible'] = False
        reasons = failure_details(dict(screen_type='song_select', selection=s))['reason']
        self.assertNotIn('selected_pack_uncertain', reasons)
        self.assertNotIn('selected_pack_disagreement', reasons)

    def test_successful_inference_is_persisted_for_tracker_consumption(self):
        c = catalog()
        c.match_identity = lambda *args: dict(status='pending_manual_review', bound_song_id=None, reasons=[])
        s = selection('one', 'two')
        s.update(pack_list_visible=True, fields={})
        s['visible_packs'] = [dict(text='Pack A', evidence=token('Pack A', 180, 260)),
                              dict(text='Pack B', evidence=token('Pack B', 180, 300))]
        with tempfile.TemporaryDirectory() as directory:
            tracker = Tracker(Path(directory))
            tracker.current_pack = dict(pack_id=2, root_id=1, status='confirmed')
            observed = apply_selection(dict(state='SONG_SELECT', selection=s),
                                       Image.new('RGB', (1024, 800)), tracker, c)
            self.assertEqual(observed['selection']['selected_pack_id'], 1)
            self.assertEqual(tracker.current_pack['pack_id'], 1)
            self.assertIsNone(observed['matching']['bound_song_id'])
            apply_selection(dict(state='SONG_SELECT', selection=s),
                            Image.new('RGB', (1024, 800)), tracker, c)
            self.assertEqual(tracker.current_pack['pack_id'], 1)
            self.assertEqual(tracker.current_pack['source'], 'right_song_list_unique_pack')


class RowHintTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(text=name, evidence=token(name, 90, y))
                     for name, y in [('Pack A', 50), ('Pack B', 90), ('Pack C', 130)]]
        self.region = (10, 20, 190, 160)

    def test_unique_paired_edges_are_colour_independent(self):
        for colour in [(90, 180, 40), (100, 50, 180), (160, 160, 160)]:
            image = Image.new('RGB', (200, 180), (30, 30, 30))
            ImageDraw.Draw(image).rectangle((8, 76, 192, 104), fill=colour)
            self.assertEqual(row_hint(image, self.rows, self.region)[0], 'Pack B')

    def test_identical_borders_or_uniform_rows_abstain(self):
        image = Image.new('RGB', (200, 180), (30, 70, 110))
        self.assertIsNone(row_hint(image, self.rows, self.region)[0])
        for y in (50, 90, 130):
            ImageDraw.Draw(image).rectangle((8, y-14, 192, y+14), outline='white', width=2)
        self.assertIsNone(row_hint(image, self.rows, self.region)[0])

    def test_busy_coloured_background_and_alike_rows_abstain(self):
        image = Image.new('RGB', (200, 180))
        image.putdata([((x*7+y*3) % 150, (x*3+y*5) % 170, (x*11+y*7) % 180)
                       for y in range(180) for x in range(200)])
        self.assertIsNone(row_hint(image, self.rows, self.region)[0])

    def test_single_edge_and_low_confidence_label_abstain(self):
        image = Image.new('RGB', (200, 180), 'black')
        ImageDraw.Draw(image).rectangle((8, 76, 192, 179), fill='white')
        self.assertIsNone(row_hint(image, self.rows, self.region)[0])
        image = Image.new('RGB', (200, 180), 'black')
        ImageDraw.Draw(image).rectangle((8, 76, 192, 104), fill='white')
        self.rows[1]['evidence']['confidence'] = .5
        self.assertIsNone(row_hint(image, self.rows, self.region)[0])

    def tinted_image(self, indices=(1,), colour=(115, 100, 112)):
        image = Image.new('RGB', (200, 180), (100, 100, 100))
        for index in indices:
            y = (50, 90, 130)[index]
            ImageDraw.Draw(image).rectangle((8, y-14, 192, y+14), fill=colour)
        return image

    def test_plain_background_weak_tint_without_paired_edges_is_detected(self):
        chosen, evidence = row_hint(self.tinted_image(), self.rows, self.region)
        self.assertEqual(chosen, 'Pack B')
        self.assertEqual(evidence[1]['edge_score'], 0)
        self.assertEqual(evidence[1]['tint_comparable'], ['Pack A', 'Pack C'])
        self.assertGreater(evidence[1]['tint_margin'], 1)

    def test_tint_with_only_one_comparable_neighbour_abstains(self):
        image = self.tinted_image()
        for y in range(112, 149):
            for x in range(200):
                image.putpixel((x, y), ((x*7+y*3) % 150, (x*3+y*5) % 170,
                                        (x*11+y*7) % 180))
        chosen, evidence = row_hint(image, self.rows, self.region)
        self.assertIsNone(chosen)
        self.assertEqual(evidence[1]['tint_comparable'], ['Pack A'])

    def test_colourful_artwork_with_alike_rows_cannot_supply_tint(self):
        image = self.tinted_image()
        for y in range(180):
            for x in range(200):
                colour = (40, 140, 70) if (x + y) % 2 else (160, 60, 150)
                image.putpixel((x, y), colour)
        chosen, evidence = row_hint(image, self.rows, self.region)
        self.assertIsNone(chosen)
        self.assertTrue(all(not row['tint_candidate'] for row in evidence))

    def test_two_equally_tinted_rows_abstain_including_untinted_row(self):
        chosen, evidence = row_hint(self.tinted_image((0, 1)), self.rows, self.region)
        self.assertIsNone(chosen)
        self.assertTrue(all(not row['tint_candidate'] for row in evidence))

    def test_tint_compares_all_local_neighbours_instead_of_favourable_subset(self):
        self.rows.append(dict(text='Pack D', evidence=token('Pack D', 90, 170)))
        image = Image.new('RGB', (200, 220), (100, 100, 100))
        draw = ImageDraw.Draw(image)
        for y in (90, 170):
            draw.rectangle((8, y-14, 192, y+14), fill=(115, 100, 112))
        chosen, evidence = row_hint(image, self.rows, (10, 20, 190, 200))
        self.assertIsNone(chosen)
        self.assertEqual(evidence[1]['tint_comparable'], ['Pack A', 'Pack C', 'Pack D'])

    def test_tint_is_relative_and_hue_independent(self):
        for colour in [(85, 100, 88), (100, 115, 100), (112, 100, 85)]:
            self.assertEqual(row_hint(self.tinted_image(colour=colour), self.rows, self.region)[0],
                             'Pack B')
        uniform = Image.new('RGB', (200, 180), (115, 100, 112))
        self.assertIsNone(row_hint(uniform, self.rows, self.region)[0])

    def test_tint_below_quantization_and_spatial_noise_margins_abstains(self):
        self.assertIsNone(row_hint(self.tinted_image(colour=(105, 100, 104)),
                                   self.rows, self.region)[0])
        image = self.tinted_image(colour=(110, 100, 108))
        for y in range(180):
            for x in range(200):
                r, g, b = image.getpixel((x, y))
                noise = (x % 5 - 2) * 3
                image.putpixel((x, y), (r+noise, g, b+noise))
        chosen, evidence = row_hint(image, self.rows, self.region)
        self.assertIsNone(chosen)
        self.assertLess(evidence[1]['tint_margin'], 1)

    def test_text_is_masked_and_broad_background_coverage_is_required(self):
        image = self.tinted_image()
        ImageDraw.Draw(image).rectangle((81, 82, 99, 98), fill='white')
        self.assertEqual(row_hint(image, self.rows, self.region)[0], 'Pack B')
        self.rows[1]['evidence']['box'] = [[10, 70], [190, 70], [190, 110], [10, 110]]
        self.assertIsNone(row_hint(image, self.rows, self.region)[0])

    def test_low_confidence_tint_label_abstains(self):
        self.rows[1]['evidence']['confidence'] = .5
        self.assertIsNone(row_hint(self.tinted_image(), self.rows, self.region)[0])

    def test_edge_and_tint_disagreement_abstains(self):
        image = Image.new('RGB', (200, 180), (30, 30, 30))
        ImageDraw.Draw(image).rectangle((8, 76, 192, 104), fill='white')
        diagnostics = [dict(text=row['text']) for row in self.rows]
        with patch('dance_results.pack_selection._local_tint_hint',
                   return_value=('Pack A', diagnostics)):
            self.assertIsNone(row_hint(image, self.rows, self.region)[0])

    def test_split_crop_names_are_joined_without_counts_or_classification(self):
        tokens = [token('Group', 20, 10), token('Pack', 30, 50), token('A', 70, 51),
                  token('74', 160, 50), token('Pack B', 70, 90)]
        rows = pack_rows(tokens, (0, 30, 180, 120))
        self.assertEqual([r['text'] for r in rows], ['Pack A', 'Pack B'])
        s = analyze_pack_crop(tokens, Image.new('RGB', (200, 150)), catalog())
        self.assertIsNone(s['selected_pack'])
        self.assertEqual(s['selected_pack_evidence']['visible_pack_ids'], [1, 2])


class PackScoringTests(unittest.TestCase):
    def label(self, allowed=False):
        return dict(sha256='a'*64, fields=dict(selected_pack='Pack A'),
                    allow_uncertain=['selected_pack'] if allowed else [])

    def test_correct_uncertain_and_wrong_are_distinct(self):
        for allowed in (False, True):
            correct = exam.score_sample('a.png', self.label(allowed), dict(selected_pack='Pack A'))
            wrong = exam.score_sample('a.png', self.label(allowed), dict(selected_pack='Pack B'))
            missing = exam.score_sample('a.png', self.label(allowed), dict(selected_pack=None))
            self.assertEqual(correct['status'], 'PASS')
            self.assertEqual(wrong['status'], 'FAIL')
            self.assertEqual(missing['status'], 'UNCERTAIN' if allowed else 'FAIL')
            self.assertEqual(missing['comparisons'][0]['outcome'], 'UNCERTAIN')
            self.assertEqual(exam.summarize([correct, wrong, missing])['selected_pack'],
                             dict(correct=1, total=3, uncertain=1, accuracy=1/3))

    def test_allowed_uncertainty_cannot_hide_other_failures(self):
        label = self.label(True)
        label['fields']['title'] = 'Song'
        self.assertEqual(exam.score_sample('a.png', label, {})['status'], 'FAIL')

    def test_allow_uncertain_validation_and_merge(self):
        labels = {}
        exam.merge_label(labels, 'a.png', self.label(True), 'source')
        self.assertEqual(labels['a.png']['allow_uncertain'], ['selected_pack'])
        for invalid in ['selected_pack', ['title'], [None]]:
            with self.assertRaises(ValueError):
                exam.merge_label({}, 'a.png', dict(self.label(), allow_uncertain=invalid), 'source')

    def test_wrong_pack_regresses_from_allowed_uncertainty_baseline(self):
        before = [exam.score_sample('a.png', self.label(True), {})]
        baseline = exam.make_baseline(before, exam.summarize(before))
        now = [exam.score_sample('a.png', self.label(True), dict(selected_pack='Pack B'))]
        self.assertTrue(exam.compare_baseline(now, exam.summarize(now), baseline))

    def test_crop_runner_bypasses_classifier_and_preserves_native_coordinates(self):
        class Engine:
            def recognize(self, image):
                return [token('Group', 20, 10), token('Pack A', 60, 50)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'crop.png'
            Image.new('RGB', (200, 100)).save(path)
            with patch.object(exam.ocr_static, 'classify', side_effect=AssertionError('must bypass')):
                actual = exam.recognize_sample(path, 'digest', Engine(), catalog(),
                                               no_cache=True, sample_kind='pack_list_crop')
            self.assertEqual(actual['screen_type'], 'pack_list_crop')
            self.assertIsNone(actual['selected_pack'])


class NicknameRereadTests(unittest.TestCase):
    def test_failed_badge_is_parsed_after_bounded_line_confirms_name(self):
        tokens = result_tokens() + [token('OVER', 25, 250)]
        tokens[4]['confidence'] = .83
        image = Image.new('RGB', (1024, 800), (50, 50, 70))
        ImageDraw.Draw(image).text((180, 246), 'TC', fill='white')

        class Engine:
            def recognize(self, image, **kwargs):
                return []

            def recognize_line(self, image, *, origin=(0, 0), scale=1):
                if origin[0] > 300:
                    return []
                return [token('TC', origin[0]+image.width/scale/2,
                              origin[1]+image.height/scale/2, .99)]

        result = recognize_result(image, Engine(), tokens=tokens)
        self.assertEqual(result['fields']['completion_status']['value'], 'FAILED')
        self.assertEqual(result['fields']['player_name']['confidence'], .99)
        self.assertEqual(result['raw_fields']['player_name']['confidence'], .83)
        self.assertTrue(any(r.get('preprocessing') == 'bounded_bright_foreground_line' for r in result['roi_rereads']))

    def test_unconfirmed_or_different_nickname_never_gains_status(self):
        tokens = result_tokens() + [token('OVER', 25, 250)]
        tokens[4]['confidence'] = .83
        image = Image.new('RGB', (1024, 800), 'black')
        ImageDraw.Draw(image).text((180, 246), 'TC', fill='white')
        for text, confidence in [('TC', .6), ('Other', .99)]:
            class Engine:
                def recognize(self, image, **kwargs):
                    return []

                def recognize_line(self, image, **kwargs):
                    return [token(text, 185, 250, confidence)]
            self.assertIsNone(recognize_result(image, Engine(), tokens=tokens)['fields']['completion_status']['value'])


if __name__ == '__main__':
    unittest.main()
