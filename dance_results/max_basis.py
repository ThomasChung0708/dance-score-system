"""Independent, per-result MAX proof. Catalog counts are never implicit proof."""
from .monitor_tracking import fingerprint
from .note_evidence import positive


def resolve(row,draft,notes,*,identity_confirmed=False):
    context=fingerprint(dict(session=row['session_id'],review=row['review_id'],image=row['screenshot_sha256'],
        song=draft.get('song_id'),identity=(draft.get('song_identity') or {}).get('relative_path'),
        values=draft['values'],player=draft.get('player_row_id'),
        catalog=notes.get('catalog_total_notes'),selected=notes.get('selected_total_notes')))
    proof=draft.get('max_basis') or {};source=proof.get('source')
    claimed=positive(proof.get('notes'))
    observed={'catalog':notes.get('catalog_total_notes'),'judged':notes.get('judged_total')}
    agrees=source=='independent' or (source in observed and claimed==observed[source])
    forbidden_partial=draft['values'].get('completion_status')=='FAILED' and source=='judged'
    verified=bool(identity_confirmed and proof.get('verified') is True and claimed is not None and
        proof.get('context')==context and source in ('catalog','judged','independent') and agrees and
        not forbidden_partial and isinstance(proof.get('evidence'),str) and proof['evidence'].strip())
    return dict(context=context,verified=verified,notes=claimed if verified else None,
        source=source if verified else None,evidence=proof.get('evidence',''),
        scope='this_session_and_review_only',verification_method='explicit_independent_max_review' if verified else None,
        catalog_agreement_is_not_proof=True,partial_judgments_forbidden=forbidden_partial)
