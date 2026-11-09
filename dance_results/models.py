from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import __version__

GAME_DIFFICULTIES = ("EASY", "NORMAL", "HARD")


@dataclass
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    location: str | None = None


@dataclass
class ParsedFile:
    """A song-title source, never a game chart or a source of difficulty data."""

    path: str
    format: str
    sha256: str | None = None
    encoding: str | None = None
    title: str | None = None
    subtitle: str | None = None
    title_fields: dict[str, list[str]] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    song_folder: str | None = None
    pack_name: str | None = None
    storage_group: str | None = None
    source_type: str = "custom"
    source_id: int | None = None
    is_present: bool | None = None
    encoding_candidates: list[str] = field(default_factory=list)
    raw_data_preserved: bool = False
    # Transport only to SQLite BLOB storage; excluded from JSON/HTML payloads.
    raw_data: bytes | None = field(default=None, repr=False)
    artist: str | None = None
    artist_fields: dict[str, list[str]] = field(default_factory=dict)
    saved_artist: str | None = None
    metadata_encodings: dict[str, list[str]] = field(default_factory=dict)
    metadata_raw_fields: dict[str, list[str]] = field(default_factory=dict)
    metadata_candidates: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    name_review_required: bool = False
    # DWI's basic BPM metadata only, never tempo changes or game observations.
    source_bpm: str | None = None
    source_bpm_fields: list[str] = field(default_factory=list)

    @property
    def title_reliable(self) -> bool:
        return bool(self.title) and not self.name_review_required and not any(
            d.severity == "error" or d.code in {
                "UNTERMINATED_TITLE_TAG", "CONFLICTING_TITLE_FIELD", "UNRECOGNIZED_OSU_HEADER"
            } for d in self.diagnostics)

    @property
    def artist_reliable(self) -> bool:
        return bool(self.artist) and not any(d.code in {
            "CONFLICTING_ARTIST_FIELD", "UNTERMINATED_ARTIST_TAG", "SUSPECTED_ARTIST_ENCODING"
        } or (d.code == "METADATA_ENCODING_REQUIRED" and (d.location or "").upper().startswith("ARTIST")) for d in self.diagnostics)

    @property
    def status(self) -> str:
        if not self.title or any(d.severity == "error" for d in self.diagnostics):
            return "failed"
        if self.name_review_required or any(d.severity == "warning" for d in self.diagnostics):
            return "partial"
        return "ok"

    @property
    def display_title(self) -> str | None:
        return " ".join(x for x in (self.title, self.subtitle) if x) if self.title else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw_data")
        return {**data, "status": self.status}


@dataclass
class SongEntry:
    song_folder: str
    pack_name: str | None
    storage_group: str | None
    title: str | None
    title_candidates: list[str]
    source_paths: list[str]
    status: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    # None means not observed. Listing the three supported labels is not evidence
    # that all three difficulties exist for this custom song.
    available_difficulties: list[str] | None = None
    game_levels: dict[str, int] = field(default_factory=dict)
    game_total_notes: dict[str, int] = field(default_factory=dict)
    song_id: int | None = None
    saved_title: str | None = None
    is_present: bool | None = None
    source_format: str | None = None
    detected_formats: list[str] = field(default_factory=list)
    format_status: str = "unknown"
    pack_type: str | None = None
    title_reliable: bool = False


@dataclass
class ScanReport:
    root: str
    started_at: str
    finished_at: str = ""
    scope: list[str] = field(default_factory=list)
    files: list[ParsedFile] = field(default_factory=list)
    songs: list[SongEntry] = field(default_factory=list)
    auxiliary_files: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    cancelled: bool = False
    database: dict[str, Any] | None = None
    view_mode: str = "scan"
    deferred_folders: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 5,
            "application_version": __version__,
            "root": self.root,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scope": self.scope,
            "cancelled": self.cancelled,
            "view_mode": self.view_mode,
            "database": self.database,
            "summary": {
                "source_file_count": len(self.files),
                "song_folder_count": len(self.songs),
                "source_status_counts": {s: sum(f.status == s for f in self.files)
                                         for s in ("ok", "partial", "failed")},
                "song_status_counts": {s: sum(song.status == s for song in self.songs)
                                       for s in ("ok", "partial", "failed")},
                "title_conflict_count": sum(any(d.code == "SONG_TITLE_CONFLICT" for d in s.diagnostics)
                                            for s in self.songs),
                "mixed_format_count": sum(s.format_status == "mixed" for s in self.songs),
                "auxiliary_file_count": len(self.auxiliary_files),
            },
            "files": [f.to_dict() for f in self.files],
            "songs": [asdict(s) for s in self.songs],
            "auxiliary_files": self.auxiliary_files,
            "diagnostics": [asdict(d) for d in self.diagnostics],
            "deferred_folders": self.deferred_folders,
            "rules": {
                "song_files_used_for": "song_metadata_only",
                "allowed_game_difficulties": list(GAME_DIFFICULTIES),
                "custom_difficulty_availability_from_files": False,
                "game_level_source": "game_screen",
                "game_total_notes_sources": ["game_screen", "completed_judgment_sum"],
                "file_chart_parsing": False,
                "file_chart_deduplication": False,
                "account_scores_shared": True,
                "game_capture_implemented": False,
                "score_recording_implemented": False,
            },
        }
