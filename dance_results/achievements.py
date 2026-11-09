"""Presentation of verified history and transaction-local achievement comparisons.

No score rules, OCR assumptions or writes. The caller saves feedback in the
existing immutable evidence JSON only after the normal submission gates pass.
"""
from datetime import datetime, timezone
import json

from .scoring import percentage_display

RANK={'UNPLAYED':0,'FAILED':1,'CLEAR':2,'AC':3,'ACP':4}
from .bests_policy import ELIGIBLE,SCORE_ELIGIBLE,LAMP_ELIGIBLE,VERIFIED_EX,TIME_ORDER,EX_POLICY
from .score_comparison import comparison


def empty_progress(label):
    return dict(difficulty=label,difficulty_id=None,best_lamp='UNPLAYED',best_ex_score=None,
        best_score_record=None,best_lamp_record=None,best_lamp_record_id=None,latest_record=None,record_count=0,
        completed_count=0,recently_updated=False,last_feedback=None,game_level=None,total_notes=None,notes_verified=False)


def record_summary(row):
    if row is None:return None
    r=dict(row)
    maximum=r.get('max_score');ex=r.get('ex_score')
    difference=maximum-ex if maximum is not None and maximum>0 and ex is not None else None
    r['max_policy']=r.pop('scoring_version',None)
    r['max_label']='本系統自訂 MAX' if r['max_policy']=='custom-judged-v1' else '歷史／另行驗證 MAX'
    r['display']=(f'MAX -{difference}' if difference is not None and 0<=difference<=150 else percentage_display(ex,maximum)) if r.get('basis_verified') else None
    raw=r.pop('feedback_json',None)
    r['max_status']='unconfirmed' if maximum is None else ('confirmed' if r.get('basis_verified') else 'legacy_basis_unreviewed')
    r.pop('basis_verified',None)
    r['feedback']=json.loads(raw) if isinstance(raw,str) else None
    return r


RECORD_FIELDS="json_extract(r.evidence_json,'$.scoring_version') AS scoring_version,json_extract(r.evidence_json,'$.played_at_source') AS played_at_source,json_extract(r.evidence_json,'$.max_basis_verification.verified') AS basis_verified,r.record_id,r.ex_score,r.max_score,r.lamp_status,r.completion_status,r.played_at,r.confirmed_at,r.game_level,r.total_notes,json_extract(r.evidence_json,'$.achievement_feedback') AS feedback_json"


def progress_for_songs(db,song_ids):
    """Bounded to the requested song page; reuse indexed difficulty_bests."""
    ids=sorted(set(song_ids));result={i:[empty_progress(d) for d in ('EASY','NORMAL','HARD')] for i in ids}
    if not ids:return result
    marks=','.join('?' for _ in ids)
    for row in db.execute('SELECT d.song_id,d.difficulty,d.game_level,d.total_notes,d.notes_verified,v.* FROM game_difficulties d '
                          'JOIN difficulty_bests v ON v.difficulty_id=d.difficulty_id '
                          'WHERE d.song_id IN ('+marks+')',ids):
        label=row['difficulty'];sid=row['song_id'];did=row['difficulty_id']
        p=next(x for x in result[sid] if x['difficulty']==label)
        p.update(difficulty_id=did,best_lamp=row['best_clear_lamp'],best_ex_score=row['highest_ex_score'])
        p.update(game_level=row['game_level'],total_notes=row['total_notes'],notes_verified=bool(row['notes_verified']))
        counts=db.execute('SELECT COUNT(*),COALESCE(SUM(r.completion_status=\'COMPLETED\'),0) FROM play_records r WHERE r.difficulty_id=? AND '+ELIGIBLE,(did,)).fetchone()
        p.update(record_count=counts[0],completed_count=counts[1])
        if not counts[0]:continue
        p['latest_record']=record_summary(db.execute('SELECT '+RECORD_FIELDS+' FROM play_records r WHERE r.difficulty_id=? AND '+ELIGIBLE+' ORDER BY julianday(r.played_at) DESC,r.record_id DESC LIMIT 1',(did,)).fetchone())
        p['best_score_record']=record_summary(db.execute('SELECT '+RECORD_FIELDS+' FROM play_records r WHERE r.difficulty_id=? AND '+SCORE_ELIGIBLE+' ORDER BY r.ex_score DESC,'+TIME_ORDER+' LIMIT 1',(did,)).fetchone())
        p['best_lamp_record']=record_summary(db.execute('SELECT '+RECORD_FIELDS+' FROM play_records r WHERE r.difficulty_id=? AND r.lamp_status=? AND '+LAMP_ELIGIBLE+' ORDER BY '+TIME_ORDER+' LIMIT 1',(did,p['best_lamp'])).fetchone())
        p['best_lamp_record_id']=p['best_lamp_record']['record_id'] if p['best_lamp_record'] else None
        compared=comparison(db,sid,label)
        p['comparison']=compared
        p['comparison_paused']=compared['ranking_paused']
        p['comparison_scope']=EX_POLICY if db.execute('SELECT 1 FROM play_records r WHERE r.difficulty_id=? AND '+VERIFIED_EX+' LIMIT 1',(did,)).fetchone() else 'legacy_unclassified'
        p['ex_compatibility_basis']='confirmed identity, own row, nonnegative judgments, recognized scoring_version and EX=P*2+C; MAX definitions remain separate'
        p['legacy_bests']=dict(db.execute("SELECT MAX(CASE WHEN r.completion_status='COMPLETED' THEN r.ex_score END) AS best_ex, MAX(CASE r.lamp_status WHEN 'ACP' THEN 4 WHEN 'AC' THEN 3 WHEN 'CLEAR' THEN 2 WHEN 'FAILED' THEN 1 ELSE 0 END) AS lamp_rank FROM play_records r WHERE r.difficulty_id=? AND COALESCE(json_extract(r.evidence_json,'$.scoring_version'),'')!='custom-judged-v1' AND ("+ELIGIBLE+")",(did,)).fetchone())
        if p['comparison_scope']==EX_POLICY and not compared['ranking_paused']:
            p['comparison_basis_notes']=p['best_score_record']['total_notes'] if p['best_score_record'] else None
        if compared['ranking_paused'] and p['best_score_record']:
            p['best_score_record']['display']=None
        p['last_feedback']=p['latest_record']['feedback']
        from .performance import update_summary
        proven=bool(db.execute('SELECT 1 FROM play_records r WHERE r.record_id=? AND '+VERIFIED_EX,(p['latest_record']['record_id'],)).fetchone())
        p['last_update']=update_summary(p['latest_record'],p['last_feedback'],eligible=proven)
        try:
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(p['latest_record']['confirmed_at'])).total_seconds()
            p['recently_updated']=0<=age<600
        except (TypeError,ValueError):pass
    return result


