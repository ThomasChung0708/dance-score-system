"""Screen-only result checks; catalog enrichment is never pixel evidence."""

from .ocr_policy import AUTO_CONFIDENCE

JUDGMENTS = ('perfect', 'cool', 'bad', 'miss')
SCREEN_SOURCES = frozenset({
    'screen_observed', 'game_screen', 'manual_game_screen',
    'confirmed_result_screen', 'confirmed_screen', 'result_screen',
    'completed_judgment_sum',
})


def screen_catalog(chart):
    """Verification of imported/manual catalog data does not prove screen origin."""
    return bool(chart and chart.get('notes_source') in SCREEN_SOURCES
                and chart.get('notes_verified')
                and chart.get('total_notes') is not None)


def screen_totals(raw, selection):
    original = selection.get('ocr_fields', selection.get('fields') or {})
    return [(source, item) for source, item in (
        ('result', raw.get('total_notes') or {}),
        ('selection', original.get('total_notes') or {}),
    ) if item.get('value') is not None
        and 'catalog' not in str(item.get('method', '')).lower()]


def validate(values, totals=()):
    rules = []

    def rule(name, passed, explanation):
        rules.append(dict(rule=name, status='pass' if passed else 'fail',
                          mandatory=True, reason=explanation))

    counts = [values.get(key) for key in JUDGMENTS]
    valid = all(type(n) is int and 0 <= n <= 10**7 for n in counts)
    total = sum(counts) if valid else None
    rule('judgment_integers', valid, 'Judgments must be nonnegative integers.')
    state = values.get('completion_status')
    rule('completion_status', state in ('COMPLETED', 'FAILED'),
         'A local result must be completed or explicitly failed.')
    rule('nonempty_completed_result', state != 'COMPLETED' or valid and total > 0,
         'A completed result must contain judged notes.')
    for source, item in totals:
        n = item.get('value')
        readable = (type(n) is int and n > 0
                    and item.get('confidence', 0) >= AUTO_CONFIDENCE
                    and not item.get('reason')
                    and 'catalog' not in str(item.get('method', '')).lower())
        rule('screen_total_' + source, readable,
             'Displayed totals need reliable original screen evidence.')
        rule('judgment_sum_' + source, readable and valid and (
            total == n if state == 'COMPLETED' else total <= n),
            'Completed judgments equal the displayed total; failed partial judgments cannot exceed it.')
    combo = values.get('max_combo')
    rule('combo_bound', type(combo) is int and valid and 0 <= combo <= total,
         'Maximum combo cannot exceed judged notes.')
    rule('combo_success_bound', type(combo) is int and valid
         and combo <= values['perfect'] + values['cool'],
         'Maximum combo cannot exceed Perfect plus Cool.')
    special = values.get('result_status')
    if special in ('FULL COMBO', 'ALL PERFECT'):
        rule('special_completion', state == 'COMPLETED' and valid
             and values['bad'] == values['miss'] == 0 and combo == total
             and (special != 'ALL PERFECT' or values['cool'] == 0),
             'FULL COMBO / ALL PERFECT must agree with completion, judgments and combo.')
    score = values.get('game_score')
    rule('score_integer', type(score) is int and 0 <= score <= 10**10,
         'Game score must be a nonnegative integer.')
    for name, reason in (
        ('grade_formula', 'Labelled samples do not establish exact grade thresholds.'),
        ('game_score_formula', 'Judgment totals omit combo progression; an exact score formula is unproven.'),
    ):
        rules.append(dict(rule=name, status='skipped', mandatory=False, reason=reason))
    return dict(judged_total=total, rules=rules,
                issues=['automatic_consistency_' + r['rule'] for r in rules
                        if r['mandatory'] and r['status'] == 'fail'])


def observation(values):
    """Stored within the append-only play evidence, without a schema migration."""
    result = validate(values)
    if values.get('completion_status') != 'COMPLETED' or result['issues']:
        return None
    return dict(total_notes=result['judged_total'], provenance='screen_observed',
                derivation='completed_result_judgment_sum',
                catalog_overwritten=False)
