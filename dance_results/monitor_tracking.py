"""Debounced state/selection/session tracking. No SQLite writes or score rules."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from .ocr_matching import normalize
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
        self.current_source = 'unknown'
        self.selection = None
        self.selection_count = 0
        self.selection_key = None
        self.session = None
        self.last_session = None
        self.last_result = None
        self.events = []
        self.epoch = 0
        self.room_preview = None
        self.last_room_role = None
        self.proposed_pack=None
        self.pack_count=0
        self.pack_candidates=[]
        self.pack_list_visible=False

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
        self.selection = None
        self.selection_key = None
        self.selection_count = 0
        if clear_pack:
            self.current_pack = None
            self.current_source = 'unknown'
            self.proposed_pack=None
            self.pack_count=0
            self.pack_candidates=[]
            self.pack_list_visible=False
        self.log('selection_invalidated', reason=reason)

    def interrupt(self, reason):
        if self.session and self.session['status'] == 'playing':
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
        self.room_preview=None
        self.last_room_role=None
        self.state = 'UNKNOWN'
        self.log('window_connected', identity=identity, epoch=self.epoch)

    def pack_observation(self, pack):
        """Called whenever the list is visible, BEFORE debounce or matching."""
        before = ((self.current_pack or {}).get('root_id'),(self.current_pack or {}).get('pack_id'))
        if pack and pack.get('status') == 'confirmed':
            proposed=(pack.get('root_id'),pack['pack_id'])
            if proposed==self.proposed_pack:self.pack_count+=1
            else:self.proposed_pack=proposed;self.pack_count=1
            if self.pack_count>=self.confirmations:
                self.current_pack = copy.deepcopy(pack)
                self.current_source = 'custom'
            else:
                self.current_pack=None
                self.current_source='unknown'
        else:
            self.current_pack = None
            self.current_source = 'unknown'
            self.proposed_pack=None
            self.pack_count=0
        after=((self.current_pack or {}).get('root_id'),(self.current_pack or {}).get('pack_id'))
        if before != after:
            self.invalidate_selection('pack_changed_or_uncertain', clear_pack=False)
            self.log('pack_observed', pack=self.current_pack)

    def update_selection(self, observation, now):
        # A transition can retain the song-select anchors while its identity is
        # unreadable. It neither changes the song nor refreshes its freshness.
        fields = observation['fields']

        def unreadable(key):
            field = fields.get(key) or {}
            return field.get('value') in (None, '') or bool(field.get('reason'))

        if unreadable('title'):
            return
        if any(unreadable(key) for key in SELECTION_REQUIRED):
            # Partially readable frame: keep the current selection only when it
            # shows the same title. A readable different title still replaces or
            # invalidates the old selection, so a stale song is never kept.
            current = ((self.selection or {}).get('fields') or {}).get('title') or {}
            if normalize(current.get('value')) == normalize(fields['title']['value']):
                return
        payload = dict(fields=observation['fields'], pack=copy.deepcopy(self.current_pack),
                       source_type=observation.get('source_type', self.current_source),
                       matching=copy.deepcopy(observation['matching']), frame_hash=observation['frame_hash'])
        if 'ocr_fields' in observation:
            payload['ocr_fields'] = copy.deepcopy(observation['ocr_fields'])
        for key in ('raw_ocr_fields','numeric_roi_evidence'):
            if key in observation:payload[key]=copy.deepcopy(observation[key])
        # Confidence boxes and background pixels are not song identity.
        key = fingerprint(dict(values={k: v.get('value') for k, v in payload['fields'].items()},
                               pack_id=(self.current_pack or {}).get('pack_id'), source_type=payload['source_type'], epoch=self.epoch))
        manual = (self.selection or {}).get('manual_confirmation')
        if manual and manual['observed_values'] == {k: v.get('value') for k, v in payload['fields'].items()} and (
                self.current_pack or {}).get('pack_id') == manual.get('pack_id'):
            key = self.selection_key
        if key != self.selection_key:
            self.selection_key = key
            self.selection_count = 1
            self.selection = None
            self.log('selection_changed', observation_id=key)
        else:
            self.selection_count += 1
        if self.selection_count >= self.confirmations:
            previous = self.selection
            payload.update(observation_id=key, observed_at=utc(), last_seen=now, epoch=self.epoch)
            if previous and previous.get('manual_confirmation'):
                payload['manual_confirmation'] = previous['manual_confirmation']
                payload['matching'] = previous['matching']
                payload['fields'] = previous['fields']
            self.selection = payload

    def consume(self, observation, now=None, image=None, sample_id=None):
        now = time.monotonic() if now is None else now
        if sample_id is not None and sample_id == self.last_sample:
            return
        self.last_sample = sample_id
        wanted = observation['state']
        if wanted == 'SONG_SELECT':
            # A readable new identity invalidates the prior one before debounce.
            self.update_selection(observation, now)
        if wanted != self.proposed:
            self.proposed = wanted
            self.count = 1
        else:
            self.count += 1
        if self.count < self.confirmations:
            return
        if wanted=='WAITING_ROOM':
            preview=observation.get('room_preview',{})
            self.room_preview=preview
            role=observation.get('context',{}).get('local_is_host')
            if (role is False and self.last_room_role is not False) or (role is None and self.last_room_role is True):
                self.invalidate_selection('multiplayer_guest_source_not_confirmed')
            self.last_room_role=role
            if self.selection and any(preview.get(k) is not None and
                    (normalize(preview[k])!=normalize(self.selection['fields'][k]['value']) if k=='title'
                     else preview[k]!=self.selection['fields'][k]['value']) for k in ['title','difficulty','game_level']):
                self.invalidate_selection('room_preview_changed',clear_pack=False)
        if wanted == self.state:
            return
        old = self.state
        self.state = wanted
        self.log('state_transition', previous=old, current=wanted)
        if wanted=='UNKNOWN' and not self.session:
            self.invalidate_selection('unknown_screen_breaks_selection_context')
        if wanted == 'PLAYING' and not self.session:
            selected = copy.deepcopy(self.selection)
            reason = None
            if selected and (now-selected['last_seen'] > self.selection_ttl or selected['epoch'] != self.epoch):
                selected = None
                reason = 'selection_expired'
            preview = observation.get('playing_preview', {})
            if selected:
                fields = selected['fields']
                if preview.get('title') and normalize(preview['title']) != normalize(fields['title']['value']):
                    selected = None
                    reason = 'playing_title_differs_from_selection'
                elif preview.get('game_level') is not None and fields['game_level']['value'] != preview['game_level']:
                    selected = None
                    reason = 'playing_level_differs_from_selection'
            binding = (selected or {}).get('matching', {})
            self.session = dict(session_id=str(uuid4()), started_at=utc(), status='playing',
                epoch=self.epoch, selection=selected, playing_preview=preview,
                song_id=binding.get('confirmed_song_id'),
                matching_status=binding.get('status', 'pending_manual_review'),
                pending_reason=reason or ('no_fresh_selection' if not selected else None),
                generates_play_record=False, result=None)
            self._save_session()
            self.log('session_started', session_id=self.session['session_id'], song_id=self.session['song_id'])
            # Consume selection: next play cannot reuse this round's identity.
            self.invalidate_selection('selection_consumed_for_play', clear_pack=False)
        elif wanted in ('RESULT', 'GAME_OVER'):
            if self.session and self.session['status'] == 'playing':
                filename = None
                if image is not None:
                    filename = 'result.png'
                    image.save(self._session_directory()/filename)
                self.session.update(status='pending_result', ended_at=utc(),
                    result=dict(state=wanted, completion_observation=observation.get('context'),
                                frame_hash=observation['frame_hash'], screenshot=filename,
                                processing_status='pending_score_ocr'))
                self._save_session()
                self.last_session = copy.deepcopy(self.session)
                self.last_result = dict(session_id=self.session['session_id'], screenshot=filename,
                                        state=wanted, captured_at=utc())
                self.log('result_saved', **self.last_result)
                self.session = None
                self.invalidate_selection('result_finished',clear_pack=False)
            else:
                self.log('unassociated_result', reason='no_active_play_session', frame_hash=observation['frame_hash'])
        elif wanted == 'SPECTATING':
            self.interrupt('spectating_is_not_local_play')
            self.state = 'SPECTATING'
        elif old == 'PLAYING' and self.session and wanted in ('LOBBY', 'WAITING_ROOM', 'LOGIN', 'SONG_SELECT'):
            # Quit/disconnect/room return without a result must not become a game failure.
            self.interrupt('left_gameplay_without_result')
        if wanted in ('LOGIN', 'LOBBY'):
            self.invalidate_selection('left_song_context')

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
