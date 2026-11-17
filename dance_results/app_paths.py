"""Where the tool keeps its own files, both from source and as a packaged exe.

From source, data lives in <repo>/data as before. A PyInstaller exe unpacks its
code into a temporary folder, so anything derived from __file__ would land
there and vanish on exit; the packaged tool keeps data next to the exe instead.
"""
from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def app_home() -> Path:
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return app_home() / 'data'


def default_database() -> Path:
    return data_dir() / 'library.sqlite3'


def ocr_available() -> bool:
    """The OCR monitor needs optional packages the packaged exe leaves out."""
    import importlib.util
    try:
        return all(importlib.util.find_spec(name) is not None for name in ('rapidocr', 'numpy', 'PIL'))
    except (ImportError, ValueError):
        return False
