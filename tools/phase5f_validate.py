"""Repeatable Phase5F observations. Never writes the production database or PNGs."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import threading
import time
from urllib.request import Request, urlopen

import psutil
from PIL import Image
from dance_results.achievements import progress_for_songs
from dance_results.download_tracking import metadata_snapshot
from dance_results.library_service import LibraryService
from dance_results.monitor_ocr import Analyzer
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.recent_updates import recent_updates
from dance_results.sqlite_safety import audit, check_integrity
from dance_results.submissions import SubmissionService

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/phase5f'
BASE=OUT/'baseline.sqlite3'
IMAGES=ROOT/'tests/專案測試圖片'


def read(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def distribution(values):
    a=sorted(values)
    return dict(n=len(a),mean=round(statistics.mean(a),3),median=round(statistics.median(a),3),p95=round(a[min(len(a)-1,int(.95*len(a)))],3),max=round(max(a),3)) if a else None


def stop_live():
    base='http://127.0.0.1:40510'
    token=re.search(r'name="dance-token" content="([^"]+)',urlopen(base+'/monitor').read().decode()).group(1)
    headers={'Content-Type':'application/json','X-Dance-Token':token}
    status=json.loads(urlopen(Request(base+'/api/monitor/status',headers=headers)).read())
    resources=[json.loads(s) for s in (OUT/'live/resources.jsonl').read_text().splitlines()]
    result=dict(scope='new passive actual window capture of stationary song selection; not new gameplay E2E',monitor=status,
                samples=len(resources),cpu_one_core_percent=distribution([r['cpu_one_core_percent'] for r in resources]),
                cpu_machine_percent=distribution([r['cpu_machine_percent'] for r in resources]),rss_mb=distribution([r['rss_mb'] for r in resources]))
    result['stop_response']=json.loads(urlopen(Request(base+'/api/monitor/stop',data=b'{}',headers=headers)).read())
    atomic_json(OUT/'live-observation.json',result)
    print('Passive live monitoring stopped; GUI remains available in the isolated database.',flush=True)


def bests():
    with closing(read(BASE)) as db:
        db.row_factory=sqlite3.Row;before=audit(db);updates=recent_updates(db)
        progress=progress_for_songs(db,[2726,127]);records={str(i):SubmissionService._record(db,i,include_evidence=False) for i in (7,15,18,19)}
        z=next(p for p in progress[2726] if p['difficulty']=='HARD');m=next(p for p in progress[127] if p['difficulty']=='HARD')
        assert (z['best_ex_score'],z['best_score_record']['record_id'],z['best_lamp'])==(3174,7,'CLEAR')
        assert (m['best_ex_score'],m['best_score_record']['record_id'],m['best_lamp'],m['best_lamp_record_id'])==(2029,18,'AC',19)
        assert records['19']['update_summary']['kind']=='LAMP_UPDATE'
        assert records['15']['update_summary']['kind'] not in ('SCORE_UPDATE','DOUBLE_UPDATE')
        assert not any(r['record_id'] in (15,19) for r in updates['updates'])
        assert len(updates['updates'])<=30
        assert len({(r['song_id'],r['difficulty']) for r in updates['updates']})==len(updates['updates'])
        assert before==audit(db);check_integrity(db)
    with closing(read(BASE)) as db:
        db.row_factory=sqlite3.Row
        assert updates==recent_updates(db)
    atomic_json(OUT/'bests.json',dict(scope='read-only current formal snapshot; no record migration or recomputation',records=records,progress=progress,recent=updates,reopened_recent_equal=True,all_rows_unchanged=True))


def lobby():
    analyzer=Analyzer(LocalOCREngine(ROOT/'.ocr-models',low_latency=True),'TC');rows=[]
    for name in ('多人遊戲時 沒有譜的話會出現NOMAP字樣.PNG','下載條.PNG'):
        path=IMAGES/name;sha=hashlib.sha256(path.read_bytes()).hexdigest();start=time.perf_counter()
        with Image.open(path) as im:observation=analyzer.analyze(im)
        d=observation.get('download_lobby') or {}
        passed=observation['state']=='WAITING_ROOM' and d.get('self_no_map') is True and d.get('slot')==2 and d.get('progress_percent') is None
        rows.append(dict(image=name,sha256=sha,seconds=time.perf_counter()-start,passed=passed,observation=observation))
        print(name,passed,d,flush=True)
    atomic_json(OUT/'lobby-ocr.json',dict(scope='fresh actual model on unchanged original lobby PNGs; no new download or synthetic progress evidence',results=rows))


def benchmark():
    target=OUT/'benchmark.sqlite3'
    if target.exists():raise ValueError('Existing benchmark preserved; choose a fresh output.')
    with closing(read(BASE)) as src,closing(sqlite3.connect(target)) as dst:src.backup(dst)
    process=psutil.Process();samples=[];stop=threading.Event();phase=['model_load'];process.cpu_percent(None)
    def sample():
        while not stop.wait(.25):samples.append(dict(phase=phase[0],cpu_one_core_percent=process.cpu_percent(None),rss_mb=process.memory_info().rss/1024**2))
    worker=threading.Thread(target=sample,daemon=True);worker.start()
    try:
        engine=LocalOCREngine(ROOT/'.ocr-models',low_latency=True);analyzer=Analyzer(engine,'TC')
        truth=json.loads((ROOT/'tests/fixtures/phase4c_ground_truth.json').read_text(encoding='utf8'))['images'];observations={};results=[]
        phase[0]='result_ocr'
        for name,expected in truth.items():
            path=IMAGES/name;sha=hashlib.sha256(path.read_bytes()).hexdigest();assert sha==expected['sha256']
            with Image.open(path) as im:
                start=time.perf_counter();observation=analyzer.analyze(im);elapsed=time.perf_counter()-start
            actual=observation['result_ocr']['fields'];checks={k:actual.get(k,{}).get('value')==v for k,v in expected['fields'].items()}
            observations[name]=observation
            results.append(dict(image=name,sha256=sha,seconds=elapsed,checks=checks,passed=all(checks.values()),observation=observation))
            print('Original result OCR:',name,round(elapsed,3),all(checks.values()),flush=True)
        atomic_json(OUT/'result-ocr.json',dict(scope='actual model, unchanged original PNGs; not live gameplay',results=results))
        selection=Image.open(IMAGES/'相同歌名2.PNG');times=[]
        phase[0]='ocr_without_scan'
        for i in range(8):
            start=time.perf_counter();analyzer.analyze(selection,state='SONG_SELECT',full=i%4==0);times.append(dict(full=i%4==0,seconds=time.perf_counter()-start))
        service=LibraryService(target)
        with service.open() as lib:before=audit(lib.connection)
        scans=[];concurrent=[];phase[0]='scan_and_ocr';scan_start=time.perf_counter()
        for root in service.bootstrap()['roots']:
            meta=metadata_snapshot(root['path']);start=time.perf_counter();service.start_scan(root['root_id'],defer_not_ready=True)
            n=0
            while service.scan_status()['status']=='running':
                begin=time.perf_counter();analyzer.analyze(selection,state='SONG_SELECT',full=n%4==0)
                concurrent.append(dict(full=n%4==0,seconds=time.perf_counter()-begin));n+=1
                time.sleep(.5)
            scans.append(dict(root=root,metadata=dict(milliseconds=meta['milliseconds'],files=len(meta['files']),folders=len(meta['folders']),errors=meta['errors']),seconds=time.perf_counter()-start,result=service.scan_status()))
        scan_elapsed=time.perf_counter()-scan_start
        with service.open() as lib:check_integrity(lib.connection);after=audit(lib.connection)
        phase[0]='replay'
        replay_checks(observations)
        result=dict(scope='original screenshot OCR and whole real SONG read-only scan into isolated SQLite; no game FPS measurement',
                    ocr_without_scan=times,ocr_during_scan=concurrent,scan_seconds=scan_elapsed,scans=scans,
                    before=before,after=after,old_records_unchanged=before['play_records']==after['play_records'],integrity='ok',foreign_keys='ok')
    finally:
        stop.set();worker.join(2)
    result['resources']=samples;result['resource_summary']={p:dict(cpu_one_core_percent=distribution([s['cpu_one_core_percent'] for s in samples if s['phase']==p]),cpu_machine_percent=distribution([s['cpu_one_core_percent']/(psutil.cpu_count() or 1) for s in samples if s['phase']==p]),rss_mb=distribution([s['rss_mb'] for s in samples if s['phase']==p])) for p in sorted({s['phase'] for s in samples})}
    result['latency']={label:{'all':distribution([r['seconds'] for r in rows]),'full':distribution([r['seconds'] for r in rows if r['full']]),'roi':distribution([r['seconds'] for r in rows if not r['full']])} for label,rows in [('without_scan',times),('during_scan',concurrent)]}
    atomic_json(OUT/'benchmark.json',result)
    print('Benchmark and replay completed.',flush=True)


def replay_checks(observations):
    directory=OUT/'replay-negative';tracker=Tracker(directory);i=0
    def consume(o,n=2,image=None):
        nonlocal i
        for _ in range(n):
            i+=1;tracker.consume(copy.deepcopy(o),now=i*.1,image=image,sample_id=i)
    # A transient misclassification is not a new Session. Unknown identity is never filled from a result.
    consume(dict(state='PLAYING',frame_hash='controlled-playing'),n=1)
    assert tracker.session is None
    consume(dict(state='LOBBY',frame_hash='controlled-lobby'))
    consume(dict(state='PLAYING',frame_hash='controlled-playing'))
    sid=tracker.session['session_id'];assert tracker.session['song_id'] is None
    original=Image.open(IMAGES/'結算畫面.PNG');consume(observations['結算畫面.PNG'],image=original)
    saved=(directory/'sessions'/sid/'session.json').read_bytes()
    consume(observations['結算畫面.PNG'],n=10000,image=original)
    assert saved==(directory/'sessions'/sid/'session.json').read_bytes()
    assert len(list((directory/'sessions').iterdir()))==1
    from dance_results.result_review import ResultReviews
    from dance_results.verified_auto import automatic_request
    reviews=ResultReviews(BASE,directory);review=reviews.ingest(tracker.last_session,observations['結算畫面.PNG']['result_ocr'])
    request,issues=automatic_request(reviews,review);assert request is None and issues
    atomic_json(OUT/'replay.json',dict(scope='real original result OCR plus explicit simulated PLAYING transition; no original matching selection evidence',one_bad_frame_no_session=True,ten_thousand_result_observations_one_session=True,original_saved_result_unchanged=True,session_song_id=None,automatic_submission=False,automatic_issues=issues,source_image='結算畫面.PNG'))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--stop-live',action='store_true');p.add_argument('--lobby',action='store_true');a=p.parse_args()
    if a.stop_live:stop_live()
    elif a.lobby:lobby()
    else:bests();benchmark()
