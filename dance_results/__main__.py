from __future__ import annotations

import argparse
import codecs
import sqlite3
import sys
import webbrowser
from pathlib import Path

from .database import LibraryDatabase
from .report import write_report
from .scanner import scan_library

DEFAULT_ROOT = r"D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def choose_directory(initial: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        try:
            return filedialog.askdirectory(title="選擇 SONG 歌曲資料夾（唯讀掃描）", initialdir=initial,
                                           mustexist=True, parent=window) or None
        finally:
            window.destroy()
    except ImportError as error:
        raise ValueError("此 Python 沒有 tkinter，請以 --root 指定 SONG 路徑。") from error


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="熱舞 Online：唯讀讀取 SM／DWI／osu 歌名並產生歌曲清單；難度與 LV 由遊戲畫面確認。")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="SONG 根目錄")
    parser.add_argument("--folder", action="append", type=Path, help="只掃描 SONG 內的指定資料夾；可重複使用")
    parser.add_argument("--choose-directory", action="store_true", help="開啟 SONG 資料夾選擇器")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports", help="報告上層目錄；不可位於 SONG 內")
    parser.add_argument("--legacy-encoding", help="僅在 UTF-8 解碼失敗時，使用你明確指定的舊檔編碼，例如 cp950／cp932")
    parser.add_argument("--open-report", action="store_true", help="完成後以預設瀏覽器開啟報告")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "library.sqlite3", help="SQLite 歌曲庫位置；不可位於 SONG 內")
    parser.add_argument("--library-only", action="store_true", help="只從已保存的 SQLite 歌曲庫產生報告，不重新掃描")
    args = parser.parse_args(argv)
    try:
        if args.legacy_encoding:
            codecs.lookup(args.legacy_encoding)
        selected = choose_directory(args.root) if args.choose_directory else args.root
        if not selected:
            print("已取消，沒有掃描或修改歌曲庫。")
            return 0
        root = Path(selected).expanduser().resolve(strict=not args.library_only)
        if args.output.expanduser().resolve().is_relative_to(root):
            raise ValueError("報告不可寫入 SONG 內。")
        if args.database.expanduser().resolve().is_relative_to(root):
            raise ValueError("資料庫不可寫入 SONG 內。")
        if args.library_only and args.folder:
            raise ValueError("--library-only 顯示此根目錄全部已存資料，不能搭配 --folder。")

        def progress(number: int, path: Path) -> None:
            if number <= 5 or number % 50 == 0:
                print(f"[{number}] {path.relative_to(root)}", flush=True)

        if args.library_only:
            print("從已保存的歌曲資料庫讀取；沒有重新掃描 SONG。", flush=True)
            with LibraryDatabase(args.database, [root], read_only=True) as library:
                report = library.load_report(root)
        else:
            print(f"熱舞 Online · 歌曲資料庫\n唯讀掃描：{root}\n來源檔只讀歌名。難度分類為 EASY／NORMAL／HARD；LV 依遊戲畫面。\n", flush=True)
            report = scan_library(root, args.folder, args.legacy_encoding, progress)
            with LibraryDatabase(args.database, [root]) as library:
                for migration in library.migration_results:
                    if migration["backup_path"]:
                        print(f"已完成資料庫 migration {migration['from_version']}→{migration['to_version']}；備份：{migration['backup_path']}", flush=True)
                library.sync(report)
            print(f"\n已保存歌曲資料庫：{report.database['path']}\n匯入結果：{report.database['changes']}", flush=True)
        destination = write_report(report, args.output)
        counts = report.to_dict()["summary"]
        print(f"\n來源檔案：{counts['source_file_count']}；歌曲資料夾：{counts['song_folder_count']}")
        print(f"歌名讀取狀態：{counts['source_status_counts']}；歌名衝突資料夾：{counts['title_conflict_count']}")
        print(f"報告：{destination}\n資料：{destination.with_name('report.json')}", flush=True)
        if args.open_report:
            try:
                if not webbrowser.open(destination.as_uri()):
                    print("瀏覽器未自動開啟，請手動開啟上述 report.html。")
            except OSError as error:
                print(f"瀏覽器未自動開啟：{error}；請手動開啟報告。")
        # Partial/failed source files remain visible in a successful diagnostic scan.
        return 130 if report.cancelled else (2 if any(d.severity == "error" for d in report.diagnostics) else 0)
    except (OSError, ValueError, LookupError, sqlite3.Error) as error:
        print(f"無法完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
