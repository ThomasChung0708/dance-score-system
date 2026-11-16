"""Schema 2 -> 3: connect storage Pack and retained source Artist only."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from uuid import uuid4

from .migrations import execute_statements

V3_SQL = """
ALTER TABLE packs ADD COLUMN pack_type TEXT NOT NULL DEFAULT 'normal' CHECK(pack_type IN ('normal','multiplayer_download'));
ALTER TABLE song_sources ADD COLUMN saved_artist TEXT;
"""


def audit_v3(db, pack_ids, connect_ids):
    """All preexisting row values except connect's deliberately changed pack_id.

    Added columns/Pack rows and the new migration log are checked separately.
    """
    result = {}
    for table in ('library_roots','packs','songs','song_sources','scan_runs','game_difficulties',
                  'song_title_aliases','source_raw_snapshots','play_sessions','play_records','schema_migrations'):
        columns = [r[1] for r in db.execute('PRAGMA table_info('+table+')')
                   if (table,r[1]) not in {('packs','pack_type'),('song_sources','saved_artist'),
                       ('library_roots','root_kind'),('play_records','song_id')}]
        if table=='play_records':
            # This audit projects the pre-v3 layout, even after later additive
            # formal-submission columns are installed.
            columns=columns[:columns.index('game_level')] if 'game_level' in columns else columns
        digest, count = hashlib.sha256(), 0
        for row in db.execute('SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+columns[0]):
            values = list(row)
            if table=='packs' and values[columns.index('pack_id')] not in pack_ids:
                continue
            if table=='schema_migrations' and values[columns.index('version')]>2:
                continue
            if table=='songs' and values[columns.index('song_id')] in connect_ids:
                values[columns.index('pack_id')]=None
            values=[{'blob_sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)} if isinstance(v,bytes) else v for v in values]
            digest.update(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
            digest.update(b'\n'); count+=1
        result[table]={'count':count,'sha256':digest.hexdigest()}
    return result


def migrate_v2(db: sqlite3.Connection, path: Path, *, new_database=False):
    backup_path=None
    db.execute('BEGIN IMMEDIATE')
    try:
        if [r[0] for r in db.execute('PRAGMA quick_check')]!=['ok'] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('資料庫完整性檢查未通過，未執行 migration。')
        old_packs={r[0] for r in db.execute('SELECT pack_id FROM packs')}
        connect=[tuple(r) for r in db.execute('SELECT song_id,root_id,relative_folder,pack_id FROM songs')
                 if len(PureWindowsPath(r[2]).parts)>=2 and PureWindowsPath(r[2]).parts[0].casefold()=='connect']
        connect_ids={r[0] for r in connect}
        before=audit_v3(db,old_packs,connect_ids)
        if not new_database:
            backup_dir=path.parent/'backups'; backup_dir.mkdir(parents=True,exist_ok=True)
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup_path=backup_dir/f'{path.stem}.pre-v3.{stamp}.{uuid4().hex[:8]}.sqlite3'
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as backup:
                    source.backup(backup)
                    if audit_v3(backup,old_packs,connect_ids)!=before or backup.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                        raise ValueError('Migration 備份驗證失敗，未修改資料庫。')
        execute_statements(db,V3_SQL)
        associations=[]
        for song_id,root_id,relative,old_pack_id in connect:
            pack_name=PureWindowsPath(relative).parts[0]
            key=os.path.normcase(pack_name).replace('\\','/')
            existing=db.execute('SELECT pack_id FROM packs WHERE root_id=? AND path_key=?',(root_id,key)).fetchone()
            if old_pack_id is not None and (existing is None or old_pack_id!=existing[0]):
                raise ValueError('connect 既有 Pack 關聯不一致，已回滾供人工確認。')
            db.execute("INSERT INTO packs(root_id,relative_path,path_key,name,pack_type) VALUES(?,?,?,?,'multiplayer_download') "
                       "ON CONFLICT(root_id,path_key) DO UPDATE SET pack_type='multiplayer_download'",
                       (root_id,pack_name,key,pack_name))
            pack_id=db.execute('SELECT pack_id FROM packs WHERE root_id=? AND path_key=?',(root_id,key)).fetchone()[0]
            db.execute('UPDATE songs SET pack_id=? WHERE song_id=?',(pack_id,song_id))
            associations.append({'song_id':song_id,'pack_id':pack_id})
        # Earlier versions did not parse Artist; retain it if an existing payload
        # already contains an unambiguous string. No game files are read here.
        for source_id,payload in db.execute('SELECT source_id,payload_json FROM song_sources').fetchall():
            artist=json.loads(payload).get('artist')
            if isinstance(artist,str) and artist:
                db.execute('UPDATE song_sources SET saved_artist=? WHERE source_id=?',(artist,source_id))
        after=audit_v3(db,old_packs,connect_ids)
        if before!=after or db.execute('PRAGMA foreign_key_check').fetchall() or [r[0] for r in db.execute('PRAGMA quick_check')]!=['ok']:
            raise ValueError('Migration 識別／名稱／歷史完整性檢查失敗，已回滾。')
        result={'from_version':2,'to_version':3,'backup_path':str(backup_path) if backup_path else None,
                'before':before,'after':after,'original_pack_ids':sorted(old_packs),
                'pack_count_after':db.execute('SELECT count(*) FROM packs').fetchone()[0],
                'connect_associations':associations,'identities_names_paths_and_history_preserved':True}
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',
                   (3,datetime.now(timezone.utc).isoformat(),result['backup_path'],json.dumps(result,ensure_ascii=False)))
        db.execute('PRAGMA user_version=3'); db.commit()
        return result
    except BaseException:
        db.rollback(); raise
