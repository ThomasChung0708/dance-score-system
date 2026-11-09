"""Per-chart observed judgment ranges; no implicit reconciliation or writes."""
import json
from pathlib import Path

POLICY='custom-judged-v1'
SOURCE='completed_result_judged_total'


def total(values):
    counts=[values.get(k) for k in ('perfect','cool','bad','miss')]
    return sum(counts) if all(type(n) is int and 0<=n<=10**7 for n in counts) else None


def comparison(db,song_id,difficulty,judged_total=None,*,directory=None,session_id=None,max_basis_notes=None,max_basis_policy=None):
    observations={}
    full_bases={}
    for r in db.execute("SELECT r.session_id,r.record_id,r.completion_status,r.perfect,r.cool,r.bad,r.miss,r.evidence_json FROM play_records r JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id WHERE d.song_id=? AND d.difficulty=? AND r.match_status='confirmed'",(song_id,difficulty)):
        sid=r['session_id']
        if sid==session_id:continue
        e=json.loads(r['evidence_json']);basis=e.get('max_basis_verification') or {}
        if basis.get('verified') is True and type(basis.get('notes')) is int:
            full_bases.setdefault(basis.get('policy') or 'independent-max-v2',set()).add(basis['notes'])
        n=total(dict(r)) if r['completion_status']=='COMPLETED' else None
        if n is not None:observations[sid]=dict(session_id=sid,record_id=r['record_id'],judged_total=n,source='immutable_formal_record')
    # Saved human confirmations and reliably locked, high-confidence results
    # can stop automation even before their own formal submission.
    if directory:
        for p in (Path(directory)/'sessions').glob('*/result-review.json'):
            try:r=json.loads(p.read_text(encoding='utf8'))
            except (OSError,ValueError):continue
            sid=r.get('session_id')
            if not sid or sid==session_id or sid in observations:continue
            draft=r.get('draft') or {};v=draft.get('values') or {};confirmation=r.get('identity_confirmation') or {}
            human=bool(confirmation.get('source')=='explicit_user_review' and confirmation.get('confirmed_at') and
                confirmation.get('values')==v and confirmation.get('song_id')==draft.get('song_id') and
                all(draft.get(k) is True for k in ('identity_reviewed','player_verified')))
            if human:
                matched=draft.get('song_id')==song_id and v.get('difficulty')==difficulty
            else:
                s=r.get('locked_session') or {};selection=s.get('selection') or {};f=selection.get('fields') or {}
                raw=(r.get('ocr') or {}).get('fields') or {}
                reliable=lambda x:isinstance(x,dict) and x.get('value') is not None and x.get('confidence',0)>=.95 and not x.get('reason')
                matched=(s.get('song_id')==song_id and s.get('matching_status') in ('confirmed_manual','confirmed_manual_reference','unique_verified_candidate','confirmed_official_catalog') and
                    f.get('difficulty',{}).get('value')==difficulty and (r.get('ocr') or {}).get('local_player_status')=='CONFIRMED' and
                    all(reliable(raw.get(k)) for k in ('perfect','cool','bad','miss','completion_status')))
                v={k:x.get('value') for k,x in raw.items()}
            n=total(v)
            if matched and v.get('completion_status')=='COMPLETED' and n is not None:
                observations[sid]=dict(session_id=sid,review_id=r['review_id'],judged_total=n,source='saved_human_review' if human else 'reliable_locked_result')
    observed=sorted({v['judged_total'] for v in observations.values()})
    all_counts=set(observed)
    if type(judged_total) is int:all_counts.add(judged_total)
    if type(max_basis_notes) is int:full_bases.setdefault(max_basis_policy or 'independent-max-v2',set()).add(max_basis_notes)
    # Distinct MAX definitions are not the same counter. Only contradictory
    # complete judgment ranges or bases within one known policy pause EX updates.
    conflict=len(all_counts)>1 or any(len(counts)>1 for counts in full_bases.values())
    return dict(policy=POLICY,status='conflict' if conflict else 'consistent' if observed else 'first_observation',
        eligible=not conflict,ranking_paused=conflict,observed_totals=observed,current_judged_total=judged_total,
        observations=sorted(observations.values(),key=lambda r:r['session_id']),
        resolution='pending_manual_investigation' if conflict else None,
        verified_full_bases={k:sorted(v) for k,v in full_bases.items()},
        legacy_policy='recognized_EX_formulas_bridge_only_with_confirmed_identity_and_result_evidence; MAX_definitions_remain_separate')


def completed_basis(values,identity_confirmed):
    n=total(values)
    valid=bool(identity_confirmed and values.get('completion_status')=='COMPLETED' and n is not None and n>0)
    return dict(verified=valid,notes=n if valid else None,source=SOURCE if valid else None,
        policy=POLICY,scope='this_session_and_review_only',is_official_game_max=False,
        verification_method='confirmed_result_judgments_custom_policy' if valid else None,
        label='本系統自訂 MAX（結算判定合計 × 2）')
