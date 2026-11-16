"""Fresh result OCR and real decision path, with counterfactual category evidence.

No selection pixels were retained on session 9. Recorded selection OCR fields
and diagnostic visible titles are replayed unchanged; category all is explicitly
simulated, never claimed to be a fresh OCR observation. Only scratch copies write.
"""
import argparse
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch

from PIL import Image

from dance_results.database import LibraryDatabase
from dance_results.library_service import LibraryService, LibraryError
from dance_results.monitor import Monitor
from dance_results.monitor_ocr import apply_selection, image_hash
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_temporal import ResultConsistency
from dance_results.pack_selection import infer_selected_pack
from dance_results.result_ocr import recognize_result
from dance_results.sqlite_safety import audit, projection, row_limits, check_integrity
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'data/library.sqlite3'
SESSIONS = (
    ('f3fd6598-7a40-4a6a-9cd1-5ff93f6d412a', 4350),
    ('bcdf2f45-3722-4001-94d9-66b8d9d4138d', 4357),
    ('d060f0c9-547f-47a6-8008-abc9c571196e', 4341),
    ('45f560a5-66a2-4155-a6c1-b981ba64fcb1', 656),
    ('bdb19217-6896-47c4-8255-aeef5b406bfb', 632),
    ('3cc49140-d96b-445d-9b4f-bf8e07eb0dcf', 658),
)


