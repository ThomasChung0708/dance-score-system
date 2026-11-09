"""Official catalog support, without altering existing song or difficulty identities."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .migrations_v4 import V4_SQL
from .sqlite_safety import audit, projection, quoted, check_integrity, verified_backup

V5_DIFFICULTY_SQL=V4_SQL.replace('game_difficulties_v4','game_difficulties_v5').replace("'manual'","'manual','official_csv'")
OFFICIAL_SQL=[
    "ALTER TABLE library_roots ADD COLUMN root_kind TEXT NOT NULL DEFAULT 'filesystem' CHECK(root_kind IN ('filesystem','official_catalog'))",
    """CREATE TABLE official_import_runs(
        import_id INTEGER PRIMARY KEY,
        catalog_key TEXT NOT NULL,
        source_name TEXT NOT NULL,
        csv_sha256 TEXT NOT NULL,
        encoding TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        summary_json TEXT NOT NULL,
        UNIQUE(catalog_key,csv_sha256))""",
    """CREATE TABLE official_song_sources(
        official_source_id INTEGER PRIMARY KEY,
        song_id INTEGER NOT NULL UNIQUE REFERENCES songs(song_id) ON DELETE RESTRICT,
        catalog_key TEXT NOT NULL,
        mode TEXT NOT NULL CHECK(mode='K'),
        file_id TEXT NOT NULL,
        gn TEXT NOT NULL,
        bpm_raw TEXT NOT NULL,
        raw_row_json TEXT NOT NULL,
        csv_record_number INTEGER NOT NULL CHECK(csv_record_number>=2),
        import_id INTEGER NOT NULL REFERENCES official_import_runs(import_id) ON DELETE RESTRICT,
        UNIQUE(catalog_key,mode,file_id,gn),
        UNIQUE(catalog_key,mode,gn))""",
    """CREATE TRIGGER official_source_requires_official_song BEFORE INSERT ON official_song_sources
       WHEN NOT EXISTS(SELECT 1 FROM songs s JOIN library_roots r ON r.root_id=s.root_id
                       WHERE s.song_id=NEW.song_id AND s.source_type='official'
                         AND s.pack_id IS NULL AND r.root_kind='official_catalog')
       BEGIN SELECT RAISE(ABORT,'Official source must reference an official catalog song'); END""",
]


def migrate_v4(db,path,*,new_database=False):
    if db.in_transaction:raise ValueError('Migration 必須從獨立交易開始。')
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE');check_integrity(db)
        columns=projection(db);before=audit(db,columns)
        backup=None if new_database else verified_backup(db,path,'pre-v5')
        views=[tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='view' ORDER BY rowid")]
        objects=[r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='game_difficulties' AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY rowid")]
        for name,_ in views:db.execute('DROP VIEW '+quoted(name))
        db.execute(V5_DIFFICULTY_SQL)
        db.execute('INSERT INTO game_difficulties_v5 SELECT * FROM game_difficulties')
        db.execute('DROP TABLE game_difficulties')
        db.execute('ALTER TABLE game_difficulties_v5 RENAME TO game_difficulties')
        for sql in objects:db.execute(sql)
        for _,sql in views:db.execute(sql)
        for sql in OFFICIAL_SQL:db.execute(sql)
        after=audit(db,columns);check_integrity(db)
        if before!=after:raise ValueError('Migration 既有資料稽核失敗，已回滾。')
        result={'from_version':4,'to_version':5,'backup_path':backup,'before':before,'after':after,
                'all_ids_values_and_history_preserved':True}
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(5,datetime.now(timezone.utc).isoformat(),backup,json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=5');db.commit();return result
    except BaseException:db.rollback();raise
    finally:db.execute('PRAGMA foreign_keys=ON')
