"""Read-only real-record replay plus explicitly generated isolated submission cases."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from PIL import Image,ImageDraw
from dance_results.database import LibraryDatabase
from dance_results.scanner import scan_library
from dance_results.library_service import LibraryService
from dance_results.result_review import ResultReviews
from dance_results.result_ocr import parse_result
from dance_results.monitor_tracking import atomic_json
from dance_results.submissions import SubmissionService
from dance_results.gui import create_server

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5d2/gui';DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor'


def token(text,x,y):return dict(text=str(text),confidence=.99,box=[[x-9,y-8],[x+9,y-8],[x+9,y+8],[x-9,y+8]])


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    if DB.exists():raise ValueError('Existing isolated experiment preserved.')
    with closing(sqlite3.connect((OUT.parent/'baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DB)) as dst:src.backup(dst)
    p=OUT/'SONG/Isolated EX Lamp/JOINT/chart.sm';p.parent.mkdir(parents=True);p.write_text('#TITLE:JOINT;',encoding='utf8')
    with LibraryDatabase(DB) as lib:
        migration=lib.migration_results[-1];lib.sync(scan_library(OUT/'SONG'))
        song=dict(lib.connection.execute('SELECT * FROM songs WHERE root_id=(SELECT root_id FROM library_roots WHERE path=?)',(str(OUT/'SONG'),)).fetchone())
    service=LibraryService(DB);reviews=ResultReviews(DB,MON);store=SubmissionService(service,reviews);records=[];pending=None
    for i,(perfect,cool,bad,miss,failed) in enumerate([(850,100,50,0,False),(950,0,3,7,True),(960,0,3,7,True)]):
        sid=str(uuid4());folder=MON/'sessions'/sid;folder.mkdir(parents=True)
        im=Image.new('RGB',(1024,800),'#222230');ImageDraw.Draw(im).text((50,80),'ISOLATED GENERATED RESULT - NOT REAL GAMEPLAY\nJOINT / '+str((perfect,cool,bad,miss))+' / '+('FAILED' if failed else 'COMPLETED'),fill='white');im.save(folder/'result.png')
        session=dict(session_id=sid,started_at=f'2026-10-27T00:0{i}:00+00:00',ended_at=f'2026-10-27T00:0{i}:50+00:00',status='pending_result',song_id=song['song_id'],matching_status='confirmed_manual',selection=None,
                     result=dict(state='GAME_OVER' if failed else 'RESULT',screenshot='result.png'),isolated_generated_test=True)
        atomic_json(folder/'session.json',session)
        tokens=[token(t,x,200) for t,x in [('Perfect',475),('Cool',555),('Bad',630),('Miss',704)]]
        tokens += [token(t,x,250) for t,x in [('TC',185),(perfect+cool,368),(perfect,480),(cool,556),(bad,635),(miss,713),(900,888),('F' if failed else 'A+',980)]]
        tokens += [token('歌曲名：',55,780),token('JOINT',140,780),token('LV:17',300,780)]
        if failed:tokens.append(token('GAMEOVER',25,250))
        ocr=parse_result(tokens,'TC');ocr['tokens']=tokens;row=reviews.ingest(session,ocr)
        values=dict(perfect=perfect,cool=cool,bad=bad,miss=miss,max_combo=perfect+cool,game_score=900,grade='F' if failed else 'A+',completion_status='FAILED' if failed else 'COMPLETED',game_level=17,total_notes=1000,difficulty='HARD',player_name='TC',duration_seconds=None)
        body=dict(session_id=sid,review_id=row['review_id'],revision=0,values=values,song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'],notes_verified=True,identity_reviewed=True,player_verified=True,duration_verified=False,confirmation_note='ISOLATED GENERATED EX/LAMP TEST ONLY',acknowledge=True,confirm=True,play_mode='single')
        saved=reviews.save(body);body['revision']=saved['revision']
        if i<2:
            preview=store.prepare(body)
            result=store.submit({**body,'submission_token':preview['submission_token'],'submit_acknowledge':True});records.append(result['record']['record_id'])
        else:pending=sid
    atomic_json(OUT/'manifest.json',dict(database=str(DB),monitor_directory=str(MON),song_id=song['song_id'],record_ids=records,pending_session=pending,migration=migration,isolated_only=True,new_real_gameplay=False))
    print('Isolated real Zhou snapshot + generated CLEAR/FAILED records prepared.')


def run():
    server=create_server(DB,40494,MON);print('ISOLATED http://127.0.0.1:40494/history?song_id=2726&difficulty=HARD',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


def lamp_review():
    """A lower EX but better lamp: still only a pending isolated test review."""
    from copy import deepcopy
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf8'))
    original=MON/'sessions'/manifest['pending_session']
    old=json.loads((original/'result-review.json').read_text(encoding='utf8'))
    sid=str(uuid4());folder=MON/'sessions'/sid;folder.mkdir(parents=True)
    session=deepcopy(old['locked_session']);session.update(session_id=sid,started_at='2026-10-27T00:03:00+00:00',ended_at='2026-10-27T00:03:50+00:00')
    session['result']['state']='RESULT';atomic_json(folder/'session.json',session)
    im=Image.new('RGB',(1024,800),'#222230');ImageDraw.Draw(im).text((50,80),'ISOLATED GENERATED LAMP-ONLY TEST\nNOT REAL GAMEPLAY / EX 1900 / AC',fill='white');im.save(folder/'result.png')
    tokens=[token(t,x,200) for t,x in [('Perfect',475),('Cool',555),('Bad',630),('Miss',704)]]
    tokens += [token(t,x,250) for t,x in [('TC',185),(1000,368),(900,480),(100,556),(0,635),(0,713),(900,888),('A+',980)]]
    tokens += [token('歌曲名：',55,780),token('JOINT',140,780),token('LV:17',300,780)]
    ocr=parse_result(tokens,'TC');ocr['tokens']=tokens
    reviews=ResultReviews(DB,MON);review=reviews.ingest(session,ocr)
    body=deepcopy(old['draft']);body.update(session_id=sid,review_id=review['review_id'],revision=0,acknowledge=True,confirm=True,play_mode='single')
    body.update(root_id=body['song_identity']['root_id'],relative_path=body['song_identity']['relative_path'])
    body['values'].update(perfect=900,cool=100,bad=0,miss=0,max_combo=1000,completion_status='COMPLETED',grade='A+')
    reviews.save(body);manifest['lamp_pending_session']=sid;atomic_json(OUT/'manifest.json',manifest)
    print('ISOLATED lamp-only pending review '+sid)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--lamp-review',action='store_true');a=parser.parse_args()
    prepare() if a.prepare else lamp_review() if a.lamp_review else run()
