"""Replay session-4 evidence through tracking, review and real disposable submits.

Locked selections are recorded debounced evidence. For the lost selection, a
saved failure payload is reused only when the original log records its exact
old identity fingerprint again. Missing result payloads never count as frames.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from PIL import Image

from dance_results.library_service import LibraryService
from dance_results.monitor import Monitor
from dance_results.monitor_ocr import image_hash
from dance_results.monitor_tracking import Tracker, fingerprint, atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_temporal import ResultConsistency
from dance_results.result_ocr import recognize_result, refine_footer_title
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request

PROJECT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def seconds(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selection_timing(events, original, previous_end=None):
    """Describe logged observations, never infer unrecorded subsecond dwell."""
    start = seconds(original['started_at'])
    lower = seconds(previous_end) if previous_end else start-90
    relevant = [e for e in events if lower <= seconds(e['at']) <= start+.02]
    selected = [e for e in relevant if e['event'] == 'recognition' and e['state'] == 'SONG_SELECT']
    playing = [e for e in relevant if e['event'] == 'recognition' and e['state'] == 'PLAYING'
               and seconds(e['at']) >= start-8]
    first_play = playing[0]['at'] if playing else None
    last_select = selected[-1]['at'] if selected else None
    changes = [e for e in relevant if e['event'] == 'selection_changed'
               and last_select and seconds(e['at']) <= seconds(last_select)]
    final_change = changes[-1]['at'] if changes else None
    final_observations = [e for e in selected if seconds(e['at']) >= seconds(final_change)] if final_change else []
    final_frames = len(final_observations)
    locked = original.get('selection') or {}
    good = locked.get('observed_at')
    return dict(
        logged_selection_frames=len(selected), final_fingerprint_frames=final_frames,
        final_fingerprint_first_observed_at=final_change, last_song_select_at=last_select,
        last_saved_good_selection_at=good, first_playing_at=first_play,
        confirmed_playing_at=original['started_at'],
        last_select_to_first_playing_seconds=round(seconds(first_play)-seconds(last_select), 3) if first_play and last_select else None,
        last_good_to_first_playing_seconds=round(seconds(first_play)-seconds(good), 3) if first_play and good else None,
        last_good_to_confirmed_playing_seconds=round(start-seconds(good), 3) if good else None,
        observed_final_fingerprint_span_seconds=round(seconds(last_select)-seconds(final_observations[0]['at']), 3) if final_observations else None,
        fast_selection_evidence='few_final_fingerprint_observations' if 0 < final_frames <= 2 else
            'no_selection_observation' if not selected else 'multiple_observations',
        subsecond_user_dwell='not_measurable_from_saved_OCR_logs',
    )


def compressed_fast_probe(directory, selection):
    """Explicit synthetic timing probe using saved fields, not historical dwell."""
    if not selection or (selection.get('pack') or {}).get('status') != 'confirmed':
        return dict(status='not_testable', reason='saved_confirmed_pack_and_selection_payload_missing')
    tracker = Tracker(directory)
    tracker.current_pack = copy.deepcopy(selection['pack'])
    observed = dict(state='SONG_SELECT', frame_hash=selection['frame_hash'],
        fields=copy.deepcopy(selection['fields']), matching=copy.deepcopy(selection['matching']),
        source_type=selection['source_type'])
    tracker.consume(observed, now=1, sample_id=1)
    tracker.invalidate_selection('black_capture')
    for now in (1.2, 1.4):
        tracker.consume(dict(state='PLAYING', frame_hash=str(now)), now=now, sample_id=now)
    consumed = (tracker.session or {}).get('selection')
    return dict(status='passed' if consumed and consumed['fields'] == observed['fields'] else 'failed',
                single_selection_frame_to_playing_seconds=.2, confirmation_seconds=.4,
                selection_consumed=bool(consumed), handoff=(consumed or {}).get('handoff'),
                synthetic_timing=True, score_submission_attempted=False)


def acceptance(report):
    plays = {p['original_session_id'][:8]: p for p in report['plays']}
    checks = {}
    million = plays.get('4bff9c7a', {})
    values = million.get('result_values') or {}
    checks['million_retained'] = (million.get('review_status') == 'pending_review'
        and values.get('title') == 'Million of Bravery' and values.get('grade') == 'A++'
        and [values.get(k) for k in ('perfect', 'cool', 'bad', 'miss')] == [667, 9, 0, 0]
        and million.get('recovery_reason') == 'result_left_before_confirmation')
    dawn = plays.get('203488b8', {})
    checks['dawn_saved_762'] = ((dawn.get('simulated_submission') or {}).get('song_id') == 762
        and dawn.get('review_status') == 'saved_verified_auto'
        and [dawn['result_values'][k] for k in ('perfect', 'cool', 'bad', 'miss')] == [1802, 71, 7, 19])
    checks['lull_bound_286'] = plays.get('b2af8bdc', {}).get('bound_song_id') == 286
    lazy = plays.get('72c41977', {})
    checks['lazy_selection_bound_21'] = (lazy.get('bound_song_id') == 21
        and lazy.get('selection_consumed') and lazy.get('pending_reason') != 'no_fresh_selection')
    ignite = plays.get('d5d3957a', {})
    checks['ignite_quick_leave_pending'] = (ignite.get('review_status') == 'pending_review'
        and 'result_left_before_confirmation' in (ignite.get('temporal_reasons') or [])
        and ignite.get('simulated_submission') is None)
    checks['four_auto_saves_preserved'] = all(plays.get(sid, {}).get('simulated_submission')
        and plays[sid]['review_status'] == 'saved_verified_auto' for sid in ('826e1c86', '09b734cc', 'b1cc6c9d', '8834a38c'))
    checks['one_good_sample_stays_pending'] = all(plays.get(sid, {}).get('review_status') == 'pending_review'
        and plays[sid]['simulated_submission'] is None for sid in ('0f9fa6e5', '07b66990'))
    checks['ghost_has_no_review'] = plays.get('d2355851', {}).get('review_status') is None
    checks['saved_reviews_excluded'] = all(not p['listed_pending'] for p in report['plays'] if p['simulated_submission'])
    checks['all_twelve_replayed'] = len(report['plays']) == 12
    checks['snapshot_unchanged'] = report['snapshot_unchanged']
    checks['fast_selection_probes'] = all(p['fast_selection_probe']['status'] == 'passed'
        for p in report['plays'] if p['fast_selection_probe']['status'] != 'not_testable')
    return checks


def replay(snapshot, failures, database):
    before = digest(database)
    events = [json.loads(line) for line in (snapshot/'diagnostics.jsonl').read_text(encoding='utf8').splitlines()]
    sessions = sorted((read(p) for p in (snapshot/'sessions').glob('*/session.json')), key=lambda s: s['started_at'])
    engine = LocalOCREngine(PROJECT/'.ocr-models', low_latency=True)
    catalog = ReadOnlyCatalog(database)
    evidence = {}
    selections = []
    try:
        for path in sorted(failures.glob('*.json')):
            meta = read(path)
            if meta['screen_type_guess'] not in ('song_select', 'results'):
                continue
            with Image.open(path.with_suffix('.png')) as source:
                image = source.convert('RGB').resize((1024, 800))
            frame_hash = image_hash(image)
            if meta['screen_type_guess'] == 'song_select':
                fields = {k: dict(value=v, confidence=meta['confidence'].get(k, 0))
                          for k, v in meta['fields'].items()}
                pack = next((p for p in catalog.packs if p['name'] == meta.get('selected_pack')), None)
                pack = dict(pack, status='confirmed', source=meta['pack_reason']) if pack else None
                if pack and meta['pack_reason'] in ('right_song_list_unique_pack', 'right_song_list_and_row_hint'):
                    selections.append(dict(at=seconds(meta['timestamp']), fields=fields, pack=pack,
                                           frame_hash=frame_hash, source_type='custom', matching={}))
                continue
            ocr = recognize_result(image, engine, 'TC')
            # Recorded readings/confidence remain evidence. Only the title ROI
            # is intentionally reinterpreted by this round's pixel-only fix.
            for key, val in meta['fields'].items():
                if key == 'title':
                    continue
                ocr['fields'][key].update(value=val, confidence=meta['confidence'].get(key, 0))
            evidence[frame_hash] = (ocr, image)

        for original in sessions:
            folder = snapshot/'sessions'/original['session_id']
            path = folder/'result-review.json'
            if not path.exists():
                continue
            ocr = read(path)['ocr']
            for sample in ocr.get('temporal_validation', {}).get('samples', []):
                with Image.open(folder/sample['path']) as source:
                    image = source.convert('RGB').resize((1024, 800))
                if image_hash(image) != sample['frame_hash']:
                    raise ValueError('Saved result pixel hash differs: '+str(folder))
                saved = copy.deepcopy(ocr)
                saved.pop('temporal_validation', None)
                saved['fields'] = copy.deepcopy(sample['fields'])
                saved['fields']['title'], _ = refine_footer_title(image, engine, saved['fields']['title'])
                evidence[sample['frame_hash']] = (saved, image)

        plays = []
        with tempfile.TemporaryDirectory(prefix='round28-replay-', dir=PROJECT/'reports') as tmp:
            for index, original in enumerate(sessions):
                # One independent copy per play; new replay UUIDs exercise a
                # fresh transaction while preserving all original receipts.
                directory = Path(tmp)/str(index)
                directory.mkdir()
                disposable = directory/'library.sqlite3'
                shutil.copyfile(database, disposable)
                monitor = Monitor(disposable, directory/'monitor')
                tracker = monitor.tracker = Tracker(monitor.directory)
                temporal = ResultConsistency()
                start = seconds(original['started_at'])
                end = seconds(original['ended_at'])+8
                if index+1 < len(sessions):
                    end = min(end, seconds(sessions[index+1]['started_at'])-1)
                begin = start-60
                locked = copy.deepcopy(original.get('selection'))
                ordered = [(seconds(e['at']), 'event', e) for e in events
                           if begin <= seconds(e['at']) <= end]
                if locked:
                    ordered.append((seconds(locked['observed_at']), 'locked', locked))
                else:
                    ordered.extend((s['at'], 'selection', s) for s in selections if start-45 <= s['at'] < start)
                ordered.sort(key=lambda e: (e[0], e[1]))
                known_identities = {}
                repeated_selection = False
                current_identity = None
                skipped_results = 0
                black_preserved = None
                active = False
                for moment, kind, item in ordered:
                    now = moment-begin
                    if kind in ('locked', 'selection'):
                        payload = copy.deepcopy(item)
                        pack = payload.get('pack') or {}
                        payload['matching'] = catalog.match_identity(payload['fields'], pack.get('pack_id'), payload['source_type'])
                        tracker.current_pack = copy.deepcopy(payload.get('pack'))
                        if kind == 'locked':
                            tracker.selection = dict(payload, last_seen=now, epoch=tracker.epoch)
                        else:
                            observation = dict(payload, state='SONG_SELECT')
                            # The original fingerprint included every optional
                            # value. A repeated ID proves the same OCR values
                            # appeared again, without inventing its pixels.
                            key = fingerprint(dict(values={k: v['value'] for k, v in payload['fields'].items()},
                                pack_id=pack.get('pack_id'), source_type=payload['source_type'], epoch=1))
                            known_identities[key] = observation
                        continue
                    event = item['event']
                    if event == 'selection_changed':
                        current_identity = item['observation_id']
                        continue
                    if event == 'connection' and '黑色' in (item.get('error') or ''):
                        prior = copy.deepcopy(tracker.selection)
                        tracker.invalidate_selection('black_capture')
                        black_preserved = prior is not None and tracker.selection == prior
                        continue
                    if event != 'recognition':
                        continue
                    state = item['state']
                    if state in ('PLAYING', 'RESULT', 'GAME_OVER') and moment < start-8:
                        continue
                    observation = dict(state=state, frame_hash=item['frame_hash'])
                    image = Image.new('RGB', (1024, 800), 'gray')
                    if state == 'SONG_SELECT':
                        if not locked and current_identity in known_identities:
                            observation.update(copy.deepcopy(known_identities[current_identity]))
                            observation['frame_hash'] = item['frame_hash']
                            repeated_selection = True
                        else:
                            # No saved OCR payload: no fabricated selection.
                            observation.update(fields={}, matching={})
                    elif state == 'WAITING_ROOM':
                        observation['context'] = dict(local_is_host=True)
                    elif state == 'PLAYING':
                        observation['playing_preview'] = original.get('playing_preview') or {}
                    elif state in ('RESULT', 'GAME_OVER'):
                        saved = evidence.get(item['frame_hash'])
                        if not saved:
                            skipped_results += 1
                            continue
                        ocr, image = saved
                        observation['result_ocr'] = copy.deepcopy(ocr)
                    temporal.observe(observation, (tracker.session or {}).get('session_id'), now, image)
                    monitor._consume_observation(observation, now, image, moment, temporal)
                    if tracker.session:
                        active = True
                    if active and tracker.last_session:
                        break
                if tracker.session:
                    tracker.interrupt(original.get('interruption_reason') or 'replay_evidence_ended')
                    monitor._ingest_pending_result()
                pending = tracker.last_session or {}
                row = monitor.reviews._read(pending['session_id'])[1] if pending.get('status') == 'pending_result' else None
                body, issues = automatic_request(monitor.reviews, row) if row else (None, ['no_result_review'])
                submission = None
                if body:
                    service = SubmissionService(LibraryService(disposable), monitor.reviews)
                    service.auto_allowed = lambda sid: True
                    try:
                        receipt = service.submit_verified(row['session_id'])
                        submission = {k: receipt['record'][k] for k in ('record_id', 'song_id', 'difficulty', 'confirmation_method')}
                        row = monitor.reviews._read(row['session_id'])[1]
                    except Exception as error:
                        issues.append('submission_failed: '+str(error))
                timing = selection_timing(events, original, sessions[index-1]['ended_at'] if index else None)
                timing['selection_kept_on_replay'] = bool(pending.get('selection'))
                replay_selection = pending.get('selection') or {}
                observed_time = begin+replay_selection['last_seen'] if 'last_seen' in replay_selection else None
                from datetime import timezone
                timing['last_replayed_selection_evidence_at'] = datetime.fromtimestamp(observed_time, timezone.utc).isoformat() if observed_time else None
                timing['replayed_selection_to_first_playing_seconds'] = round(seconds(timing['first_playing_at'])-observed_time, 3) if observed_time and timing['first_playing_at'] else None
                plays.append(dict(
                    original_session_id=original['session_id'], original_title=(original.get('playing_preview') or {}).get('title'),
                    session_status=pending.get('status'), pending_reason=pending.get('pending_reason'),
                    selection_consumed=bool(pending.get('selection')), repeated_selection_fingerprint=repeated_selection,
                    black_preserved=black_preserved, bound_song_id=(row['draft'].get('song_id') if row else pending.get('song_id')),
                    review_status=row['status'] if row else None,
                    result_values={k: row['ocr']['fields'][k]['value'] for k in ('title', 'grade', 'game_level', 'perfect', 'cool', 'bad', 'miss', 'game_score')} if row else None,
                    temporal_status=row['ocr'].get('temporal_validation', {}).get('status') if row else None,
                    temporal_reasons=row['ocr'].get('temporal_validation', {}).get('reasons') if row else None,
                    recovery_reason=(pending.get('result') or {}).get('recovery_reason'),
                    auto_eligible=body is not None, automatic_issues=issues, simulated_submission=submission,
                    listed_pending=any(r['session_id'] == pending.get('session_id') for r in monitor.reviews.list()['results']),
                    unavailable_result_payloads_skipped=skipped_results,
                    selection_timing=timing,
                    fast_selection_probe=compressed_fast_probe(directory/'fast-probe', pending.get('selection')),
                ))
        return dict(plays=plays, snapshot_hash_before=before, snapshot_hash_after=digest(database),
                    snapshot_unchanged=before == digest(database),
                    limitation='Only saved result images count toward temporal confirmation. Locked selections and repeated logged selection fingerprints are recorded identity evidence; missing result payloads are skipped. Submissions use new replay UUIDs on independent disposable DB copies; original receipts remain intact.')
    finally:
        catalog.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=PROJECT/'data/realplay4_snapshot')
    parser.add_argument('--failures', type=Path, default=PROJECT/'data/ocr_failures_snapshot')
    parser.add_argument('--database', type=Path, default=PROJECT/'data/library.sqlite3')
    args = parser.parse_args()
    report = replay(args.snapshot, args.failures, args.database)
    report['acceptance'] = acceptance(report)
    report['passed'] = all(report['acceptance'].values())
    atomic_json(PROJECT/'reports/round28-replay.json', report)
    lines = [report['limitation'], '']
    for play in report['plays']:
        lines.append(json.dumps(play, ensure_ascii=False, indent=2))
    lines.append('Snapshot unchanged: '+str(report['snapshot_unchanged']))
    lines.append('Acceptance: '+json.dumps(report['acceptance'], ensure_ascii=False, indent=2))
    (PROJECT/'reports/round28-replay.txt').write_text('\n'.join(lines)+'\n', encoding='utf8')
    print('\n'.join(lines))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
