"""Actual-model ROI regression; never starts live capture or submits a result."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from PIL import Image
from dance_results.ocr_engine import LocalOCREngine
from dance_results.monitor_ocr import Analyzer,apply_selection
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.sqlite_safety import audit,check_integrity
from tools.selection_acceptance import selection_readiness

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f1-bpm-20261108';FAILED=ROOT/'reports/phase5f/live-accept-20261108-01'

def read(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    isolated=OUT/'ISOLATED.sqlite3'
    if isolated.exists():raise ValueError('Existing evidence preserved; use a new experiment directory.')
    with closing(read(ROOT/'data/library.sqlite3'))as src,closing(sqlite3.connect(isolated))as dst:src.backup(dst)
    before=json.loads((OUT/'before.json').read_text(encoding='utf8'))
    source=next((FAILED/'selection-evidence').glob('*.png'));engine=LocalOCREngine(ROOT/'.ocr-models',low_latency=True);analyzer=Analyzer(engine)
    with Image.open(source)as image:
        start=time.perf_counter();after=analyzer.analyze(image,state='SONG_SELECT',full=False);elapsed=time.perf_counter()-start
        assert after['roi_mode'] is True and not after.get('roi_fallback_reason')
        bpm=after['selection']['fields']['bpm'];assert bpm['value']==132 and bpm['confidence']>=.95
        catalog=ReadOnlyCatalog(isolated)
        try:
            tracker=Tracker(OUT/'static-matching-only');after_match=apply_selection(copy.deepcopy(after),image,tracker,catalog,source_mode='official')
            old=json.loads((OUT/'before-roi.json').read_text(encoding='utf8'))
            old_match=apply_selection(copy.deepcopy(old),image,tracker,catalog,source_mode='official')
            readiness_before=selection_readiness(old_match);readiness_after=selection_readiness(after_match)
            assert old_match['matching']['confirmed_song_id']==after_match['matching']['confirmed_song_id']==2730
            assert not readiness_before['complete_selection_observation'] and readiness_after['complete_selection_observation']
        finally:catalog.close()
    truth=json.loads((ROOT/'tests/fixtures/phase4a_ground_truth.json').read_text(encoding='utf8'))['images'];regression=[]
    for name,expected in truth.items():
        if expected.get('screen_type')!='song_select':continue
        path=ROOT/'tests/專案測試圖片'/name;original=sha(path);assert original==expected['sha256']
        for full in (True,False):
            with Image.open(path)as image:o=analyzer.analyze(image,state='SONG_SELECT',full=full)
            actual=o['selection']['fields']['bpm']
            regression.append(dict(image=name,sha256=original,requested_full=full,actual_roi=o['roi_mode'],fallback=o.get('roi_fallback_reason'),expected_bpm=expected['fields']['bpm'],bpm=actual,correct=actual['value']==expected['fields']['bpm']))
        assert sha(path)==original
    assert all(r['correct']for r in regression)
    for file in (ROOT/'data/library.sqlite3',isolated):
        with closing(read(file))as db:assert audit(db)==before['tables'];check_integrity(db);assert db.execute('PRAGMA user_version').fetchone()[0]==10
    assert all(sha(Path(f))==h for f,h in before['files'].items())
    result=dict(scope='STATIC actual-model regression on unchanged saved PNG; same Analyzer SONG_SELECT/full=False path used by Monitor; not live gameplay or two fresh result frames',
                image=str(source),image_sha256=sha(source),model=engine.metadata(),actual_roi=True,full_fallback=False,seconds=elapsed,
                before=old,after=after,matching_before=old_match['matching'],matching_after=after_match['matching'],readiness_before=readiness_before,readiness_after=readiness_after,
                legacy_bpm_regression=regression,legacy_correct=sum(r['correct']for r in regression),legacy_total=len(regression),
                formal_and_isolated_tables_unchanged=True,old_evidence_files_unchanged=True,schema=10,new_sessions=0,new_reviews=0,new_records=0)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(actual_roi=True,full_fallback=False,bpm=bpm['value'],confidence=bpm['confidence'],raw_pair=after['selection']['raw_fields']['bpm'],legacy_correct=result['legacy_correct'],legacy_total=result['legacy_total'],old_evidence_unchanged=True),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
