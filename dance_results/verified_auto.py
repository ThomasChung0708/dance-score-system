"""Conservative automatic eligibility and a queued adapter to SubmissionService."""
import copy
import json
from pathlib import Path
import queue
import threading

from .library_service import LibraryError
from .record_events import RecordEventFeed
from .monitor_tracking import atomic_json,utc,ManualReferences
from .ocr_matching import ReadOnlyCatalog,normalize,same_song_title_key
from .note_review import result_time
from .result_ocr import NUMERIC
from .result_consistency import screen_catalog, screen_totals, validate, selection_is_reference
from .ocr_policy import (
    AUTO_CONFIDENCE, RESULT_REQUIRED, SELECTION_REQUIRED,
    RESULT_NONFATAL_ERRORS,
)

STORAGE_POLICY = 'round32-screen-v1'


def reliable(field):
    return isinstance(field,dict) and field.get('value') is not None and field.get('confidence',0)>=AUTO_CONFIDENCE and not field.get('reason')


def automatic_request(reviews,row):
    """No manual values or identity checkboxes can become automatic evidence."""
    from .source_choice import effective_selection
    issues=[];s=row['locked_session'];selection=effective_selection(row)
    from .ocr_temporal import issues as temporal_issues
    issues.extend(temporal_issues(row['ocr'],s,reviews.directory/'sessions'/s['session_id']))
    from .download_tracking import verified_download_binding, session_download
    binding=verified_download_binding(reviews,row)
    if binding:selection=binding['selection']
    from .room_identity import guest_context, bind_guest_official
    if guest_context(s) and not row.get('source_choice') and not binding:
        download = session_download(reviews.directory, s['session_id'])
        guest_catalog = ReadOnlyCatalog(reviews.database)
        try:
            guest = bind_guest_official(guest_catalog, s, row['ocr']) if download is None else None
        finally:
            guest_catalog.close()
        if guest:
            selection = guest
        else:
            issues.append('automatic_guest_identity_requires_picker')
    ocr=row['ocr'];raw=ocr.get('fields') or {}
    screen_values = {k: item.get('value') for k, item in raw.items()}
    if row['revision']!=0 or row['status']!='pending_review':issues.append('automatic_review_already_edited')
    from datetime import datetime
    try:
        start=datetime.fromisoformat(s['started_at'].replace('Z','+00:00'));end=datetime.fromisoformat(result_time(s)[0].replace('Z','+00:00'))
        if start.tzinfo is None or end<start:issues.append('automatic_session_time_invalid')
    except (KeyError,TypeError,ValueError,AttributeError):issues.append('automatic_session_time_invalid')
    if not (s.get('result') or {}).get('frame_hash'):issues.append('automatic_frame_evidence_missing')
    if s.get('status')!='pending_result' or not result_time(s)[0]:issues.append('automatic_complete_session_required')
    # A multiplayer footer belongs to the HOST. This warning requires using
    # the independently verified local chart below, not rejecting every own row.
    fatal_ocr=[e for e in ocr.get('errors',[]) if e not in RESULT_NONFATAL_ERRORS]
    if fatal_ocr:issues.append('automatic_ocr_errors')
    if ocr.get('local_player_status')!='CONFIRMED':issues.append('automatic_local_player_uncertain')
    result_state=(s.get('result') or {}).get('state')
    if result_state not in ('RESULT','GAME_OVER') or (result_state=='GAME_OVER' and raw.get('completion_status',{}).get('value')!='FAILED'):
        issues.append('automatic_completion_session_conflict')
    from .ocr_temporal import reliable_result_field
    for key in RESULT_REQUIRED:
        if not reliable_result_field(ocr, key):issues.append('automatic_unreliable_'+key)
    if ocr.get('multiplayer') is None:issues.append('automatic_play_mode_unknown')
    own=[r for r in reviews.player_rows(row) if normalize(r.get('name'))==normalize(ocr.get('requested_player_name'))]
    if len(own)!=1:issues.append('automatic_unique_local_row_required')
    elif any(not reliable_result_field(ocr, k, own[0]['fields'].get(k, {})) or own[0]['fields'][k]['value']!=raw.get(k,{}).get('value') for k in (*NUMERIC,'grade','completion_status')):
        issues.append('automatic_player_row_conflict')
    from .result_title_binding import bind_result_title, context_selection
    selected_fields = selection.get('ocr_fields', selection.get('fields') or {})
    if (not selected_fields.get('title', {}).get('value')
            and (context_selection(s) or selection.get('scope_authority') == 'session_source_choice')):
        with_catalog = ReadOnlyCatalog(reviews.database)
        try:
            recovered = bind_result_title(with_catalog, s, ocr, selection)
            if recovered:
                selection = recovered
        finally:
            with_catalog.close()
    fields=copy.deepcopy(selection.get('ocr_fields',selection.get('fields') or {}))
    if same_song_title_key(raw.get('title',{}).get('value'))!=same_song_title_key(fields.get('title',{}).get('value')):issues.append('automatic_result_title_conflict')
    if not ocr.get('multiplayer') and (not reliable(raw.get('game_level')) or raw['game_level']['value']!=fields.get('game_level',{}).get('value')):
        issues.append('automatic_result_level_conflict')
    catalog=ReadOnlyCatalog(reviews.database)
    try:
        source = selection.get('source_type')
        from .chart_changes import official_fields, official_context
        official_observations = official_fields(selection, ocr, s.get('playing_preview'))
        if source not in ('custom', 'official'):
            from .ocr_matching import result_identity_fields
            fields = official_observations
            resolved = catalog.match_identity(fields)
            resolved_sid = resolved.get('confirmed_song_id')
            if resolved_sid is not None and (catalog.songs[resolved_sid]['source_type'] == 'custom'
                    or resolved.get('resolved_source_type') == 'official'):
                source = catalog.songs[resolved_sid]['source_type']
        sid = None
        gd = None
        match = {}
        if source=='official':
            fields = official_observations
            match=catalog.match_official(fields,source_confirmed=True);sid=match.get('confirmed_song_id')
            if sid:gd=catalog.difficulties.get((sid,fields.get('difficulty',{}).get('value')))
        elif source=='custom':
            from .ocr_matching import result_identity_fields
            fields = result_identity_fields(selection, ocr, s.get('playing_preview'))
            pack=selection.get('pack') or {}
            confirmed_pack = pack.get('pack_id') if pack.get('status') == 'confirmed' else None
            chosen_id = binding['song_id'] if binding else (row.get('source_choice') or {}).get('song_id')
            match = catalog.match_identity(fields, confirmed_pack, 'custom',
                candidate_ids=[chosen_id] if chosen_id is not None else None)
            reference_match = catalog.match(fields, confirmed_pack, 'custom')
            ref=ManualReferences(reviews.directory,reviews.database).safe_lookup(fields,confirmed_pack,'custom',catalog,reference_match)
            sid = ref['song_id'] if ref and chosen_id is None else match.get('confirmed_song_id')
            if (binding and not any(song_id == binding['song_id'] for song_id, _ in catalog.difficulties)
                    and reliable(fields.get('title'))):
                # Stable new-folder provenance supplies guest chart identity;
                # no chart rows exist from which to infer a local LV (v11).
                sid = binding['song_id']
            if pack.get('status') != 'confirmed' and sid is None:
                issues.append('automatic_pack_unconfirmed')
            if sid:
                song=reviews._song(catalog,sid)
                gd=next((d for d in song['difficulties'] if d['difficulty']==fields.get('difficulty',{}).get('value')),None)
        else:issues.append('automatic_source_unconfirmed')
        official_result = bool(sid and source == 'official' and official_context(catalog, row, sid))
        totals = screen_totals(raw, selection)
        if official_result:
            totals = [(name, item) for name, item in totals if name == 'result']
        consistency = validate(screen_values, totals,
            selection_reference=selection_is_reference(reviews.scoring_policy, selection))
        issues.extend(consistency['issues'])
        uncharted = bool(sid and source == 'custom'
            and not any(song_id == sid for song_id, _ in catalog.difficulties))
        for key in SELECTION_REQUIRED:
            if uncharted and key == 'difficulty' and fields.get(key, {}).get('value') is None:
                continue
            if uncharted and key == 'game_level' and not binding and reliable(raw.get(key)):
                continue
            if not reliable(fields.get(key)):
                issues.append('automatic_selection_' + key + '_required')
        # Temporal title agreement grants authority only inside the bound source
        # and confirmed pack; every namesake remains a candidate.
        title_key = same_song_title_key(raw.get('title', {}).get('value'))
        title_ids = {i for name, ids in catalog.index.items()
            if title_key and same_song_title_key(name) == title_key for i in ids
            if catalog.songs[i]['source_type'] == source
            and (source != 'custom' or (selection.get('pack') or {}).get('pack_id') is None or catalog.songs[i]['pack_id'] == (selection.get('pack') or {}).get('pack_id'))}
        title_relaxed = any(not reliable(sample.get('fields', {}).get('title'))
            for sample in (ocr.get('temporal_validation') or {}).get('samples', []))
        if title_relaxed and title_ids != {sid}:
            issues.append('automatic_unique_result_title_required')
        if uncharted and ((ocr.get('multiplayer') and not binding) or screen_values.get('completion_status') != 'COMPLETED'
                or consistency['judged_total'] != screen_values.get('max_combo')):
            issues.append('automatic_uncharted_complete_combo_required')
        if sid is None:issues.append('automatic_unique_verified_identity_required')
        if selection.get('handoff') == 'single_observation' and (
                source != 'custom' or (selection.get('pack') or {}).get('status') != 'confirmed'
                or sid is None or sid != s.get('song_id')):
            issues.append('automatic_selection_single_observation_requires_review')
        if match.get('review_only'):issues.append('automatic_stylized_title_requires_review')
        if not official_result and any('total_notes' in c.get('conflicts', []) for c in match.get('candidates', [])):
            issues.append('automatic_screen_catalog_notes_conflict')
        if sid is not None and s.get('song_id') is not None and s['song_id']!=sid and not row.get('source_choice'):issues.append('automatic_locked_identity_conflict')
        if issues:return None,sorted(set(issues))
        song=catalog.songs[sid]
        values={k:copy.deepcopy(raw.get(k,{}).get('value')) for k in (*NUMERIC,'grade','completion_status','player_name','game_level')}
        values.update(difficulty=fields.get('difficulty', {}).get('value'),game_level=fields['game_level']['value'],
            total_notes=consistency['judged_total'] if screen_values.get('completion_status') == 'COMPLETED' else None,
            duration_seconds=fields.get('duration_seconds',{}).get('value'))
        if values['completion_status'] not in ('COMPLETED','FAILED'):issues.append('automatic_completion_unknown')
        if gd and gd.get('game_level') is not None and gd['game_level']!=values['game_level']:issues.append('automatic_catalog_level_conflict')
        if not official_result and screen_catalog(gd):
            for _, item in screen_totals(raw, selection):
                if item['value'] != gd['total_notes']:
                    issues.append('automatic_screen_catalog_notes_conflict')
            if values['completion_status'] == 'COMPLETED' and consistency['judged_total'] != gd['total_notes']:
                issues.append('automatic_screen_catalog_notes_conflict')
        judgments=[values[k] for k in ('perfect','cool','bad','miss')]
        if any(type(n) is not int or n<0 for n in judgments):issues.append('automatic_judgments_invalid')
        elif values['completion_status']=='COMPLETED':
            from .score_comparison import comparison
            compared=comparison(catalog.db,sid,values['difficulty'],sum(judgments),directory=reviews.directory,session_id=row['session_id'],confirmed_only=True)
            if compared['ranking_paused']:issues.append('automatic_cross_session_basis_conflict')
        if issues:return None,sorted(set(issues))
        body=dict(session_id=row['session_id'],review_id=row['review_id'],revision=row['revision'],song_id=sid,
            root_id=song['root_id'],relative_path=song['relative_folder'],values=values,
            player_row_id=own[0]['row_id'],notes_verified=values['total_notes'] is not None,identity_reviewed=True,player_verified=True,
            duration_verified=bool(fields.get('duration_seconds',{}).get('value')),confirmation_note='',
            play_mode='multiplayer' if ocr['multiplayer'] else 'single',submission_mode='VERIFIED_AUTO')
        if selection.get('result_title_binding'):
            body['identity_binding_method'] = selection['result_title_binding']['method']
        return body,[]
    finally:catalog.close()


