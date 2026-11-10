"""Round 2.6: retain complete results and constrain inferred identities."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from dance_results.monitor import Monitor
from dance_results.monitor_ocr import apply_selection, image_hash, black_or_loading_frame
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_failures import failure_details
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_temporal import ResultConsistency, issues
from dance_results.result_ocr import parse_result, recognize_result
from dance_results.result_review import ResultReviews
from test_monitor import selected, scene
from test_result_review import result_tokens, token


def catalog():
    c = ReadOnlyCatalog.__new__(ReadOnlyCatalog)
    c.packs = [dict(pack_id=i, root_id=1, name='Pack '+str(i)) for i in (1, 2)]
    c.songs = {}
    for sid, pid, title in ((10, 1, 'JOINT'), (11, 1, 'Other'), (20, 2, 'JOINT')):
        c.songs[sid] = dict(song_id=sid, pack_id=pid, root_id=1, source_type='custom',
                           pack_name='Pack '+str(pid), folder_name=title, relative_folder=title,
                           saved_title=title, latest_title=title)
    c.index = {'joint': {10, 20}, 'other': {11}}
    c.reviews = set()
    c.difficulties = {}
    c.official_sources = {}
    return c


class IdentityTests(unittest.TestCase):
    def test_ambiguous_across_packs_and_non_disambiguating_pack_do_not_bind(self):
        c = catalog()
        for pack in (None, 99):
            self.assertIsNone(c.match_identity(selected()['fields'], pack)['bound_song_id'])
        c.songs[20]['pack_id'] = 1
        self.assertIsNone(c.match_identity(selected()['fields'], 1)['bound_song_id'])

    def test_unique_title_binds_without_fabricating_verified_chart(self):
        c = catalog()
        c.index['joint'] = {10}
        result = c.match_identity(selected()['fields'])
        self.assertEqual(result['confirmed_song_id'], 10)
        self.assertEqual(result['status'], 'unique_consistent_candidate')
        self.assertIsNone(result['bound_difficulty_id'])

    def test_inferred_pack_can_disambiguate_but_cannot_contradict_unique_song(self):
        c = catalog()
        self.assertEqual(c.match_identity(selected()['fields'], 1)['confirmed_song_id'], 10)
        c.index['joint'] = {10}
        self.assertIsNone(c.match_identity(selected()['fields'], 2)['bound_song_id'])

    def test_known_level_notes_and_duration_conflicts_block_identity(self):
        c = catalog()
        c.index['joint'] = {10}
        c.difficulties[(10, 'HARD')] = dict(
            difficulty_id=100, game_level=17, total_notes=821, duration_seconds=96,
            level_source='manual', notes_source='manual', notes_verified=1, duration_verified=1,
        )
        for key, wrong in (('game_level', 18), ('total_notes', 822), ('duration_seconds', 97)):
            fields = selected()['fields']
            fields[key]['value'] = wrong
            self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'], key)

    def test_low_confidence_core_identity_never_binds(self):
        c = catalog()
        for key in ('title', 'difficulty', 'game_level'):
            fields = selected()['fields']
            fields[key]['confidence'] = .949
            self.assertIsNone(c.match_identity(fields, 1)['bound_song_id'])

    def test_unreliable_optional_conflict_cannot_eliminate_a_namesake(self):
        c = catalog()
        for sid, notes in ((10, 821), (20, 1000)):
            c.difficulties[(sid, 'HARD')] = dict(
                difficulty_id=sid, game_level=17, total_notes=notes,
                level_source='manual', notes_source='manual', notes_verified=1,
            )
        fields = selected()['fields']
        fields['total_notes'].update(value=1000, confidence=.94)
        self.assertIsNone(c.match_identity(fields)['bound_song_id'])

    def test_official_identity_needs_no_pack(self):
        c = catalog()
        c.index['joint'] = {10}
        c.songs[10].update(pack_id=None, pack_name=None, source_type='official')
        result = c.match_identity(selected()['fields'])
        self.assertEqual(result['confirmed_song_id'], 10)
        self.assertNotIn('pack_not_confirmed', result['reasons'])

    def test_successful_inference_is_on_selection_consumed_at_play_start(self):
        c = catalog()
        c.index['joint'] = {10}
        with tempfile.TemporaryDirectory() as directory:
            tracker = Tracker(directory)
            image = Image.new('RGB', (1024, 800))
            for i in range(1, 4):
                selection = dict(fields=selected()['fields'], pack_list_visible=False,
                                 visible_packs=[], song_title_tokens=[token('JOINT', 500, 250),
                                                                      token('Other', 500, 280)])
                observed = apply_selection(dict(state='SONG_SELECT', frame_hash=str(i), selection=selection),
                                           image, tracker, c)
                tracker.consume(observed, now=i, sample_id=i)
            for i in (4, 5):
                tracker.consume(scene('PLAYING'), now=i, sample_id=i)
            locked = tracker.session['selection']
            self.assertEqual(locked['pack']['pack_id'], 1)
            self.assertEqual(locked['pack']['source'], 'right_song_list_unique_pack')
            self.assertEqual(tracker.session['song_id'], 10)

    def test_inferred_pack_cannot_force_contradicting_or_ambiguous_song(self):
        for contradiction in (True, False):
            c = catalog()
            if contradiction:
                c.index['joint'] = {10}
                c.index['right'] = {20}
                c.index['last'] = {21}
                c.songs[21] = dict(c.songs[20], song_id=21)
                titles = ('right', 'last')
            else:
                c.songs[20]['pack_id'] = 1
                titles = ('JOINT', 'Other')
            with tempfile.TemporaryDirectory() as directory:
                tracker = Tracker(directory)
                image = Image.new('RGB', (1024, 800))
                for i in (1, 2):
                    selection = dict(fields=selected()['fields'], pack_list_visible=False,
                                     song_title_tokens=[token(t, 500, 250+j*30) for j, t in enumerate(titles)])
                    observed = apply_selection(dict(state='SONG_SELECT', frame_hash=str(i), selection=selection),
                                               image, tracker, c)
                    tracker.consume(observed, now=i, sample_id=i)
                for i in (3, 4):
                    tracker.consume(scene('PLAYING'), now=i, sample_id=i)
                self.assertIsNone(tracker.session['song_id'])
                self.assertEqual(tracker.session['matching_status'], 'pending_manual_review')


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.monitor = Monitor(Path(self.temp.name)/'unused.sqlite3', Path(self.temp.name)/'monitor')
        self.monitor.tracker = Tracker(self.monitor.directory)
        self.consistency = ResultConsistency()
        self.counter = 0
        self.send(scene('PLAYING'))
        self.send(scene('PLAYING'))

    def send(self, observation, color='gray'):
        self.counter += 1
        image = Image.new('RGB', (1024, 800), color)
        if observation['state'] == 'RESULT':
            observation['frame_hash'] = image_hash(image)
        self.consistency.observe(observation, (self.monitor.tracker.session or {}).get('session_id'),
                                 self.counter, image)
        self.monitor._consume_observation(observation, self.counter, image, self.counter, self.consistency)

    def result(self):
        return dict(state='RESULT', result_ocr=dict(parse_result(result_tokens()), parser_version='phase5f1-v1'))

    def test_one_complete_frame_then_leave_writes_best_result_review(self):
        self.send(self.result())
        incomplete = self.result()
        incomplete['result_ocr']['fields']['miss']['value'] = None
        self.send(incomplete, 'black')
        self.send(scene('UNKNOWN'))
        self.send(scene('UNKNOWN'))
        self.send(selected())
        self.send(selected())
        session = self.monitor.tracker.last_session
        self.assertEqual(session['status'], 'pending_result')
        folder = self.monitor.directory/'sessions'/session['session_id']
        row = json.loads((folder/'result-review.json').read_text(encoding='utf8'))
        self.assertEqual(row['status'], 'pending_review')
        self.assertEqual(row['ocr']['fields']['miss']['value'], 0)
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'pending_review')
        self.assertIn('automatic_temporal_confirmation_required', issues(row['ocr'], session, folder))
        with Image.open(folder/'result.png') as saved:
            self.assertEqual(saved.getpixel((0, 0)), (128, 128, 128))

    def test_no_complete_frame_keeps_old_interruption(self):
        observation = self.result()
        observation['result_ocr']['fields']['grade']['confidence'] = .94
        self.send(observation)
        self.assertIsNone(self.monitor.tracker.best_result)
        self.send(selected())
        self.send(selected())
        self.assertEqual(self.monitor.tracker.last_session['status'], 'interrupted')

    def test_incomplete_only_then_leave_is_interrupted_without_review(self):
        observation = self.result()
        observation['result_ocr']['fields']['miss']['value'] = None
        self.send(observation)
        self.send(selected())
        self.send(selected())
        session = self.monitor.tracker.last_session
        self.assertEqual(session['status'], 'interrupted')
        self.assertIsNone(session['result'])
        self.assertFalse((self.monitor.directory/'sessions'/session['session_id']/'result-review.json').exists())

    def test_window_loss_after_complete_frame_also_preserves_pending(self):
        self.send(self.result())
        self.monitor.tracker.interrupt('window_identity_changed')
        self.monitor._ingest_pending_result()
        self.assertEqual(self.monitor.tracker.last_session['status'], 'pending_result')

    def test_later_unreliable_complete_frame_does_not_replace_best_sample(self):
        self.send(self.result())
        low = self.result()
        low['result_ocr']['fields']['grade']['confidence'] = .94
        self.send(low, 'navy')
        session = self.monitor.tracker.last_session
        row = self.monitor.reviews._read(session['session_id'])[1]
        self.assertEqual(row['ocr']['fields']['grade']['confidence'], .99)
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'pending_review')

    def test_two_distinct_complete_identical_frames_still_confirm(self):
        self.send(self.result())
        second = self.result()
        second['result_ocr']['fields']['grade']['confidence'] = .96
        self.send(second, 'navy')
        session = self.monitor.tracker.last_session
        row = self.monitor.reviews._read(session['session_id'])[1]
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'confirmed')

    def test_best_complete_frame_survives_lower_confidence_and_state_bounce(self):
        self.send(self.result())
        self.send(scene('PLAYING'))
        lower = self.result()
        lower['result_ocr']['fields']['grade']['confidence'] = .96
        self.send(lower, 'navy')
        self.send(selected())
        self.send(selected())
        session = self.monitor.tracker.last_session
        row = self.monitor.reviews._read(session['session_id'])[1]
        self.assertEqual(row['ocr']['fields']['grade']['confidence'], .99)
        self.assertEqual(row['ocr']['temporal_validation']['status'], 'pending_review')


class SelectionAndFooterTests(unittest.TestCase):
    def test_black_and_near_black_fades_are_not_result_evidence(self):
        for shade in (0, 3, 8, 15):
            self.assertTrue(black_or_loading_frame(Image.new('RGB', (1024, 800), (shade,)*3)))
        self.assertFalse(black_or_loading_frame(Image.new('RGB', (1024, 800), 'gray')))

    def test_black_capture_preserves_selection_without_refreshing_ttl(self):
        with tempfile.TemporaryDirectory() as directory:
            tracker = Tracker(directory)
            tracker.consume(selected(), now=1, sample_id=1)
            tracker.consume(selected(), now=2, sample_id=2)
            original = copy.deepcopy(tracker.selection)
            tracker.invalidate_selection('black_capture')
            self.assertEqual(tracker.selection, original)
            for i in (3, 4):
                tracker.consume(scene('PLAYING'), now=i, sample_id=i)
            self.assertEqual(tracker.session['selection'], original)

    def test_other_invalidations_still_clear_selection(self):
        for reason in ('window_minimized', 'window_identity_changed', 'capture_stream_lost', 'capture_stale'):
            with tempfile.TemporaryDirectory() as directory:
                tracker = Tracker(directory)
                tracker.selection = selected()
                tracker.invalidate_selection(reason)
                self.assertIsNone(tracker.selection)

    def test_single_character_footer_accepts_letters_digits_and_cjk_at_gate(self):
        base = [t for t in result_tokens() if '歌曲名' not in t['text'] and t['text'] != 'JOINT']
        for title in ('A', '7', '舞'):
            for joined in (False, True):
                footer = [token('歌曲名：'+title, 100, 780, .95)] if joined else [
                    token('歌曲名：', 50, 780, .99), token(title, 110, 780, .95)]
                self.assertEqual(parse_result(base+footer)['fields']['title']['value'], title)

    def test_single_character_garbage_or_low_confidence_is_not_title(self):
        base = [t for t in result_tokens() if '歌曲名' not in t['text'] and t['text'] != 'JOINT']
        for text, confidence in (('A', .949), ('?', .99), ('+', .99)):
            for joined in (False, True):
                footer = [token('歌曲名：'+text, 100, 780, confidence)] if joined else [
                    token('歌曲名：', 50, 780), token(text, 110, 780, confidence)]
                self.assertIsNone(parse_result(base+footer)['fields']['title']['value'])

    def test_missing_detector_character_requires_label_bounded_reliable_reread(self):
        tokens = [t for t in result_tokens() if t['text'] != 'JOINT']
        image = Image.new('RGB', (1024, 800), 'gray')
        for text, confidence, expected in (('A', .95, 'A'), ('A', .949, None), ('?', .99, None)):
            def read(image, *, origin=(0, 0), scale=1):
                x, y = origin
                return [token('A+' if x >= 940 else text, x+image.width/scale/2,
                              y+image.height/scale/2, .99 if x >= 940 else confidence)]
            engine = SimpleNamespace(recognize=read, recognize_line=read)
            result = recognize_result(image, engine, tokens=tokens)
            self.assertEqual(result['fields']['title']['value'], expected)
            self.assertTrue(any(r.get('preprocessing') == 'footer_title_after_label'
                                for r in result['roi_rereads']))

    def test_session3_replay_skips_missing_snapshot(self):
        from tools.replay_realplay3 import replay

        with tempfile.TemporaryDirectory() as directory:
            report = replay(Path(directory)/'missing', Path(directory)/'missing.sqlite3')
            self.assertEqual(report['status'], 'skipped')

    def test_not_readable_transition_noise_is_suppressed_but_conflicts_survive(self):
        fields = {k: dict(value=None, confidence=0, reason='not_readable')
                  for k in ('title', 'game_level', 'difficulty')}
        observation = dict(screen_type='song_select', selection=dict(
            fields=fields, pack_list_visible=True, pack_reason='selected_pack_uncertain'))
        self.assertEqual(failure_details(observation)['reason'], [])
        fields['title']['reason'] = 'ambiguous'
        self.assertIn('title: ambiguous', failure_details(observation)['reason'])
        observation['screen_type'] = 'results'
        observation['result_ocr'] = dict(fields=fields)
        self.assertIn('difficulty: not_readable', failure_details(dict(
            screen_type='song_select', ocr_fields=dict(difficulty=dict(
                value='HARD', confidence=.7, reason='not_readable'))))['reason'])
        self.assertIn('title: missing', failure_details(observation)['reason'])


if __name__ == '__main__':
    unittest.main()
