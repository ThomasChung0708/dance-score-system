from collections import defaultdict
import json
import re

from .models import Diagnostic, ParsedFile, SongEntry
from .parsers.common import normalized_title


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _payload(row):
    return json.loads(row.get('payload_json') or '{}')


def _without_dwi_number(title):
    return re.sub(r'^\d+\.\s*(?=[^\d\s])', '', _text(title))


def display_title(song, sources=(), *, observed_titles=()):
    """Name for an already identified song, never evidence for matching.

    Keep consensus/saved catalog names intact. On a missing catalog name prefer
    SM's TITLE alone (SUBTITLE can be an album/game credit), then this play's
    observed names, other sources/candidates, and finally the folder basename.
    The original titles, reliability and diagnostics are never modified.
    """
    if song is None:
        return None
    song = dict(song)
    raw = _payload(song)
    for key in ('saved_title', 'latest_title', 'title'):
        if _text(song.get(key)):
            return _text(song[key])
    sources = [dict(source) for source in sources]
    for source in sources:
        if source.get('format') == 'sm':
            title = _text(source.get('saved_title') or source.get('title') or
                          _payload(source).get('title'))
            if title:
                return title
    for title in observed_titles:
        if _text(title):
            return _text(title)
    for source in sources:
        title = _text(source.get('saved_title') or source.get('title') or
                      _payload(source).get('title'))
        if source.get('format') == 'dwi':
            title = _without_dwi_number(title)
        if title:
            return title
    for title in song.get('title_candidates', raw.get('title_candidates', [])):
        if _text(title):
            return _without_dwi_number(title)
    folder = (song.get('folder_name') or song.get('relative_folder') or
              song.get('song_folder') or raw.get('song_folder') or '')
    folder = _text(folder).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    return _without_dwi_number(folder) or f"Song #{song.get('song_id', '?')}"


def catalog_display_title(db, song=None, *, song_id=None, observed_titles=()):
    """Read-time fallback for old catalogs, with no scan or DB write required."""
    if song is None and song_id is not None:
        song = db.execute('SELECT * FROM songs WHERE song_id=?', (song_id,)).fetchone()
    if song is None:
        return None
    song = dict(song)
    sources = ()
    if not any(_text(song.get(key)) for key in ('saved_title', 'latest_title', 'title')):
        sources = db.execute(
            'SELECT format,saved_title,payload_json FROM song_sources '
            'WHERE song_id=? ORDER BY source_id', (song.get('song_id'),)).fetchall()
    return display_title(song, sources, observed_titles=observed_titles)


def evidence_titles(evidence):
    """Only this play's result OCR and consumed selection, for display use."""
    review = evidence.get('review') or {}
    session = evidence.get('session') or review.get('locked_session') or {}
    result = (review.get('ocr') or {}).get('fields') or {}
    selection = (session.get('selection') or {}).get('fields') or {}
    return ((result.get('title') or {}).get('value'),
            (selection.get('title') or {}).get('value'))


def record_display_title(db, evidence, song_id, session_title=None):
    snapshot = evidence.get('song') or {}
    if _text(snapshot.get('saved_title')):
        return _text(snapshot['saved_title'])
    observed = (session_title, *evidence_titles(evidence))
    return (catalog_display_title(db, song_id=song_id, observed_titles=observed) or
            display_title(snapshot or None, observed_titles=observed) or
            _text(session_title) or next((t for t in evidence_titles(evidence) if _text(t)), None) or
            _text((evidence.get('values') or {}).get('title')) or '歌曲待確認')


def record_display_artist(evidence):
    """Display-only provenance; never use artist text to bind a catalog song."""
    review = evidence.get('review') or {}
    session = evidence.get('session') or review.get('locked_session') or {}
    result_fields = (review.get('ocr') or {}).get('fields') or {}
    selection_fields = (session.get('selection') or {}).get('fields') or {}
    return next((_text(value) for value in (
        (evidence.get('song') or {}).get('artist'),
        ((evidence.get('game_result') or {}).get('song') or {}).get('artist'),
        (evidence.get('values') or {}).get('artist'),
        (result_fields.get('artist') or {}).get('value'),
        (selection_fields.get('artist') or {}).get('value'),
    ) if _text(value)), '')


def group_songs(files: list[ParsedFile]) -> list[SongEntry]:
    groups: dict[str, list[ParsedFile]] = defaultdict(list)
    for file in files:
        groups[file.song_folder or file.path].append(file)
    songs = []
    for folder, sources in groups.items():
        candidates = list(dict.fromkeys(f.display_title for f in sources if f.display_title))
        distinct = {normalized_title(title) for title in candidates}
        issues = []
        formats = sorted({f.format for f in sources})
        if len(formats) > 1:
            if "osu" in formats:
                issues.append(Diagnostic("SOURCE_FORMAT_REVIEW", "同資料夾含 osu 與 SM／DWI，保留原檔供人工檢查，不拆分歌曲。"))
            else:
                issues.append(Diagnostic("MULTIPLE_TITLE_FORMATS", "SM＋DWI 為同歌曲的名稱來源，保留兩種格式。", "info"))
        title = candidates[0] if len(distinct) == 1 else None
        if len(distinct) > 1:
            issues.append(Diagnostic("SONG_TITLE_CONFLICT", "同資料夾的來源名稱／副標題不一致，主要顯示名稱待確認；不是歌曲身分衝突，不拆分或覆寫。"))
        if not candidates:
            status = "failed"
        elif any(d.severity != "info" for d in issues) or any(f.status != "ok" for f in sources):
            status = "partial"
        else:
            status = "ok"
        first = sources[0]
        songs.append(SongEntry(folder, first.pack_name, first.storage_group, title,
                               candidates, [f.path for f in sources], status, issues,
                               source_format=formats[0] if len(formats) == 1 else None,
                               detected_formats=formats, format_status="single" if len(formats) == 1 else "mixed",
                               pack_type=("multiplayer_download" if (first.pack_name or "").casefold()=="connect" else "normal") if first.pack_name else None,
                               title_reliable=title is not None and all(f.title_reliable for f in sources)))
    return songs
