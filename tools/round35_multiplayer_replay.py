"""Fresh result OCR plus explicitly reconstructed missing room/roster controls.

No room/selection pixels survived tonight. Diagnostic scene timestamps and
selection values are recorded; room local HARD and role True/None are supplied
investigation controls. Their .99 confidence is stipulated, NOT measured OCR.
The historical auto-save claim is conditional on those missing preconditions.
Only disposable databases and copied session evidence are written.
"""
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
from unittest.mock import patch

from PIL import Image, ImageDraw

from dance_results.database import LibraryDatabase
from dance_results.download_tracking import metadata_snapshot, verified_download_binding, download_view
from dance_results.library_service import LibraryService, LibraryError
from dance_results.monitor import Monitor
from dance_results.monitor_ocr import image_hash
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_temporal import ResultConsistency
from dance_results.result_ocr import recognize_result
from dance_results.room_identity import field
from dance_results.room_sampler import RoomSampler
from dance_results.source_choice import offer
from dance_results.sqlite_safety import audit, projection, row_limits, check_integrity
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request
from .round34_jp_replay import readonly, read, seconds


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/round35'
SOURCE = ROOT/'data/library-live-1617.sqlite3'
CASES = [('d7c35757', 4381), ('6b7dd400', 1982), ('c5dbf34f', 4280), ('7026885e', None)]


def room(title, level):
    return dict(title=title, difficulty='HARD', game_level=level,
        fields=dict(difficulty=field('HARD', 'RECONSTRUCTED_LOCAL_HARD_NOT_MEASURED_OCR', .99)))


def consume(tracker, observation, now, stamp, image=None):
    with patch('dance_results.monitor_tracking.utc', return_value=stamp):
        tracker.consume(copy.deepcopy(observation), now=now, sample_id=now, image=image)


def finish(monitor, original, fresh):
    consistency = ResultConsistency()
    sid = monitor.tracker.session['session_id']
    for index, (image, ocr) in enumerate(fresh):
        obs = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=copy.deepcopy(ocr))
        consistency.observe(obs, sid, 1200+index*3, image)
        with patch('dance_results.monitor_tracking.utc', return_value=original['ended_at']):
            monitor._consume_observation(obs, 1200+index*3, image, 1200+index*3, consistency)
    return monitor.reviews._read(sid)[1]


def outcome(database, monitor, row):
    body, blockers = automatic_request(monitor.reviews, row)
    catalog = ReadOnlyCatalog(database)
    try:
        prompt = offer(catalog, row, verified_download_binding(monitor.reviews, row))
    finally:
        catalog.close()
    result = dict(bound_song_id=row['draft']['song_id'], binding_method=row['draft'].get('identity_binding_method'),
        temporal_status=row['ocr']['temporal_validation']['status'], automatic_blockers=blockers,
        picker=prompt['kind'] if prompt else None, saved=False)
    if body:
        result.update(bound_song_id=body['song_id'], binding_method=body.get('identity_binding_method'))
        store = SubmissionService(LibraryService(database), monitor.reviews)
        store.auto_allowed = lambda sid: True
        try:
            receipt = store.submit_verified(row['session_id'])
            retry = store.submit_verified(row['session_id'])
            record = receipt['record']
            result.update(saved=True, retry_idempotent=retry['idempotent'],
                stored={k: record.get(k) for k in ('record_id', 'song_id', 'difficulty_id', 'difficulty',
                    'game_level', 'perfect', 'cool', 'bad', 'miss', 'total_notes', 'lamp_status', 'confirmation_method')})
        except LibraryError as error:
            result['submit_error'] = str(error)
    return result


