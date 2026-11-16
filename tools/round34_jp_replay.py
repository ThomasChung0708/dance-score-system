"""Fresh two-frame result OCR; recorded context plus reconstructed controls.

No song-select pixels survive for missing selections. Never invent their OCR
confidences. Historical invalidations are replayed under current context rules;
the separate confirmed-pack control remains conditional evidence. Only scratch writes.
"""
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import Mock, patch

from PIL import Image

from dance_results.database import LibraryDatabase
from dance_results.library_service import LibraryService, LibraryError
from dance_results.monitor import Monitor
from dance_results.monitor_ocr import image_hash
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog, normalize, same_song_title_key
from dance_results.ocr_temporal import ResultConsistency
from dance_results.result_ocr import recognize_result
from dance_results.sqlite_safety import audit, projection, row_limits, check_integrity
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'data/library-live-1930.sqlite3'
OUT = ROOT/'reports/round34'
SESSIONS = (
    ('ae053598', 662, True),
    ('09eba4b6', 637, True),
    ('271750d2', 679, False),
    ('a0920b4b', 1279, True),
    ('66ea5571', 1295, True),
    ('ba5760d7', 1253, False),
    ('7664e3e7', 1249, False),
    ('49da36f2', 1313, False),
    ('8313407a', 1240, True),
)


