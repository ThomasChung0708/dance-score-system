"""Queued metadata-only download observations and reuse of the full SONG scan."""
import copy
import json
import os
from pathlib import Path
import queue
import stat
import sqlite3
from contextlib import closing
import threading
import time
from uuid import uuid4

from .monitor_tracking import atomic_json,utc,fingerprint,ManualReferences
from .scanner import is_link
from .ocr_matching import ReadOnlyCatalog,normalize,same_song_title_key


def guest_download_binding(reviews, row, evidence):
    from .room_identity import agreeing_room, field
    session = row['locked_session']
    room = agreeing_room(session, row['ocr'])
    if (not room or not room.get('custom') or not evidence.get('scan_completed')
            or evidence.get('room_key') != room.get('room_key')
            or evidence.get('epoch') != session.get('epoch')
            or not room.get('room_entry_at') or evidence.get('room_entry_at') != room['room_entry_at']):
        return None
    title_key = same_song_title_key(room['title'])
    result_title = row['ocr'].get('fields', {}).get('title', {}).get('value')
    playing_title = (session.get('playing_preview') or {}).get('title')
    if (same_song_title_key(result_title) != title_key
            or same_song_title_key(playing_title) != title_key):
        return None
    # Count ALL new folders, before filtering by title: a second download must
    # never disappear just because only one title happens to agree.
    candidates = evidence.get('candidates', [])
    if len(candidates) != 1 or evidence.get('uncertain_imports'):
        return None
    candidate = candidates[0]
    if not all(candidate.get(k) for k in ('is_new_song', 'stable_source', 'observed_after_snapshot')):
        return None
    catalog = ReadOnlyCatalog(reviews.database)
    try:
        sid = candidate['song_id']
        if sid not in catalog.title_ids(room['title'], source_type='custom'):
            return None
        song = catalog.songs[sid]
        if song['root_id'] != candidate['root_id'] or song['relative_folder'] != candidate['relative_path']:
            return None
        root = catalog.db.execute('SELECT path FROM library_roots WHERE root_id=?', (song['root_id'],)).fetchone()[0]
        live = metadata_snapshot(Path(root)/song['relative_folder'])
        if live['errors'] or not live['files'] or live['files'] != candidate.get('stable_metadata'):
            return None
        from .ocr_matching import result_identity_fields
        fields = result_identity_fields({}, row['ocr'], session.get('playing_preview'))
        title_samples = [s.get('fields', {}).get('title') or {}
                         for s in row['ocr']['temporal_validation']['samples']]
        agreeing = [f for f in title_samples if same_song_title_key(f.get('value')) == title_key
                    and not f.get('reason')]
        if agreeing:
            fields['title'] = copy.deepcopy(max(agreeing, key=lambda f: f.get('confidence', 0)))
        fields['difficulty'] = copy.deepcopy((room.get('fields') or {}).get('difficulty') or field(None, 'room_local_difficulty_setting'))
        # The footer/room LV belongs to the host. Only independently measured
        # local PLAYING evidence may fill the v11 mandatory result LV.
        local_level = ((session.get('playing_preview') or {}).get('fields') or {}).get('game_level') or {}
        fields['game_level'] = copy.deepcopy(local_level) if local_level.get('confidence', 0) >= .95 and not local_level.get('reason') else field(None, 'guest_host_level_not_local')
        method = 'guest_download_stable_new_folder_title'
        selection = dict(source_type='custom', fields=fields, ocr_fields=copy.deepcopy(fields),
            download_song_id=sid, result_title_binding=dict(method=method, song_id=sid,
                room=copy.deepcopy(room), episode_id=evidence['episode_id']))
        return dict(song_id=sid, selection=selection, episode_id=evidence['episode_id'], method=method)
    finally:
        catalog.close()


def prefill_download(reviews, row):
    if row['revision'] != 0 or row['status'] != 'pending_review':
        return False
    binding = verified_download_binding(reviews, row)
    if not binding:
        return False
    draft = row['draft']
    before = copy.deepcopy(draft)
    draft.update(song_id=binding['song_id'], identity_binding_method=binding['method'])
    for key in ('difficulty', 'game_level', 'total_notes'):
        draft['values'][key] = binding['selection']['fields'].get(key, {}).get('value')
    return draft != before


