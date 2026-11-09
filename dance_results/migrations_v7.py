"""Replace only formal validation triggers and the bests view; preserve tables."""
import json
from datetime import datetime,timezone
from .migrations_v6 import FORMAL_INVALID
from .sqlite_safety import audit,projection,check_integrity,verified_backup

# Pending lamp exceptions have a confirmed score, but NULL never ranks as a lamp.
ELIGIBLE="""r.match_status='confirmed' AND (r.validation_status='verified' OR
 (r.validation_status='pending' AND r.confirmation_method='manual_note_exception'
  AND r.lamp_status IS NULL AND json_extract(r.evidence_json,'$.note_exception.authorized')=1))"""

EXCEPTION="""(NEW.confirmation_method='manual_note_exception'
 AND json_extract(NEW.evidence_json,'$.note_exception.authorized') IS 1
 AND json_extract(NEW.evidence_json,'$.note_exception.scope')='this_session_and_review_only'
 AND json_extract(NEW.evidence_json,'$.note_exception.difference')=NEW.perfect+NEW.cool+NEW.bad+NEW.miss-NEW.total_notes
 AND json_extract(NEW.evidence_json,'$.note_sources.verified_total_notes')=NEW.total_notes
 AND json_extract(NEW.evidence_json,'$.note_sources.selected_database_conflict') IS 0
 AND json_extract(NEW.evidence_json,'$.note_exception.review.judgments') IS 1
 AND json_extract(NEW.evidence_json,'$.note_exception.review.identity') IS 1
 AND json_extract(NEW.evidence_json,'$.note_exception.review.source_confirmed') IS 1
 AND json_extract(NEW.evidence_json,'$.note_exception.review.difference') IS 1)"""


def validation_sql():
    sql=FORMAL_INVALID.replace("NEW.confirmation_method IS NOT 'manual_review'", "NEW.confirmation_method NOT IN ('manual_review','manual_note_exception','verified_auto') OR NEW.confirmation_method IS NULL")
    sql=sql.replace("NEW.validation_status!='verified'", "(NEW.validation_status!='verified' AND NOT ("+EXCEPTION+" AND NEW.validation_status='pending' AND NEW.lamp_status IS NULL AND NEW.bad=0 AND NEW.miss=0))")
    sql=sql.replace("NEW.review_revision<1", "(NEW.review_revision<1 AND NEW.confirmation_method!='verified_auto') OR NEW.review_revision<0")
    sql=sql.replace('NEW.total_notes!=NEW.perfect+NEW.cool+NEW.bad+NEW.miss', '(NEW.total_notes!=NEW.perfect+NEW.cool+NEW.bad+NEW.miss AND NOT '+EXCEPTION+')')
    sql=sql.replace('NEW.total_notes<NEW.perfect+NEW.cool+NEW.bad+NEW.miss', '(NEW.total_notes<NEW.perfect+NEW.cool+NEW.bad+NEW.miss AND NOT '+EXCEPTION+')')
    extra=""" OR (NEW.confirmation_method='manual_note_exception' AND NOT EXC)
 OR (NEW.confirmation_method='verified_auto' AND (json_extract(NEW.evidence_json,'$.automatic.authorized') IS NOT 1
 OR json_extract(NEW.evidence_json,'$.note_exception.authorized') IS 1))
 OR (NEW.lamp_status IS NULL AND NOT (EXC AND NEW.validation_status='pending' AND NEW.bad=0 AND NEW.miss=0))
""".replace('EXC',EXCEPTION)
    return sql.rstrip()[:-1]+extra+')'


def migrate_v6(db,path,*,new_database=False):
    if db.in_transaction:raise ValueError('Migration requires its own transaction.')
    try:
        db.execute('BEGIN IMMEDIATE');check_integrity(db)
        columns=projection(db);before=audit(db,columns)
        backup=None if new_database else verified_backup(db,path,'pre-v7')
        for name in ('formal_record_valid_insert','formal_record_valid_update'):
            db.execute('DROP TRIGGER '+name)
            operation='INSERT' if name.endswith('insert') else 'UPDATE'
            db.execute('CREATE TRIGGER '+name+' BEFORE '+operation+' ON play_records WHEN '+validation_sql()+" BEGIN SELECT RAISE(ABORT,'formal submission is incomplete or inconsistent'); END")
        db.execute('DROP VIEW difficulty_bests')
        db.execute("""CREATE VIEW difficulty_bests AS SELECT d.difficulty_id,d.song_id,d.difficulty,
 MAX(CASE WHEN r.completion_status='COMPLETED' THEN r.ex_score END) AS highest_ex_score,
 CASE MAX(CASE r.lamp_status WHEN 'ACP' THEN 4 WHEN 'AC' THEN 3 WHEN 'CLEAR' THEN 2 WHEN 'FAILED' THEN 1 ELSE 0 END)
 WHEN 4 THEN 'ACP' WHEN 3 THEN 'AC' WHEN 2 THEN 'CLEAR' WHEN 1 THEN 'FAILED' ELSE 'UNPLAYED' END AS best_clear_lamp
 FROM game_difficulties d LEFT JOIN play_records r ON r.difficulty_id=d.difficulty_id AND ("""+ELIGIBLE+") GROUP BY d.difficulty_id")
        after=audit(db,columns);check_integrity(db)
        if before!=after:raise ValueError('Existing rows changed; migration rolled back.')
        result=dict(from_version=6,to_version=7,backup_path=backup,before=before,after=after,all_ids_values_and_history_preserved=True)
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(7,datetime.now(timezone.utc).isoformat(),backup,json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=7');db.commit();return result
    except BaseException:db.rollback();raise
