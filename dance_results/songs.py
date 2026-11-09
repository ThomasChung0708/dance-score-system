from collections import defaultdict

from .models import Diagnostic, ParsedFile, SongEntry
from .parsers.common import normalized_title


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
