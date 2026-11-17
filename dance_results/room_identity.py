"""Guest identity from frozen room evidence and independently confirmed results."""
import copy

from .ocr_matching import probably_same_title, same_song_title_key
from .ocr_temporal import reliable_result_field


def guest_context(session):
    context = session.get('identity_context') or {}
    if (session.get('selection') or context.get('epoch') != session.get('epoch')
            or context.get('captured_at') != session.get('started_at')):
        return None
    if ((context.get('pack') or {}).get('status') == 'confirmed'
            and context.get('pack_context_current') is True):
        return None
    return context.get('guest_room')


def agreeing_room(session, ocr):
    room = guest_context(session)
    temporal = ocr.get('temporal_validation') or {}
    if (not room or not room.get('title') or temporal.get('status') != 'confirmed'
            or len(temporal.get('samples', [])) < 2 or not reliable_result_field(ocr, 'title')):
        return None
    result = ocr.get('fields', {}).get('title', {}).get('value')
    playing = (session.get('playing_preview') or {}).get('title')
    # Tolerance is an agreement check only. Catalog retrieval below always uses
    # the exact room retrieval key, and never an edit-distance alias.
    if not (probably_same_title(room['title'], result)['same']
            or playing and probably_same_title(room['title'], playing)['same']):
        return None
    # A real contradictory banner cannot be masked by a matching result.
    if playing and not probably_same_title(room['title'], playing)['same']:
        return None
    return room


def field(value, method, confidence=1):
    return dict(value=value, confidence=confidence, method=method, reason=None)


def bind_guest_official(catalog, session, ocr):
    room = agreeing_room(session, ocr)
    if not room or room.get('custom'):
        return None
    raw = ocr.get('fields') or {}
    if not reliable_result_field(ocr, 'completion_status') or raw['completion_status']['value'] != 'COMPLETED':
        return None
    judgments = [raw.get(k) or {} for k in ('perfect', 'cool', 'bad', 'miss')]
    if not all(reliable_result_field(ocr, k) and type(raw[k]['value']) is int
               and raw[k]['value'] >= 0 for k in ('perfect', 'cool', 'bad', 'miss')):
        return None
    difficulty = (room.get('fields') or {}).get('difficulty') or {}
    if difficulty.get('confidence', 0) < .95 or difficulty.get('reason'):
        return None
    notes = sum(f['value'] for f in judgments)
    ids = catalog.title_ids(room['title'])
    if any(catalog.songs[sid]['source_type'] == 'custom' for sid in ids):
        return None
    charts = [(sid, catalog.difficulties.get((sid, difficulty.get('value')))) for sid in ids
              if catalog.songs[sid]['source_type'] == 'official']
    matching = [(sid, gd) for sid, gd in charts if gd and gd.get('total_notes') == notes
                and gd.get('notes_verified') and gd.get('game_level') is not None
                and gd.get('level_source') and (catalog.official_sources.get(sid) or {}).get('mode') == 'K']
    if len(matching) != 1:
        return None
    sid, gd = matching[0]
    title = max((s.get('fields', {}).get('title') or {} for s in ocr['temporal_validation']['samples']),
                key=lambda f: f.get('confidence', 0))
    if same_song_title_key(title.get('value')) != same_song_title_key(room['title']):
        return None
    fields = dict(title=copy.deepcopy(title), difficulty=copy.deepcopy(difficulty),
        game_level=field(gd['game_level'], 'catalog_chart_after_guest_notes_match'),
        total_notes=field(notes, 'completed_result_judgment_sum', min(f['confidence'] for f in judgments)))
    evidence = dict(method='guest_room_title_official_unique', song_id=sid,
        room=copy.deepcopy(room), judged_notes=notes, title_song_ids=sorted(ids),
        playing_title=(session.get('playing_preview') or {}).get('title'))
    return dict(source_type='official', fields=fields, ocr_fields=copy.deepcopy(fields),
        result_title_binding=evidence)
