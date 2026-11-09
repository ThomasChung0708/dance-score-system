"""Create clearly marked synthetic review fixtures in a full catalog COPY.

No real play is claimed. Existing game screenshots and production sidecars are
read-only. Formal records are created only when the test GUI explicitly submits.
"""
from contextlib import closing
import copy
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from PIL import Image,ImageDraw
from dance_results.library_service import LibraryService
from dance_results.monitor_tracking import atomic_json
from dance_results.result_review import ResultReviews
from dance_results.sqlite_safety import audit

ROOT=Path(__file__).resolve().parent.parent


def main():
    out=ROOT/'reports/phase5b';run=out/('gui-'+uuid4().hex[:10]);run.mkdir(parents=True)
    target=run/'PHASE5B-TEST.sqlite3';directory=run/'monitor'
    production=ROOT/'data/library.sqlite3'
    with closing(sqlite3.connect(production.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        before=audit(db)
        with closing(sqlite3.connect(target)) as dest:db.backup(dest)
    reviews=ResultReviews(target,directory);library=LibraryService(target)
    atomic_json(directory/'catalog-location.json',dict(database_path=str(target.resolve())))
    song=library.detail(1,16)
    source=ROOT/'data/monitor/sessions/38f201bf-025a-4eae-9e73-a78d13fa9b3c/result-review.json'
    original=json.loads(source.read_text(encoding='utf8'))['ocr']
    scenarios=[('first_clear',700,0,138,0,'COMPLETED'),
               ('failed',800,0,0,38,'FAILED'),
               ('first_ac',200,638,0,0,'COMPLETED'),
               ('first_acp',838,0,0,0,'COMPLETED'),
               ('saved',838,0,0,0,'COMPLETED')]
    cases=[]
    for index,(name,p,c,b,m,completion) in enumerate(scenarios):
        sid=str(uuid4());folder=directory/'sessions'/sid;folder.mkdir(parents=True)
        values=dict(perfect=p,cool=c,bad=b,miss=m,max_combo=p+c,game_score=10000+index,
                    grade='F' if completion=='FAILED' else 'A++',completion_status=completion,
                    game_level=20,total_notes=838,difficulty='HARD',player_name='TC',duration_seconds=88)
        # Synthetic image, clearly labelled. None of the user's PNGs is changed.
        image=Image.new('RGB',(1024,800),'#151321');draw=ImageDraw.Draw(image)
        draw.text((45,100),'PHASE 5B / ISOLATED GUI TEST / NOT A REAL GAMEPLAY',fill='#d4b0ff',stroke_width=1)
        draw.text((45,185),'Cagayake! GIRLS - HARD LV20 / TOTAL838 / '+name,fill='white')
        draw.text((45,240),f'TC | Perfect {p} | Cool {c} | Bad {b} | Miss {m}',fill='#ffd45c')
        draw.text((45,295),'COMPLETION '+completion+' / Synthetic review values',fill='white')
        image.save(folder/'result.png')
        session=dict(session_id=sid,status='pending_result',started_at=f'2026-10-23T12:00:0{index}+00:00',
            song_id=16,matching_status='confirmed_manual',evidence_source='ISOLATED SYNTHETIC GUI TEST; NOT A REAL PLAY',
            result=dict(state='GAME_OVER' if completion=='FAILED' else 'RESULT',screenshot='result.png'),
            selection=dict(source_type='custom',pack=dict(pack_id=song['pack_id'],name=song['pack_name'],status='confirmed'),
                fields={k:dict(value=v,confidence=1.0) for k,v in dict(title='Cagayake! GIRLS',difficulty='HARD',game_level=20,total_notes=838,duration_seconds=88).items()}))
        atomic_json(folder/'session.json',session)
        ocr=copy.deepcopy(original)
        for key,item in ocr['fields'].items():
            item.update(value='Cagayake! GIRLS' if key=='title' else values.get(key),confidence=1.0,reason=None,method='synthetic_fixture')
        for row in ocr.get('player_rows',[]):
            for key,item in row['fields'].items():
                if key in values:item.update(value=values[key],confidence=1.0,reason=None)
        ocr['multiplayer']=False;ocr['footer_level_scope']='local_chart';ocr['local_player_status']='CONFIRMED'
        row=reviews.ingest(session,ocr)
        request=dict(session_id=sid,review_id=row['review_id'],revision=0,song_id=16,root_id=song['root_id'],
            relative_path=song['relative_folder'],values=values,notes_verified=True,duration_verified=True,
            player_verified=True,identity_reviewed=True,acknowledge=True,confirm=True,
            confirmation_note='ISOLATED SYNTHETIC GUI TEST. No real gameplay or production submission.')
        saved=reviews.save(request)
        request.update(revision=saved['revision'],play_mode='single')
        cases.append(dict(name=name,session_id=sid,request=request))
    # Keep Heart's original evidence for a rejected-submit GUI regression.
    heart='944a2977-fe34-4112-a990-e864c317287c'
    source=ROOT/'data/monitor/sessions'/heart;dest=directory/'sessions'/heart;dest.mkdir()
    shutil.copyfile(source/'result.png',dest/'result.png')
    session=json.loads((source/'session.json').read_text(encoding='utf8'))
    atomic_json(dest/'session.json',session)
    ocr=json.loads((source/'result-review.json').read_text(encoding='utf8'))['ocr']
    reviews.ingest(session,ocr)
    manifest=dict(database=str(target.resolve()),monitor_directory=str(directory.resolve()),
        run_path=str(run.resolve()),cases=cases,heart_session_id=heart,song_id=16,
        evidence_type='SYNTHETIC GUI TEST + saved Heart screenshot regression; no new real gameplay',
        production_before=before)
    atomic_json(run/'manifest.json',manifest);atomic_json(out/'gui-fixture.json',manifest)
    with closing(sqlite3.connect(production.resolve().as_uri()+'?mode=ro',uri=True)) as db:assert audit(db)==before
    print(json.dumps(dict(database=str(target),monitor_directory=str(directory),cases={c['name']:c['session_id'] for c in cases}),ensure_ascii=False))


if __name__=='__main__':main()