def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    return db


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def seconds(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def collision_report(catalog):
    old_index = {}
    rows = catalog.db.execute('''
        SELECT saved_title AS title,song_id FROM songs
        UNION ALL SELECT latest_title,song_id FROM songs
        UNION ALL SELECT saved_title,song_id FROM song_sources
        UNION ALL SELECT display_title,song_id FROM song_title_aliases WHERE confirmed=1
    ''')
    for title, sid in rows:
        if normalize(title):
            old_index.setdefault(normalize(title), set()).add(sid)
    old_pairs = { (a, b) for ids in old_index.values() for a in ids for b in ids if a < b }
    groups = {}
    for name, ids in catalog.index.items():
        key = same_song_title_key(name)
        groups.setdefault(key, {})[name] = sorted(ids)
    collisions = []
    for key, names in groups.items():
        ids = {sid for songs in names.values() for sid in songs}
        # A new collision introduces a pair previously separated by normalize.
        pairs = {(a, b) for a in ids for b in ids if a < b}
        new_pairs = sorted(pairs-old_pairs)
        if new_pairs:
            collisions.append(dict(key=key, names=names, song_ids=sorted(ids), new_pairs=new_pairs,
                within_pack_pairs=[pair for pair in new_pairs if catalog.songs[pair[0]]['pack_id']
                    == catalog.songs[pair[1]]['pack_id']]))
    return dict(old_multi_song_keys=sum(len(ids) > 1 for ids in old_index.values()),
        total_multi_song_keys=sum(len({s for ids in n.values() for s in ids}) > 1 for n in groups.values()),
        new_collision_keys=len(collisions), new_song_pairs=sum(len(c['new_pairs']) for c in collisions),
        within_pack_pairs=sum(len(c['within_pack_pairs']) for c in collisions), details=collisions)


def run_case(database, scratch, archived, original, events, catalog, fresh, control, expected, auto):
    start = seconds(original['started_at'])
    applicable = [e for e in events if seconds(e['at']) <= start]
    pack_event = [e for e in applicable if e['event'] == 'pack_observed' and e.get('pack')][-1]
    pack = copy.deepcopy(pack_event['pack'])
    selected = copy.deepcopy(original.get('selection'))
    mode = 'confirmed_pack_control' if control else 'recorded_context'
    directory = scratch/mode/original['session_id']/'monitor'
    tracker = Tracker(directory)
    monitor = Monitor(database, directory)
    monitor.tracker = tracker
    tracker.title_catalog = catalog
    # Use recorded wall-clock deltas, on an independent monotonic replay clock.
    pack_time = 1000 + seconds(pack_event['at'])-start
    tracker.pack_observation(pack, inferred=True, now=pack_time)
    reconstruction = []
    scope_at = pack_event['at']
    if selected:
        # The locked selection includes its contemporaneous Pack (possibly
        # absent). It is later evidence than earlier diagnostic invalidations.
        # Its two original pre-play samples are unavailable: debounce/transition
        # timing is simulated, field values and confidences remain recorded.
        selected_pack = copy.deepcopy(selected.get('pack'))
        scope_at = selected['observed_at']
        if selected_pack:
            pack = selected_pack
        observed = seconds(selected['observed_at'])
        seen = 1000 + observed-start
        tracker.pack_observation(selected_pack, inferred=bool(selected_pack), uncertain=not selected_pack, now=seen)
        tracker.current_source = selected['source_type']
        selected['matching'] = catalog.match_identity(selected.get('ocr_fields', selected['fields']),
            (selected_pack or {}).get('pack_id'), selected['source_type'])
        for now in (seen-.1, seen):
            tracker.update_selection(dict(state='SONG_SELECT', **selected), now)
        tracker.state = 'SONG_SELECT'
        reconstruction.append('Recorded locked selection values/confidences; simulated second debounce sample, no selection pixels.')
    if control:
        tracker.pack_observation(pack, inferred=True, now=980)
        reconstruction.append('CONDITIONAL independently confirmed same Pack 20s before play; recorded contradictory room events omitted only in this control.')
    else:
        # A historical role/preview invalidation is not itself evidence of a
        # different room. Apply the new tracker policy rather than treating an
        # old implementation's Pack revocation as an external fact. True
        # window/list/category changes and Pack ambiguity still revoke scope.
        # A later locked selection records the current Pack or its absence,
        # plus independent title/source evidence. Earlier revocations precede
        # that selection; never reorder them after it (Qubism's Pack was
        # already absent when its valid selection was captured).
        context_events = [e for e in applicable if e['at'] > scope_at]
        applied = []
        for event in context_events:
            if event['event'] == 'pack_observed' and not event.get('pack'):
                tracker.pack_observation(None, uncertain=True, now=1000 + seconds(event['at'])-start)
                applied.append(dict(at=event['at'], reason='pack_became_uncertain'))
            elif (event['event'] == 'selection_invalidated' and event.get('reason') in (
                    'multiplayer_guest_source_not_confirmed', 'room_preview_changed',
                    'window_identity_changed', 'left_song_context', 'category_changed',
                    'pack_changed_or_uncertain')):
                tracker.invalidate_selection(event['reason'])
                applied.append(dict(at=event['at'], reason=event['reason']))
        reconstruction.append(dict(recorded_scope_at=scope_at, recorded_context_events=applied,
            policy='Recorded Pack/locked selection; historical invalidations AFTER the latest recorded evidence applied using current tracker policy.'))
    playing = dict(state='PLAYING', frame_hash='simulated-playing-transition',
                   playing_preview=copy.deepcopy(original['playing_preview']))
    frames = Mock()
    frames.before.return_value = []
    monitor._recover_fast_selection(Mock(), frames, playing, 1000, catalog)
    for now in (1000, 1001):
        with patch('dance_results.monitor_tracking.utc', return_value=original['started_at']):
            tracker.consume(copy.deepcopy(playing), now=now, sample_id=now)
    locked = copy.deepcopy(tracker.session)
    sid = locked['session_id']
    consistency = ResultConsistency()
    for index, (image, ocr) in enumerate(fresh):
        observation = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=copy.deepcopy(ocr))
        consistency.observe(observation, sid, 1100+index*3, image)
        with patch('dance_results.monitor_tracking.utc', return_value=original['ended_at']):
            monitor._consume_observation(observation, 1200+index*3, image, 1100+index*3, consistency)
    row = monitor.reviews._read(sid)[1]
    body, blockers = automatic_request(monitor.reviews, row)
    item = dict(original_session_id=original['session_id'], scenario=mode, expected_song_id=expected,
        expected_auto_save=auto, tracker_song_id=locked['song_id'], bound_song_id=row['draft']['song_id'],
        binding_method=row['draft'].get('identity_binding_method'), identity_context=locked['identity_context'],
        recorded_pack_at=pack_event['at'], reconstruction=reconstruction,
        temporal_status=row['ocr']['temporal_validation']['status'],
        temporal_reasons=row['ocr']['temporal_validation']['reasons'],
        automatic_blockers=blockers, verified_auto_saved=False,
        fresh_result_fields=[{k:{n:f.get(n) for n in ('value', 'confidence', 'method', 'reason')}
            for k, f in ocr['fields'].items()} for _, ocr in fresh])
    if body:
        store = SubmissionService(LibraryService(database), monitor.reviews)
        store.auto_allowed = lambda session_id: True
        try:
            receipt = store.submit_verified(sid)
            retry = store.submit_verified(sid)
            item.update(verified_auto_saved=True, idempotent_retry=retry['idempotent'],
                stored={k:receipt['record'].get(k) for k in ('record_id', 'song_id', 'difficulty_id',
                    'perfect', 'cool', 'bad', 'miss', 'max_combo', 'game_score', 'grade',
                    'game_level', 'total_notes', 'confirmation_method')})
        except LibraryError as error:
            item['submission_error'] = str(error)
    item['identity_matches_expected'] = item['bound_song_id'] == expected
    item['save_matches_expected'] = item['verified_auto_saved'] == auto
    item['matches_requested_expectation'] = (item['identity_matches_expected'] and item['save_matches_expected']
        and set(blockers) == (set() if auto else {'automatic_uncharted_complete_combo_required'}))
    if not item['matches_requested_expectation']:
        item['evidence_gap'] = 'See exact blockers/submission error; no expectation substituted.'
    print(json.dumps(item, ensure_ascii=False, indent=2), flush=True)
    return item


