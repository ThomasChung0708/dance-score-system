from __future__ import annotations

import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .models import Diagnostic, ScanReport
from .parsers import parse_file
from .parsers.base import SUPPORTED_EXTENSIONS
from .songs import group_songs


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_link(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def scan_library(root: Path, folders: list[Path] | None = None,
                 legacy_encoding: str | None = None,
                 progress: Callable[[int, Path], None] | None = None,
                 defer_not_ready: bool = False) -> ScanReport:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("SONG 路徑不是資料夾。")
    scopes = []
    for folder in folders or [root]:
        candidate = folder if folder.is_absolute() else root / folder
        if is_link(candidate):
            raise ValueError(f"不掃描連結／接合點：{candidate}")
        candidate = candidate.resolve(strict=True)
        if not candidate.is_relative_to(root) or not candidate.is_dir():
            raise ValueError(f"掃描範圍必須是 SONG 內的資料夾：{candidate}")
        if not any(candidate.is_relative_to(previous) for previous in scopes):
            scopes = [previous for previous in scopes if not previous.is_relative_to(candidate)]
            scopes.append(candidate)
    report = ScanReport(str(root), utc_now(), scope=[str(p) for p in scopes])
    paths: set[Path] = set()
    deferred_folders: set[Path] = set()

    def walk_error(error: OSError) -> None:
        report.diagnostics.append(Diagnostic("WALK_ERROR", str(error), "error"))

    try:
        for scope in sorted(scopes):
            for directory, directories, filenames in os.walk(scope, followlinks=False, onerror=walk_error):
                base = Path(directory)
                depth = len(base.relative_to(root).parts)
                kept = []
                for name in sorted(directories, key=str.casefold):
                    child = base / name
                    if is_link(child):
                        report.diagnostics.append(Diagnostic("SKIPPED_LINK", f"略過目錄連結／接合點：{child}", "info"))
                    elif depth >= 2:
                        report.diagnostics.append(Diagnostic("NESTED_DIRECTORY_SKIPPED", f"依 SONG／Pack／Song 結構，不遞迴歌曲內的子目錄：{child}", "info"))
                    elif name not in {".git", "__pycache__"}:
                        kept.append(name)
                directories[:] = kept
                for name in sorted(filenames, key=str.casefold):
                    path = base / name
                    if is_link(path):
                        report.diagnostics.append(Diagnostic("SKIPPED_LINK", f"略過檔案連結：{path}", "info"))
                        continue
                    suffix = path.suffix.lower()
                    if suffix in SUPPORTED_EXTENSIONS:
                        paths.add(path)
                    elif suffix in {".dps", ".osb", ".old"} or name.lower() == "sdoinfo.dat":
                        kind = {".dps": "unknown_auxiliary", ".osb": "storyboard", ".old": "backup_excluded"}.get(suffix, "game_sidecar")
                        report.auxiliary_files.append({"path": str(path), "kind": kind, "counted_as_chart": False})
        observations = {}
        if defer_not_ready and paths:
            for path in paths:
                try:
                    observed=path.stat();observations[path]=(observed.st_size,observed.st_mtime_ns)
                except OSError:observations[path]=None
            time.sleep(.25)  # One bounded stability interval for the whole scan, not per file.
        for number, path in enumerate(sorted(paths, key=lambda p: str(p).casefold()), 1):
            if progress:
                progress(number, path)
            try:
                parsed = parse_file(path, legacy_encoding)
            except Exception as error:
                # A bad title source cannot stop the remaining library.
                from .models import ParsedFile
                parsed = ParsedFile(str(path), path.suffix.lstrip("."))
                parsed.diagnostics.append(Diagnostic("UNEXPECTED_PARSER_ERROR", f"{type(error).__name__}: {error}", "error"))
            if defer_not_ready:
                try:
                    current=path.stat();stamp=(current.st_size,current.st_mtime_ns)
                    ready=stamp==observations.get(path) and current.st_size>0 and parsed.title_reliable
                except OSError:ready=False
                if not ready:
                    deferred_folders.add(path.parent)
                    report.diagnostics.append(Diagnostic('SOURCE_NOT_READY',
                        f'名稱未能可靠解析或來源仍在變動，稍後再掃描；保留既有來源與歷史：{path}'))
                    continue
            relative = path.parent.relative_to(root)
            parsed.song_folder = str(path.parent)
            if len(relative.parts) >= 2:
                parsed.storage_group = relative.parts[0]
                parsed.pack_name = relative.parts[0]
                if relative.parts[0].casefold() == "connect":
                    parsed.diagnostics.append(Diagnostic("MULTIPLAYER_DOWNLOAD_PACK", "connect 是多人下載管理 Pack；不代表已驗證原始 Pack 或成績配對。", "info"))
            else:
                parsed.diagnostics.append(Diagnostic("NONSTANDARD_LAYOUT", "不符合 SONG／Pack／Song 結構，Pack 保持未知。"))
            report.files.append(parsed)
    except KeyboardInterrupt:
        report.cancelled = True
        report.diagnostics.append(Diagnostic("SCAN_CANCELLED", "使用者中止掃描；本報告不代表完整歌曲庫。"))
    if not report.files:
        report.diagnostics.append(Diagnostic("NO_SUPPORTED_FILES", "掃描範圍沒有支援的譜面檔案。"))
    if deferred_folders:
        # Do not turn a previously ambiguous folder into a reliable single-title
        # song merely because another source in that folder is still downloading.
        retained=[]
        for source in report.files:
            if Path(source.path).parent in deferred_folders:
                report.diagnostics.append(Diagnostic('SOURCE_NOT_READY',
                    f'同一 SongFolder 尚有來源待確認，整個資料夾延後更新：{source.path}'))
            else:retained.append(source)
        report.files=retained
    report.deferred_folders=sorted(str(p.relative_to(root)) for p in deferred_folders)
    report.songs = group_songs(report.files)
    report.finished_at = utc_now()
    return report
