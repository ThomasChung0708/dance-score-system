"""Independent, transactional K-only official catalog importer. No OCR dependency."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .database import APPLICATION_ID, LibraryDatabase, json_text
from .models import GAME_DIFFICULTIES
from .sqlite_safety import audit, row_limits, check_integrity, verified_backup

CATALOG_KEY='dance_online_official_keyboard'
CATALOG_ROOT='official://dance_online/K'
MAX_CSV_BYTES=4*1024*1024
REQUIRED=('fileId','title','artist','bpm','mode','gn','lvEasy','notesEasy','lvNormal','notesNormal','lvHard','notesHard')
DEFAULT_DATABASE=Path(__file__).resolve().parent.parent/'data/library.sqlite3'


@dataclass
class Catalog:
    rows: list[dict]
    summary: dict
    sha256: str
    source_name: str


def read_catalog(data: bytes,source_name: str) -> Catalog:
    if not data or len(data)>MAX_CSV_BYTES:raise ValueError('CSV 必須非空且不超過 4 MiB。')
    try:text=data.decode('utf-8-sig',errors='strict')
    except UnicodeDecodeError as error:raise ValueError('官方 CSV 必須是 UTF-8／UTF-8 BOM；未猜測或替換錯誤字元。') from error
    if '\x00' in text:raise ValueError('CSV 含 NUL，未匯入。')
    reader=csv.DictReader(io.StringIO(text,newline=''),strict=True)
    if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames) or not set(REQUIRED)<=set(reader.fieldnames):
        raise ValueError('CSV 缺少必要欄位或存在重複欄名。')
    rows=[];modes=Counter();file_ids=defaultdict(list);gns=set();valid_difficulties=0;unprovided=[]
    try:
        for number,raw in enumerate(reader,2):
            if None in raw or any(v is None for v in raw.values()):raise ValueError(f'CSV 記錄 {number} 欄數不一致。')
            modes[raw['mode']]+=1
            if raw['mode']!='K':continue
            identity=(raw['fileId'],raw['gn'])
            if not re.fullmatch(r'[0-9]+',raw['fileId']) or int(raw['fileId'])<=0 or not raw['gn'] or any(v!=v.strip() for v in identity):
                raise ValueError(f'CSV 記錄 {number} 的 fileId／gn 無效。')
            if raw['gn'] in gns:raise ValueError(f'K 模式 gn 重複：{raw["gn"]}；未合併或覆蓋。')
            gns.add(raw['gn'])
            if not raw['title'].strip() or not raw['artist'].strip():raise ValueError(f'CSV 記錄 {number} 缺少 Title／Artist。')
            try:
                bpm=Decimal(raw['bpm'])
                if not bpm.is_finite() or bpm<=0:raise InvalidOperation
            except (InvalidOperation,ValueError):raise ValueError(f'CSV 記錄 {number} BPM 無效。')
            difficulties=[]
            for label in GAME_DIFFICULTIES:
                suffix=label.title();level=raw['lv'+suffix];notes=raw['notes'+suffix]
                if level in ('','0') and notes in ('','0'):
                    unprovided.append({'file_id':raw['fileId'],'gn':raw['gn'],'difficulty':label,'level_raw':level,'notes_raw':notes});continue
                if not all(re.fullmatch(r'[0-9]+',value) and 0<int(value)<=9007199254740991 for value in (level,notes)):
                    raise ValueError(f'CSV 記錄 {number} {label} 的 LV／音符數必須同時為正整數，或皆為 0／空白。')
                difficulties.append({'difficulty':label,'game_level':int(level),'total_notes':int(notes)})
            valid_difficulties+=len(difficulties)
            rows.append({'raw':raw,'record_number':number,'difficulties':difficulties})
            file_ids[raw['fileId']].append({'gn':raw['gn'],'record_number':number})
    except csv.Error as error:raise ValueError('CSV 結構不正確：'+str(error)) from error
    if not rows:raise ValueError('CSV 沒有 mode=K 的資料，未匯入。')
    duplicates=[{'file_id':key,'sources':values} for key,values in file_ids.items() if len(values)>1]
    summary={'total_rows':sum(modes.values()),'mode_counts':dict(modes),'keyboard_rows':len(rows),
             'excluded_rows':sum(modes.values())-len(rows),'valid_difficulties':valid_difficulties,
             'duplicate_file_ids':duplicates,'identity_policy':'catalog_key + mode + fileId + gn',
             'unprovided_difficulties':unprovided,'hidden_keyboard_rows':sum(r['raw'].get('hidden')=='1' for r in rows)}
    return Catalog(rows,summary,hashlib.sha256(data).hexdigest(),str(source_name))


def catalog_delta(db,catalog):
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_song_sources'").fetchone()
    sources=[dict(r) for r in db.execute('SELECT * FROM official_song_sources WHERE catalog_key=?',(CATALOG_KEY,))] if exists else []
    by_key={(s['file_id'],s['gn']):s for s in sources};by_gn={s['gn']:s for s in sources}
    incoming={(r['raw']['fileId'],r['raw']['gn']) for r in catalog.rows}
    new=[];conflicts=[];unchanged=0
    for row in catalog.rows:
        raw=row['raw'];key=(raw['fileId'],raw['gn']);old=by_key.get(key)
        if old is None:
            if raw['gn'] in by_gn or any(s['file_id']==raw['fileId'] and (s['file_id'],s['gn']) not in incoming for s in sources):
                conflicts.append(f'{raw["fileId"]}／{raw["gn"]} 的來源身份變動，需人工確認。')
            else:new.append(row)
            continue
        if old['raw_row_json']!=json_text(raw):
            conflicts.append(f'{raw["fileId"]}／{raw["gn"]} 的既有來源內容不同，未覆寫。');continue
        differences=[]
        for d in row['difficulties']:
            actual=db.execute('SELECT game_level,total_notes FROM game_difficulties WHERE song_id=? AND difficulty=?',(old['song_id'],d['difficulty'])).fetchone()
            if actual is None or tuple(actual)!=(d['game_level'],d['total_notes']):differences.append(d['difficulty'])
        if differences:conflicts.append(f'{raw["fileId"]}／{raw["gn"]} 的既有難度資料不同（{"、".join(differences)}），未覆寫。')
        else:unchanged+=1
    return {'new_songs':len(new),'new_sources':len(new),'new_difficulties':sum(len(r['difficulties']) for r in new),
            'unchanged_songs':unchanged,'conflicts':conflicts},new


def preview_catalog(database,catalog):
    path=Path(database).expanduser().resolve()
    if not path.is_file():delta={'new_songs':len(catalog.rows),'new_sources':len(catalog.rows),'new_difficulties':catalog.summary['valid_difficulties'],'unchanged_songs':0,'conflicts':[]}
    else:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            if db.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID:raise ValueError('不是本程式的歌曲資料庫。')
            delta,_=catalog_delta(db,catalog)
    return {**catalog.summary,'csv_sha256':catalog.sha256,'source_name':catalog.source_name,'changes':delta}


def _insert_song(db,root_id,row,import_id):
    raw=row['raw'];key=json_text(['K',raw['fileId'],raw['gn']]);stamp=datetime.now(timezone.utc).isoformat()
    payload=json_text({'diagnostics':[],'title_candidates':[raw['title']]})
    song_id=db.execute('INSERT INTO songs(root_id,pack_id,relative_folder,folder_key,source_type,latest_title,saved_title,latest_status,payload_json,created_at,artist) '
                      "VALUES(?,NULL,?,?,'official',?,?,'ok',?,?,?)",
                      (root_id,'K/'+raw['fileId']+'/'+raw['gn'],key,raw['title'],raw['title'],payload,stamp,raw['artist'])).lastrowid
    db.execute('INSERT INTO official_song_sources(song_id,catalog_key,mode,file_id,gn,bpm_raw,raw_row_json,csv_record_number,import_id) '
               "VALUES(?,?,'K',?,?,?,?,?,?)",(song_id,CATALOG_KEY,raw['fileId'],raw['gn'],raw['bpm'],json_text(raw),row['record_number'],import_id))
    for difficulty in row['difficulties']:
        db.execute('INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,level_source,notes_source,notes_verified) '
                   "VALUES(?,?,?,?,'official_csv','official_csv',1)",(song_id,difficulty['difficulty'],difficulty['game_level'],difficulty['total_notes']))


def import_catalog(database,catalog,*,expected_sha256=None):
    if expected_sha256 is not None and catalog.sha256!=expected_sha256:raise ValueError('CSV 與預覽不同，請重新預覽。')
    path=Path(database).expanduser().resolve()
    protected=[]
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as existing:
            columns=[r[1] for r in existing.execute('PRAGMA table_info(library_roots)')]
            if columns:
                condition=" WHERE root_kind='filesystem'" if 'root_kind' in columns else ''
                protected=[Path(r[0]) for r in existing.execute('SELECT path FROM library_roots'+condition)]
    with LibraryDatabase(path,protected) as library:
        migrations=library.migration_results;db=library.connection
        db.execute('BEGIN IMMEDIATE')
        try:
            check_integrity(db);changes,new=catalog_delta(db,catalog)
            if changes['conflicts']:raise ValueError('官方匯入有衝突，整批未寫入：'+'；'.join(changes['conflicts'][:10]))
            limits=row_limits(db);before=audit(db,limits=limits)
            backup=verified_backup(db,path,'pre-official-import') if new else None
            if new:
                db.execute("INSERT INTO library_roots(path,path_key,created_at,root_kind) VALUES(?,?,?,'official_catalog') ON CONFLICT(path_key) DO NOTHING",
                           (CATALOG_ROOT,CATALOG_ROOT,datetime.now(timezone.utc).isoformat()))
                root=db.execute('SELECT root_id,root_kind FROM library_roots WHERE path_key=?',(CATALOG_ROOT,)).fetchone()
                if root['root_kind']!='official_catalog':raise ValueError('官方來源命名空間已被其他 root 使用。')
                db.execute('INSERT INTO official_import_runs(catalog_key,source_name,csv_sha256,encoding,imported_at,summary_json) VALUES(?,?,?,?,?,?) '
                           'ON CONFLICT(catalog_key,csv_sha256) DO NOTHING',(CATALOG_KEY,catalog.source_name,catalog.sha256,'utf-8-sig',datetime.now(timezone.utc).isoformat(),json_text(catalog.summary)))
                import_id=db.execute('SELECT import_id FROM official_import_runs WHERE catalog_key=? AND csv_sha256=?',(CATALOG_KEY,catalog.sha256)).fetchone()[0]
                for row in new:_insert_song(db,root['root_id'],row,import_id)
            after=audit(db,limits=limits)
            if before!=after:raise ValueError('匯入改變既有資料，已回滾。')
            check_integrity(db)
            totals={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('packs','songs','song_sources','official_song_sources','game_difficulties','play_records')}
            db.commit()
            return {**catalog.summary,'csv_sha256':catalog.sha256,'changes':changes,'totals':totals,
                    'backup_path':backup,'migrations':migrations,'existing_data_preserved':True,'before':before,'after':after}
        except BaseException:db.rollback();raise


def main(argv=None):
    parser=argparse.ArgumentParser(description='官方歌曲 CSV：只匯入 mode=K，不依賴 OCR 或自製歌曲掃描。')
    parser.add_argument('--csv',type=Path,required=True)
    parser.add_argument('--database',type=Path,default=DEFAULT_DATABASE)
    parser.add_argument('--import',dest='apply',action='store_true',help='備份並正式匯入；未指定時僅預覽')
    parser.add_argument('--result',type=Path,help='另存 JSON 結果，不能覆寫 CSV 或資料庫')
    args=parser.parse_args(argv)
    try:
        csv_path=args.csv.expanduser().resolve();db_path=args.database.expanduser().resolve()
        if csv_path==db_path:raise ValueError('CSV 與資料庫路徑不可相同。')
        if args.result and args.result.resolve() in (csv_path,db_path):raise ValueError('結果路徑不可覆寫 CSV 或資料庫。')
        if csv_path.stat().st_size>MAX_CSV_BYTES:raise ValueError('CSV 不可超過 4 MiB。')
        catalog=read_catalog(csv_path.read_bytes(),str(csv_path))
        result=import_catalog(db_path,catalog) if args.apply else preview_catalog(db_path,catalog)
        if args.result:
            try:
                args.result.parent.mkdir(parents=True,exist_ok=True);args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError as error:
                print(('匯入已提交，但' if args.apply else '預覽完成，但')+'結果檔寫入失敗：'+str(error))
                return 3
        print(json.dumps({k:v for k,v in result.items() if k not in ('before','after','migrations')},ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,sqlite3.Error) as error:
        print('熱舞官方譜面未完成匯入：'+str(error));return 2


if __name__=='__main__':raise SystemExit(main())