def metadata_snapshot(root):
    start=time.perf_counter();root=Path(root);files={};folders=[];errors=[]
    if not root.is_dir() or is_link(root):return dict(files={},folders=[],errors=['root_unavailable_or_link'],milliseconds=0)
    pending=[root]
    while pending:
        base=pending.pop();folders.append(str(base.relative_to(root)))
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    try:
                        # DirEntry reuses Windows directory metadata. Avoid two
                        # extra system calls per file; never follow reparse points.
                        s=entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(s.st_mode) or getattr(s,'st_file_attributes',0)&0x400:continue
                        p=Path(entry.path)
                        if stat.S_ISDIR(s.st_mode):
                            if entry.name not in ('.git','__pycache__'):pending.append(p)
                        elif stat.S_ISREG(s.st_mode):
                            rel=str(p.relative_to(root));ext=p.suffix.lower().lstrip('.')
                            files[rel]=dict(size=s.st_size,mtime_ns=s.st_mtime_ns,source_format=ext if ext in ('sm','dwi','osu') else None)
                    except OSError as e:errors.append(str(e))
        except OSError as e:errors.append(str(e))
    return dict(files=files,folders=sorted(folders),errors=errors,
        fingerprint=fingerprint(files),milliseconds=round((time.perf_counter()-start)*1000,3),content_hashes_computed=0)


def session_download(directory,sid):
    p=Path(directory)/'sessions'/str(sid)/'download-evidence.json'
    try:return json.loads(p.read_text(encoding='utf8'))
    except (OSError,ValueError):return None


def related_new_songs(evidence,row=None):
    """UI shortlist only; title relevance never establishes chart identity."""
    if not evidence or evidence.get('candidate_scope')!='observed_new_song_ids_v2':return []
    s=(row or {}).get('locked_session') or {};selection=s.get('selection') or {}
    titles=[((row or {}).get('ocr') or {}).get('fields',{}).get('title',{}).get('value'),
            selection.get('fields',{}).get('title',{}).get('value'),
            (s.get('playing_preview') or {}).get('title'),(evidence.get('room_observation') or {}).get('title')]
    # Result/locked song evidence takes precedence over a possibly old room title.
    titles=[same_song_title_key(t) for t in titles if t]
    title=titles[0] if titles else None
    return [c for c in evidence.get('candidates',[]) if c.get('is_new_song') is True and
            c.get('observed_after_snapshot') is True and title and
            title in {same_song_title_key(t) for t in [c.get('title'),*c.get('source_titles',[])] if t}]


def download_receipt(database,sid):
    if not sid:return None
    from .songs import record_display_title
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        r = db.execute(
            "SELECT r.record_id,COALESCE(d.song_id,json_extract(r.evidence_json,'$.song.song_id')) AS song_id,r.evidence_json,ps.display_song_title "
            'FROM play_records r LEFT JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id '
            'JOIN play_sessions ps ON ps.session_id=r.session_id WHERE r.session_id=?', (sid,)).fetchone()
        if r is None:
            return None
        return dict(record_id=r['record_id'], song_id=r['song_id'],
                    title=record_display_title(db, json.loads(r['evidence_json']),
                                               r['song_id'], r['display_song_title']))


