"""Independent EX/lamp views and evidence guards. All historical rows immutable."""
import json
from datetime import datetime,timezone
from .migrations_v9 import validation_sql as previous_validation
from .bests_policy import BESTS_VIEW
from .sqlite_safety import audit,projection,check_integrity,verified_backup


def validation_sql():
    sql=previous_validation()
    sql=sql.replace("((NEW.bad!=0 OR NEW.miss!=0) AND NOT (COALESCE(json_extract(NEW.evidence_json,'$.scoring_version'),'')='custom-judged-v1' AND json_extract(NEW.evidence_json,'$.comparison.eligible') IS 0))",
                    "((NEW.bad!=0 OR NEW.miss!=0) AND json_extract(NEW.evidence_json,'$.comparison.eligible') IS NOT 0)")
    return '('+sql+" OR (NEW.review_id IS NOT NULL AND json_extract(NEW.evidence_json,'$.comparison.eligible') IS 0 AND (json_extract(NEW.evidence_json,'$.achievement_feedback.new_score') IS 1 OR json_extract(NEW.evidence_json,'$.achievement_feedback.new_lamp') IS 1)))"


def migrate_v9(db,path,*,new_database=False):
    if db.in_transaction:raise ValueError('Independent migration transaction required.')
    try:
        db.execute('BEGIN IMMEDIATE');check_integrity(db)
        columns=projection(db);before=audit(db,columns)
        old_bests=[tuple(r) for r in db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id')]
        backup=None if new_database else verified_backup(db,path,'pre-v10-independent-bests')
        for name in ('formal_record_valid_insert','formal_record_valid_update'):
            db.execute('DROP TRIGGER '+name)
            operation='INSERT' if name.endswith('insert') else 'UPDATE'
            db.execute('CREATE TRIGGER '+name+' BEFORE '+operation+' ON play_records WHEN '+validation_sql()+" BEGIN SELECT RAISE(ABORT,'formal result MAX or comparison evidence is incomplete'); END")
        db.execute('DROP VIEW difficulty_bests');db.execute(BESTS_VIEW)
        after=audit(db,columns);check_integrity(db)
        if before!=after:raise ValueError('Historical rows changed; migration rolled back.')
        new_bests=[tuple(r) for r in db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id')]
        changes=[dict(before=list(a),after=list(b)) for a,b in zip(old_bests,new_bests) if a!=b]
        result=dict(from_version=9,to_version=10,backup_path=backup,before=before,after=after,
            all_ids_values_and_history_preserved=True,derived_bests_changes=changes,
            explanation='EX proven arithmetic compatibility; FAILED eligible with proof; independent lamp aggregate. No old events rewritten or replayed.')
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(10,datetime.now(timezone.utc).isoformat(),backup,json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=10');db.commit();return result
    except BaseException:db.rollback();raise
