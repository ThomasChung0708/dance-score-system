"""Mutable official metadata is advisory; result pixels retain authority."""
import copy
from .ocr_matching import result_identity_fields, same_song_title_key
from .result_consistency import validate


def official_fields(selection, ocr, playing=None):
    fields = result_identity_fields(selection, ocr, playing)
    raw = ocr.get('fields') or {}
    # Only official identity can use two strict result titles to corroborate
    # an agreeing selection title below the automatic confidence floor.
    samples = (ocr.get('temporal_validation') or {}).get('samples') or []
    title = raw.get('title') or {}
    if (len(samples) == 2 and title.get('confidence', 0) >= .95 and not title.get('reason')
            and same_song_title_key(title.get('value')) == same_song_title_key(fields.get('title', {}).get('value'))
            and all((s.get('fields', {}).get('title') or {}).get('confidence', 0) >= .95
                and not (s.get('fields', {}).get('title') or {}).get('reason')
                and same_song_title_key(s.get('fields', {}).get('title', {}).get('value')) == same_song_title_key(title.get('value'))
                for s in samples)):
        fields['title'] = copy.deepcopy(title)
    judgments = [raw.get(k) or {} for k in ('perfect', 'cool', 'bad', 'miss')]
    if (raw.get('completion_status', {}).get('value') == 'COMPLETED'
            and all(type(f.get('value')) is int and f['value'] >= 0
                and f.get('confidence', 0) >= .95 and not f.get('reason') for f in judgments)):
        fields['total_notes'] = dict(value=sum(f['value'] for f in judgments),
            confidence=min(f['confidence'] for f in judgments), reason=None,
            method='result_screen_judged_sum')
    return fields


def official_context(catalog, row, song_id):
    """Recompute identity rather than trusting client warning/exception flags."""
    song = catalog.songs.get(song_id) or {}
    from .source_choice import effective_selection
    selection = effective_selection(row)
    from .room_identity import bind_guest_official
    guest = bind_guest_official(catalog, row['locked_session'], row['ocr'])
    if guest and not row.get('source_choice'):
        selection = guest
    if song.get('source_type') != 'official' or selection.get('source_type') == 'custom':
        return False
    fields = official_fields(selection, row['ocr'])
    match = (catalog.match_official(fields, source_confirmed=True)
        if selection.get('source_type') == 'official' or row.get('source_choice')
        else catalog.match_identity(fields))
    return match.get('confirmed_song_id') == song_id


def warnings(row, chart):
    raw = row['ocr'].get('fields') or {}
    observed = validate({k: f.get('value') for k, f in raw.items()})['judged_total']
    if raw.get('completion_status', {}).get('value') != 'COMPLETED' or observed is None:
        return []
    selection = row['locked_session'].get('selection') or {}
    selected = selection.get('ocr_fields', selection.get('fields') or {}).get('total_notes') or {}
    catalog_notes = (chart or {}).get('total_notes')
    selection_notes = selected.get('value')
    changed = (catalog_notes is not None and catalog_notes != observed) or (
        selection_notes is not None and selected.get('confidence', 0) >= .95
        and not selected.get('reason') and selection_notes != observed)
    if not changed:
        return []
    return [dict(code='CHART_NOTES_CHANGED', field='total_notes', severity='warning',
        catalog_notes=catalog_notes, selection_notes=selection_notes, observed_notes=observed,
        catalog_difference=observed-catalog_notes if catalog_notes is not None else None,
        selection_difference=observed-selection_notes if type(selection_notes) is int else None)]


def bpm_evidence(row, catalog, song_id):
    """Observed integer display is compatible with floor, not a proven identity.

    Session d060f0c9 is the sole retained .5 BPM selection sample. Preserve the
    raw decimal and an explicit display hypothesis instead of a tolerance.
    """
    from decimal import Decimal, InvalidOperation, ROUND_FLOOR
    selection = row['locked_session'].get('selection') or {}
    field = selection.get('ocr_fields', selection.get('fields') or {}).get('bpm') or {}
    source = catalog.official_sources.get(song_id) or {}
    if field.get('value') is None or not source.get('bpm_raw'):
        return None
    try:
        observed, imported = Decimal(str(field['value'])), Decimal(source['bpm_raw'])
        floor = imported.to_integral_value(rounding=ROUND_FLOOR)
        return dict(observed_bpm=field['value'], catalog_bpm=source['bpm_raw'],
            exact=observed == imported, integer_floor_compatible=observed == floor,
            display_convention='floor_compatible_single_observed_sample_not_proven',
            identity_requirement=False)
    except (InvalidOperation, ValueError):
        return dict(observed_bpm=field['value'], catalog_bpm=source['bpm_raw'],
            identity_requirement=False, comparison='unreadable')
