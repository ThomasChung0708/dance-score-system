"""Small room-only crops; no screenshots or other players' text are persisted."""
import hashlib
import time

import numpy as np

from .ocr_screens import center, compact


class RoomSampler:
    def __init__(self):
        self.key = None
        self.slot = None
        self.host_slot = None
        self.runs = {}
        self.badge_hash = None
        self.badge_no_map = False
        self.last_sample = None

    def configure(self, tracker, observation):
        key = (tracker.epoch, tracker.room_key)
        if key != self.key:
            self.__init__()
            self.key = key
        lobby = observation.get('download_lobby') or {}
        if lobby.get('local_player_status') == 'CONFIRMED':
            self.slot = lobby.get('slot')
        hosts = [t for t in observation.get('tokens', [])
                 if compact(t['text']) == 'HOST' and t['confidence'] >= .9
                 and 100 <= center(t)[1] <= 210]
        slots = {int((center(t)[0]-55)//156) for t in hosts}
        if len(slots) == 1:
            self.host_slot = slots.pop()

    def sample(self, image, tracker, engine=None, sample_id=None):
        started = time.perf_counter()
        if sample_id is not None and sample_id == self.last_sample:
            return 0
        self.last_sample = sample_id
        if self.key != (tracker.epoch, tracker.room_key) or self.slot not in range(6):
            return 0
        scale = image.width/1024

        def crop(box):
            return image.crop(tuple(round(n*scale) for n in box)).convert('RGB')

        cues = {}
        for slot, cue in ((self.slot, 'self_green_strip'), (self.host_slot, 'host_blue_upload_strip')):
            if slot not in range(6):
                continue
            pixels = np.asarray(crop((67+156*slot, 175, 207+156*slot, 180))).astype(np.int16)
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
            cues[(cue, slot)] = longest >= round(25*scale) and int(mask.sum()) >= round(75*scale*scale)
        # Tight text line excludes the animated face above and the strip below.
        badge = crop((70+156*self.slot, 138, 199+156*self.slot, 166))
        digest = hashlib.sha256(badge.tobytes()).hexdigest()
        if digest != self.badge_hash:
            self.badge_hash = digest
            self.badge_no_map = False
            if engine is not None and hasattr(engine, 'recognize_line'):
                tokens = engine.recognize_line(badge.resize((badge.width*2, badge.height*2)))
                self.badge_no_map = any(compact(t['text']) == 'NOMAP' and t['confidence'] >= .9 for t in tokens)
        cues[('self_no_map', self.slot)] = self.badge_no_map
        for key, detected in cues.items():
            self.runs[key] = self.runs.get(key, 0)+1 if detected else 0
            if self.runs[key] == 2:
                tracker.custom_cue(key[0], key[1], self.runs[key])
        return (time.perf_counter()-started)*1000
