"""Add formal submission snapshots to existing PlayRecord, preserving every old column."""
import json
from datetime import datetime, timezone
from .sqlite_safety import audit, projection, check_integrity, verified_backup

COLUMNS={
    'game_level':'INTEGER CHECK(game_level IS NULL OR (typeof(game_level)=\'integer\' AND game_level>0))',
    'max_combo':'INTEGER CHECK(max_combo IS NULL OR (typeof(max_combo)=\'integer\' AND max_combo>=0))',
    'game_score':'INTEGER CHECK(game_score IS NULL OR (typeof(game_score)=\'integer\' AND game_score>=0))',
    'max_difference':'INTEGER CHECK(max_difference IS NULL OR (typeof(max_difference)=\'integer\' AND max_difference>=0))',
    'grade':'TEXT','screenshot_path':'TEXT','screenshot_sha256':'TEXT','review_id':'TEXT',
    'review_revision':'INTEGER','confirmed_at':'TEXT','confirmation_method':'TEXT','submission_hash':'TEXT',
}

FORMAL_INVALID="""NEW.review_id IS NOT NULL AND (
 NEW.confirmation_method IS NOT 'manual_review' OR NEW.difficulty_id IS NULL OR
 NEW.match_status!='confirmed' OR NEW.validation_status!='verified' OR
 NEW.game_level IS NULL OR NEW.game_level<=0 OR
 NEW.max_combo IS NULL OR NEW.game_score IS NULL OR
 NEW.max_combo>NEW.perfect+NEW.cool OR
 typeof(NEW.perfect)!='integer' OR typeof(NEW.cool)!='integer' OR
 typeof(NEW.bad)!='integer' OR typeof(NEW.miss)!='integer' OR typeof(NEW.ex_score)!='integer' OR
 NEW.ex_score!=2*NEW.perfect+NEW.cool OR
 NEW.screenshot_path IS NULL OR length(NEW.screenshot_path)=0 OR
 NEW.screenshot_sha256 IS NULL OR length(NEW.screenshot_sha256)!=64 OR
 NEW.submission_hash IS NULL OR length(NEW.submission_hash)!=64 OR
 NEW.confirmed_at IS NULL OR NEW.review_revision IS NULL OR NEW.review_revision<1 OR
 (NEW.max_score IS NULL AND (NEW.total_notes IS NOT NULL OR NEW.percentage IS NOT NULL OR NEW.max_difference IS NOT NULL)) OR
 (NEW.max_score IS NOT NULL AND (NEW.total_notes IS NULL OR NEW.max_difference IS NULL OR NEW.percentage IS NULL OR NEW.max_difference!=NEW.max_score-NEW.ex_score)) OR
 (NEW.completion_status='COMPLETED' AND (NEW.max_score IS NULL OR NEW.total_notes!=NEW.perfect+NEW.cool+NEW.bad+NEW.miss)) OR
 (NEW.total_notes IS NOT NULL AND NEW.total_notes<NEW.perfect+NEW.cool+NEW.bad+NEW.miss) OR
 NOT EXISTS(SELECT 1 FROM game_difficulties d WHERE d.difficulty_id=NEW.difficulty_id AND
     (d.game_level IS NULL OR d.game_level=NEW.game_level) AND
     (d.notes_verified=0 OR d.total_notes=NEW.total_notes))
)"""


def migrate_v5(db,path,*,new_database=False):
    if db.in_transaction:raise ValueError('Migration 必須從獨立交易開始。')
    try:
        db.execute('BEGIN IMMEDIATE');check_integrity(db)
        projection_before=projection(db);before=audit(db,projection_before)
        backup=None if new_database else verified_backup(db,path,'pre-v6')
        for name,kind in COLUMNS.items():db.execute('ALTER TABLE play_records ADD COLUMN '+name+' '+kind)
        db.execute('CREATE UNIQUE INDEX records_review_unique ON play_records(review_id) WHERE review_id IS NOT NULL')
        db.execute('CREATE INDEX records_played_at_index ON play_records(played_at DESC,record_id DESC)')
        db.execute("CREATE TRIGGER formal_record_valid_insert BEFORE INSERT ON play_records WHEN "+FORMAL_INVALID+" BEGIN SELECT RAISE(ABORT,'formal submission is incomplete or inconsistent'); END")
        db.execute("CREATE TRIGGER formal_record_valid_update BEFORE UPDATE ON play_records WHEN "+FORMAL_INVALID+" BEGIN SELECT RAISE(ABORT,'formal submission is incomplete or inconsistent'); END")
        db.execute("CREATE TRIGGER formal_record_immutable BEFORE UPDATE ON play_records WHEN OLD.review_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'submitted record is immutable'); END")
        db.execute("CREATE TRIGGER formal_record_preserve BEFORE DELETE ON play_records WHEN OLD.review_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'submitted record must be preserved'); END")
        after=audit(db,projection_before);check_integrity(db)
        if before!=after:raise ValueError('Migration 既有資料稽核失敗，已回滾。')
        result=dict(from_version=5,to_version=6,backup_path=backup,before=before,after=after,all_ids_values_and_history_preserved=True)
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(6,datetime.now(timezone.utc).isoformat(),backup,json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=6');db.commit();return result
    except BaseException:db.rollback();raise
