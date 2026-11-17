"""Room-only guest identity, sticky cues, folder provenance and host flicker."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw

from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.room_identity import bind_guest_official, field
from dance_results.room_sampler import RoomSampler
from dance_results.source_choice import offer
from dance_results.download_tracking import verified_download_binding, metadata_snapshot, download_view
from dance_results.verified_auto import automatic_request
from test_monitor import selected, scene
from test_round33 import catalog as old_catalog
from test_round34 import result
import test_result_review as review_fixture
import test_phase5c as auto_fixture
from test_phase5c import attach_auto_frames


FIXTURE = Path(__file__).parent/'fixtures/round35/room-nomap-anon.png'


def room(title='this time', difficulty='HARD', level=99):
    return dict(title=title, difficulty=difficulty, game_level=level,
                fields=dict(difficulty=field(difficulty, 'room_local_difficulty_setting', .99)))


def guest(title='this time', custom=False):
    start = '2026-11-16T15:59:22+00:00'
    preview = room(title)
    preview.update(custom=custom, room_key=['thistime', 'HARD'], room_entry_at='room-entry',
                   source_evidence=[dict(cue='host_blue_upload_strip')] if custom else [])
    return dict(session_id='guest', epoch=1, selection=None, started_at=start,
        playing_preview=dict(title=title), identity_context=dict(epoch=1, captured_at=start,
            source_type='custom' if custom else 'unknown', guest_room=preview))


def catalog():
    c = old_catalog()
    c.songs = {1: dict(song_id=1, source_type='official', pack_id=None, root_id=2,
        saved_title='this time', latest_title='this time', folder_name='this time', pack_name=None,
        relative_folder='this time')}
    c.index = {'this time': {1}}
    c.difficulties = {(1, 'HARD'): dict(difficulty_id=11, difficulty='HARD', game_level=10,
        total_notes=812, notes_verified=1, notes_source='official_csv', level_source='official_csv')}
    c.official_sources = {1: dict(mode='K')}
    return c


def score(title='this time', notes=812):
    r = result(title, 99)
    r['multiplayer'] = True
    r['fields'].update({k: field(v, 'result_screen', .99) for k, v in
        dict(perfect=notes, cool=0, bad=0, miss=0, completion_status='COMPLETED').items()})
    r['temporal_validation']['samples'] = [dict(fields=copy.deepcopy(r['fields'])) for _ in range(2)]
    return r


class GuestIdentityTests(unittest.TestCase):
    def test_footer_label_confidence_is_location_not_title_confidence(self):
        from dance_results.result_ocr import parse_result
        tokens = review_fixture.result_tokens()
        next(t for t in tokens if t['text'] == '歌曲名：')['confidence'] = .9375
        parsed = parse_result(tokens, 'TC')['fields']['title']
        self.assertEqual(parsed['confidence'], .99)
        self.assertEqual(parsed['anchor_evidence'][0]['confidence'], .9375)
        next(t for t in tokens if t['text'] == 'JOINT')['confidence'] = .8
        self.assertEqual(parse_result(tokens, 'TC')['fields']['title']['confidence'], .8)

    def test_unique_official_uses_notes_not_room_or_footer_level(self):
        b = bind_guest_official(catalog(), guest(), score())
        self.assertEqual(b['result_title_binding']['song_id'], 1)
        self.assertEqual(b['fields']['game_level']['value'], 10)
        self.assertEqual(b['result_title_binding']['method'], 'guest_room_title_official_unique')

    def test_shared_custom_title_always_picker_even_chartless(self):
        c = catalog()
        c.songs[2] = dict(c.songs[1], song_id=2, source_type='custom')
        c.index['this time'].add(2)
        self.assertIsNone(bind_guest_official(c, guest(), score()))
        prompt = offer(c, dict(status='pending_review', revision=0, locked_session=guest(), ocr=score(),
                               session_id='guest', review_id='review'))
        self.assertEqual(prompt['kind'], 'source')

    def test_duplicate_official_chart_requires_picker(self):
        c = catalog()
        c.songs[2] = dict(c.songs[1], song_id=2)
        c.index['this time'].add(2)
        c.difficulties[(2, 'HARD')] = dict(c.difficulties[(1, 'HARD')], difficulty_id=12)
        c.official_sources[2] = dict(mode='K')
        self.assertIsNone(bind_guest_official(c, guest(), score()))

    def test_custom_flag_notes_mismatch_failed_and_missing_notes_picker(self):
        for kind in ('flag', 'notes', 'failed', 'missing', 'confidence', 'temporal', 'title', 'difficulty'):
            with self.subTest(kind=kind):
                s, r = guest(custom=kind == 'flag'), score()
                if kind == 'notes':
                    r['fields']['perfect']['value'] = 811
                elif kind == 'failed':
                    r['fields']['completion_status']['value'] = 'FAILED'
                elif kind == 'missing':
                    r['fields']['perfect']['value'] = None
                elif kind == 'confidence':
                    s['identity_context']['guest_room']['fields']['difficulty']['confidence'] = .9
                elif kind == 'temporal':
                    r['temporal_validation']['status'] = 'pending_review'
                elif kind == 'title':
                    s['playing_preview']['title'] = 'Different Song'
                elif kind == 'difficulty':
                    s['identity_context']['guest_room']['fields']['difficulty']['value'] = 'EASY'
                self.assertIsNone(bind_guest_official(catalog(), s, r))
                prompt = offer(catalog(), dict(status='pending_review', revision=0,
                    locked_session=s, ocr=r, session_id='guest', review_id='review'))
                self.assertEqual(prompt['kind'], 'source')

    def test_multiple_custom_folders_offer_folder_picker(self):
        c = catalog()
        c.songs[1]['source_type'] = 'custom'
        c.songs[2] = dict(c.songs[1], song_id=2)
        c.index['this time'].add(2)
        c.difficulties = {}
        p = offer(c, dict(status='pending_review', revision=0, locked_session=guest(custom=True),
            ocr=score(), session_id='guest', review_id='review'))
        self.assertEqual(p['kind'], 'folder')

    def test_scope_requires_current_epoch_and_session_start(self):
        for key in ('epoch', 'captured_at'):
            s = guest()
            s['identity_context'][key] = 'other'
            self.assertIsNone(bind_guest_official(catalog(), s, score()))


class TrackingTests(unittest.TestCase):
    def test_positive_custom_cue_revokes_stale_official_host_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            s = selected('this time')
            s['source_type'] = 'official'
            for i in (1, 2):
                t.consume(s, now=i, sample_id=i)
            for i in (3, 4):
                t.consume(scene('WAITING_ROOM', room_preview=room(), context=dict(local_is_host=True)),
                          now=i, sample_id=i)
            t.custom_cue('host_blue_upload_strip', 1)
            for i in (5, 6):
                t.consume(scene('PLAYING', playing_preview=dict(title='this time')), now=i, sample_id=i)
            self.assertIsNone(t.session['selection'])
            self.assertEqual(t.session['identity_context']['source_type'], 'custom')

    def test_flag_key_clear_and_playing_consumption(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.consume(scene('WAITING_ROOM', room_preview=room()), sample_id=1)
            t.custom_cue('host_blue_upload_strip', 1, 2)
            t.consume(scene('WAITING_ROOM', room_preview=room()), sample_id=2)
            for i in (3, 4):
                t.consume(scene('PLAYING', playing_preview=dict(title='this time')), sample_id=i)
            self.assertEqual(t.session['identity_context']['source_type'], 'custom')
            self.assertTrue(t.session['identity_context']['guest_room']['custom'])
            t.observe_room(dict(room_preview=room('Other')))
            self.assertFalse(t.room_custom)
            t.custom_cue('self_no_map', 2, 1)
            t.observe_room(dict(room_preview=room('Other', 'EASY')))
            self.assertFalse(t.room_custom)
            t.custom_cue('self_no_map', 2, 1)
            t.new_window('second')
            self.assertFalse(t.room_custom)

    def test_leaving_room_clears_even_before_debounce(self):
        for state in ('LOBBY', 'LOGIN', 'SONG_SELECT', 'PROFILE'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temp:
                t = Tracker(temp)
                t.observe_room(dict(room_preview=room()))
                t.custom_cue('self_no_map')
                obs = selected() if state == 'SONG_SELECT' else scene(state)
                t.consume(obs, sample_id=1)
                self.assertFalse(t.room_custom)

    def test_unreadable_difficulty_does_not_clear_positive_cue(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            t.custom_cue('host_blue_upload_strip', 1)
            t.observe_room(dict(room_preview=room(difficulty=None)))
            self.assertTrue(t.room_custom)
            t.observe_room(dict(room_preview=room()))
            self.assertTrue(t.room_custom)

    def test_host_none_and_single_false_survive_two_real_false_revoke(self):
        for roles, survives in [([True, None], True), ([True, False], True),
                               ([True, False, None, False], True), ([True, False, False], False)]:
            with self.subTest(roles=roles), tempfile.TemporaryDirectory() as temp:
                t = Tracker(temp)
                for i in (1, 2):
                    t.consume(selected('this time'), now=i, sample_id=i)
                for i, role in enumerate(roles, 3):
                    t.consume(scene('WAITING_ROOM', room_preview=room(), context=dict(local_is_host=role)),
                              now=i, sample_id=i)
                self.assertEqual(t.selection is not None, survives)


class SamplerTests(unittest.TestCase):
    def test_anonymized_badge_with_real_line_ocr(self):
        from dance_results.ocr_engine import LocalOCREngine
        models = Path(__file__).resolve().parents[1]/'.ocr-models'
        if not models.is_dir():
            self.skipTest('Local OCR models unavailable')
        engine = LocalOCREngine(models, low_latency=True)
        with Image.open(FIXTURE) as image:
            cues, _ = self.run_sampler(image, engine)
        self.assertEqual({c['cue'] for c in cues}, {'host_blue_upload_strip', 'self_no_map'})

    def run_sampler(self, image, engine=None, count=2):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room('Genjitsu Escape')))
            sampler = RoomSampler()
            sampler.key = (t.epoch, t.room_key)
            sampler.slot, sampler.host_slot = 2, 1
            costs = [sampler.sample(image, t, engine, i) for i in range(count)]
            return t.room_custom, costs

    def test_fixture_blue_host_and_no_map_crop_once(self):
        engine = Mock()
        engine.recognize_line.return_value = [dict(text='NO MAP', confidence=.99)]
        with Image.open(FIXTURE) as image:
            cues, costs = self.run_sampler(image, engine)
        self.assertEqual({c['cue'] for c in cues}, {'host_blue_upload_strip', 'self_no_map'})
        self.assertEqual(engine.recognize_line.call_count, 1)
        self.assertTrue(all(c >= 0 for c in costs))

    def test_single_frame_never_sets_flag(self):
        with Image.open(FIXTURE) as image:
            self.assertFalse(self.run_sampler(image, count=1)[0])

    def test_shifted_band_and_blue_elsewhere_are_negative(self):
        with Image.open(FIXTURE) as source:
            image = source.copy()
        ImageDraw.Draw(image).rectangle((218, 170, 370, 184), fill='black')
        ImageDraw.Draw(image).rectangle((223, 165, 324, 169), fill=(107,167,255))
        ImageDraw.Draw(image).rectangle((400, 300, 700, 350), fill=(107,167,255))
        self.assertFalse(self.run_sampler(image)[0])

    def test_green_strip_on_local_card(self):
        image = Image.new('RGB', (1024, 800), 'black')
        ImageDraw.Draw(image).rectangle((380, 175, 450, 179), fill=(40,180,80))
        self.assertEqual(self.run_sampler(image)[0][0]['cue'], 'self_green_strip')


class AutoAndDownloadTests(unittest.TestCase):
    setUp = review_fixture.ReviewPersistenceTests.setUp
    tearDown = review_fixture.ReviewPersistenceTests.tearDown
    official_auto = auto_fixture.Phase5CTests.official_auto

    def prepare_guest(self, custom=False):
        row = self.official_auto(title='this time', notes=11)
        context = guest(custom=custom)['identity_context']
        context['captured_at'] = self.session['started_at']
        self.session.update(selection=None, song_id=None, epoch=1, identity_context=context,
                            playing_preview=dict(title='this time'), pending_reason='no_fresh_selection')
        row['locked_session'] = copy.deepcopy(self.session)
        attach_auto_frames(self, row)
        return row

    def test_guest_official_auto_request_and_submit(self):
        from dance_results.submissions import SubmissionService
        from dance_results.library_service import LibraryService
        row = self.prepare_guest()
        body, issues = automatic_request(self.service, row)
        self.assertEqual(issues, [])
        self.assertEqual(body['identity_binding_method'], 'guest_room_title_official_unique')
        store = SubmissionService(LibraryService(self.db), self.service)
        store.auto_allowed = lambda sid: True
        self.assertEqual(store.submit_verified(self.sid)['record']['confirmation_method'], 'verified_auto')

    def test_custom_flag_blocks_official_auto(self):
        row = self.prepare_guest(custom=True)
        self.assertIn('automatic_guest_identity_requires_picker', automatic_request(self.service, row)[1])

    def download(self):
        row = self.prepare_guest(custom=True)
        # Small synthetic APT. folder: same shape as recorded song 4582, with
        # disposable local file metadata instead of the user's filesystem.
        source = self.base/'SONG/Pack A/Folder A/song.sm'
        source.write_text('#TITLE:APT.;', encoding='utf8')
        from dance_results.database import LibraryDatabase
        from dance_results.scanner import scan_library
        with LibraryDatabase(self.db) as db:
            db.sync(scan_library(self.base/'SONG'))
        self.session['identity_context']['guest_room'].update(title='APT.', room_key=['apt.', 'HARD'])
        self.session['playing_preview']['title'] = 'APT.'
        self.session['playing_preview']['fields'] = dict(game_level=field(17, 'local_playing_banner_level', .99))
        row['ocr']['fields']['title']['value'] = 'APT.'
        row['ocr']['multiplayer'] = True
        for r in row['ocr']['player_rows']:
            if 'title' in r['fields']:
                r['fields']['title']['value'] = 'APT.'
        attach_auto_frames(self, row)
        candidate = dict(song_id=1, title='APT.', root_id=1, relative_path='Pack A\\Folder A',
            stable_source=True, is_new_song=True, observed_after_snapshot=True,
            stable_metadata=metadata_snapshot(source.parent)['files'])
        evidence = dict(episode_id='synthetic-apt', session_id=self.sid, epoch=1,
            candidate_scope='observed_new_song_ids_v2', scan_completed=True, status='pending_identity',
            room_key=['apt.', 'HARD'], room_entry_at='room-entry', candidates=[candidate], events=[], result_seen=True)
        atomic_json(self.output/'sessions'/self.sid/'download-evidence.json', evidence)
        return row, evidence

    def test_guest_download_fc_auto_and_non_fc_pending_prefill(self):
        row, evidence = self.download()
        binding = verified_download_binding(self.service, row)
        self.assertEqual(binding['song_id'], 1)
        body, blockers = automatic_request(self.service, row)
        self.assertEqual(blockers, [])
        self.assertEqual(body['song_id'], 1)
        for f in [row['ocr']['fields'], *[r['fields'] for r in row['ocr']['player_rows']]]:
            f['perfect']['value'], f['bad']['value'], f['max_combo']['value'] = 9, 1, 10
        attach_auto_frames(self, row)
        self.assertIn('automatic_uncharted_complete_combo_required', automatic_request(self.service, row)[1])
        self.assertEqual(self.service.detail(self.sid)['draft']['song_id'], 1)

    def test_download_fc_submits_with_independent_local_playing_level(self):
        from dance_results.submissions import SubmissionService
        from dance_results.library_service import LibraryService
        row, _ = self.download()
        store = SubmissionService(LibraryService(self.db), self.service)
        store.auto_allowed = lambda sid: True
        record = store.submit_verified(self.sid)['record']
        self.assertEqual(record['song_id'], 1)
        self.assertEqual(record['game_level'], 17)

    def test_download_without_local_level_stays_prefilled_pending(self):
        row, _ = self.download()
        row['locked_session']['playing_preview'].pop('fields')
        binding = verified_download_binding(self.service, row)
        self.assertEqual(binding['song_id'], 1)
        self.assertIn('automatic_selection_game_level_required', automatic_request(self.service, row)[1])

    def test_metadata_worker_marks_room_custom_without_no_map(self):
        from dance_results.download_tracking import DownloadTracker
        from dance_results.library_service import LibraryService
        tracker = Tracker(self.output/'room-tracker')
        tracker.state = 'WAITING_ROOM'
        tracker.observe_room(dict(room_preview=room('APT.', difficulty=None)))
        downloads = DownloadTracker(LibraryService(self.db), self.service, interval=0, quiet_seconds=0)
        try:
            downloads.room_tick(tracker)
            downloads.queue.join()
            # Difficulty/field confidence can become readable after entry.
            # Keep the original baseline while refreshing the room key.
            tracker.observe_room(dict(room_preview=room('APT.')))
            folder = self.base/'SONG/New Pack/APT'
            folder.mkdir(parents=True)
            (folder/'song.sm').write_text('#TITLE:APT.;', encoding='utf8')
            downloads.room_tick(tracker)
            downloads.queue.join()
            downloads.room_tick(tracker)
            downloads.queue.join()
            self.assertTrue(tracker.room_custom)
            self.assertEqual(tracker.room_custom[-1]['cue'], 'download_folder_changed')
            self.assertEqual(downloads.active['room_entry_at'], tracker.room_entry_at)
        finally:
            downloads.close()

    def test_pending_scan_then_completed_prefills_without_editing_revision(self):
        row, evidence = self.download()
        row['draft']['song_id'] = None
        atomic_json(self.output/'sessions'/self.sid/'result-review.json', row)
        evidence.update(scan_completed=False, status='scanning')
        path = self.output/'sessions'/self.sid/'download-evidence.json'
        atomic_json(path, evidence)
        self.assertIsNone(verified_download_binding(self.service, row))
        self.assertTrue(download_view(evidence, row)['scanning'])
        evidence.update(scan_completed=True, status='pending_identity')
        atomic_json(path, evidence)
        detail = self.service.detail(self.sid)
        self.assertEqual((detail['draft']['song_id'], detail['revision']), (1, 0))

    def test_two_folders_mismatch_old_room_or_changed_files_never_bind(self):
        row, evidence = self.download()
        path = self.output/'sessions'/self.sid/'download-evidence.json'
        for case in ('two', 'title', 'result_title', 'playing_title', 'room', 'epoch', 'unstable', 'old', 'files'):
            e = copy.deepcopy(evidence)
            r = copy.deepcopy(row)
            if case == 'two':
                e['candidates'].append(dict(e['candidates'][0], song_id=2))
            elif case == 'title':
                r['locked_session']['identity_context']['guest_room']['title'] = 'Different'
            elif case == 'result_title':
                r['ocr']['fields']['title']['value'] = 'Different'
            elif case == 'playing_title':
                r['locked_session']['playing_preview']['title'] = 'APT!'
            elif case == 'room':
                e['room_entry_at'] = 'old'
            elif case == 'epoch':
                e['epoch'] = 5
            elif case == 'unstable':
                e['candidates'][0]['stable_source'] = False
            elif case == 'old':
                e['candidates'][0]['is_new_song'] = False
            else:
                e['candidates'][0]['stable_metadata'] = {}
            atomic_json(path, e)
            with self.subTest(case=case):
                self.assertIsNone(verified_download_binding(self.service, r))
