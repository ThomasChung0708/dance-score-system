"""Real original PNG regression and read-only formal-data audit, no old reviews processed."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from dance_results.ocr_engine import LocalOCREngine
from dance_results.monitor_ocr import Analyzer,apply_selection
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.sqlite_safety import audit,check_integrity
from dance_results.ocr_temporal import issues
from dance_results.scoring import calculate
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f1';LIVE=ROOT/'reports/phase5f/live-gameplay-20261027-01'

def read(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    db=OUT/'ISOLATED.sqlite3'
    if db.exists():raise ValueError('Existing experiment preserved; choose a fresh output directory.')
    OUT.mkdir(exist_ok=True)
    with closing(read(ROOT/'data/library.sqlite3')) as src,closing(sqlite3.connect(db)) as dst:src.backup(dst);baseline=audit(dst)
    original_files=[p for base in (ROOT/'data/monitor',LIVE,ROOT/'tests/專案測試圖片') for p in base.rglob('*') if p.is_file()]
    original_hashes={str(p):sha(p)for p in original_files}
    (OUT/'before.json').write_text(json.dumps(dict(tables=baseline,files=original_hashes),ensure_ascii=False,indent=2),encoding='utf8')
    old=json.loads((LIVE/'monitor/sessions/66f5e80e-c894-4103-9994-0778f785ebf9/result-review.json').read_text(encoding='utf8'))
    original_selection=next((LIVE/'selection-evidence').glob('*.png'));result=LIVE/'monitor/sessions/66f5e80e-c894-4103-9994-0778f785ebf9/result.png'
    engine=LocalOCREngine(ROOT/'.ocr-models',low_latency=True);analyzer=Analyzer(engine)
    selection=analyzer.analyze(Image.open(original_selection));result_observation=analyzer.analyze(Image.open(result));ocr=result_observation['result_ocr']
    catalog=ReadOnlyCatalog(db);tracker=Tracker(OUT/'replay-monitor')
    apply_selection(selection,Image.open(original_selection),tracker,catalog,source_mode=old['locked_session']['selection']['source_type'])
    assert selection['matching']['confirmed_song_id']==2730
    # Explicit static state-machine replay; these are NOT independent real frames.
    tracker.consume(copy.deepcopy(selection),sample_id=1);tracker.consume(copy.deepcopy(selection),sample_id=2)
    tracker.consume(dict(state='PLAYING',frame_hash='CONTROLLED_PLAYING_STEP'),sample_id=3)
    tracker.consume(dict(state='PLAYING',frame_hash='CONTROLLED_PLAYING_STEP'),sample_id=4)
    assert tracker.session['song_id']==2730
    assert ocr['fields']['miss']['value']==1 and ocr['fields']['miss']['confidence']>=.95
    assert ocr['fields']['game_level']['value']==10 and ocr['fields']['game_level']['confidence']>=.95
    assert selection['fields']['bpm']['value']==132 and selection['fields']['bpm']['confidence']>=.95
    assert issues(ocr,old['locked_session'],result.parent)==['automatic_temporal_confirmation_required']
    values={k:v['value']for k,v in ocr['fields'].items()}
    values.update(difficulty=selection['fields']['difficulty']['value'],total_notes=selection['fields']['total_notes']['value'])
    preview=calculate(values,notes_verified=True,identity_confirmed=True)
    assert (preview['ex_score'],preview['max_score'],preview['display'],preview['lamp_preview'])==(1865,1870,'MAX -5','CLEAR')
    catalog.close()
    truth=json.loads((ROOT/'tests/fixtures/phase4c_ground_truth.json').read_text(encoding='utf8'))['images'];regression=[]
    for name,expected in truth.items():
        start=time.perf_counter();obs=analyzer.analyze(Image.open(ROOT/'tests/專案測試圖片'/name));fields=obs['result_ocr']['fields']
        checks={k:fields[k]['value']==v for k,v in expected['fields'].items()}
        regression.append(dict(image=name,checks=checks,seconds=time.perf_counter()-start,observation=obs))
        print(name,all(checks.values()),flush=True)
    for name,expected in [('相同歌名2.PNG',(17,821,96)),('相同歌名3.PNG',(20,929,95))]:
        obs=analyzer.analyze(Image.open(ROOT/'tests/專案測試圖片'/name));f=obs['selection']['fields']
        assert tuple(f[k]['value'] for k in ('game_level','total_notes','duration_seconds'))==expected
    with closing(read(ROOT/'data/library.sqlite3')) as formal,closing(read(db)) as isolated:
        assert audit(formal)==baseline and audit(isolated)==baseline;check_integrity(formal);check_integrity(isolated)
    assert all(sha(Path(path))==digest for path,digest in original_hashes.items())
    result=dict(scope='unchanged original screenshots; isolated static state replay only; no new live multi-frame or gameplay test',
        before=dict(bpm=old['locked_session']['selection']['ocr_fields']['bpm'],miss=old['ocr']['fields']['miss'],game_level=old['ocr']['fields']['game_level']),
        same_image_raw=dict(bpm=selection['raw_ocr_fields']['bpm'],miss=ocr['raw_fields']['miss'],game_level=ocr['raw_fields']['game_level']),score_preview=preview,
        after=dict(bpm=selection['fields']['bpm'],miss=ocr['fields']['miss'],game_level=ocr['fields']['game_level']),
        selection=selection,result=result_observation,song_id_static_lock=2730,single_image_auto_blocked=True,
        regression=regression,correct=sum(sum(r['checks'].values())for r in regression),total=sum(len(r['checks'])for r in regression),
        production_unchanged=True,isolated_unchanged=True,all_original_files_unchanged=True,original_file_count=len(original_hashes),schema=10)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('Original source evidence and both databases unchanged; static identity lock verified; single-frame automatic submission blocked.',flush=True)

if __name__=='__main__':main()
