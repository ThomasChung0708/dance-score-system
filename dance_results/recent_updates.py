"""Recent My Best updates from immutable committed evidence, never scan times."""
from datetime import datetime
import json
from .bests_policy import VERIFIED_EX,EX_POLICY
from .game_sync import SOURCE as GAME_RESULT_SOURCE
from .score_comparison import comparison
from .scoring import percentage_display
from .note_review import result_time
from .songs import record_display_title


def recent_updates(db,limit=30):
    rows = db.execute(
        'SELECT r.*,COALESCE(r.song_id,d.song_id) AS bound_song_id,COALESCE(d.difficulty,ps.local_difficulty) AS difficulty,s.saved_title,s.latest_title,s.source_type,'
        's.relative_folder,p.name AS pack_name,ps.display_song_title FROM play_records r '
        'JOIN play_sessions ps ON ps.session_id=r.session_id '
        'LEFT JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id '
        'JOIN songs s ON s.song_id=COALESCE(r.song_id,d.song_id) LEFT JOIN packs p ON p.pack_id=s.pack_id '
        'WHERE ('+VERIFIED_EX+') ORDER BY r.record_id').fetchall()
    bests={};latest={};blocked={};skipped=0
    for row in rows:
        r=dict(row);r['song_id']=r.pop('bound_song_id');e=json.loads(r['evidence_json']);f=e.get('achievement_feedback') or {};policy=e.get('scoring_version')
        chart=(r['song_id'],r['difficulty']);key=(*chart,EX_POLICY)
        if chart not in blocked:blocked[chart]=comparison(db,*chart)['ranking_paused']
        previous=bests.get(key);ex=r['ex_score'];basis=r['total_notes']
        compatible=True # MAX basis is never inferred from a partial FAILED total.
        expected=previous['ex'] if previous else None
        qualifies=(not blocked[chart] and policy is not None and compatible and type(ex) is int and
            all(type(r[k]) is int and r[k]>=0 for k in ('perfect','cool','bad','miss')) and ex==2*r['perfect']+r['cool'] and
            (expected is None or ex>expected) and f.get('new_score') is True and 'NEW_SCORE' in f.get('events',[]) and
            f.get('old_best_score')==expected and f.get('best_ex_score')==ex and
            not f.get('new_comparison_group') and not f.get('comparison_paused'))
        # Every qualified historical result participates in the prior-best check,
        # including an ordinary SAVED that established a separate scoring group.
        if type(ex) is int and (previous is None or ex>previous['ex']):bests[key]=dict(ex=ex,basis=basis)
        if not qualifies:skipped+=1;continue
        try:
            played=datetime.fromisoformat(r['played_at'].replace('Z','+00:00'))
            if e.get('played_at_source')==GAME_RESULT_SOURCE:
                # From the game's results.jsonl: no OCR review exists; the line's own time is the result time.
                original,source=(e.get('game_result') or {}).get('at'),'game_result.at'
            else:
                original,source=result_time((e.get('review') or {}).get('locked_session') or {})
            if played.tzinfo is None or not original or not source or not e.get('played_at_source'):raise ValueError()
            if played!=datetime.fromisoformat(original.replace('Z','+00:00')):raise ValueError()
        except (ValueError,AttributeError):skipped+=1;continue
        difference=r['max_difference']
        basis_verified=(e.get('max_basis_verification') or {}).get('verified') is True
        r.update(title=record_display_title(db, e, r['song_id'], r.pop('display_song_title')),scoring_version=policy,
            previous_best_ex=expected,new_best_ex=ex,improvement=None if expected is None else ex-expected,
            update_type='FIRST BEST' if expected is None else 'NEW SCORE',
            display=(f'MAX -{difference}' if difference is not None and 0<=difference<=150 else percentage_display(ex,r['max_score'])) if basis_verified else None,
            max_status='confirmed' if basis_verified else 'unconfirmed',
            event_source='immutable_play_record.achievement_feedback',_sort_time=played.timestamp())
        from .performance import update_summary
        r['update_summary']=update_summary(r,f)
        for k in ('evidence_json','saved_title','latest_title'):r.pop(k,None)
        latest[chart]=r
    ordered=sorted(latest.values(),key=lambda r:(r['_sort_time'],r['record_id']),reverse=True)
    for r in ordered:r.pop('_sort_time')
    return dict(updates=ordered[:limit],total=len(ordered),limit=limit,skipped_unproven_records=skipped,
        source='validated_committed_achievement_feedback',schema_migration_required=False)
