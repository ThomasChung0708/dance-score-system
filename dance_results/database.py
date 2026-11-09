from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import Diagnostic, ParsedFile, ScanReport, SongEntry
from .migrations import SCHEMA_VERSION, migrate_v1
from .migrations_v3 import migrate_v2
from .migrations_v4 import migrate_v3
from .migrations_v5 import migrate_v4
from .migrations_v6 import migrate_v5
from .migrations_v7 import migrate_v6
from .migrations_v8 import migrate_v7
from .migrations_v9 import migrate_v8
from .migrations_v10 import migrate_v9

APPLICATION_ID = 0x44414E43

# Kept verbatim as the schema-1 migration starting point and test fixture.
SCHEMA = """
CREATE TABLE library_roots (
    root_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE packs (
    pack_id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES library_roots(root_id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(root_id, path_key)
);
CREATE TABLE scan_runs (
    scan_id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES library_roots(root_id) ON DELETE RESTRICT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK(complete IN (0,1)),
    report_json TEXT NOT NULL
);
CREATE TABLE songs (
    song_id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES library_roots(root_id) ON DELETE RESTRICT,
    pack_id INTEGER REFERENCES packs(pack_id) ON DELETE RESTRICT,
    relative_folder TEXT NOT NULL,
    folder_key TEXT NOT NULL,
    storage_group TEXT,
    source_type TEXT NOT NULL DEFAULT 'custom' CHECK(source_type IN ('official','custom','unknown')),
    latest_title TEXT,
    saved_title TEXT,
    latest_status TEXT NOT NULL CHECK(latest_status IN ('ok','partial','failed')),
    payload_json TEXT NOT NULL,
    is_present INTEGER NOT NULL DEFAULT 1 CHECK(is_present IN (0,1)),
    last_seen_scan_id INTEGER REFERENCES scan_runs(scan_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(root_id, folder_key)
);
CREATE TABLE song_sources (
    source_id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(song_id) ON DELETE RESTRICT,
    relative_path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    format TEXT NOT NULL CHECK(format IN ('sm','dwi','osu')),
    latest_sha256 TEXT,
    saved_title TEXT,
    saved_subtitle TEXT,
    latest_status TEXT NOT NULL CHECK(latest_status IN ('ok','partial','failed')),
    payload_json TEXT NOT NULL,
    is_present INTEGER NOT NULL DEFAULT 1 CHECK(is_present IN (0,1)),
    last_seen_scan_id INTEGER REFERENCES scan_runs(scan_id) ON DELETE RESTRICT,
    UNIQUE(song_id, path_key)
);
CREATE TABLE charts (
    chart_id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(song_id) ON DELETE RESTRICT,
    difficulty TEXT NOT NULL CHECK(difficulty IN ('EASY','NORMAL','HARD')),
    game_level INTEGER CHECK(game_level IS NULL OR game_level >= 0),
    total_notes INTEGER CHECK(total_notes IS NULL OR total_notes > 0),
    notes_source TEXT CHECK(notes_source IS NULL OR notes_source IN ('game_screen','completed_judgment_sum')),
    UNIQUE(song_id, difficulty),
    CHECK((total_notes IS NULL AND notes_source IS NULL) OR
          (total_notes IS NOT NULL AND notes_source IS NOT NULL))
);
CREATE INDEX songs_pack_index ON songs(pack_id);
CREATE INDEX sources_song_index ON song_sources(song_id);
CREATE INDEX scan_runs_root_index ON scan_runs(root_id,scan_id);
"""


def path_key(path: Path) -> str:
    return os.path.normcase(str(path)).replace("\\", "/")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_payload(source: ParsedFile) -> str:
    data = asdict(source)
    for key in ("source_id", "is_present", "raw_data", "saved_artist"):
        data.pop(key)
    return json_text(data)


def song_payload(song: SongEntry) -> str:
    data = asdict(song)
    for key in ("song_id", "saved_title", "is_present"):
        data.pop(key)
    return json_text(data)


