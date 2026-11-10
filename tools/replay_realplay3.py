"""Replay saved session-3 evidence without changing the catalog or snapshots.

The log contains state/hash order, not every OCR payload. Only saved frames may
provide result fields; an unrecorded result frame cannot confirm a score. Locked
selections are already debounced evidence. The missing pre-fade selection uses
the playing preview only as a retention probe, explicitly reported below.
"""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from PIL import Image

from dance_results.monitor import Monitor
from dance_results.monitor_ocr import image_hash, apply_selection
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_screens import parse_selection
from dance_results.ocr_static import cached_page, normalize_tokens
from dance_results.ocr_temporal import ResultConsistency
from dance_results.result_ocr import recognize_result
from dance_results.verified_auto import automatic_request

PROJECT = Path(__file__).resolve().parents[1]


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def replay(snapshot, database, *, engine=None):
    snapshot, database = Path(snapshot), Path(database)
    if not snapshot.is_dir():
        return dict(status='skipped', reason='session-3 snapshot is missing', plays=[])
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    events = [json.loads(line) for line in (snapshot/'diagnostics.jsonl').read_text(encoding='utf-8').splitlines()]
    sessions = sorted((read_json(p) for p in (snapshot/'sessions').glob('*/session.json')),
                      key=lambda s: s['started_at'])
    engine = engine or LocalOCREngine(PROJECT/'.ocr-models', low_latency=True)
    catalog = ReadOnlyCatalog(database)
    evidence = {}
    select_evidence = []

    def recognize(path, tokens=None):
        with Image.open(path) as original:
            image = original.convert('RGB')
            if tokens is None:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                tokens = normalize_tokens(cached_page(engine, image, digest, PROJECT/'reports/phase4a/cache'),
                                          *image.size)
            return image, tokens

    try:
        for path in sorted((snapshot/'ocr_failures').glob('*.json')):
            metadata = read_json(path)
            kind = metadata['screen_type_guess']
            if kind not in ('results', 'song_select'):
                continue
            image, tokens = recognize(path.with_suffix('.png'))
            normalized = image.resize((1024, 800))
            digest = image_hash(normalized)
            if kind == 'song_select':
                select_evidence.append((timestamp(metadata['timestamp']), digest,
                                        parse_selection(tokens, normalized), image))
            else:
                ocr = recognize_result(image, engine, 'TC', tokens=tokens)
                # Preserve actual recorded fields/confidences. A formerly absent
                # title can be recovered by the new parser from this same PNG.
                for key, value in metadata['fields'].items():
                    if key == 'title' and value is None:
                        continue
                    old = ocr['fields'].get(key, {})
                    reason = next((r.split(': ', 1)[1] for r in metadata['reason']
                                   if r.startswith(key+': ') and r not in (key+': missing', key+': low_confidence')), None)
                    ocr['fields'][key] = dict(old, value=value,
                                             confidence=metadata['confidence'].get(key, 0), reason=reason)
                evidence[digest] = (ocr, image)
        for session in sessions:
            folder = snapshot/'sessions'/session['session_id']
            if not (folder/'result-review.json').exists():
                continue
            ocr = read_json(folder/'result-review.json')['ocr']
            for sample in ocr.get('temporal_validation', {}).get('samples', []):
                image, _ = recognize(folder/sample['path'], tokens=[])
                saved = copy.deepcopy(ocr)
                saved.pop('temporal_validation', None)
                saved['fields'] = copy.deepcopy(sample['fields'])
                if saved['fields']['title']['value'] is None:
                    saved = recognize_result(image, engine, 'TC', tokens=ocr['tokens'])
                evidence[sample['frame_hash']] = (saved, image)

        plays = []
        scratch = PROJECT/'reports'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='round26-replay-', dir=scratch) as temporary:
            for index, original in enumerate(sessions):
                directory = Path(temporary)/str(index)
                monitor = Monitor(database, directory)
                tracker = monitor.tracker = Tracker(directory)
                consistency = ResultConsistency()
                start = timestamp(original['started_at'])
                next_start = timestamp(sessions[index+1]['started_at']) if index+1 < len(sessions) else float('inf')
                end = min(next_start, timestamp(original['ended_at'])+25)
                locked = copy.deepcopy(original.get('selection'))
                missing_selection = locked is None
                black = [e for e in events if e.get('reason') == 'black_capture' and start-60 < timestamp(e['at']) < start]
                if missing_selection:
                    # No claim that preview fields carry song-select confidence.
                    # This probes preservation only; no matching/auto authority.
                    preview = original.get('playing_preview') or {}
                    locked = dict(fields={k: dict(value=preview.get(k), confidence=0, reason='preview_only_probe')
                                          for k in ('title', 'game_level', 'difficulty')},
                                  matching={}, source_type='unknown', pack=None)
                    seed_time = timestamp(black[0]['at'])-1 if black else start-5
                else:
                    seed_time = timestamp(locked['observed_at'])
                begin = min(seed_time-45, start-60)
                ordered = [(timestamp(e['at']), 'event', e) for e in events
                           if begin <= timestamp(e['at']) <= end
                           and (e['event'] == 'recognition' or e.get('reason') == 'black_capture')]
                ordered.append((seed_time, 'selection', locked))
                ordered.sort(key=lambda entry: (entry[0], entry[1]))
                skipped_results = 0
                black_preserved = None
                active_id = None
                for moment, kind, item in ordered:
                    now = moment-begin
                    if kind == 'selection':
                        pack = copy.deepcopy(tracker.current_pack)
                        matching = catalog.match_identity(item['fields'], (pack or {}).get('pack_id'))
                        sid = matching.get('confirmed_song_id')
                        source = catalog.songs[sid]['source_type'] if sid is not None else item['source_type']
                        tracker.selection = dict(item, pack=pack, matching=matching, source_type=source,
                                                 epoch=tracker.epoch, last_seen=now)
                        continue
                    if item.get('reason') == 'black_capture':
                        prior = copy.deepcopy(tracker.selection)
                        tracker.invalidate_selection('black_capture')
                        black_preserved = prior is not None and tracker.selection == prior
                        continue
                    state = item['state']
                    observation = dict(state=state, frame_hash=item['frame_hash'])
                    image = Image.new('RGB', (1024, 800), 'gray')
                    if state == 'SONG_SELECT':
                        actual = next((s for s in select_evidence if s[1] == item['frame_hash']), None)
                        if actual:
                            _, _, selection, image = actual
                            observation['selection'] = copy.deepcopy(selection)
                            observation = apply_selection(observation, image, tracker, catalog)
                        else:
                            observation.update(fields={}, matching={})
                    elif state == 'WAITING_ROOM':
                        observation.update(context=dict(local_is_host=True))
                    elif state == 'PLAYING':
                        observation['playing_preview'] = original.get('playing_preview') or {}
                    elif state in ('RESULT', 'GAME_OVER'):
                        saved = evidence.get(item['frame_hash'])
                        if not saved:
                            skipped_results += 1
                            continue
                        ocr, image = saved
                        observation['result_ocr'] = copy.deepcopy(ocr)
                    if state in ('RESULT', 'GAME_OVER'):
                        consistency.observe(observation, (tracker.session or {}).get('session_id'), now, image)
                    monitor._consume_observation(observation, now, image, moment, consistency)
                    if tracker.session and start-8 <= moment <= start+8:
                        active_id = tracker.session['session_id']
                    if active_id and tracker.last_session and tracker.last_session['session_id'] == active_id:
                        break
                pending = tracker.last_session or tracker.session or {}
                row = None
                if pending.get('status') == 'pending_result':
                    row = monitor.reviews._read(pending['session_id'])[1]
                body, issues = automatic_request(monitor.reviews, row) if row else (None, ['no_result_review'])
                simulated_submission = None
                if body is not None:
                    # Exercise the real transaction only on a disposable DB copy.
                    # Reviews and matching continue reading the original read-only
                    # snapshot, so no catalog enrichment is fabricated for replay.
                    from dance_results.library_service import LibraryService
                    from dance_results.submissions import SubmissionService

                    copy_database = Path(temporary)/f'catalog-{index}.sqlite3'
                    shutil.copyfile(database, copy_database)
                    submissions = SubmissionService(LibraryService(copy_database), monitor.reviews)
                    submissions.auto_allowed = lambda sid: True
                    record = submissions.submit_verified(pending['session_id'])['record']
                    simulated_submission = dict(
                        status='saved_on_disposable_copy',
                        confirmation_method=record['confirmation_method'],
                        observed_notes=record['evidence']['screen_note_observation'],
                    )
                plays.append(dict(
                    original_session=original['session_id'],
                    title=(original.get('playing_preview') or {}).get('title'),
                    song_id=pending.get('song_id'), status=pending.get('status'),
                    selection_pack_id=((pending.get('selection') or {}).get('pack') or {}).get('pack_id'),
                    selection_pack_source=((pending.get('selection') or {}).get('pack') or {}).get('source'),
                    review_status=row['status'] if row else None,
                    result_title=row['ocr']['fields']['title']['value'] if row else None,
                    result_grade=row['ocr']['fields']['grade']['value'] if row else None,
                    result_score=row['ocr']['fields']['game_score']['value'] if row else None,
                    result_judgments={k: row['ocr']['fields'][k]['value']
                                      for k in ('perfect', 'cool', 'bad', 'miss')} if row else None,
                    bound_pack=catalog.songs.get(pending.get('song_id'), {}).get('pack_name'),
                    difficulty=(body or {}).get('values', {}).get('difficulty'),
                    simulated_submission=simulated_submission,
                    recovery_reason=(pending.get('result') or {}).get('recovery_reason'),
                    temporal_status=row['ocr'].get('temporal_validation', {}).get('status') if row else None,
                    auto_eligible=body is not None, automatic_issues=issues,
                    black_capture_preserved_selection=black_preserved,
                    missing_selection_payload=missing_selection,
                    selection_probe_note='Playing preview only; original pre-fade selection payload is absent.' if missing_selection else None,
                    unavailable_result_payloads_skipped=skipped_results,
                ))
        return dict(status='replayed', plays=plays,
                    limitation='Only saved OCR frames are evidence; missing payloads do not count toward two-frame confirmation.',
                    catalog_unchanged=before == hashlib.sha256(database.read_bytes()).hexdigest())
    finally:
        catalog.close()


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=PROJECT/'data/realplay3_snapshot')
    parser.add_argument('--database', type=Path, default=PROJECT/'data/library.sqlite3')
    parser.add_argument('--output', type=Path, default=PROJECT/'reports/round26-replay.json')
    args = parser.parse_args()
    report = replay(args.snapshot, args.database)
    destination = args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
