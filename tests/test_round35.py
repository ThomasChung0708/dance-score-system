"""Room-only guest identity, sticky cues, folder provenance and host flicker."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.room_identity import bind_guest_official, field
from dance_results.room_sampler import RoomSampler
from dance_results.ocr_matching import same_song_title_key
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


def sampler_observation(size=(1027, 776), top=0):
    """Anonymized token control in Analyzer's 1024 x 800 frame space."""
    from test_ocr_static import token
    content_height = size[1]-top
    own = token('TC', 446.5, (203.5*content_height/800+top)*800/size[1])
    host = token('HOST', 291.5, (159*content_height/800+top)*800/size[1])
    return dict(state='WAITING_ROOM', capture_size=list(size), tokens=[own, host],
                download_lobby=dict(local_player_status='CONFIRMED', slot=2,
                                    player_name='TC', evidence=[own]))


def synthetic_nameplate(image):
    """Explicit anonymous card pixels for generated positive controls."""
    draw = ImageDraw.Draw(image)
    draw.rectangle((387, 187, 507, 207), fill=(100, 60, 160))
    draw.text((441, 192), 'TC', fill='white')
    return image


def room(title='this time', difficulty='HARD', level=99):
    return dict(title=title, difficulty=difficulty, game_level=level,
                fields=dict(difficulty=field(difficulty, 'room_local_difficulty_setting', .99)))


