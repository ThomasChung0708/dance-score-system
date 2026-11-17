"""Small room-only crops; no screenshots or other players' text are persisted."""
import time

import numpy as np
from .ocr_screens import center, compact
from .lobby_download_ocr import claim_badge_ocr, own_badge_crop, badge_text_mask


# Aspect is only a supported-layout guard, never a vertical coordinate scale.
REFERENCE_HEIGHT = 774
ASPECT_TOLERANCE = .04


class RoomSampler:
    def __init__(self):
        self.key = None
        self.slot = None
        self.host_slot = None
        self.runs = {}
        self.badge_mask = None
        self.badge_no_map = False
        self.badge_ocr_at = float('-inf')
        self.badge_ocr_calls = 0
        self.last_sample = None
        self.anchor = None

    def invalidate(self, tracker):
        self.anchor = None
        self.slot = self.host_slot = None
        self.runs = {}
        self.badge_mask = None
        self.badge_no_map = False
        tracker.room_coverage.update(slot_confirmed=False, sampled_frames=0)

    def configure(self, tracker, observation, sample_id=None):
        key = self.scope(tracker)
        if key != self.key:
            total_calls = self.badge_ocr_calls
            self.__init__()
            self.badge_ocr_calls = total_calls
            self.key = key
        if observation.get('state') != 'WAITING_ROOM':
            self.invalidate(tracker)
            return
        lobby = observation.get('download_lobby') or {}
        size = observation.get('capture_size')
        if not (isinstance(size, (list, tuple)) and len(size) == 2
                and all(type(n) is int and n > 0 for n in size)):
            self.invalidate(tracker)
            return
        if self.anchor and self.anchor['size'] != tuple(size):
            self.invalidate(tracker)
        if lobby.get('local_player_status') != 'CONFIRMED':
            return
        own = [t for t in lobby.get('evidence', [])
               if compact(t['text']) == compact(lobby.get('player_name') or '')
               and t['confidence'] >= .95]
        slot = lobby.get('slot')
        if len(own) != 1 or type(slot) is not int or slot not in range(6):
            return
        x, y = center(own[0])
        if int((x-55)//156) != slot or not 165 <= y <= 250:
            return
        if self.slot is not None and self.slot != slot:
            self.invalidate(tracker)
        self.slot = slot
        # Analyzer normalizes the entire raw frame to 1024 x 800. Undo that
        # transform for the token; card dimensions scale with width, so adding
        # a title bar translates the crops rather than stretching their y.
        self.anchor = dict(size=tuple(size), x=x*size[0]/1024,
                           y=y*size[1]/800, name_x=x, name_y=y, sampled_at=sample_id)
        proof = np.asarray(lobby.get('badge_text_mask', []), dtype=bool)
        if proof.shape == (8, 32) and type(lobby.get('self_no_map')) is bool:
            self.badge_mask = proof
            self.badge_no_map = lobby['self_no_map']
        tracker.room_coverage['slot_confirmed'] = True
        # HOST is only a crop locator for a conservative custom cue. Its lower
        # .85 floor cannot confirm local identity or authorize an official bind.
        hosts = [t for t in observation.get('tokens', [])
                 if compact(t['text']) == 'HOST' and t['confidence'] >= .85
                 and 20 <= y-center(t)[1] <= 80]
        self.host_slot = None
        if len(hosts) == 1:
            host_x = center(hosts[0])[0]
            host_slot = int((host_x-55)//156)
            if host_slot in range(6):
                self.host_slot = host_slot
                self.anchor['host_x'] = host_x*size[0]/1024

    @staticmethod
    def scope(tracker):
        return (tracker.epoch, tracker.room_visit_at,
                tracker.room_key[0] if tracker.room_key else None)

    def sample(self, image, tracker, engine=None, sample_id=None):
        started = time.perf_counter()
        if sample_id is not None and sample_id == self.last_sample:
            return None
        if self.last_sample is not None and sample_id is not None and sample_id < self.last_sample:
            return None
        if (self.key != self.scope(tracker) or not self.anchor
                or self.anchor['size'] != image.size
                or abs((image.width/image.height)/(1024/REFERENCE_HEIGHT)-1) > ASPECT_TOLERANCE):
            self.invalidate(tracker)
            return None
        anchor_at = self.anchor['sampled_at']
        if anchor_at is not None and (sample_id is None or sample_id < anchor_at):
            return None
        self.last_sample = sample_id
        scale_x = scale_y = image.width/1024
        sampled_at = sample_id if isinstance(sample_id, (int, float)) else time.monotonic()
        def crop(box, x):
            return image.crop(tuple(round(n*scale_x+(x if i % 2 == 0 else self.anchor['y']))
                                    for i, n in enumerate(box))).convert('RGB')

        # Recheck the card in EVERY sampled frame, not only the earlier OCR.
        # This skin has a purple nameplate with white text at the name anchor.
        # A blank, displaced or occluded crop revokes all accumulated coverage;
        # a fresh analyzer confirmation is then necessary before sampling again.
        plate = np.asarray(crop((-60, -10, 60, 10), self.anchor['x'])).astype(np.int16)
        r, g, b = (plate[:, :, i] for i in range(3))
        purple = (r > 50) & (b-r > 25) & (r-g > 20)
        name = np.asarray(crop((-12, -9, 12, 9), self.anchor['x'])).astype(np.int16)
        white_name = (name.min(axis=2) >= 200) & (name.max(axis=2)-name.min(axis=2) <= 45)
        if np.mean(purple) < .75 or int(white_name.sum()) < max(6, round(8*scale_x*scale_y)):
            self.invalidate(tracker)
            return None
        if engine is not None and hasattr(engine, 'recognize_line'):
            tracker.room_coverage['sampled_frames'] = tracker.room_coverage.get('sampled_frames', 0)+1

        cues = {}
        for slot, cue in ((self.slot, 'self_green_strip'), (self.host_slot, 'host_blue_upload_strip')):
            if slot not in range(6):
                continue
            x = self.anchor['x'] if slot == self.slot else self.anchor['host_x']
            pixels = np.asarray(crop((-70, -22, 70, -17), x)).astype(np.int16)
            r, g, b = (pixels[:, :, i] for i in range(3))
            mask = ((g > 90) & (g-r > 35) & (g-b > 15) if cue == 'self_green_strip'
                    else (abs(r-107) <= 16) & (abs(g-167) <= 16) & (abs(b-255) <= 16))
            # Require a long horizontal run AND substantial area in the exact
            # five-pixel strip. Costume colours elsewhere cannot contribute.
            longest = 0
            for row in mask:
                run = 0
                for value in row:
                    run = run+1 if value else 0
                    longest = max(longest, run)
            cues[(cue, slot)] = longest >= round(25*scale_x) and int(mask.sum()) >= round(75*scale_x*scale_y)
        # Use exactly the full analyzer's pixel space and complete badge box.
        # A shorter box clips the italic glyphs; raw-space rounding also changes
        # line-OCR confidence on near-native monitor captures. Normalize before
        # cropping so both paths see the same pixels, retaining the .90 floor.
        normalized = image.convert('RGB').resize((1024, 800))
        badge, _ = own_badge_crop(normalized, self.anchor['name_x'], self.anchor['name_y'])
        mask = badge_text_mask(badge)
        changed = self.badge_mask is None or np.mean(mask != self.badge_mask) > .06
        if changed:
            self.badge_no_map = False
            if (sampled_at-self.badge_ocr_at >= 1
                    and engine is not None and hasattr(engine, 'recognize_line')
                    and claim_badge_ocr(engine, sampled_at)):
                self.badge_mask = mask
                self.badge_ocr_at = sampled_at
                self.badge_ocr_calls += 1
                tokens = engine.recognize_line(badge.resize((badge.width*4, badge.height*4)))
                self.badge_no_map = any(compact(t['text']) == 'NOMAP' and t['confidence'] >= .9 for t in tokens)
        cues[('self_no_map', self.slot)] = self.badge_no_map
        for key, detected in cues.items():
            self.runs[key] = self.runs.get(key, 0)+1 if detected else 0
            if self.runs[key] >= 2:
                tracker.custom_cue(key[0], key[1], self.runs[key], sampled_at)
        return (time.perf_counter()-started)*1000
