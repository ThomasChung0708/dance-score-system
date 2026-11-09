"""Verify production preservation and the explicitly isolated GUI experiment."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from dance_results.sqlite_safety import audit,check_integrity
from dance_results.library_service import LibraryService
from dance_results.result_review import ResultReviews
from dance_results.submissions import SubmissionService

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5c1'


def ro(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def main():
    baseline=json.loads((OUT/'before.json').read_text(encoding='utf8'))
    with closing(ro(ROOT/'data/library.sqlite3')) as db,closing(ro(OUT/'baseline.sqlite3')) as old:
        check_integrity(db);after=audit(db)
        assert db.execute('PRAGMA user_version').fetchone()[0]==8
        identity={}
        for table,cols in dict(packs='pack_id,root_id,relative_path,path_key,name',
            songs='song_id,root_id,pack_id,relative_folder,folder_key',
            song_sources='source_id,song_id,relative_path,path_key',
            official_song_sources='song_id,file_id,catalog_key,mode',
            game_difficulties='difficulty_id,song_id,difficulty').items():
            existing={r[0]:tuple(r) for r in old.execute('SELECT '+cols+' FROM '+table)}
            current={r[0]:tuple(r) for r in db.execute('SELECT '+cols+' FROM '+table)}
            assert all(current.get(k)==v for k,v in existing.items()),table
            identity[table]=dict(existing_preserved=len(existing),current=len(current))
        for table in ('play_records','play_sessions'):
            before_rows=old.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
            current=db.execute('SELECT * FROM '+table+' WHERE rowid<=? ORDER BY rowid',(len(before_rows),)).fetchall()
            assert before_rows==current,table+' old history changed'
        migration=json.loads(db.execute('SELECT audit_json FROM schema_migrations WHERE version=8').fetchone()[0])
        assert migration['before']==migration['after'] and migration['old_bests_unchanged']
        with closing(ro(Path(migration['backup_path']))) as backup:
            check_integrity(backup);assert audit(backup)==migration['before']
        production_sessions={r[0] for r in db.execute('SELECT session_id FROM play_records')}
    files={str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in (ROOT/'data/monitor/sessions').rglob('*') if p.is_file()}
    changed=[p for p,h in baseline['session_files'].items() if files.get(p)!=h]
    assert not [p for p in changed if p.endswith('.png')]
    manifest=json.loads((OUT/'gui/manifest.json').read_text(encoding='utf8'))
    fixture={p['session']['session_id'] for p in manifest['payloads'].values()}
    assert not fixture & production_sessions
    for payload in manifest['payloads'].values():
        sid=payload['session']['isolated_replay']['original_session']
        row=json.loads((ROOT/'data/monitor/sessions'/sid/'result-review.json').read_text(encoding='utf8'))
        assert row['ocr']==payload['ocr']
    service=LibraryService(Path(manifest['database']))
    store=SubmissionService(service,ResultReviews(service.path,Path(manifest['monitor_directory'])))
    with closing(ro(service.path)) as db:
        new_ids=[r[0] for r in db.execute('SELECT record_id FROM play_records WHERE record_id>8 ORDER BY record_id')]
        check_integrity(db)
    records=[store.detail(i) for i in new_ids]
    assert len(records)==3
    heart,pending,auto=records
    assert [heart[k] for k in ('song_id','perfect','cool','bad','miss','ex_score','max_score','lamp_status')]==[4343,1145,30,7,10,2320,None,'CLEAR']
    assert heart['catalog_total_notes']==1194 and heart['judged_total']==1192 and heart['max_basis_notes'] is None
    assert heart['note_exception']['authorized'] and 'NEW_SCORE' in heart['achievement_feedback']['events']
    assert pending['ex_score']==1673 and pending['lamp_status'] is None and pending['max_score'] is None
    assert 'NEW_LAMP' not in pending['achievement_feedback']['events']
    assert auto['confirmation_method']=='verified_auto' and auto['ex_score']==2461
    assert auto['max_score'] is None and auto['achievement_feedback']['events']==['SAVED']
    assert all(r['total_notes'] is None and r['percentage'] is None and r['max_difference'] is None for r in records)
    real=json.loads((OUT/'real-ocr.json').read_text(encoding='utf8'))
    assert len(real)==3 and all(r['judgments_match_expected'] for r in real)
    gui=json.loads((OUT/'gui-checks.json').read_text(encoding='utf8'))
    log=(OUT/'unittest.log').read_text(encoding='utf8');m=re.search(r'Ran (\d+) tests in ([\d.]+)s',log)
    assert m and log.rstrip().endswith('OK')
    result=dict(version='0.11.1',schema=8,as_of=datetime.now(timezone.utc).isoformat(),
        tests=dict(count=int(m[1]),seconds=float(m[2]),passed=True),real_ocr_results=real,
        production=dict(before=baseline['tables'],after=after,identity_checks=identity,
            original_eight_records_and_sessions_unchanged=True,migration_backup_verified=True,
            migration_preserved_all_rows=True,old_bests_unchanged=True,changed_existing_sidecars=changed,
            new_files=sorted(set(files)-set(baseline['session_files'])),original_screenshots_preserved=True,
            isolated_sessions_absent=True),isolated=dict(records=records,gui_checks=gui),
        new_gameplay_e2e_verified=False)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(tests=result['tests'],production_counts={k:v['count'] for k,v in after.items()},
        identities_preserved=True,isolated_new_records=len(records),old_records_unchanged=True)))


if __name__=='__main__':main()
