"""Replay local diagnostic JSONs without images, OCR, or database writes."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from dance_results.ocr_failures import failure_details


def reconstruct(snapshot):
    """Snapshots omit source/mode and tokens; preserve known parser/gate reasons.

    Do not guess custom source from missing metadata. A host-footer warning is
    explicit multiplayer evidence. Generic missing/low-confidence reasons are
    recomputed from values and model confidence, rather than replayed verbatim.
    """
    fields = {
        key: dict(value=value, confidence=snapshot.get('confidence', {}).get(key, 0))
        for key, value in snapshot.get('fields', {}).items()
    }
    gate_reasons = []
    error = None
    for reason in snapshot.get('reason', []):
        key, separator, detail = reason.partition(': ')
        if separator and key in fields:
            if detail not in ('missing', 'low_confidence'):
                fields[key]['reason'] = detail
        elif key == 'recognition_exception':
            error = type(detail, (Exception,), {})()
        elif reason != 'screen_unrecognized' and not reason.startswith('selected_pack_'):
            gate_reasons.append(reason)
    screen = snapshot.get('screen_type_guess')
    observation = dict(screen_type=screen)
    if screen == 'results':
        observation['result_ocr'] = dict(
            fields=fields, errors=gate_reasons,
            multiplayer='footer_level_is_host_not_local' in gate_reasons,
        )
    else:
        observation['ocr_fields'] = fields
    if 'pack_reason' in snapshot:
        observation['selection'] = {
            key: snapshot.get(key) for key in (
                'selected_pack', 'pack_reason', 'selected_pack_confidence')
        }
        observation['selection']['pack_list_visible'] = True
    return observation, error


def replay(directory):
    snapshots = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(directory).glob('*.json'))]
    counts = Counter()
    last_unrecognized = float('-inf')
    for snapshot in snapshots:
        observation, error = reconstruct(snapshot)
        now = datetime.fromisoformat(snapshot['timestamp']).timestamp()
        raw_details = failure_details(observation, error)
        details = failure_details(observation, error, unrecognized_age=now - last_unrecognized)
        if 'screen_unrecognized' in details['reason']:
            last_unrecognized = now
        counts['before'] += bool(snapshot.get('reason'))
        counts['after_without_cooldown'] += bool(raw_details['reason'])
        counts['after'] += bool(details['reason'])
        counts['total'] += 1
        if details['reason']:
            counts[details['screen_type_guess']] += 1
    return dict(counts)


if __name__ == '__main__':
    print(json.dumps(replay(Path(__file__).resolve().parents[1] / 'data' / 'ocr_failures_snapshot'), indent=2))
