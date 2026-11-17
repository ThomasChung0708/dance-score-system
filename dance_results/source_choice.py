"""Session-scoped human source/copy choice; all result safety stays downstream."""
import copy

from .chart_changes import official_fields
from .ocr_matching import normalize
from .result_title_binding import context_selection


def effective_selection(row):
    selected = copy.deepcopy(row['locked_session'].get('selection') or {})
    if not selected.get('fields', {}).get('title', {}).get('value'):
        for key, value in context_selection(row['locked_session']).items():
            if not selected.get(key) or selected[key] == 'unknown':
                selected[key] = value
    choice = row.get('source_choice') or {}
    if choice:
        selected['scope_authority'] = 'session_source_choice'
        selected['source_type'] = choice['source_type']
        selected['pack'] = (dict(pack_id=choice['pack_id'], status='confirmed',
            source='session_source_choice') if choice.get('pack_id') is not None else None)
    return selected


def offer(catalog, row, download_binding=None, *, directory=None):
    if row['status'] != 'pending_review' or row['revision'] != 0 or row.get('source_choice'):
        return None
    if download_binding:
        return None
    selection = effective_selection(row)
    from .room_identity import guest_context, bind_guest_official
    guest = guest_context(row['locked_session'])
    from .download_tracking import session_download
    download = session_download(directory, row['session_id']) if directory is not None else None
    if (guest and directory is not None and download is None
            and bind_guest_official(catalog, row['locked_session'], row['ocr'])):
        return None
    # A confirmed source/pack is already authoritative, including manual GROUP.
    source = selection.get('source_type')
    if not guest and (source == 'official' or (selection.get('pack') or {}).get('status') == 'confirmed'):
        return None
    fields = official_fields(selection, row['ocr'])
    title = normalize(guest['title'] if guest else fields.get('title', {}).get('value'))
    ids = sorted(catalog.title_ids(title))
    if source == 'custom' and not guest:
        ids = [i for i in ids if catalog.songs[i]['source_type'] == 'custom']
    if not ids:
        return None
    # Rule-resolved official plus chartless custom copies need no interruption.
    resolved = catalog.match_identity(fields, source_type=source)
    if resolved.get('confirmed_song_id') is not None and not guest:
        return None
    sources = {catalog.songs[i]['source_type'] for i in ids}
    custom_ids = [i for i in ids if catalog.songs[i]['source_type'] == 'custom']
    both = sources == {'official', 'custom'}
    if not guest and not both and not (sources == {'custom'} and len(custom_ids) > 1):
        return None
    candidates = []
    for sid in ids:
        song = catalog.songs[sid]
        charts = [dict(d) for (i, _), d in catalog.difficulties.items() if i == sid]
        exact = any(d['difficulty'] == fields.get('difficulty', {}).get('value')
            and d.get('game_level') == fields.get('game_level', {}).get('value') for d in charts)
        candidates.append(dict(song_id=sid, source_type=song['source_type'],
            pack_name=song.get('pack_name'), pack_id=song.get('pack_id'),
            folder_name=song.get('folder_name'), title=song.get('saved_title') or song.get('latest_title'),
            difficulties=charts, exact_match=exact))
    # A sole exact custom chart can resolve copies with different known LVs.
    # Unknown charts remain candidates unless the normal matcher resolves them.
    return dict(session_id=row['session_id'], review_id=row['review_id'], revision=row['revision'],
        kind='folder' if sources == {'custom'} and len(custom_ids) > 1 else 'source',
        question='這首是哪個資料夾的譜面？' if sources == {'custom'} and len(custom_ids) > 1 else '這首是官方譜還是自製譜？',
        dismissed=bool(row.get('source_prompt_dismissed')),
        candidates=sorted(candidates, key=lambda c: (not c['exact_match'], c['source_type'], c['song_id'])))