def download_view(evidence,row=None,receipt=None):
    """Small public projection. Full provenance stays in episode/Session logs."""
    if not evidence:return None
    modern=evidence.get('candidate_scope')=='observed_new_song_ids_v2'
    new=evidence.get('candidates',[]) if modern else []
    related=related_new_songs(evidence,row)
    matched_id=evidence.get('confirmed_song_id') if modern else None
    matched=next((c for c in [*new,*evidence.get('existing_updates',[])] if c['song_id']==matched_id),None)
    draft=(row or {}).get('draft') or {};confirmation=(row or {}).get('identity_confirmation') or {}
    if ((row or {}).get('status')=='confirmed_manual' and draft.get('identity_reviewed') is True and
        confirmation.get('source')=='explicit_user_review' and confirmation.get('confirmed_at') and
        type(draft.get('song_id')) is int and confirmation.get('song_id')==draft['song_id'] and
        confirmation.get('values')==draft.get('values')):
        matched=next((c for c in new if c['song_id']==draft['song_id']),dict(song_id=draft['song_id'],title=None))
    if receipt:matched=dict(song_id=receipt['song_id'],title=receipt['title'])
    status=evidence.get('status','idle');completed=bool(evidence.get('scan_completed'))
    return dict(episode_id=evidence.get('episode_id'),session_id=evidence.get('session_id'),status=status,scan_completed=completed,
        no_map_detected=evidence.get('self_no_map_confirmed'),
        download_tracking=status not in ('idle','armed','confirmed','pending_identity') and not completed,
        progress_observed=any(e['event']=='SELF_DOWNLOAD_PROGRESS_OBSERVED' for e in evidence.get('events',[])),
        scanning=status=='scanning',new_song_count=len(new),matched_song=dict(song_id=matched['song_id'],title=matched.get('title')) if matched else None,
        needs_manual_review=bool(evidence.get('result_seen') and not receipt),formal_saved=receipt is not None,record_id=receipt['record_id'] if receipt else None,
        acquisition_uncertain=(bool(evidence.get('uncertain_imports')) or completed and bool(evidence.get('self_no_map_confirmed')) and
            not any(c.get('observed_after_snapshot') for c in [*new,*evidence.get('existing_updates',[])])) if modern else bool(evidence.get('candidates')),
        related_new_count=len(related),candidates=[{k:c.get(k) for k in ('song_id','title','pack','relative_path','root_id')} for c in related])


def verified_download_binding(reviews,row):
    evidence=session_download(reviews.directory,row['session_id'])
    if not evidence or evidence.get('candidate_scope')!='observed_new_song_ids_v2':return None
    from .room_identity import guest_context
    if guest_context(row['locked_session']):
        return guest_download_binding(reviews, row, evidence)
    if not evidence.get('scan_completed') or not evidence.get('self_no_map_confirmed'):return None
    selection=copy.deepcopy(row['locked_session'].get('selection') or {})
    pack=selection.get('pack') or {};fields=selection.get('ocr_fields',selection.get('fields') or {})
    if pack.get('status')!='confirmed':return None
    if any(fields.get(k,{}).get('value') is None or fields[k].get('confidence',0)<.95 or fields[k].get('reason') for k in ('title','difficulty','game_level','total_notes','duration_seconds')):return None
    catalog=ReadOnlyCatalog(reviews.database)
    try:
        match=catalog.match(fields,pack.get('pack_id'),'custom')
        ref=ManualReferences(reviews.directory,reviews.database).safe_lookup(fields,pack.get('pack_id'),'custom',catalog,match)
        if ref:sid=ref['song_id']
        elif match['status']=='unique_verified_candidate' and len(match['candidates'])==1:sid=match['candidates'][0]['song_id']
        else:return None
        candidates=[c for c in [*evidence.get('candidates',[]),*evidence.get('existing_updates',[])]
                    if c['song_id']==sid and c.get('stable_source') and c.get('observed_after_snapshot')]
        if len(candidates)!=1:return None
        source=catalog.songs[sid]
        root=catalog.db.execute('SELECT path FROM library_roots WHERE root_id=?',(source['root_id'],)).fetchone()[0]
        live=metadata_snapshot(Path(root)/source['relative_folder'])
        if live['errors'] or live['files']!=candidates[0].get('stable_metadata'):return None
        if source['root_id']!=candidates[0]['root_id'] or source['relative_folder']!=candidates[0]['relative_path']:return None
        selection.update(source_type='custom',fields=fields,matching=dict(match,confirmed_song_id=sid))
        return dict(song_id=sid,selection=selection,episode_id=evidence['episode_id'],
            method='download_path_plus_independent_verified_chart_match')
    finally:catalog.close()


