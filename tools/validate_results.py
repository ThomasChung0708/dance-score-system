"""Phase 4C real-image evaluation plus isolated SQLite-copy save rehearsal.

No gameplay is simulated as live evidence. All PlayRecord mutations are confined
to a newly created marked subdirectory of reports/phase4c/sandbox/.
"""
from contextlib import closing
from pathlib import Path
import copy
import hashlib
import json
import shutil
import sqlite3
import time
from uuid import uuid4, uuid5, NAMESPACE_URL

from PIL import Image

from dance_results.monitor_ocr import Analyzer, active_pack
from dance_results.monitor_tracking import atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_screens import parse_selection
from dance_results.result_review import ResultReviews
from dance_results.sqlite_safety import audit, check_integrity

ROOT=Path(__file__).resolve().parent.parent
OUTPUT=ROOT/'reports/phase4c'
PRODUCTION=ROOT/'data/library.sqlite3'


def sandbox_save(path, review, force_failure=False):
    path=path.resolve()
    assert path.is_relative_to((OUTPUT/'sandbox').resolve()) and path != PRODUCTION.resolve()
    assert (path.parent/'phase4c-sandbox.json').is_file()
    assert review['status']=='confirmed_manual' and review['preview']['can_confirm']
    draft=review['draft'];v=draft['values'];sid=review['session_id'];p=review['preview']
    digest=hashlib.sha256(json.dumps(draft,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        try:
            db.execute('CREATE TABLE IF NOT EXISTS phase4c_result_receipts(session_id TEXT PRIMARY KEY, result_id TEXT UNIQUE NOT NULL, screenshot_sha256 TEXT NOT NULL, payload_hash TEXT NOT NULL)')
            old=db.execute('SELECT result_id,screenshot_sha256,payload_hash FROM phase4c_result_receipts WHERE session_id=?',(sid,)).fetchone()
            if old:
                if old!=(review['review_id'],review['screenshot_sha256'],digest):
                    raise ValueError('Existing session receipt conflicts with this review')
                record_id=db.execute('SELECT record_id FROM play_records WHERE session_id=?',(sid,)).fetchone()[0]
                db.commit();return record_id
            db.execute('INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,notes_verified,notes_source,level_source) VALUES(?,?,?,?,1,\'manual\',\'manual\') ON CONFLICT(song_id,difficulty) DO NOTHING',
                (draft['song_id'],v['difficulty'],v['game_level'],v['total_notes']))
            gd=db.execute('SELECT difficulty_id,game_level,total_notes FROM game_difficulties WHERE song_id=? AND difficulty=?',(draft['song_id'],v['difficulty'])).fetchone()
            if gd[1:]!=(v['game_level'],v['total_notes']):raise ValueError('Sandbox difficulty conflict')
            db.execute('INSERT INTO play_sessions(session_id,player_name,play_mode,source_type,local_difficulty,local_game_level,display_song_title,display_pack_name,room_status,player_status,evidence_json) VALUES(?,?,\'single\',\'custom\',?,?,?,?,\'result\',?,?)',
                (sid,review['ocr']['fields']['player_name']['value'],v['difficulty'],v['game_level'],
                 draft['song_identity']['title'],draft['song_identity']['pack_name'],
                 'failed' if v['completion_status']=='FAILED' else 'completed',json.dumps({'sandbox_only':True,'review_id':review['review_id']})))
            percentage=p['ex_score']/p['max_score']*100
            record_id=db.execute('''INSERT INTO play_records(session_id,difficulty_id,played_at,completion_status,
                perfect,cool,bad,miss,ex_score,max_score,percentage,lamp_status,total_notes,notes_source,
                match_status,validation_status,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'game_screen','confirmed','verified',?)''',
                (sid,gd[0],review['locked_session']['started_at'],v['completion_status'],v['perfect'],v['cool'],v['bad'],v['miss'],
                 -1 if force_failure else p['ex_score'],p['max_score'],percentage,p['lamp_preview'],v['total_notes'],
                 json.dumps({'sandbox_only':True,'result_id':review['review_id'],'screenshot_sha256':review['screenshot_sha256']}))).lastrowid
            db.execute('INSERT INTO phase4c_result_receipts VALUES(?,?,?,?)',(sid,review['review_id'],review['screenshot_sha256'],digest))
            db.commit();return record_id
        except BaseException:
            db.rollback();raise


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    truth=json.loads((ROOT/'tests/fixtures/phase4c_ground_truth.json').read_text(encoding='utf8'))['images']
    old_truth=json.loads((ROOT/'tests/fixtures/phase4a_ground_truth.json').read_text(encoding='utf8'))['images']
    # Create a baseline for a fresh checkout, preserving an existing run's starting evidence.
    if not (OUTPUT/'database-before.json').exists():
        with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            atomic_json(OUTPUT/'database-before.json',audit(db))
    if not (OUTPUT/'original-images.json').exists():
        atomic_json(OUTPUT/'original-images.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (ROOT/'tests/專案測試圖片').glob('*.PNG')})
    if not (OUTPUT/'new-images.json').exists():
        atomic_json(OUTPUT/'new-images.json',{name:row for name,row in old_truth.items()
                    if row['screen_type']=='spectating' or name in truth and truth[name].get('annotation_note')})
    analyzer=Analyzer(LocalOCREngine(ROOT/'.ocr-models',low_latency=True))
    rows=[];observations={}
    for path in sorted((ROOT/'tests/專案測試圖片').glob('*.PNG')):
        image=Image.open(path);start=time.perf_counter();observation=analyzer.analyze(image)
        observations[path.name]=observation
        checks=[dict(field='screen_type',expected=old_truth[path.name]['screen_type'],actual=observation['screen_type'],
                     correct=old_truth[path.name]['screen_type']==observation['screen_type'])]
        for k,wanted in old_truth[path.name].get('fields',{}).items():
            found=observation['selection']['fields'][k]['value']
            checks.append(dict(field='selection.'+k,actual=found,expected=wanted,correct=found==wanted))
        if path.name in truth:
            assert hashlib.sha256(path.read_bytes()).hexdigest()==truth[path.name]['sha256']
            for k,wanted in truth[path.name]['fields'].items():
                f=observation['result_ocr']['fields'][k]
                checks.append(dict(field='result.'+k,actual=f['value'],expected=wanted,correct=f['value']==wanted,
                    confidence=f['confidence'],needs_review=f['value'] is None or f['confidence']<.9 or bool(f.get('reason'))))
        rows.append(dict(image=path.name,kind='original_static_screenshot_not_live_gameplay',
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),ocr_ms=round((time.perf_counter()-start)*1000,2),
            checks=checks,observation=observation))
        print(path.name,round((time.perf_counter()-start)*1000),'ms',sum(c['correct']for c in checks),'/',len(checks),flush=True)
    (OUTPUT/'image-results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    # Compare real list captures, retaining the lack of authoritative hover/selection labels.
    raw=json.loads((ROOT/'reports/phase4a/raw-pages.json').read_text(encoding='utf8'))
    catalog=ReadOnlyCatalog(PRODUCTION);pack_rows=[]
    try:
        for name in ('1.PNG','相同歌名1.PNG'):
            image=Image.open(ROOT/'tests/專案測試圖片'/name).convert('RGB').resize((1024,800))
            selection=parse_selection(raw[name],image);chosen,evidence=active_pack(selection,image,catalog)
            pack_rows.append(dict(image=name,truth='open_list_selection_vs_hover_unlabelled',candidate=chosen,rows=evidence,
                                  confidence_state='CANDIDATE',automatic_confirmation_allowed=False))
        for name in ('pack-selection.png','pack-highlight.png'):
            path=ROOT/'reports/phase4b/real-game'/name
            if path.exists():
                observation=analyzer.analyze(Image.open(path))
                image=Image.open(path).convert('RGB').resize((1024,800))
                candidate,evidence=active_pack(observation['selection'],image,catalog)
                pack_rows.append(dict(image=str(path.relative_to(ROOT)),truth='user_tried_selection_hover_not_controlled',
                    candidate=candidate,rows=evidence,automatic_confirmation_allowed=False,confidence_state='CANDIDATE'))
    finally:catalog.close()
    (OUTPUT/'pack-analysis.json').write_text(json.dumps(dict(real_captures=pack_rows,closed_list_examples=['2.PNG','相同歌名2.PNG','相同歌名3.PNG'],
        missing=['Same visible Pack list with pointer outside list before selection','Same list hovering a different Pack without click',
                 'Same list immediately after clicking target Pack and moving pointer away','Closed list immediately after the confirmed selection'],
        conclusion='Color alone is never confirmed. Manual Pack confirmation is invalidated by reopening the list.'),ensure_ascii=False,indent=2),encoding='utf8')
    # Fresh complete catalog copy; the production path is never opened writable here.
    run=OUTPUT/'sandbox'/('run-'+uuid4().hex[:10]);run.mkdir(parents=True)
    target=run/'library.sqlite3';directory=run/'monitor'
    atomic_json(run/'phase4c-sandbox.json',dict(production_read_only=str(PRODUCTION),test_database=str(target)))
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        before=audit(source)
        with closing(sqlite3.connect(target)) as dest:source.backup(dest)
    service=ResultReviews(target,directory)
    # Import each original result with an explicitly synthetic replay session, never a guessed song binding.
    for name in truth:
        sid=str(uuid5(NAMESPACE_URL,'phase4c-static:'+truth[name]['sha256']))
        folder=directory/'sessions'/sid;folder.mkdir(parents=True)
        shutil.copyfile(ROOT/'tests/專案測試圖片'/name,folder/'result.png')
        session=dict(session_id=sid,status='pending_result',started_at='2026-10-22T00:00:00+00:00',song_id=None,
            selection=None,matching_status='pending_manual_review',result={'state':'RESULT','screenshot':'result.png'},
            evidence_source='static_image_replay_not_live_play')
        atomic_json(folder/'session.json',session);service.ingest(session,observations[name]['result_ocr'])
    sid='38f201bf-025a-4eae-9e73-a78d13fa9b3c'
    actual=ROOT/'reports/phase4b/real-game/sessions'/sid
    folder=directory/'sessions'/sid;folder.mkdir(parents=True)
    for name in ('session.json','result.png'):shutil.copyfile(actual/name,folder/name)
    session=json.loads((folder/'session.json').read_text(encoding='utf8'))
    result=analyzer.analyze(Image.open(folder/'result.png'))['result_ocr']
    row=service.ingest(session,result);detail=service.detail(sid)
    chosen=next(c for c in detail['candidates']if c['song_id']==16)
    body=dict(session_id=sid,review_id=row['review_id'],revision=0,song_id=16,root_id=chosen['root_id'],
        relative_path=chosen['relative_path'],values=copy.deepcopy(row['draft']['values']),player_verified=True,
        identity_reviewed=True,notes_verified=True,confirm=True,acknowledge=True)
    # Explicit test-only correction from the known 4B result: grade has weak OCR confidence.
    body['values']['grade']='A++'
    saved=service.save(body);again=service.save(body)
    restarted=ResultReviews(target,directory).detail(sid)
    assert saved['revision']==again['revision']==restarted['revision']==1
    first_id=sandbox_save(target,saved);second_id=sandbox_save(target,restarted)
    assert first_id==second_id
    rollback=copy.deepcopy(saved);rollback['session_id']=str(uuid4());rollback['review_id']=str(uuid4())
    with closing(sqlite3.connect(target)) as db:copy_before_failure=audit(db)
    try:sandbox_save(target,rollback,force_failure=True)
    except sqlite3.IntegrityError:pass
    else:raise AssertionError('Expected sandbox constraint failure')
    with closing(sqlite3.connect(target)) as db:
        assert audit(db)==copy_before_failure
        check_integrity(db)
        copy_record_count=db.execute('SELECT COUNT(*) FROM play_records').fetchone()[0]
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        production_unchanged=audit(db)==before
        assert production_unchanged
    result_checks=[c for r in rows for c in r['checks']if c['field'].startswith('result.')]
    historic=dict(kind='previous_4b_result_static_reanalysis_not_new_live_gameplay',session_id=sid,
        fields=result['fields'],preview=saved['preview'])
    summary=dict(version='0.8.0',static_images=len(rows),screen_classification_correct=sum(r['checks'][0]['correct']for r in rows),
        result_field_checks=len(result_checks),result_values_correct=sum(c['correct']for c in result_checks),
        result_values_correct_and_reliable=sum(c['correct']and not c['needs_review']for c in result_checks),
        result_value_accuracy=sum(c['correct']for c in result_checks)/len(result_checks),
        failures=[dict(image=r['image'],**c)for r in rows for c in r['checks']if not c['correct']],
        historic_result=historic,sandbox=dict(database=str(target),monitor_directory=str(directory),
            repeated_review_save_revision=again['revision'],repeated_sql_record_ids=[first_id,second_id],
            play_records=copy_record_count,rollback_verified=True,production_unchanged=production_unchanged),
        pack_automatic_confirmation=False,new_live_gameplay_tested=False)
    atomic_json(OUTPUT/'validation.json',summary)
    print('Finished:',summary['result_values_correct'],'/',len(result_checks),'result values; sandbox record IDs',first_id,second_id)


if __name__=='__main__':main()
