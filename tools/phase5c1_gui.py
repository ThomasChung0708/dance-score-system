"""Original screenshot replay on a full, isolated catalog. No real-play claim."""
import argparse
import copy
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from uuid import uuid4

from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.monitor_tracking import atomic_json,ManualReferences
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.result_review import ResultReviews

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5c1/gui'
DB=OUT/'PHASE5C1-TEST.sqlite3';MON=OUT/'monitor'


def materialize(reviews,payload):
    s=payload['session'];folder=MON/'sessions'/s['session_id'];folder.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(payload['image'],folder/'result.png');atomic_json(folder/'session.json',s)
    return reviews.ingest(s,payload['ocr'])


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if DB.exists():raise ValueError('Existing isolated experiment preserved.')
    with closing(sqlite3.connect((ROOT/'reports/phase5c1/baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(DB)) as dest:src.backup(dest)
    with LibraryDatabase(DB) as library:migrations=library.migration_results
    atomic_json(MON/'catalog-location.json',dict(database_path=str(DB)))
    payloads={}
    for name,original in [('heart_unknown','944a2977-fe34-4112-a990-e864c317287c'),
                          ('auto_clear','6e7e3783-80b5-4a2e-8b21-23319ee8ef88'),
                          ('cagayake_pending','38f201bf-025a-4eae-9e73-a78d13fa9b3c')]:
        folder=ROOT/'data/monitor/sessions'/original
        row=json.loads((folder/'result-review.json').read_text(encoding='utf8'))
        session=copy.deepcopy(row['locked_session']);session['session_id']=str(uuid4())
        session['isolated_replay']=dict(original_session=original,not_new_real_gameplay=True)
        payloads[name]=dict(session=session,ocr=row['ocr'],image=str(folder/'result.png'))
    catalog=ReadOnlyCatalog(DB)
    try:
        for name,sid in [('auto_clear',483),('cagayake_pending',16)]:
            p=payloads[name];song=catalog.songs[sid]
            p['session']['selection']['source_type']='custom'
            p['session']['selection']['pack']=dict(pack_id=song['pack_id'],name=song['pack_name'],status='confirmed')
            p['session'].update(song_id=sid,matching_status='confirmed_manual_reference')
            ManualReferences(MON,DB).add(p['session']['selection']['fields'],song,'isolated_controlled_identity_reference')
    finally:catalog.close()
    p=copy.deepcopy(payloads['heart_unknown']);p['session']['session_id']=str(uuid4())
    p['session']['isolated_replay']['hypothetical_max_proof_test_only']=True
    payloads['heart_hypothetical_proof']=p
    reviews=ResultReviews(DB,MON)
    for name in ('heart_unknown','heart_hypothetical_proof','cagayake_pending'):materialize(reviews,payloads[name])
    atomic_json(OUT/'manifest.json',dict(database=str(DB),monitor_directory=str(MON),payloads=payloads,migrations=migrations))
    print('Isolated 5C.1 fixture prepared; original screenshots unchanged.')


def run():
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf8'))
    server=create_server(DB,40480,MON)
    def receive():
        last=None
        while True:
            path=OUT/'control.json'
            if path.exists():
                request=json.loads(path.read_text(encoding='utf8'))
                if request['id']!=last:
                    last=request['id'];materialize(server.monitor.reviews,manifest['payloads'][request['case']])
                    atomic_json(OUT/'control-result.json',request)
            time.sleep(.25)
    threading.Thread(target=receive,daemon=True).start()
    print('ISOLATED http://127.0.0.1:40480/reviews',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');args=parser.parse_args()
    prepare() if args.prepare else run()
