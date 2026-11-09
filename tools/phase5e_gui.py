"""Generate clearly labelled isolated GUI examples; never use the formal DB."""
import argparse
from contextlib import closing
from datetime import datetime,timedelta
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from PIL import Image,ImageDraw
from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryService
from dance_results.monitor_tracking import atomic_json
from dance_results.result_ocr import parse_result
from dance_results.result_review import ResultReviews
from dance_results.scanner import scan_library
from dance_results.submissions import SubmissionService

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5e/gui';DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor';MANIFEST=OUT/'manifest.json'
TITLE='[ISOLATED] Pulse Archive 夜の軌跡'
CASES=dict(first=(850,100,100,0,False),score=(900,100,50,0,False),lamp=(750,300,0,0,False),failed=(970,0,3,7,True),saved=(900,100,50,0,False),double=(1050,0,0,0,False),reduced=(850,100,100,0,False))


def token(value,x,y):return dict(text=str(value),confidence=.99,box=[[x-9,y-8],[x+9,y-8],[x+9,y+8],[x-9,y+8]])


def prepare():
    if DB.exists():raise ValueError('Existing isolated experiment is preserved.')
    OUT.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect((OUT.parent/'baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(DB)) as dst:src.backup(dst)
    p=OUT/'SONG/Phase5E Isolated Stage/Pulse Archive/chart.sm';p.parent.mkdir(parents=True);p.write_text('#TITLE:'+TITLE+';\n#ARTIST:Phase5E fixture;',encoding='utf8')
    with LibraryDatabase(DB) as lib:
        lib.sync(scan_library(OUT/'SONG'))
        song=dict(lib.connection.execute('SELECT * FROM songs WHERE root_id=(SELECT root_id FROM library_roots WHERE path=?)',(str(OUT/'SONG'),)).fetchone())
    atomic_json(MANIFEST,dict(database=str(DB),monitor_directory=str(MON),song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'],isolated_only=True,new_real_gameplay=False,cases={}))
    add_review('first',commit=True)


def add_review(case,*,commit=False):
    m=json.loads(MANIFEST.read_text(encoding='utf8'))
    if case in m['cases']:raise ValueError('Existing case preserved: '+case)
    perfect,cool,bad,miss,failed=CASES[case];i=list(CASES).index(case)
    sid=str(uuid4());folder=MON/'sessions'/sid;folder.mkdir(parents=True)
    im=Image.new('RGB',(1024,800),'#171723');draw=ImageDraw.Draw(im)
    draw.rectangle((25,120,995,360),outline='#a080cc',width=3)
    draw.text((45,155),f'ISOLATED GENERATED FIXTURE - NOT REAL GAMEPLAY\nCASE: {case} / EX {perfect*2+cool}\n\nPerfect {perfect} / Cool {cool} / Bad {bad} / Miss {miss}\n\n'+('GAME OVER' if failed else 'COMPLETED'),fill='white')
    im.save(folder/'result.png')
    start=datetime.fromisoformat('2026-10-27T00:00:00+09:00')+timedelta(minutes=i*2)
    session=dict(session_id=sid,started_at=start.isoformat(),ended_at=(start+timedelta(seconds=90)).isoformat(),status='pending_result',song_id=m['song_id'],matching_status='confirmed_manual',selection=None,result=dict(state='GAME_OVER' if failed else 'RESULT',screenshot='result.png'),isolated_generated_test=True)
    atomic_json(folder/'session.json',session)
    tokens=[token(t,x,200) for t,x in [('Perfect',475),('Cool',555),('Bad',630),('Miss',704)]]
    tokens += [token(t,x,250) for t,x in [('TC',185),(perfect+cool,368),(perfect,480),(cool,556),(bad,635),(miss,713),(900,888),('F' if failed else 'A+',980)]]
    tokens += [token('歌曲名：',55,780),token(TITLE,140,780),token('LV:17',300,780)]
    if failed:tokens.append(token('GAMEOVER',25,250))
    ocr=parse_result(tokens,'TC');ocr['tokens']=tokens
    reviews=ResultReviews(DB,MON);review=reviews.ingest(session,ocr)
    values=dict(perfect=perfect,cool=cool,bad=bad,miss=miss,max_combo=perfect+cool,game_score=900,grade='F' if failed else 'A+',completion_status='FAILED' if failed else 'COMPLETED',game_level=17,total_notes=1050,difficulty='NORMAL' if case=='reduced' else 'HARD',player_name='TC',duration_seconds=None)
    body=dict(session_id=sid,review_id=review['review_id'],revision=0,values=values,song_id=m['song_id'],root_id=m['root_id'],relative_path=m['relative_path'],notes_verified=True,identity_reviewed=True,player_verified=True,duration_verified=False,confirmation_note='ISOLATED GENERATED PHASE5E GUI TEST; NOT REAL GAMEPLAY',acknowledge=True,confirm=True,play_mode='single')
    saved=reviews.save(body);body['revision']=saved['revision']
    item=dict(session_id=sid,body=body)
    if commit:
        store=SubmissionService(LibraryService(DB),reviews);preview=store.prepare(body)
        result=store.submit({**body,'submission_token':preview['submission_token'],'submit_acknowledge':True});item['record_id']=result['record']['record_id']
    m['cases'][case]=item;atomic_json(MANIFEST,m);print(json.dumps(dict(case=case,session_id=sid,song_id=m['song_id'],record_id=item.get('record_id'),isolated_only=True)))


def serve():
    server=create_server(DB,40496,MON);print('ISOLATED http://127.0.0.1:40496/history',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--review-case',choices=CASES);p.add_argument('--commit-generated-case',action='store_true');args=p.parse_args()
    prepare() if args.prepare else add_review(args.review_case,commit=args.commit_generated_case) if args.review_case else serve()
