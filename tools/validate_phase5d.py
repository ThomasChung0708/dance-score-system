"""Audit production preservation, immutable refresh evidence, isolated GUI and tests."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from dance_results import __version__
from dance_results.sqlite_safety import audit,check_integrity,verified_backup
from dance_results.recent_updates import recent_updates

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5d'


def ro(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;return db


def main():
    before=json.loads((OUT/'before.json').read_text(encoding='utf8'))
    production=ROOT/'data/library.sqlite3'
    with closing(ro(production)) as db:
        check_integrity(db);after=audit(db);assert after==before['tables'],'Production table changed during this task'
        assert [r[0] for r in db.execute('PRAGMA integrity_check')]==['ok']
        assert db.execute('PRAGMA user_version').fetchone()[0]==9
        recent=recent_updates(db)
        assert len(recent['updates'])==8 and {r['record_id'] for r in recent['updates']}==set(range(1,9))
        baseline_ddl=db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    with closing(ro(OUT/'baseline.sqlite3')) as db:
        assert [tuple(r) for r in db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]==[tuple(r) for r in baseline_ddl]
    with closing(sqlite3.connect(production)) as db:
        try:db.execute('BEGIN IMMEDIATE');backup=verified_backup(db,production,'pre-phase5d-schema9')
        finally:db.rollback()
    with closing(ro(Path(backup))) as db:
        assert audit(db)==after;check_integrity(db)
        assert [r[0] for r in db.execute('PRAGMA integrity_check')]==['ok']
    files={str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data/monitor/sessions').rglob('*') if p.is_file()}
    assert all(files.get(p)==h for p,h in before['session_files'].items()),'Existing Session evidence modified'
    manifest=json.loads((OUT/'gui/manifest.json').read_text(encoding='utf8'))
    with closing(ro(Path(manifest['database']))) as db:
        check_integrity(db);records=[dict(r) for r in db.execute('SELECT * FROM play_records WHERE record_id>8 ORDER BY record_id')]
        assert len(records)==2
        assert [(r['perfect'],r['cool'],r['ex_score'],r['max_score']) for r in records]==[(1145,30,2320,2384),(1150,25,2325,2384)]
        assert all(r['difficulty_id']==records[0]['difficulty_id'] for r in records)
        new_evidence=[json.loads(r['evidence_json']) for r in records]
        assert new_evidence[0]['download_evidence']['scan_completed']
        assert new_evidence[1]['review']['ocr']['fields']['perfect']['value']==1145
        assert new_evidence[1]['achievement_feedback']['events']==['NEW_SCORE']
        isolated_recent=recent_updates(db)
        heart=[r for r in isolated_recent['updates'] if r['song_id']==4343]
        assert len(heart)==1 and heart[0]['improvement']==5 and heart[0]['record_id']==10
        assert isolated_recent['updates'][0]['record_id']==8 # Late replay never uses confirmation time.
    production_sessions={r['session_id'] for r in records}
    with closing(ro(production)) as db:
        assert not production_sessions & {r[0] for r in db.execute('SELECT session_id FROM play_records')}
        assert audit(db)==after
    stat=json.loads((OUT/'static-final/results.json').read_text(encoding='utf8'))
    assert stat['summary']['metrics']['screen_classification']['correct']==27
    image_hashes={r['image']['name']:r['image']['sha256'] for r in stat['images']}
    assert all(hashlib.sha256((ROOT/'tests/專案測試圖片'/name).read_bytes()).hexdigest()==h for name,h in image_hashes.items())
    log=(OUT/'unittest.log').read_text(encoding='utf8');match=re.search(r'Ran (\d+) tests in ([\d.]+)s',log)
    assert match and log.rstrip().endswith('OK')
    benchmark=json.loads((OUT/'benchmark.json').read_text(encoding='utf8'))
    checks=json.loads((OUT/'gui-checks.json').read_text(encoding='utf8'))
    result=dict(version=__version__,schema=9,schema_ddl_unchanged=True,new_tables=False,new_indexes=False,production_backup=backup,
        production_before=before['tables'],production_after=after,original_records_and_sessions_unchanged=True,
        integrity_check='ok',foreign_key_check=[],backup_integrity_check='ok',
        session_evidence_unchanged=True,original_pngs_unchanged=True,isolated_sessions_absent_from_production=True,
        tests=dict(total=int(match[1]),seconds=float(match[2]),all_passed=True),static=stat['summary'],
        original_validated_refreshes=recent,isolated_gui_records=[{k:v for k,v in r.items() if k!='evidence_json'} for r in records],
        isolated_recent=isolated_recent,benchmark=benchmark,gui=checks,
        new_live_multiplayer_download_e2e_verified=False,new_live_gameover_verified=False)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(version=__version__,schema=9,tests=result['tests'],production_preserved=True,backup=backup),ensure_ascii=False))

if __name__=='__main__':main()
