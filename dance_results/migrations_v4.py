"""Add honest manual provenance while preserving difficulty IDs and all history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

V4_SQL = """CREATE TABLE game_difficulties_v4 (
    difficulty_id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(song_id) ON DELETE RESTRICT,
    difficulty TEXT NOT NULL CHECK(difficulty IN ('EASY','NORMAL','HARD')),
    game_level INTEGER CHECK(game_level IS NULL OR game_level >= 0),
    total_notes INTEGER CHECK(total_notes IS NULL OR total_notes > 0),
    notes_source TEXT CHECK(notes_source IS NULL OR notes_source IN ('game_screen','completed_judgment_sum','manual')),
    notes_verified INTEGER NOT NULL DEFAULT 0 CHECK(notes_verified IN (0,1) AND (notes_verified=0 OR total_notes IS NOT NULL)),
    level_source TEXT CHECK(level_source IS NULL OR level_source IN ('game_screen','manual')),
    UNIQUE(song_id,difficulty),
    CHECK((total_notes IS NULL AND notes_source IS NULL) OR (total_notes IS NOT NULL AND notes_source IS NOT NULL))
)"""


def identifier(value):
    return '"'+value.replace('"','""')+'"'


def data_audit(db):
    result={}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        digest,count=hashlib.sha256(),0
        condition=' WHERE version<=3' if name=='schema_migrations' else ''
        for row in db.execute('SELECT * FROM '+identifier(name)+condition+' ORDER BY 1'):
            values=[{'blob':hashlib.sha256(v).hexdigest(),'size':len(v)} if isinstance(v,bytes) else v for v in row]
            digest.update(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\n')
            count+=1
        result[name]={'count':count,'sha256':digest.hexdigest()}
    return result


def migrate_v3(db: sqlite3.Connection,path: Path,*,new_database=False):
    if db.in_transaction:
        raise ValueError('Migration 必須從獨立交易開始。')
    backup_path=None
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        if [r[0] for r in db.execute('PRAGMA quick_check')]!=['ok'] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('資料庫完整性檢查失敗，未執行 migration。')
        before=data_audit(db)
        if not new_database:
            backup_dir=path.parent/'backups';backup_dir.mkdir(parents=True,exist_ok=True)
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup_path=backup_dir/f'{path.stem}.pre-v4.{stamp}.{uuid4().hex[:8]}.sqlite3'
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as backup:
                    source.backup(backup)
                    if data_audit(backup)!=before or backup.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                        raise ValueError('Migration 備份核對失敗，未修改資料庫。')
        views=[tuple(r) for r in db.execute("SELECT name,sql FROM sqlite_master WHERE type='view' ORDER BY rowid")]
        objects=[r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='game_difficulties' AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY rowid")]
        for name,_ in views:db.execute('DROP VIEW '+identifier(name))
        db.execute(V4_SQL)
        db.execute('INSERT INTO game_difficulties_v4 SELECT * FROM game_difficulties')
        db.execute('DROP TABLE game_difficulties')
        db.execute('ALTER TABLE game_difficulties_v4 RENAME TO game_difficulties')
        for sql in objects:db.execute(sql)
        for _,sql in views:db.execute(sql)
        after=data_audit(db)
        if before!=after or db.execute('PRAGMA foreign_key_check').fetchall() or [r[0] for r in db.execute('PRAGMA quick_check')]!=['ok']:
            raise ValueError('Migration 身分／歷史稽核失敗，已回滾。')
        result={'from_version':3,'to_version':4,'backup_path':str(backup_path) if backup_path else None,
                'before':before,'after':after,'all_ids_values_and_history_preserved':True}
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',(4,datetime.now(timezone.utc).isoformat(),result['backup_path'],json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=4');db.commit()
        return result
    except BaseException:
        db.rollback();raise
    finally:
        db.execute('PRAGMA foreign_keys=ON')
