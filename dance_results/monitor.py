"""Bounded live capture/OCR worker behind the existing local GUI."""
from __future__ import annotations

import copy
import math
import json
from pathlib import Path
import statistics
import threading
import time

from .library_service import LibraryError
from .monitor_tracking import Tracker, ManualReferences, utc
from .monitor_ocr import Analyzer, apply_selection, change_signature, change_amount
from .ocr_matching import ReadOnlyCatalog
from .window_capture import Windows, WGCCapture, CaptureError

PROJECT = Path(__file__).resolve().parent.parent


class Monitor:
    def __init__(self, database, directory=None, windows_factory=Windows, capture_factory=WGCCapture, engine_factory=None):
        self.database = Path(database)
        default=(PROJECT/'data'/'monitor' if self.database.resolve()==(PROJECT/'data'/'library.sqlite3').resolve()
                 else self.database.parent/(self.database.stem+'-monitor'))
        self.directory = Path(directory or default)
        self.windows_factory = windows_factory
        self.capture_factory = capture_factory
        self.engine_factory = engine_factory
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.running = False
        self.error = None
        self.connection = 'stopped'
        self.window = None
        self.tracker = None
        self.references = None
        self.config = {}
        self.metrics = {}
        self.latest_image = None
        self.catalog = None
        from .result_review import ResultReviews
        self.reviews = ResultReviews(self.database, self.directory, engine_factory)
        self.downloads=None

    def windows(self, all_windows=False):
        try:
            return self.windows_factory().list(all_windows)
        except Exception as error:
            raise LibraryError(str(error)) from error

    def start(self, body):
        with self.lock:
            if self.running:
                raise LibraryError('監控已啟動或正在停止，請等候停止完成。', 409)
            def number(name, default, minimum, maximum):
                try:
                    value = float(body.get(name, default))
                except (TypeError, ValueError):
                    raise LibraryError(name+' 必須是數字。')
                if not math.isfinite(value) or not minimum <= value <= maximum:
                    raise LibraryError(f'{name} 必須在 {minimum}–{maximum} 之間。')
                return value
            self.config = dict(capture_hz=number('capture_hz', 2, .2, 10),
                               ocr_interval=number('ocr_interval', 2, .5, 30),
                               confirmations=int(number('confirmations', 2, 2, 5)),
                               selection_ttl=number('selection_ttl', 60, 5, 600),
                               player_name=str(body.get('player_name', 'TC')).strip()[:64],
                               source_mode=body.get('source_mode', 'auto'))
            if not self.config['player_name'] or self.config['source_mode'] not in ('auto', 'official', 'custom'):
                raise LibraryError('暱稱或來源分類不正確。')
            hwnd = body.get('window_hwnd')
            if hwnd is not None and (type(hwnd) is not int or hwnd <= 0):
                raise LibraryError('視窗 Handle 不正確。')
            self.config['window_hwnd'] = hwnd
            self.config['window_pid'] = body.get('window_pid')
            marker=self.directory/'catalog-location.json'
            if marker.exists():
                try:location=json.loads(marker.read_text(encoding='utf-8'))['database_path']
                except (OSError,ValueError,KeyError) as error:raise LibraryError('監控資料庫來源標記損壞，請使用獨立目錄。') from error
                if Path(location).resolve()!=self.database.resolve():
                    raise LibraryError('此監控目錄屬於另一份 SQLite，請指定獨立監控目錄。',409)
            else:
                from .monitor_tracking import atomic_json
                atomic_json(marker,dict(database_path=str(self.database.resolve())))
            self.tracker = Tracker(self.directory, self.config['confirmations'], self.config['selection_ttl'])
            # Recover unfinished sessions from a prior process crash without inventing results.
            sessions = []
            for path in (self.directory/'sessions').glob('*/session.json'):
                try:
                    row = json.loads(path.read_text(encoding='utf-8'))
                    from uuid import UUID
                    if str(UUID(path.parent.name))!=path.parent.name or row['session_id']!=path.parent.name:
                        raise ValueError('Invalid session identity')
                    if row['status'] == 'playing':
                        from .monitor_tracking import atomic_json
                        row.update(status='interrupted', ended_at=utc(), interruption_reason='monitor_process_restarted')
                        atomic_json(path, row)
                    sessions.append(row)
                except (OSError, ValueError, KeyError,TypeError):
                    self.tracker.log('invalid_session_file', file=path.name)
            if sessions:
                self.tracker.last_session = max(sessions, key=lambda r: r['started_at'])
                completed = [r for r in sessions if r.get('result') and r['result'].get('screenshot')]
                if completed:
                    last = max(completed, key=lambda r: r['started_at'])
                    self.tracker.last_result = dict(session_id=last['session_id'], screenshot='result.png',
                                                    state=last['result']['state'], captured_at=last['ended_at'])
            self.references = ManualReferences(self.directory, self.database)
            self.error = None
            self.metrics = dict(captured_frames=0, analyzed_frames=0, skipped_frames=0, ocr_calls=0,
                                capture_copy_ms=None, ocr_ms=None, ocr_p50_ms=None, ocr_p95_ms=None,
                                process_cpu_percent=None, rss_mb=None, peak_rss_mb=None)
            self.running = True
            self.connection = 'initializing'
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name='Dance-monitor-ocr', daemon=True)
            if self.downloads:self.downloads.arm()
            self.thread.start()
            return self.status()

    def stop(self, wait=.15):
        self.stop_event.set()
        thread = self.thread
        if thread and thread.is_alive():
            thread.join(timeout=wait)
        return self.status()

    def status(self):
        with self.lock:
            result = dict(running=self.running, stopping=self.running and self.stop_event.is_set(),
                          connection=self.connection, error=self.error, window=copy.deepcopy(self.window),
                          config=copy.deepcopy(self.config), metrics=copy.deepcopy(self.metrics),
                          output_directory=str(self.directory.resolve()),downloads=self.downloads.status() if self.downloads else None,
                          tracking=self.tracker.snapshot() if self.tracker else None)
            return result

    def _connection(self, status, error=None):
        with self.lock:
            if self.connection != status or self.error != error:
                self.tracker.log('connection', status=status, error=error)
            self.connection = status
            self.error = error

    def _run(self):
        capture = None
        current = None
        catalog = None
        try:
            from .ocr_engine import LocalOCREngine
            catalog = ReadOnlyCatalog(self.database)
            # This SQLite connection stays in this worker. GUI confirmation uses its own read-only connection.
            windows = self.windows_factory()
            engine = self.engine_factory() if self.engine_factory else LocalOCREngine(PROJECT/'.ocr-models', low_latency=True)
            analyzer = Analyzer(engine, self.config['player_name'])
            from .ocr_temporal import ResultConsistency
            result_consistency=ResultConsistency()
            try:
                import psutil
                process = psutil.Process()
                process.cpu_percent(None)
            except ImportError:
                process = None
            durations = []
            catalog_generation=0
            last_ocr = -1e9
            last_full = -1e9
            signature = None
            last_native = time.monotonic()
            pending_manual = self.config['window_hwnd']
            while not self.stop_event.is_set():
                now = time.monotonic()
                if current:
                    live = windows.info(current['hwnd'])
                    if not live or live['pid'] != current['pid']:
                        with self.lock:
                            self.tracker.interrupt('game_window_closed')
                            self.window = None
                        try:capture.stop()
                        except Exception:pass
                        capture = None; current = None
                        signature = None
                        self._connection('window_not_found', '遊戲視窗已關閉；等候重新啟動。')
                        continue
                    current = live
                else:
                    if pending_manual:
                        found = windows.info(pending_manual)
                        if found and self.config.get('window_pid') not in (None, found['pid']):
                            found = None
                        pending_manual = None
                    else:
                        available = windows.list()
                        found = available[0] if len(available) == 1 else None
                        if len(available) > 1:
                            self._connection('ambiguous_windows', '找到多個遊戲視窗，請手動選擇。')
                    if not found:
                        if self.connection != 'ambiguous_windows':
                            self._connection('window_not_found', '找不到遊戲視窗；可手動選擇，或等候遊戲啟動。')
                        self.stop_event.wait(1)
                        continue
                    current = found
                    with self.lock:
                        self.tracker.new_window(dict(hwnd=current['hwnd'], pid=current['pid'], title=current['title']))
                        self.window = current
                    try:
                        capture = self.capture_factory(current['hwnd'], self.config['capture_hz'])
                    except Exception as error:
                        current = None
                        self._connection('capture_error', str(error))
                        self.stop_event.wait(2)
                        continue
                    last_native = now
                    last_ocr = -1e9
                    last_full = -1e9
                    signature = None
                with self.lock:
                    self.window = current
                if current['minimized']:
                    if self.connection != 'minimized':
                        with self.lock:
                            self.tracker.invalidate_selection('window_minimized')
                    self._connection('minimized', '遊戲已最小化；暫停辨識，現有遊玩 Session 保留。')
                    self.stop_event.wait(.25)
                    continue
                if capture.closed or capture.error:
                    self._connection('capture_error', capture.error or '擷取串流關閉，將重新連接。')
                    with self.lock:
                        self.tracker.invalidate_selection('capture_stream_lost')
                    try:capture.stop()
                    except Exception:pass
                    capture = None; current = None
                    self.stop_event.wait(1)
                    continue
                frame = capture.take()
                if frame is None:
                    if now-last_native > max(8, self.config['ocr_interval']*3):
                        self._connection('capture_stale', '暫無新畫面；不使用舊畫面建立新 Session 或結算。')
                        if signature is not None:
                            with self.lock:
                                self.tracker.invalidate_selection('capture_stale')
                            signature = None
                    self.stop_event.wait(.05)
                    continue
                sampled, image = frame
                last_native = now
                body=image.crop((0,int(image.height*.06),image.width,int(image.height*.94))).convert('L').resize((32,24))
                if body.getextrema()[1]<4:
                    self._connection('capture_error','擷取到全黑畫面；不使用此畫面判定遊戲結果。')
                    with self.lock:self.tracker.invalidate_selection('black_capture')
                    self.stop_event.wait(.2)
                    continue
                self._connection('connected')
                with self.lock:
                    self.metrics['captured_frames'] += 1
                    self.metrics['capture_copy_ms'] = round(capture.copy_ms, 3)
                if now-last_ocr < self.config['ocr_interval']:
                    with self.lock:self.metrics['skipped_frames'] += 1
                    continue
                next_signature = change_signature(image)
                with self.lock:
                    confirmed = self.tracker.count >= self.config['confirmations']
                    current_state = self.tracker.state
                heartbeat = now-last_ocr >= max(8, self.config['ocr_interval']*2)
                if confirmed and change_amount(signature, next_signature) < 2 and not heartbeat:
                    with self.lock:self.metrics['skipped_frames'] += 1
                    continue
                started = time.perf_counter()
                full = current_state not in ('PLAYING','SONG_SELECT') or now-last_full > 20
                observation = analyzer.analyze(image, current_state, full)
                elapsed = (time.perf_counter()-started)*1000
                if self.stop_event.is_set():
                    break
                signature = next_signature
                last_ocr = time.monotonic()
                if full:last_full = last_ocr
                durations.append(elapsed); durations = durations[-200:]
                with self.lock:
                    if self.downloads and self.downloads.generation!=catalog_generation:
                        refreshed=time.perf_counter();catalog.close();catalog=ReadOnlyCatalog(self.database);catalog_generation=self.downloads.generation
                        self.metrics['catalog_refresh_ms']=round((time.perf_counter()-refreshed)*1000,2)
                    observation = apply_selection(observation, image, self.tracker, catalog, self.references,
                                                  self.config['source_mode'])
                    result_consistency.observe(observation,(self.tracker.session or {}).get('session_id'),sampled,image)
                    self.latest_image = image
                    self.tracker.consume(observation, now=last_ocr, image=image, sample_id=sampled)
                    if self.downloads:self.downloads.observe(observation,self.tracker,image)
                    pending = self.tracker.last_session
                    if observation.get('result_ocr') and pending and pending.get('status') == 'pending_result' and (
                            pending.get('result') or {}).get('frame_hash') == observation['frame_hash']:
                        # Reuse these OCR tokens once; repeated frames cannot overwrite human review.
                        folder=self.directory/'sessions'/pending['session_id']
                        if not (folder/'result-review.json').exists():
                            result_consistency.save(folder,observation['result_ocr'])
                        self.reviews.ingest(pending, observation['result_ocr'])
                    self.metrics.update(analyzed_frames=self.metrics['analyzed_frames']+1, ocr_calls=analyzer.calls,
                        ocr_ms=round(elapsed, 2), ocr_p50_ms=round(statistics.median(durations), 2),
                        ocr_p95_ms=round(sorted(durations)[max(0, math.ceil(len(durations)*.95)-1)], 2))
                    if process:
                        rss = process.memory_info().rss/1024**2
                        cpu=process.cpu_percent(None)
                        cores=psutil.cpu_count() or 1
                        self.metrics.update(process_cpu_percent=cpu,cpu_unit='100% = one logical CPU',
                                            logical_cpu_count=cores,cpu_machine_percent=round(cpu/cores,2),rss_mb=round(rss, 1),
                                            peak_rss_mb=round(max(rss, self.metrics['peak_rss_mb'] or 0), 1))
                    self.tracker.log('recognition', state=observation['state'], frame_hash=observation['frame_hash'],
                                     ocr_ms=round(elapsed, 2), roi_mode=observation['roi_mode'])
        except Exception as error:
            self._connection('error', f'{type(error).__name__}: {error}')
        finally:
            if self.downloads:self.downloads.pause()
            if capture:
                try:capture.stop()
                except Exception:pass
            if catalog:catalog.close()
            with self.lock:
                self.tracker.interrupt('monitor_stopped' if self.stop_event.is_set() else 'monitor_error')
                self.running = False
                if self.stop_event.is_set():
                    self.connection = 'stopped'
                    self.error = None

    def packs(self):
        catalog = ReadOnlyCatalog(self.database)
        try:
            return dict(packs=catalog.packs)
        finally:
            catalog.close()

    def confirm_pack(self, body):
        with self.lock:
            selection = self.tracker.selection if self.tracker else None
            if not self.running or not selection or body.get('observation_id') != selection['observation_id']:
                raise LibraryError('選歌已變更或尚未穩定，請重新核對 Pack。', 409)
            if self.tracker.pack_list_visible or self.tracker.state != 'SONG_SELECT' or self.tracker.session:
                raise LibraryError('請先關閉 Pack 清單，停在選歌畫面再人工確認。', 409)
            if body.get('acknowledge') is not True:
                raise LibraryError('請核對目前實際選取的 Pack。')
            catalog = ReadOnlyCatalog(self.database)
            try:
                pack = next((p for p in catalog.packs if p['pack_id'] == body.get('pack_id') and
                             p['root_id'] == body.get('root_id')), None)
                if not pack:
                    raise LibraryError('Pack ID／Root 不正確。')
                self.tracker.current_pack = dict(pack_id=pack['pack_id'], root_id=pack['root_id'], name=pack['name'],
                    status='confirmed', source='explicit_user_review', confirmed_at=utc())
                self.tracker.current_source = 'custom'
                self.tracker.invalidate_selection('pack_manually_confirmed_recheck_song', clear_pack=False)
                self.tracker.log('pack_confirmed_manually', pack=self.tracker.current_pack)
            finally:
                catalog.close()
            return self.status()

    def confirm(self, body):
        with self.lock:
            selection = self.tracker.selection if self.tracker else None
            if not self.running or not selection or selection['observation_id'] != body.get('observation_id'):
                raise LibraryError('選歌已變更或尚未穩定，請重新核對。', 409)
            if self.tracker.state not in ('SONG_SELECT', 'WAITING_ROOM', 'PROFILE') or self.tracker.session:
                raise LibraryError('僅能確認尚未開始遊玩的當前歌曲。', 409)
            if body.get('acknowledge') is not True:
                raise LibraryError('請先核對 Song ID、Pack／資料夾及全部遊戲數值。')
            song_id = body.get('song_id')
            if type(song_id) is not int or song_id not in {c['song_id'] for c in selection['matching']['candidates']}:
                raise LibraryError('Song ID 不在當前候選中。')
            fields = copy.deepcopy(selection['fields'])
            original_values = {k: v['value'] for k, v in fields.items()}
            for name, maximum in [('game_level', 10000), ('total_notes', 10000000), ('duration_seconds', 36000)]:
                value = body.get(name)
                if type(value) is not int or not 1 <= value <= maximum:
                    raise LibraryError(name+' 必須是有效正整數。')
                fields[name] = dict(value=value, confidence=1.0, method='manual', evidence=[])
            difficulty = body.get('difficulty')
            if difficulty not in ('EASY', 'NORMAL', 'HARD'):
                raise LibraryError('請確認 EASY／NORMAL／HARD。')
            fields['difficulty'] = dict(value=difficulty, confidence=1.0, method='manual', evidence=[])
            with_catalog = ReadOnlyCatalog(self.database)
            try:
                song = with_catalog.songs.get(song_id)
                if not song or song['relative_folder'] != body.get('relative_path') or song['root_id'] != body.get('root_id'):
                    raise LibraryError('歌曲來源身份已變更，請重新載入。', 409)
                try:row = self.references.add(fields, song, selection['frame_hash'])
                except ValueError as error:raise LibraryError(str(error), 409) from error
                if song['pack_id'] is not None:
                    pack = next(p for p in with_catalog.packs if p['pack_id'] == song['pack_id'])
                    self.tracker.current_pack = dict(pack_id=pack['pack_id'], root_id=pack['root_id'], name=pack['name'],
                                                     status='confirmed', source='explicit_user_review')
                self.tracker.current_source = song['source_type']
                selection.update(fields=fields, pack=copy.deepcopy(self.tracker.current_pack), source_type=song['source_type'],
                    manual_confirmation=dict(observed_values=original_values, pack_id=song['pack_id'], reference=row))
                selection['matching'].update(status='confirmed_manual', confirmed_song_id=song_id, reference=row,
                    prior_automatic_reasons=selection['matching'].get('reasons',[]),reasons=[])
                self.tracker.log('manual_confirmation', song_id=song_id, observation_id=selection['observation_id'])
                if self.latest_image:
                    path = self.directory/'confirmations'/(selection['observation_id']+'.png')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.latest_image.save(path)
            finally:
                with_catalog.close()
            return self.status()

    def result_image(self):
        with self.lock:
            result = self.tracker.last_result if self.tracker else None
            if not result or not result.get('screenshot'):
                raise LibraryError('尚無結算截圖。', 404)
            path = self.directory/'sessions'/result['session_id']/'result.png'
            from uuid import UUID
            if str(UUID(result['session_id']))!=result['session_id']:
                raise LibraryError('結算 Session ID 不正確。')
            return path.read_bytes()
