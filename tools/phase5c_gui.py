"""Isolated GUI harness. Original screenshots read-only; no production writes.

Run --prepare, then run the server. A local control file simulates newly received
Sessions through the real ResultReviews.ingest hook, not an alternate DB writer.
"""
import argparse
import copy
from contextlib import closing
import hashlib
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

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5c/gui'
DB=OUT/'PHASE5C-TEST.sqlite3';MON=OUT/'monitor'


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if DB.exists():raise ValueError('Existing isolated GUI data preserved; use it or choose a fresh directory.')
    with closing(sqlite3.connect((ROOT/'reports/phase5c/baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(DB)) as target:source.backup(target)
    with LibraryDatabase(DB) as library:migrations=library.migration_results
    atomic_json(MON/'catalog-location.json',dict(database_path=str(DB)))
    payloads={}
    for name,original in [('heart_manual','944a2977-fe34-4112-a990-e864c317287c'),
                          ('auto_good','6e7e3783-80b5-4a2e-8b21-23319ee8ef88'),
                          ('auto_low_confidence','38f201bf-025a-4eae-9e73-a78d13fa9b3c')]:
        folder=ROOT/'data/monitor/sessions'/original
        row=json.loads((folder/'result-review.json').read_text(encoding='utf8'))
        session=copy.deepcopy(row['locked_session']);sid=str(uuid4());session['session_id']=sid
        session['isolated_replay']=dict(original_session=original,not_new_real_gameplay=True)
        payloads[name]=dict(session=session,ocr=row['ocr'],image=str(folder/'result.png'),session_id=sid)
    # The source Pack/reference is a controlled fixture; original screen source
    # was UNKNOWN. It is never claimed as a newly verified real play.
    good=payloads['auto_good'];good['session']['selection']['source_type']='custom'
    good['session']['selection']['pack']=dict(pack_id=15,name="Blue's 3rd step",status='confirmed')
    good['session'].update(song_id=483,matching_status='confirmed_manual_reference')
    catalog=ReadOnlyCatalog(DB)
    try:
        ManualReferences(MON,DB).add(good['session']['selection']['fields'],catalog.songs[483],'isolated_manual_reference_fixture')
    finally:catalog.close()
    pending=copy.deepcopy(good);pending['session_id']=str(uuid4());pending['session']['session_id']=pending['session_id']
    pending['synthetic_values']=True
    vals=dict(perfect=1251,cool=0,bad=0,miss=0,max_combo=1251,game_score=99999,grade='A++')
    for k,v in vals.items():
        pending['ocr']['fields'][k].update(value=v,confidence=.99,reason=None)
        for player in pending['ocr']['player_rows']:player['fields'][k].update(value=v,confidence=.99,reason=None)
    from PIL import Image,ImageDraw
    image=Image.new('RGB',(1024,800),'#171421');draw=ImageDraw.Draw(image)
    draw.text((60,120),'ISOLATED SYNTHETIC TEST / NOT A REAL PLAY',fill='white')
    draw.text((60,200),'Amnolys / HARD LV28 / FULL1253',fill='#d5b0ff')
    draw.text((60,260),'TC / P1251 C0 B0 M0 / Combo1251',fill='#ffe290')
    synthetic=OUT/'synthetic-pending-lamp.png';image.save(synthetic)
    pending['image']=str(synthetic);payloads['manual_pending_lamp']=pending
    reviews=ResultReviews(DB,MON)
    for name in ('heart_manual','manual_pending_lamp'):materialize(reviews,payloads[name])
    atomic_json(OUT/'manifest.json',dict(database=str(DB),monitor_directory=str(MON),migrations=migrations,payloads=payloads))
    print('Prepared isolated Phase 5C GUI fixture.')


def materialize(reviews,payload):
    session=payload['session'];folder=MON/'sessions'/session['session_id'];folder.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(payload['image'],folder/'result.png');atomic_json(folder/'session.json',session)
    return reviews.ingest(session,payload['ocr'])


def run():
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf8'))
    server=create_server(DB,40476,MON)
    def controls():
        last=None
        while True:
            path=OUT/'control.json'
            if path.exists():
                data=json.loads(path.read_text(encoding='utf8'))
                if data.get('id')!=last:
                    last=data['id'];name=data['case']
                    materialize(server.monitor.reviews,manifest['payloads'][name])
                    atomic_json(OUT/'control-result.json',dict(case=name,session_id=manifest['payloads'][name]['session_id']))
            time.sleep(.25)
    threading.Thread(target=controls,daemon=True).start()
    print('ISOLATED GUI http://127.0.0.1:40476/reviews',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');args=parser.parse_args()
    prepare() if args.prepare else run()