class DownloadTracker:
    def __init__(self,library,reviews,*,interval=5,quiet_seconds=2,on_update=None):
        self.library=library;self.reviews=reviews;self.directory=reviews.directory
        self.interval=interval;self.quiet_seconds=quiet_seconds;self.on_update=on_update
        self.lock=threading.RLock();self.queue=queue.Queue();self.stop_event=threading.Event()
        self.thread=None;self.active=None;self.armed={};self.last_key=None;self.last_poll=0
        self.associated=set();self.results=set();self.last_lobby=None
        self.generation=0;self.enabled=False;self.view=dict(status='idle',events=[],metrics={})
        self.room_scope = None
        self.room_baseline = None
        self.room_poll_at = 0

    def room_tick(self, tracker):
        """Capture-thread bridge; all filesystem work stays on our worker."""
        room = tracker.room_preview
        if (tracker.state != 'WAITING_ROOM' and tracker.proposed != 'WAITING_ROOM') or not tracker.room_key:
            return
        scope = dict(room_key=list(tracker.room_key), room_entry_at=tracker.room_entry_at,
                     epoch=tracker.epoch, room=copy.deepcopy(room))
        if not self.room_scope or scope['room_entry_at'] != self.room_scope['room_entry_at']:
            self.room_scope = scope
            self._enqueue('room_enter', copy.deepcopy(scope))
        else:
            # Better OCR of the same room must not discard its entry snapshot.
            self.room_scope = scope
        now = time.monotonic()
        if now-self.room_poll_at >= self.interval:
            self.room_poll_at = now
            self._enqueue('room_poll', copy.deepcopy(scope))
        with self.lock:
            view = dict(self.view)
        if (view.get('room_entry_at') == tracker.room_entry_at
                and view.get('room_key') == list(tracker.room_key)
                and (view.get('file_changes') or view.get('metadata_change_observed'))):
            tracker.custom_cue('download_folder_changed', frames=1)

    def _room_poll(self, scope):
        scope_keys = ('epoch', 'room_entry_at', 'room_key')
        if (self.room_baseline is None or not self.room_scope
                or any(self.room_scope.get(k) != scope.get(k) for k in scope_keys)):
            return
        current = self._snapshots()
        changed = any(item['snapshot']['files'] != self.room_baseline.get(rid, {}).get('snapshot', {}).get('files')
                      for rid, item in current.items())
        if not changed:
            return
        if not self.active or self.active.get('room_entry_at') != scope['room_entry_at']:
            self._begin(dict(observed_at=utc(), lobby=self.last_lobby or {}, room=scope['room'],
                epoch=scope['epoch'], baseline=self.room_baseline, self_no_map_confirmed=False))
        if not self.active.get('session_id'):
            self.active.update(metadata_change_observed=True, room_key=scope['room_key'],
                               room_entry_at=scope['room_entry_at'])
            self._save()

    def _enqueue(self,kind,data=None):
        if self.stop_event.is_set():return
        self.queue.put((kind,data))
        if not self.thread or not self.thread.is_alive():
            self.thread=threading.Thread(target=self._run,name='Dance-download-metadata',daemon=True);self.thread.start()

    def arm(self):self.enabled=True;self._enqueue('arm')

    def pause(self):self.enabled=False

    def observe(self,observation,tracker,image=None):
        self.room_tick(tracker)
        state=observation['state'];lobby=observation.get('download_lobby') or {}
        if state=='WAITING_ROOM' and tracker.count>=tracker.confirmations:
            self.last_lobby=copy.deepcopy(lobby)
            key=fingerprint(dict(epoch=tracker.epoch,player=lobby.get('player_name'),slot=lobby.get('slot'),room=observation.get('room_preview')))
            if lobby.get('self_no_map') and lobby.get('multiplayer_observed') and lobby.get('local_player_status')=='CONFIRMED' and key!=self.last_key:
                self.last_key=key;self._enqueue('no_map',dict(lobby=lobby,room=observation.get('room_preview'),epoch=tracker.epoch,image=None,observed_at=utc(),key=key))
            elif self.active and (lobby.get('self_ready') or lobby.get('progress_observed')):
                self._enqueue('lobby_status',lobby)
        session=tracker.session if state=='PLAYING' else tracker.last_session if state in ('RESULT','GAME_OVER') else None
        if session:
            sid=session['session_id'];is_result=bool(session.get('result'))
            if sid not in self.associated or is_result and sid not in self.results:
                self.associated.add(sid)
                if is_result:self.results.add(sid)
                self._enqueue('associate',copy.deepcopy(session))
            if state=='PLAYING':self.last_key=None
        now=time.monotonic()
        if self.active and now-self.last_poll>=self.interval:
            self.last_poll=now;self._enqueue('poll')
        if state in ('LOGIN','LOBBY','INTERRUPTED'):self.last_key=None

    def status(self):
        # _save replaces the immutable diagnostic snapshot. Polling the GUI
        # must not deep-copy every old SongFolder's metadata before projecting.
        with self.lock:view=dict(self.view)
        return download_view(view,receipt=download_receipt(self.library.path,view.get('session_id')))

    def retry(self,sid):self._enqueue('retry',sid)

    def _event(self,name,**extra):
        a=self.active
        if not a:return
        if any(e['event']==name and e.get('key')==extra.get('key') for e in a['events']):return False
        a['events'].append(dict(event=name,at=utc(),**extra));a['events']=a['events'][-80:]
        return True

    def _save(self):
        if not self.active:return
        a=self.active;public={k:v for k,v in a.items() if not k.startswith('_')}
        atomic_json(self.directory/'downloads'/a['episode_id']/'evidence.json',public)
        if a.get('session_id'):atomic_json(self.directory/'sessions'/a['session_id']/'download-evidence.json',public)
        with self.lock:self.view=copy.deepcopy(public)

    def _roots(self):return [r for r in self.library.bootstrap()['roots'] if r.get('root_kind')=='filesystem']

    def _snapshots(self):return {str(r['root_id']):dict(path=r['path'],snapshot=metadata_snapshot(r['path'])) for r in self._roots()}

    def _begin(self,data):
        # A new selection supersedes an unassociated lobby episode, never a
        # captured result. Result evidence remains on the old Session sidecar.
        self.active=dict(episode_id=str(uuid4()),session_id=None,status='observed_no_map',
            first_observed_at=data['observed_at'],self_no_map_confirmed=data.get('self_no_map_confirmed',True),player=data['lobby'].get('player_name'),
            room_observation=data['room'],lobby_observation=data['lobby'],epoch=data['epoch'],
            baseline_may_follow_download_start=True,_baseline=data.get('baseline') or self.room_baseline or self._snapshots(),events=[],candidates=[],
            candidate_scope='observed_new_song_ids_v2',existing_updates=[],uncertain_imports=[],
            scan_completed=False,result_seen=False,attempts=0,_session=None,_last_stamp=None,_last_change=time.monotonic(),_scanned=None)
        a=self.active
        if (self.room_scope and same_song_title_key((data.get('room') or {}).get('title'))
                == self.room_scope['room_key'][0]):
            a.update(room_key=self.room_scope['room_key'], room_entry_at=self.room_scope['room_entry_at'])
        with self.library.open() as lib:catalog_ids=[r[0] for r in lib.connection.execute('SELECT song_id FROM songs')]
        atomic_json(self.directory/'downloads'/a['episode_id']/'catalog-before-observation.json',catalog_ids)
        snapshot_path=self.directory/'downloads'/a['episode_id']/'baseline.json'
        atomic_json(snapshot_path,a['_baseline'])
        a.update(baseline_path=str(snapshot_path.resolve()),baseline_metadata_fingerprint=fingerprint(a['_baseline']),baseline_root_count=len(a['_baseline']))
        image=data.get('image')
        if image is not None:
            p=self.directory/'downloads'/self.active['episode_id']/'self-no-map.png';p.parent.mkdir(parents=True,exist_ok=True);image.save(p)
            self.active['observation_screenshot']=str(p.resolve())
        self._event('MULTIPLAYER_LOBBY')
        if a['self_no_map_confirmed']:self._event('SELF_NO_MAP')
        else:a['trigger']='metadata_change_only_identity_unconfirmed'
        self._save()

    def _associate(self,s):
        room = (s.get('identity_context') or {}).get('guest_room') or {}
        if (self.active and room.get('room_entry_at')
                and self.active.get('room_entry_at') != room['room_entry_at']):
            return
        if not self.active or self.active.get('session_id') not in (None,s['session_id']):
            if not self.last_lobby or not self.last_lobby.get('multiplayer_observed') or not self.armed:return
            current=self._snapshots()
            if fingerprint({k:v['snapshot'].get('fingerprint') for k,v in current.items()})==fingerprint({k:v['snapshot'].get('fingerprint') for k,v in self.armed.items()}):return
            self._begin(dict(observed_at=utc(),lobby=self.last_lobby,room={},epoch=s.get('epoch'),image=None,
                baseline=self.armed,self_no_map_confirmed=False))
        if self.active.get('session_id') not in (None,s['session_id']):return
        self.active.update(session_id=s['session_id'],_session=s,result_seen=bool(s.get('result')))
        if s.get('result'):self.active['_result_deadline']=time.monotonic()+120
        self.active['status']='waiting_stable_files';self._save();self._notify()

    def _notify(self):
        a=self.active
        if a and a.get('result_seen') and a.get('scan_completed') and a.get('_notified')!=a.get('_scanned') and (self.directory/'sessions'/a['session_id']/'result-review.json').exists():
            a['_notified']=a['_scanned']
            if self.on_update:self.on_update(a['session_id'])

    def _poll(self):
        self._notify()
        a=self.active
        if not a or not a.get('session_id') or a['attempts']>=4:return
        if a.get('_result_deadline') and time.monotonic()>a['_result_deadline'] and not a.get('_manual_retry'):return
        current=self._snapshots();stamp=fingerprint({k:v['snapshot'].get('fingerprint') for k,v in current.items()})
        if any(v['snapshot']['errors'] for v in current.values()):a['status']='metadata_error_retry_pending';self._save();return
        if stamp!=a['_last_stamp']:
            a.pop('confirmed_song_id',None)
            a.update(_last_stamp=stamp,_last_change=time.monotonic(),status='waiting_stable_files');self._save();return
        if time.monotonic()-a['_last_change']<self.quiet_seconds or stamp==a['_scanned']:return
        changes={};deferred_by_root={}
        with self.library.open() as lib:
            db=lib.connection
            for root_id,item in current.items():
                baseline=a['_baseline'].get(root_id,{}).get('snapshot',{}).get('files',{});files=item['snapshot']['files']
                armed=self.armed.get(root_id,{}).get('snapshot',{}).get('files')
                paths=[p for p,meta in files.items() if baseline.get(p)!=meta or armed is not None and armed.get(p)!=meta]
                changed_folders={str(Path(*Path(p).parts[:2])) if len(Path(p).parts)>=3 else str(Path(p).parent) for p in paths}
                observed_changed={str(Path(*Path(p).parts[:2])) if len(Path(p).parts)>=3 else str(Path(p).parent)
                    for p in paths if baseline.get(p)!=files[p] or armed is not None and armed.get(p)!=files[p]}
                observed_sources={str(Path(*Path(p).parts[:2])) if len(Path(p).parts)>=3 else str(Path(p).parent)
                    for p in paths if files[p]['source_format'] and baseline.get(p)!=files[p]}
                changes[root_id]=dict(changed_paths=paths,folders=sorted(changed_folders),observed_changed_folders=sorted(observed_changed),
                    observed_source_folders=sorted(observed_sources),metadata_only=True)
            if '_scan_before_ids' not in a:
                catalog_path=self.directory/'downloads'/a['episode_id']/'catalog-before-scan.json'
                if catalog_path.exists():ids=json.loads(catalog_path.read_text(encoding='utf8'))
                else:
                    ids=[r[0] for r in db.execute('SELECT song_id FROM songs')];atomic_json(catalog_path,ids)
                a['_scan_before_ids']=set(ids)
            if '_sources_before' not in a:
                source_path=self.directory/'downloads'/a['episode_id']/'sources-before-scan.json'
                if source_path.exists():sources=json.loads(source_path.read_text(encoding='utf8'))
                else:
                    sources={str(r['source_id']):dict(r) for r in db.execute('SELECT source_id,song_id,relative_path,latest_sha256 FROM song_sources')}
                    atomic_json(source_path,sources)
                a['_sources_before']=sources
        a['file_changes']=changes;a['latest_snapshot_metrics']={k:v['snapshot']['milliseconds'] for k,v in current.items()}
        self._event('DOWNLOAD_CANDIDATES_FOUND',key=stamp)
        a['status']='scanning';a['attempts']+=1;self._save()
        for root_id,item in current.items():
            if self.stop_event.is_set():return
            try:
                status=self.library.scan_status()
                reuse=status.get('status')=='running' and status.get('root_id')==int(root_id) and status.get('defer_not_ready') is True
                if not reuse:
                    while status.get('status')=='running' and not self.stop_event.wait(.1):status=self.library.scan_status()
                if self.stop_event.is_set():return
                job=status if reuse else self.library.start_scan(int(root_id),defer_not_ready=True)
            except Exception as error:
                a['attempts']=max(0,a['attempts']-1)
                a.update(status='scan_busy_or_failed_retry_pending',scan_error=str(error));self._save();return
            started=time.perf_counter()
            while not self.stop_event.wait(.1):
                status=self.library.scan_status()
                if status['status']!='running':break
            if self.stop_event.is_set():return
            a.setdefault('scan_results',[]).append(dict(status,root_id=int(root_id),elapsed_ms=round((time.perf_counter()-started)*1000,2)))
            if status.get('scan_id'):
                with self.library.open() as lib:
                    report=lib.connection.execute('SELECT report_json FROM scan_runs WHERE scan_id=?',(status['scan_id'],)).fetchone()
                    if report:atomic_json(self.directory/'downloads'/a['episode_id']/f"scan-{status['scan_id']}.json",json.loads(report[0]))
            deferred=set(status.get('deferred_folders',[]))
            deferred_by_root[root_id]=deferred
            changed_deferred=sorted(deferred & set(changes[root_id]['observed_changed_folders']))
            if status['status']!='completed' or changed_deferred or status.get('deferred_sources') and 'deferred_folders' not in status:
                a['pending_folders']=changed_deferred
                a.update(status='scan_deferred_or_failed_retry_pending');self._save();return
        # Do not accept a candidate if any metadata changed during the scan.
        after=self._snapshots()
        if stamp!=fingerprint({k:v['snapshot'].get('fingerprint') for k,v in after.items()}):
            a['status']='changed_during_scan_retry_pending';self._save();return
        catalog=ReadOnlyCatalog(self.library.path)
        try:
            source_changes=[]
            for source in catalog.db.execute('SELECT source_id,song_id,relative_path,latest_sha256 FROM song_sources'):
                old=a['_sources_before'].get(str(source['source_id']))
                if old is None or old['latest_sha256']!=source['latest_sha256']:
                    source_changes.append(dict(source_id=source['source_id'],song_id=source['song_id'],relative_path=source['relative_path'],change='added' if old is None else 'updated'))
            updated_ids={r['song_id'] for r in source_changes if r['song_id'] in a['_scan_before_ids']}
            candidates=[];updates=[];uncertain=[];s=a['_session'];selection=s.get('selection') or {};fields=selection.get('fields') or {};pack=selection.get('pack') or {}
            observed_title=(fields.get('title') or {}).get('value') or (s.get('playing_preview') or {}).get('title') or (a.get('room_observation') or {}).get('title')
            for rid,change in changes.items():
                for song in catalog.songs.values():
                    if song['root_id']!=int(rid) or song['relative_folder'] in deferred_by_root.get(rid,set()):continue
                    is_new=song['song_id'] not in a['_scan_before_ids']
                    observed=song['relative_folder'] in change['observed_source_folders']
                    if not is_new and song['relative_folder'] not in change['folders'] and song['song_id'] not in updated_ids:continue
                    match=catalog.match(fields,pack.get('pack_id') if pack.get('status')=='confirmed' else None,'custom')
                    detail=next((c for c in match['candidates'] if c['song_id']==song['song_id']),{})
                    source_detail=self.reviews._song(catalog,song['song_id'])
                    if not detail:
                        detail=self.reviews._comparison(source_detail,{k:v.get('value') for k,v in fields.items()})
                    missing=list(detail.get('missing',[]))
                    if pack.get('status')!='confirmed':missing.append('confirmed_pack')
                    artists=sorted({r['artist'] for r in source_detail['sources'] if r.get('artist')})
                    candidate=dict(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'],
                        pack=song['pack_name'],title=song['saved_title'] or song['latest_title'],artist=song.get('artist') or ' / '.join(artists) or None,stable_source=True,
                        is_new_song=is_new,observed_after_snapshot=observed,
                        source_titles=[song['saved_title'],song['latest_title'],*[r.get('title') for r in source_detail['sources']],
                            *[r[0] for r in catalog.db.execute('SELECT display_title FROM song_title_aliases WHERE song_id=? AND confirmed=1',(song['song_id'],))]],
                        title_matches=song['song_id'] in catalog.title_ids(observed_title),
                        missing=sorted(set(missing)),conflicts=detail.get('conflicts',[]),game_difficulties=source_detail['difficulties'],
                        source_files=[r[0] for r in catalog.db.execute('SELECT relative_path FROM song_sources WHERE song_id=?',(song['song_id'],))],
                        stable_metadata={str(Path(p).relative_to(song['relative_folder'])):meta for p,meta in after[rid]['snapshot']['files'].items() if Path(p).is_relative_to(song['relative_folder'])})
                    if is_new and observed:candidates.append(candidate)
                    elif is_new:uncertain.append(candidate)
                    else:updates.append(candidate)
            a.update(candidates=candidates,existing_updates=updates,uncertain_imports=uncertain,candidate_scope='observed_new_song_ids_v2',
                source_changes=source_changes,
                scan_completed=True,status='pending_identity',_scanned=stamp,_manual_retry=False)
            self.armed=current
            self.generation+=1;self._event('DOWNLOAD_SCAN_COMPLETED',key=stamp);self._save()
            try:
                _,row=self.reviews._read(a['session_id']);binding=verified_download_binding(self.reviews,row)
                if binding:a.update(confirmed_song_id=binding['song_id'],identity_status='confirmed_independent_chart_match',status='confirmed');self._save()
            except (OSError,ValueError):pass
            self._notify()
        finally:catalog.close()

    def _run(self):
        while not self.stop_event.is_set():
            try:kind,data=self.queue.get(timeout=self.interval)
            except queue.Empty:
                if self.active and (self.enabled or self.active.get('_manual_retry')) and (not self.active.get('_result_deadline') or time.monotonic()<self.active['_result_deadline']):
                    try:self._poll()
                    except Exception as e:self.active.update(status='failed_retry_pending',error=str(e));self._save()
                continue
            try:
                if kind=='arm':
                    self.active=None;self.last_key=None;self.last_lobby=None;self.associated.clear();self.results.clear();self.armed=self._snapshots()
                    with self.lock:self.view=dict(status='armed',events=[],metrics={k:dict(milliseconds=v['snapshot']['milliseconds'],files=len(v['snapshot']['files']),errors=v['snapshot']['errors']) for k,v in self.armed.items()})
                elif kind=='no_map':self._begin(data)
                elif kind=='room_enter':
                    self.room_baseline = self._snapshots()
                elif kind=='room_poll':self._room_poll(data)
                elif kind=='associate':self._associate(data)
                elif kind=='poll':self._poll()
                elif kind=='lobby_status' and self.active:
                    changed=False
                    if data.get('self_ready'):changed=self._event('SELF_READY') or changed
                    if data.get('progress_observed'):changed=self._event('SELF_DOWNLOAD_PROGRESS_OBSERVED',percentage=None) or changed
                    if changed:self._save()
                elif kind=='retry':
                    saved=session_download(self.directory,data)
                    if saved and (not self.active or self.active.get('session_id')!=data):
                        from uuid import UUID
                        episode=str(UUID(saved['episode_id']))
                        baseline=json.loads((self.directory/'downloads'/episode/'baseline.json').read_text(encoding='utf8'))
                        _,row=self.reviews._read(data)
                        self.active=dict(saved,_baseline=baseline,_session=row['locked_session'],_last_stamp=None,_last_change=time.monotonic(),_scanned=None,_result_deadline=time.monotonic()+120)
                    if self.active and self.active.get('session_id')==data:
                        self.active.update(attempts=0,_scanned=None,_manual_retry=True,_result_deadline=time.monotonic()+120)
                        self._poll()
            except Exception as e:
                with self.lock:self.view.update(status='failed_retry_pending',error=str(e))
            finally:self.queue.task_done()

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(2)
