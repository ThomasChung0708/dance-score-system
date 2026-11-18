"""GUI-facing queries and ID-bound manual edits; reuse the existing scanner/sync."""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import threading
import unicodedata
from pathlib import Path

from .database import LibraryDatabase
from .models import GAME_DIFFICULTIES
from .scanner import scan_library
from .official_import import read_catalog, preview_catalog, import_catalog
from .achievements import progress_for_songs
from .songs import catalog_display_title


class LibraryError(ValueError):
    def __init__(self,message,status=400):
        super().__init__(message);self.status=status


def folded(value):
    return unicodedata.normalize('NFKC',value or '').casefold()


def integer(value,name,minimum=0):
    if type(value) is not int or not minimum<=value<=9007199254740991:
        raise LibraryError(f'{name} 必須是大於等於 {minimum} 的整數。')
    return value


def root_label(path):
    """Short name of a song location: '' for the game's own DATA/ADDON/SONG, else e.g. 'StepMania3.9' or 'osu!'."""
    parts=[p for p in str(path or '').replace('\\','/').split('/') if p]
    if not parts or parts[0].startswith('official:'):return ''
    if len(parts)>=2 and parts[-2].lower()=='addon' and parts[-1].lower()=='song':return ''
    return parts[-2] if parts[-1].lower()=='songs' and len(parts)>=2 else parts[-1]


