"""Bounded handoff/pack safety and review inbox behavior, on disposable data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from dance_results.library_service import LibraryError, LibraryService
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.result_review import ResultReviews
from dance_results.submissions import SubmissionService
from test_monitor import selected, scene
import test_result_review as review_fixture


class StickySelectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.t = Tracker(temp.name)
        self.pack = dict(root_id=1, pack_id=39, name="Zelda-Yeh's Simfiles", status='confirmed')
        self.t.pack_observation(self.pack, inferred=True, now=0)

    def send(self, observation, when):
        self.t.consume(copy.deepcopy(observation), now=when, sample_id=when)

    def choose(self, title='JOINT', when=1):
        self.send(selected(title), when)
        self.send(selected(title), when+1)

    def play(self, title='JOINT', when=20):
        for i in range(2):
            self.send(scene('PLAYING', playing_preview=dict(title=title, game_level=17)), when+i)

    def test_unknown_fade_keeps_recent_selection(self):
        self.choose()
        self.send(scene('UNKNOWN'), 5)
        self.send(scene('UNKNOWN'), 8)
        self.play()
        self.assertEqual(self.t.session['selection']['fields']['title']['value'], 'JOINT')

    def test_unknown_does_not_refresh_expiration(self):
        self.choose()
        self.send(scene('UNKNOWN'), 50)
        self.send(scene('UNKNOWN'), 55)
        self.play(when=100)
        self.assertIsNone(self.t.session['selection'])

    def test_same_room_title_with_host_chart_does_not_erase_local_chart(self):
        self.choose()
        for n in (5, 8):
            self.send(scene('WAITING_ROOM', room_preview=dict(title='JOINT', difficulty='NORMAL', game_level=3),
                            context=dict(local_is_host=True)), n)
        self.play()
        self.assertEqual(self.t.session['selection']['fields']['difficulty']['value'], 'HARD')

    def test_different_room_title_never_restores_old_song(self):
        self.choose()
        for n in (5, 8):
            self.send(scene('WAITING_ROOM', room_preview=dict(title='Other'), context=dict(local_is_host=True)), n)
        self.play()
        self.assertIsNone(self.t.session['selection'])

    def test_one_different_room_preview_invalidates_before_state_debounce(self):
        self.choose()
        self.send(scene('WAITING_ROOM', room_preview=dict(title='Other')), 5)
        self.play()
        self.assertIsNone(self.t.session['selection'])

    def test_single_selection_host_room_title_corroborates_longer_start(self):
        self.send(selected(), 1)
        for n in (5, 8):
            self.send(scene('WAITING_ROOM', room_preview=dict(title='JOINT'), context=dict(local_is_host=True)), n)
        self.play(when=30)
        selection = self.t.session['selection']
        self.assertEqual(selection['handoff'], 'single_observation')
        self.assertEqual(selection['last_seen'], 1)
        self.assertTrue(selection['room_title_corroborated'])

    def test_room_without_title_cannot_extend_single_selection_grace(self):
        self.send(selected(), 1)
        for n in (5, 8):
            self.send(scene('WAITING_ROOM', room_preview={}, context=dict(local_is_host=True)), n)
        self.play(when=30)
        self.assertIsNone(self.t.session['selection'])

    def test_readable_different_selection_blocks_prior_song(self):
        self.choose()
        self.send(selected('Other'), 8)
        self.play()
        self.assertIsNone(self.t.session['selection'])

    def test_long_gap_after_other_selection_cannot_resurrect_prior_song(self):
        self.choose()
        self.choose('Other', when=5)
        self.play(when=120)
        self.assertIsNone(self.t.session['selection'])

    def test_pack_change_invalidates_old_chart_before_confirmation(self):
        self.choose()
        other = dict(self.pack, pack_id=37, name='Another')
        self.t.pack_observation(other, now=8)
        self.play()
        self.assertIsNone(self.t.session['selection'])

    def test_pack_blank_preserves_last_confirmed_without_refresh(self):
        self.choose()
        for n in (4, 10, 35):
            self.t.pack_observation(None, now=n)
        self.assertEqual(self.t.current_pack['pack_id'], 39)
        self.assertEqual(self.t.pack_confirmed_at, 0)
        self.play()
        self.assertEqual(self.t.session['selection']['pack']['pack_id'], 39)

    def test_pack_blank_grace_expires(self):
        self.choose()
        self.t.pack_observation(None, now=61)
        self.assertIsNone(self.t.current_pack)
        self.assertIsNone(self.t.selection)

    def test_genuine_pack_ambiguity_clears_immediately(self):
        self.choose()
        self.t.pack_observation(None, uncertain=True, now=4)
        self.assertIsNone(self.t.current_pack)
        self.assertIsNone(self.t.selection)

    def test_blank_cannot_resurrect_after_ambiguity(self):
        self.t.pack_observation(None, uncertain=True, now=4)
        self.t.pack_observation(None, now=5)
        self.assertIsNone(self.t.current_pack)

    def test_new_confirmed_pack_replaces_old_pack(self):
        other = dict(self.pack, pack_id=37, name='Another')
        self.t.pack_observation(other, inferred=True, now=4)
        self.choose()
        self.play()
        self.assertEqual(self.t.session['selection']['pack']['pack_id'], 37)

    def test_different_playing_title_rejects_even_after_unknown(self):
        self.choose()
        self.send(scene('UNKNOWN'), 5)
        self.send(scene('UNKNOWN'), 8)
        self.play(title='Other')
        self.assertIsNone(self.t.session['selection'])

    def test_window_change_discards_selection_and_pack(self):
        self.choose()
        self.t.new_window(dict(hwnd=99))
        self.play()
        self.assertIsNone(self.t.session['selection'])
        self.assertIsNone(self.t.current_pack)

    def test_selection_consumed_once_after_fade(self):
        self.choose()
        self.play()
        self.send(scene('RESULT'), 30)
        self.send(scene('RESULT'), 31)
        self.play(when=40)
        self.assertIsNone(self.t.session['selection'])


class ReviewInboxTests(unittest.TestCase):
    setUp = review_fixture.ReviewPersistenceTests.setUp
    tearDown = review_fixture.ReviewPersistenceTests.tearDown
    body = review_fixture.ReviewPersistenceTests.body
    assert_database_unchanged = review_fixture.ReviewPersistenceTests.assert_database_unchanged

    def test_one_pending_card_per_session_has_values_pack_and_plain_reason(self):
        rows = self.service.list()['results']
        self.assertEqual([r['session_id'] for r in rows], [self.sid])
        self.assertEqual(rows[0]['values']['perfect'], 10)
        self.assertEqual(rows[0]['pack_name'], 'Pack A')
        self.assertTrue(rows[0]['review_reasons'])

    def test_confirmed_manual_hidden_but_legacy_detail_still_loads(self):
        self.service.save(self.body())
        self.assertEqual(self.service.list()['results'], [])
        self.assertEqual(self.service.detail(self.sid)['status'], 'confirmed_manual')

    def test_saved_auto_status_hidden_without_receipt(self):
        folder, row = self.service._read(self.sid)
        row['status'] = 'saved_verified_auto'
        atomic_json(folder/'result-review.json', row)
        self.assertEqual(self.service.list()['results'], [])
        self.assert_database_unchanged()

    def test_draft_stays_pending_and_keeps_corrected_values(self):
        body = self.body(confirm=False)
        body['values'].update(perfect=9, cool=2)
        self.service.save(body)
        self.assertEqual(self.service.list()['results'][0]['values']['perfect'], 9)
        self.assertEqual(self.service.detail(self.sid)['ocr']['fields']['perfect']['value'], 10)

    def test_discard_hides_and_retains_screenshot_and_ocr(self):
        folder, old = self.service._read(self.sid)
        image = (folder/'result.png').read_bytes()
        result = self.service.discard(self.body())
        self.assertEqual(result['status'], 'discarded')
        self.assertEqual(self.service.list()['results'], [])
        self.assertEqual((folder/'result.png').read_bytes(), image)
        self.assertEqual(self.service.detail(self.sid)['ocr'], old['ocr'])
        self.assert_database_unchanged()

    def test_discard_stale_revision_rejected(self):
        with self.assertRaises(LibraryError):
            self.service.discard(self.body(revision=99))
        self.assertEqual(len(self.service.list()['results']), 1)

    def test_discarded_review_cannot_be_saved_or_submitted(self):
        self.service.discard(self.body())
        with self.assertRaises(LibraryError):
            self.service.save(self.body(revision=1))
        store = SubmissionService(LibraryService(self.db), self.service)
        p = store.prepare(self.body(revision=1, play_mode='single'))
        self.assertFalse(p['can_submit'])
        self.assertIn('review_discarded', [i['code'] for i in p['issues']])

    def test_discard_is_idempotent(self):
        self.service.discard(self.body())
        self.service.discard(self.body())
        self.assertEqual(self.service.detail(self.sid)['revision'], 1)

    def test_unprocessed_result_can_be_discarded_without_ocr(self):
        folder, _ = self.service._read(self.sid)
        (folder/'result-review.json').unlink()
        self.service.discard(dict(session_id=self.sid))
        self.assertEqual(self.service.list()['results'], [])
        self.assertTrue((folder/'result.png').exists())

    def test_search_retains_same_title_candidates_with_pack(self):
        result = self.service.candidates('JOINT')
        self.assertEqual(result['count'], 2)
        self.assertTrue(all(c['pack_name'] == 'Pack A' for c in result['candidates']))

    def test_listing_does_not_rewrite_source_files(self):
        paths = list((self.output/'sessions'/self.sid).glob('*.*'))
        before = {p: p.read_bytes() for p in paths}
        self.service.list()
        self.assertEqual(before, {p: p.read_bytes() for p in paths})


if __name__ == '__main__':
    unittest.main()
