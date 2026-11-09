"""Per-result human exception authorization. Never infers a counting rule."""
from .monitor_tracking import fingerprint


def authorization(row, draft, notes):
    context=fingerprint(dict(session_id=row['session_id'],review_id=row['review_id'],
        screenshot=row['screenshot_sha256'],song_id=draft.get('song_id'),
        identity=(draft.get('song_identity') or {}).get('relative_path'),
        player_row_id=draft.get('player_row_id'),values=draft['values'],
        database=notes['database_total_notes'],selected=notes['selected_total_notes']))
    review=draft.get('note_review') or {}
    source=review.get('source')
    source_ok=(source=='catalog' and notes['database_notes_verified'] and
               notes['database_total_notes']==notes['verified_total_notes']) or (
        source=='selection' and notes['selected_total_notes']==notes['verified_total_notes'] and
        (notes['selected_source'].get('confidence') or 0)>=.9 and not notes['selected_source'].get('reason')) or (
        source=='manual' and draft.get('notes_verified') is True and notes['verified_total_notes'] is not None)
    authorized=bool(notes['status']=='count_discrepancy_pending' and not notes['selected_database_conflict'] and
        source_ok and draft.get('identity_reviewed') is True and draft.get('player_verified') is True and
        review.get('context')==context and all(review.get(k) is True for k in ('judgments','identity','source_confirmed','difference')))
    return dict(authorized=authorized,context=context,source=source,review=review,
        difference=notes['judgment_difference'],counting_rule_verified=False,
        scope='this_session_and_review_only',lamp_pending=bool(authorized and
            draft['values'].get('bad')==0 and draft['values'].get('miss')==0))


def result_time(session):
    """Only a timezone-aware result/end timestamp, never submission or start."""
    from datetime import datetime
    result=session.get('result') or {}
    for source,value in [('session.ended_at',session.get('ended_at')),
                         ('result.captured_at',result.get('captured_at')),
                         ('result.observed_at',result.get('observed_at'))]:
        try:
            if isinstance(value,str) and datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None:
                return value,source
        except ValueError:pass
    return None,None
