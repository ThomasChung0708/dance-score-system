"""Read-only audits; accepts documented concurrent user additions, never restores."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from dance_results.library_service import LibraryService
from dance_results.sqlite_safety import audit,check_integrity
from dance_results.submissions import SubmissionService
from dance_results.result_review import ResultReviews

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5c'


def open_ro(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def main():
    baseline=json.loads((OUT/'before.json').read_text(encoding='utf8'))
    with closing(open_ro(ROOT/'data/library.sqlite3')) as db,closing(open_ro(OUT/'baseline.sqlite3')) as original:
        check_integrity(db);after=audit(db)
        assert db.execute('PRAGMA user_version').fetchone()[0]==7
        identities={}
        for table,cols in dict(packs='pack_id,root_id,relative_path,path_key,name',
            songs='song_id,root_id,pack_id,relative_folder,folder_key',
            song_sources='source_id,song_id,relative_path,path_key',
            official_song_sources='official_source_id,song_id,file_id,catalog_key',
            game_difficulties='difficulty_id,song_id,difficulty').items():
            # Official source PK is schema-defined; don't invent a new identity.
            if table=='official_song_sources':cols='song_id,file_id,catalog_key,mode'
            old={r[0]:tuple(r) for r in original.execute('SELECT '+cols+' FROM '+table)}
            now={r[0]:tuple(r) for r in db.execute('SELECT '+cols+' FROM '+table)}
            assert all(now.get(k)==v for k,v in old.items()),table+' existing identity changed'
            identities[table]=dict(existing_preserved=len(old),current=len(now))
        migration=json.loads(db.execute('SELECT audit_json FROM schema_migrations WHERE version=7').fetchone()[0])
        assert migration['before']==migration['after']
        with closing(open_ro(Path(migration['backup_path']))) as backup:
            check_integrity(backup);assert audit(backup)==migration['before']
        production_sessions={r[0] for r in db.execute('SELECT session_id FROM play_records')}
    files={str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT/'data/monitor/sessions').rglob('*') if p.is_file()}
    existing_changed=[p for p,h in baseline['session_files'].items() if files.get(p)!=h]
    assert not [p for p in existing_changed if p.endswith('.png')],'Original screenshot changed'
    manifest=json.loads((OUT/'gui/manifest.json').read_text(encoding='utf8'))
    for name,payload in manifest['payloads'].items():
        if payload.get('synthetic_values'):continue
        original_sid=payload['session']['isolated_replay']['original_session']
        actual=json.loads((ROOT/'data/monitor/sessions'/original_sid/'result-review.json').read_text(encoding='utf8'))
        assert actual['ocr']==payload['ocr'],'Original OCR changed: '+original_sid
    fixture_sessions={v['session_id'] for v in manifest['payloads'].values()}
    assert not fixture_sessions & production_sessions,'Isolated test leaked into production'
    service=LibraryService(Path(manifest['database']));store=SubmissionService(service,ResultReviews(service.path,Path(manifest['monitor_directory'])))
    records=store.history()['records'];assert len(records)==4
    records=sorted([store.detail(r['record_id']) for r in records],key=lambda r:r['record_id'])
    heart,auto,pending,repeated=records
    assert [heart[k] for k in ('song_id','perfect','cool','bad','miss','ex_score','max_score','lamp_status')]==[4343,1145,30,7,10,2320,2388,'CLEAR']
    assert heart['note_exception']['authorized'] and heart['evidence']['note_sources']['selected_total_notes'] is None
    assert auto['confirmation_method']=='verified_auto' and pending['lamp_status'] is None
    assert pending['validation_status']=='pending' and 'NEW_LAMP' not in pending['achievement_feedback']['events']
    assert repeated['achievement_feedback']['events']==['SAVED']
    progress=next(p for p in service.detail(1,483)['progress'] if p['difficulty']=='HARD')
    assert (progress['best_ex_score'],progress['best_lamp'],progress['best_score_record']['record_id'],progress['best_lamp_record_id'])==(2502,'CLEAR',3,2)
    real=json.loads((OUT/'real-ocr.json').read_text(encoding='utf8'));assert all(r['judgments_match_expected'] for r in real)
    gui=json.loads((OUT/'gui-checks.json').read_text(encoding='utf8'));checks={r['case']:r['value'] for r in gui}
    assert json.loads(checks['pending_lamp_no_acp_upgrade']['event'])['events']==['NEW_SCORE']
    assert checks['new_tab_does_not_replay_automatic_achievement']['deliveries']=='0'
    assert checks['restart_preserves_mode_and_records']['mode']=='VERIFIED_AUTO'
    log=(OUT/'unittest.log').read_text(encoding='utf8');m=re.search(r'Ran (\d+) tests in ([\d.]+)s',log)
    assert m and log.rstrip().endswith('OK')
    result=dict(version='0.11.0',schema=7,as_of=datetime.now(timezone.utc).isoformat(),
        tests=dict(count=int(m[1]),seconds=float(m[2]),passed=True),real_ocr_results=real,
        production=dict(before=baseline['tables'],after=after,identity_checks=identities,
            migration_backup_verified=True,migration_preserved_all_rows=True,
            concurrent_user_changes=True,changed_existing_sidecars=existing_changed,
            new_monitor_files=sorted(set(files)-set(baseline['session_files'])),
            original_screenshots_preserved=True,test_sessions_absent=True),
        isolated=dict(records=records,progress=progress,gui_checks=gui),
        new_gameplay_e2e_verified=False)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(tests=result['tests'],production_counts={k:v['count'] for k,v in after.items()},
                         existing_identities_preserved=True,isolated_records=len(records)),ensure_ascii=False))


if __name__=='__main__':main()
