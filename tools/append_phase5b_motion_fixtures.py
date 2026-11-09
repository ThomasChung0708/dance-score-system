"""Supplement only the generated Phase5B test directory with motion fixtures."""
import copy
import json
from pathlib import Path
from uuid import uuid4

from PIL import Image,ImageDraw
from dance_results.monitor_tracking import atomic_json
from dance_results.result_review import ResultReviews


def main():
    root=Path(__file__).resolve().parent.parent;out=root/'reports/phase5b'
    manifest=json.loads((out/'gui-fixture.json').read_text(encoding='utf8'))
    database=Path(manifest['database']).resolve();directory=Path(manifest['monitor_directory']).resolve()
    assert database.is_relative_to(out) and directory.is_relative_to(out)
    reviews=ResultReviews(database,directory)
    template=next(c for c in manifest['cases'] if c['name']=='first_acp')
    source=directory/'sessions'/template['session_id']
    source_session=json.loads((source/'session.json').read_text(encoding='utf8'))
    source_review=json.loads((source/'result-review.json').read_text(encoding='utf8'))
    for name,difficulty,p,b in [('first_easy_clear','EASY',700,138),('reduced_normal_acp','NORMAL',838,0),('failed_after_acp','HARD',800,0)]:
        if any(c['name']==name for c in manifest['cases']):continue
        sid=str(uuid4());folder=directory/'sessions'/sid;folder.mkdir()
        session=copy.deepcopy(source_session);session['session_id']=sid
        session['selection']['fields']['difficulty']['value']=difficulty
        session['started_at']='2026-10-23T12:01:00+00:00'
        if name=='failed_after_acp':session['result']['state']='GAME_OVER'
        atomic_json(folder/'session.json',session)
        image=Image.new('RGB',(1024,800),'#151321');draw=ImageDraw.Draw(image)
        draw.text((40,100),'PHASE 5B ISOLATED SYNTHETIC MOTION TEST / NOT REAL PLAY',fill='#ffd45c')
        draw.text((40,200),f'Cagayake! GIRLS / {difficulty} / LV20 / 838 notes',fill='white')
        draw.text((40,260),f'TC | P{p} C0 B{b} M0 | {name}',fill='white');image.save(folder/'result.png')
        values=copy.deepcopy(template['request']['values'])
        values.update(difficulty=difficulty,perfect=p,bad=b,max_combo=p,game_score=11000)
        if name=='failed_after_acp':values.update(completion_status='FAILED',miss=38,grade='F')
        ocr=copy.deepcopy(source_review['ocr'])
        for key in values:
            if key in ocr['fields']:ocr['fields'][key]['value']=values[key]
        for row in ocr.get('player_rows',[]):
            for key in values:
                if key in row['fields']:row['fields'][key]['value']=values[key]
        row=reviews.ingest(session,ocr)
        body=copy.deepcopy(template['request']);body.update(session_id=sid,review_id=row['review_id'],revision=0,values=values)
        saved=reviews.save(body);body['revision']=saved['revision']
        manifest['cases'].append(dict(name=name,session_id=sid,request=body))
    atomic_json(out/'gui-fixture.json',manifest);atomic_json(Path(manifest['run_path'])/'manifest.json',manifest)
    print(json.dumps({c['name']:c['session_id'] for c in manifest['cases'][-2:]}))


if __name__=='__main__':main()
