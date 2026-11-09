"""Phase 5A full catalog COPY rehearsal with saved real screenshot replays.

No production migration or submission. All human confirmations below are
explicitly labelled test decisions in a separate catalog/monitor directory.
"""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from dance_results.database import LibraryDatabase
from dance_results.library_service import LibraryService
from dance_results.monitor_ocr import Analyzer
from dance_results.monitor_tracking import atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.result_review import ResultReviews
from dance_results.scanner import scan_library
from dance_results.sqlite_safety import audit,projection,row_limits,check_integrity
from dance_results.submissions import SubmissionService

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/phase5a'
PRODUCTION=ROOT/'data/library.sqlite3'
CAG=ROOT/'data/monitor/sessions/38f201bf-025a-4eae-9e73-a78d13fa9b3c'
ASTRAL=ROOT/'data/monitor/sessions/f6bba0ba-582a-4e3f-a66a-71b06bff3da5'


def main():
    OUT.mkdir(exist_ok=True,parents=True);run=OUT/('run-'+uuid4().hex[:10]);run.mkdir()
    target=run/'library.sqlite3';directory=run/'monitor'
    assert target.resolve()!=PRODUCTION.resolve() and target.is_relative_to(OUT)
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        before=audit(source);columns=projection(source);limits=row_limits(source)
        with closing(sqlite3.connect(target)) as dest:source.backup(dest)
    atomic_json(run/'test-only.json',dict(production_read_only=str(PRODUCTION),test_database=str(target),evidence_type='saved screenshot replay, not live gameplay'))
    with LibraryDatabase(target) as db:
        migrations=db.migration_results
        assert audit(db.connection,columns,limits)==before
        check_integrity(db.connection)
        root=Path(db.connection.execute("SELECT path FROM library_roots WHERE root_kind='filesystem'").fetchone()[0])
        # Actual source path found during Phase 4C. Import it on this COPY only,
        # using the existing parser; never create an ID from title alone.
        report=scan_library(root,[Path('Osu Transfer')/'Astral Paradox - Kobaryo [a52c0e94]'],defer_not_ready=True)
        db.sync(report)
        astral_song=next(s.song_id for s in report.songs if s.title=='Astral Paradox')
    analyzer=Analyzer(LocalOCREngine(ROOT/'.ocr-models',low_latency=True))
    cag_ocr=analyzer.analyze(Image.open(CAG/'result.png'))['result_ocr']
    astral_ocr=analyzer.analyze(Image.open(ASTRAL/'result.png'))['result_ocr']
    atomic_json(run/'static-ocr.json',dict(cagayake=cag_ocr,astral=astral_ocr))
    reviews=ResultReviews(target,directory);store=SubmissionService(LibraryService(target),reviews)

    def replay(original,ocr,song_id,values,mode,*,confirm=True):
        sid=str(uuid4());folder=directory/'sessions'/sid;folder.mkdir(parents=True)
        session=json.loads((original/'session.json').read_text(encoding='utf8'))
        session['session_id']=sid;session['evidence_source']='PHASE5A TEST REPLAY; not a new real play'
        shutil.copyfile(original/'result.png',folder/'result.png');atomic_json(folder/'session.json',session)
        row=reviews.ingest(session,copy.deepcopy(ocr))
        song=next(c for c in reviews.candidates(values.get('search_title',''),'custom',limit=100)['candidates']if c['song_id']==song_id)
        values={k:v for k,v in values.items()if k!='search_title'}
        body=dict(session_id=sid,review_id=row['review_id'],revision=0,song_id=song_id,
            root_id=song['root_id'],relative_path=song['relative_path'],values=values,
            player_verified=True,identity_reviewed=True,notes_verified=values.get('total_notes') is not None,
            duration_verified=values.get('duration_seconds') is not None,player_row_id='row-1' if mode=='multiplayer' else 'row-0',
            confirmation_note='ISOLATED TEST: manual confirmation of saved screenshot replay',acknowledge=True,confirm=confirm)
        saved=reviews.save(body);body.update(revision=saved['revision'],play_mode=mode)
        return body

    def submit(body):
        p=store.prepare(body);assert p['can_submit'],p['issues']
        request={**body,'submission_token':p['submission_token'],'submit_acknowledge':True}
        return store.submit(request),request

    cvalues=dict(search_title='Cagayake! GIRLS',perfect=835,cool=3,bad=0,miss=0,max_combo=838,game_score=59232,
                 grade='A++',completion_status='COMPLETED',difficulty='HARD',game_level=20,total_notes=838,
                 duration_seconds=88,player_name='TC')
    normal_body=replay(CAG,cag_ocr,16,cvalues,'single')
    normal,request=submit(normal_body);again=store.submit(request)
    assert normal['record']['record_id']==again['record']['record_id'] and again['idempotent']
    assert (normal['record']['ex_score'],normal['record']['max_score'],normal['record']['max_difference'],normal['record']['lamp_status'])==(1673,1676,3,'AC')
    second,_=submit(replay(CAG,cag_ocr,16,cvalues,'single'))
    assert second['record']['record_id']!=normal['record']['record_id']
    fvalues=dict(search_title='Astral Paradox',perfect=585,cool=32,bad=2,miss=45,max_combo=238,game_score=35575,
                 grade='F',completion_status='FAILED',difficulty='HARD',game_level=38,total_notes=None,
                 duration_seconds=None,player_name='TC')
    failed,_=submit(replay(ASTRAL,astral_ocr,astral_song,fvalues,'multiplayer'))
    assert failed['record']['ex_score']==1202 and failed['record']['lamp_status']=='FAILED'
    assert all(failed['record'][key] is None for key in ('total_notes','max_score','max_difference','percentage'))
    gui_body=replay(CAG,cag_ocr,16,cvalues,'single');prepared=store.prepare(gui_body)
    with closing(sqlite3.connect(target)) as db:before_rollback=audit(db)
    def fail(name):
        if name=='after_record':raise RuntimeError('intentional rollback rehearsal')
    with patch.object(store,'_checkpoint',side_effect=fail):
        try:store.submit({**gui_body,'submission_token':prepared['submission_token'],'submit_acknowledge':True})
        except RuntimeError:pass
        else:raise AssertionError('rollback fault not raised')
    with closing(sqlite3.connect(target)) as db:
        assert audit(db)==before_rollback;check_integrity(db)
    # Leave a confirmed copy review for an actual browser click-through. Also
    # leave an unconfirmed NULL Cool example for the disabled-button check.
    missing=copy.deepcopy(cag_ocr);missing['fields']['cool']['value']=None
    invalid_body=replay(CAG,missing,16,{**cvalues,'cool':None},'single',confirm=False)
    invalid=store.prepare(invalid_body);assert not invalid['can_submit']
    fixture=dict(database=str(target),monitor=str(directory),ready_session_id=gui_body['session_id'],
                 missing_cool_session_id=invalid_body['session_id'],ready_request=gui_body,
                 expected_gui_record_id=4,kind='isolated catalog and screenshot replay, not live play')
    atomic_json(run/'gui-fixture.json',fixture)
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        assert audit(source)==before
    for original in (CAG,ASTRAL):
        assert (original/'result.png').is_file()
    history=SubmissionService(LibraryService(target),ResultReviews(target,directory)).history()
    summary=dict(run_directory=str(run),schema_migrations=migrations,existing_projection_preserved=True,
        production_unchanged=True,production_play_records=before['play_records']['count'],copy_records=history['total'],
        normal_record=normal['record']['record_id'],normal_score=dict(ex_score=1673,max_score=1676,max_difference=3,lamp='AC'),
        duplicate_record_id=again['record']['record_id'],new_session_record_id=second['record']['record_id'],
        failed_record=failed['record']['record_id'],failed_score=dict(ex_score=1202,max_score=None,lamp='FAILED'),
        rollback_all_tables_unchanged=True,null_cool_submission_rejected=True,gui_fixture=str(run/'gui-fixture.json'),
        new_live_gameplay_tested=False)
    atomic_json(OUT/'validation.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
