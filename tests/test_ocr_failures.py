from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from dance_results.monitor import Monitor
from dance_results.ocr_failures import FailureCapture, failure_details


class FakeFrame:
    def save(self, stream, *, format):
        assert format == 'PNG'
        stream.write(b'fake PNG bytes')


def observation(**field):
    return {
        'screen_type': 'song_select',
        'ocr_fields': {'title': {'value': 'Song', 'confidence': .99, **field}},
    }


class FailureCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / 'ocr_failures'
        self.now = 0
        self.capture = FailureCapture(self.directory, clock=lambda: self.now)
        self.frame = FakeFrame()

    def tearDown(self):
        self.temp.cleanup()

    def test_low_confidence_saves_pair_with_small_metadata_and_utc_timestamp(self):
        original = observation(confidence=.7)
        before = copy.deepcopy(original)
        self.assertTrue(self.capture.capture(self.frame, original))
        self.assertEqual(original, before)
        metadata = json.loads(next(self.directory.glob('*.json')).read_text(encoding='utf-8'))
        self.assertIn('title: low_confidence', metadata['reason'])
        self.assertEqual(metadata['fields'], {'title': 'Song'})
        self.assertEqual(metadata['confidence'], {'title': .7})
        self.assertEqual(metadata['screen_type_guess'], 'song_select')
        self.assertTrue(metadata['timestamp'].endswith('+00:00'))
        self.assertEqual((self.directory / metadata['image']).read_bytes(), b'fake PNG bytes')

    def test_healthy_observation_creates_no_directory(self):
        self.assertFalse(self.capture.capture(self.frame, observation()))
        self.assertFalse(self.directory.exists())

    def test_unknown_and_recognition_exception_are_captured(self):
        self.assertTrue(self.capture.capture(self.frame, {'screen_type': 'unknown'}))
        self.now = 6
        self.assertTrue(self.capture.capture(self.frame, error=ValueError('failure')))
        self.assertEqual(len(list(self.directory.glob('*.png'))), 2)

    def test_missing_zero_and_parse_reason_are_distinct(self):
        self.assertIn('title: missing', failure_details(observation(value=None))['reason'])
        self.assertEqual(failure_details(observation(value=0))['reason'], [])
        self.assertIn('title: ambiguous', failure_details(observation(reason='ambiguous'))['reason'])

    def test_pending_temporal_uncertainty_and_result_errors_are_captured(self):
        result = {'screen_type': 'results', 'result_ocr': {
            'fields': {'miss': {'value': 0, 'confidence': .99}},
            'errors': ['local_player_missing'],
            'temporal_validation': {'status': 'pending_review', 'reasons': ['conflicting_miss']},
        }}
        before = copy.deepcopy(result)
        self.assertTrue(self.capture.capture(self.frame, result))
        self.assertEqual(result, before)
        metadata = json.loads(next(self.directory.glob('*.json')).read_text())
        self.assertIn('conflicting_miss', metadata['reason'])
        self.assertIn('local_player_missing', metadata['reason'])

    def test_rate_limit_applies_even_when_reasons_alternate(self):
        self.assertTrue(self.capture.capture(self.frame, observation(confidence=.5)))
        self.now = 1
        self.assertFalse(self.capture.capture(self.frame, {'screen_type': 'unknown'}))
        self.now = 5
        self.assertTrue(self.capture.capture(self.frame, {'screen_type': 'unknown'}))

    def test_identical_consecutive_reasons_are_deduplicated_for_thirty_seconds(self):
        failed = observation(confidence=.5)
        self.assertTrue(self.capture.capture(self.frame, failed))
        self.now = 6
        self.assertFalse(self.capture.capture(self.frame, failed))
        self.now = 29
        self.assertFalse(self.capture.capture(self.frame, failed))
        self.now = 30
        self.assertTrue(self.capture.capture(self.frame, failed))

    def test_cap_counts_existing_and_orphan_files_and_survives_restart(self):
        self.directory.mkdir()
        (self.directory / 'orphan.png').write_bytes(b'existing evidence')
        self.capture.max_files = 3
        self.assertTrue(self.capture.capture(self.frame, {'screen_type': 'unknown'}))
        restarted = FailureCapture(self.directory, max_files=3)
        self.assertFalse(restarted.capture(self.frame, error=RuntimeError()))
        self.assertEqual(len(list(self.directory.iterdir())), 3)
        self.assertEqual((self.directory / 'orphan.png').read_bytes(), b'existing evidence')

    def test_frame_save_exception_is_swallowed_and_failed_attempts_are_rate_limited(self):
        broken = Mock()
        broken.save.side_effect = RuntimeError('save failed')
        self.assertFalse(self.capture.capture(broken, {'screen_type': 'unknown'}))
        self.now = 1
        self.assertFalse(self.capture.capture(broken, error=ValueError()))
        self.assertEqual(broken.save.call_count, 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_directory_permission_and_json_write_errors_never_escape(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('denied')):
            self.assertFalse(self.capture.capture(self.frame, {'screen_type': 'unknown'}))
        self.now = 6
        with patch.object(Path, 'open', side_effect=OSError('disk full')):
            self.assertFalse(self.capture.capture(self.frame, {'screen_type': 'unknown'}))

    def test_monitor_hook_is_independently_guarded(self):
        monitor = Monitor.__new__(Monitor)
        monitor.failure_capture = Mock()
        monitor.failure_capture.capture.side_effect = RuntimeError('unexpected capture bug')
        original = observation(confidence=.6)
        before = copy.deepcopy(original)
        monitor._capture_ocr_failure(self.frame, original)
        self.assertEqual(original, before)
        monitor.failure_capture.capture.assert_called_once_with(self.frame, original, None)

    def test_fake_frame_can_be_replaced_by_real_generated_png(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is optional outside the OCR environment')
        image = Image.new('RGB', (2, 2), 'white')
        self.assertTrue(self.capture.capture(image, {'screen_type': 'unknown'}))
        with Image.open(io.BytesIO(next(self.directory.glob('*.png')).read_bytes())) as saved:
            self.assertEqual(saved.size, (2, 2))

    def run_worker(self, *, recognition_error=None, capture_error=None):
        from PIL import Image

        image = Image.new('RGB', (16, 16), 'white')
        window = {'hwnd': 1, 'pid': 2, 'title': 'Dance Online', 'minimized': False}
        windows = Mock()
        windows.info.return_value = window
        capture = Mock(closed=False, error=None, copy_ms=.1)
        capture.take.return_value = (1, image)
        monitor = Monitor(self.directory / 'test.sqlite3', self.directory / 'monitor',
                          windows_factory=lambda: windows, capture_factory=lambda *_: capture,
                          engine_factory=lambda: object())
        monitor.tracker = Mock(count=0, state='UNKNOWN', session=None, last_session=None)
        monitor.tracker.consume.side_effect = lambda *_, **__: monitor.stop_event.set()
        monitor.config = {
            'window_hwnd': 1,
            'window_pid': 2,
            'capture_hz': 2,
            'ocr_interval': .5,
            'confirmations': 2,
            'player_name': 'TC',
            'source_mode': 'auto',
        }
        monitor.metrics = {'captured_frames': 0, 'analyzed_frames': 0, 'skipped_frames': 0}
        monitor.running = True
        monitor.failure_capture = Mock()
        monitor.failure_capture.capture.side_effect = capture_error
        observed = {
            'state': 'UNKNOWN',
            'screen_type': 'unknown',
            'frame_hash': 'fake-frame',
            'roi_mode': False,
        }
        analyzer = Mock(calls=1)
        analyzer.analyze.return_value = observed
        analyzer.analyze.side_effect = recognition_error
        with patch('dance_results.monitor.ReadOnlyCatalog'), \
                patch('dance_results.monitor.Analyzer', return_value=analyzer), \
                patch.dict(sys.modules, {'psutil': None}):
            monitor._run()
        return monitor, observed, capture

    def test_worker_consumes_observation_even_if_failure_capture_raises(self):
        monitor, observed, capture = self.run_worker(capture_error=OSError('disk full'))
        monitor.tracker.consume.assert_called_once()
        self.assertIs(monitor.tracker.consume.call_args.args[0], observed)
        self.assertEqual(monitor.connection, 'stopped')
        self.assertIsNone(monitor.error)
        capture.stop.assert_called_once()

    def test_worker_captures_recognition_exception_and_keeps_existing_error_path(self):
        error = RuntimeError('OCR failed')
        monitor, _, capture = self.run_worker(recognition_error=error, capture_error=OSError('disk full'))
        monitor.failure_capture.capture.assert_called_once()
        self.assertIs(monitor.failure_capture.capture.call_args.args[2], error)
        monitor.tracker.consume.assert_not_called()
        self.assertEqual(monitor.connection, 'error')
        self.assertEqual(monitor.error, 'RuntimeError: OCR failed')
        monitor.tracker.interrupt.assert_called_once_with('monitor_error')
        capture.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
