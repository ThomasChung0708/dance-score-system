"""Read-only final audit of the Phase 5B GUI exercise and production catalog."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import time

from dance_results.library_service import LibraryService
from dance_results.sqlite_safety import audit, check_integrity

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/phase5b'


def main():
    before=json.loads((OUT/'before.json').read_text(encoding='utf8'))
    production=ROOT/'data/library.sqlite3'
    with closing(sqlite3.connect(production.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        check_integrity(db)
        after=dict(schema=db.execute('PRAGMA user_version').fetchone()[0],tables=audit(db),
                   schema_sql=[list(r) for r in db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name')])
    files={str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted((ROOT/'data/monitor/sessions').rglob('*')) if p.is_file()}
    changed_files=[p for p,h in before['session_files'].items() if files.get(p)!=h]
    new_files=sorted(set(files)-set(before['session_files']))
    assert after['schema']==6
    assert after['tables']==before['tables'],'Production tables changed; investigate, never restore automatically.'
    assert after['schema_sql']==before['schema_sql'],'Schema changed.'
    assert not changed_files,'Existing Session evidence changed.'

    fixture=json.loads((OUT/'gui-fixture.json').read_text(encoding='utf8'))
    with closing(sqlite3.connect(Path(fixture['database']).as_uri()+'?mode=ro',uri=True)) as db:
        check_integrity(db);db.row_factory=sqlite3.Row
        records=[dict(r) for r in db.execute('SELECT r.record_id,d.song_id,r.difficulty_id,r.ex_score,r.lamp_status,r.evidence_json FROM play_records r JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id ORDER BY r.record_id')]
    assert len(records)==8
    assert [r['lamp_status'] for r in records]==['CLEAR','FAILED','AC','ACP','ACP','CLEAR','ACP','FAILED']
    for r in records:r['feedback']=json.loads(r.pop('evidence_json'))['achievement_feedback']
    assert records[2]['feedback']['new_lamp'] and not records[2]['feedback']['new_score']
    assert records[3]['feedback']['score_delta']==276
    assert records[4]['feedback']['events']==['SAVED']
    assert records[-1]['feedback']['best_lamp']=='ACP' and records[-1]['feedback']['best_ex_score']==1676
    assert all(r['song_id']==16 for r in records),'Unexpected submission including Heart.'
    service=LibraryService(Path(fixture['database']))
    progress=service.detail(1,16)['progress'];hard=next(p for p in progress if p['difficulty']=='HARD')
    assert (hard['best_ex_score'],hard['best_lamp'],hard['latest_record']['lamp_status'])==(1676,'ACP','FAILED')
    measurements=[]
    for _ in range(5):
        start=time.perf_counter();service.songs(1);measurements.append((time.perf_counter()-start)*1000)

    gui=json.loads((OUT/'gui-checks.json').read_text(encoding='utf8'))
    checks={r['case']:r for r in gui}
    for name in ('UNPLAYED_to_CLEAR','CLEAR_to_AC','AC_to_ACP_and_NEW_SCORE'):
        assert 'lamp-upgrade' in [a['name'] for a in checks[name]['animations']]
    assert checks['equal_score_SAVED']['event']['events']==['SAVED']
    assert {a['name'] for a in checks['reduced_motion_upgrade']['animations']}=={'fx-fade'}
    failed=checks['FAILED_does_not_replace_ACP']
    assert 'lamp-failed' in [a['name'] for a in failed['animations']]
    assert not {'lamp-upgrade','lamp-ring','lamp-shine'} & {a['name'] for a in failed['animations']}
    blocked=checks['Heart_notes_mismatch_no_success']
    assert blocked['confirmDisabled'] and blocked['submitDisabled'] and not blocked['toastVisible']
    assert all(p['lamp']['entryKind']=='entry' for p in checks['reload_persists_bests_without_replaying_upgrade']['value']['details'])
    assert not checks['review_reload_does_not_replay_success']['value']['toast']
    log=(OUT/'unittest.log').read_text(encoding='utf8');matched=re.search(r'Ran (\d+) tests in ([\d.]+)s',log)
    assert matched and log.rstrip().endswith('OK')
    result=dict(version='0.10.0',generated_at=datetime.now(timezone.utc).isoformat(),schema=6,
        unittest=dict(total=int(matched[1]),seconds=float(matched[2]),passed=True),
        production=dict(tables_unchanged=True,schema_unchanged=True,existing_evidence_unchanged=True,
                        new_monitor_files=new_files,after=after),
        isolated_gui=dict(database=fixture['database'],synthetic_records=records,progress=progress,
                          browser_checks=gui,integrity='ok',foreign_keys=[]),
        timings=dict(kind='five sequential local LibraryService.songs calls, 60-song page on full catalog copy',
                     milliseconds=measurements,median_ms=statistics.median(measurements),fps_cpu_memory_not_measured=True),
        real_gameplay_this_phase=False)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(tests=result['unittest'],production_unchanged=True,new_monitor_files=len(new_files),
                         gui_records=len(records),median_page_ms=result['timings']['median_ms']),ensure_ascii=False))


if __name__=='__main__':main()