class AutomaticStorage:
    """One worker; no historical backlog opt-in, no database writes outside submit."""
    def __init__(self,submissions,*,event_feed=None):
        self.submissions=submissions;self.reviews=submissions.reviews
        self.path=self.reviews.directory/'storage-mode.json';self.lock=threading.RLock()
        self.queue=queue.Queue();self.stop_event=threading.Event();self.queued=set();self.worker=None
        self.state=dict(mode='MANUAL',catalog_path=str(self.reviews.database),excluded_sessions=[],enabled_at=None,ocr_policy=STORAGE_POLICY)
        if self.path.exists():
            data=json.loads(self.path.read_text(encoding='utf8'))
            if data.get('catalog_path')!=str(self.reviews.database):raise LibraryError('自動儲存設定屬於其他歌曲庫。',409)
            if data.get('mode') not in ('MANUAL','VERIFIED_AUTO'):raise LibraryError('自動儲存模式設定無效。',409)
            self.state=data
            if self.state.get('ocr_policy')!=STORAGE_POLICY:
                # An OCR upgrade never turns an old Pending into a new automatic play.
                self.state['excluded_sessions']=sorted(set(self.state.get('excluded_sessions',[]))|{p.parent.name for p in (self.reviews.directory/'sessions').glob('*/session.json')})
                self.state['ocr_policy']=STORAGE_POLICY
                atomic_json(self.path,self.state)
        self.event_feed = event_feed if event_feed is not None else RecordEventFeed()
        self.diagnostics = {}
        submissions.auto_allowed=self.allowed
        self.reviews.on_ingest=self.enqueue
        if self.state['mode']=='VERIFIED_AUTO':
            for p in (self.reviews.directory/'sessions').glob('*/result-review.json'):self.enqueue(p.parent.name)

    def allowed(self,sid):
        with self.lock:
            self._refresh_state()
            return not self.stop_event.is_set() and self.state['mode']=='VERIFIED_AUTO' and sid not in self.state['excluded_sessions']

    def _refresh_state(self):
        """Disabling in another GUI process must also gate this worker."""
        try:
            data=json.loads(self.path.read_text(encoding='utf8')) if self.path.exists() else None
            if data is not None:
                if (data.get('catalog_path')!=str(self.reviews.database) or
                    data.get('mode') not in ('MANUAL','VERIFIED_AUTO') or
                    not isinstance(data.get('excluded_sessions'),list)):
                    raise ValueError('invalid storage setting')
                self.state=data
            elif self.state.get('mode')=='VERIFIED_AUTO':self.state['mode']='MANUAL'
        except (OSError,ValueError,TypeError):
            self.state['mode']='MANUAL'

    def configure(self,body):
        mode=body.get('mode')
        if mode not in ('MANUAL','VERIFIED_AUTO'):raise LibraryError('儲存模式不正確。')
        if mode=='VERIFIED_AUTO' and body.get('acknowledge') is not True:raise LibraryError('請明確確認啟用安全自動儲存。',409)
        with self.lock:
            self._refresh_state()
            if mode!=self.state['mode']:
                self.state.update(mode=mode)
                if mode=='VERIFIED_AUTO':
                    self.state.update(enabled_at=utc(),excluded_sessions=[p.parent.name for p in (self.reviews.directory/'sessions').glob('*/session.json')])
                atomic_json(self.path,self.state)
        return self.status()

    def enqueue(self,sid):
        if not self.allowed(sid):return
        self.reviews._directory(sid)
        with self.lock:
            if sid in self.queued:return
            self.queued.add(sid);self.queue.put(sid)
            if not self.worker or not self.worker.is_alive():
                self.worker=threading.Thread(target=self._run,name='Dance-verified-storage',daemon=True);self.worker.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:sid=self.queue.get(timeout=.5)
            except queue.Empty:continue
            try:
                if not self.allowed(sid):continue
                response=self.submissions.submit_verified(sid)
                if not response['idempotent']:
                    self.event_feed.publish(response['record']['record_id'])
                with self.lock:self.diagnostics[sid]=dict(status='saved',record_id=response['record']['record_id'])
            except Exception as error:
                with self.lock:self.diagnostics[sid]=dict(status='pending_review',reason=str(error))
            finally:
                with self.lock:self.queued.discard(sid)
                self.queue.task_done()

    def status(self):
        with self.lock:
            self._refresh_state()
            state = copy.deepcopy(self.state)
            diagnostics = copy.deepcopy(self.diagnostics)
        events = self.event_feed.snapshot()
        with self.submissions.library.open() as library:
            receipts=[self.submissions._record(library.connection,i,include_evidence=False) for i in events]
        for receipt in receipts:receipt['catalog_path']=str(self.reviews.database)
        return dict(mode=state['mode'],enabled_at=state['enabled_at'],events=receipts,diagnostics=diagnostics)

    def close(self):
        self.stop_event.set()
        if self.worker:self.worker.join(3)
