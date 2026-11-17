"""Debounced state/selection/session tracking. No SQLite writes or score rules."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from .ocr_matching import ReadOnlyCatalog, same_song_title_key, probably_same_title
from .ocr_policy import SELECTION_REQUIRED


def utc():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Tracker:
    def __init__(self, directory, confirmations=2, selection_ttl=60):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.confirmations = confirmations
        self.selection_ttl = selection_ttl
        self.state = 'UNKNOWN'
        self.proposed = None
        self.count = 0
        self.last_sample = None
        self.current_pack = None
        self.pack_observed_at = None
        self.pack_confirmed_at = None
        self.current_source = 'unknown'
        self.current_category = None
        self.proposed_category = None
        self.category_count = 0
        self.category_seen_at = None
        self.selection = None
        self.selection_candidate = None
        self.playing_candidate = None
        self.selection_count = 0
        self.selection_key = None
        self.session = None
        self.last_session = None
        self.last_result = None
        self.best_result = None
        self.last_result_observation = None
        self.events = []
        self.epoch = 0
        self.room_preview = None
        self.last_room_role = None
        self.proposed_pack=None
        self.pack_count=0
        self.pack_candidates=[]
        self.pack_list_visible=False
        self.title_catalog = None
        self.playing_context = None
        self.room_custom = []
        self.room_key = None
        self.room_entry_at = None
        self.non_host_reads = 0

    def clear_room(self):
        self.room_preview = None
        self.room_key = None
        self.room_entry_at = None
        self.room_custom = []
        self.last_room_role = None
        self.non_host_reads = 0

    def observe_room(self, observation):
        preview = observation.get('room_preview') or {}
        title = same_song_title_key(preview.get('title'))
        if title:
            key = (title, preview.get('difficulty'))
            changed = (self.room_key is None or title != self.room_key[0]
                       or key[1] is not None and self.room_key[1] is not None and key[1] != self.room_key[1])
            if changed:
                self.room_custom = []
                self.room_entry_at = utc()
            if key[1] is None and self.room_key and title == self.room_key[0]:
                key = self.room_key
            self.room_key = key
            if any(preview.get(k) != (self.room_preview or {}).get(k) for k in ('title', 'game_level', 'difficulty')):
                self.log('room_preview', preview=preview, epoch=self.epoch)
            self.room_preview = copy.deepcopy(preview)
        lobby = observation.get('download_lobby') or {}
        if lobby.get('local_player_status') == 'CONFIRMED':
            if lobby.get('self_no_map'):
                self.custom_cue('self_no_map', lobby.get('slot'), 1)

    def custom_cue(self, cue, slot=None, frames=2):
        if not self.room_key:
            return
        item = dict(cue=cue, slot=slot, frames=frames)
        if item not in self.room_custom:
            self.room_custom.append(item)
            self.log('room_custom_cue', **item, room_key=self.room_key, epoch=self.epoch)

    def log(self, kind, **data):
        event = dict(at=utc(), event=kind, **data)
        self.events.append(event)
        self.events = self.events[-200:]
        path = self.directory/'diagnostics.jsonl'
        if path.exists() and path.stat().st_size > 10*1024*1024:
            path.replace(self.directory/'diagnostics.previous.jsonl')
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False)+'\n')

    def invalidate_selection(self, reason, clear_pack=True):
        # Loading fades contain no new identity evidence and do not refresh TTL.
        if reason == 'black_capture':
            return
        # Preview/role changes describe the selection inside a room, not a
        # move to another list or room. Independently confirmed local Pack
        # evidence survives; a guest without that evidence still loses scope.
        if reason == 'room_preview_changed' or (
                reason == 'multiplayer_guest_source_not_confirmed'
                and (self.current_pack or {}).get('status') == 'confirmed'):
            clear_pack = False
        previous = self.selection or self.selection_candidate or {}
        previous_title = previous.get('fields', {}).get('title', {}).get('value')
        self.selection = None
        self.selection_candidate = None
        self.playing_candidate = None
        self.selection_key = None
        self.selection_count = 0
        if clear_pack:
            self.current_category = None
            self.proposed_category = None
            self.category_count = 0
            self.category_seen_at = None
            self.current_pack = None
            self.pack_observed_at = None
            self.pack_confirmed_at = None
            self.current_source = 'unknown'
            self.proposed_pack=None
            self.pack_count=0
            self.pack_candidates=[]
            self.pack_list_visible=False
        self.log('selection_invalidated', reason=reason, last_selection_title=previous_title,
                 last_selection_key=same_song_title_key(previous_title))

    def playing_title_agrees(self, selected, preview, catalog=None):
        title = selected.get('fields', {}).get('title', {}).get('value')
        detail = probably_same_title(title, preview)
        catalog = catalog or self.title_catalog
        if isinstance(catalog, ReadOnlyCatalog):
            pack_id = (selected.get('pack') or {}).get('pack_id')
            source = selected.get('source_type')
            ids = catalog.title_ids(preview, pack_id, source)
            selected_id = (selected.get('matching') or {}).get('confirmed_song_id')
            if selected_id is None:
                selected_ids = catalog.title_ids(title, pack_id, source)
                selected_id = next(iter(selected_ids)) if len(selected_ids) == 1 else None
            detail['preview_exact_song_ids'] = sorted(ids)
            if selected_id is not None and len(ids) == 1 and selected_id not in ids:
                detail.update(same=False, method='different_unique_catalog_song')
        self.log('playing_title_comparison', selection_title=title, preview_title=preview, **detail)
        return detail['same']

    def identity_context(self, now):
        """Freeze current context scope at PLAYING, never infer it at RESULT."""
        pack_age = None if self.pack_confirmed_at is None else now - self.pack_confirmed_at
        source_age = None if self.category_seen_at is None else now - self.category_seen_at
        # current_pack is revoked on context changes/contradiction. Its last
        # observation time is diagnostic, not a lease on an unchanged list.
        pack = self.current_pack if (self.current_pack or {}).get('status') == 'confirmed' else None
        source = ('custom' if pack else self.current_source if source_age is not None
            and 0 <= source_age <= self.selection_ttl else 'unknown')
        context = dict(epoch=self.epoch, pack=copy.deepcopy(pack), source_type=source,
                    pack_context_current=bool(pack),
                    pack_age_seconds=pack_age, source_age_seconds=source_age,
                    max_age_seconds=self.selection_ttl)
        if self.room_preview and self.room_key:
            room = copy.deepcopy(self.room_preview)
            room.update(custom=bool(self.room_custom), source_evidence=copy.deepcopy(self.room_custom),
                        room_key=list(self.room_key), room_entry_at=self.room_entry_at)
            context['guest_room'] = room
            if self.room_custom:
                context.update(source_type='custom', source_evidence=copy.deepcopy(self.room_custom))
        return context

    def interrupt(self, reason):
        if reason in ('game_window_closed', 'window_identity_changed', 'monitor_stopped', 'monitor_error'):
            self.clear_room()
        if self.session and self.session['status'] == 'playing':
            if self.best_result:
                observation, image = self.best_result
                observation = copy.deepcopy(observation)
                temporal = observation['result_ocr'].get('temporal_validation') or {}
                samples = temporal.get('samples') or []
                from .ocr_temporal import POLICY
                observation['result_ocr']['temporal_validation'] = dict(
                    policy=POLICY, session_id=self.session['session_id'], status='pending_review',
                    reasons=['result_left_before_confirmation'], samples=samples[-1:],
                )
                self._finish_result(observation, image, recovery_reason=reason)
                # Only the retained frame is available here. It is review evidence,
                # never a replacement for two fresh identical result frames.
                from .ocr_temporal import ResultConsistency
                retained = ResultConsistency()
                retained.images = [image]
                retained.save(self.directory/'sessions'/self.last_session['session_id'],
                              self.last_result_observation['result_ocr'])
            else:
                self.session.update(status='interrupted', ended_at=utc(), interruption_reason=reason)
                self._save_session()
                self.last_session = copy.deepcopy(self.session)
                self.session = None
                self.state = 'INTERRUPTED'
                self.log('session_interrupted', reason=reason)
        self.invalidate_selection(reason)
        self.count = 0
        self.proposed = None

    def new_window(self, identity):
        self.interrupt('window_identity_changed')
        self.epoch += 1
        self.clear_room()
        self.state = 'UNKNOWN'
        self.log('window_connected', identity=identity, epoch=self.epoch)

    def pack_observation(self, pack, *, inferred=False, uncertain=False, now=None):
        """Called whenever the list is visible, BEFORE debounce or matching."""
        now = time.monotonic() if now is None else now
        before = ((self.current_pack or {}).get('root_id'),(self.current_pack or {}).get('pack_id'))
        if pack and pack.get('status') == 'confirmed':
            proposed=(pack.get('root_id'),pack['pack_id'])
            if proposed==self.proposed_pack:self.pack_count+=1
            else:self.proposed_pack=proposed;self.pack_count=1
            # Successful library inference already requires two distinct songs.
            # Persist that evidence without pretending to observe another frame.
            if inferred or self.pack_count>=self.confirmations:
                self.current_pack = copy.deepcopy(pack)
                self.current_source = 'custom'
                self.pack_confirmed_at = now
            else:
                self.current_pack=None
                self.current_source='unknown'
        else:
            # Empty frames do not change list/room context, refresh timestamps,
            # or restore a Pack revoked by a real context change/ambiguity.
            if not pack and not uncertain and self.current_pack:
                return
            self.current_pack = None
            self.pack_confirmed_at = None
            self.current_source = 'unknown'
            self.proposed_pack=None
            self.pack_count=0
        after=((self.current_pack or {}).get('root_id'),(self.current_pack or {}).get('pack_id'))
        if before != after:
            self.invalidate_selection('pack_changed_or_uncertain', clear_pack=False)
            self.pack_observed_at = now if self.current_pack else None
            self.log('pack_observed', pack=self.current_pack)

    def category_observation(self, category, confidence=0, now=None):
        """Two observations grant source authority; contradiction revokes it now."""
        now = time.monotonic() if now is None else now
        reliable = confidence >= .9 and category in ('all', 'jinyue')
        if not reliable:
            self.proposed_category = None
            self.category_count = 0
            if (category is not None or self.category_seen_at is not None
                    and now-self.category_seen_at > self.selection_ttl):
                self.current_category = None
                self.current_source = 'custom' if self.current_pack else 'unknown'
            return
        self.category_seen_at = now
        if category == self.proposed_category:
            self.category_count += 1
        else:
            self.proposed_category = category
            self.category_count = 1
        if category != self.current_category:
            self.invalidate_selection('category_changed', clear_pack=False)
            self.current_category = None
            self.current_pack = None
            self.pack_confirmed_at = None
            self.proposed_pack = None
            self.pack_count = 0
            self.current_source = 'unknown'
        if self.category_count >= self.confirmations:
            self.current_category = category
            self.current_source = 'official' if category == 'all' else 'custom'
            if category == 'all':
                self.current_pack = None
                self.pack_candidates = []

    def update_selection(self, observation, now):
        # A transition can retain the song-select anchors while its identity is
        # unreadable. It neither changes the song nor refreshes its freshness.
        fields = observation['fields']

        def unreadable(key):
            field = fields.get(key) or {}
            return field.get('value') in (None, '') or bool(field.get('reason'))

        if unreadable('title'):
            incoming = fields.get('title', {}).get('value')
            previous = self.selection or self.selection_candidate or {}
            previous_title = previous.get('fields', {}).get('title', {}).get('value')
            if incoming and previous_title and same_song_title_key(incoming) != same_song_title_key(previous_title):
                self.invalidate_selection('unreliable_new_selection_title', clear_pack=False)
            return
        if any(unreadable(key) for key in SELECTION_REQUIRED):
            # Partially readable frame: keep the current selection only when it
            # shows the same title. A readable different title still replaces or
            # invalidates the old selection, so a stale song is never kept.
            current = ((self.selection or {}).get('fields') or {}).get('title') or {}
            if same_song_title_key(current.get('value')) == same_song_title_key(fields['title']['value']):
                return
        payload = dict(fields=observation['fields'], pack=copy.deepcopy(self.current_pack),
                       source_type=observation.get('source_type', self.current_source),
                       matching=copy.deepcopy(observation['matching']), frame_hash=observation['frame_hash'])
        payload['category'] = copy.deepcopy(observation.get('category'))
        if 'ocr_fields' in observation:
            payload['ocr_fields'] = copy.deepcopy(observation['ocr_fields'])
        for key in ('raw_ocr_fields','numeric_roi_evidence'):
            if key in observation:payload[key]=copy.deepcopy(observation[key])
        # Confidence boxes and background pixels are not song identity.
        # Optional metadata can alternate while the selected chart stays put
        # (e.g. notes disappearing during a preview animation). Debounce chart
        # identity, not the incidental presence of artist/duration/notes.
        values = {k: payload['fields'].get(k, {}).get('value') for k in SELECTION_REQUIRED}
        values['title'] = same_song_title_key(values['title'])
        key = fingerprint(dict(values=values,
                               pack_id=(self.current_pack or {}).get('pack_id'), source_type=payload['source_type'], epoch=self.epoch))
        manual = (self.selection or {}).get('manual_confirmation')
        if manual and manual['observed_values'] == {k: v.get('value') for k, v in payload['fields'].items()} and (
                self.current_pack or {}).get('pack_id') == manual.get('pack_id'):
            key = self.selection_key
        if key != self.selection_key:
            self.selection_key = key
            self.selection_count = 1
            self.selection = None
            self.log('selection_changed', observation_id=key,
                     title=fields['title']['value'], title_key=values['title'],
                     difficulty=values.get('difficulty'), game_level=values.get('game_level'))
        else:
            self.selection_count += 1
            previous_title = (self.selection_candidate or {}).get('fields', {}).get('title', {}).get('value')
            if previous_title != fields['title']['value']:
                self.log('selection_title_variant', title=fields['title']['value'],
                         previous_title=previous_title, title_key=values['title'],
                         observation_id=key, confirmation_count=self.selection_count)
        # Preserve even one observed chart before the user immediately starts.
        # It is review evidence, not a substitute for a debounced selection.
        payload.update(observation_id=key, observed_at=utc(), last_seen=now, epoch=self.epoch)
        self.selection_candidate = copy.deepcopy(payload)
        if self.selection_count >= self.confirmations:
            previous = self.selection
            if previous and previous.get('manual_confirmation'):
                payload['manual_confirmation'] = previous['manual_confirmation']
                payload['matching'] = previous['matching']
                payload['fields'] = previous['fields']
            self.selection = payload

    def fast_selection(self, now):
        candidate = self.selection_candidate
        # Extend only while the list itself remains the active context. A blank
        # room cannot corroborate a lone observation (the older 10s rule stays).
        grace = self.selection_ttl if self.state == 'SONG_SELECT' else min(10, self.selection_ttl)
        if (not candidate or candidate['epoch'] != self.epoch
                or not 0 <= now-candidate['last_seen'] <= grace):
            return None
        title = candidate['fields'].get('title') or {}
        pack = candidate.get('pack') or {}
        if (not title.get('value') or title.get('reason') or title.get('confidence', 0) < .8
                or pack.get('status') != 'confirmed'
                or pack.get('pack_id') != (self.current_pack or {}).get('pack_id')):
            return None
        selected = copy.deepcopy(candidate)
        selected.update(handoff='single_observation', observed_frames=self.selection_count)
        return selected

    def room_title_agrees(self, preview):
        selected = self.selection or self.selection_candidate or {}
        title = same_song_title_key(selected.get('fields', {}).get('title', {}).get('value'))
        room_title = same_song_title_key(preview.get('title'))
        return (not room_title or room_title == title or
                (len(room_title) >= 8 and title.startswith(room_title)))

    def consume(self, observation, now=None, image=None, sample_id=None):
        now = time.monotonic() if now is None else now
        if sample_id is not None and sample_id == self.last_sample:
            return
        self.last_sample = sample_id
        wanted = observation['state']
        if wanted == 'WAITING_ROOM':
            self.observe_room(observation)
            role = observation.get('context', {}).get('local_is_host')
            self.non_host_reads = self.non_host_reads + 1 if role is False else 0
            if self.non_host_reads == 2:
                self.invalidate_selection('multiplayer_guest_source_not_confirmed')
            if role is not None:
                self.last_room_role = role
        elif wanted in ('LOBBY', 'LOGIN', 'SONG_SELECT', 'PROFILE', 'SPECTATING', 'INTERRUPTED'):
            self.clear_room()
        else:
            self.non_host_reads = 0
        if self.session and wanted in ('RESULT', 'GAME_OVER') and image is not None:
            from .result_ocr import complete_reliable_result
            ocr = observation.get('result_ocr') or {}
            if complete_reliable_result(ocr):
                from .ocr_policy import result_required
                quality = min(ocr['fields'][k].get('confidence', 0)
                              for k in result_required(ocr) if k != 'title')
                previous = self.best_result
                if previous is None or quality >= previous[0].get('retention_quality', 0):
                    saved = copy.deepcopy(observation)
                    saved['retention_quality'] = quality
                    self.best_result = (saved, image.copy())
        if wanted == 'SONG_SELECT':
            # A readable new identity invalidates the prior one before debounce.
            self.update_selection(observation, now)
        if (wanted == 'WAITING_ROOM' and not self.session and (self.selection or self.selection_candidate)
                and not self.room_title_agrees(observation.get('room_preview') or {})):
            # Contradictory identity wins even before scene debounce. A later
            # blank preview cannot resurrect this selection.
            self.invalidate_selection('room_preview_changed', clear_pack=False)
        if wanted == 'PLAYING' and self.proposed != 'PLAYING':
            self.playing_context = self.identity_context(now)
            # Check freshness on the first PLAYING sample, before state debounce
            # spends another OCR interval confirming that play really started.
            self.playing_candidate = self.fast_selection(now) if self.selection is None else None
            if self.playing_candidate:
                preview = observation.get('playing_preview') or {}
                fields = self.playing_candidate['fields']
                if ((preview.get('title') and not self.playing_title_agrees(self.playing_candidate, preview['title']))
                        or (preview.get('game_level') is not None
                            and fields.get('game_level', {}).get('value') is not None
                            and preview['game_level'] != fields['game_level']['value'])):
                    self.playing_candidate = None
        if wanted != self.proposed:
            self.proposed = wanted
            self.count = 1
        else:
            self.count += 1
        if self.count < self.confirmations:
            return
        if wanted in ('RESULT', 'GAME_OVER') and self.session and self.best_result:
            temporal = (observation.get('result_ocr') or {}).get('temporal_validation') or {}
            if len(temporal.get('samples', [])) == 1 and not temporal.get('incomplete'):
                # A preceding frame without a local row can advance scene
                # debounce (it may be a settled spectator result). It cannot
                # make the first complete own score consume the active play.
                # Wait for a second distinct score, or retain it on quick leave.
                self.count = 1
                return
        if wanted=='WAITING_ROOM':
            preview=observation.get('room_preview',{})
            role=observation.get('context',{}).get('local_is_host')
            current_selection = self.selection or self.selection_candidate
            # Room LV/difficulty describe the host preview, not necessarily the
            # local player's chart. Only a different title breaks local identity.
            # The narrow room title ROI may clip a long title at its right edge;
            # PLAYING still has to agree with the full selected title below.
            room_title = same_song_title_key(preview.get('title'))
            selected_title = same_song_title_key((current_selection or {}).get('fields', {}).get('title', {}).get('value'))
            if current_selection and not self.room_title_agrees(preview):
                self.invalidate_selection('room_preview_changed',clear_pack=False)
            elif (self.selection is None and current_selection and role is True
                  and room_title and room_title == selected_title
                  and 0 <= now-current_selection['last_seen'] <= self.selection_ttl
                  and (current_selection.get('pack') or {}).get('status') == 'confirmed'):
                # A debounced host room corroborates a single captured local
                # selection during a longer room-start handoff. Keep original
                # timestamp/confidences and the strict single-observation gate.
                self.selection = copy.deepcopy(current_selection)
                self.selection['handoff'] = 'single_observation'
                self.selection['room_title_corroborated'] = True
        if wanted == self.state:
            return
        old = self.state
        self.state = wanted
        self.log('state_transition', previous=old, current=wanted)
        # UNKNOWN/load fades contain no contradictory identity. Existing TTL,
        # window epoch, room-title and PLAYING-title checks still apply.
        if wanted == 'PLAYING' and not self.session:
            selected = copy.deepcopy(self.selection or self.playing_candidate)
            reason = None
            if selected and (now-selected['last_seen'] > self.selection_ttl or selected['epoch'] != self.epoch):
                selected = None
                reason = 'selection_expired'
            if selected and self.room_custom and selected.get('source_type') == 'official':
                selected = None
                reason = 'room_custom_cue_contradicts_official_selection'
            preview = observation.get('playing_preview', {})
            if selected:
                fields = selected['fields']
                if preview.get('title') and not self.playing_title_agrees(selected, preview['title']):
                    selected = None
                    reason = 'playing_title_differs_from_selection'
                elif (preview.get('game_level') is not None and fields.get('game_level', {}).get('value') is not None
                      and fields['game_level']['value'] != preview['game_level']):
                    selected = None
                    reason = 'playing_level_differs_from_selection'
            binding = (selected or {}).get('matching', {})
            self.session = dict(session_id=str(uuid4()), started_at=utc(), status='playing',
                epoch=self.epoch, selection=selected, playing_preview=preview,
                song_id=binding.get('confirmed_song_id'),
                matching_status=binding.get('status', 'pending_manual_review'),
                pending_reason=reason or ('no_fresh_selection' if not selected else None),
                generates_play_record=False, result=None)
            context = copy.deepcopy(self.playing_context or {})
            context['captured_at'] = self.session['started_at']
            self.session['identity_context'] = context
            self.best_result = None
            self._save_session()
            self.log('session_started', session_id=self.session['session_id'], song_id=self.session['song_id'])
            # Consume selection: next play cannot reuse this round's identity.
            self.invalidate_selection('selection_consumed_for_play', clear_pack=False)
        elif wanted in ('RESULT', 'GAME_OVER'):
            if self.session and self.session['status'] == 'playing':
                temporal = (observation.get('result_ocr') or {}).get('temporal_validation') or {}
                if (self.best_result and self.best_result[0]['frame_hash'] != observation['frame_hash']
                        and temporal.get('status') != 'confirmed'):
                    self.interrupt('result_became_unreliable')
                else:
                    self._finish_result(observation, image)
                self.invalidate_selection('result_finished',clear_pack=False)
            else:
                self.log('unassociated_result', reason='no_active_play_session', frame_hash=observation['frame_hash'])
        elif wanted == 'SPECTATING':
            self.interrupt('spectating_is_not_local_play')
            self.state = 'SPECTATING'
        elif (self.session and (old == 'PLAYING' or self.best_result)
              and wanted in ('LOBBY', 'WAITING_ROOM', 'LOGIN', 'SONG_SELECT')):
            # Quit/disconnect/room return without a result must not become a game failure.
            self.interrupt('result_left_before_confirmation' if self.best_result else 'left_gameplay_without_result')
        if wanted in ('LOGIN', 'LOBBY'):
            self.invalidate_selection('left_song_context')

    def _finish_result(self, observation, image, recovery_reason=None):
        filename = None
        if image is not None:
            filename = 'result.png'
            image.save(self._session_directory()/filename)
        self.session.update(status='pending_result', ended_at=utc(), result=dict(
            state=observation['state'], completion_observation=observation.get('context'),
            frame_hash=observation['frame_hash'], screenshot=filename,
            processing_status='pending_score_ocr',
        ))
        if recovery_reason:
            self.session['result']['recovery_reason'] = recovery_reason
        self._save_session()
        self.last_session = copy.deepcopy(self.session)
        self.last_result_observation = copy.deepcopy(observation)
        self.last_result = dict(session_id=self.session['session_id'], screenshot=filename,
                                state=observation['state'], captured_at=utc())
        self.log('result_saved', **self.last_result)
        self.session = None
        self.best_result = None

    def _session_directory(self):
        directory = self.directory/'sessions'/self.session['session_id']
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _save_session(self):
        atomic_json(self._session_directory()/'session.json', self.session)

    def snapshot(self):
        return copy.deepcopy(dict(state=self.state, proposed_state=self.proposed, confirmation_count=self.count,
            pack=self.current_pack, source_type=self.current_source, selection=self.selection,
            pack_confidence='CONFIRMED' if self.current_pack else ('CANDIDATE' if any(
                r.get('candidates') for r in self.pack_candidates) else 'UNKNOWN'),
            pack_candidates=self.pack_candidates,pack_list_visible=self.pack_list_visible,
            session=self.session, last_session=self.last_session, last_result=self.last_result,
            room_preview=self.room_preview,room_is_host=self.last_room_role,diagnostics=self.events[-50:]))


def reference_key(fields, pack_id, source_type):
    keys = ['title', 'difficulty', 'game_level', 'total_notes', 'duration_seconds']
    return fingerprint(dict(values={k: fields[k]['value'] for k in keys}, pack_id=pack_id, source_type=source_type))


class ManualReferences:
    """Explicit verified mappings in a sidecar, never in the production catalog."""
    def __init__(self, directory, database=None):
        self.path = Path(directory)/'manual-references.json'
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.ambiguous = set()
        if database is not None:
            self._load_review_mappings(Path(directory), Path(database).resolve())

    def _load_review_mappings(self, directory, database):
        """Read only fully reviewed, intact evidence. No migration of old drafts."""
        import hashlib
        for path in (directory/'sessions').glob('*/result-review.json'):
            try:
                if path.is_symlink() or path.parent.is_symlink():continue
                row=json.loads(path.read_text(encoding='utf8'))
                from .note_evidence import reviewed_reference
                ref=row.get('reusable_mapping') or reviewed_reference(row)
                confirmation=row.get('identity_confirmation') or {}
                if not ref or row.get('status')!='confirmed_manual' or row.get('catalog_path')!=str(database):continue
                if confirmation.get('source')!='explicit_user_review' or ref['song_id']!=confirmation.get('song_id'):continue
                identity=confirmation.get('song_identity') or {}
                if any(identity.get(k)!=ref.get(k) for k in ('song_id','root_id','relative_path','pack_id','source_type')):continue
                if ref['review_id']!=row['review_id'] or ref['screenshot_sha256']!=row['screenshot_sha256']:continue
                if any(row['draft'].get(k) is not True for k in ('identity_reviewed','duration_verified','player_verified')):continue
                if any(ref['values'][k]!=row['draft']['values'].get(k) for k in ('difficulty','game_level','total_notes','duration_seconds')):continue
                source=path.parent/'result.png'
                if source.is_symlink() or (path.parent/'session.json').is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest()!=row['screenshot_sha256']:continue
                if json.loads((path.parent/'session.json').read_text(encoding='utf8'))!=row['locked_session']:continue
                fields={k:dict(value=v,confidence=1) for k,v in ref['values'].items()}
                key=reference_key(fields, ref['pack_id'], ref['source_type'])
                existing=self.data.get(key)
                if key in self.ambiguous or (existing and existing['song_id']!=ref['song_id']):
                    self.ambiguous.add(key)
                    if existing:self.data[key+':'+str(existing['song_id'])]=existing
                    self.data[key+':'+str(ref['song_id'])]=ref
                    self.data.pop(key,None)
                elif existing and any(existing.get(k)!=ref.get(k) for k in ('root_id','relative_path')):
                    self.ambiguous.add(key)
                elif not existing:self.data[key]=ref
            except (OSError,ValueError,KeyError,TypeError):
                continue

    def add(self, fields, song, frame_hash):
        key = reference_key(fields, song['pack_id'], song['source_type'])
        row = dict(song_id=song['song_id'], root_id=song['root_id'], relative_path=song['relative_folder'],
                   pack_id=song['pack_id'], source_type=song['source_type'],
                   values={k: v['value'] for k, v in fields.items()}, confirmed_at=utc(),
                   confirmation_source='explicit_user_review', frame_hash=frame_hash)
        previous = self.data.get(key)
        if previous and previous['song_id'] != song['song_id']:
            raise ValueError('此組完整證據已有不同的人工對應，不能覆蓋。')
        if not previous:
            self.data[key] = row
            atomic_json(self.path, self.data)
        return previous or row

    def lookup(self, fields, pack_id, source_type, songs):
        required = ['title', 'difficulty', 'game_level', 'total_notes', 'duration_seconds']
        if any(fields.get(k, {}).get('value') is None or fields[k].get('confidence', 0) < .9 for k in required):
            return None
        key = reference_key(fields, pack_id, source_type)
        if key in self.ambiguous:return None
        row = self.data.get(key)
        if not row:
            return None
        song = songs.get(row['song_id'])
        if not song or song['root_id'] != row['root_id'] or song['relative_folder'] != row['relative_path'] or song['pack_id']!=pack_id or song['source_type']!=source_type:
            return None
        return copy.deepcopy(row)

    def safe_lookup(self, fields, pack_id, source_type, catalog, matching):
        if source_type=='custom' and pack_id is None:return None
        row=self.lookup(fields,pack_id,source_type,catalog.songs)
        if row and any(c['song_id']==row['song_id'] and c.get('conflicts') for c in matching['candidates']):return None
        others_known=all(any(ref['song_id']==c['song_id'] and ref['values'].get('difficulty')==
            fields.get('difficulty',{}).get('value') for ref in self.data.values()) for c in matching['candidates'])
        same=[r for r in self.data.values() if r['pack_id']==pack_id and r['source_type']==source_type and
              all(r['values'].get(k)==fields.get(k,{}).get('value') for k in
                  ('title','difficulty','game_level','total_notes','duration_seconds'))]
        return row if row and others_known and len(same)==1 and row['song_id'] in {c['song_id'] for c in matching['candidates']} else None
