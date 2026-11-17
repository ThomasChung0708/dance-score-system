"""Persistent human result review. The catalog is read-only; final records use SubmissionService."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path
import threading
from uuid import UUID, uuid5, NAMESPACE_URL

from .library_service import LibraryError
from .monitor_tracking import atomic_json, fingerprint, utc, ManualReferences
from .ocr_matching import ReadOnlyCatalog, normalize
from .result_ocr import NUMERIC
from .note_evidence import note_evidence
from .songs import catalog_display_title

EDITABLE = (*NUMERIC, 'grade', 'completion_status', 'game_level', 'total_notes', 'difficulty', 'player_name', 'duration_seconds')


def score_preview(values, notes_verified=False, identity_confirmed=False, max_basis=None):
    """Compatibility entry point: both preview and formal storage share the core."""
    from .scoring import calculate
    return calculate(values, notes_verified=notes_verified, identity_confirmed=identity_confirmed,max_basis=max_basis)


class ResultReviews:
    def __init__(self, database, directory, engine_factory=None, *, scoring_policy='custom-judged-v1'):
        if scoring_policy not in ('custom-judged-v1','independent-max-v2'):raise ValueError('Unsupported score policy')
        self.scoring_policy=scoring_policy
        self.database = Path(database).resolve()
        self.directory = Path(directory)
        self.engine_factory = engine_factory
        self.lock = threading.RLock()
        self.jobs = {}
        self.worker = None
        self.on_ingest = None
        from .player_settings import PlayerSettings
        self.player_settings = PlayerSettings(self.database.parent/'player-settings.json')

    def _directory(self, session_id):
        try:
            if str(UUID(session_id)) != session_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise LibraryError('Session ID 不正確。')
        folder = self.directory/'sessions'/session_id
        if folder.is_symlink() or (folder/'result.png').is_symlink() or (folder/'session.json').is_symlink():
            raise LibraryError('不接受連結形式的結算來源。')
        marker = self.directory/'catalog-location.json'
        if marker.exists():
            if Path(json.loads(marker.read_text(encoding='utf8'))['database_path']).resolve() != self.database:
                raise LibraryError('此監控目錄屬於另一份歌曲庫。', 409)
        return folder

    def _source(self, session_id):
        folder = self._directory(session_id)
        try:
            session = json.loads((folder/'session.json').read_text(encoding='utf8'))
            if session['session_id'] != session_id or session['status'] != 'pending_result' or not session.get('result'):
                raise LibraryError('此 Session 沒有可確認的結算。', 409)
            image = (folder/'result.png').read_bytes()
        except (OSError, KeyError, ValueError) as error:
            raise LibraryError('找不到完整的 Session 與結算原圖。', 404) from error
        return folder, session, image, hashlib.sha256(image).hexdigest()

    def _read(self, session_id):
        folder, session, image, sha = self._source(session_id)
        try:
            row = json.loads((folder/'result-review.json').read_text(encoding='utf8'))
        except FileNotFoundError:
            raise LibraryError('此結算尚未執行 OCR。', 404)
        if row['screenshot_sha256'] != sha or row['session_id'] != session_id or row['catalog_path'] != str(self.database):
            raise LibraryError('結算原圖或身份已變更，保留原辨識資料供檢查。', 409)
        return folder, row

    def list(self):
        with self.lock:
            # SQLite receipts are authoritative even if a crash happened after
            # commit and before the review sidecar could be updated. Read only;
            # listing reviews must never rewrite historical evidence.
            saved = set()
            song_packs = {}
            song_titles = {}
            if self.database.is_file():
                connection = sqlite3.connect(self.database.as_uri()+'?mode=ro', uri=True)
                try:
                    connection.row_factory = sqlite3.Row
                    connection.execute('PRAGMA query_only=ON')
                    saved = {r[0] for r in connection.execute('SELECT session_id FROM play_records')}
                    song_packs = dict(connection.execute(
                        'SELECT s.song_id, p.name FROM songs s LEFT JOIN packs p ON p.pack_id=s.pack_id'))
                    song_titles = {
                        song['song_id']: catalog_display_title(connection, song)
                        for song in connection.execute('SELECT * FROM songs')
                    }
                finally:
                    connection.close()
            rows = []
            for path in (self.directory/'sessions').glob('*/session.json'):
                try:
                    if path.parent.name in saved:
                        continue
                    if json.loads(path.read_text(encoding='utf8')).get('status') != 'pending_result':
                        continue
                    folder, session, _, sha = self._source(path.parent.name)
                    review_path = folder/'result-review.json'
                    row = json.loads(review_path.read_text(encoding='utf8')) if review_path.exists() else None
                    if (folder/'review-dismissal.json').exists():
                        continue
                    if row and row.get('status') in ('saved_verified_auto', 'confirmed_manual', 'discarded'):
                        continue
                    selection = session.get('selection') or {}
                    title = selection.get('fields', {}).get('title', {}).get('value')
                    if row:
                        title = row['ocr']['fields']['title']['value'] or title
                    bound_id = (row.get('draft') or {}).get('song_id') if row else session.get('song_id')
                    if not title and bound_id:
                        title = song_titles.get(bound_id)
                    rows.append(dict(session_id=session['session_id'], started_at=session.get('started_at'),
                        title=title, song_id=(row.get('draft') or {}).get('song_id') if row else session.get('song_id'),
                        status=row['status'] if row else 'unprocessed', revision=row['revision'] if row else 0,
                        pack_name=song_packs.get((row.get('draft') or {}).get('song_id')) if row else
                            (selection.get('pack') or {}).get('name'),
                        values=copy.deepcopy(row['draft']['values']) if row else {},
                        review_reasons=self.review_reasons(row) if row else ['尚未辨識結算，請先辨識原圖。'],
                        image_intact=not row or row['screenshot_sha256'] == sha,
                        job=self.jobs.get(session['session_id'])))
                except (LibraryError, OSError, ValueError, KeyError, TypeError):
                    rows.append(dict(session_id=path.parent.name, status='invalid_source'))
            return dict(results=sorted(rows, key=lambda r: r.get('started_at') or '', reverse=True),
                        jobs=copy.deepcopy(self.jobs), writes_play_record=False)

    @staticmethod
    def review_reasons(row):
        session = row.get('locked_session') or {}
        reasons = []
        selection = session.get('selection') or {}
        if len((selection.get('matching') or {}).get('candidates') or []) > 1:
            reasons.append('有多首同名候選，請核對所屬曲包並選擇正確歌曲。')
        if not (row.get('draft') or {}).get('song_id'):
            reasons.append('歌曲尚未唯一確認，請核對歌曲與曲包。')
        if session.get('pending_reason') == 'no_fresh_selection':
            reasons.append('開始遊玩前沒有保留到可用的選曲資料。')
        temporal = row.get('ocr', {}).get('temporal_validation') or {}
        if temporal.get('status') != 'confirmed':
            reasons.append('結算停留時間或清晰畫面不足，請核對截圖中的數字。')
        fields = row.get('ocr', {}).get('fields') or {}
        if any(f.get('value') is None or f.get('reason') or f.get('confidence', 0) < .95
               for k, f in fields.items() if k in ('perfect', 'cool', 'bad', 'miss', 'grade', 'game_score')):
            reasons.append('部分成績欄位辨識不確定，請對照原圖修正。')
        return reasons or ['本次未通過自動儲存檢查，請核對歌曲與本人成績後儲存。']

    def discard(self, body):
        """Dismiss one play without deleting screenshot/OCR or writing SQLite."""
        with self.lock:
            folder, _, _, sha = self._source(body.get('session_id'))
            connection = sqlite3.connect(self.database.as_uri()+'?mode=ro', uri=True)
            try:
                connection.execute('PRAGMA query_only=ON')
                if connection.execute('SELECT 1 FROM play_records WHERE session_id=?',
                                      (body['session_id'],)).fetchone():
                    raise LibraryError('這次成績已儲存，不能捨棄待確認結算。', 409)
            finally:
                connection.close()
            path = folder/'result-review.json'
            if path.exists():
                _, row = self._read(body.get('session_id'))
                if row['status'] == 'discarded':
                    return dict(session_id=row['session_id'], status='discarded')
                if body.get('review_id') != row['review_id'] or body.get('revision') != row['revision']:
                    raise LibraryError('審核版本已變更，請重新載入。', 409)
                if row['status'] == 'saved_verified_auto':
                    raise LibraryError('已儲存的成績請至歷史成績查看。', 409)
                row['history'].append(dict(at=utc(), previous_status=row['status'],
                                           revision=row['revision'], source='explicit_user_discard'))
                row.update(status='discarded', revision=row['revision']+1, updated_at=utc())
                atomic_json(path, row)
            else:
                atomic_json(folder/'review-dismissal.json', dict(
                    session_id=body['session_id'], screenshot_sha256=sha, discarded_at=utc()))
            return dict(session_id=body['session_id'], status='discarded')

    def record_automatic_submission(self, record):
        """Reflect a committed receipt without changing the locked OCR/session."""
        if record['confirmation_method'] != 'verified_auto':
            return
        with self.lock:
            folder, row = self._read(record['session_id'])
            if (row['review_id'] != record['review_id'] or
                    row['screenshot_sha256'] != record['screenshot_sha256']):
                raise LibraryError('Committed result evidence differs from review', 409)
            row.update(status='saved_verified_auto', writes_play_record=True,
                       generates_play_record=True, record_id=record['record_id'],
                       confirmed_at=record['confirmed_at'])
            atomic_json(folder/'result-review.json', row)

    def ingest(self, session, ocr):
        with self.lock:
            folder, stored, image, sha = self._source(session['session_id'])
            path = folder/'result-review.json'
            if path.exists():
                return self._read(session['session_id'])[1]
            selection = stored.get('selection') or {}
            from .result_title_binding import bind_result_title, context_selection
            if (not selection.get('fields', {}).get('title', {}).get('value')
                    and context_selection(stored)):
                selection = {**context_selection(stored), **selection}
                catalog = ReadOnlyCatalog(self.database)
                try:
                    recovered = bind_result_title(catalog, stored, ocr, selection)
                    if recovered:
                        selection = recovered
                finally:
                    catalog.close()
            selected_fields = selection.get('fields') or {}
            values = {key: ocr['fields'].get(key, {}).get('value') for key in EDITABLE}
            for key in ('difficulty', 'total_notes', 'duration_seconds'):
                values[key] = selected_fields.get(key, {}).get('value')
            if ocr['multiplayer']:
                values['game_level'] = selected_fields.get('game_level', {}).get('value')
            binding = stored.get('matching_status', '')
            draft = dict(values=values, song_id=stored.get('song_id'),
                notes_verified=bool(stored.get('song_id') and binding in ('confirmed_manual', 'confirmed_manual_reference', 'unique_verified_candidate', 'confirmed_official_catalog')),
                duration_verified=bool(stored.get('song_id') and binding in ('confirmed_manual', 'confirmed_manual_reference', 'unique_verified_candidate') and values.get('duration_seconds')),
                player_verified=False, identity_reviewed=False)
            if selection.get('result_title_binding'):
                draft.update(song_id=selection['result_title_binding']['song_id'],
                             identity_binding_method=selection['result_title_binding']['method'],
                             identity_binding_evidence=copy.deepcopy(selection['result_title_binding']))
            if draft['song_id'] is None and selected_fields:
                # A clearer result title can recover a review's song binding.
                # Keep the original locked selection/confidences intact: this
                # assistance grants no automatic submission authority.
                from .ocr_matching import result_identity_fields
                fields = result_identity_fields(selection, ocr, stored.get('playing_preview'))
                observed_title = ocr['fields'].get('title') or {}
                if normalize(observed_title.get('value')) == normalize(fields.get('title', {}).get('value')):
                    fields['title'] = copy.deepcopy(observed_title)
                pack = selection.get('pack') or {}
                catalog = ReadOnlyCatalog(self.database)
                try:
                    match = catalog.match_identity(fields,
                        pack.get('pack_id') if pack.get('status') == 'confirmed' else None,
                        selection.get('source_type'))
                    draft['song_id'] = match.get('confirmed_song_id')
                finally:
                    catalog.close()
            row = dict(schema_version=1, review_id=str(uuid5(NAMESPACE_URL, 'dance-result:'+stored['session_id']+':'+sha)),
                session_id=stored['session_id'], screenshot_sha256=sha, catalog_path=str(self.database),
                created_at=utc(), revision=0, status='pending_review', ocr=copy.deepcopy(ocr),
                locked_session=copy.deepcopy(stored), draft=draft, history=[], writes_play_record=False)
            from .download_tracking import prefill_download
            prefill_download(self, row)
            atomic_json(path, row)
            if self.on_ingest:self.on_ingest(row['session_id'])
            return row

    def process(self, session_id, player_name=None):
        with self.lock:
            folder, session, image, sha = self._source(session_id)
            if (folder/'result-review.json').exists():
                return dict(status='already_processed', review_id=self._read(session_id)[1]['review_id'])
            if self.worker and self.worker.is_alive():
                raise LibraryError('正在辨識結算，請等候完成。', 409)
            player_name = self.player_settings.get() if player_name is None else player_name
            if not isinstance(player_name, str) or not player_name.strip() or len(player_name) > 64:
                raise LibraryError('請輸入本人暱稱。')
            self.player_settings.set(player_name)
            self.jobs[session_id] = dict(status='running', started_at=utc())
            def run():
                try:
                    from PIL import Image
                    from io import BytesIO
                    from .ocr_engine import LocalOCREngine
                    from .result_ocr import recognize_result
                    engine = self.engine_factory() if self.engine_factory else LocalOCREngine(Path(__file__).resolve().parent.parent/'.ocr-models', low_latency=True)
                    import time
                    started = time.perf_counter()
                    result = recognize_result(Image.open(BytesIO(image)), engine, player_name.strip())
                    result['ocr_ms'] = round((time.perf_counter()-started)*1000, 2)
                    with self.lock:
                        self.ingest(session, result)
                        self.jobs[session_id] = dict(status='completed', completed_at=utc())
                except Exception as error:
                    with self.lock:
                        self.jobs[session_id] = dict(status='error', error=f'{type(error).__name__}: {error}')
            self.worker = threading.Thread(target=run, name='Dance-result-review-ocr', daemon=True)
            self.worker.start()
            return dict(status='running', session_id=session_id)

    def candidates(self, query, source_type='all', offset=0, limit=60, values=None):
        if not isinstance(query, str) or len(query) > 200:
            raise LibraryError('搜尋字串不正確。')
        if source_type not in ('custom','official','all'):
            raise LibraryError('來源篩選不正確。')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
            raise LibraryError('候選分頁不正確。')
        catalog = ReadOnlyCatalog(self.database)
        try:
            key = normalize(query)
            ids = []
            for song in catalog.songs.values():
                if source_type!='all' and song['source_type']!=source_type:
                    continue
                title = song['saved_title'] or song['latest_title']
                if key and key not in normalize(title) and song['song_id'] not in catalog.title_ids(key):
                    continue
                ids.append(song['song_id'])
            ids.sort(key=lambda i:(normalize(catalog.songs[i]['saved_title'] or catalog.songs[i]['latest_title']),i))
            references=ManualReferences(self.directory,self.database).data.values()
            rows=[self._song(catalog,i,references) for i in ids[offset:offset+limit]]
            for song in rows:
                song['comparison']=self._comparison(song,values or {})
            return dict(candidates=rows, count=len(ids), offset=offset, limit=limit,
                        has_more=offset+limit<len(ids),truncated=offset+limit<len(ids),source_type=source_type,
                        search_scope='all_custom_packs_including_connect' if source_type=='custom' else source_type,
                        auto_selected_song_id=None)
        finally:
            catalog.close()

    def _song(self,catalog, song_id,references=None):
        song = catalog.songs.get(song_id)
        if not song:
            return None
        verified=[]
        for ref in (ManualReferences(self.directory,self.database).data.values() if references is None else references):
            if ref['song_id']==song_id and ref['root_id']==song['root_id'] and ref['relative_path']==song['relative_folder']:
                verified.append(ref)
        difficulties=[copy.deepcopy(v) for (sid, _), v in catalog.difficulties.items() if sid == song_id]
        for difficulty in ('EASY','NORMAL','HARD'):
            refs=[r for r in verified if r['values'].get('difficulty')==difficulty]
            if not refs:continue
            entry=next((d for d in difficulties if d['difficulty']==difficulty),None)
            if entry is None:
                entry=dict(difficulty_id=None,difficulty=difficulty,game_level=None,total_notes=None,notes_verified=0)
                difficulties.append(entry)
            for key in ('game_level','total_notes','duration_seconds'):
                observed={r['values'].get(key) for r in refs if r['values'].get(key) is not None}
                if len(observed)==1 and entry.get(key) is None:
                    entry[key]=next(iter(observed))
                    entry[{'game_level':'level_source','total_notes':'notes_source','duration_seconds':'duration_source'}[key]]='manual_reference'
                    if key in ('total_notes','duration_seconds'):entry['notes_verified' if key=='total_notes' else 'duration_verified']=1
                elif len(observed)>1 or (len(observed)==1 and entry.get(key) is not None and entry[key]!=next(iter(observed))):
                    entry.setdefault('manual_reference_conflicts',[]).append(key)
        sources=[]
        for row in catalog.db.execute('SELECT source_id,relative_path,format,saved_title,saved_artist,payload_json FROM song_sources WHERE song_id=? ORDER BY source_id',(song_id,)):
            payload=json.loads(row['payload_json'])
            sources.append(dict(source_id=row['source_id'],relative_path=row['relative_path'],format=row['format'],
                title=row['saved_title'],artist=row['saved_artist'],encoding=payload.get('encoding'),
                encoding_candidates=payload.get('encoding_candidates',[])))
        return dict(song_id=song_id, root_id=song['root_id'], relative_path=song['relative_folder'],
            pack_id=song['pack_id'], pack_name=song['pack_name'], source_type=song['source_type'],
            title=catalog_display_title(catalog.db, song), official_source=catalog.official_sources.get(song_id),
            artist=song.get('artist'),
            difficulties=difficulties,sources=sources,acquisition_method='unknown',acquisition_verified=False,
            is_present=bool(song['is_present']))

    @staticmethod
    def _comparison(song,values):
        gd=next((d for d in song['difficulties'] if d['difficulty']==values.get('difficulty')),None)
        missing=[];conflicts=[]
        for key in ('difficulty','game_level','total_notes','duration_seconds'):
            if values.get(key) is None:missing.append('observed_'+key)
            if key!='difficulty':
                if not gd or gd.get(key) is None or (key in ('total_notes','duration_seconds') and not gd.get('notes_verified' if key=='total_notes' else 'duration_verified')):
                    missing.append('verified_'+key)
                elif values.get(key) is not None and gd[key]!=values[key]:conflicts.append(key)
                if gd and key in gd.get('manual_reference_conflicts',[]) and key not in conflicts:conflicts.append(key)
        return dict(missing=missing,conflicts=conflicts,automatic_binding=False,
                    note='Candidate comparison never confirms missing Pack or selects a Song ID.')

    def player_rows(self,row):
        if 'player_rows' in row['ocr']:return copy.deepcopy(row['ocr']['player_rows'])
        from .result_ocr import parse_result
        return parse_result(row['ocr'].get('tokens',[]),row['ocr'].get('requested_player_name','TC')).get('player_rows',[])

    def _preview(self, row, draft):
        values = draft['values']
        catalog = ReadOnlyCatalog(self.database)
        try:
            song_id = draft.get('song_id')
            song = self._song(catalog, song_id) if type(song_id) is int else None
            session = row['locked_session']
            selected = session.get('selection') or {}
            known_identity = song is not None and (draft.get('identity_reviewed') is True or (
                session.get('song_id') == song_id and session.get('matching_status') in (
                    'confirmed_manual','confirmed_manual_reference','unique_verified_candidate','confirmed_official_catalog')))
            notes_verified = draft.get('notes_verified') is True and known_identity
            gd = catalog.difficulties.get((song_id, values.get('difficulty'))) if song else None
            from .chart_changes import official_context, warnings as chart_warnings
            official_result = bool(song and official_context(catalog, row, song_id))
            if gd and gd['notes_verified'] and gd['total_notes'] is not None and values.get('total_notes') == gd['total_notes']:
                notes_verified = known_identity
            preview = score_preview(values, notes_verified, known_identity)
            selected_player=next((p for p in self.player_rows(row) if p['row_id']==draft.get('player_row_id')),None)
            notes = note_evidence(session, values, gd, identity_confirmed=known_identity,
                manual_notes_verified=draft.get('notes_verified') is True,
                ocr_values={k:v.get('value') for k,v in (selected_player['fields'] if selected_player else row['ocr']['fields']).items()})
            if (official_result and draft.get('notes_verified') is True
                    and values.get('completion_status') == 'COMPLETED'
                    and values.get('total_notes') == notes['judged_total'] == notes['ocr_judged_total']):
                # The verified reference for this play is the result itself.
                # Preserve all catalog/selection counts and their differences.
                notes.update(catalog_result_difference=notes['judged_total']-gd['total_notes']
                        if gd and gd.get('total_notes') is not None else None,
                    verified_total_notes=notes['judged_total'],
                    verification_source='result_screen_judged_sum',
                    verification_scope='this_result_screen',
                    note_count_difference=0, judgment_difference=0, status='consistent')
            notes['ocr_player_row_id']=selected_player['row_id'] if selected_player else None
            from .note_review import authorization
            exception=authorization(row,draft,notes)
            from .max_basis import resolve
            from .score_comparison import completed_basis,comparison
            use_custom=self.scoring_policy=='custom-judged-v1'
            basis=completed_basis(values,known_identity) if use_custom and values.get('completion_status')=='COMPLETED' else resolve(row,draft,notes,identity_confirmed=known_identity)
            compared=comparison(catalog.db,song_id,values.get('difficulty'),preview.get('judged_notes') if values.get('completion_status')=='COMPLETED' else None,directory=self.directory,session_id=row['session_id'],max_basis_notes=basis.get('notes') if basis.get('verified') else None,max_basis_policy=basis.get('policy')) if song else dict(status='identity_pending',eligible=False,ranking_paused=False,observed_totals=[])
            basis.setdefault('context',None)
            notes.update(max_basis_notes=basis['notes'],max_basis_verified=basis['verified'],max_basis_verification=basis)
            from .scoring import calculate
            preview=calculate(values,notes_verified=notes_verified,identity_confirmed=known_identity,
                note_exception=notes['status']=='count_discrepancy_pending',max_basis=basis,comparison_conflict=compared['ranking_paused'])
            issues = list(preview['errors'])
            if notes['selected_database_conflict'] and not official_result:
                issues.append(dict(field='total_notes',code='selected_database_notes_conflict'))
                preview.update(max_score=None,difference=None,percentage=None,display=None,lamp_preview=None)
            if not official_result and not use_custom and notes['status']=='count_discrepancy_pending' and not exception['authorized']:
                issues.append(dict(field='total_notes',code='note_source_difference_requires_review'))
            warnings = []
            if official_result:
                warnings.extend(chart_warnings(row, gd))
            if use_custom and notes['status']=='count_discrepancy_pending':warnings.append(dict(field='total_notes',code='catalog_difference_informational'))
            if compared['ranking_paused']:warnings.append(dict(field='max_basis',code='cross_session_basis_conflict'))
            if not basis['verified']:warnings.append(dict(field='max_basis',code='max_basis_unverified'))
            original_completion = (selected_player['fields'] if selected_player else row['ocr']['fields'])['completion_status']
            if (original_completion['value'] == 'FAILED' or (session.get('result') or {}).get('state')=='GAME_OVER') and values.get('completion_status') == 'COMPLETED':
                issues.append(dict(field='completion_status', code='gameover_cannot_be_clear'))
                preview.update(lamp_preview='FAILED', max_score=None, difference=None, percentage=None, display=None)
            if not song:
                issues.append(dict(field='song_id', code='song_identity_not_confirmed'))
            elif not known_identity:
                issues.append(dict(field='song_id', code='song_identity_requires_manual_review'))
            uncharted = bool(song and song['source_type'] == 'custom' and not song['difficulties'])
            if values.get('difficulty') not in ('EASY', 'NORMAL', 'HARD') and not (uncharted and values.get('difficulty') is None):
                issues.append(dict(field='difficulty', code='game_difficulty_required'))
            if values.get('game_level') is None:
                issues.append(dict(field='game_level', code='local_game_level_required'))
            for k in (*NUMERIC, 'grade', 'completion_status'):
                if values.get(k) is None or values.get(k) == '':
                    issues.append(dict(field=k, code='field_required'))
            if values.get('grade') is not None:
                import re
                if not isinstance(values['grade'], str) or not re.fullmatch(r'[A-FS]{1,3}\+{0,3}', values['grade']):
                    issues.append(dict(field='grade', code='invalid_grade'))
            if draft.get('player_verified') is not True:
                issues.append(dict(field='player_name', code='local_player_requires_review'))
            if not isinstance(values.get('player_name'), str) or not values['player_name'].strip() or len(values['player_name'])>64:
                issues.append(dict(field='player_name', code='local_player_name_required'))
            if song:
                gd = catalog.difficulties.get((song_id, values.get('difficulty')))
                if song['source_type'] == 'official' and values.get('difficulty') in ('EASY','NORMAL','HARD') and not gd:
                    issues.append(dict(field='difficulty', code='official_difficulty_not_in_catalog'))
                if gd:
                    if gd['game_level'] is not None and values.get('game_level') != gd['game_level']:
                        issues.append(dict(field='game_level', code='verified_catalog_level_conflict'))
                    from .result_consistency import screen_catalog
                    if not official_result and screen_catalog(gd) and values.get('total_notes') != gd['total_notes']:
                        issues.append(dict(field='total_notes', code='verified_catalog_notes_conflict'))
            fields = selected.get('fields') or {}
            for key in ('title', 'game_level'):
                observed = row['ocr']['fields'][key]['value']
                expected = fields.get(key, {}).get('value')
                if key == 'game_level' and row['ocr']['footer_level_scope'] == 'host_only_not_local_chart':
                    warnings.append(dict(field=key, code='footer_level_is_host_not_local'))
                    continue
                if observed is not None and expected is not None and normalize(str(observed)) != normalize(str(expected)):
                    warnings.append(dict(field=key, code='result_session_conflict', observed=observed, session=expected))
                    if draft.get('identity_reviewed') is not True:
                        issues.append(dict(field=key, code='session_conflict_requires_manual_identity_review'))
            if not notes_verified and values.get('completion_status')!='COMPLETED':
                warnings.append(dict(field='total_notes', code='full_notes_unverified_no_max'))
            duration_verified=draft.get('duration_verified') is True and type(values.get('duration_seconds')) is int and values['duration_seconds']>0
            if not duration_verified:
                warnings.append(dict(field='duration_seconds',code='game_duration_requires_confirmation'))
            blocking_identity = {'song_identity_not_confirmed','song_identity_requires_manual_review',
                'game_difficulty_required','local_game_level_required','official_difficulty_not_in_catalog',
                'verified_catalog_level_conflict','verified_catalog_notes_conflict',
                'session_conflict_requires_manual_identity_review','gameover_cannot_be_clear'}
            if any(i['code'] in blocking_identity for i in issues) or (
                    row['ocr']['local_player_status'] != 'CONFIRMED' and draft.get('player_verified') is not True):
                preview.update(max_score=None, difference=None, percentage=None, display=None,
                    lamp_preview='FAILED' if values.get('completion_status') == 'FAILED' or original_completion['value'] == 'FAILED' else None)
                if any(i['code'] in blocking_identity-{'song_identity_requires_manual_review'} for i in issues):
                    notes['conditional_score']=None
            unreadable = [k for k, v in row['ocr']['fields'].items() if v['value'] is None or v['confidence'] < .9 or v.get('reason')]
            image_reviewed=bool(draft.get('player_verified') and draft.get('identity_reviewed') and (draft.get('acknowledge') or row.get('identity_confirmation')))
            if use_custom and unreadable and not image_reviewed:
                preview['lamp_preview']=None
            readiness=dict(ocr='recognized' if row['ocr'].get('fields') else 'pending',
                song_identity='confirmed_manual' if known_identity else 'pending',
                player_row='confirmed_manual' if draft.get('player_verified') is True else 'pending',
                full_notes='verified' if notes_verified and notes['status']=='consistent' else 'pending',game_duration='verified' if duration_verified else 'pending',
                review_saved=row['status'] in ('confirmed_manual','reviewed_pending_full_notes','reviewed_pending_duration','reviewed_pending_max_basis'),
                formal_storage_enabled=False)
            readiness['ready_for_phase5_review']=bool(readiness['review_saved'] and not issues and notes_verified and duration_verified and row['draft']==draft)
            return dict(**preview, issues=issues, warnings=warnings, ocr_fields_requiring_review=unreadable,
                note_sources=notes,note_exception=exception,max_basis_verification=basis,comparison=compared,
                selected_song=song, can_confirm=not issues,readiness=readiness,
                confirmation_scope='reviewed_values_only_no_play_record')
        finally:
            catalog.close()

    def detail(self, session_id):
        with self.lock:
            folder, row = self._read(session_id)
            from .download_tracking import prefill_download
            if prefill_download(self, row):
                atomic_json(folder/'result-review.json', row)
            result = copy.deepcopy(row)
            result['review_reasons'] = self.review_reasons(row)
            result['draft']['values'].setdefault('duration_seconds',None)
            result['preview'] = self._preview(row, row['draft'])
            result['player_rows']=self.player_rows(row)
            from .source_choice import effective_selection
            selection=effective_selection(row)
            scope = 'official' if selection.get('source_type') == 'official' else (
                'custom' if selection.get('source_type') == 'custom'
                and (selection.get('pack') or {}).get('status') == 'confirmed' else 'all')
            result['candidate_source_type']=scope
            titles = [row['ocr']['fields']['title']['value'],
                      (row['locked_session'].get('selection') or {}).get('fields', {}).get('title', {}).get('value')]
            download=self.download_detail(session_id,row)
            candidates = {c['song_id']:c for c in download['candidates']}
            if not download['download_evidence']:
                for title in titles:
                    if title:
                        candidates.update({c['song_id']: c for c in self.candidates(title,scope,values=row['draft']['values'])['candidates']})
            selected = result['preview']['selected_song']
            if selected:
                candidates[selected['song_id']] = selected
            result['candidates'] = list(candidates.values())
            result['candidate_count']=len(candidates) if download['download_evidence'] else max((self.candidates(t,scope,limit=1)['count'] for t in titles if t),default=0)
            result['candidate_scope']='related_new_downloads' if download['download_evidence'] else 'title_search'
            result['pack_required_for_automatic_matching']=True
            result['autofill'] = self._assistance(row, row['draft'])
            result['download_evidence']=download['download_evidence']
            from .source_choice import offer
            catalog = ReadOnlyCatalog(self.database)
            try:
                from .download_tracking import verified_download_binding
                result['source_prompt'] = offer(catalog, row, verified_download_binding(self, row))
            finally:
                catalog.close()
            return result

    def source_prompts(self):
        """Read-only UI poll; never blocks or calls the capture loop."""
        from .source_choice import offer
        prompts = []
        catalog = ReadOnlyCatalog(self.database)
        try:
            for item in self.list()['results']:
                if item['status'] != 'pending_review':
                    continue
                try:
                    from .download_tracking import verified_download_binding
                    row = self._read(item['session_id'])[1]
                    prompt = offer(catalog, row, verified_download_binding(self, row))
                    if prompt and not prompt['dismissed']:
                        prompts.append(prompt)
                except LibraryError:
                    continue
        finally:
            catalog.close()
        return dict(prompts=prompts)

    def choose_source(self, body):
        from .source_choice import offer
        with self.lock:
            folder, row = self._read(body.get('session_id'))
            if body.get('review_id') != row['review_id'] or body.get('revision') != row['revision']:
                raise LibraryError('此結算已變更，請重新載入。', 409)
            previous = row.get('source_choice')
            if previous:
                if (previous['source_type'], previous.get('song_id')) == (body.get('source_type'), body.get('song_id')):
                    return dict(session_id=row['session_id'], status=row['status'], idempotent=True)
                raise LibraryError('此 Session 已選擇來源，請到待確認核對。', 409)
            catalog = ReadOnlyCatalog(self.database)
            try:
                prompt = offer(catalog, row)
                if not prompt:
                    raise LibraryError('此結算已無需選擇來源，請重新載入。', 409)
                if body.get('dismiss') is True:
                    row['source_prompt_dismissed'] = True
                else:
                    source, sid = body.get('source_type'), body.get('song_id')
                    candidates = [c for c in prompt['candidates'] if c['source_type'] == source]
                    if source not in ('official', 'custom') or not candidates:
                        raise LibraryError('來源不在這次結算候選中。', 409)
                    if source == 'custom' and (type(sid) is not int or sid not in {c['song_id'] for c in candidates}):
                        raise LibraryError('請選擇本次候選的自製譜資料夾。', 409)
                    if source == 'official' and sid is not None:
                        raise LibraryError('官方來源請由正常判定流程辨識譜面。', 409)
                    candidate = next((c for c in candidates if c['song_id'] == sid), None)
                    row['source_choice'] = dict(source_type=source, song_id=sid,
                        pack_id=candidate['pack_id'] if candidate else None,
                        chosen_at=utc(), review_id=row['review_id'])
                    if candidate:
                        row['draft']['song_id'] = sid
                    elif source == 'official':
                        from .chart_changes import official_fields
                        row['draft']['song_id'] = catalog.match_official(
                            official_fields(row['locked_session'].get('selection') or {}, row['ocr']),
                            source_confirmed=True).get('confirmed_song_id')
                    row['source_prompt_dismissed'] = False
                atomic_json(folder/'result-review.json', row)
            finally:
                catalog.close()
            if not body.get('dismiss') and self.on_ingest:
                self.on_ingest(row['session_id'])
            return dict(session_id=row['session_id'], status=row['status'], idempotent=False)

    def download_detail(self,session_id,row=None):
        from .download_tracking import session_download,download_view,download_receipt,prefill_download
        if row is None:
            with self.lock:
                folder, row = self._read(session_id)
                if prefill_download(self, row):
                    atomic_json(folder/'result-review.json', row)
        view=download_view(session_download(self.directory,session_id),row,download_receipt(self.database,session_id))
        candidates=[]
        if view and view['candidates']:
            catalog=ReadOnlyCatalog(self.database)
            try:
                for candidate in view['candidates']:
                    song=self._song(catalog,candidate['song_id'])
                    if song and song['root_id']==candidate['root_id'] and song['relative_path']==candidate['relative_path']:
                        song['comparison']=self._comparison(song,row['draft']['values']);candidates.append(song)
            finally:catalog.close()
        return dict(download_evidence=view, candidates=candidates,
                    download_prefill=copy.deepcopy(row['draft']) if row['draft'].get('identity_binding_method') == 'guest_download_stable_new_folder_title' else None)

    def _assistance(self, row, draft, official_source_confirmed=False):
        """An unsaved proposal: never changes raw OCR, flags or submitted reviews."""
        values = draft['values']; selection = row['locked_session'].get('selection') or {}
        fields = copy.deepcopy(selection.get('ocr_fields', selection.get('fields') or {}))
        raw = row['ocr']['fields']
        if not fields.get('title', {}).get('value'):
            fields['title'] = copy.deepcopy(raw.get('title', {}))
        observation_conflicts = []
        for key in ('difficulty','game_level','total_notes','duration_seconds'):
            supplied = values.get(key)
            if supplied is not None and fields.get(key, {}).get('value') != supplied:
                if fields.get(key,{}).get('value') is not None:
                    observation_conflicts.append(key)
                    continue
                # Manually entered chart evidence is scoped to this request. It
                # is not rewritten into OCR or marked as a verified observation.
                if key=='game_level' and raw.get(key,{}).get('value')==supplied and row['ocr'].get('footer_level_scope')!='host_only_not_local_chart':
                    fields[key]=copy.deepcopy(raw[key])
                else:
                    fields[key] = dict(value=supplied,confidence=1.0,method='review_form_input')
        catalog = ReadOnlyCatalog(self.database)
        try:
            sid = draft.get('song_id'); method = 'explicit_song_selection' if sid is not None else None
            match = None
            if sid is None and not observation_conflicts and (official_source_confirmed is True or selection.get('source_type') == 'official'):
                match = catalog.match_official(fields, source_confirmed=True)
                sid = match.get('confirmed_song_id')
                if sid is not None:method = 'official_k_verified_chart'
            if sid is None and selection.get('source_type') == 'custom' and (selection.get('pack') or {}).get('status') == 'confirmed':
                refs = ManualReferences(self.directory, self.database)
                pack = selection['pack']['pack_id']
                match = catalog.match(fields, pack, 'custom')
                ref = refs.safe_lookup(fields, pack, 'custom', catalog, match)
                if ref:
                    sid = ref['song_id']; method = 'confirmed_manual_reference'
            song = self._song(catalog, sid) if type(sid) is int else None
            gd = next((d for d in (song or {}).get('difficulties', []) if d['difficulty'] == values.get('difficulty')),None)
            patch = {}; conflicts = list(observation_conflicts)
            if gd:
                for key in ('game_level','total_notes','duration_seconds'):
                    verified = key == 'game_level' and gd.get('level_source') or (
                        gd.get('notes_verified' if key=='total_notes' else 'duration_verified'))
                    if gd.get(key) is None or not verified or key in gd.get('manual_reference_conflicts',[]):continue
                    if values.get(key) is None:patch[key] = gd[key]
                    elif values[key] != gd[key]:conflicts.append(key)
            return dict(selected_song=song, patch=patch, method=method, conflicts=conflicts,
                matching=match, applies_automatically=bool(song and not conflicts and
                    (selection.get('source_type')=='official' or method=='confirmed_manual_reference')),
                persisted=False, confirmation_flags_changed=False, writes_play_record=False)
        finally:catalog.close()

    def assist(self, body):
        with self.lock:
            _,row = self._read(body.get('session_id'))
            draft = self._body(row, body)
            return self._assistance(row,draft,body.get('official_source_confirmed') is True)

    def _body(self, row, body):
        values = body.get('values')
        if not isinstance(values, dict) or set(values)-set(EDITABLE):
            raise LibraryError('成績欄位不正確。')
        merged = copy.deepcopy(row['draft'])
        merged['values'].update(values)
        merged.update({k: body.get(k, False) for k in ('notes_verified', 'player_verified', 'identity_reviewed', 'duration_verified')})
        row_id=body.get('player_row_id')
        if row_id is not None and row_id not in {p['row_id'] for p in self.player_rows(row)}:
            raise LibraryError('本人列不在這張原始結算中。',409)
        merged['player_row_id']=row_id
        note=body.get('confirmation_note','')
        if not isinstance(note,str) or len(note)>2000:raise LibraryError('確認依據須為 2000 字以內。')
        merged['confirmation_note']=note
        basis=body.get('max_basis') or {}
        if not isinstance(basis,dict) or set(basis)-{'context','notes','source','verified','evidence'}:raise LibraryError('MAX 基礎欄位不正確。')
        if not isinstance(basis.get('evidence',''),str) or len(basis.get('evidence',''))>2000:raise LibraryError('MAX 驗證證據須為2000字內。')
        if basis:merged['max_basis']=copy.deepcopy(basis)
        else:merged.pop('max_basis',None)
        review=body.get('note_review') or {}
        if not isinstance(review,dict) or set(review)-{'context','source','judgments','identity','source_confirmed','difference','note'}:raise LibraryError('音符差異核對欄位不正確。')
        if not isinstance(review.get('note',''),str) or len(review.get('note',''))>2000:raise LibraryError('差異備註須在2000字內。')
        if review:merged['note_review']=copy.deepcopy(review)
        else:merged.pop('note_review',None)
        song_id = body.get('song_id')
        if song_id is not None and type(song_id) is not int:
            raise LibraryError('Song ID 不正確。')
        merged['song_id'] = song_id
        if song_id is not None:
            catalog = ReadOnlyCatalog(self.database)
            try:
                song = self._song(catalog, song_id)
                if not song or song['root_id'] != body.get('root_id') or song['relative_path'] != body.get('relative_path'):
                    raise LibraryError('Song ID／Root／SongFolder 不一致，請重新選擇。', 409)
                previous = merged.get('song_identity') or {}
                # Keep the user's saved identity snapshot stable when newly
                # reusable metadata becomes available after this same review.
                if not (previous.get('song_id')==song_id and previous.get('root_id')==song['root_id'] and
                        previous.get('relative_path')==song['relative_path']):
                    merged['song_identity'] = song
            finally:
                catalog.close()
        else:
            merged.pop('song_identity', None)
        return merged

    def preview(self, body):
        with self.lock:
            _, row = self._read(body.get('session_id'))
            return self._preview(row, self._body(row, body))

    def save(self, body):
        with self.lock:
            folder, row = self._read(body.get('session_id'))
            if row['status'] == 'discarded':
                raise LibraryError('此結算已捨棄，原始證據仍保留。', 409)
            if body.get('review_id') != row['review_id']:
                raise LibraryError('結算身份已變更，請重新載入。', 409)
            draft = self._body(row, body)
            preview = self._preview(row, draft)
            confirmed = body.get('confirm') is True
            if confirmed and (body.get('acknowledge') is not True or not preview['can_confirm']):
                raise LibraryError('仍有必要欄位或衝突待確認，請核對畫面與歌曲身份。', 409)
            digest = fingerprint(dict(draft=draft, confirmed=confirmed))
            if row.get('last_save_fingerprint') == digest:
                return self.detail(row['session_id'])
            if type(body.get('revision')) is not int or body['revision'] != row['revision']:
                raise LibraryError('此成績已在別處修改，請重新載入。', 409)
            # Invalid supplied numbers cannot be persisted as an apparently valid draft.
            if any(e['code']!='judgment_total_mismatch' for e in preview['errors']) or any(i['code'] == 'invalid_grade' for i in preview['issues']):
                raise LibraryError('數字或成績評級無效，未儲存。')
            row['history'].append(dict(at=utc(), previous_draft=row['draft'], previous_status=row['status'],
                                       source='explicit_user_review', revision=row['revision']))
            row.update(draft=draft, revision=row['revision']+1, updated_at=utc(), last_save_fingerprint=digest,
                status=('confirmed_manual' if preview['max_score'] is not None else 'reviewed_pending_max_basis') if confirmed else 'draft')
            row['note_sources'] = preview['note_sources']
            row['note_exception'] = preview['note_exception']
            if confirmed:
                row['identity_confirmation']=dict(source='explicit_user_review',confirmed_at=utc(),
                    song_id=draft['song_id'],song_identity=draft.get('song_identity'),
                    observed_pack=(row['locked_session'].get('selection') or {}).get('pack'),
                    values=copy.deepcopy(draft['values']),player_row_id=row['draft'].get('player_row_id'),
                    screenshot_sha256=row['screenshot_sha256'],note=draft['confirmation_note'],
                    acquisition_method='unknown',acquisition_verified=False)
                self._reusable_mapping(row, draft, preview)
            else:
                row.pop('reusable_mapping', None)
            atomic_json(folder/'result-review.json', row)
            return self.detail(row['session_id'])

    def _reusable_mapping(self, row, draft, preview):
        row.pop('reusable_mapping', None)
        song = preview['selected_song']; v = draft['values']; title = row['ocr']['fields'].get('title',{})
        if not (song and song['source_type']=='custom' and draft.get('identity_reviewed') is True and
                draft.get('duration_verified') is True and preview['notes_verified'] and
                preview['note_sources']['status']=='consistent' and title.get('confidence',0)>=.9 and
                title.get('value') and not title.get('reason')):return
        if any(v.get(k) is None for k in ('difficulty','game_level','total_notes','duration_seconds')):return
        row['reusable_mapping'] = dict(song_id=song['song_id'],root_id=song['root_id'],
            relative_path=song['relative_path'],pack_id=song['pack_id'],source_type='custom',
            values=dict(title=title['value'],**{k:v[k] for k in ('difficulty','game_level','total_notes','duration_seconds')}),
            confirmation_source='explicit_user_review',confirmed_at=row['identity_confirmation']['confirmed_at'],
            review_id=row['review_id'],screenshot_sha256=row['screenshot_sha256'])

    def image(self, session_id):
        return self._source(session_id)[2]
