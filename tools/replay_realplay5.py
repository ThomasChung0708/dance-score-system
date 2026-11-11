"""Replay six session-5 folders and saved failures using disposable DB copies."""
import argparse
import json
from pathlib import Path

from dance_results.monitor_tracking import atomic_json
from tools.replay_realplay4 import PROJECT, replay


def acceptance(report):
    plays = {p['original_session_id'][:8]: p for p in report['plays']}
    checks = {}
    expected = (
        ('cbb572bd', 630, [1804, 48, 2, 10], 107929, 'A+'),
        ('d1c7b1ef', 601, [529, 0, 0, 0], 37410, 'A++'),
        ('82fa768f', 649, [835, 8, 0, 0], 59533, 'A++'),
        ('a1f18ded', 633, [1270, 31, 4, 5], 74704, 'A+'),
    )
    for prefix, song, judgments, score, grade in expected:
        play = plays.get(prefix, {})
        record = play.get('simulated_submission') or {}
        checks[f'{prefix}_saved_correctly'] = (
            play.get('review_status') == 'saved_verified_auto' and record.get('song_id') == song
            and [record.get(k) for k in ('perfect', 'cool', 'bad', 'miss')] == judgments
            and record.get('game_score') == score and record.get('grade') == grade)
    bon = plays.get('213d6eed', {})
    checks['bon_bound_644_pending_quick_leave'] = (
        bon.get('bound_song_id') == 644 and bon.get('review_status') == 'pending_review'
        and bon.get('recovery_reason') == 'result_left_before_confirmation'
        and bon.get('simulated_submission') is None)
    checks['abort_no_review'] = plays.get('bb1c6763', {}).get('review_status') is None
    checks['all_six_replayed'] = len(plays) == 6
    checks['no_wrong_auto'] = sum(bool(p['simulated_submission']) for p in report['plays']) == 4 and all(
        checks[f'{prefix}_saved_correctly'] for prefix, *_ in expected)
    checks['saved_reviews_hidden'] = all(not p['listed_pending'] for p in report['plays'] if p['simulated_submission'])
    checks['snapshot_unchanged'] = report['snapshot_unchanged']
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=PROJECT/'data/realplay5_snapshot')
    parser.add_argument('--failures', type=Path, default=PROJECT/'data/ocr_failures_snapshot')
    parser.add_argument('--database', type=Path, default=PROJECT/'data/library.sqlite3')
    parser.add_argument('--output-prefix', default='round29-replay5')
    args = parser.parse_args()
    report = replay(args.snapshot, args.failures, args.database)
    report['acceptance'] = acceptance(report)
    report['passed'] = all(report['acceptance'].values())
    atomic_json(PROJECT/'reports'/f'{args.output_prefix}.json', report)
    output = json.dumps(report, ensure_ascii=False, indent=2)+'\n'
    (PROJECT/'reports'/f'{args.output_prefix}.txt').write_text(output, encoding='utf8')
    print(output)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
