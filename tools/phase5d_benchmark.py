"""Read the real SONG tree; scan writes go only to an isolated SQLite copy."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
import psutil
from dance_results.download_tracking import metadata_snapshot
from dance_results.library_service import LibraryService
from dance_results.sqlite_safety import audit,check_integrity

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5d'


def main():
    target=OUT/'benchmark.sqlite3'
    if target.exists():raise ValueError('Existing benchmark preserved; choose a new output.')
    with closing(sqlite3.connect((OUT/'baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dest:src.backup(dest)
    process=psutil.Process();samples=[];stop=threading.Event();process.cpu_percent(None)
    def sample():
        while not stop.wait(.1):samples.append(dict(cpu_one_core_percent=process.cpu_percent(None),rss_mb=process.memory_info().rss/1024**2))
    t=threading.Thread(target=sample,daemon=True);t.start()
    service=LibraryService(target);results=[]
    with service.open() as lib:before=audit(lib.connection)
    started=time.perf_counter()
    for root in service.bootstrap()['roots']:
        snap=metadata_snapshot(root['path'])
        snapshots=[dict(milliseconds=snap['milliseconds'],files=len(snap['files']),folders=len(snap['folders']),errors=snap['errors'],content_hashes_computed=snap['content_hashes_computed'])]
        snap=metadata_snapshot(root['path']);snapshots.append(dict(milliseconds=snap['milliseconds'],files=len(snap['files']),errors=snap['errors'],content_hashes_computed=0))
        scan_start=time.perf_counter();service.start_scan(root['root_id'],defer_not_ready=True)
        while service.scan_status()['status']=='running':time.sleep(.1)
        results.append(dict(root=root,metadata_snapshots=snapshots,scan_wall_seconds=round(time.perf_counter()-scan_start,3),scan=service.scan_status()))
    elapsed=time.perf_counter()-started;stop.set();t.join()
    with service.open() as lib:check_integrity(lib.connection);after=audit(lib.connection)
    result=dict(scope='real SONG read-only, isolated SQLite writes; no active game/FPS measurement; no OCR model in this process',
        total_seconds=round(elapsed,3),logical_cpus=psutil.cpu_count(),samples=samples,
        peak_rss_mb=round(max(s['rss_mb'] for s in samples),2),
        peak_cpu_one_core_percent=max(s['cpu_one_core_percent'] for s in samples),
        mean_cpu_one_core_percent=round(sum(s['cpu_one_core_percent'] for s in samples)/len(samples),2),results=results,
        existing_play_records_unchanged=before['play_records']==after['play_records'],before=before,after=after)
    (OUT/'benchmark.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('samples','before','after','results')},ensure_ascii=False))

if __name__=='__main__':main()
