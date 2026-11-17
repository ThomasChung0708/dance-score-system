"""Result-title authority inside scope frozen at the start of this play."""
import copy

from .ocr_matching import result_identity_fields, same_song_title_key
from .ocr_temporal import reliable_result_field


def context_selection(session):
    """No legacy/stale pack inference: scope must be captured for this epoch."""
    context = session.get('identity_context') or {}
    if (context.get('epoch') != session.get('epoch')
            or context.get('captured_at') != session.get('started_at')):
        return {}
    pack = context.get('pack') or {}
    # Only the tracker can freeze an active context. Legacy age-only evidence
    # cannot establish that an old Pack still belonged to this list/room.
    if pack.get('status') == 'confirmed' and context.get('pack_context_current') is True:
        return dict(pack=copy.deepcopy(pack), source_type='custom')
    if context.get('guest_room') and not session.get('selection'):
        room = context['guest_room']
        return dict(source_type='custom' if room.get('custom') else 'unknown', guest_room=copy.deepcopy(room))
    # Source-only category evidence retains its existing bounded lifetime.
    limit = min(60, context.get('max_age_seconds', 0))
    age = context.get('source_age_seconds')
    source = context.get('source_type')
    if source in ('custom', 'official') and isinstance(age, (int, float)) and 0 <= age <= limit:
        return dict(source_type=source)
    return {}


def bind_result_title(catalog, session, ocr, selection):
    """Only fill an absent selection title, preserving two-frame/title floors.

    Matching still enforces chart LV and namesake ambiguity. No preview title
    or result pack label can supply catalog authority.
    """
    from .room_identity import bind_guest_official, guest_context
    if guest_context(session) and selection.get('scope_authority') != 'session_source_choice':
        return bind_guest_official(catalog, session, ocr)
    original_fields = selection.get('ocr_fields', selection.get('fields') or {})
    if original_fields.get('title', {}).get('value'):
        return None
    temporal = ocr.get('temporal_validation') or {}
    if (temporal.get('status') != 'confirmed' or len(temporal.get('samples', [])) < 2
            or not reliable_result_field(ocr, 'title')):
        return None
    frozen = context_selection(session)
    # Explicit per-session source choice remains authoritative (Round 3.3).
    explicit = selection.get('scope_authority') == 'session_source_choice'
    if not frozen and not explicit:
        return None
    scope = copy.deepcopy(selection)
    if not explicit:
        scope.update(frozen)
    source = scope.get('source_type')
    if source not in ('custom', 'official'):
        return None
    fields = result_identity_fields(scope, ocr, session.get('playing_preview'))
    # Use a strong ORIGINAL agreeing title sample when the final frame is the
    # >=.90 anchored partner. Preserve every measured source confidence.
    titles = [sample.get('fields', {}).get('title', {}) for sample in temporal['samples']]
    current_title = fields.get('title') or {}
    agreeing = [title for title in titles if same_song_title_key(title.get('value'))
        == same_song_title_key(current_title.get('value')) and not title.get('reason')]
    if agreeing:
        strongest = max(agreeing, key=lambda title: title.get('confidence', 0))
        if strongest.get('confidence', 0) > current_title.get('confidence', 0):
            fields['title'] = copy.deepcopy(strongest)
    pack = scope.get('pack') or {}
    pack_id = pack.get('pack_id') if pack.get('status') == 'confirmed' else None
    # A result contains LV but no difficulty label. A single title and a
    # single chart at that reliable LV can supply its catalog difficulty.
    # Never use this to eliminate another song or an unknown chart.
    ids = catalog.title_ids(fields.get('title', {}).get('value'), pack_id, source)
    if fields.get('difficulty', {}).get('value') is None and len(ids) == 1:
        charts = [gd for (sid, _), gd in catalog.difficulties.items() if sid in ids]
        level = fields.get('game_level') or {}
        matching = [gd for gd in charts if gd.get('game_level') == level.get('value')]
        if (len(matching) == 1 and all(gd.get('game_level') is not None for gd in charts)
                and level.get('confidence', 0) >= .95 and not level.get('reason')):
            fields['difficulty'] = dict(value=matching[0]['difficulty'], confidence=1,
                method='unique_catalog_chart_at_confirmed_result_level', reason=None)
    match = catalog.match_identity(fields, pack_id, source)
    if match.get('confirmed_song_id') is None or match.get('review_only'):
        return None
    method = 'result_title_in_confirmed_pack' if pack_id is not None else 'result_title_in_confirmed_source'
    scope.update(fields=fields, ocr_fields=copy.deepcopy(fields), matching=match,
                 result_title_binding=dict(method=method, song_id=match['confirmed_song_id']))
    return scope