def feedback(before,*,ex_score,lamp,completion,comparison_paused=False,new_policy=False):
    """Two independent strict comparisons; a verified FAILED can improve EX."""
    old_score=before['best_ex_score'];old_lamp=before['best_lamp']
    if comparison_paused:
        return dict(version=2,first_record=False,first_clear=False,first_ac=False,first_acp=False,new_score=False,new_lamp=False,events=['SAVED'],primary='SAVED',old_best_score=old_score,best_ex_score=old_score,score_delta=None,old_best_lamp=old_lamp,best_lamp=old_lamp,current_lamp=lamp,tone='SAVED',comparison_paused=True)
    if before.get('comparison_scope')=='legacy_unclassified' and old_score is not None:
        return dict(version=2,first_record=False,first_clear=False,first_ac=False,first_acp=False,new_score=False,new_lamp=False,events=['SAVED'],primary='SAVED',old_best_score=None,best_ex_score=ex_score,score_delta=None,old_best_lamp='UNPLAYED',best_lamp=lamp or 'UNPLAYED',current_lamp=lamp,tone='SAVED',new_comparison_group=True)
    completed=completion=='COMPLETED'
    new_score=completion in ('COMPLETED','FAILED') and (old_score is None or ex_score>old_score)
    best_lamp=lamp if RANK.get(lamp,0)>RANK[old_lamp] else old_lamp
    new_lamp=RANK[best_lamp]>RANK[old_lamp]
    first_record=before['record_count']==0
    first_clear=completed and before['completed_count']==0
    first_ac=completed and lamp=='AC' and RANK[old_lamp]<RANK['AC']
    first_acp=completed and lamp=='ACP' and RANK[old_lamp]<RANK['ACP']
    flags=dict(first_record=first_record,first_clear=first_clear,first_ac=first_ac,first_acp=first_acp,
               new_score=bool(new_score),new_lamp=new_lamp)
    events=[key.upper() for key in ('first_record','first_clear','first_ac','first_acp','new_score','new_lamp') if flags[key]]
    if not events:events=['SAVED']
    if first_acp:primary='FIRST ACP'
    elif new_lamp and best_lamp=='ACP':primary='NEW LAMP'
    elif first_ac:primary='FIRST AC'
    elif new_lamp and best_lamp=='AC':primary='NEW LAMP'
    elif first_clear:primary='FIRST CLEAR'
    elif new_lamp and best_lamp!='FAILED':primary='NEW LAMP'
    elif new_score:primary='NEW SCORE'
    elif first_record:primary='FIRST RECORD'
    else:primary='SAVED'
    return dict(version=3,**flags,events=events,primary=primary,old_best_score=old_score,
        ex_comparison_policy=EX_POLICY,score_update=dict(type='SCORE_UPDATE',previous=old_score,current=ex_score,improvement=ex_score-old_score if old_score is not None else None,first_best=old_score is None) if new_score else None,
        lamp_update=dict(type='LAMP_UPDATE',previous=old_lamp,current=best_lamp) if new_lamp else None,
        best_ex_score=ex_score if new_score else old_score,score_delta=ex_score-old_score if new_score and old_score is not None else None,
        old_best_lamp=old_lamp,best_lamp=best_lamp,current_lamp=lamp,
        tone='SCORE' if new_score and not completed else 'FAILED' if not completed else (best_lamp if new_lamp else 'SCORE' if new_score else 'SAVED'))
