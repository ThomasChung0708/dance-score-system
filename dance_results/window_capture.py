"""Window metadata and WGC pixels only: no process memory, input or injection."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import re
import sys
import threading
import time


class CaptureError(RuntimeError):
    pass


def is_game_title(title):
    return bool(re.match(r'^dance(?:\s+v\d|\s+online\b|$)',title,re.I))


class Windows:
    def __init__(self):
        if sys.platform != 'win32':
            raise CaptureError('即時視窗擷取僅支援 Windows。')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        for name in ('IsWindow', 'IsWindowVisible', 'IsIconic'):
            getattr(self.user, name).argtypes = [wintypes.HWND]
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetForegroundWindow.restype = wintypes.HWND

    def info(self, hwnd):
        if not self.user.IsWindow(hwnd):
            return None
        title = ctypes.create_unicode_buffer(self.user.GetWindowTextLengthW(hwnd)+1)
        self.user.GetWindowTextW(hwnd, title, len(title))
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        self.user.GetWindowRect(hwnd, ctypes.byref(rect))
        return dict(hwnd=int(hwnd), pid=pid.value, title=title.value,
                    minimized=bool(self.user.IsIconic(hwnd)),
                    foreground=self.user.GetForegroundWindow() == hwnd,
                    width=rect.right-rect.left, height=rect.bottom-rect.top,
                    is_game=is_game_title(title.value))

    def list(self, all_windows=False):
        entries = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                entry = self.info(hwnd)
                if entry and entry['title'] and (all_windows or entry['is_game']):
                    entries.append(entry)
            return True

        self.user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user.EnumWindows(callback, 0)
        return entries


class WGCCapture:
    """Latest-frame buffer of capacity one, independent of OCR and GUI threads."""
    def __init__(self, hwnd, capture_hz=2):
        try:
            from windows_capture import WindowsCapture
        except ImportError as error:
            raise CaptureError('請用 .venv-ocr 安裝 requirements-monitor.txt，並從 MonitorGUI.cmd 啟動。') from error
        self.lock = threading.Lock()
        self.latest = None
        self.closed = False
        self.error = None
        self.frames = 0
        self.last_copy = 0.0
        self.copy_ms = 0.0
        self.interval = 1/capture_hz
        # MinimumUpdateInterval is unavailable on Windows 10. Throttle copies in the
        # callback instead; requesting the newer WGC option prevents capture entirely.
        self.capture = WindowsCapture(window_hwnd=hwnd, cursor_capture=False,
                                      draw_border=None, minimum_update_interval=None)

        @self.capture.event
        def on_frame_arrived(frame, capture_control):
            now = time.monotonic()
            if now-self.last_copy < self.interval:
                return
            try:
                from PIL import Image
                started = time.perf_counter()
                # Copy within callback lifetime; native mapped memory must not escape the callback.
                pixels = frame.frame_buffer[:, :, [2, 1, 0]].copy()
                image = Image.fromarray(pixels)
                with self.lock:
                    self.latest = (now, image)
                    self.frames += 1
                    self.copy_ms = (time.perf_counter()-started)*1000
                self.last_copy = now
            except Exception as error:
                self.error = str(error)

        @self.capture.event
        def on_closed():
            self.closed = True

        self.control = self.capture.start_free_threaded()

    def take(self):
        with self.lock:
            frame = self.latest
            self.latest = None
            return frame

    def stop(self):
        self.control.stop()
        self.closed = True
