"""Local HTML/JS GUI, served by Python's standard library on loopback only."""
from __future__ import annotations
import argparse
import html
import json
import logging
import secrets
import sqlite3
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .app_paths import default_database, ocr_available
from .database import LibraryDatabase
from .game_folder import GameFolderError
from .game_sync import GameSync
from .library_service import LibraryError, LibraryService
from .monitor import Monitor
from .submissions import SubmissionService
from .verified_auto import AutomaticStorage

ASSETS=Path(__file__).with_name('gui_assets')
DEFAULT_DATABASE=default_database()


def create_server(database,port=0,monitor_directory=None,*,game_sync=True):
    path=Path(database).expanduser().resolve()
    if not path.is_file():
        # Game folder mode needs no prior scan: start from an empty library.
        path.parent.mkdir(parents=True,exist_ok=True)
        with LibraryDatabase(path,[]):pass
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        columns=[r[1] for r in db.execute('PRAGMA table_info(library_roots)')]
        condition=" WHERE root_kind='filesystem'" if 'root_kind' in columns else ''
        roots=[Path(r[0]) for r in db.execute('SELECT path FROM library_roots'+condition)]
    with LibraryDatabase(path,roots) as db:migrations=db.migration_results
    service=LibraryService(path)
    monitor=Monitor(path,monitor_directory)
    submissions=SubmissionService(service,monitor.reviews)
    automatic=AutomaticStorage(submissions)
    from .download_tracking import DownloadTracker
    monitor.downloads=DownloadTracker(service,monitor.reviews,on_update=automatic.enqueue)
    game = GameSync(
        path, path.parent, write_lock=service._write_lock,
        event_feed=automatic.event_feed,
    )
    if game_sync:game.start()
    ocr=ocr_available()
    token=secrets.token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def _response(self,status,data,content_type='application/json; charset=utf-8'):
            if status>=400 and self.command=='POST' and not getattr(self,'_body_consumed',False):
                # Closing a Windows TCP socket with an unread small POST body
                # can reset it before the browser receives the 403. Discard a
                # bounded body without parsing or authorizing its contents.
                try:
                    remaining=int(self.headers.get('Content-Length','0'))
                    if 0<remaining<=32768:
                        self.connection.settimeout(.25)
                        self.rfile.read(remaining)
                        self._body_consumed=True
                except (ValueError,OSError):pass
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            try:
                self.end_headers()
                self.wfile.write(data)
            except ConnectionError:
                # Navigation and AbortController can close a GET while its
                # response is being sent. The client is gone; do not attempt a
                # second 500 response or log this as an application failure.
                self.close_connection=True

        def _check(self,api=False):
            expected=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=expected:raise LibraryError('此服務僅接受本機網址。',403)
            origin=self.headers.get('Origin')
            if origin is not None and origin!='http://'+expected:raise LibraryError('不接受其他網頁的資料請求。',403)
            if api and not secrets.compare_digest(self.headers.get('X-Dance-Token',''),token):
                raise LibraryError('工作階段已失效，請重新開啟介面。',403)

        def do_GET(self):
            try:
                parsed=urlsplit(self.path);self._check(parsed.path.startswith('/api/'))
                pages = {
                    '/': 'index.html',
                    '/monitor': 'monitor.html',
                    '/reviews': 'reviews.html',
                    '/history': 'history.html',
                    '/plays': 'plays.html',
                }
                if parsed.path in pages:
                    source = (ASSETS / pages[parsed.path]).read_text(encoding='utf-8')
                    source = source.replace('SESSION_TOKEN', html.escape(token, quote=True))
                    source=source.replace('PLAYER_NAME_SETTING', html.escape(monitor.player_settings.get(), quote=True))
                    return self._response(200,source.encode('utf-8'),'text/html; charset=utf-8')
                assets={'/app.js':('app.js','text/javascript; charset=utf-8'),'/monitor.js':('monitor.js','text/javascript; charset=utf-8'),'/reviews.js':('reviews.js','text/javascript; charset=utf-8'),'/reviews.css':('reviews.css','text/css; charset=utf-8'),'/history.js':('history.js','text/javascript; charset=utf-8'),'/history.css':('history.css','text/css; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
                assets.update({'/lamps.js':('lamps.js','text/javascript; charset=utf-8'),'/lamps.css':('lamps.css','text/css; charset=utf-8')})
                assets['/source-choice.js'] = ('source-choice.js', 'text/javascript; charset=utf-8')
                assets['/player-settings.js'] = ('player-settings.js', 'text/javascript; charset=utf-8')
                assets['/game-sync.js'] = ('game-sync.js', 'text/javascript; charset=utf-8')
                assets['/plays.js'] = ('plays.js', 'text/javascript; charset=utf-8')
                if parsed.path in assets:
                    file,mime=assets[parsed.path]
                    return self._response(200,(ASSETS/file).read_bytes(),mime)
                args=parse_qs(parsed.query,keep_blank_values=True)
                def number(name,default=None):
                    value=args.get(name,[default])[0]
                    if value is None:return None
                    try:return int(value)
                    except (ValueError,TypeError):raise LibraryError(name+' 必須是整數。')
                if parsed.path=='/api/bootstrap':result=service.bootstrap()
                elif parsed.path=='/api/game':result=dict(game.status(),ocr_available=ocr)
                elif parsed.path=='/api/player-settings':result=dict(player_name=monitor.player_settings.get())
                elif parsed.path=='/api/history':
                    result=submissions.history(song_id=number('song_id'),difficulty=args.get('difficulty',[None])[0],
                        player_name=args.get('player_name',[None])[0],offset=number('offset',0),limit=number('limit',60),
                        lamp=args.get('lamp',[''])[0],title=args.get('title',[None])[0],
                        artist=args.get('artist',[None])[0],hide_no_update=True)
                elif parsed.path == '/api/plays/top':
                    result = submissions.top_plays(number('limit', 30))
                elif parsed.path == '/api/history/song-search':
                    result = submissions.song_search(
                        args.get('q', [''])[0], limit=number('limit', 20),
                        song_id=number('song_id'),
                    )
                elif parsed.path=='/api/progress-history':
                    result=submissions.progress_history(song_id=number('song_id'),difficulty=args.get('difficulty',[None])[0],
                        offset=number('offset',0),limit=number('limit',20),order=args.get('order',['desc'])[0])
                elif parsed.path=='/api/history/detail':result=submissions.detail(number('record_id'))
                elif parsed.path=='/api/history/image':return self._response(200,submissions.image(number('record_id')),'image/png')
                elif parsed.path=='/api/packs':result=service.packs(number('root_id'))
                elif parsed.path=='/api/songs':
                    result=service.songs(number('root_id'),query=args.get('q',[''])[0],pack_id=number('pack_id'),
                                         category=args.get('category',['all'])[0],order=args.get('order',['asc'])[0],
                                         offset=number('offset',0),limit=number('limit',60),lamp=args.get('lamp',[''])[0])
                elif parsed.path.startswith('/api/songs/'):
                    try:song_id=int(parsed.path.removeprefix('/api/songs/'))
                    except ValueError:raise LibraryError('Song ID 不正確。')
                    result=service.detail(number('root_id'),song_id)
                elif parsed.path=='/api/scan':result=service.scan_status()
                elif parsed.path=='/api/recent-updates':
                    from .recent_updates import recent_updates
                    with service.open() as lib:result=recent_updates(lib.connection)
                elif parsed.path=='/api/monitor/status':result=monitor.status()
                elif parsed.path=='/api/monitor/windows':result={'windows':monitor.windows(args.get('all',['0'])[0]=='1')}
                elif parsed.path=='/api/monitor/result':return self._response(200,monitor.result_image(),'image/png')
                elif parsed.path=='/api/monitor/packs':result=monitor.packs()
                elif parsed.path=='/api/reviews/storage':result=automatic.status()
                elif parsed.path=='/api/reviews/source-prompts':result=monitor.reviews.source_prompts()
                elif parsed.path=='/api/reviews':result=monitor.reviews.list()
                elif parsed.path=='/api/reviews/detail':result=monitor.reviews.detail(args.get('session_id',[''])[0])
                elif parsed.path=='/api/reviews/download':
                    result=monitor.reviews.download_detail(args.get('session_id',[''])[0])
                elif parsed.path=='/api/reviews/candidates':
                    values=None
                    if args.get('session_id'):
                        values=monitor.reviews._read(args['session_id'][0])[1]['draft']['values']
                    result=monitor.reviews.candidates(args.get('q',[''])[0],args.get('source_type',['all'])[0],
                        number('offset',0),number('limit',60),values)
                elif parsed.path=='/api/reviews/scan':
                    result=dict(roots=[r for r in service.bootstrap()['roots'] if r['root_kind']=='filesystem'],scan=service.scan_status())
                elif parsed.path=='/api/reviews/image':return self._response(200,monitor.reviews.image(args.get('session_id',[''])[0]),'image/png')
                else:raise LibraryError('找不到此頁面。',404)
                self._response(200,result)
            except LibraryError as error:self._response(error.status,{'error':str(error)})
            except Exception:
                logging.exception('GUI request failed')
                self._response(500,{'error':'讀取失敗，請重新載入；詳細原因已顯示在啟動視窗。'})

        def do_POST(self):
            try:
                self._check(True)
                route=urlsplit(self.path).path
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise LibraryError('只接受 JSON 資料。')
                try:length=int(self.headers.get('Content-Length','0'))
                except ValueError:raise LibraryError('資料長度不正確。')
                maximum=8*1024*1024 if route in ('/api/official/preview','/api/official/import') else 32768
                if not 0<length<=maximum:raise LibraryError('資料長度不正確。')
                self.connection.settimeout(15)
                try:
                    payload=self.rfile.read(length)
                    self._body_consumed=True
                    body=json.loads(payload)
                except (ValueError,UnicodeError):raise LibraryError('JSON 資料不正確。')
                if not isinstance(body,dict):raise LibraryError('資料格式不正確。')
                if route=='/api/game/settings':
                    # Answer only after a pass ran, so the button can say what happened.
                    try:result=dict(game.save_and_sync(body.get('game_dir'),body.get('character')),ocr_available=ocr)
                    except GameFolderError as error:raise LibraryError(str(error))
                    # The chosen character is "me" everywhere, including the OCR monitor's player name.
                    if result.get('character_name'):monitor.player_settings.set(result['character_name'])
                elif route=='/api/game/sync':
                    result=dict(game.sync_now(),ocr_available=ocr)
                elif route=='/api/difficulty':result=service.save_difficulty(body)
                elif route=='/api/player-settings':result=dict(player_name=monitor.player_settings.set(body.get('player_name')))
                elif route=='/api/scan':result=service.start_scan(body.get('root_id'))
                elif route=='/api/official/preview':result=service.official_catalog(body)
                elif route=='/api/official/import':result=service.official_catalog(body,apply=True)
                elif route=='/api/monitor/start':result=monitor.start(body)
                elif route=='/api/monitor/stop':result=monitor.stop()
                elif route=='/api/monitor/confirm':result=monitor.confirm(body)
                elif route=='/api/monitor/confirm-pack':result=monitor.confirm_pack(body)
                elif route=='/api/reviews/storage':result=automatic.configure(body)
                elif route=='/api/reviews/source-choice':
                    result=monitor.reviews.choose_source(body)
                    if not body.get('dismiss') and automatic.allowed(body.get('session_id')):
                        try:
                            result['submission']=submissions.submit_verified(body.get('session_id'))
                        except LibraryError as error:
                            result.update(status='pending_review', reason=str(error))
                elif route=='/api/reviews/retry-auto':
                    if not automatic.allowed(body.get('session_id')):raise LibraryError('此 Session 不在自動儲存範圍；請啟用模式後遊玩新的一首，既有待確認資料仍需人工提交。',409)
                    automatic.enqueue(body.get('session_id'));result=automatic.status()
                elif route=='/api/reviews/process':result=monitor.reviews.process(body.get('session_id'),body.get('player_name'))
                elif route=='/api/reviews/preview':result=monitor.reviews.preview(body)
                elif route=='/api/reviews/assist':result=monitor.reviews.assist(body)
                elif route=='/api/reviews/save':result=monitor.reviews.save(body)
                elif route=='/api/reviews/discard':result=monitor.reviews.discard(body)
                elif route=='/api/reviews/submission-preview':result=submissions.prepare(body)
                elif route=='/api/reviews/submit':result=submissions.submit(body)
                elif route=='/api/reviews/rescan':
                    _,review=monitor.reviews._read(body.get('session_id'))
                    if body.get('review_id')!=review['review_id']:raise LibraryError('結算身份已變更，請重新載入。',409)
                    result=service.start_scan(body.get('root_id'),defer_not_ready=True)
                    monitor.downloads.retry(body.get('session_id'))
                else:raise LibraryError('找不到此功能。',404)
                self._response(200,result)
            except LibraryError as error:self._response(error.status,{'error':str(error)})
            except Exception:
                logging.exception('GUI write failed')
                self._response(500,{'error':'操作失敗，沒有回報儲存成功；請重新載入檢查，詳細原因見啟動視窗。'})

    class Server(ThreadingHTTPServer):
        def server_close(self):
            game.close()
            automatic.close()
            monitor.stop(wait=3)
            monitor.downloads.close()
            super().server_close()

    server=Server(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.service=service
    server.monitor=monitor
    server.submissions=submissions
    server.automatic=automatic
    server.session_token=token
    server.migrations=migrations
    server.game=game
    return server


def main(argv=None):
    parser=argparse.ArgumentParser(description='熱舞 Online 歌曲資料庫 GUI（僅在本機運作）')
    parser.add_argument('--database',type=Path,default=DEFAULT_DATABASE)
    parser.add_argument('--port',type=int,default=0,help='本機連接埠；預設自動選擇')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--monitor-directory',type=Path,help='Session、診斷與人工對應目錄；預設 data/monitor')
    args=parser.parse_args(argv)
    try:server=create_server(args.database,args.port,args.monitor_directory)
    except (OSError,ValueError,sqlite3.Error) as error:
        print('無法啟動歌曲庫介面：'+str(error),flush=True);return 2
    url=f'http://127.0.0.1:{server.server_port}/'
    for migration in server.migrations:
        if migration['backup_path']:print('資料庫已安全遷移，備份：'+migration['backup_path'],flush=True)
    print('歌曲資料庫 GUI：'+url+'\n保持此視窗開啟；按 Ctrl+C 關閉本機服務。',flush=True)
    if not args.no_browser:
        try:webbrowser.open(url)
        except OSError:print('請手動開啟上方網址。',flush=True)
    try:server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0


if __name__=='__main__':raise SystemExit(main())
