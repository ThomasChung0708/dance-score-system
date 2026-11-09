"""Read original screenshots; investigate on an isolated SQLite/Session copy.

This is saved-image OCR and simulated human review, NOT a fresh gameplay test.
Never imports into, confirms a review in, or submits to the production catalog.
"""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
from uuid import uuid4

from PIL import Image
from dance_results.library_service import LibraryError, LibraryService
from dance_results.monitor_ocr import Analyzer
from dance_results.monitor_tracking import atomic_json
from dance_results.note_evidence import judged
from dance_results.ocr_engine import LocalOCREngine
from dance_results.official_import import read_catalog
from dance_results.result_review import ResultReviews
from dance_results.sqlite_safety import audit, check_integrity
from dance_results.submissions import SubmissionService

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/phase5a1'
DATABASE=ROOT/'data/library.sqlite3'
MONITOR=ROOT/'data/monitor'
HEART='944a2977-fe34-4112-a990-e864c317287c'
CAG='38f201bf-025a-4eae-9e73-a78d13fa9b3c'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    run=OUT/('run-'+uuid4().hex[:10]);run.mkdir()
    target=run/'library.sqlite3';directory=run/'monitor'
    before_files={str(p.relative_to(MONITOR)):sha(p) for p in (MONITOR/'sessions').glob('*/*') if p.is_file()}
    with closing(sqlite3.connect(DATABASE.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;before=audit(db)
        with closing(sqlite3.connect(target)) as dest:db.backup(dest)
        official=dict(db.execute('SELECT * FROM official_song_sources WHERE song_id=4343').fetchone())
        gd=dict(db.execute("SELECT * FROM game_difficulties WHERE song_id=4343 AND difficulty='HARD'").fetchone())
        imported=dict(db.execute('SELECT * FROM official_import_runs WHERE import_id=?',(official['import_id'],)).fetchone())
    csvpath=Path(imported['source_name']);csv=read_catalog(csvpath.read_bytes(),str(csvpath))
    raw=next(r for r in csv.rows if r['raw']['fileId']==official['file_id'] and r['raw']['gn']==official['gn'])
    assert raw['raw']==json.loads(official['raw_row_json']) and raw['raw']['notesHard']=='1194'
    assert csv.sha256==imported['csv_sha256']
    original_session=json.loads((MONITOR/'sessions'/HEART/'session.json').read_text(encoding='utf8'))
    stored=json.loads((MONITOR/'sessions'/HEART/'result-review.json').read_text(encoding='utf8'))
    analyzer=Analyzer(LocalOCREngine(ROOT/'.ocr-models',low_latency=True))
    samples={};ocr={}
    for name,path in [('heart',MONITOR/'sessions'/HEART/'result.png'),
                      ('cagayake',MONITOR/'sessions'/CAG/'result.png'),
                      ('multiplayer',ROOT/'tests/專案測試圖片/多人遊戲結算畫面.PNG')]:
        started=time.perf_counter()
        with Image.open(path) as image:observation=analyzer.analyze(image)
        elapsed=round((time.perf_counter()-started)*1000,2)
        ocr[name]=observation['result_ocr']
        samples[name]=dict(path=str(path),sha256=sha(path),ocr_ms=elapsed,
            state=observation['state'],fields=ocr[name]['fields'],local_player_status=ocr[name]['local_player_status'])
    assert {k:ocr['heart']['fields'][k]['value'] for k in ('perfect','cool','bad','miss')}==dict(perfect=1145,cool=30,bad=7,miss=10)
    assert all(ocr['heart']['fields'][k]['value']==stored['ocr']['fields'][k]['value'] for k in ('perfect','cool','bad','miss'))
    reviews=ResultReviews(target,directory);submissions=SubmissionService(LibraryService(target),reviews)
    atomic_json(directory/'catalog-location.json',dict(database_path=str(target.resolve())))
    if (MONITOR/'manual-references.json').exists():shutil.copyfile(MONITOR/'manual-references.json',directory/'manual-references.json')

    def replay(sid,parsed):
        folder=directory/'sessions'/sid;folder.mkdir(parents=True)
        source=MONITOR/'sessions'/sid
        session=json.loads((source/'session.json').read_text(encoding='utf8'))
        session['evidence_source']='ISOLATED saved-image replay, not a new gameplay test'
        shutil.copyfile(source/'result.png',folder/'result.png');atomic_json(folder/'session.json',session)
        row=reviews.ingest(session,copy.deepcopy(parsed))
        return dict(session_id=sid,review_id=row['review_id'],revision=0,
            values=copy.deepcopy(row['draft']['values']),song_id=None,root_id=None,relative_path=None,
            player_verified=False,identity_reviewed=False,notes_verified=False,duration_verified=False,
            confirm=False,confirmation_note='ISOLATED TEST: supplied human game-context replay')

    heart=replay(HEART,ocr['heart'])
    assert heart['values']['total_notes'] is None
    heart['values']['difficulty']='HARD'  # User's stated chart; not recovered OCR.
    assisted=reviews.assist({**heart,'official_source_confirmed':True})
    assert assisted['selected_song']['song_id']==4343 and assisted['patch']==dict(total_notes=1194)
    heart.update(song_id=4343,root_id=assisted['selected_song']['root_id'],relative_path=assisted['selected_song']['relative_path'],
                 player_verified=True,identity_reviewed=True)
    heart['values'].update(assisted['patch'])
    preview=reviews.preview(heart)
    assert preview['note_sources']['selected_total_notes'] is None
    assert preview['note_sources']['conditional_score']['display']=='MAX -68'
    assert not preview['can_confirm'] and preview['display'] is None
    saved=reviews.save(heart);heart['revision']=saved['revision']
    fresh=ResultReviews(target,directory).detail(HEART)
    assert fresh['draft']['values']['total_notes']==1194 and fresh['preview']['note_sources']['judged_total']==1192
    denied=[]
    for action in ('confirm','submit'):
        try:
            if action=='confirm':reviews.save({**heart,'confirm':True,'acknowledge':True})
            else:submissions.submit({**heart,'play_mode':'single','submit_acknowledge':True})
        except LibraryError as error:denied.append(dict(action=action,error=str(error)))
        else:raise AssertionError('Unverified discrepancy must remain blocked')
    assert len(denied)==2

    # A consistent existing real screenshot still confirms and submits on COPY.
    cag=replay(CAG,ocr['cagayake']);s=next(c for c in reviews.candidates('Cagayake','custom',limit=100)['candidates']if c['song_id']==16)
    cag.update(song_id=16,root_id=s['root_id'],relative_path=s['relative_path'],player_verified=True,
        identity_reviewed=True,notes_verified=True,duration_verified=True,confirm=True,acknowledge=True)
    cag['values'].update(difficulty='HARD',game_level=20,total_notes=838,duration_seconds=88)
    saved=reviews.save(cag);cag.update(revision=saved['revision'],play_mode='single')
    prepared=submissions.prepare(cag);assert prepared['can_submit'],prepared['issues']
    request={**cag,'submission_token':prepared['submission_token'],'submit_acknowledge':True}
    receipt=submissions.submit(request);duplicate=submissions.submit(request)
    assert receipt['record']['display']=='MAX -3' and receipt['record']['lamp_status']=='AC' and duplicate['idempotent']
    assert receipt['record']['evidence']['note_sources']['verified_total_notes']==838
    with closing(sqlite3.connect(target)) as db:
        check_integrity(db)
        assert db.execute('select count(*) from play_records').fetchone()[0]==1
        assert db.execute('select count(*) from songs').fetchone()[0]==before['songs']['count']
    with closing(sqlite3.connect(DATABASE.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        assert audit(db)==before
    after_files={str(p.relative_to(MONITOR)):sha(p) for p in (MONITOR/'sessions').glob('*/*') if p.is_file()}
    assert before_files==after_files
    report=dict(phase='5A.1',run_path=str(run.resolve()),database=str(target.resolve()),monitor_directory=str(directory.resolve()),
        evidence_type='Original saved screenshot OCR + isolated catalog simulated confirmation; no fresh gameplay',
        csv=dict(path=str(csvpath),sha256=csv.sha256,imported_sha256=imported['csv_sha256'],original_record=raw['record_number'],raw=raw['raw']),
        heart=dict(official_source=official,database_difficulty=gd,
            selected_ocr_available=False,selected_notes_user_report=1194,selected_duration_user_report=93,
            session_selection=original_session['selection'],saved_judgments={k:stored['ocr']['fields'][k] for k in ('perfect','cool','bad','miss')},
            fresh_judged_total=judged({k:v['value'] for k,v in ocr['heart']['fields'].items()}),
            note_sources=preview['note_sources'],preview=preview,blocked_operations=denied,
            displayed_game_hit_rate_observed_visually='98.57%',
            conditional_hit_rate_arithmetic=round((1145+30)/1192*100,2),
            hit_rate_formula_verified=False,game_counting_difference_reason='unconfirmed'),
        samples=samples,copy_submit=dict(record_id=receipt['record']['record_id'],display=receipt['record']['display'],lamp=receipt['record']['lamp_status'],idempotent=duplicate['idempotent']),
        production_unchanged=True,production_session_and_review_files_unchanged=True,production_tables=before,
        schema_version=6,migrations=[],fresh_gameplay_tested=False)
    atomic_json(run/'investigation.json',report);atomic_json(OUT/'latest.json',report)
    atomic_json(run/'ocr.json',ocr)
    print(json.dumps(dict(run=str(run),heart_ocr_ms=samples['heart']['ocr_ms'],copy_submit=report['copy_submit'],production_unchanged=True),ensure_ascii=False))


if __name__=='__main__':main()
