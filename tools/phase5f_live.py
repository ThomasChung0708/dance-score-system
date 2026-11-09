"""Passive real-window validation, restricted to a separate SQLite/monitor tree."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import psutil
from dance_results.gui import create_server
from dance_results.sqlite_safety import audit,check_integrity
from dance_results.monitor_ocr import image_hash
from tools.selection_acceptance import selection_readiness

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f/live';DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor'


def prepare(fresh_production=False):
    if DB.exists():raise ValueError('Existing live experiment preserved.')
    OUT.mkdir(parents=True,exist_ok=True)
    source=ROOT/'data/library.sqlite3' if fresh_production else OUT.parent/'baseline.sqlite3'
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DB)) as dst:
        src.backup(dst);check_integrity(dst);baseline=audit(dst)
    MON.mkdir(exist_ok=True)
    refs=ROOT/'data/monitor/manual-references.json';provenance=None
    if refs.exists():
        shutil.copyfile(refs,MON/refs.name)
        provenance=dict(path=str(refs),sha256=hashlib.sha256(refs.read_bytes()).hexdigest(),copied_without_editing=True)
    (OUT/'manifest.json').write_text(json.dumps(dict(database=str(DB),monitor_directory=str(MON),scope='new passive live-window observation; isolated writes only',copied_prior_manual_references=provenance,baseline_source=str(source),baseline=baseline),ensure_ascii=False,indent=2),encoding='utf8')
    print('Isolated live database ready; no old pending reviews copied.')


def serve(source,mode):
    assert DB.resolve()!=(ROOT/'data/library.sqlite3').resolve()
    import dance_results.monitor as monitor_module
    original_apply=monitor_module.apply_selection
    def observed_selection(*args,**kwargs):
        result=original_apply(*args,**kwargs)
        if result.get('state')=='SONG_SELECT':
            entry=dict(at=datetime.now(timezone.utc).isoformat(),frame_hash=result.get('frame_hash'),source=result.get('source_type'),
                fields={k:dict(value=v.get('value'),confidence=v.get('confidence'),reason=v.get('reason')) for k,v in result.get('fields',{}).items()},
                matching=result.get('matching'))
            entry['preplay_readiness']=selection_readiness(result)
            with (OUT/'observations.jsonl').open('a',encoding='utf8') as log:log.write(json.dumps(entry,ensure_ascii=False)+'\n')
        return result
    monitor_module.apply_selection=observed_selection
    server=create_server(DB,40510,MON)
    if mode=='VERIFIED_AUTO':server.automatic.configure(dict(mode=mode,acknowledge=True))
    server.monitor.start(dict(player_name='TC',source_mode=source,capture_hz=2,ocr_interval=2,confirmations=2))
    process=psutil.Process();process.cpu_percent(None);stop=threading.Event()
    def sample():
        n=0;saved_selections=set()
        while not stop.wait(.5):
            now=datetime.now(timezone.utc).isoformat();cpu=process.cpu_percent(None)
            with (OUT/'resources.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(dict(at=now,cpu_one_core_percent=cpu,cpu_machine_percent=cpu/(psutil.cpu_count() or 1),rss_mb=process.memory_info().rss/1024**2))+'\n')
            n+=1
            # Save each stable selection once for this experiment, never every frame.
            # Sampling shares the monitor lock and checks the exact recognized pixels.
            with server.monitor.lock:
                tracker=server.monitor.tracker
                selection=tracker.selection if tracker and tracker.state=='SONG_SELECT' else None
                picture=server.monitor.latest_image
                if selection and selection['observation_id'] not in saved_selections and picture is not None and image_hash(picture.convert('RGB').resize((1024,800)))==selection['frame_hash']:
                    folder=OUT/'selection-evidence';folder.mkdir(exist_ok=True)
                    name=selection['observation_id'];picture.save(folder/(name+'.png'))
                    (folder/(name+'.json')).write_text(json.dumps(dict(observed_at=now,selection=selection,source='passive live OCR, not manual confirmation',preplay_readiness=selection_readiness(selection)),ensure_ascii=False,indent=2),encoding='utf8')
                    saved_selections.add(name)
            if n%4==0:
                status=server.monitor.status();status['observed_at']=now
                status['preplay_readiness']=selection_readiness((status.get('tracking')or{}).get('selection'))
                temporary=OUT/'status.tmp';temporary.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(OUT/'status.json')
    worker=threading.Thread(target=sample,daemon=True);worker.start()
    print('ISOLATED LIVE http://127.0.0.1:40510/monitor ; source='+source+' ; mode='+mode,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close();stop.set();worker.join(2)
        with closing(sqlite3.connect(DB)) as db:summary=dict(tables=audit(db),schema=db.execute('PRAGMA user_version').fetchone()[0])
        summary.update(monitor=server.monitor.status(),storage=server.automatic.status(),new_live_observation=True,isolated_only=True)
        (OUT/'final.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--fresh-production',action='store_true');p.add_argument('--run-name');p.add_argument('--source-mode',choices=['auto','official','custom'],default='auto');p.add_argument('--mode',choices=['MANUAL','VERIFIED_AUTO'],default='MANUAL');a=p.parse_args()
    if a.run_name:
        if not a.run_name.replace('-','').isalnum() or len(a.run_name)>64:raise ValueError('Use a simple unique run name.')
        OUT=ROOT/'reports/phase5f'/a.run_name;DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor'
    prepare(a.fresh_production) if a.prepare else serve(a.source_mode,a.mode)