def folder_is_covered(folder: Path, scopes: list[Path], root: Path) -> bool:
    """Match the scanner's nonrecursive Song directory boundary, not string prefixes."""
    for scope in scopes:
        if folder == scope:
            return True
        if folder.is_relative_to(scope) and len(folder.relative_to(root).parts) <= 2:
            return True
    return False


class LibraryDatabase:
    def __init__(self, path: Path, protected_roots: list[Path] | None = None, *, read_only: bool = False):
        self.path = path.expanduser().resolve()
        for root in protected_roots or []:
            if self.path.is_relative_to(root.expanduser().resolve()):
                raise ValueError("資料庫不可建立或開啟在 SONG 內，請使用歌曲庫以外的位置。")
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise ValueError("尚未建立歌曲資料庫，請先掃描匯入一次。")
            self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.migration_result = None
        self.migration_results = []
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            app_id = self.connection.execute("PRAGMA application_id").fetchone()[0]
            tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            new_database = version == 0 and app_id == 0 and not tables and not read_only
            if new_database:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n" + SCHEMA +
                    f"\nPRAGMA application_id={APPLICATION_ID};\nPRAGMA user_version=1;\nCOMMIT;"
                )
                version, app_id = 1, APPLICATION_ID
            if app_id != APPLICATION_ID or version not in (1,2,3,4,5,6,7,8,9,SCHEMA_VERSION):
                raise ValueError("資料庫不是本程式的支援版本，未覆寫或自動重建。")
            if version != SCHEMA_VERSION and read_only:
                raise ValueError(f"資料庫需要 schema {version}→{SCHEMA_VERSION} migration；請先以一般掃描入口執行，程式會先備份。唯讀入口未修改資料。")
            if version == 1:
                self.migration_results.append(migrate_v1(self.connection,self.path,new_database=new_database))
                version = 2
            if version == 2:
                self.migration_results.append(migrate_v2(self.connection,self.path,new_database=new_database))
                version = 3
            if version == 3:
                self.migration_results.append(migrate_v3(self.connection,self.path,new_database=new_database))
                version = 4
            if version == 4:
                self.migration_results.append(migrate_v4(self.connection,self.path,new_database=new_database))
                version = 5
            if version == 5:
                self.migration_results.append(migrate_v5(self.connection,self.path,new_database=new_database))
            if version == 5:version = 6
            if version == 6:
                self.migration_results.append(migrate_v6(self.connection,self.path,new_database=new_database))
            if version == 6:version = 7
            if version == 7:
                self.migration_results.append(migrate_v7(self.connection,self.path,new_database=new_database))
            if version == 7:version = 8
            if version == 8:
                self.migration_results.append(migrate_v8(self.connection,self.path,new_database=new_database));version=9
            if version == 9:self.migration_results.append(migrate_v9(self.connection,self.path,new_database=new_database))
            self.migration_result = self.migration_results[-1] if self.migration_results else None
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> LibraryDatabase:
        return self

    def __exit__(self, *args) -> None:
        self.connection.close()

    def totals(self, root_id: int) -> dict[str, int]:
        db = self.connection
        values = dict(db.execute(
            "SELECT COUNT(*) AS songs, COALESCE(SUM(is_present),0) AS present_songs, "
            "COALESCE(SUM(1-is_present),0) AS missing_songs FROM songs WHERE root_id=?", (root_id,)
        ).fetchone())
        values["packs"] = db.execute("SELECT COUNT(*) FROM packs WHERE root_id=?", (root_id,)).fetchone()[0]
        values["sources"] = db.execute(
            "SELECT COUNT(*) FROM song_sources f JOIN songs s ON s.song_id=f.song_id WHERE s.root_id=?", (root_id,)
        ).fetchone()[0]
        values["game_difficulties"] = db.execute(
            "SELECT COUNT(*) FROM game_difficulties d JOIN songs s ON s.song_id=d.song_id WHERE s.root_id=?", (root_id,)
        ).fetchone()[0]
        values["scan_runs"] = db.execute("SELECT COUNT(*) FROM scan_runs WHERE root_id=?", (root_id,)).fetchone()[0]
        return values

    def sync(self, report: ScanReport) -> dict[str, Any]:
        if self.read_only:
            raise ValueError("唯讀資料庫不能匯入。")
        if report.view_mode != "scan":
            raise ValueError("已存歌曲庫檢視不能當作新的掃描結果匯入。")
        root = Path(report.root).resolve()
        if self.path.is_relative_to(root):
            raise ValueError("資料庫不可位於 SONG 內。")
        scopes = [Path(scope).resolve() for scope in report.scope]
        if not scopes or any(not scope.is_relative_to(root) for scope in scopes):
            raise ValueError("掃描範圍無效，未寫入資料庫。")
        # A failure to observe a directory or link must not be interpreted as removal.
        complete = not report.cancelled and not any(
            d.severity == "error" or d.code in ("SKIPPED_LINK", "SOURCE_NOT_READY") for d in report.diagnostics
        )
        stats = {key: 0 for key in ("new_songs", "updated_songs", "unchanged_songs", "restored_songs",
                                   "new_sources", "updated_sources", "missing_songs", "missing_sources")}
        previous_database = report.database
        db = self.connection
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO library_roots(path,path_key,created_at) VALUES(?,?,?) "
                       "ON CONFLICT(path_key) DO NOTHING", (str(root), path_key(root), report.started_at))
            root_id = db.execute("SELECT root_id FROM library_roots WHERE path_key=?", (path_key(root),)).fetchone()[0]
            scan_id = db.execute("INSERT INTO scan_runs(root_id,started_at,finished_at,scopes_json,complete,report_json) "
                                 "VALUES(?,?,?,?,?,?)", (root_id, report.started_at, report.finished_at,
                                                        json_text(report.scope), int(complete), "{}")).lastrowid
            old_songs = {row["folder_key"]: row for row in db.execute("SELECT * FROM songs WHERE root_id=?", (root_id,))}
            old_pack_ids = {row[0] for row in db.execute('SELECT pack_id FROM packs WHERE root_id=?',(root_id,))}
            seen_songs, seen_sources = set(), set()
            by_folder = {}
            for song in report.songs:
                folder = Path(song.song_folder).resolve()
                if not folder.is_relative_to(root) or not folder_is_covered(folder, scopes, root):
                    raise ValueError("歌曲不在本次掃描範圍內，未寫入。")
                relative = folder.relative_to(root)
                key = path_key(relative)
                pack_id = None
                if song.pack_name is not None:
                    if not relative.parts or song.pack_name != relative.parts[0]:
                        raise ValueError("來源 Pack 關聯不一致。")
                    pack_relative = Path(relative.parts[0])
                    pack_type = "multiplayer_download" if relative.parts[0].casefold() == "connect" else "normal"
                    song.pack_type = pack_type
                    db.execute("INSERT INTO packs(root_id,relative_path,path_key,name,pack_type) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(root_id,path_key) DO UPDATE SET name=excluded.name,relative_path=excluded.relative_path,pack_type=excluded.pack_type",
                               (root_id, str(pack_relative), path_key(pack_relative), song.pack_name, pack_type))
                    pack_id = db.execute("SELECT pack_id FROM packs WHERE root_id=? AND path_key=?",
                                         (root_id, path_key(pack_relative))).fetchone()[0]
                payload = song_payload(song)
                old = old_songs.get(key)
                saved_title = song.title if song.title_reliable else (old["saved_title"] if old else None)
                if old is None:
                    song_id = db.execute(
                        "INSERT INTO songs(root_id,pack_id,relative_folder,folder_key,storage_group,latest_title,saved_title,"
                        "latest_status,payload_json,last_seen_scan_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (root_id, pack_id, str(relative), key, song.storage_group, song.title, saved_title,
                         song.status, payload, scan_id, report.started_at)
                    ).lastrowid
                    stats["new_songs"] += 1
                else:
                    song_id = old["song_id"]
                    stats["updated_songs" if old["payload_json"] != payload else "unchanged_songs"] += 1
                    stats["restored_songs"] += int(not old["is_present"])
                    db.execute("UPDATE songs SET pack_id=?,relative_folder=?,storage_group=?,latest_title=?,saved_title=?,"
                               "latest_status=?,payload_json=?,is_present=1,last_seen_scan_id=? WHERE song_id=?",
                               (pack_id, str(relative), song.storage_group, song.title, saved_title, song.status, payload, scan_id, song_id))
                seen_songs.add(song_id)
                db.execute("UPDATE songs SET folder_name=?,source_format=?,format_status=? WHERE song_id=?",
                           (relative.name, song.source_format, song.format_status, song_id))
                by_folder[path_key(folder)] = song_id
                song.song_id, song.saved_title, song.is_present = song_id, saved_title, True
            for source in report.files:
                path = Path(source.path).resolve()
                if not path.is_relative_to(root) or source.song_folder is None:
                    raise ValueError("來源檔案不在 SONG 內。")
                if path.parent != Path(source.song_folder).resolve():
                    raise ValueError("來源檔案與歌曲資料夾不一致。")
                song_id = by_folder[path_key(path.parent)]
                relative = path.relative_to(root)
                key = path_key(Path(path.name))
                old = db.execute("SELECT * FROM song_sources WHERE song_id=? AND path_key=?", (song_id, key)).fetchone()
                payload = source_payload(source)
                saved_title = source.title if source.title_reliable else (old["saved_title"] if old else None)
                saved_subtitle = source.subtitle if source.title_reliable else (old["saved_subtitle"] if old else None)
                saved_artist = source.artist if source.artist_reliable else (old["saved_artist"] if old else None)
                if old is None:
                    source_id = db.execute(
                        "INSERT INTO song_sources(song_id,relative_path,path_key,format,latest_sha256,saved_title,saved_subtitle,"
                        "latest_status,payload_json,last_seen_scan_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (song_id, str(relative), key, source.format, source.sha256, saved_title, saved_subtitle,
                         source.status, payload, scan_id)
                    ).lastrowid
                    stats["new_sources"] += 1
                else:
                    source_id = old["source_id"]
                    stats["updated_sources"] += int(old["payload_json"] != payload)
                    db.execute("UPDATE song_sources SET relative_path=?,format=?,latest_sha256=?,saved_title=?,saved_subtitle=?,"
                               "latest_status=?,payload_json=?,is_present=1,last_seen_scan_id=? WHERE source_id=?",
                               (str(relative), source.format, source.sha256, saved_title, saved_subtitle, source.status, payload, scan_id, source_id))
                source.source_id, source.is_present = source_id, True
                db.execute("UPDATE song_sources SET saved_artist=? WHERE source_id=?", (saved_artist,source_id))
                source.saved_artist = saved_artist
                if source.raw_data is not None:
                    import hashlib
                    if hashlib.sha256(source.raw_data).hexdigest() != source.sha256:
                        raise ValueError("來源原始資料與雜湊不一致，未保存。")
                    db.execute("INSERT INTO source_raw_snapshots(source_id,sha256,raw_data,reason,first_scan_id) "
                               "VALUES(?,?,?,?,?) ON CONFLICT(source_id,sha256) DO NOTHING",
                               (source_id, source.sha256, source.raw_data, "legacy_or_unknown_encoding", scan_id))
                seen_sources.add(source_id)
            if complete:
                for old in old_songs.values():
                    folder = root / old["relative_folder"]
                    if old["song_id"] not in seen_songs and folder_is_covered(folder, scopes, root):
                        stats["missing_songs"] += int(old["is_present"])
                        db.execute("UPDATE songs SET is_present=0 WHERE song_id=?", (old["song_id"],))
                for old in db.execute(
                    "SELECT f.source_id,f.is_present,s.relative_folder FROM song_sources f "
                    "JOIN songs s ON s.song_id=f.song_id WHERE s.root_id=?", (root_id,)
                ).fetchall():
                    if old["source_id"] not in seen_sources and folder_is_covered(root / old["relative_folder"], scopes, root):
                        stats["missing_sources"] += int(old["is_present"])
                        db.execute("UPDATE song_sources SET is_present=0 WHERE source_id=?", (old["source_id"],))
            stats['new_packs'] = sum(row[0] not in old_pack_ids for row in db.execute(
                'SELECT pack_id FROM packs WHERE root_id=?',(root_id,)))
            report.database = {"path": str(self.path), "root_id": root_id, "scan_id": scan_id,
                               "mode": "sync", "complete_scan": complete, "changes": stats, "totals": self.totals(root_id)}
            db.execute("UPDATE scan_runs SET report_json=? WHERE scan_id=?", (json_text(report.to_dict()), scan_id))
            db.commit()
            return report.database
        except BaseException:
            db.rollback()
            report.database = previous_database
            for song in report.songs:
                song.song_id = song.saved_title = song.is_present = None
            for source in report.files:
                source.source_id = source.is_present = None
                source.saved_artist = None
            raise

    def load_report(self, root: Path) -> ScanReport:
        root = root.expanduser().resolve()
        db = self.connection
        stored = db.execute("SELECT * FROM library_roots WHERE path_key=?", (path_key(root),)).fetchone()
        if stored is None:
            raise ValueError("資料庫尚無這個 SONG 路徑的資料，請先掃描。")
        root_id = stored["root_id"]
        latest = db.execute("SELECT * FROM scan_runs WHERE root_id=? ORDER BY scan_id DESC LIMIT 1", (root_id,)).fetchone()
        report = ScanReport(stored["path"], latest["started_at"], latest["finished_at"], [stored["path"]], view_mode="database")
        for row in db.execute("SELECT * FROM songs WHERE root_id=? ORDER BY folder_key", (root_id,)):
            data = json.loads(row["payload_json"])
            data["diagnostics"] = [Diagnostic(**item) for item in data["diagnostics"]]
            pack = db.execute("SELECT name,pack_type FROM packs WHERE pack_id=?",(row["pack_id"],)).fetchone()
            data["pack_name"] = pack["name"] if pack else None
            data["pack_type"] = pack["pack_type"] if pack else None
            data["source_format"], data["format_status"] = row["source_format"], row["format_status"]
            data["detected_formats"] = [item[0] for item in db.execute(
                "SELECT DISTINCT format FROM song_sources WHERE song_id=? AND is_present=1 ORDER BY format", (row["song_id"],))]
            difficulties = db.execute("SELECT * FROM game_difficulties WHERE song_id=? ORDER BY difficulty_id", (row["song_id"],)).fetchall()
            data["available_difficulties"] = [d["difficulty"] for d in difficulties] or None
            data["game_levels"] = {d["difficulty"]: d["game_level"] for d in difficulties if d["game_level"] is not None and d["level_source"] in ("game_screen","manual","official_csv")}
            data["game_total_notes"] = {d["difficulty"]: d["total_notes"] for d in difficulties if d["total_notes"] is not None and d["notes_verified"]}
            report.songs.append(SongEntry(**data, song_id=row["song_id"], saved_title=row["saved_title"], is_present=bool(row["is_present"])))
        by_id = {song.song_id: song for song in report.songs}
        for row in db.execute("SELECT f.* FROM song_sources f JOIN songs s ON s.song_id=f.song_id "
                              "WHERE s.root_id=? ORDER BY s.folder_key,f.path_key", (root_id,)):
            data = json.loads(row["payload_json"])
            data["diagnostics"] = [Diagnostic(**item) for item in data["diagnostics"]]
            data["saved_artist"] = row["saved_artist"]
            pack = db.execute("SELECT p.name FROM songs s LEFT JOIN packs p ON p.pack_id=s.pack_id WHERE s.song_id=?", (row["song_id"],)).fetchone()
            data["pack_name"] = pack["name"] if pack else None
            report.files.append(ParsedFile(**data, source_id=row["source_id"], is_present=bool(row["is_present"])))
            source_paths = by_id[row["song_id"]].source_paths
            if data["path"] not in source_paths:
                source_paths.append(data["path"])
        report.database = {"path": str(self.path), "root_id": root_id, "scan_id": latest["scan_id"],
                           "mode": "stored", "totals": self.totals(root_id)}
        report.diagnostics.append(Diagnostic("DATABASE_VIEW", "此清單來自資料庫，可能包含不同次掃描及未找到的來源；沒有重新檢查遊戲檔案。", "info"))
        return report