def readonly(path):
    connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def title_selection(event):
    evidence = event['pack']['evidence']
    tokens = {m['title']: dict(text=m['title'], confidence=m['confidence'])
              for m in evidence['matched_titles']}
    return dict(song_title_tokens=list(tokens.values()), visible_packs=[],
        pack_list_visible=False, pack_row_hint=None, song_page=evidence.get('page'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-root', type=Path, default=ROOT/'data/round33_scratch')
    parser.add_argument('--diagnostics', type=Path,
        default=ROOT/'reports/round33-inputs/realplay9/diagnostics_20261116_session9.jsonl')
    args = parser.parse_args()
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    scratch = args.scratch_root/('category-'+stamp)
    scratch.mkdir(parents=True)
    database = scratch/'ISOLATED.sqlite3'
    with closing(readonly(SOURCE)) as source, closing(sqlite3.connect(database)) as target:
        source.backup(target)
        old_columns, limits, before = projection(source), row_limits(source), audit(source)
    with LibraryDatabase(database) as library:
        migration = library.migration_result
    diagnostics = args.diagnostics
    events = [json.loads(line) for line in diagnostics.read_text(encoding='utf8').splitlines()]
    events = [e for e in events if e.get('event') == 'pack_observed' and e.get('pack')]
    engine = LocalOCREngine(ROOT/'.ocr-models', low_latency=True)
    catalog = ReadOnlyCatalog(database)
    results = []
    visible_checks = []
    try:
        for event in events:
            selected = infer_selected_pack(title_selection(event), catalog)
            visible_checks.append(dict(at=event['at'], titles=[t['text'] for t in selected['song_title_tokens']],
                selected_pack_id=selected['selected_pack_id'], evidence=selected['selected_pack_evidence']))
        for original_id, expected in SESSIONS:
            archived = scratch/'originals'/original_id
            shutil.copytree(ROOT/'data/monitor/sessions'/original_id, archived)
            original = read(archived/'session.json')
            applicable = [e for e in events if e['at'] <= original['started_at']]
            event = applicable[-1] if applicable else events[0]
            custom = expected in (656, 632, 658)
            if custom:
                event = dict(pack=original['selection']['pack'])
            # OCR is re-run once per original frame; independent scenario state
            # then traverses the same temporal/monitor/submission path twice.
            fresh = []
            for frame_index in (0, 1):
                with Image.open(archived/'result-observations'/f'frame-{frame_index}.png') as source_image:
                    image = source_image.convert('RGB').resize((1024, 800))
                fresh.append((image, recognize_result(image, engine, 'TC')))
            for category in ((None, 'jinyue') if custom else (None, 'all')):
                mode = category or 'unknown'
                monitor_dir = scratch/mode/'monitor'
                monitor = Monitor(database, monitor_dir)
                tracker = Tracker(monitor_dir)
                monitor.tracker = tracker
                selected = title_selection(event)
                selected.update(fields=copy.deepcopy(original['selection'].get('ocr_fields', original['selection']['fields'])),
                    category=category, category_confidence=.95 if category else 0,
                    category_method='explicit_counterfactual_no_selection_pixels')
                for sample in (1, 2, 3):
                    observation = dict(state='SONG_SELECT', selection=copy.deepcopy(selected),
                        frame_hash='recorded-selection-fields-not-original-pixels')
                    apply_selection(observation, Image.new('RGB', (1024, 800)), tracker, catalog)
                    tracker.update_selection(observation, sample)
                source_before_play = tracker.current_source
                pack_before_play = copy.deepcopy(tracker.current_pack)
                playing = dict(state='PLAYING', frame_hash='explicit-simulated-playing-transition',
                    playing_preview=copy.deepcopy(original['playing_preview']))
                for sample in (4, 5):
                    with patch('dance_results.monitor_tracking.utc', return_value=original['started_at']):
                        tracker.consume(copy.deepcopy(playing), now=sample, sample_id=sample)
                bound = copy.deepcopy(tracker.session)
                sid = bound['session_id']
                consistency = ResultConsistency()
                for frame_index, (image, ocr) in enumerate(fresh):
                    observation = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=copy.deepcopy(ocr))
                    consistency.observe(observation, sid, 100+frame_index*3, image)
                    with patch('dance_results.monitor_tracking.utc', return_value=original['ended_at']):
                        monitor._consume_observation(observation, 150+frame_index*3,
                            image, 100+frame_index*3, consistency)
                row = monitor.reviews._read(sid)[1]
                _, blockers = automatic_request(monitor.reviews, row)
                detail = monitor.reviews.detail(sid)
                candidates = catalog.match_identity(selected['fields'])['candidates']
                official_check = catalog.match_official(selected['fields'], source_confirmed=True)
                item = dict(original_session_id=original_id, scenario=mode, expected_song_id=expected,
                    replay_session_id=sid, tracker_source=source_before_play,
                    inferred_pack=pack_before_play, bound_song_id=bound['song_id'],
                    selection_source=(bound.get('selection') or {}).get('source_type'),
                    draft_song_id=row['draft']['song_id'], candidate_scope=detail['candidate_source_type'],
                    title_candidate_ids=[c['song_id'] for c in candidates],
                    official_chart_check=official_check,
                    temporal_status=row['ocr']['temporal_validation']['status'],
                    temporal_reasons=row['ocr']['temporal_validation']['reasons'],
                    automatic_blockers=blockers, verified_auto_saved=False,
                    fresh_result_fields=[{k:{n:f.get(n) for n in ('value', 'confidence', 'method', 'reason')}
                        for k, f in ocr['fields'].items()} for _, ocr in fresh],
                    reconstruction=__doc__)
                if not blockers:
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
                expected_pending = expected in (656, 632)
                expected_blockers = {'automatic_uncharted_complete_combo_required'} if expected_pending else set()
                item['passed'] = (item['temporal_status'] == 'confirmed'
                    and row['draft']['song_id'] == expected
                    and (pack_before_play is not None if custom else pack_before_play is None)
                    and set(blockers) == expected_blockers
                    and item['verified_auto_saved'] is not expected_pending)
                if item['verified_auto_saved']:
                    item['warnings'] = receipt['record']['warnings']
                    item['notes_source'] = receipt['record']['notes_source']
                    item['passed'] = item['passed'] and receipt['record']['song_id'] == expected
                    if expected == 4357:
                        changes = [w for w in item['warnings'] if w['code'] == 'CHART_NOTES_CHANGED']
                        item['passed'] = (item['passed'] and item['stored']['total_notes'] == 1796
                            and item['notes_source'] == 'result_screen_judged_sum'
                            and len(changes) == 1 and changes[0]['catalog_notes'] == 1797
                            and changes[0]['observed_notes'] == 1796)
                results.append(item)
                print(json.dumps(item, ensure_ascii=False, indent=2), flush=True)
    finally:
        catalog.close()
    with closing(readonly(database)) as connection:
        unchanged = audit(connection, old_columns, limits) == before
        check_integrity(connection)
    snapshot_unchanged = hashlib.sha256(SOURCE.read_bytes()).hexdigest() == digest
    report = dict(scope=__doc__, scratch_database=str(database), migration=migration,
        snapshot_sha256=digest, snapshot_unchanged=snapshot_unchanged,
        old_rows_unchanged=unchanged, diagnostic_visible_lists=visible_checks, sessions=results)
    report['passed'] = (all(r['passed'] for r in results) and unchanged and snapshot_unchanged
                        and all(r['selected_pack_id'] is None for r in visible_checks))
    atomic_json(ROOT/'reports/round33/category-replay.json', report)
    print('Round 3.3 category replay passed:', report['passed'], flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
