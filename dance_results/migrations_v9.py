"""Custom result MAX policy: validation and policy-separated bests, no row edits."""
import json
from datetime import datetime,timezone
from .migrations_v8 import ELIGIBLE,VALIDATION as OLD_VALIDATION
from .sqlite_safety import audit,projection,check_integrity,verified_backup

CUSTOM="json_extract(r.evidence_json,'$.scoring_version')='custom-judged-v1'"
RANKING_ELIGIBLE="("+ELIGIBLE+") AND (COALESCE("+CUSTOM+",0)=0 OR json_extract(r.evidence_json,'$.comparison.eligible') IS 1) AND ("+CUSTOM+" OR NOT EXISTS(SELECT 1 FROM play_records cr WHERE cr.difficulty_id=r.difficulty_id AND json_extract(cr.evidence_json,'$.scoring_version')='custom-judged-v1' AND json_extract(cr.evidence_json,'$.comparison.eligible') IS 1))"
# NULL version means legacy. SQL OR must explicitly coalesce the comparison.
RANKING_ELIGIBLE=RANKING_ELIGIBLE.replace('AND ('+CUSTOM+' OR NOT EXISTS','AND (COALESCE('+CUSTOM+',0)=1 OR NOT EXISTS')
IS_CUSTOM="COALESCE(json_extract(NEW.evidence_json,'$.scoring_version'),'')='custom-judged-v1'"


def validation_sql():
    sql=OLD_VALIDATION
    sql=sql.replace("NEW.validation_status!='pending' OR NEW.completion_status!='COMPLETED' OR NEW.bad!=0 OR NEW.miss!=0", "NEW.validation_status!='pending' OR NEW.completion_status!='COMPLETED' OR ((NEW.bad!=0 OR NEW.miss!=0) AND NOT ("+IS_CUSTOM+" AND json_extract(NEW.evidence_json,'$.comparison.eligible') IS 0))")
    sql=sql.replace("COALESCE(json_extract(NEW.evidence_json,'$.note_sources.note_count_difference'),0)!=0", "(COALESCE(json_extract(NEW.evidence_json,'$.note_sources.note_count_difference'),0)!=0 AND NOT ("+IS_CUSTOM+"))")
    # v8 independent MAX is retained for FAILED and historical import validation.
    extra=""" OR (CUSTOM AND (
 NEW.completion_status!='COMPLETED' OR
 json_extract(NEW.evidence_json,'$.max_basis_verification.policy') IS NOT 'custom-judged-v1' OR
 json_extract(NEW.evidence_json,'$.max_basis_verification.is_official_game_max') IS NOT 0 OR
 json_extract(NEW.evidence_json,'$.note_sources.judged_total') IS NOT NEW.perfect+NEW.cool+NEW.bad+NEW.miss OR
 json_extract(NEW.evidence_json,'$.lamp_verification.source') IS NOT 'system_result_judgments' OR
 (NEW.perfect+NEW.cool+NEW.bad+NEW.miss>0 AND (
  NEW.total_notes IS NOT NEW.perfect+NEW.cool+NEW.bad+NEW.miss OR NEW.max_score IS NOT 2*NEW.total_notes OR
  json_extract(NEW.evidence_json,'$.max_basis_verification.source') IS NOT 'completed_result_judged_total')) OR
 (json_extract(NEW.evidence_json,'$.comparison.eligible') IS NOT 1 AND (NEW.lamp_status IS NOT NULL OR NEW.confirmation_method='verified_auto')) OR
 (EXISTS(SELECT 1 FROM play_records pr WHERE pr.difficulty_id=NEW.difficulty_id AND pr.session_id!=NEW.session_id AND pr.completion_status='COMPLETED' AND pr.match_status='confirmed' AND pr.perfect+pr.cool+pr.bad+pr.miss!=NEW.perfect+NEW.cool+NEW.bad+NEW.miss)
  AND (json_extract(NEW.evidence_json,'$.comparison.eligible') IS NOT 0 OR NEW.lamp_status IS NOT NULL OR NEW.confirmation_method='verified_auto')) OR
 (json_extract(NEW.evidence_json,'$.comparison.eligible') IS 0 AND (json_extract(NEW.evidence_json,'$.achievement_feedback.new_score') IS 1 OR json_extract(NEW.evidence_json,'$.achievement_feedback.new_lamp') IS 1))
 ))""".replace('CUSTOM',IS_CUSTOM)
    return '('+sql+extra+')'


def migrate_v8(db,path,*,new_database=False):
    if db.in_transaction:raise ValueError('Migration requires an independent transaction.')
    try:
        db.execute('BEGIN IMMEDIATE');check_integrity(db)
        columns=projection(db);before=audit(db,columns)
        bests=db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id').fetchall()
        backup=None if new_database else verified_backup(db,path,'pre-v9')
        for name in ('formal_record_valid_insert','formal_record_valid_update'):
            db.execute('DROP TRIGGER '+name)
            operation='INSERT' if name.endswith('insert') else 'UPDATE'
            db.execute('CREATE TRIGGER '+name+' BEFORE '+operation+' ON play_records WHEN '+validation_sql()+" BEGIN SELECT RAISE(ABORT,'formal custom MAX or comparison evidence is incomplete'); END")
        old_view=db.execute("SELECT sql FROM sqlite_master WHERE name='difficulty_bests'").fetchone()[0]
        if ELIGIBLE not in old_view:raise ValueError('Unexpected bests view; migration rolled back.')
        db.execute('DROP VIEW difficulty_bests');db.execute(old_view.replace(ELIGIBLE,RANKING_ELIGIBLE))
        after=audit(db,columns);check_integrity(db)
        if before!=after or bests!=db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id').fetchall():raise ValueError('Old records or bests changed; rollback.')
        result=dict(from_version=8,to_version=9,backup_path=backup,before=before,after=after,all_ids_values_and_history_preserved=True,old_bests_unchanged=True)
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(9,datetime.now(timezone.utc).isoformat(),backup,json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=9');db.commit();return result
    except BaseException:db.rollback();raise
