"""Counterfactual kept-selection replay; fresh original result OCR, scratch writes.

MAGIC's lost selection pixels/field confidences are unavailable. Its pack/title
are reconstructed from the supplied timeline; LV31 comes from the recorded
PLAYING preview, with confidence zero (no invented OCR confidence). The narrow
uncharted rule instead requires reliable LV on both original result frames.
Brave uses its original pre-play selection. Neither original review is edited.
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
from unittest.mock import Mock, patch

from PIL import Image

from dance_results.database import LibraryDatabase
from dance_results.library_service import LibraryService, LibraryError
from dance_results.monitor import Monitor
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.monitor_ocr import image_hash
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_temporal import ResultConsistency
from dance_results.result_ocr import recognize_result
from dance_results.result_review import ResultReviews
from dance_results.sqlite_safety import audit, projection, row_limits, check_integrity
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'data/library.sqlite3'
SESSIONS = ('a2f67ad5-694b-45b3-bb75-558fe93b5909', '0216118f-4ed3-4bf6-8d17-ec6a93878a2d')


def readonly(path):
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-root', type=Path, default=ROOT/'data/round32_scratch')
    args = parser.parse_args()
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    scratch = args.scratch_root/('session8-' + stamp)
    scratch.mkdir(parents=True)
    database = scratch/'ISOLATED.sqlite3'
    with closing(readonly(SOURCE)) as source, closing(sqlite3.connect(database)) as target:
        source.backup(target)
        old_columns, limits, before = projection(source), row_limits(source), audit(source)
    with LibraryDatabase(database) as library:
        migration = library.migration_result
    monitor_dir = scratch/'monitor'
    engine = LocalOCREngine(ROOT/'.ocr-models', low_latency=True)
    catalog = ReadOnlyCatalog(database)
    results = []
    try:
        for index, original_id in enumerate(SESSIONS):
            archived = scratch/'originals'/original_id
            shutil.copytree(ROOT/'data/monitor/sessions'/original_id, archived)
            original = read(archived/'session.json')
            tracker = Tracker(monitor_dir)
            monitor = Monitor(database, monitor_dir)
            monitor.tracker = tracker
            selection = copy.deepcopy(original.get('selection'))
            if selection is None:
                pack = next(p for p in catalog.packs if p['pack_id'] == 14)
                pack.update(status='confirmed', source='reconstructed_2026-11-15_17:21:55_timeline')
                fields = dict(
                    title=dict(value='MAGIC & LOVE', confidence=.96007, reason=None,
                        method='timeline_visible_title_reconstruction'),
                    difficulty=dict(value=None, confidence=0, reason=None, method='unknown'),
                    game_level=dict(value=original['playing_preview']['game_level'], confidence=0,
                        reason=None, method='recorded_playing_preview_no_selection_confidence'))
                selection = dict(fields=fields, ocr_fields=copy.deepcopy(fields), pack=pack,
                    source_type='custom', frame_hash='timeline-reconstructed-not-original-selection-pixels')
                selection['matching'] = catalog.match_identity(fields, 14, 'custom')
            tracker.current_pack = copy.deepcopy(selection['pack'])
            observed = dict(state='SONG_SELECT', **selection)
            tracker.update_selection(observed, 1)
            tracker.update_selection(observed, 2)
            playing = dict(state='PLAYING', frame_hash='explicit-reconstructed-playing-transition',
                playing_preview=original['playing_preview'])
            frames = Mock()
            frames.before.return_value = []
            monitor._recover_fast_selection(Mock(), frames, playing, 25, catalog)
            for sample in (25, 26):
                with patch('dance_results.monitor_tracking.utc', return_value=original['started_at']):
                    tracker.consume(copy.deepcopy(playing), now=sample, sample_id=sample)
            bound = copy.deepcopy(tracker.session)
            sid = bound['session_id']
            consistency = ResultConsistency()
            fresh = []
            for frame_index in (0, 1):
                path = archived/'result-observations'/f'frame-{frame_index}.png'
                with Image.open(path) as source_image:
                    image = source_image.convert('RGB').resize((1024, 800))
                ocr = recognize_result(image, engine, 'TC')
                fresh.append({k: {n: f.get(n) for n in ('value', 'confidence', 'method', 'reason')}
                    for k, f in ocr['fields'].items()})
                observation = dict(state='RESULT', frame_hash=image_hash(image), result_ocr=ocr)
                consistency.observe(observation, sid, 100 + frame_index * 3, image)
                with patch('dance_results.monitor_tracking.utc', return_value=original['ended_at']):
                    monitor._consume_observation(observation, 150 + frame_index * 3,
                        image, 100 + frame_index * 3, consistency)
            row = monitor.reviews._read(sid)[1]
            _, blockers = automatic_request(monitor.reviews, row)
            item = dict(original_session_id=original_id, replay_session_id=sid,
                bound_song=bound['song_id'], selection_survived=bound['song_id'] == (457 if index == 0 else 645),
                reconstruction=__doc__ if index == 0 else 'Original locked pre-play selection; simulated PLAYING/clock.',
                fresh_result_fields=fresh, temporal_status=row['ocr']['temporal_validation']['status'],
                temporal_reasons=row['ocr']['temporal_validation']['reasons'],
                automatic_blockers=blockers, verified_auto_saved=False)
            if not blockers:
                store = SubmissionService(LibraryService(database), monitor.reviews)
                store.auto_allowed = lambda session_id: True
                try:
                    receipt = store.submit_verified(sid)
                    record = receipt['record']
                    retry = store.submit_verified(sid)
                    item.update(verified_auto_saved=True, idempotent_retry=retry['idempotent'],
                        stored={k: record.get(k) for k in ('record_id', 'song_id', 'difficulty_id',
                            'difficulty', 'perfect', 'cool', 'bad', 'miss', 'max_combo',
                            'game_score', 'grade', 'game_level', 'total_notes', 'ex_score',
                            'max_score', 'confirmation_method')})
                except LibraryError as error:
                    item['submission_error'] = str(error)
            results.append(item)
            print(json.dumps(item, ensure_ascii=False, indent=2), flush=True)
    finally:
        catalog.close()
    with closing(readonly(database)) as connection:
        unchanged = audit(connection, old_columns, limits) == before
        check_integrity(connection)
        chart_count = connection.execute('SELECT COUNT(*) FROM game_difficulties WHERE song_id=457').fetchone()[0]
    snapshot_unchanged = hashlib.sha256(SOURCE.read_bytes()).hexdigest() == digest
    report = dict(scope=__doc__, scratch_database=str(database), migration=migration,
        snapshot_sha256=digest, snapshot_unchanged=snapshot_unchanged,
        old_rows_unchanged=unchanged, song457_chart_rows=chart_count, sessions=results)
    expected = ((457, 1375, 6, 1381, 97601, 31), (645, 1186, 2, 1188, 83994, 23))
    checks = []
    for item, (song, perfect, cool, combo, score, level) in zip(results, expected):
        record = item.get('stored', {})
        checks.append(item['verified_auto_saved'] and record.get('song_id') == song
            and [record.get(k) for k in ('perfect', 'cool', 'bad', 'miss')] == [perfect, cool, 0, 0]
            and record.get('max_combo') == combo and record.get('game_score') == score
            and record.get('grade') == 'A++' and record.get('game_level') == level)
    report['passed'] = all(checks) and unchanged and snapshot_unchanged and chart_count == 0
    atomic_json(ROOT/'reports/round32/session8-replay.json', report)
    print('Session 8 replay passed:', report['passed'], flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
