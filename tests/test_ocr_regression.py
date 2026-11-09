from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import ocr_regression as exam


DIGEST = 'a' * 64


def label(**fields):
    return {'sha256': DIGEST, 'screen_type': 'results', 'fields': fields}


def score(name='sample.png', **fields):
    return exam.score_sample(name, label(**fields), {'screen_type': 'results', **fields})


class ScoringTests(unittest.TestCase):
    def test_null_is_not_evaluated_even_when_actual_is_non_null(self):
        result = exam.score_sample('a.png', label(miss=None, cool=0),
                                   {'screen_type': 'results', 'miss': 23, 'cool': 0})
        fields = exam.summarize([result])
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(fields['miss'], {'correct': 0, 'total': 0, 'accuracy': None})
        self.assertEqual(fields['cool']['total'], 1)
        self.assertEqual(fields['judgments']['total'], 1)

    def test_missing_actual_is_a_mismatch_with_expected_and_got(self):
        result = exam.score_sample('a.png', label(title='Song', difficulty='HARD'), {})
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn({'field': 'title', 'expected': 'Song', 'got': None,
                       'evaluated': True, 'correct': False}, result['mismatches'])

    def test_exact_matching_does_not_normalize_titles(self):
        result = exam.score_sample('a.png', label(title='Song'),
                                   {'screen_type': 'results', 'title': 'song'})
        self.assertEqual(result['mismatches'][0]['field'], 'title')

    def test_derived_ex_and_individual_and_grouped_judgments(self):
        result = exam.score_sample('a.png', label(perfect=10, cool=2, bad=0, miss=1),
                                   {'screen_type': 'results', 'perfect': 10, 'cool': 3, 'bad': 0, 'miss': 1})
        fields = exam.summarize([result])
        self.assertEqual(fields['judgments'], {'correct': 3, 'total': 4, 'accuracy': .75})
        self.assertEqual(fields['ex_score']['correct'], 0)
        ex = next(item for item in result['mismatches'] if item['field'] == 'ex_score')
        self.assertEqual((ex['expected'], ex['got']), (22, 23))

    def test_zero_ex_is_valid_and_unknown_is_never_zero(self):
        self.assertEqual(exam.derived_ex({'perfect': 0, 'cool': 0}), 0)
        for fields in ({'perfect': 0}, {'perfect': True, 'cool': 0}, {'perfect': -1, 'cool': 0}):
            self.assertIsNone(exam.derived_ex(fields))
        self.assertEqual(exam.summarize([score(perfect=0, cool=0)])['ex_score']['accuracy'], 1)

    def test_null_explicit_ex_label_is_respected(self):
        self.assertEqual(exam.summarize([score(perfect=1, cool=0, ex_score=None)])['ex_score']['total'], 0)

    def test_extra_labelled_fields_and_local_status_are_scored(self):
        expected = label(artist='Artist', duration_seconds=30, new_field=42)
        expected['local_player_status'] = 'failed'
        actual = {'screen_type': 'results', **expected['fields'], 'local_player_status': 'failed'}
        result = exam.score_sample('a.png', expected, actual)
        fields = exam.summarize([result])
        self.assertEqual(result['status'], 'PASS')
        for name in ('artist', 'duration_seconds', 'new_field', 'local_player_status'):
            self.assertEqual(fields[name]['accuracy'], 1)

    def test_skipped_images_do_not_affect_totals(self):
        skipped = {'status': 'SKIPPED'}
        fields = exam.summarize([skipped, score(miss=0)])
        self.assertEqual(fields['screen_type']['total'], 1)
        self.assertEqual(fields['miss']['total'], 1)

    def test_scoring_is_pure_and_ocr_error_is_failure(self):
        expected = label(perfect=4, cool=1)
        actual = {'screen_type': 'results', 'perfect': 4, 'cool': 1}
        before = copy.deepcopy((expected, actual))
        result = exam.score_sample('a.png', expected, actual, error='fake OCR failure')
        self.assertEqual((expected, actual), before)
        self.assertEqual(result['status'], 'FAIL')


class BaselineTests(unittest.TestCase):
    def baseline(self, samples):
        return exam.make_baseline(samples, exam.summarize(samples))

    def test_fresh_baseline_and_improvements_pass(self):
        before = [score(miss=1)]
        baseline = self.baseline(before)
        self.assertEqual(exam.compare_baseline(before, exam.summarize(before), baseline), [])
        baseline['fields']['miss'] = {'correct': 0, 'total': 1, 'accuracy': 0}
        baseline['samples']['sample.png']['status'] = 'FAIL'
        self.assertEqual(exam.compare_baseline(before, exam.summarize(before), baseline), [])

    def test_accuracy_drop_fails_and_comparison_uses_counts(self):
        baseline = self.baseline([score(miss=1)])
        now = [exam.score_sample('sample.png', label(miss=1), {'screen_type': 'results', 'miss': 2})]
        regressions = exam.compare_baseline(now, exam.summarize(now), baseline)
        self.assertTrue(any(item.get('field') == 'miss' for item in regressions))
        self.assertTrue(any(item.get('image') == 'sample.png' for item in regressions))

    def test_aggregate_gain_cannot_hide_previous_pass_failure(self):
        before = [score('a.png', miss=1),
                  exam.score_sample('b.png', label(miss=1), {'screen_type': 'results', 'miss': 2})]
        now = [exam.score_sample('a.png', label(miss=1), {'screen_type': 'results', 'miss': 2}),
               score('b.png', miss=1), score('c.png', miss=1)]
        regressions = exam.compare_baseline(now, exam.summarize(now), self.baseline(before))
        self.assertEqual(len(regressions), 1)
        self.assertEqual(regressions[0]['image'], 'a.png')

    def test_missing_image_is_skipped_but_removed_label_is_detected(self):
        before = [score('a.png', miss=1)]
        skipped = [{**before[0], 'status': 'SKIPPED', 'comparisons': []}]
        baseline = self.baseline(before)
        self.assertEqual(exam.compare_baseline(skipped, exam.summarize(skipped), baseline), [])
        self.assertTrue(exam.compare_baseline([], exam.summarize([]), baseline))

    def test_missing_passing_image_does_not_lower_unchanged_mixed_accuracy(self):
        before = [score('a.png', miss=1),
                  exam.score_sample('b.png', label(miss=1), {'screen_type': 'results', 'miss': 2})]
        now = [{**before[0], 'status': 'SKIPPED', 'comparisons': []}, before[1]]
        self.assertEqual(exam.compare_baseline(now, exam.summarize(now), self.baseline(before)), [])

    def test_regression_in_remaining_fields_is_detected_with_missing_images(self):
        before = [score('a.png', miss=1), score('b.png', miss=1)]
        now = [{**before[0], 'status': 'SKIPPED', 'comparisons': []},
               exam.score_sample('b.png', label(miss=1), {'screen_type': 'results', 'miss': 2})]
        regressions = exam.compare_baseline(now, exam.summarize(now), self.baseline(before))
        self.assertTrue(any(item.get('field') == 'miss' for item in regressions))
        self.assertTrue(any(item.get('field') == 'judgments' for item in regressions))
        self.assertFalse(any(item.get('image') == 'a.png' for item in regressions))