def replay_case(database, scratch, original, expected, events, fresh):
    monitor = Monitor(database, scratch/'replay'/original['session_id'])
    tracker = Tracker(monitor.directory)
    monitor.tracker = tracker
    tracker.epoch = original['epoch']
    title = original['playing_preview']['title']
    level = original['playing_preview']['game_level']
    start = seconds(original['started_at'])
    before = [e for e in events if seconds(e['at']) <= start]
    host = original['session_id'].startswith('d7c35757')
    reconstructed = ['Room preview title/HARD reconstructed from supplied investigation; no room pixels or local-difficulty confidences retained.']
    if host:
        selected = [e for e in before if e['event'] == 'selection_changed' and e.get('title') == title][-1]
        fields = {k: field(selected[k], 'RECORDED_VALUE_STIPULATED_CONFIDENCE_NOT_OCR', .99)
                  for k in ('title', 'difficulty', 'game_level')}
        catalog = ReadOnlyCatalog(database)
        try:
            match = catalog.match_official(fields, source_confirmed=True)
        finally:
            catalog.close()
        selection = dict(state='SONG_SELECT', frame_hash='reconstructed-selection', fields=fields,
                         matching=match, source_type='official')
        for now in (970, 971):
            consume(tracker, selection, now, selected['at'])
        reconstructed.append('This Game selection title/HARD/LV15 recorded in selection_changed; original confidence/source and second selection sample stipulated. Host True then None from investigation, not logged roster values.')
    room_events = [e for e in before if e['event'] == 'recognition' and e['state'] == 'WAITING_ROOM'
                   and start-seconds(e['at']) < (40 if host else 80)]
    if not room_events:
        raise ValueError('Expected recorded WAITING_ROOM timestamps')
    for index, event in enumerate(room_events):
        role = (True if index == 0 else None) if host else False
        consume(tracker, dict(state='WAITING_ROOM', frame_hash=event['frame_hash'],
            room_preview=room(title, level), context=dict(local_is_host=role)),
            1000+seconds(event['at'])-start, event['at'])
    playing = dict(state='PLAYING', frame_hash='recorded-playing-transition', playing_preview=original['playing_preview'])
    for now in (1000, 1001):
        consume(tracker, playing, now, original['started_at'])
    locked = copy.deepcopy(tracker.session)
    result = dict(original_session_id=original['session_id'], expected_song_id=expected,
        identity_context=locked['identity_context'], selected_at_play=bool(locked['selection']),
        recorded_room_frames=len(room_events), reconstruction=reconstructed,
        evidence_grade='conditional_controls_missing_preplay_pixels', no_new_live_gameplay=True)
    if not original.get('result'):
        for now in (1100, 1101):
            consume(tracker, dict(state='WAITING_ROOM', room_preview=room(title, level)), now, original['ended_at'])
        result.update(saved=False, bound_song_id=None, status=tracker.last_session['status'],
            interruption_reason=tracker.last_session['interruption_reason'], picker=None)
    else:
        row = finish(monitor, original, fresh)
        result.update(outcome(database, monitor, row))
        result['fresh_result_fields'] = [ocr['fields'] for _, ocr in fresh]
    result['passed'] = result['bound_song_id'] == expected and result['saved'] == (expected is not None) and not result.get('picker')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def synthetic_download(database, scratch, fresh):
    """Recorded APT. acquisition shape; synthetic local metadata and FC/non-FC."""
    from uuid import uuid4
    dbpath = scratch/'apt'/ 'ISOLATED.sqlite3'
    dbpath.parent.mkdir()
    with closing(readonly(database)) as source, closing(sqlite3.connect(dbpath)) as target:
        source.backup(target)
    evidence = read(ROOT/'reports/round35-inputs/realplay11/evidence/7026885e-download-evidence.json')
    candidate = copy.deepcopy(next(c for c in evidence['candidates'] if c['song_id'] == 4582))
    folder = scratch/'apt/SONG'/candidate['relative_path']
    folder.mkdir(parents=True)
    (folder/'song.sm').write_text('#TITLE:APT.;#ARTIST:SYNTHETIC REPLAY;', encoding='utf8')
    with closing(sqlite3.connect(dbpath)) as db:
        db.execute('UPDATE library_roots SET path=? WHERE root_id=?', (str(scratch/'apt/SONG'), candidate['root_id']))
        db.commit()
    candidate['stable_metadata'] = metadata_snapshot(folder)['files']
    controls = []
    for fc in (True, False):
        monitor = Monitor(dbpath, scratch/'apt'/('fc' if fc else 'non-fc'))
        tracker = Tracker(monitor.directory)
        monitor.tracker = tracker
        entry = '2026-11-16T15:59:00+00:00'
        for now in (1, 2):
            consume(tracker, dict(state='WAITING_ROOM', room_preview=room('APT.', 99),
                context=dict(local_is_host=False)), now, entry)
        tracker.custom_cue('download_folder_changed', frames=1)
        for now in (3, 4):
            consume(tracker, dict(state='PLAYING', playing_preview=dict(title='APT.', game_level=24,
                fields=dict(game_level=field(24, 'SYNTHETIC_LOCAL_PLAYING_LV_NOT_HOST_ROOM_LV', .99)))),
                now, '2026-11-16T16:00:00+00:00')
        session = tracker.session
        fields = copy.deepcopy(fresh[1][1])
        fields['fields']['title'] = field('APT.', 'SYNTHETIC_TITLE_NOT_RESULT_OCR', .99)
        fields['multiplayer'] = True
        fields['errors'] = [e for e in fields['errors'] if e != 'footer_level_is_host_not_local']
        if not fc:
            own = [r['fields'] for r in fields['player_rows'] if r.get('name') == 'TC']
            for raw in [fields['fields'], *own]:
                raw['perfect']['value'] -= 1
                raw['bad']['value'] += 1
                raw['max_combo']['value'] -= 1
        # Distinct generated frames are explicitly synthetic temporal controls.
        generated = []
        for index in (0, 1):
            image = Image.new('RGB', (1024, 800), 'gray')
            ImageDraw.Draw(image).text((10,10), 'SYNTHETIC APT. '+str(index), fill='white')
            generated.append((image, copy.deepcopy(fields)))
        room_context = session['identity_context']['guest_room']
        e = dict(evidence, episode_id=str(uuid4()), session_id=session['session_id'], epoch=tracker.epoch,
            room_key=room_context['room_key'], room_entry_at=room_context['room_entry_at'],
            room_observation=room('APT.', 99), candidates=[candidate], existing_updates=[], uncertain_imports=[],
            scan_completed=False, status='scanning', result_seen=True)
        evidence_path = monitor.directory/'sessions'/session['session_id']/'download-evidence.json'
        atomic_json(evidence_path, e)
        row = finish(monitor, dict(ended_at='2026-11-16T16:02:00+00:00'), generated)
        pending = download_view(e, row)
        e.update(scan_completed=True, status='pending_identity')
        atomic_json(evidence_path, e)
        row = monitor.reviews.detail(session['session_id'])
        binding = verified_download_binding(monitor.reviews, row)
        item = outcome(dbpath, monitor, row)
        item.update(scenario='synthetic_APT_FC' if fc else 'synthetic_APT_non_FC',
            download_bound_id=binding['song_id'] if binding else None,
            scan_pending_visible=pending['scanning'], prefilled_song_id=row['draft']['song_id'],
            synthetic=True, provenance='Recorded APT. song 4582/folder; new stable local file metadata, room/playing/title/FC control reconstructed. Actual APT. was never played.')
        item['passed'] = item['download_bound_id'] == 4582 and item['prefilled_song_id'] == 4582 and item['saved'] == fc
        if not fc:
            item['passed'] &= item['automatic_blockers'] == ['automatic_uncharted_complete_combo_required']
        controls.append(item)
        print(json.dumps(item, ensure_ascii=False, indent=2), flush=True)
    return controls


