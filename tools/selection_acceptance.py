"""Strict metadata readiness for the live acceptance tool, not matching policy."""
import math


def selection_readiness(selection):
    selection=selection or {};fields=selection.get('fields')or{};matching=selection.get('matching')or{}
    confirmed=matching.get('confirmed_song_id') is not None and matching.get('status') in (
        'confirmed_official_catalog','confirmed_manual','confirmed_manual_reference')
    reasons=[]
    for key in ('title','difficulty','game_level','total_notes','bpm'):
        field=fields.get(key)or{};v=field.get('value')
        valid=(isinstance(v,str) and bool(v.strip())) if key=='title' else v in ('EASY','NORMAL','HARD') if key=='difficulty' else (
            type(v) in ((int,float) if key=='bpm' else (int,)) and math.isfinite(v) and v>0)
        if not valid:reasons.append(key+'_missing_or_invalid')
        elif field.get('confidence',0)<.95 or field.get('reason'):reasons.append(key+'_unreliable')
    return dict(matching_identity_confirmed=confirmed,observed_metadata_complete=not reasons,
                complete_selection_observation=confirmed and not reasons,
                reasons=reasons+([] if confirmed else ['song_identity_unconfirmed']),
                scope='Metadata readiness only; expected target, pre-PLAYING lock, result frames and submission must be verified separately.')
