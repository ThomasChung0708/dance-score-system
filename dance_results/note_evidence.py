"""Independent full-note provenance and explicitly conditional score estimates.

No counter is replaced with judgment totals. This module does not authorize a
formal submission or define exceptions to the game's unresolved counting rules.
"""
from .scoring import calculate, percentage_display


def positive(value):
    return value if type(value) is int and 0 < value <= 10**7 else None


def judged(values):
    nums = [values.get(k) for k in ('perfect','cool','bad','miss')]
    return sum(nums) if all(type(v) is int and 0 <= v <= 10**7 for v in nums) else None


def reviewed_reference(row):
    """Derive a read-only reference from complete earlier human confirmations.

    Old drafts and partially reviewed results are never upgraded. The caller
    still checks the actual image/Session and current catalog identity.
    """
    draft=row.get('draft') or {}; confirmation=row.get('identity_confirmation') or {}
    song=confirmation.get('song_identity') or {}; values=draft.get('values') or {}
    title=row.get('ocr',{}).get('fields',{}).get('title') or {}
    if row.get('status') not in ('confirmed_manual','reviewed_pending_max_basis') or confirmation.get('source')!='explicit_user_review':return None
    if any(draft.get(k) is not True for k in ('identity_reviewed','player_verified','notes_verified','duration_verified')):return None
    if confirmation.get('song_id')!=draft.get('song_id') or confirmation.get('values')!=values:return None
    if song.get('song_id')!=draft.get('song_id') or song.get('source_type')!='custom' or song.get('pack_id') is None:return None
    if not positive(values.get('duration_seconds')) or not title.get('value') or title.get('confidence',0)<.9 or title.get('reason'):return None
    if confirmation.get('screenshot_sha256')!=row.get('screenshot_sha256') or not confirmation.get('confirmed_at'):return None
    score=calculate(values,notes_verified=True,identity_confirmed=True)
    if score['errors'] or judged(values)!=values.get('total_notes') or values.get('completion_status')!='COMPLETED':return None
    if values.get('difficulty') not in ('EASY','NORMAL','HARD') or not positive(values.get('game_level')):return None
    return dict(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_path'],
        pack_id=song['pack_id'],source_type='custom',
        values=dict(title=title['value'],**{k:values[k] for k in ('difficulty','game_level','total_notes','duration_seconds')}),
        confirmation_source='explicit_user_review',confirmed_at=confirmation['confirmed_at'],
        review_id=row['review_id'],screenshot_sha256=row['screenshot_sha256'])


def note_evidence(session, values, difficulty=None, *, identity_confirmed=False,
                  manual_notes_verified=False, ocr_values=None):
    selection = session.get('selection') or {}
    # New sessions preserve original OCR separately from catalog enrichment.
    original = selection.get('ocr_fields', selection.get('fields') or {})
    item = original.get('total_notes') or {}
    selected = positive(item.get('value')) if item.get('method') != 'official_catalog_fill' else None
    gd = difficulty or {}
    database = positive(gd.get('total_notes'))
    database_verified = bool(gd.get('notes_verified') and database is not None)
    supplied = positive(values.get('total_notes'))
    verified = supplied if identity_confirmed and supplied is not None and (
        (database_verified and supplied == database) or
        (manual_notes_verified is True and not database_verified)) else None
    total = judged(values)
    # A catalog comparison can warn before the human identity checkbox is
    # checked. It remains a candidate basis, never a verified formal MAX.
    comparison_total = verified if verified is not None else (
        database if database_verified else None)
    source_conflict = bool(selected is not None and database_verified and selected != database)
    discrepancy = bool(values.get('completion_status') == 'COMPLETED' and
                       total is not None and comparison_total is not None and total != comparison_total)
    if source_conflict:status = 'source_conflict_pending'
    elif discrepancy:status = 'count_discrepancy_pending'
    elif values.get('completion_status') == 'FAILED':status = 'failed_partial_judgments'
    elif verified is None or total is None:status = 'incomplete_evidence'
    else:status = 'consistent'
    conditional = None
    previews=[]
    score = calculate(values)
    catalog_total=database if database is not None else (supplied if manual_notes_verified and identity_confirmed else None)
    for source,count in [('catalog',catalog_total),('judged',total if values.get('completion_status')=='COMPLETED' else None)]:
        if count is None or count<=0 or score['ex_score'] is None:continue
        maximum=count*2;difference=maximum-score['ex_score']
        entry=dict(source=source,total_notes=count,ex_score=score['ex_score'],max_score=maximum,difference=difference,
            display=(f'MAX -{difference}' if difference<=150 else percentage_display(score['ex_score'],maximum)) if difference>=0 else None,
            confirmed=False,condition='independent_game_max_definition_not_verified',writes_play_record=False)
        previews.append(entry)
        if source=='catalog' and difference>=0 and not source_conflict:conditional=entry
    return dict(verification_scope='chart_reference_only_not_game_max',catalog_reference_verified=bool(database_verified or (identity_confirmed and manual_notes_verified)),catalog_total_notes=catalog_total,max_basis_notes=None,max_basis_verified=False,conditional_previews=previews,note_count_difference=(total-comparison_total) if total is not None and comparison_total is not None else None,selected_total_notes=selected, database_total_notes=database,
        database_notes_verified=database_verified, judged_total=total,
        ocr_judged_total=judged(ocr_values or {}), verified_total_notes=verified,
        selected_source=dict(available=selected is not None, confidence=item.get('confidence'),
                             method=item.get('method'), reason=item.get('reason'),observed_at=selection.get('observed_at')),
        database_source=dict(difficulty_id=gd.get('difficulty_id'), notes_source=gd.get('notes_source')),
        verification_source=('catalog' if database_verified else 'explicit_manual_review') if verified is not None else None,
        selected_database_conflict=source_conflict, judgment_difference=(total-comparison_total) if total is not None and comparison_total is not None else None,
        status=status, counting_rule_verified=False if discrepancy else None, conditional_score=conditional)
