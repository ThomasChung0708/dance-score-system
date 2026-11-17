"""Bounded live capture/OCR worker behind the existing local GUI."""
from __future__ import annotations

import copy
from contextlib import closing
import math
import json
from pathlib import Path
import statistics
import sqlite3
import threading
import time

from .library_service import LibraryError
from .monitor_tracking import Tracker, ManualReferences, utc
from .monitor_ocr import Analyzer, SelectionFrameBuffer, apply_selection, change_signature, change_amount, black_or_loading_frame
from .ocr_matching import ReadOnlyCatalog, same_song_title_key
from .window_capture import Windows, WGCCapture, CaptureError
from .ocr_failures import FailureCapture
from .songs import catalog_display_title

from .app_paths import app_home

PROJECT = app_home()


class Monitor:
    def __init__(self, database, directory=None, windows_factory=Windows, capture_factory=WGCCapture, engine_factory=None):
        self.database = Path(database)
        default=(PROJECT/'data'/'monitor' if self.database.resolve()==(PROJECT/'data'/'library.sqlite3').resolve()
                 else self.database.parent/(self.database.stem+'-monitor'))
        self.directory = Path(directory or default)
        failure_directory = (PROJECT / 'data' / 'ocr_failures'
                             if self.database.resolve() == (PROJECT / 'data' / 'library.sqlite3').resolve()
                             else self.directory / 'ocr_failures')
        self.failure_capture = FailureCapture(failure_directory)
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
        self.player_settings = self.reviews.player_settings
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
                               room_capture_hz=number('room_capture_hz', 4, .2, 10),
                               room_min_samples=int(number('room_min_samples', 4, 2, 40)),
                               ocr_interval=number('ocr_interval', 2, .5, 30),
                               confirmations=int(number('confirmations', 2, 2, 5)),
                               selection_ttl=number('selection_ttl', 60, 5, 600),
                               player_name=self.player_settings.set(body.get('player_name', self.player_settings.get())),
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
            self.tracker = Tracker(self.directory, self.config['confirmations'], self.config['selection_ttl'],
                                   self.config['room_min_samples'])
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
            tracking = result['tracking'] or {}
            # Enrich the public copy only. Display fallbacks never feed back into
            # selection fields, session evidence or matching/auto-save decisions.
            missing_titles = []
            for key in ('selection', 'session', 'last_session'):
                item = tracking.get(key)
                if not item:
                    continue
                selected = item.get('selection') or item
                title = ((selected.get('fields') or {}).get('title') or {}).get('value')
                song_id = item.get('song_id') or (item.get('matching') or {}).get('confirmed_song_id')
                if song_id and not title:
                    missing_titles.append((item, song_id))
            if missing_titles:
                with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    db.execute('PRAGMA query_only=ON')
                    for item, song_id in missing_titles:
                        item['display_song_title'] = catalog_display_title(db, song_id=song_id)
            return result

    def _connection(self, status, error=None):
        with self.lock:
            if self.connection != status or self.error != error:
                self.tracker.log('connection', status=status, error=error)
            self.connection = status
            self.error = error

    def _capture_ocr_failure(self, image, observation=None, error=None):
        try:
            self.failure_capture.capture(image, observation, error)
        except Exception:
            pass

    def _consume_observation(self, observation, now, image, sample_id, result_consistency=None):
        from .result_ocr import incomplete_result
        from .ocr_screens import compact, in_region

        ocr = observation.get('result_ocr') or {}
        if observation.get('state') in ('RESULT', 'GAME_OVER') and incomplete_result(ocr):
            context = observation.get('context') or {}
            player = ocr.get('requested_player_name') or context.get('player_name')
            own_name = ocr.get('fields', {}).get('player_name', {})
            own_row = (ocr.get('row') is not None or context.get('local_row_evidence') or
                       (ocr.get('local_player_status') == 'CONFIRMED' and own_name.get('value')))
            # Combo can still be drawing, so the name may precede a parsed row.
            own_name_visible = player and any(
                compact(t['text']) == compact(player) and t['confidence'] >= .9
                for t in in_region(ocr.get('tokens', []), (110, 225, 300, 605)))
            if own_row or own_name_visible:
                return
        # Missing local players also occur on settled spectator/not-listed
        # results. Those retain the original tracker transitions and capture.
        self.tracker.consume(observation, now=now, image=image, sample_id=sample_id)
        self._ingest_pending_result(result_consistency)

    def _ingest_pending_result(self, result_consistency=None):
        pending = self.tracker.last_session
        observed = self.tracker.last_result_observation
        if (not getattr(self, 'reviews', None) or not isinstance(pending, dict)
                or pending.get('status') != 'pending_result' or not isinstance(observed, dict)
                or not observed.get('result_ocr')):
            return
        folder = self.directory/'sessions'/pending['session_id']
        if (folder/'result-review.json').exists():
            return
        if result_consistency and not pending['result'].get('recovery_reason'):
            result_consistency.save(folder, observed['result_ocr'])
        self.reviews.ingest(pending, observed['result_ocr'])

    def _recover_fast_selection(self, analyzer, frames, observation, sampled, catalog):
        """Re-read captured selection pixels once, before the PLAYING handoff.

        A preview can corroborate a captured title, never supply missing song-
        select fields. An observed newer different song stops the search.
        """
        # The catalog stays read-only in this monitor worker; the tracker uses
        # it solely to reject a typo-tolerant comparison naming another song.
        self.tracker.title_catalog = catalog
        if observation['state'] != 'PLAYING' or self.tracker.session or self.tracker.proposed == 'PLAYING':
            return
        preview = observation.get('playing_preview') or {}
        if not preview.get('title'):
            return
        current = self.tracker.selection or self.tracker.fast_selection(sampled)
        if (current and sampled-current['last_seen'] <= self.tracker.selection_ttl
                and self.tracker.playing_title_agrees(current, preview['title'], catalog)):
            return
        for captured_at, image in frames.before(sampled):
            recovered = analyzer.analyze(image, 'SONG_SELECT', full=False)
            if recovered['state'] != 'SONG_SELECT':
                continue
            fields = recovered['selection']['fields']
            title = fields.get('title') or {}
            if not title.get('value') or title.get('reason'):
                continue
            compared = dict(fields=fields, pack=self.tracker.current_pack,
                            source_type=self.tracker.current_source)
            if not self.tracker.playing_title_agrees(compared, preview['title'], catalog):
                self.tracker.invalidate_selection('newer_captured_selection_differs_from_playing')
                break
            from .pack_selection import infer_selected_pack
            infer_selected_pack(recovered['selection'], catalog)
            current_pack = self.tracker.current_pack or {}
            pack_time = self.tracker.pack_observed_at
            if (pack_time is not None and captured_at < pack_time
                    and current_pack.get('status') == 'confirmed'
                    and recovered['selection'].get('selected_pack_id') != current_pack.get('pack_id')):
                self.tracker.log('selection_capture_rejected', reason='newer_pack_evidence',
                                 frame_hash=recovered['frame_hash'])
                break
            recovered = apply_selection(recovered, image, self.tracker, catalog,
                                        self.references, self.config.get('source_mode', 'auto'))
            self.tracker.update_selection(recovered, captured_at)
            self.tracker.log('selection_recovered_from_capture', captured_at=captured_at,
                             playing_captured_at=sampled, frame_hash=recovered['frame_hash'])
            break
        frames.clear()

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
            selection_frames=SelectionFrameBuffer()
            try:
                import psutil
                process = psutil.Process()
                process.cpu_percent(None)
            except ImportError:
                process = None
            durations = []
            from .room_sampler import RoomSampler
            from .lobby_download_ocr import badge_ocr_calls
            room_sampler = RoomSampler()
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
                            self._ingest_pending_result()
                            self.window = None
                        try:capture.stop()
                        except Exception:pass
                        capture = None; current = None
                        signature = None
                        selection_frames.clear()
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
                    selection_frames.clear()
                with self.lock:
                    self.window = current
                if current['minimized']:
                    if self.connection != 'minimized':
                        with self.lock:
                            self.tracker.invalidate_selection('window_minimized')
                        selection_frames.clear()
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
                    selection_frames.clear()
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
                            selection_frames.clear()
                    self.stop_event.wait(.05)
                    continue
                sampled, image = frame
                room_sampling = self.tracker.proposed == 'WAITING_ROOM' or self.tracker.state == 'WAITING_ROOM'
                rate_key = 'room_capture_hz' if room_sampling else 'capture_hz'
                capture.interval = 1/self.config.get(rate_key, 4 if room_sampling else 2)
                recent = capture.take_recent() if hasattr(capture, 'take_recent') else [frame]
                if not isinstance(recent, (list, tuple)):
                    recent = [frame]
                with self.lock:
                    if room_sampling:
                        costs = [room_sampler.sample(pixels, self.tracker, analyzer.engine, stamp)
                                 for stamp, pixels in recent]
                        costs = [cost for cost in costs if cost is not None]
                        if costs:
                            self.metrics['room_sampler_ms'] = round(statistics.mean(costs), 3)
                        self.metrics['badge_ocr_calls'] = badge_ocr_calls(analyzer.engine)
                    if self.downloads:
                        self.downloads.room_tick(self.tracker)
                selection_frames.observe(recent, time.monotonic())
                last_native = now
                if black_or_loading_frame(image):
                    self._connection('capture_error','擷取到黑色或載入過場畫面；暫停辨識。')
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
                try:
                    observation = analyzer.analyze(image, current_state, full)
                except Exception as error:
                    self._capture_ocr_failure(image, error=error)
                    raise
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
                    self._recover_fast_selection(analyzer, selection_frames, observation, sampled, catalog)
                    observation = apply_selection(observation, image, self.tracker, catalog, self.references,
                                                  self.config['source_mode'])
                    result_consistency.observe(observation,(self.tracker.session or {}).get('session_id'),sampled,image)
                    self._capture_ocr_failure(image, observation)
                    self.latest_image = image
                    self._consume_observation(observation, last_ocr, image, sampled, result_consistency)
                    room_sampler.configure(self.tracker, observation, sample_id=sampled)
                    if self.downloads:self.downloads.observe(observation,self.tracker)
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
                                     ocr_ms=round(elapsed, 2), roi_mode=observation['roi_mode'],
                                     room_sampler_ms=self.metrics.get('room_sampler_ms'),
                                     badge_ocr_calls=badge_ocr_calls(analyzer.engine))
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
                self._ingest_pending_result()
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
