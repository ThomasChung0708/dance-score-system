import re

from ..models import Diagnostic, ParsedFile
from .common import unique_field


class OsuParser:
    def parse(self, text: str, result: ParsedFile) -> None:
        lines = text.splitlines()
        valid_header = bool(lines and re.fullmatch(r"osu file format v\d+", lines[0].strip()))
        in_metadata = False
        for raw_line in lines[1:] if valid_header else lines:
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_metadata = line == "[Metadata]"
                continue
            if not in_metadata or ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip() in {"Title", "TitleUnicode", "Artist", "ArtistUnicode"}:
                fields = result.artist_fields if key.strip().startswith("Artist") else result.title_fields
                fields.setdefault(key.strip(), []).append(value.strip())
        unicode_title = unique_field(result.title_fields.get("TitleUnicode", []), "TitleUnicode", result)
        plain_title = unique_field(result.title_fields.get("Title", []), "Title", result)
        # Conflicting preferred values remain unresolved instead of falling back silently.
        conflicting_unicode = len(set(v for v in result.title_fields.get("TitleUnicode", []) if v)) > 1
        result.title = None if conflicting_unicode else (unicode_title or plain_title)
        unicode_artist = unique_field(result.artist_fields.get("ArtistUnicode", []), "ArtistUnicode", result)
        plain_artist = unique_field(result.artist_fields.get("Artist", []), "Artist", result)
        conflicting_artist = len(set(v for v in result.artist_fields.get("ArtistUnicode", []) if v)) > 1
        result.artist = None if conflicting_artist else (unicode_artist or plain_artist)
        if not valid_header:
            result.diagnostics.append(Diagnostic("UNRECOGNIZED_OSU_HEADER", "osu 標頭未辨識；僅讀取找到的 Metadata 歌名，請核對。"))
