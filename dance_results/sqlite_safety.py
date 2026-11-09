"""Verified SQLite backups and projections used by independent import transactions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def quoted(value):
    return '"'+value.replace('"','""')+'"'


def projection(db):
    names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name:[r[1] for r in db.execute('PRAGMA table_info('+quoted(name)+')')] for name in names}


def audit(db, columns=None, limits=None):
    result={}
    for name, fields in (columns or projection(db)).items():
        digest=hashlib.sha256();count=0
        condition=' WHERE rowid<=?' if limits is not None and name in limits else ''
        parameters=(limits[name],) if condition else ()
        for row in db.execute('SELECT '+','.join(map(quoted,fields))+' FROM '+quoted(name)+condition+' ORDER BY 1',parameters):
            values=[{'blob':hashlib.sha256(v).hexdigest(),'size':len(v)} if isinstance(v,bytes) else v for v in row]
            digest.update(json.dumps(values,ensure_ascii=False,separators=(',',':')).encode('utf-8')+b'\n');count+=1
        result[name]={'count':count,'sha256':digest.hexdigest()}
    return result


def row_limits(db):
    result={}
    for name in projection(db):
        try:result[name]=db.execute('SELECT COALESCE(MAX(rowid),0) FROM '+quoted(name)).fetchone()[0]
        except sqlite3.OperationalError:pass  # WITHOUT ROWID tables are audited in full.
    return result


def check_integrity(db):
    if [r[0] for r in db.execute('PRAGMA quick_check')]!=['ok'] or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('SQLite 完整性或外鍵檢查失敗。')


def verified_backup(db,path,label):
    """Caller must hold BEGIN IMMEDIATE; backup from a separate read-only source."""
    if not db.in_transaction:raise ValueError('備份前必須取得寫入交易鎖。')
    check_integrity(db);before=audit(db)
    directory=path.parent/'backups';directory.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=directory/f'{path.stem}.{label}.{stamp}.{uuid4().hex[:8]}.sqlite3'
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(target)) as backup:
            source.backup(backup);check_integrity(backup)
            if audit(backup)!=before or backup.execute('PRAGMA user_version').fetchone()[0]!=db.execute('PRAGMA user_version').fetchone()[0]:
                raise ValueError('SQLite 備份稽核失敗，未繼續寫入。')
    return str(target)
