"""Versioned, backed-up SQLite migrations. Never reads or changes game files."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from uuid import uuid4

SCHEMA_VERSION = 10

V2_SQL = """
ALTER TABLE charts RENAME TO game_difficulties;
ALTER TABLE game_difficulties RENAME COLUMN chart_id TO difficulty_id;
ALTER TABLE game_difficulties ADD COLUMN notes_verified INTEGER NOT NULL DEFAULT 0 CHECK(notes_verified IN (0,1) AND (notes_verified=0 OR total_notes IS NOT NULL));
ALTER TABLE game_difficulties ADD COLUMN level_source TEXT CHECK(level_source IS NULL OR level_source='game_screen');
ALTER TABLE songs ADD COLUMN folder_name TEXT;
ALTER TABLE songs ADD COLUMN artist TEXT;
ALTER TABLE songs ADD COLUMN source_format TEXT CHECK(source_format IS NULL OR source_format IN ('sm','dwi','osu'));
ALTER TABLE songs ADD COLUMN format_status TEXT NOT NULL DEFAULT 'unknown' CHECK(format_status IN ('single','mixed','unknown'));
CREATE TABLE song_title_aliases (
    alias_id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(song_id) ON DELETE RESTRICT,
    display_title TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('game_screen','manual')),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)),
    UNIQUE(song_id,display_title,source)
);
CREATE TABLE source_raw_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES song_sources(source_id) ON DELETE RESTRICT,
    sha256 TEXT NOT NULL,
    raw_data BLOB NOT NULL,
    reason TEXT NOT NULL,
    first_scan_id INTEGER NOT NULL REFERENCES scan_runs(scan_id) ON DELETE RESTRICT,
    UNIQUE(source_id,sha256)
);
CREATE TABLE play_sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    player_name TEXT NOT NULL,
    account_label TEXT,
    play_mode TEXT NOT NULL CHECK(play_mode IN ('single','multiplayer')),
    is_host INTEGER CHECK(is_host IS NULL OR is_host IN (0,1)),
    source_type TEXT NOT NULL DEFAULT 'unknown' CHECK(source_type IN ('official','custom','unknown')),
    difficulty_mode TEXT CHECK(difficulty_mode IS NULL OR difficulty_mode IN ('free','host_selected')),
    local_difficulty TEXT CHECK(local_difficulty IS NULL OR local_difficulty IN ('EASY','NORMAL','HARD')),
    local_game_level INTEGER CHECK(local_game_level IS NULL OR local_game_level>=0),
    host_result_level INTEGER CHECK(host_result_level IS NULL OR host_result_level>=0),
    display_song_title TEXT,
    display_pack_name TEXT,
    room_status TEXT NOT NULL DEFAULT 'playing' CHECK(room_status IN ('playing','result','quit','disconnected','interrupted')),
    player_status TEXT NOT NULL DEFAULT 'playing' CHECK(player_status IN ('playing','failed_waiting','completed','failed','quit','disconnected','interrupted')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    CHECK(play_mode='multiplayer' OR is_host IS NULL)
);
CREATE TABLE play_records (
    record_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES play_sessions(session_id) ON DELETE RESTRICT,
    difficulty_id INTEGER REFERENCES game_difficulties(difficulty_id) ON DELETE RESTRICT,
    played_at TEXT NOT NULL,
    completion_status TEXT NOT NULL CHECK(completion_status IN ('COMPLETED','FAILED')),
    perfect INTEGER CHECK(perfect IS NULL OR perfect>=0),
    cool INTEGER CHECK(cool IS NULL OR cool>=0),
    bad INTEGER CHECK(bad IS NULL OR bad>=0),
    miss INTEGER CHECK(miss IS NULL OR miss>=0),
    ex_score INTEGER CHECK(ex_score IS NULL OR ex_score>=0),
    max_score INTEGER CHECK(max_score IS NULL OR max_score>0),
    percentage REAL CHECK(percentage IS NULL OR percentage BETWEEN 0 AND 100),
    lamp_status TEXT CHECK(lamp_status IS NULL OR lamp_status IN ('ACP','AC','CLEAR','FAILED')),
    total_notes INTEGER CHECK(total_notes IS NULL OR total_notes>0),
    notes_source TEXT CHECK(notes_source IS NULL OR notes_source IN ('game_screen','completed_judgment_sum')),
    match_status TEXT NOT NULL DEFAULT 'pending' CHECK(match_status IN ('pending','confirmed')),
    validation_status TEXT NOT NULL DEFAULT 'pending' CHECK(validation_status IN ('pending','verified','rejected')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    CHECK(match_status='pending' OR difficulty_id IS NOT NULL),
    CHECK((total_notes IS NULL AND notes_source IS NULL) OR (total_notes IS NOT NULL AND notes_source IS NOT NULL)),
    CHECK(notes_source IS NULL OR notes_source!='completed_judgment_sum' OR
          (completion_status='COMPLETED' AND perfect IS NOT NULL AND cool IS NOT NULL AND bad IS NOT NULL AND miss IS NOT NULL AND total_notes=perfect+cool+bad+miss)),
    CHECK(ex_score IS NULL OR perfect IS NULL OR cool IS NULL OR ex_score=2*perfect+cool),
    CHECK(max_score IS NULL OR total_notes IS NULL OR max_score=2*total_notes),
    CHECK(ex_score IS NULL OR max_score IS NULL OR ex_score<=max_score),
    CHECK(percentage IS NULL OR (ex_score IS NOT NULL AND max_score IS NOT NULL AND abs(percentage-100.0*ex_score/max_score)<0.000001)),
    CHECK(lamp_status IS NULL OR (completion_status='FAILED' AND lamp_status='FAILED') OR
          (completion_status='COMPLETED' AND lamp_status IN ('ACP','AC','CLEAR'))),
    CHECK(validation_status!='verified' OR
          (perfect IS NOT NULL AND cool IS NOT NULL AND bad IS NOT NULL AND miss IS NOT NULL AND ex_score IS NOT NULL AND lamp_status IS NOT NULL)),
    CHECK(validation_status!='verified' OR completion_status='FAILED' OR
          lamp_status=CASE WHEN perfect>0 AND cool=0 AND bad=0 AND miss=0 THEN 'ACP'
                           WHEN perfect+cool>0 AND bad=0 AND miss=0 THEN 'AC' ELSE 'CLEAR' END)
);
CREATE INDEX records_difficulty_index ON play_records(difficulty_id);
CREATE INDEX aliases_song_index ON song_title_aliases(song_id);
CREATE TRIGGER record_requires_result_insert BEFORE INSERT ON play_records
WHEN NOT EXISTS (SELECT 1 FROM play_sessions s WHERE s.session_id=NEW.session_id AND s.room_status='result'
    AND s.player_status=CASE NEW.completion_status WHEN 'FAILED' THEN 'failed' ELSE 'completed' END)
BEGIN SELECT RAISE(ABORT,'play record requires own final result'); END;
CREATE TRIGGER record_requires_result_update BEFORE UPDATE ON play_records
WHEN NOT EXISTS (SELECT 1 FROM play_sessions s WHERE s.session_id=NEW.session_id AND s.room_status='result'
    AND s.player_status=CASE NEW.completion_status WHEN 'FAILED' THEN 'failed' ELSE 'completed' END)
BEGIN SELECT RAISE(ABORT,'play record requires own final result'); END;
CREATE TRIGGER session_preserves_result BEFORE UPDATE ON play_sessions
WHEN EXISTS (SELECT 1 FROM play_records r WHERE r.session_id=OLD.session_id AND
    (NEW.room_status!='result' OR NEW.player_status!=CASE r.completion_status WHEN 'FAILED' THEN 'failed' ELSE 'completed' END))
BEGIN SELECT RAISE(ABORT,'session already has a final record'); END;
CREATE VIEW difficulty_bests AS
SELECT d.difficulty_id,d.song_id,d.difficulty,
    MAX(CASE WHEN r.completion_status='COMPLETED' THEN r.ex_score END) AS highest_ex_score,
    CASE MAX(CASE r.lamp_status WHEN 'ACP' THEN 4 WHEN 'AC' THEN 3 WHEN 'CLEAR' THEN 2 WHEN 'FAILED' THEN 1 ELSE 0 END)
        WHEN 4 THEN 'ACP' WHEN 3 THEN 'AC' WHEN 2 THEN 'CLEAR' WHEN 1 THEN 'FAILED' ELSE 'UNPLAYED' END AS best_clear_lamp
FROM game_difficulties d LEFT JOIN play_records r ON r.difficulty_id=d.difficulty_id
    AND r.match_status='confirmed' AND r.validation_status='verified'
GROUP BY d.difficulty_id;
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    backup_path TEXT,
    audit_json TEXT NOT NULL
);
"""


def execute_statements(db: sqlite3.Connection, sql: str) -> None:
    """executescript commits an existing transaction; execute complete statements instead."""
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            db.execute(pending)
            pending = ""
    if pending.strip():
        raise ValueError("Incomplete migration SQL")


def identity_audit(db: sqlite3.Connection, *, migrated: bool = False) -> dict:
    """Fingerprint existing identity/title data and every historical scan snapshot."""
    queries = {
        'library_roots': 'SELECT root_id,path,path_key,created_at FROM library_roots ORDER BY root_id',
        'packs': 'SELECT pack_id,root_id,relative_path,path_key,name FROM packs ORDER BY pack_id',
        'songs': 'SELECT song_id,root_id,pack_id,relative_folder,folder_key,latest_title,saved_title,payload_json,is_present FROM songs ORDER BY song_id',
        'song_sources': 'SELECT source_id,song_id,relative_path,path_key,format,latest_sha256,saved_title,saved_subtitle,latest_status,payload_json,is_present,last_seen_scan_id FROM song_sources ORDER BY source_id',
        'scan_runs': 'SELECT * FROM scan_runs ORDER BY scan_id',
        'game_difficulties': 'SELECT ' + ('difficulty_id' if migrated else 'chart_id') + ',song_id,difficulty,game_level,total_notes,notes_source FROM ' + ('game_difficulties' if migrated else 'charts') + ' ORDER BY 1',
    }
    audit = {}
    for name, query in queries.items():
        digest, count = hashlib.sha256(), 0
        for row in db.execute(query):
            digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            digest.update(b'\n')
            count += 1
        audit[name] = {'count': count, 'sha256': digest.hexdigest()}
    return audit


def migrate_v1(db: sqlite3.Connection, path: Path, *, new_database: bool = False) -> dict:
    backup_path = None
    db.execute('BEGIN IMMEDIATE')
    try:
        if db.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('資料庫完整性檢查未通過，未執行 migration。')
        before = identity_audit(db)
        if not new_database:
            backup_dir = path.parent / 'backups'
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup_path = backup_dir / f'{path.stem}.pre-v2.{stamp}.{uuid4().hex[:8]}.sqlite3'
            # The reserved write lock above blocks other writers while this separate
            # read connection creates a consistent, pre-migration SQLite backup.
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as backup:
                    source.backup(backup)
                    if identity_audit(backup) != before or backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('Migration 備份驗證失敗，未修改資料庫。')
        execute_statements(db, V2_SQL)
        for song_id, relative in db.execute('SELECT song_id,relative_folder FROM songs').fetchall():
            formats = [row[0] for row in db.execute('SELECT DISTINCT format FROM song_sources WHERE song_id=? AND is_present=1 ORDER BY format', (song_id,))]
            db.execute('UPDATE songs SET folder_name=?,source_format=?,format_status=? WHERE song_id=?',
                       (PureWindowsPath(relative).name, formats[0] if len(formats)==1 else None,
                        'single' if len(formats)==1 else 'mixed' if formats else 'unknown', song_id))
        after = identity_audit(db, migrated=True)
        if before != after:
            raise ValueError('Migration 改變了既有識別或資料，已回滾。')
        if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Migration 完整性檢查失敗，已回滾。')
        result = {'from_version': 1, 'to_version': 2, 'backup_path': str(backup_path) if backup_path else None,
                  'before': before, 'after': after, 'identities_and_history_preserved': True}
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',
                   (2, datetime.now(timezone.utc).isoformat(), result['backup_path'], json.dumps(result, ensure_ascii=False)))
        db.execute('PRAGMA user_version=2')
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
