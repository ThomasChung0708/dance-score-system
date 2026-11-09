"""Separate confirmed judgments from optional independently verified MAX.

Only validation triggers and the aggregate view change. No table or old Record
is rewritten, including its stored MAX and historical lamp.
"""
import json
from datetime import datetime,timezone
from .migrations_v7 import ELIGIBLE as LEGACY_ELIGIBLE
from .sqlite_safety import audit,projection,check_integrity,verified_backup

ELIGIBLE="("+LEGACY_ELIGIBLE+""" OR (r.match_status='confirmed' AND r.validation_status='pending'
 AND r.lamp_status IS NULL AND json_extract(r.evidence_json,'$.result_verification.confirmed') IS 1))"""

VALIDATION="""NEW.review_id IS NOT NULL AND (
 NEW.confirmation_method IS NULL OR NEW.confirmation_method NOT IN ('manual_review','manual_note_exception','verified_auto') OR
 NEW.difficulty_id IS NULL OR NEW.match_status!='confirmed' OR
 NEW.validation_status NOT IN ('verified','pending') OR
 NEW.game_level IS NULL OR NEW.game_level<=0 OR NEW.max_combo IS NULL OR NEW.game_score IS NULL OR
 NEW.max_combo>NEW.perfect+NEW.cool OR
 typeof(NEW.perfect)!='integer' OR typeof(NEW.cool)!='integer' OR typeof(NEW.bad)!='integer' OR typeof(NEW.miss)!='integer' OR
 typeof(NEW.ex_score)!='integer' OR NEW.ex_score!=2*NEW.perfect+NEW.cool OR
 NEW.screenshot_path IS NULL OR NEW.screenshot_sha256 IS NULL OR length(NEW.screenshot_sha256)!=64 OR
 NEW.submission_hash IS NULL OR length(NEW.submission_hash)!=64 OR NEW.confirmed_at IS NULL OR
 NEW.review_revision IS NULL OR NEW.review_revision<0 OR (NEW.review_revision=0 AND NEW.confirmation_method!='verified_auto') OR
 json_extract(NEW.evidence_json,'$.result_verification.confirmed') IS NOT 1 OR
 (NEW.max_score IS NULL AND (NEW.total_notes IS NOT NULL OR NEW.percentage IS NOT NULL OR NEW.max_difference IS NOT NULL)) OR
 (NEW.max_score IS NOT NULL AND (NEW.total_notes IS NULL OR NEW.max_difference IS NULL OR NEW.percentage IS NULL OR
  NEW.max_score!=2*NEW.total_notes OR NEW.max_difference!=NEW.max_score-NEW.ex_score OR
  json_extract(NEW.evidence_json,'$.max_basis_verification.verified') IS NOT 1 OR
  json_extract(NEW.evidence_json,'$.note_sources.max_basis_notes') IS NOT NEW.total_notes)) OR
 (NEW.lamp_status IS NULL AND (NEW.validation_status!='pending' OR NEW.completion_status!='COMPLETED' OR NEW.bad!=0 OR NEW.miss!=0)) OR
 (NEW.lamp_status IN ('AC','ACP') AND (NEW.max_score IS NULL OR
  NEW.perfect+NEW.cool+NEW.bad+NEW.miss!=NEW.total_notes OR
  COALESCE(json_extract(NEW.evidence_json,'$.note_sources.note_count_difference'),0)!=0)) OR
 (NEW.confirmation_method='verified_auto' AND (json_extract(NEW.evidence_json,'$.automatic.authorized') IS NOT 1 OR
  COALESCE(json_extract(NEW.evidence_json,'$.note_sources.note_count_difference'),0)!=0)) OR
 (NEW.completion_status='COMPLETED' AND COALESCE(json_extract(NEW.evidence_json,'$.note_sources.note_count_difference'),0)!=0 AND (
  NEW.confirmation_method!='manual_note_exception' OR json_extract(NEW.evidence_json,'$.note_exception.authorized') IS NOT 1 OR
  json_extract(NEW.evidence_json,'$.note_exception.scope') IS NOT 'this_session_and_review_only' OR
  json_extract(NEW.evidence_json,'$.note_exception.difference') IS NOT json_extract(NEW.evidence_json,'$.note_sources.note_count_difference') OR
  json_extract(NEW.evidence_json,'$.note_exception.review.judgments') IS NOT 1 OR
  json_extract(NEW.evidence_json,'$.note_exception.review.identity') IS NOT 1 OR
  json_extract(NEW.evidence_json,'$.note_exception.review.source_confirmed') IS NOT 1 OR
  json_extract(NEW.evidence_json,'$.note_exception.review.difference') IS NOT 1)) OR
 NOT EXISTS(SELECT 1 FROM game_difficulties d WHERE d.difficulty_id=NEW.difficulty_id AND
  (d.game_level IS NULL OR d.game_level=NEW.game_level) AND
  (d.notes_verified=0 OR d.total_notes IS json_extract(NEW.evidence_json,'$.note_sources.catalog_total_notes')))
)"""


def migrate_v7(db,path,*,new_database=False):
    if db.in_transaction:raise ValueError('Migration requires an independent transaction.')
    try:
        db.execute('BEGIN IMMEDIATE');check_integrity(db)
        columns=projection(db);before=audit(db,columns)
        bests=db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id').fetchall()
        backup=None if new_database else verified_backup(db,path,'pre-v8')
        for name in ('formal_record_valid_insert','formal_record_valid_update'):
            db.execute('DROP TRIGGER '+name)
            operation='INSERT' if name.endswith('insert') else 'UPDATE'
            db.execute('CREATE TRIGGER '+name+' BEFORE '+operation+' ON play_records WHEN '+VALIDATION+" BEGIN SELECT RAISE(ABORT,'formal result or MAX evidence is incomplete'); END")
        old_view=db.execute("SELECT sql FROM sqlite_master WHERE name='difficulty_bests'").fetchone()[0]
        # Replace the prior eligibility clause without changing ranking rules.
        if LEGACY_ELIGIBLE not in old_view:raise ValueError('Unexpected prior bests view; no changes applied.')
        db.execute('DROP VIEW difficulty_bests');db.execute(old_view.replace(LEGACY_ELIGIBLE,ELIGIBLE))
        after=audit(db,columns);check_integrity(db)
        if before!=after or bests!=db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id').fetchall():
            raise ValueError('Existing records or bests changed; migration rolled back.')
        result=dict(from_version=7,to_version=8,backup_path=backup,before=before,after=after,
                    all_ids_values_and_history_preserved=True,old_bests_unchanged=True)
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(8,datetime.now(timezone.utc).isoformat(),backup,json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=8');db.commit();return result
    except BaseException:db.rollback();raise
