"""Session 6 replay with explicitly labelled counterfactual lost-selection probes.

Two original sessions have no surviving selected-row payload or selected-row
pixels. Their handoff scenarios are NOT recovered historical OCR. They test the
requested behavior using the recorded PLAYING title/LV, observed HARD tab and
pack, at the actual final SONG_SELECT time. Result samples are real saved pixels
and confidences; no scores, frames, or temporal agreement are fabricated.
"""
import argparse
import json
from pathlib import Path

from dance_results.monitor_tracking import atomic_json
from tools.replay_realplay4 import PROJECT, read, replay, seconds


EXPECTED = (
    ('14f81c7b', 477, [968, 22, 3, 3], 528, 60366, 13, 'A+'),
    ('5e0e7668', 511, [1192, 12, 0, 1], 1204, 85022, 25, 'A+'),
    ('850772b1', 942, [761, 4, 0, 0], 765, 54059, 20, 'A++'),
    ('1b08a518', 933, [772, 52, 9, 13], 164, 44279, 20, 'A+'),
    ('95c62056', 965, [917, 27, 3, 6], 285, 53045, 27, 'A+'),
    ('1a762873', 1776, [1108, 10, 0, 1], 801, 72499, 22, 'A+'),
    ('7f290dcd', 1414, [1231, 55, 7, 5], 546, 75289, 30, 'A+'),
    ('9fab0ff5', 1433, [870, 69, 13, 16], 248, 51603, 25, 'A+'),
)


def scenarios(snapshot):
    events = [json.loads(line) for line in (snapshot/'diagnostics.jsonl').read_text(encoding='utf8').splitlines()]
    result = {}
    for path in (snapshot/'sessions').glob('*/session.json'):
        session = read(path)
        if session.get('selection'):
            continue
        start = seconds(session['started_at'])
        selected = [e for e in events if e['event'] == 'recognition' and e['state'] == 'SONG_SELECT'
                    and start-60 <= seconds(e['at']) < start]
        last = selected[-1]
        packs = [e for e in events if e['event'] == 'pack_observed' and e.get('pack')
                 and start-1200 <= seconds(e['at']) <= seconds(last['at'])]
        preview = session['playing_preview']
        result[session['session_id']] = dict(
            fields={k: dict(value=v, confidence=.99, reason=None,
                            method='counterfactual_handoff_fixture_not_historical_ocr')
                    for k, v in dict(title=preview['title'], game_level=preview['game_level'], difficulty='HARD').items()},
            pack=packs[-1]['pack'], observed_at=last['at'], frame_hash=last['frame_hash'],
            source_type='custom', matching={})
    return result


def acceptance(report):
    plays = {p['original_session_id'][:8]: p for p in report['plays']}
    checks = {}
    for prefix, song, judgments, combo, score, level, grade in EXPECTED:
        p = plays.get(prefix, {})
        r = p.get('simulated_submission') or {}
        checks[prefix+'_saved_correctly'] = (
            r.get('song_id') == song and p.get('review_status') == 'saved_verified_auto'
            and [r.get(k) for k in ('perfect', 'cool', 'bad', 'miss')] == judgments
            and r.get('max_combo') == combo and r.get('game_score') == score
            and r.get('grade') == grade and r.get('game_level') == level)
    checks['marshmallow_uses_846'] = (plays.get('1b08a518', {}).get('simulated_submission') or {}).get('total_notes') == 846
    checks['no_wrong_auto'] = len(plays) == 8 and all(checks.values())
    checks['snapshot_unchanged'] = report['snapshot_unchanged']
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=PROJECT/'data/realplay6_snapshot')
    parser.add_argument('--failures', type=Path, default=PROJECT/'data/ocr_failures_snapshot')
    parser.add_argument('--database', type=Path, default=PROJECT/'data/library.sqlite3')
    parser.add_argument('--output-prefix', default='round3-replay6')
    args = parser.parse_args()
    probes = scenarios(args.snapshot)
    report = replay(args.snapshot, args.failures, args.database,
                    handoff_scenarios=probes, replay_packs=True)
    report['lost_selection_limit'] = __doc__
    report['counterfactual_sessions'] = sorted(probes)
    report['acceptance'] = acceptance(report)
    report['passed'] = all(report['acceptance'].values())
    atomic_json(PROJECT/'reports'/f'{args.output_prefix}.json', report)
    output = json.dumps(report, ensure_ascii=False, indent=2)+'\n'
    (PROJECT/'reports'/f'{args.output_prefix}.txt').write_text(output, encoding='utf8')
    print(output)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