class LabelTests(unittest.TestCase):
    def test_merge_existing_and_new_labels_without_double_counting(self):
        labels = {}
        exam.merge_label(labels, 'a.PNG', label(title='Song'), 'phase4a')
        exam.merge_label(labels, 'a.png', {'sha256': DIGEST, 'fields': {'miss': 1}}, 'phase4c')
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels['a.PNG']['fields'], {'title': 'Song', 'miss': 1})

    def test_conflicting_answers_or_hash_are_rejected(self):
        for conflicting in (label(title='Other'), {**label(title='Song'), 'sha256': 'b' * 64}):
            labels = {}
            exam.merge_label(labels, 'a.png', label(title='Song'), 'first')
            with self.assertRaises(ValueError):
                exam.merge_label(labels, 'a.png', conflicting, 'second')

    def test_nested_pngs_allowed_and_escaping_paths_rejected(self):
        self.assertEqual(exam.image_name('new\\fail.PNG'), 'new/fail.PNG')
        for name in ('../a.png', '/a.png', 'C:\\a.png', 'a.jpg'):
            with self.assertRaises(ValueError):
                exam.image_name(name)

    def test_label_folder_reads_only_json_and_requires_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            fixtures = Path(directory)
            for name in ('phase4a_ground_truth.json', 'phase4c_ground_truth.json'):
                exam.write_json(fixtures / name, {'images': {}})
            folder = fixtures / 'ocr_labels'
            folder.mkdir()
            (folder / 'template.json.example').write_text('not a label', encoding='utf-8')
            exam.write_json(folder / 'new.json', {'image': 'sub/a.png', **label(miss=1)})
            self.assertEqual(list(exam.load_labels(fixtures)), ['sub/a.png'])
            exam.write_json(folder / 'invalid.json', {'image': 'b.png'})
            with self.assertRaises(ValueError):
                exam.load_labels(fixtures)


class OfflineRunnerTests(unittest.TestCase):
    def test_runner_skips_missing_and_reuses_cache_flag_without_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            fixtures = project / 'tests' / 'fixtures'
            source = project / 'tests' / '專案測試圖片'
            source.mkdir(parents=True)
            image = source / 'a.png'
            image.write_bytes(b'fake-image')
            expected = {**label(miss=1), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
            labels = {'a.png': expected, 'missing.png': label()}
            models = project / '.ocr-models'
            models.mkdir()
            for filename in ('ch_PP-OCRv5_det_mobile.onnx', 'ch_PP-OCRv5_rec_mobile.onnx'):
                (models / filename).touch()
            catalog = Mock()
            engine = Mock()
            engine.metadata.return_value = {'profile': 'fake'}
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                    patch.object(exam, 'PROJECT', project), patch.object(exam, 'FIXTURES', fixtures), \
                    patch.object(exam, 'load_labels', return_value=labels), \
                    patch.object(exam.ocr_static, 'ReadOnlyCatalog', return_value=catalog), \
                    patch.object(exam.ocr_static, 'audit', return_value={}), \
                    patch.object(exam.ocr_static, 'check_integrity'), \
                    patch.object(exam.ocr_static, 'LocalOCREngine', return_value=engine), \
                    patch.object(exam, 'recognize_sample', return_value={'screen_type': 'results', 'miss': 1}) as recognize:
                self.assertEqual(exam.main(['--update-baseline', '--no-cache']), 0)
                self.assertEqual(recognize.call_args.args, (image, expected['sha256'], engine, catalog))
                self.assertTrue(recognize.call_args.kwargs['no_cache'])
                self.assertEqual(exam.main([]), 0)
                recognize.return_value = {'screen_type': 'results', 'miss': 3}
                self.assertEqual(exam.main([]), 1)
                recognize.side_effect = RuntimeError('fake OCR error')
                self.assertEqual(exam.main([]), 1)
                report = json.loads((project / 'reports' / 'ocr_regression' / 'latest.json').read_text())
                self.assertEqual(report['counts'], {'PASS': 0, 'FAIL': 1, 'SKIPPED': 1})
                image.write_bytes(b'wrong-hash')
                before = (fixtures / 'ocr_regression_baseline.json').read_bytes()
                self.assertEqual(exam.main(['--update-baseline']), 2)
                self.assertEqual((fixtures / 'ocr_regression_baseline.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