class LibraryService:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve()
        self._cache_lock=threading.Lock()
        self._write_lock=threading.Lock()
        self._scan_lock=threading.Lock()
        self._cache={}
        self._scan={'status':'idle'}

    def open(self,read_only=True):
        return LibraryDatabase(self.path,read_only=read_only)

    def bootstrap(self):
        with self.open() as library:
            all_roots=[dict(r) for r in library.connection.execute('SELECT root_id,path,root_kind FROM library_roots ORDER BY root_id')]
            roots=[r for r in all_roots if r['root_kind']=='filesystem'] or all_roots
        return {'roots':roots,'database':str(self.path)}

    def _root(self,db,root_id):
        integer(root_id,'歌曲庫 ID',1)
        row=db.execute('SELECT * FROM library_roots WHERE root_id=?',(root_id,)).fetchone()
        if row is None:raise LibraryError('找不到此歌曲庫。',404)
        return row

    def _stamp(self):
        return tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None
                     for p in (self.path,Path(str(self.path)+'-wal')))

    def _catalog(self,root_id):
        # root_id None = every song location together (official, ADDON\SONG, StepMania, osu...).
        stamp=self._stamp()
        with self._cache_lock:
            if root_id in self._cache and self._cache[root_id][0]==stamp:
                return self._cache[root_id][1]
        with self.open() as library:
            db=library.connection
            if root_id is not None:self._root(db,root_id)
            scope="(s.root_id=? OR r.root_kind='official_catalog')" if root_id is not None else '1'
            args=(root_id,) if root_id is not None else ()
            rows=[dict(r) for r in db.execute(
                'SELECT s.song_id,s.root_id,s.pack_id,s.relative_folder,s.folder_name,s.saved_title,s.latest_title,s.payload_json,s.latest_status,s.is_present,s.source_type,p.name AS pack_name,p.pack_type,o.file_id,o.gn '
                'FROM songs s JOIN library_roots r ON r.root_id=s.root_id LEFT JOIN packs p ON p.pack_id=s.pack_id '
                'LEFT JOIN official_song_sources o ON o.song_id=s.song_id WHERE '+scope,args)]
            extra={r['song_id']:[] for r in rows}
            for r in db.execute("SELECT f.song_id,f.saved_title,f.saved_subtitle,json_extract(f.payload_json,'$.title_fields') AS fields FROM song_sources f JOIN songs s ON s.song_id=f.song_id WHERE "+('s.root_id=?' if root_id is not None else '1'),args):
                extra[r['song_id']].extend(v for v in (r['saved_title'],r['saved_subtitle']) if v)
                for values in json.loads(r['fields'] or '{}').values():extra[r['song_id']].extend(values)
            for r in db.execute('SELECT a.song_id,a.display_title FROM song_title_aliases a JOIN songs s ON s.song_id=a.song_id JOIN library_roots r ON r.root_id=s.root_id WHERE '+scope,args):
                extra[r['song_id']].append(r['display_title'])
            for r in rows:
                r['display_title'] = catalog_display_title(db, r)
                r.pop('payload_json')
        for r in rows:
            r['display_is_saved']=bool(r['saved_title'] and r['saved_title']!=r['latest_title'])
            # Downloads from multiplayer rooms (connect) are ordinary custom songs in the list; the pack
            # type stays in the database for the OCR monitor, which still tracks those downloads.
            r['category']=r['source_type']
            r['_title_candidates'] = [
                r['display_title'], r['saved_title'] or '', *extra[r['song_id']],
            ]
            r['_search']=folded('\n'.join([r['display_title'],r['relative_folder'],r['pack_name'] or '',r['file_id'] or '',r['gn'] or '',*extra[r['song_id']]]))
            r['_sort']=(folded(r['display_title']),r['song_id'])
        with self._cache_lock:self._cache[root_id]=(stamp,rows)
        return rows

    def packs(self,root_id):
        rows=self._catalog(root_id)
        categories={key:sum(r['category']==key for r in rows) for key in ('official','custom','download','unknown')}
        with self.open() as library:
            if root_id is None:
                # Packs of every location; the location name tells same-named packs apart.
                packs=[dict(r) for r in library.connection.execute('SELECT p.pack_id,p.name,p.pack_type,p.relative_path,p.root_id,r.path AS root_path FROM packs p JOIN library_roots r ON r.root_id=p.root_id ORDER BY p.root_id,p.name COLLATE NOCASE,p.pack_id')]
                for p in packs:p['root_label']=root_label(p.pop('root_path'))
                source_count=library.connection.execute('SELECT count(*) FROM song_sources').fetchone()[0]
            else:
                packs=[dict(r) for r in library.connection.execute('SELECT pack_id,name,pack_type,relative_path FROM packs WHERE root_id=? ORDER BY name COLLATE NOCASE,pack_id',(root_id,))]
                source_count=library.connection.execute('SELECT count(*) FROM song_sources f JOIN songs s ON s.song_id=f.song_id WHERE s.root_id=?',(root_id,)).fetchone()[0]
            official_count=library.connection.execute('SELECT count(*) FROM official_song_sources').fetchone()[0]
        for p in packs:
            songs=[r for r in rows if r['pack_id']==p['pack_id']]
            p['song_count']=len(songs)
            p['categories']={key:sum(r['category']==key for r in songs) for key in categories}
        return {'packs':packs,'categories':categories,'song_count':len(rows),'pack_count':len(packs),
                'source_count':source_count+official_count,'file_source_count':source_count,'official_source_count':official_count}

    def songs(self,root_id,*,query='',pack_id=None,category='all',order='asc',offset=0,limit=60):
        if category not in ('all','official','custom','download','unknown'):raise LibraryError('分類不正確。')
        if order not in ('asc','desc'):raise LibraryError('排序不正確。')
        integer(offset,'頁面起點');integer(limit,'每頁筆數',1)
        if limit>100:raise LibraryError('每頁最多 100 首。')
        if not isinstance(query,str) or len(query)>500:raise LibraryError('搜尋文字過長。')
        if pack_id is not None:integer(pack_id,'Pack ID',1)
        q=folded(query.strip())
        rows=[r for r in self._catalog(root_id) if (pack_id is None or r['pack_id']==pack_id)
              and (category=='all' or r['category']==category) and (not q or q in r['_search'])]
        rows.sort(key=lambda r:r['_sort'],reverse=order=='desc')
        page=[{k:v for k,v in r.items() if not k.startswith('_')} for r in rows[offset:offset+limit]]
        with self.open() as library:
            progress=progress_for_songs(library.connection,[r['song_id'] for r in page])
        for song in page:song['progress']=progress[song['song_id']]
        return {'total':len(rows),'offset':offset,'limit':limit,'songs':page}

    def _song(self,db,root_id,song_id):
        if root_id is not None:self._root(db,root_id)
        integer(song_id,'Song ID',1)
        row=db.execute('SELECT s.*,p.name AS pack_name,p.pack_type,r.path AS root_path FROM songs s '
                       'JOIN library_roots r ON r.root_id=s.root_id LEFT JOIN packs p ON p.pack_id=s.pack_id '
                       "WHERE s.song_id=? AND (? IS NULL OR s.root_id=? OR r.root_kind='official_catalog')",(song_id,root_id,root_id)).fetchone()
        if row is None:raise LibraryError('找不到此歌曲，請重新選取。',404)
        return row

    def _difficulty(self,db,song,label):
        row=db.execute('SELECT * FROM game_difficulties WHERE song_id=? AND difficulty=?',(song['song_id'],label)).fetchone()
        value=dict(row) if row else {'difficulty_id':None,'difficulty':label,'game_level':None,'total_notes':None,
                                    'level_source':None,'notes_source':None,'notes_verified':0}
        value['exists']=row is not None
        value['max_score']=2*value['total_notes'] if value['total_notes'] is not None and value['notes_verified'] else None
        value['not_provided']=song['source_type']=='official' and row is None
        value['token']=hashlib.sha256(json.dumps([song['song_id'],song['root_id'],song['relative_folder'],label,
                                      dict(row) if row else None],ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
        return value

    def detail(self,root_id,song_id):
        with self.open() as library:
            db=library.connection;song=self._song(db,root_id,song_id);raw=json.loads(song['payload_json'])
            sources=[]
            for r in db.execute('SELECT * FROM song_sources WHERE song_id=? ORDER BY source_id',(song_id,)):
                p=json.loads(r['payload_json'])
                sources.append({'source_id':r['source_id'],'relative_path':r['relative_path'],
                    'original_path':p['path'],'format':r['format'],'title':p.get('title'),'subtitle':p.get('subtitle'),
                    'artist':p.get('artist'),'saved_artist':r['saved_artist'],'saved_title':r['saved_title'],
                    'source_bpm':p.get('source_bpm'),'source_bpm_fields':p.get('source_bpm_fields',[]),
                    'title_fields':p.get('title_fields',{}),'artist_fields':p.get('artist_fields',{}),
                    'encoding':p.get('encoding'),'metadata_encodings':p.get('metadata_encodings',{}),
                    'metadata_candidates':p.get('metadata_candidates',{}),'diagnostics':p['diagnostics'],
                    'name_review_required':p.get('name_review_required',False),'is_present':bool(r['is_present'])})
            artists=list(dict.fromkeys(s['saved_artist'] or s['artist'] for s in sources if s['saved_artist'] or s['artist']))
            official_row=db.execute('SELECT o.*,i.source_name,i.csv_sha256 FROM official_song_sources o JOIN official_import_runs i ON i.import_id=o.import_id WHERE o.song_id=?',(song_id,)).fetchone()
            official=dict(official_row) if official_row else None
            if official:official['raw']=json.loads(official.pop('raw_row_json'));artists=[song['artist']] if song['artist'] else []
            result={key:song[key] for key in ('song_id','root_id','pack_id','pack_name','pack_type','relative_folder','folder_name','latest_title','saved_title','latest_status','source_type','is_present')}
            result.update(display_title=catalog_display_title(db, song),
                          original_path=None if official else str(Path(song['root_path'])/song['relative_folder']),artists=artists,sources=sources,official=official,
                          formats=sorted({s['format'] for s in sources}),diagnostics=raw['diagnostics'],
                          title_candidates=raw.get('title_candidates',[]),
                          difficulties=[self._difficulty(db,song,label) for label in GAME_DIFFICULTIES])
            result['progress']=progress_for_songs(db,[song_id])[song_id]
            return result

    def save_difficulty(self,body):
        if not isinstance(body,dict):raise LibraryError('資料格式不正確。')
        root_id=integer(body.get('root_id'),'歌曲庫 ID',1);song_id=integer(body.get('song_id'),'Song ID',1)
        label=body.get('difficulty')
        if label not in GAME_DIFFICULTIES:raise LibraryError('遊戲難度只允許 EASY／NORMAL／HARD。')
        if body.get('exists') is not True:raise LibraryError('請先確認此歌曲具有這個遊戲難度。')
        level,notes=body.get('game_level'),body.get('total_notes')
        if level is not None:integer(level,'LV')
        if notes is not None:integer(notes,'總音符數',1)
        if not self._write_lock.acquire(blocking=False):raise LibraryError('歌曲庫正在更新，請完成後再儲存。',409)
        try:
            with self.open(False) as library:
                db=library.connection
                try:
                    db.execute('BEGIN IMMEDIATE')
                    song=self._song(db,root_id,song_id)
                    if song['source_type']=='official':raise LibraryError('官方 CSV 難度目前為來源檢視，不能以自製歌曲編輯流程覆寫。',409)
                    if body.get('song_folder')!=song['relative_folder']:raise LibraryError('Song ID 與資料夾不一致，未寫入。',409)
                    current=self._difficulty(db,song,label)
                    if body.get('token')!=current['token']:raise LibraryError('此難度資料已變動；請重新載入後再編輯。',409)
                    level_source=current['level_source'] if current['exists'] and current['game_level']==level else ('manual' if level is not None else None)
                    notes_source=current['notes_source'] if current['exists'] and current['total_notes']==notes else ('manual' if notes is not None else None)
                    verified=current['notes_verified'] if current['exists'] and current['total_notes']==notes else int(notes is not None)
                    db.execute('INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,notes_source,notes_verified,level_source) '
                               'VALUES(?,?,?,?,?,?,?) ON CONFLICT(song_id,difficulty) DO UPDATE SET game_level=excluded.game_level,total_notes=excluded.total_notes,'
                               'notes_source=excluded.notes_source,notes_verified=excluded.notes_verified,level_source=excluded.level_source',
                               (song_id,label,level,notes,notes_source,verified,level_source))
                    value=self._difficulty(db,song,label);db.commit()
                    return value
                except BaseException:db.rollback();raise
        finally:self._write_lock.release()

    def scan_status(self):
        with self._scan_lock:return copy.deepcopy(self._scan)

    def start_scan(self,root_id,*,defer_not_ready=False):
        with self.open() as library:
            record=self._root(library.connection,root_id)
            if record['root_kind']!='filesystem':raise LibraryError('熱舞官方譜面使用 CSV 匯入，不執行歌曲資料夾掃描。')
            root=Path(record['path'])
        if not root.is_dir():raise LibraryError('SONG 路徑目前不存在；既有資料保留，沒有執行掃描。')
        if not self._write_lock.acquire(blocking=False):raise LibraryError('已有更新作業進行中。',409)
        from uuid import uuid4
        job_id=str(uuid4())
        with self._scan_lock:self._scan={'status':'running','job_id':job_id,'root_id':root_id,'processed':0,'current_file':'',
                                       'scan_scope':'whole_song_root','defer_not_ready':defer_not_ready}
        def work():
            try:
                def progress(n,p):
                    if n==1 or n%25==0:
                        with self._scan_lock:self._scan.update(processed=n,current_file=str(p.relative_to(root)))
                report=scan_library(root,progress=progress,**({'defer_not_ready':True} if defer_not_ready else {}))
                with self.open(False) as library:library.sync(report)
                diagnostics=[*report.diagnostics,*[d for s in report.songs for d in s.diagnostics],*[d for f in report.files for d in f.diagnostics]]
                with self._cache_lock:self._cache.clear()
                with self._scan_lock:self._scan={'status':'completed','job_id':job_id,'root_id':root_id,'processed':len(report.files),
                    'scan_id':report.database['scan_id'],
                    'scan_scope':'whole_song_root','deferred_sources':sum(d.code=='SOURCE_NOT_READY' for d in report.diagnostics),
                    'deferred_folders':report.deferred_folders,
                    'changes':report.database['changes'],'warning_count':sum(d.severity!='info' for d in diagnostics),
                    'error_count':sum(d.severity=='error' for d in diagnostics),'complete_scan':report.database['complete_scan']}
            except Exception as error:
                with self._scan_lock:self._scan={'status':'failed','job_id':job_id,'root_id':root_id,'message':str(error)}
            finally:self._write_lock.release()
        try:threading.Thread(target=work,name='song-library-scan',daemon=True).start()
        except Exception:
            with self._scan_lock:self._scan={'status':'failed','root_id':root_id,'message':'無法啟動更新作業。'}
            self._write_lock.release()
            raise
        return self.scan_status()

    def official_catalog(self,body,*,apply=False):
        value=body.get('csv_base64');name=body.get('file_name')
        if not isinstance(value,str) or len(value)>6*1024*1024 or not isinstance(name,str) or not 0<len(name)<=1024:
            raise LibraryError('CSV 檔案資料不正確。')
        try:catalog=read_catalog(base64.b64decode(value,validate=True),name)
        except ValueError as error:raise LibraryError(str(error)) from error
        if not apply:
            try:return preview_catalog(self.path,catalog)
            except ValueError as error:raise LibraryError(str(error)) from error
        if body.get('expected_sha256')!=catalog.sha256:raise LibraryError('CSV 與預覽不同，請重新預覽。',409)
        if not self._write_lock.acquire(blocking=False):raise LibraryError('歌曲庫正在寫入，請完成後再匯入。',409)
        try:
            try:result=import_catalog(self.path,catalog,expected_sha256=body['expected_sha256'])
            except ValueError as error:raise LibraryError(str(error),409) from error
            with self._cache_lock:self._cache.clear()
            return result
        finally:self._write_lock.release()
