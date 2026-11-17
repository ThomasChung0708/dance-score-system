"""Recorded pre-play evidence replay, explicitly using a simulated PLAYING step."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch
import argparse
from datetime import datetime, timezone
from PIL import Image
from dance_results.library_service import LibraryService,LibraryError
from dance_results.monitor_ocr import Analyzer,apply_selection
from dance_results.monitor_tracking import Tracker,ManualReferences,atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.result_review import ResultReviews
from dance_results.sqlite_safety import audit,projection,row_limits,check_integrity
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import automatic_request

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f/recorded-replay-v2';BASE=OUT.parent/'baseline.sqlite3';DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor'


def read(path):
    connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    connection.execute('PRAGMA query_only=ON')
    return connection


def main():
    global OUT, DB, MON
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    OUT = args.output.resolve()
    if not OUT.is_relative_to((ROOT/'reports/phase5f').resolve()):
        raise ValueError('Replay output must remain under reports/phase5f.')
    scratch = ROOT/'data/round35_scratch'/('phase5f-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    scratch.mkdir(parents=True)
    DB = scratch/'ISOLATED.sqlite3'
    MON = scratch/'monitor'
    if DB.exists():raise ValueError('Existing replay is preserved.')
    OUT.mkdir(parents=True,exist_ok=True)
    with closing(read(BASE)) as src,closing(sqlite3.connect(DB)) as dst:src.backup(dst)
    from dance_results.database import LibraryDatabase
    with LibraryDatabase(DB):
        pass
    MON.mkdir(exist_ok=True);refs=ROOT/'data/monitor/manual-references.json'
    if refs.exists():shutil.copyfile(refs,MON/refs.name)
    analyzer=Analyzer(LocalOCREngine(ROOT/'.ocr-models',low_latency=True));cases=[]
    with closing(read(BASE)) as db:rows=db.execute('SELECT record_id,session_id FROM play_records WHERE record_id IN (5,6)').fetchall()
    catalog=ReadOnlyCatalog(DB);references=ManualReferences(MON,DB)
    try:
        for rid,original_sid in rows:
            original_dir=ROOT/'data/monitor/sessions'/original_sid;original=json.loads((original_dir/'session.json').read_text(encoding='utf8'))
            selection=original['selection'];picture=ROOT/'data/monitor/confirmations'/(selection['observation_id']+'.png')
            assert picture.exists() and selection.get('manual_confirmation')
            pre_image=Image.open(picture);result_image=Image.open(original_dir/'result.png')
            pre=analyzer.analyze(pre_image);result=analyzer.analyze(result_image)
            tracker=Tracker(MON);counter=0
            # Preserve only the original pre-play explicit Pack confirmation, never a post-result guess.
            tracker.current_pack=copy.deepcopy(selection.get('pack'))
            def consume(o,stamp,image=None):
                nonlocal counter
                for _ in range(2):
                    counter+=1
                    with patch('dance_results.monitor_tracking.utc',return_value=stamp):tracker.consume(copy.deepcopy(o),now=counter,image=image,sample_id=counter)
            apply_selection(pre,pre_image,tracker,catalog,references,source_mode=selection['source_type'])
            consume(pre,original['started_at'])
            fields=pre.get('fields') or {};preview={k:fields.get(k,{}).get('value') for k in ('title','game_level')}
            consume(dict(state='PLAYING',frame_hash='explicit-simulated-playing-step',playing_preview=preview),original['started_at'])
            locked=copy.deepcopy(tracker.session)
            consume(result,original['ended_at'],result_image)
            reviews=ResultReviews(DB,MON);row=reviews.ingest(tracker.last_session,result['result_ocr']);body,issues=automatic_request(reviews,row)
            case=dict(original_record_id=rid,original_session_id=original_sid,session_id=locked['session_id'],
                source=selection['source_type'],selection_image=str(picture),selection_sha256=hashlib.sha256(picture.read_bytes()).hexdigest(),
                result_image=str(original_dir/'result.png'),preplay_evidence_source='original explicit pre-play confirmation; new model OCR values are not replaced',
                selection=pre,locked_song_id=locked['song_id'],expected_preplay_song_id=original['song_id'],result=result,
                automatic_issues=issues,automatic_saved=False,simulated_playing_transition=True,new_real_gameplay=False)
            if body:
                store=SubmissionService(LibraryService(DB),reviews);store.auto_allowed=lambda sid:True
                try:
                    receipt=store.submit_verified(locked['session_id']);retry=store.submit_verified(locked['session_id'])
                    assert retry['idempotent'] and receipt['record']['record_id']==retry['record']['record_id']
                    assert receipt['record']['played_at']==original['ended_at']
                    case.update(automatic_saved=True,receipt=receipt,idempotent_retry=True,played_at_matches_original_result_time=True)
                except LibraryError as error:case['submission_pending_reason']=str(error)
            cases.append(case);print('Recorded evidence replay:',rid,'locked=',locked['song_id'],'saved=',case['automatic_saved'],'issues=',issues,flush=True)
    finally:catalog.close()
    with closing(read(BASE)) as baseline,closing(read(DB)) as db:
        unchanged=audit(db,projection(baseline),row_limits(baseline))==audit(baseline)
        assert unchanged;check_integrity(db)
    atomic_json(OUT/'results.json',dict(scope='original selection and result PNG model OCR, original pre-play context, explicitly simulated PLAYING and recorded clock; isolated writes only',scratch_database=str(DB),old_rows_unchanged=unchanged,cases=cases))


if __name__=='__main__':main()