def guest(title='this time', custom=False):
    start = '2026-11-16T15:59:22+00:00'
    preview = room(title)
    preview.update(custom=custom, room_key=[same_song_title_key(title), 'HARD'], room_entry_at='room-entry',
                   coverage=dict(slot_confirmed=True, sampled_frames=4, minimum_frames=4,
                                 baseline_before_entry=True, baseline_at='2026-11-16T15:59:00+00:00'),
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

    def test_guest_official_requires_sampler_coverage(self):
        for change in (None, dict(slot_confirmed=False), dict(sampled_frames=3),
                       dict(sampled_frames=4, minimum_frames=5),
                       dict(baseline_before_entry=False), dict(baseline_at=None)):
            with self.subTest(change=change):
                s = guest()
                if change is None:
                    s['identity_context']['guest_room'].pop('coverage')
                else:
                    s['identity_context']['guest_room']['coverage'].update(change)
                self.assertIsNone(bind_guest_official(catalog(), s, score()))
                self.assertEqual(offer(catalog(), dict(status='pending_review', revision=0,
                    locked_session=s, ocr=score(), session_id='guest', review_id='review'))['kind'], 'source')
        self.assertIsNotNone(bind_guest_official(catalog(), guest(), score()))

    def test_guest_requires_multiplayer_result(self):
        from dance_results.room_identity import agreeing_room
        for value in (None, False, 1, 'true'):
            r = score()
            r['multiplayer'] = value
            self.assertIsNone(agreeing_room(guest(), r))


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
            t.custom_cue('host_blue_upload_strip', 1, 2, observed_at=1.5)
            t.consume(scene('WAITING_ROOM', room_preview=room()), sample_id=2)
            for i in (3, 4):
                t.consume(scene('PLAYING', playing_preview=dict(title='this time')), sample_id=i)
            self.assertEqual(t.session['identity_context']['source_type'], 'custom')
            self.assertTrue(t.session['identity_context']['guest_room']['custom'])
            t.observe_room(dict(room_preview=room('Other')))
            self.assertTrue(t.room_custom)
            t.observe_room(dict(room_preview=room('Other')))
            self.assertFalse(t.room_custom)
            t.custom_cue('self_no_map', 2, 1)
            t.observe_room(dict(room_preview=room('Other', 'EASY')))
            self.assertTrue(t.room_custom)
            t.custom_cue('self_no_map', 2, 1)
            t.new_window('second')
            self.assertFalse(t.room_custom)

    def test_cue_between_ocr_title_change_survives(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room('A')), observed_at=0)
            t.custom_cue('self_no_map', 2, observed_at=.5)
            t.observe_room(dict(room_preview=room('B')), observed_at=3.1)
            t.observe_room(dict(room_preview=room('B')), observed_at=6.2)
            self.assertEqual(t.room_key[0], 'b')
            self.assertEqual(t.room_custom[0]['observed_at'], .5)

    def test_one_frame_truncated_title_keeps_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room('Genjitsu Escape')), observed_at=0)
            t.custom_cue('self_no_map', observed_at=.5)
            entry = t.room_entry_at
            t.observe_room(dict(room_preview=room('Genjitsu')), observed_at=3)
            t.observe_room(dict(room_preview=room('Genjitsu Escape')), observed_at=6)
            self.assertTrue(t.room_custom)
            self.assertEqual(t.room_entry_at, entry)
            self.assertEqual(t.room_key[0], 'genjitsuescape')

    def test_difficulty_change_after_cue_preserves_room_song(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()), observed_at=0)
            t.custom_cue('self_no_map', observed_at=.5)
            entry = t.room_entry_at
            t.room_coverage['sampled_frames'] = 4
            t.observe_room(dict(room_preview=room(difficulty='NORMAL')), observed_at=3)
            self.assertTrue(t.room_custom)
            self.assertEqual(t.room_entry_at, entry)
            self.assertEqual(t.room_coverage['sampled_frames'], 4)
            self.assertEqual(t.identity_context(3)['guest_room']['fields']['difficulty']['value'], 'NORMAL')

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
    def test_full_badge_proof_is_reused_only_for_same_pixel_mask(self):
        from dance_results.lobby_download_ocr import own_badge_crop, badge_text_mask
        with Image.open(FIXTURE) as source, tempfile.TemporaryDirectory() as temp:
            image = source.convert('RGB')
            observation = sampler_observation(image.size)
            badge, _ = own_badge_crop(image.resize((1024, 800)), 446.5, 203.5)
            observation['download_lobby'].update(
                self_no_map=True, badge_text_mask=badge_text_mask(badge).tolist())
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            sampler = RoomSampler()
            sampler.configure(t, observation)
            engine = Mock()
            engine.recognize_line.return_value = []
            for stamp in (1, 1.25):
                sampler.sample(image, t, engine, stamp)
            self.assertTrue(sampler.badge_no_map)
            self.assertIn('self_no_map', {c['cue'] for c in t.room_custom})
            self.assertEqual(engine.recognize_line.call_count, 0)
            changed = image.copy()
            ImageDraw.Draw(changed).rectangle((380, 130, 516, 178), fill='black')
            sampler.sample(changed, t, engine, 2)
            self.assertFalse(sampler.badge_no_map)
            self.assertEqual(sampler.runs[('self_no_map', 2)], 0)
            self.assertEqual(engine.recognize_line.call_count, 1)

    def test_coverage_rechecks_nameplate_pixels_and_revokes_prior_samples(self):
        with Image.open(FIXTURE) as source:
            client = source.convert('RGB')
        shifted = Image.new('RGB', client.size, '#303030')
        shifted.paste(client, (0, 24))
        scaled = Image.new('RGB', client.size, '#303030')
        scaled.paste(client.resize((822, 621)), (0, 0))
        erased = client.copy()
        ImageDraw.Draw(erased).rectangle((382, 187, 512, 208), fill=(100, 60, 160))
        blank = Image.new('RGB', client.size, 'gray')
        for kind, image in (('shifted', shifted), ('scaled', scaled),
                            ('name_erased', erased), ('blank', blank)):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                t = Tracker(temp)
                t.observe_room(dict(room_preview=room()))
                sampler = RoomSampler()
                sampler.configure(t, sampler_observation(client.size))
                engine = Mock()
                engine.recognize_line.return_value = []
                for i in range(4):
                    self.assertIsNotNone(sampler.sample(client, t, engine, i))
                self.assertEqual(t.room_coverage['sampled_frames'], 4)
                calls = engine.recognize_line.call_count
                self.assertIsNone(sampler.sample(image, t, engine, 4))
                self.assertEqual(t.room_coverage['sampled_frames'], 0)
                self.assertFalse(t.room_coverage['slot_confirmed'])
                self.assertIsNone(sampler.anchor)
                self.assertEqual(engine.recognize_line.call_count, calls)
                self.assertIsNone(sampler.sample(client, t, engine, 5))
                locked = guest()
                locked['identity_context']['guest_room']['coverage'].update(t.room_coverage)
                self.assertIsNone(bind_guest_official(catalog(), locked, score()))

    def test_sampler_and_full_analyzer_use_identical_complete_badge_pixels(self):
        from dance_results.lobby_download_ocr import reread_own_badge
        with Image.open(FIXTURE) as source, tempfile.TemporaryDirectory() as temp:
            image = Image.new('RGB', (source.width, source.height+24), '#303030')
            image.paste(source, (0, 24))
            observation = sampler_observation(image.size, top=24)
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            sampler = RoomSampler()
            sampler.configure(t, observation)
            full_engine, sample_engine = Mock(), Mock()
            full_engine.recognize_line.return_value = []
            sample_engine.recognize_line.return_value = []
            reread_own_badge(full_engine, image.resize((1024, 800)),
                             observation['download_lobby']['evidence'], 'TC')
            sampler.sample(image, t, sample_engine, 1)
            full_crop = full_engine.recognize_line.call_args.args[0]
            sample_crop = sample_engine.recognize_line.call_args.args[0]
            self.assertEqual(full_crop.size, (520, 160))
            self.assertEqual(full_crop.tobytes(), sample_crop.tobytes())

    def test_title_bar_and_client_frames_use_full_analyzer_token_anchors(self):
        from dance_results.monitor_ocr import Analyzer
        from dance_results.ocr_engine import LocalOCREngine
        models = Path(__file__).resolve().parents[1]/'.ocr-models'
        if not models.is_dir():
            self.skipTest('Local OCR models unavailable')
        engine = LocalOCREngine(models, low_latency=True)
        analyzer = Analyzer(engine)
        with Image.open(FIXTURE) as source:
            client = source.convert('RGB')
        titled = Image.new('RGB', (client.width, client.height+24), '#303030')
        titled.paste(client, (0, 24))
        for image in (client, titled):
            with self.subTest(size=image.size), tempfile.TemporaryDirectory() as temp:
                observation = analyzer.analyze(image)
                self.assertEqual(observation['capture_size'], list(image.size))
                self.assertEqual(observation['state'], 'WAITING_ROOM')
                self.assertEqual(observation['download_lobby']['slot'], 2)
                t = Tracker(temp)
                t.observe_room(dict(room_preview=room()))
                sampler = RoomSampler()
                stamp = max(time.monotonic(), getattr(engine, '_room_badge_ocr_at', 0))+1
                sampler.configure(t, observation, sample_id=stamp)
                self.assertAlmostEqual(sampler.anchor['y'], 197.4+(24 if image is titled else 0), delta=2)
                for i in range(4):
                    self.assertIsNotNone(sampler.sample(image, t, engine, stamp+i*.25))
                self.assertEqual({c['cue'] for c in t.room_custom},
                                 {'host_blue_upload_strip', 'self_no_map'})
                self.assertEqual(t.room_coverage['sampled_frames'], 4)
                self.assertTrue(t.room_coverage['slot_confirmed'])

    def test_title_bar_missing_stale_or_mismatched_anchor_requires_picker(self):
        with Image.open(FIXTURE) as source:
            image = Image.new('RGB', (source.width, source.height+24), '#303030')
            image.paste(source, (0, 24))
        for kind in ('missing', 'no_token', 'epoch', 'visit', 'title', 'size', 'older_frame'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                t = Tracker(temp)
                t.observe_room(dict(room_preview=room()))
                sampler = RoomSampler()
                observation = sampler_observation(image.size, top=24)
                if kind == 'no_token':
                    observation['download_lobby']['evidence'] = []
                if kind == 'size':
                    observation = sampler_observation()
                if kind == 'missing':
                    # Pass 1's fixed slot/height alone cannot grant coverage.
                    sampler.key = sampler.scope(t)
                    sampler.slot, sampler.host_slot = 2, 1
                else:
                    sampler.configure(t, observation, sample_id=10)
                if kind == 'epoch':
                    t.epoch += 1
                elif kind == 'visit':
                    t.room_visit_at = 'another-visit'
                elif kind == 'title':
                    t.room_key = ('another-title', 'HARD')
                engine = Mock()
                engine.recognize_line.return_value = []
                for i in range(4):
                    self.assertIsNone(sampler.sample(image, t, engine,
                                                    i if kind == 'older_frame' else 10+i*.25))
                self.assertEqual(t.room_coverage['sampled_frames'], 0)
                self.assertFalse(t.room_custom)
                self.assertEqual(engine.recognize_line.call_count, 0)
                locked = guest()
                locked['identity_context']['guest_room']['coverage'].update(t.room_coverage)
                self.assertIsNone(bind_guest_official(catalog(), locked, score()))

    def test_non_room_analyzer_observation_revokes_anchor(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            sampler = RoomSampler()
            sampler.configure(t, sampler_observation())
            sampler.configure(t, dict(state='RESULT', capture_size=[1027, 776]))
            self.assertIsNone(sampler.sample(Image.new('RGB', (1027, 776)), t, Mock(), 1))
            self.assertFalse(t.room_coverage['slot_confirmed'])
            self.assertEqual(t.room_coverage['sampled_frames'], 0)

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
            sampler.configure(t, sampler_observation(image.size))
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
        image = synthetic_nameplate(Image.new('RGB', (1024, 774), 'black'))
        ImageDraw.Draw(image).rectangle((380, 175, 450, 179), fill=(40,180,80))
        self.assertEqual(self.run_sampler(image)[0][0]['cue'], 'self_green_strip')

    def test_sampler_coordinate_scaling_and_aspect_rejection(self):
        with Image.open(FIXTURE) as image:
            resized = image.resize((1280, 960))
            self.assertIn('host_blue_upload_strip', {c['cue'] for c in self.run_sampler(resized)[0]})
            wide = image.resize((1366, 768))
            self.assertEqual(self.run_sampler(wide), ([], [None, None]))
            bordered = Image.new('RGB', (1366, 768), 'black')
            bordered.paste(image.resize((1016, 768)), (175, 0))
            self.assertEqual(self.run_sampler(bordered), ([], [None, None]))

    def test_badge_ocr_coarse_mask_and_one_second_rate_limit(self):
        with tempfile.TemporaryDirectory() as temp, Image.open(FIXTURE) as source:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            sampler = RoomSampler()
            sampler.configure(t, sampler_observation(source.size))
            engine = Mock()
            engine.recognize_line.return_value = []
            # Change the entire badge mask every frame, at the live 4 Hz cadence.
            for i in range(20):
                image = source.copy()
                ImageDraw.Draw(image).rectangle((382, 138, 511, 166), fill='white' if i % 2 else 'black')
                sampler.sample(image, t, engine, i/4)
            self.assertLessEqual(engine.recognize_line.call_count, 5)
            self.assertEqual(sampler.badge_ocr_calls, engine.recognize_line.call_count)
            sampler.sample(image, t, engine, 5)
            before = engine.recognize_line.call_count
            # Small RGB dithering of bright text leaves the coarse mask stable.
            jitter = image.point(lambda n: max(0, n-2))
            sampler.sample(jitter, t, engine, 6)
            self.assertEqual(engine.recognize_line.call_count, before)

    def test_invalid_or_duplicate_frames_do_not_count_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            sampler = RoomSampler()
            image = synthetic_nameplate(Image.new('RGB', (1024, 774)))
            sampler.configure(t, {})
            self.assertIsNone(sampler.sample(image, t, sample_id=1))
            self.assertEqual(t.room_coverage['sampled_frames'], 0)
            sampler.configure(t, sampler_observation(image.size))
            engine = Mock()
            engine.recognize_line.return_value = []
            self.assertIsNotNone(sampler.sample(image, t, engine, sample_id=2))
            self.assertIsNone(sampler.sample(image, t, sample_id=2))
            self.assertEqual(t.room_coverage['sampled_frames'], 1)

    def test_full_ocr_and_sampler_share_badge_call_budget(self):
        from dance_results.lobby_download_ocr import reread_own_badge, badge_ocr_calls
        from test_ocr_static import token
        with tempfile.TemporaryDirectory() as temp, Image.open(FIXTURE) as image:
            t = Tracker(temp)
            t.observe_room(dict(room_preview=room()))
            sampler = RoomSampler()
            sampler.configure(t, sampler_observation(image.size))
            engine = Mock()
            engine.recognize_line.return_value = []
            with patch('dance_results.lobby_download_ocr.time.monotonic', return_value=10):
                reread_own_badge(engine, image, [token('TC', 430, 210)], 'TC')
            for stamp in (10, 10.25, 10.5, 10.75):
                sampler.sample(image, t, engine, stamp)
            self.assertEqual(badge_ocr_calls(engine), 1)
            sampler.sample(image, t, engine, 11)
            self.assertEqual(badge_ocr_calls(engine), 2)
            with patch('dance_results.lobby_download_ocr.time.monotonic', return_value=11.1):
                reread_own_badge(engine, image, [token('TC', 430, 210)], 'TC')
            self.assertEqual(badge_ocr_calls(engine), 2)


class AutoAndDownloadTests(unittest.TestCase):
    setUp = review_fixture.ReviewPersistenceTests.setUp
    tearDown = review_fixture.ReviewPersistenceTests.tearDown
    official_auto = auto_fixture.Phase5CTests.official_auto

    def test_monitor_samples_recent_room_frames_and_downloads_under_lock(self):
        from dance_results.monitor import Monitor
        from dance_results.monitor_ocr import Analyzer
        info = dict(hwnd=1, pid=10, title='dance v99', minimized=False,
                    foreground=False, width=1024, height=774, is_game=True)
        windows = Mock()
        windows.list.return_value = [info]
        windows.info.return_value = info
        image = Image.new('RGB', (1024, 774), 'gray')
        capture = Mock(closed=False, error=None, copy_ms=.1)
        capture.take.side_effect = lambda: (time.monotonic(), image)
        capture.take_recent.side_effect = lambda: [(time.monotonic()-.1, image), (time.monotonic(), image)]
        monitor = Monitor(self.db, self.output/'monitor-loop', windows_factory=lambda: windows,
                          capture_factory=lambda *_: capture, engine_factory=Mock)
        observation = dict(state='WAITING_ROOM', frame_hash='room', roi_mode=False,
            room_preview=room(), download_lobby=dict(local_player_status='CONFIRMED', slot=2))
        sampled = []
        done = threading.Event()

        def sample(_sampler, pixels, tracker, engine, stamp):
            self.assertTrue(monitor.lock._is_owned())
            self.assertEqual(capture.interval, .25)
            sampled.append(stamp)
            if len(sampled) == 2:
                monitor.stop_event.set()
                done.set()
                return 4
            return None

        def room_tick(tracker):
            self.assertTrue(monitor.lock._is_owned())

        monitor.downloads = Mock(generation=0)
        monitor.downloads.room_tick.side_effect = room_tick
        with patch.object(Analyzer, 'analyze', return_value=observation), patch.object(RoomSampler, 'sample', sample):
            try:
                monitor.start(dict(ocr_interval=.5))
                self.assertTrue(done.wait(4), monitor.error)
            finally:
                monitor.stop(wait=3)
        self.assertIsNone(monitor.error)
        self.assertEqual(len(sampled), 2)
        self.assertLess(sampled[0], sampled[1])
        self.assertEqual(monitor.metrics['room_sampler_ms'], 4)
        self.assertGreater(monitor.downloads.room_tick.call_count, 0)

    def test_full_ocr_restores_four_times_own_badge_reread(self):
        from dance_results.monitor_ocr import Analyzer
        from test_ocr_static import token
        tokens = [token('TC', 430, 210), token('OTHER', 270, 210)]
        engine = Mock()
        engine.recognize.return_value = tokens
        engine.recognize_line.return_value = [token('NO MAP', 430, 160)]
        analyzer = Analyzer(engine)
        with patch('dance_results.monitor_ocr.classify', return_value=('waiting_room', [])):
            observation = analyzer.analyze(Image.new('RGB', (1024, 800), 'gray'))
        self.assertTrue(observation['download_lobby']['self_no_map'])
        args, kwargs = engine.recognize_line.call_args
        self.assertEqual(args[0].size, (520, 160))
        self.assertEqual(kwargs['scale'], 4)

    def prepare_guest(self, custom=False):
        row = self.official_auto(title='this time', notes=11)
        context = guest(custom=custom)['identity_context']
        context['captured_at'] = self.session['started_at']
        self.session.update(selection=None, song_id=None, epoch=1, identity_context=context,
                            playing_preview=dict(title='this time'), pending_reason='no_fresh_selection')
        row['locked_session'] = copy.deepcopy(self.session)
        row['ocr']['multiplayer'] = True
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

    def test_unscanned_download_episode_blocks_guest_official(self):
        row = self.prepare_guest()
        path = self.output/'sessions'/self.sid/'download-evidence.json'
        atomic_json(path, dict(scan_completed=False, status='waiting_stable_files', candidates=[]))
        body, blockers = automatic_request(self.service, row)
        self.assertIsNone(body)
        self.assertIn('automatic_guest_identity_requires_picker', blockers)
        from dance_results.ocr_matching import ReadOnlyCatalog
        c = ReadOnlyCatalog(self.db)
        try:
            self.assertEqual(offer(c, row, directory=self.output)['kind'], 'source')
            self.assertEqual(offer(c, row)['kind'], 'source')
        finally:
            c.close()
        self.assertEqual(self.service.detail(self.sid)['source_prompt']['kind'], 'source')

    def test_malformed_download_evidence_and_missing_root_return_none(self):
        row, evidence = self.download()
        path = self.output/'sessions'/self.sid/'download-evidence.json'
        for key in ('song_id', 'root_id', 'relative_path'):
            bad = copy.deepcopy(evidence)
            bad['candidates'][0].pop(key)
            atomic_json(path, bad)
            with self.subTest(missing=key):
                self.assertIsNone(verified_download_binding(self.service, row))
                self.service.detail(self.sid)
        for payload in ('null', '[]', '{broken', json.dumps(dict(evidence, candidates=[None])),
                        json.dumps(dict(evidence, candidates=None))):
            path.write_text(payload, encoding='utf8')
            with self.subTest(payload=payload):
                self.assertIsNone(verified_download_binding(self.service, row))
                self.service.detail(self.sid)
        atomic_json(path, evidence)
        from dance_results.download_tracking import guest_download_binding
        from dance_results.ocr_matching import ReadOnlyCatalog
        c = ReadOnlyCatalog(self.db)
        c.db = Mock(wraps=c.db)
        c.db.execute.return_value.fetchone.return_value = None
        with patch('dance_results.download_tracking.ReadOnlyCatalog', return_value=c):
            self.assertIsNone(guest_download_binding(self.service, row, evidence))

    def make_download_tracker(self):
        from dance_results.download_tracking import DownloadTracker
        from dance_results.library_service import LibraryService
        downloads = DownloadTracker(LibraryService(self.db), self.service, interval=0, quiet_seconds=0)
        downloads.armed = downloads._snapshots()
        downloads.armed_at = '2020-01-01T00:00:00+00:00'
        downloads.last_lobby = dict(multiplayer_observed=True, player_name='TC')
        return downloads

    def land_download(self, name='B'):
        folder = self.base/'SONG/New Pack'/name
        folder.mkdir(parents=True)
        (folder/'song.sm').write_text('#TITLE:'+name+';', encoding='utf8')

    def test_download_landing_between_ocrs_keeps_entry_baseline(self):
        tracker = Tracker(self.output/'room-tracker')
        tracker.state = 'WAITING_ROOM'
        tracker.observe_room(dict(room_preview=room('A')), observed_at=0)
        downloads = self.make_download_tracker()
        with patch.object(downloads, '_enqueue') as enqueue:
            downloads.room_tick(tracker)
            scope_a = copy.deepcopy(downloads.room_scope)
            downloads._room_enter(scope_a)
            baseline = downloads.room_baseline
            self.land_download()
            tracker.observe_room(dict(room_preview=room('B')), observed_at=3)
            tracker.observe_room(dict(room_preview=room('B')), observed_at=6)
            downloads.room_tick(tracker)
            self.assertEqual(sum(call.args[0] == 'room_enter' for call in enqueue.call_args_list), 1)
            self.assertIs(downloads.room_baseline, baseline)
            downloads._room_poll(downloads.room_scope)
            self.assertTrue(downloads.active['metadata_change_observed'])
            self.assertIs(downloads.active['_baseline'], baseline)
            downloads.room_tick(tracker)
            self.assertTrue(tracker.room_custom)
            self.assertTrue(tracker.room_coverage['baseline_before_entry'])

    def test_baseline_errors_cannot_grant_official_coverage(self):
        tracker = Tracker(self.output/'room-tracker')
        tracker.state = 'WAITING_ROOM'
        tracker.observe_room(dict(room_preview=room()))
        downloads = self.make_download_tracker()
        for item in downloads.armed.values():
            item['snapshot']['errors'] = ['root unavailable']
        with patch.object(downloads, '_enqueue'):
            downloads.room_tick(tracker)
            downloads._room_enter(downloads.room_scope)
            downloads.room_tick(tracker)
        self.assertFalse(tracker.room_coverage['baseline_before_entry'])

    def test_first_waiting_frame_baselines_even_without_title(self):
        tracker = Tracker(self.output/'room-tracker')
        tracker.state = 'WAITING_ROOM'
        tracker.observe_room({})
        downloads = self.make_download_tracker()
        with patch.object(downloads, '_enqueue') as enqueue:
            downloads.room_tick(tracker)
            scope = copy.deepcopy(downloads.room_scope)
            self.assertIsNone(scope['room_key'])
            downloads._room_enter(scope)
            self.land_download()
            tracker.observe_room(dict(room_preview=room('B')))
            downloads.room_tick(tracker)
            self.assertEqual(sum(call.args[0] == 'room_enter' for call in enqueue.call_args_list), 1)
            downloads._room_poll(downloads.room_scope)
            self.assertTrue(downloads.active['metadata_change_observed'])

    def test_download_polled_before_title_ocr_keeps_pre_download_snapshot(self):
        tracker = Tracker(self.output/'room-tracker')
        tracker.state = 'WAITING_ROOM'
        tracker.observe_room(dict(room_preview=room('A')), observed_at=0)
        downloads = self.make_download_tracker()
        with patch.object(downloads, '_enqueue'):
            downloads.room_tick(tracker)
            downloads._room_enter(downloads.room_scope)
            baseline = downloads.room_baseline
            self.land_download()
            with patch('dance_results.download_tracking.time.monotonic', return_value=.5):
                downloads._room_poll(downloads.room_scope)
            tracker.observe_room(dict(room_preview=room('B')), observed_at=3)
            tracker.observe_room(dict(room_preview=room('B')), observed_at=6)
            downloads.room_tick(tracker)
            downloads._room_poll(downloads.room_scope)
            self.assertEqual(downloads.active['room_entry_at'], tracker.room_entry_at)
            self.assertIs(downloads.active['_baseline'], baseline)
            self.assertEqual(downloads.active['metadata_observed_at'], .5)

    def test_second_room_song_download_associates_new_episode(self):
        for entry in (None, 'previous-song'):
            with self.subTest(old_entry=entry):
                downloads = self.make_download_tracker()
                self.land_download('B'+str(entry))
                downloads.active = dict(session_id='previous-session', room_entry_at=entry, scan_completed=True)
                s = guest('B')
                s['session_id'] = self.sid
                downloads._associate(s)
                self.assertEqual(downloads.active['session_id'], self.sid)
                self.assertEqual(downloads.active['room_entry_at'], 'room-entry')
                self.assertFalse(downloads.active['scan_completed'])

    def test_room_poll_preserves_captured_episode_with_scan_pending(self):
        downloads = self.make_download_tracker()
        tracker = Tracker(self.output/'room-tracker')
        tracker.observe_room(dict(room_preview=room('B')))
        scope = dict(room_key=list(tracker.room_key), room_entry_at=tracker.room_entry_at,
                     epoch=tracker.epoch, room=tracker.room_preview)
        downloads.room_scope = scope
        downloads._room_enter(scope)
        old = dict(session_id='previous-session', room_entry_at='previous-song', scan_completed=False)
        downloads.active = old
        self.land_download()
        downloads._room_poll(scope)
        self.assertIs(downloads.active, old)
        self.assertEqual(len(downloads.deferred), 1)
        s = guest('B')
        s['session_id'] = self.sid
        s['identity_context']['guest_room'].update(room_entry_at=scope['room_entry_at'], room_key=scope['room_key'])
        downloads._associate(s)
        self.assertIs(downloads.active, old)
        self.assertEqual(json.loads((self.output/'sessions'/self.sid/'download-evidence.json').read_text())['status'],
                         'waiting_previous_scan')
        old['scan_completed'] = True
        downloads._resume_deferred()
        self.assertEqual(downloads.active['session_id'], self.sid)
        self.assertFalse(downloads.active['scan_completed'])

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
