"""Shared IIDX-style integer scoring. No OCR, database or player-level semantics."""
from decimal import Decimal, ROUND_HALF_UP


def percentage_display(ex_score, max_score):
    if max_score is None or max_score <= 0 or ex_score is None:
        return None
    return str((Decimal(ex_score)*100/Decimal(max_score)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))+'%'


def calculate(values, *, notes_verified=False, identity_confirmed=False, note_exception=False, max_basis=None, comparison_conflict=False):
    """Nullable preview with the same validation used for formal submissions.

    Scores/differences are integers. Stored percentage retains full floating
    precision; only its presentation rounds (decimal half up) to two places.
    """
    errors=[]
    def issue(code,field):errors.append(dict(code=code,field=field))
    for key in ('perfect','cool','bad','miss','max_combo','game_score','game_level','total_notes','duration_seconds'):
        number=values.get(key)
        minimum=1 if key in ('game_level','total_notes','duration_seconds') else 0
        maximum=36000 if key=='duration_seconds' else (10**10 if key=='game_score' else 10**7)
        if number is not None and (type(number) is not int or not minimum<=number<=maximum):
            issue('invalid_positive_integer' if minimum else 'invalid_nonnegative_integer',key)
    completion=values.get('completion_status')
    if completion not in (None,'COMPLETED','FAILED'):issue('invalid_completion_status','completion_status')
    judgments=[values.get(k) for k in ('perfect','cool','bad','miss')]
    complete=all(type(n) is int and 0<=n<=10**7 for n in judgments)
    total=sum(judgments) if complete else None
    p=values.get('perfect');c=values.get('cool')
    ex=p*2+c if all(type(n) is int and 0<=n<=10**7 for n in (p,c)) else None
    if complete and type(values.get('max_combo')) is int and values['max_combo']>p+c:
        issue('combo_exceeds_successful_judgments','max_combo')
    # Catalog/reference verification never proves the game's MAX definition.
    if max_basis is None and completion=='COMPLETED':
        from .score_comparison import completed_basis
        max_basis=completed_basis(values,identity_confirmed)
    basis=max_basis or {};notes=basis.get('notes')
    custom=basis.get('source')=='completed_result_judged_total'
    verified=bool(basis.get('verified') is True and type(notes) is int and 0<notes<=10**7 and identity_confirmed is True and not (completion=='FAILED' and basis.get('source') in ('judged','completed_result_judged_total')) and (not custom or total==notes))
    usable=verified and complete and completion in ('COMPLETED','FAILED') and not errors
    maximum=notes*2 if usable else None
    difference=maximum-ex if maximum is not None and ex is not None else None
    if difference is not None and difference<0:
        issue('ex_score_exceeds_max','perfect');maximum=difference=None
    percentage=ex*100/maximum if maximum is not None else None
    display=(f'MAX -{difference}' if difference<=150 else percentage_display(ex,maximum)) if difference is not None else None
    lamp=None
    if completion=='FAILED':lamp='FAILED'
    elif complete and identity_confirmed and completion=='COMPLETED' and not errors and not comparison_conflict:
        if values['bad']>0 or values['miss']>0:lamp='CLEAR'
        elif usable and total==notes and (custom or not note_exception):
            if p>0 and c==0:lamp='ACP'
            elif p+c>0:lamp='AC'
    return dict(ex_score=ex,judged_notes=total,max_score=maximum,difference=difference,
                percentage=percentage,display=display,lamp_preview=lamp,errors=errors,
                notes_verified=bool(notes_verified),max_basis_notes=notes if verified else None,max_basis_verified=verified,max_policy=basis.get('policy'),max_label='本系統自訂 MAX' if custom else '另行驗證 MAX' if maximum is not None else 'MAX 未確認',lamp_source='system_result_judgments' if custom and lamp else None,score_status='judgments_available' if complete else 'incomplete_judgments',preview_only=True,writes_play_record=False)
