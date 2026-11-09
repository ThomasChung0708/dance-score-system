"""Real screenshot replay and artificial downloaded files, isolated catalog only."""
import argparse
from contextlib import closing
import copy
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4
from PIL import Image
from dance_results.gui import create_server
from dance_results.database import LibraryDatabase
from dance_results.scanner import scan_library
from dance_results.monitor_tracking import atomic_json
from dance_results.result_review import ResultReviews
from dance_results.download_tracking import DownloadTracker
from dance_results.library_service import LibraryService
from dance_results.recent_updates import recent_updates

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5d/gui'
DB=OUT/'PHASE5D-TEST.sqlite3';MON=OUT/'monitor';SONG=OUT/'SONG'


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if DB.exists():raise ValueError('Existing GUI experiment preserved.')
    with closing(sqlite3.connect((ROOT/'reports/phase5d/baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DB)) as dest:src.backup(dest)
    initial=SONG/'Existing Pack/Existing Song/song.sm';initial.parent.mkdir(parents=True);initial.write_text('#TITLE:Existing Song;',encoding='utf8')
    with LibraryDatabase(DB) as lib:
        lib.sync(scan_library(SONG));rid=lib.connection.execute('SELECT root_id FROM library_roots WHERE path=?',(str(SONG),)).fetchone()[0]
    atomic_json(MON/'catalog-location.json',dict(database_path=str(DB)))
    original=ROOT/'data/monitor/sessions/944a2977-fe34-4112-a990-e864c317287c'
    review=json.loads((original/'result-review.json').read_text(encoding='utf8'));s=copy.deepcopy(review['locked_session']);s['session_id']=str(uuid4())
    s['isolated_replay']=dict(original_session=original.name,not_new_real_gameplay=True,artificial_download_tree=True)
    dest=MON/'sessions'/s['session_id'];dest.mkdir(parents=True)
    shutil.copyfile(original/'result.png',dest/'result.png');atomic_json(dest/'session.json',s)
    reviews=ResultReviews(DB,MON);reviews.ingest(s,review['ocr']);library=LibraryService(DB)
    with library.open() as lib:before=recent_updates(lib.connection)
    tracker=DownloadTracker(library,reviews,quiet_seconds=0)
    tracker._roots=lambda:[dict(root_id=rid,path=str(SONG),root_kind='filesystem')]
    proof=ROOT/'tests/專案測試圖片/下載條.PNG'
    try:
        tracker._begin(dict(lobby=dict(player_name='TC',slot=2,confidence=.9169,local_player_status='CONFIRMED',multiplayer_observed=True,self_no_map=True),room=dict(title='Heart of Witch'),epoch=1,image=Image.open(proof).convert('RGB'),observed_at='2026-10-26T00:00:00+00:00'))
        tracker._associate(s)
        p=SONG/'New Download Pack/Heart Alternate/song.sm';p.parent.mkdir(parents=True);p.write_text('#TITLE:Heart of Witch;#ARTIST:Isolated fixture;',encoding='utf8')
        tracker._poll();tracker._poll()
        with library.open() as lib:after=recent_updates(lib.connection)
        assert [r['record_id'] for r in before['updates']]==[r['record_id'] for r in after['updates']]
        atomic_json(OUT/'manifest.json',dict(database=str(DB),monitor_directory=str(MON),session_id=s['session_id'],root_id=rid,
            download_evidence=tracker.status(),scan_did_not_change_recent=True,initial_recent_count=len(before['updates']),
            isolated_only=True,new_real_gameplay=False))
    finally:tracker.close()
    print('Isolated GUI prepared. Original screenshots and production records unchanged.')


def run():
    server=create_server(DB,40488,MON)
    print('ISOLATED http://127.0.0.1:40488/',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');a=parser.parse_args()
    prepare() if a.prepare else run()