def main():
    snapshots = [ROOT/'data/library.sqlite3', SOURCE]
    digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    scratch = ROOT/'data/round34_scratch'/('jp-'+stamp)
    scratch.mkdir(parents=True)
    database = scratch/'ISOLATED.sqlite3'
    with closing(readonly(SOURCE)) as source, closing(sqlite3.connect(database)) as target:
        source.backup(target)
    originals = []
    for prefix, expected, auto in SESSIONS:
        folder = next((ROOT/'data/monitor/sessions').glob(prefix+'*'))
        archived = scratch/'originals'/folder.name
        shutil.copytree(folder, archived)
        originals.append((archived, read(archived/'session.json'), expected, auto))
    # Remove only tonight's receipts in the disposable database. Foreign-key
    # dependencies are pruned there too; originals and both snapshots stay RO.
    with closing(sqlite3.connect(database)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        preserve = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='formal_record_preserve'").fetchone()[0]
        # The production immutability trigger is suspended ONLY for fixture
        # preparation in this new disposable copy, then restored before replay.
        db.execute('DROP TRIGGER formal_record_preserve')
        removed = []
        for _, original, _, _ in originals:
            rows = db.execute('SELECT record_id FROM play_records WHERE session_id=?', (original['session_id'],)).fetchall()
            removed.extend(r[0] for r in rows)
            db.execute('DELETE FROM play_records WHERE session_id=?', (original['session_id'],))
            db.execute('DELETE FROM play_sessions WHERE session_id=?', (original['session_id'],))
        db.execute(preserve)
        db.commit()
    with closing(readonly(database)) as db:
        columns, limits, before = projection(db), row_limits(db), audit(db)
    with LibraryDatabase(database) as library:
        migration = library.migration_result
    events = [json.loads(line) for line in (ROOT/'reports/round34-inputs/diagnostics-live-copy.jsonl').read_text(encoding='utf8').splitlines()]
    engine = LocalOCREngine(ROOT/'.ocr-models', low_latency=True)
    catalog = ReadOnlyCatalog(database)
    results = []
    try:
        collisions = collision_report(catalog)
        for archived, original, expected, auto in originals:
            fresh = []
            for index in (0, 1):
                with Image.open(archived/'result-observations'/f'frame-{index}.png') as source:
                    image = source.convert('RGB').resize((1024, 800))
                fresh.append((image, recognize_result(image, engine, 'TC')))
            for control in (False, True):
                results.append(run_case(database, scratch, archived, original, events, catalog,
                    fresh, control, expected, auto))
    finally:
        catalog.close()
    with closing(readonly(database)) as db:
        unchanged = audit(db, columns, limits) == before
        check_integrity(db)
    shas = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots}
    report = dict(scope=__doc__, scratch_database=str(database), removed_existing_record_ids=removed,
        migration=migration, snapshot_sha256_before=digests, snapshot_sha256_after=shas,
        snapshots_unchanged=digests == shas, retained_old_rows_unchanged=unchanged,
        catalog_collisions=collisions, sessions=results)
    report['confirmed_pack_control_passed'] = all(r['matches_requested_expectation'] for r in results if r['scenario'] == 'confirmed_pack_control')
    report['recorded_context_matches_requested'] = all(r['matches_requested_expectation'] for r in results if r['scenario'] == 'recorded_context')
    report['safety_checks_passed'] = unchanged and digests == shas
    atomic_json(OUT/'jp-replay.json', report)
    print('Confirmed-pack control:', report['confirmed_pack_control_passed'],
          'recorded context:', report['recorded_context_matches_requested'], 'safety:', report['safety_checks_passed'])
    return 0 if (report['confirmed_pack_control_passed']
        and report['recorded_context_matches_requested'] and report['safety_checks_passed']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
