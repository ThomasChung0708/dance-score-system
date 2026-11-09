"""Explicit production migration audit; never creates/submits gameplay records."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from dance_results.database import LibraryDatabase
from dance_results.sqlite_safety import audit
from dance_results.achievements import progress_for_songs
from dance_results.recent_updates import recent_updates

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/phase5d2'


def read(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def sidecars():
    return {str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT/'data/monitor').rglob('*') if p.is_file()}


def run():
    path=ROOT/'data/library.sqlite3'
    with closing(read(path)) as db:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (9,10):raise ValueError(f'Expected schema9/10, got {version}.')
        if version==10:
            migration=json.loads(db.execute('SELECT audit_json FROM schema_migrations WHERE version=10').fetchone()[0])
            with closing(read(Path(migration['backup_path']))) as backup:
                before=audit(backup)
                records=backup.execute('SELECT * FROM play_records ORDER BY record_id').fetchall()
        else:
            before=audit(db)
            records=db.execute('SELECT * FROM play_records ORDER BY record_id').fetchall()
    files=sidecars()
    with LibraryDatabase(path) as lib:
        if lib.migration_results:migration=lib.migration_results[-1]
        after=audit(lib.connection)
        assert {k:v for k,v in before.items() if k!='schema_migrations'}=={k:v for k,v in after.items() if k!='schema_migrations'}
        assert records==[tuple(r) for r in lib.connection.execute('SELECT * FROM play_records ORDER BY record_id')]
        assert files==sidecars()
        assert [r[0] for r in lib.connection.execute('PRAGMA integrity_check')]==['ok']
        assert not lib.connection.execute('PRAGMA foreign_key_check').fetchall()
        progress=next(p for p in progress_for_songs(lib.connection,[2726])[2726] if p['difficulty']=='HARD')
        assert (progress['best_ex_score'],progress['best_score_record']['record_id'],progress['best_lamp'],progress['best_lamp_record_id'])==(3174,7,'CLEAR',7)
        recent=recent_updates(lib.connection)
        fake=json.loads((OUT/'gui/manifest.json').read_text(encoding='utf8'))
        assert not lib.connection.execute('SELECT 1 FROM play_records WHERE session_id=?',(fake['pending_session'],)).fetchone()
        assert not lib.connection.execute('SELECT 1 FROM packs WHERE name=?',('Isolated EX Lamp',)).fetchone()
    with closing(read(Path(migration['backup_path']))) as db:
        assert audit(db)==before
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    result=dict(version='0.14.0',schema_before=9,schema_after=10,migration=migration,before=before,after=after,
                existing_records_unchanged=len(records),monitor_files_unchanged=len(files),integrity_check='ok',foreign_key_errors=0,
                backup_verified=True,generated_test_data_absent=True,zhou_hard=progress,recent_updates=recent,
                new_real_gameplay_test=False,old_achievement_events_unchanged=True)
    (OUT/'production-migration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(schema=10,records_preserved=len(records),monitor_files_preserved=len(files),backup=migration['backup_path'],my_best=3174,best_lamp='CLEAR'),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--migrate-production',action='store_true',required=True);p.parse_args();run()