def sampler_benchmark(engine):
    tracker = Tracker(OUT/'sampler-diagnostics')
    tracker.observe_room(dict(room_preview=room('Genjitsu Escape', 24)))
    sampler = RoomSampler()
    sampler.key = (tracker.epoch, tracker.room_key)
    sampler.slot, sampler.host_slot = 2, 1
    with Image.open(ROOT/'tests/fixtures/round35/room-nomap-anon.png') as image:
        costs = [sampler.sample(image, tracker, sample_id=i) for i in range(200)]
        sampler.badge_hash = None
        ocr_cost = sampler.sample(image, tracker, engine, 201)
        sampler.sample(image, tracker, engine, 202)
    return dict(frames=len(costs), pixel_mean_ms=statistics.mean(costs),
        pixel_p95_ms=sorted(costs)[189], changed_badge_with_line_ocr_ms=ocr_cost,
        cues=tracker.room_custom, engine='fresh local recognize_line on anonymized fixture')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    snapshots = [ROOT/'data'/name for name in ('library.sqlite3', 'library-live-1930.sqlite3', SOURCE.name)]
    before_sha = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots}
    scratch = ROOT/'data/round35_scratch'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    scratch.mkdir(parents=True)
    database = scratch/'ISOLATED.sqlite3'
    with closing(readonly(SOURCE)) as source, closing(sqlite3.connect(database)) as target:
        source.backup(target)
    originals = []
    for prefix, expected in CASES:
        folder = next((ROOT/'data/monitor/sessions').glob(prefix+'*'))
        archive = scratch/'originals'/folder.name
        shutil.copytree(folder, archive)
        originals.append((archive, read(archive/'session.json'), expected))
    with closing(sqlite3.connect(database)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='formal_record_preserve'").fetchone()[0]
        db.execute('DROP TRIGGER formal_record_preserve')
        removed = []
        for _, original, expected in originals:
            if expected is None:
                continue
            ids = [r[0] for r in db.execute('SELECT record_id FROM play_records WHERE session_id=?', (original['session_id'],))]
            removed.extend(ids)
            db.execute('DELETE FROM play_records WHERE session_id=?', (original['session_id'],))
            db.execute('DELETE FROM play_sessions WHERE session_id=?', (original['session_id'],))
        db.execute(trigger)
        db.commit()
    if sorted(removed) != [70, 74, 75]:
        raise ValueError('Unexpected snapshot receipts')
    with closing(readonly(database)) as db:
        columns, limits, retained = projection(db), row_limits(db), audit(db)
    with LibraryDatabase(database) as library:
        migration = library.migration_result
    events = [json.loads(line) for line in (ROOT/'reports/round35-inputs/realplay11/evidence/diagnostics-tonight.jsonl').read_text(encoding='utf8').splitlines()]
    engine = LocalOCREngine(ROOT/'.ocr-models', low_latency=True)
    results, apt_fresh = [], None
    for archive, original, expected in originals:
        fresh = []
        if original.get('result'):
            for index in (0, 1):
                with Image.open(archive/'result-observations'/f'frame-{index}.png') as source:
                    image = source.convert('RGB').resize((1024, 800))
                fresh.append((image, recognize_result(image, engine, 'TC')))
            if original['session_id'].startswith('6b7dd400'):
                apt_fresh = fresh
        results.append(replay_case(database, scratch, original, expected, events, fresh))
    synthetic = synthetic_download(database, scratch, apt_fresh)
    with closing(readonly(database)) as db:
        unchanged = audit(db, columns, limits) == retained
        check_integrity(db)
    after_sha = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots}
    report = dict(scope=__doc__, scratch_database=str(database), removed_record_ids=removed,
        migration=migration, sessions=results, synthetic_download=synthetic,
        sampler=sampler_benchmark(engine), snapshot_sha256_before=before_sha, snapshot_sha256_after=after_sha,
        snapshots_unchanged=before_sha == after_sha, retained_old_rows_unchanged=unchanged)
    report['passed'] = all(r['passed'] for r in results+synthetic) and unchanged and before_sha == after_sha
    atomic_json(OUT/'mp-replay.json', report)
    print('Round 3.5 conditional replay:', report['passed'], flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
