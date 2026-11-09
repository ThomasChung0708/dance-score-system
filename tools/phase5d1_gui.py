"""Compact download GUI: full catalog copy + artificial SONG, no live play claim."""
import argparse
import copy
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4
from dance_results.database import LibraryDatabase
from dance_results.scanner import scan_library
from dance_results.library_service import LibraryService
from dance_results.monitor_tracking import atomic_json
from dance_results.result_review import ResultReviews
from dance_results.download_tracking import DownloadTracker,session_download
from dance_results.gui import create_server

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5d1/gui'
DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor';SONG=OUT/'SONG'


def source(pack,folder,ext='sm',title='Heart of Witch'):
    p=SONG/pack/folder/('song.'+ext);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text('#TITLE:'+title+';',encoding='utf8');return p


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if DB.exists():raise ValueError('Preserve the existing isolated fixture.')
    with closing(sqlite3.connect((OUT.parent/'baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DB)) as dst:src.backup(dst)
    for i in range(20):source('Barry',str(i),title='Old Barry '+str(i))
    with LibraryDatabase(DB) as lib:
        lib.sync(scan_library(SONG));rid=lib.connection.execute('SELECT root_id FROM library_roots WHERE path=?',(str(SONG),)).fetchone()[0]
    for i in range(20):source('Barry',str(i),'dwi',title='Old Barry '+str(i))
    source('Already On Disk','Unregistered',title='Old before snapshot')
    original=ROOT/'data/monitor/sessions/944a2977-fe34-4112-a990-e864c317287c'
    row=json.loads((original/'result-review.json').read_text(encoding='utf8'))
    session=copy.deepcopy(row['locked_session']);session['session_id']=str(uuid4())
    session['isolated_replay']=dict(not_new_real_gameplay=True,artificial_download_tree=True)
    folder=MON/'sessions'/session['session_id'];folder.mkdir(parents=True)
    shutil.copyfile(original/'result.png',folder/'result.png');atomic_json(folder/'session.json',session)
    reviews=ResultReviews(DB,MON);reviews.ingest(session,row['ocr'])
    library=LibraryService(DB);tracker=DownloadTracker(library,reviews,quiet_seconds=0)
    tracker._roots=lambda:[dict(root_id=rid,path=str(SONG),root_kind='filesystem')]
    try:
        tracker._begin(dict(observed_at='2026-10-26T15:00:00+00:00',lobby=dict(player_name='TC'),room=dict(title='Heart of Witch'),epoch=1))
        tracker._associate(session)
        source('connect','New Heart');source('Brand New Pack','Other Song',title='Unrelated New Song')
        tracker._poll();tracker._poll()
        evidence=session_download(MON,session['session_id']);assert len(evidence['candidates'])==2 and len(evidence['existing_updates'])==20 and len(evidence['uncertain_imports'])==1
        detail=reviews.detail(session['session_id']);assert len(detail['candidates'])==1
        atomic_json(OUT/'manifest.json',dict(database=str(DB),monitor_directory=str(MON),session_id=session['session_id'],
            isolated_only=True,new_real_gameplay=False,root_id=rid,evidence=evidence,summary=detail['download_evidence']))
    finally:tracker.close()
    print('Prepared isolated compact-summary GUI: 2 new / 20 existing updates / 1 unknown import / 1 relevant candidate.')


def run():
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf8'))
    server=create_server(DB,40492,MON)
    server.monitor.downloads.view=manifest['evidence']
    print('ISOLATED http://127.0.0.1:40492/reviews',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');a=parser.parse_args()
    prepare() if a.prepare else run()
