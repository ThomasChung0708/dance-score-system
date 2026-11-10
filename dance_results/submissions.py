"""Explicit, backed-up and idempotent formal submissions of saved human reviews."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from .library_service import LibraryError, integer
from .monitor_tracking import fingerprint, utc
from .ocr_matching import normalize
from .scoring import calculate, percentage_display
from .sqlite_safety import verified_backup, check_integrity
from .achievements import progress_for_songs, feedback
from .note_review import result_time
from .result_consistency import screen_catalog, observation, screen_totals, validate

def basis_is_custom(score):return score.get('max_policy')=='custom-judged-v1'


class SubmissionService:
    def __init__(self, library, reviews):
        self.library=library
        self.reviews=reviews
        self.auto_allowed=lambda sid:False

    @staticmethod
    def request_hash(body):
        # An old identical HTTP retry can return the immutable stored receipt even
        # after the sidecar was edited. Different requests cannot overwrite it.
        payload={k:body.get(k) for k in ('session_id','review_id','revision','values','song_id',
            'root_id','relative_path','player_row_id','notes_verified','identity_reviewed','player_verified',
            'duration_verified','confirmation_note','play_mode','note_review','submission_mode')}
        if body.get('max_basis'):payload['max_basis']=body['max_basis']
        return fingerprint(payload)

    def _existing(self,db,session_id,review_id):
        return db.execute('SELECT * FROM play_records WHERE session_id=? OR review_id=?',(session_id,review_id)).fetchone()

    def _build(self, db, body, *, automatic=False):
        folder,row=self.reviews._read(body.get('session_id'))
        _,session,_,sha=self.reviews._source(row['session_id'])
        draft=self.reviews._body(row,body)
        preview=self.reviews._preview(row,draft)
        issues=list(preview['issues']);warnings=list(preview['warnings'])
        def error(code,field='review'):issues.append(dict(code=code,field=field))
        if body.get('review_id')!=row['review_id'] or body.get('revision')!=row['revision']:
            error('review_revision_changed')
        if not automatic and (row['status'] not in ('confirmed_manual','reviewed_pending_full_notes','reviewed_pending_duration','reviewed_pending_max_basis') or not row.get('identity_confirmation')):
            error('save_confirmed_review_first')
        if not automatic and fingerprint(draft)!=fingerprint(row['draft']):error('save_confirmed_review_first')
        if fingerprint(session)!=fingerprint(row['locked_session']):error('session_evidence_changed','session_id')
        if draft.get('identity_reviewed') is not True:error('identity_requires_explicit_confirmation','song_id')
        if draft.get('player_verified') is not True:error('local_player_requires_review','player_name')
        mode=body.get('play_mode')
        if mode not in ('single','multiplayer'):error('play_mode_required','play_mode')
        if row['ocr'].get('multiplayer') and mode=='single':error('multiplayer_result_requires_multiplayer_mode','play_mode')
        values=copy.deepcopy(draft['values'])
        chosen=next((r for r in self.reviews.player_rows(row) if r['row_id']==draft.get('player_row_id')),None)
        if chosen is None and row['ocr'].get('local_player_status')=='CONFIRMED':
            own=[r for r in self.reviews.player_rows(row)if normalize(r['name'])==normalize(row['ocr']['fields']['player_name']['value'])]
            if len(own)==1:chosen=own[0]
            expected=row['ocr']['fields']['player_name']['value']
        else:expected=chosen['name'] if chosen else None
        if expected and (not isinstance(values.get('player_name'),str) or normalize(values.get('player_name'))!=normalize(expected)):
            error('player_row_name_conflict','player_name')
        if mode=='multiplayer' and chosen is None:error('manual_player_row_required','player_row_id')
        song=db.execute('SELECT s.*,p.name AS pack_name FROM songs s LEFT JOIN packs p ON p.pack_id=s.pack_id WHERE s.song_id=?',(draft.get('song_id'),)).fetchone()
        gd=None
        if not song:error('song_identity_not_confirmed','song_id')
        else:
            if song['root_id']!=body.get('root_id') or song['relative_folder']!=body.get('relative_path'):
                error('song_path_changed','song_id')
            gd=db.execute('SELECT * FROM game_difficulties WHERE song_id=? AND difficulty=?',(song['song_id'],values.get('difficulty'))).fetchone()
            if song['source_type']=='official' and gd is None:error('official_difficulty_not_in_catalog','difficulty')
            if gd and gd['game_level'] is not None and gd['game_level']!=values.get('game_level'):
                error('verified_catalog_level_conflict','game_level')
        verified=draft.get('notes_verified') is True and values.get('total_notes') is not None
        notes_source='manual_game_screen' if verified else None
        if gd and gd['notes_verified'] and gd['total_notes'] is not None:
            if values.get('total_notes') is None:
                values['total_notes']=gd['total_notes']
                issues=[i for i in issues if i['code']!='verified_catalog_notes_conflict']
            if values['total_notes']!=gd['total_notes']:
                if screen_catalog(dict(gd)):
                    error('verified_catalog_notes_conflict','total_notes')
            else:
                verified=True;notes_source=gd['notes_source']
        if not verified:
            # Even a manually typed number is not a confirmed full song length.
            values['total_notes']=None
        exception=preview['note_exception']
        if automatic:
            from .verified_auto import automatic_request
            expected,auto_issues=automatic_request(self.reviews,row)
            if not self.auto_allowed(row['session_id']):error('automatic_mode_disabled')
            for problem in auto_issues:error(problem)
            if expected is None or self.request_hash(body)!=self.request_hash(expected):error('automatic_evidence_changed')
            if exception['authorized']:error('automatic_note_exception_forbidden')
        basis=preview['max_basis_verification']
        score=calculate(values,notes_verified=verified,identity_confirmed=True,note_exception=preview['note_sources']['status']=='count_discrepancy_pending',max_basis=basis,comparison_conflict=preview['comparison']['ranking_paused'])
        issues.extend(score['errors'])
        # Unknown MAX is a legitimate snapshot, not a reason to change judgments.
        for key in ('perfect','cool','bad','miss'):
            if type(values.get(key)) is not int:error('judgment_required',key)
        if values.get('completion_status') not in ('COMPLETED','FAILED'):error('completion_required','completion_status')
        played_at,time_source=result_time(session)
        if played_at is None:error('session_result_time_required','played_at')
        if not automatic and not row.get('identity_confirmation',{}).get('confirmed_at'):error('saved_confirmation_time_required')
        # The exact immutable evidence and current catalog snapshot are part of
        # the confirmation token, guarding against edits between preview/submit.
        evidence=dict(review_id=row['review_id'],review_revision=row['revision'],
            review=copy.deepcopy(row),session=session,screenshot_sha256=sha,
            screenshot_path=str((folder/'result.png').resolve()),song=dict(song) if song else None,
            difficulty=dict(gd) if gd else None,values=values,notes_verified=verified,
            note_sources=preview.get('note_sources'),note_exception=exception,max_basis_verification=basis,comparison=preview['comparison'],lamp_verification=dict(source='system_result_judgments' if values.get('completion_status')=='COMPLETED' else 'game_over_evidence',independent_game_lamp_verified=False),played_at_source=time_source,
            result_verification=dict(confirmed=not issues,source='verified_ocr' if automatic else 'explicit_human_review'),
            automatic=dict(authorized=automatic,method='revalidated_session_ocr_catalog' if automatic else None),
            full_notes_source=notes_source,play_mode=mode,player_row_id=chosen['row_id'] if chosen else None)
        evidence['screen_note_observation'] = observation(values)
        raw = row['ocr'].get('fields') or {}
        evidence['screen_consistency'] = validate(
            {k: item.get('value') for k, item in raw.items()},
            screen_totals(raw, session.get('selection') or {}))
        screen = evidence['screen_note_observation']
        if screen is not None and gd is not None and gd['total_notes'] is not None:
            if screen['total_notes'] != gd['total_notes']:
                disagreement = dict(code='catalog_screen_notes_disagreement',
                                    catalog_value=gd['total_notes'], catalog_source=gd['notes_source'],
                                    screen_value=screen['total_notes'], provenance='screen_observed')
                evidence['catalog_note_disagreement'] = disagreement
                warnings.append(dict(disagreement, field='total_notes'))
        if automatic and screen is not None:
            evidence['full_notes_source'] = 'screen_observed'
        from .download_tracking import session_download
        evidence['download_evidence']=session_download(self.reviews.directory,row['session_id'])
        token_evidence=copy.deepcopy(evidence)
        token_evidence['comparison'].pop('observations',None)
        token=fingerprint(token_evidence)
        summary=dict(session_id=row['session_id'],review_id=row['review_id'],review_revision=row['revision'],
            song_id=draft.get('song_id'),title=(song['saved_title'] or song['latest_title']) if song else None,
            pack_name=song['pack_name'] if song else None,relative_path=song['relative_folder'] if song else None,
            source_type=song['source_type'] if song else None,play_mode=mode,played_at=played_at,
            values=values,score=score,full_notes_verified=verified,
            max_status='confirmed' if score['max_score'] is not None else 'unconfirmed',
            player_row_id=evidence['player_row_id'],creates_game_difficulty=bool(song and not gd))
        unique={(i['code'],i['field']):i for i in issues}
        return dict(can_submit=not unique,issues=list(unique.values()),warnings=warnings,summary=summary,
                    submission_token=token,existing_record=None),evidence

    def prepare(self,body):
        with self.reviews.lock,self.library.open() as library:
            old=self._existing(library.connection,body.get('session_id'),body.get('review_id'))
            if old:
                return dict(can_submit=False,issues=[dict(code='already_submitted',field='review')],
                            existing_record=self._record(library.connection,old['record_id']))
            return self._build(library.connection,body)[0]

    def _checkpoint(self,name):
        """Fault-injection seam for real transaction rollback tests."""

    def submit_verified(self,session_id):
        from .verified_auto import automatic_request
        _,row=self.reviews._read(session_id)
        body,issues=automatic_request(self.reviews,row)
        if issues:raise LibraryError('自動儲存轉待確認：'+', '.join(issues),409)
        with self.reviews.lock,self.library.open() as library:
            old=self._existing(library.connection,session_id,row['review_id'])
            if old:return dict(record=self._record(library.connection,old['record_id']),idempotent=True)
            prepared,_=self._build(library.connection,body,automatic=True)
        if not prepared['can_submit']:raise LibraryError('自動儲存轉待確認：'+', '.join(i['code'] for i in prepared['issues']),409)
        return self.submit({**body,'submission_token':prepared['submission_token'],'submit_acknowledge':True},automatic=True)

    def submit(self,body,*,automatic=False):
        if body.get('submit_acknowledge') is not True:raise LibraryError('請先核對正式提交摘要並明確確認。',409)
        if not self.library._write_lock.acquire(timeout=10):raise LibraryError('歌曲庫正在更新，請稍後再提交。',409)
        try:
            with self.reviews.lock,self.library.open(False) as library:
                db=library.connection
                try:
                    db.execute('BEGIN IMMEDIATE')
                    request_hash=self.request_hash(body)
                    old=self._existing(db,body.get('session_id'),body.get('review_id'))
                    if old:
                        if old['session_id']!=body.get('session_id') or old['review_id']!=body.get('review_id') or old['submission_hash']!=request_hash:
                            raise LibraryError('此結算已提交不同內容；保留原成績，不覆寫。',409)
                        db.rollback()
                        return dict(record=self._record(db,old['record_id']),idempotent=True)
                    prepared,evidence=self._build(db,body,automatic=automatic)
                    if not prepared['can_submit']:raise LibraryError('仍有必要資料待確認：'+', '.join(i['code'] for i in prepared['issues']),409)
                    if body.get('submission_token')!=prepared['submission_token']:raise LibraryError('摘要或來源已變更，請重新核對正式提交摘要。',409)
                    backup=verified_backup(db,self.library.path,'pre-submit')
                    # Re-read sidecars after potentially slow backup, before any
                    # mutation. A changed review/session/image needs a new preview.
                    checked,evidence=self._build(db,body,automatic=automatic)
                    if not checked['can_submit'] or checked['submission_token']!=prepared['submission_token']:
                        raise LibraryError('備份期間確認資料已變更，未寫入成績。',409)
                    s=checked['summary'];v=s['values'];score=s['score'];song=evidence['song'];gd=evidence['difficulty']
                    if gd is None:
                        gd_id=db.execute('INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(?,?,?,\'manual\',?,?,?)',
                            (song['song_id'],v['difficulty'],v['game_level'],v['total_notes'],
                             'game_screen' if evidence['screen_note_observation'] and
                             evidence['screen_note_observation']['total_notes'] == v['total_notes'] else
                             'manual' if s['full_notes_verified'] else None,int(s['full_notes_verified']))).lastrowid
                    else:gd_id=gd['difficulty_id']
                    self._checkpoint('after_difficulty')
                    from .bests_policy import EX_POLICY
                    timestamp=utc();evidence.update(submitted_at=timestamp,backup_path=backup,request_hash=request_hash,ex_comparison_policy=EX_POLICY,scoring_version='custom-judged-v1' if basis_is_custom(score) else 'iidx-v2-independent-max')
                    previous=next(p for p in progress_for_songs(db,[song['song_id']])[song['song_id']] if p['difficulty']==v['difficulty'])
                    evidence['achievement_feedback']=feedback(previous,ex_score=score['ex_score'],
                        lamp=score['lamp_preview'],completion=v['completion_status'],comparison_paused=evidence['comparison']['ranking_paused'],new_policy=basis_is_custom(score))
                    db.execute('INSERT INTO play_sessions(session_id,player_name,play_mode,source_type,local_difficulty,local_game_level,host_result_level,display_song_title,display_pack_name,room_status,player_status,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,\'result\',?,?)',
                        (s['session_id'],v['player_name'],s['play_mode'],song['source_type'],v['difficulty'],v['game_level'],
                         evidence['review']['ocr']['fields']['game_level']['value'] if s['play_mode']=='multiplayer' else None,
                         s['title'],s['pack_name'],'failed' if v['completion_status']=='FAILED' else 'completed',
                         json.dumps(dict(original_session=evidence['session'],review_id=s['review_id'],player_row_id=evidence['player_row_id']),ensure_ascii=False)))
                    self._checkpoint('after_session')
                    record=dict(session_id=s['session_id'],difficulty_id=gd_id,played_at=s['played_at'],
                        completion_status=v['completion_status'],perfect=v['perfect'],cool=v['cool'],bad=v['bad'],miss=v['miss'],
                        ex_score=score['ex_score'],max_score=score['max_score'],max_difference=score['difference'],percentage=score['percentage'],
                        lamp_status=score['lamp_preview'],total_notes=score['max_basis_notes'],notes_source='completed_judgment_sum' if basis_is_custom(score) else 'game_screen' if score['max_score'] is not None else None,
                        match_status='confirmed',validation_status='pending' if score['lamp_preview'] is None else 'verified',game_level=v['game_level'],max_combo=v['max_combo'],game_score=v['game_score'],
                        grade=v['grade'],screenshot_path=evidence['screenshot_path'],screenshot_sha256=evidence['screenshot_sha256'],
                        review_id=s['review_id'],review_revision=s['review_revision'],confirmed_at=timestamp,confirmation_method='verified_auto' if automatic else 'manual_note_exception' if evidence['note_exception']['authorized'] else 'manual_review',
                        submission_hash=request_hash,evidence_json=json.dumps(evidence,ensure_ascii=False))
                    columns=','.join(record);placeholders=','.join('?'for _ in record)
                    record_id=db.execute('INSERT INTO play_records('+columns+') VALUES('+placeholders+')',tuple(record.values())).lastrowid
                    self._checkpoint('after_record');check_integrity(db);self._checkpoint('before_commit')
                    db.commit()
                    return dict(record=self._record(db,record_id),idempotent=False,backup_path=backup)
                except BaseException:db.rollback();raise
        finally:self.library._write_lock.release()

    @staticmethod
    def _record(db,record_id,*,include_evidence=True):
        row=db.execute('SELECT r.*,d.song_id,d.difficulty,s.saved_title,s.latest_title,s.relative_folder,s.source_type,p.name AS pack_name,ps.player_name,ps.play_mode '
            'FROM play_records r JOIN play_sessions ps ON ps.session_id=r.session_id LEFT JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id '
            'LEFT JOIN songs s ON s.song_id=d.song_id LEFT JOIN packs p ON p.pack_id=s.pack_id WHERE r.record_id=?',(record_id,)).fetchone()
        if row is None:raise LibraryError('找不到成績。',404)
        result=dict(row);evidence=json.loads(result.pop('evidence_json'))
        result['title']=(evidence.get('song') or {}).get('saved_title') or result.pop('saved_title') or result.get('latest_title')
        result.pop('saved_title',None);result.pop('latest_title',None)
        snapshot=evidence.get('song') or {}
        for key in ('song_id','pack_name','source_type'):
            if key in snapshot:result[key]=snapshot[key]
        if snapshot.get('relative_folder') is not None:result['relative_folder']=snapshot['relative_folder']
        if (evidence.get('values') or {}).get('difficulty'):result['difficulty']=evidence['values']['difficulty']
        result['display']=(f"MAX -{result['max_difference']}" if result.get('max_difference') is not None and result['max_difference']<=150
                           else percentage_display(result['ex_score'],result['max_score']))
        result['max_policy']=evidence.get('scoring_version');result['comparison']=evidence.get('comparison');result['lamp_verification']=evidence.get('lamp_verification');result['max_label']='本系統自訂 MAX' if result['max_policy']=='custom-judged-v1' else '歷史／另行驗證 MAX'
        result['max_status']=('confirmed' if evidence.get('max_basis_verification',{}).get('verified') else 'legacy_basis_unreviewed') if result['max_score'] is not None else 'unconfirmed'
        result['note_sources']=evidence.get('note_sources',{})
        result['max_basis_notes']=evidence.get('note_sources',{}).get('max_basis_notes')
        result['catalog_total_notes']=result['note_sources'].get('catalog_total_notes',result['note_sources'].get('database_total_notes'))
        result['judged_total']=sum(result[k] for k in ('perfect','cool','bad','miss')) if all(type(result[k]) is int for k in ('perfect','cool','bad','miss')) else None
        from .score_comparison import comparison
        current_comparison=comparison(db,result.get('song_id'),result.get('difficulty'))
        result['comparison_at_submission']=result.get('comparison')
        result['comparison']=current_comparison
        result['achievement_feedback']=evidence.get('achievement_feedback')
        result['note_exception']=evidence.get('note_exception')
        result['lamp_pending']=result['lamp_status'] is None
        result['played_at_source']=evidence.get('played_at_source')
        result['played_at_verified']=bool(result['played_at_source'])
        from .performance import update_summary,score_status
        from .bests_policy import VERIFIED_EX
        eligible=bool(db.execute('SELECT 1 FROM play_records r WHERE r.record_id=? AND '+VERIFIED_EX,(record_id,)).fetchone())
        result['update_summary']=update_summary(result,result['achievement_feedback'],eligible=eligible)
        result['score_status']=score_status(result)
        if include_evidence:result['evidence']=evidence
        return result

    def history(self,*,song_id=None,difficulty=None,player_name=None,offset=0,limit=60,order='desc'):
        integer(offset,'起點');integer(limit,'筆數',1)
        if limit>100:raise LibraryError('每頁最多100筆。')
        if order not in ('asc','desc'):raise LibraryError('遊玩時間排序不正確。')
        if song_id is not None:integer(song_id,'Song ID',1)
        if difficulty is not None and difficulty not in ('EASY','NORMAL','HARD'):raise LibraryError('難度不正確。')
        if player_name is not None and (not isinstance(player_name,str) or len(player_name)>64):raise LibraryError('暱稱不正確。')
        where=[];args=[]
        for sql,value in [('d.song_id=?',song_id),('d.difficulty=?',difficulty),('p.player_name=?',player_name)]:
            if value is not None:where.append(sql);args.append(value)
        suffix=(' WHERE '+' AND '.join(where)) if where else ''
        joins=' FROM play_records r LEFT JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id JOIN play_sessions p ON p.session_id=r.session_id'
        with self.library.open() as library:
            db=library.connection;count=db.execute('SELECT count(*)'+joins+suffix,args).fetchone()[0]
            ids=[r[0]for r in db.execute('SELECT r.record_id'+joins+suffix+' ORDER BY julianday(r.played_at) '+order+',r.record_id '+order+' LIMIT ? OFFSET ?',(*args,limit,offset))]
            records=[self._record(db,i,include_evidence=False)for i in ids]
            bests=[]
            if song_id is not None:
                # Separate provenance for the highest verified EX and first best lamp.
                bests=[dict(r)for r in db.execute('SELECT * FROM difficulty_bests WHERE song_id=?'+(' AND difficulty=?' if difficulty else '')+' ORDER BY difficulty_id',(song_id,difficulty) if difficulty else (song_id,))]
                progress=progress_for_songs(db,[song_id])[song_id]
                for item in bests:
                    p=next(x for x in progress if x['difficulty']==item['difficulty'])
                    item.update(comparison_paused=p.get('comparison_paused',False),my_best_score=p['best_score_record'],best_lamp_record=p['best_lamp_record'],comparison_scope=p.get('comparison_scope'))
            return dict(records=records,total=count,offset=offset,limit=limit,bests=bests)

    def progress_history(self,*,song_id,difficulty=None,offset=0,limit=20,order='desc'):
        integer(song_id,'Song ID',1)
        with self.library.open() as library:
            if not library.connection.execute('SELECT 1 FROM songs WHERE song_id=?',(song_id,)).fetchone():
                raise LibraryError('找不到歌曲。',404)
        result=self.history(song_id=song_id,difficulty=difficulty,offset=offset,limit=limit,order=order)
        result.update(song_id=song_id,difficulty=difficulty,order=order,
                      event_source='immutable_play_record.achievement_feedback',reconstructed_events=False)
        return result

    def detail(self,record_id):
        integer(record_id,'Record ID',1)
        with self.library.open() as library:return self._record(library.connection,record_id)

    def image(self,record_id):
        record=self.detail(record_id);path=Path(record['screenshot_path'] or '')
        allowed=(self.reviews.directory/'sessions').resolve()
        if not path.is_absolute() or not path.resolve().is_relative_to(allowed) or path.is_symlink() or path.parent.is_symlink():
            raise LibraryError('結算原圖目前不在已登錄的監控目錄。',404)
        try:data=path.read_bytes()
        except OSError:raise LibraryError('原圖目前不存在；歷史成績仍保留。',404)
        if hashlib.sha256(data).hexdigest()!=record['screenshot_sha256']:raise LibraryError('原圖指紋已變更，保留歷史紀錄。',409)
        return data
