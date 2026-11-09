"""Strict metadata-only recovery; unresolved bytes/candidates are retained, not guessed."""
from __future__ import annotations

import codecs
import re

from ..models import Diagnostic, ParsedFile


def recover_metadata(data: bytes, result: ParsedFile, parser, legacy_encoding: str | None) -> None:
    fragments = []
    if result.format in ("sm", "dwi"):
        pattern = rb"(?im)^[ \t]*#(TITLE|SUBTITLE|ARTIST|BPM):[^\r\n]*" if result.format == "dwi" else rb"(?im)^[ \t]*#(TITLE|SUBTITLE|ARTIST):[^\r\n]*"
        fields = [(m.group(1).decode("ascii").upper(), m.group(0)) for m in re.finditer(
            pattern, data)]
        prefix = ""
    else:
        fields = []
        in_metadata = False
        lines = data.splitlines()
        # Keep the actual header; never manufacture a recognized osu header.
        try:
            prefix = (lines[0].decode("utf-8-sig", errors="strict") if lines else "") + "\n[Metadata]\n"
        except UnicodeDecodeError:
            prefix = "[Metadata]\n"
        for raw in lines[1:]:
            stripped = raw.strip()
            if stripped.startswith(b"[") and stripped.endswith(b"]"):
                in_metadata = stripped == b"[Metadata]"
            elif in_metadata:
                match = re.match(rb"[ \t]*(TitleUnicode|Title|ArtistUnicode|Artist)[ \t]*:", raw)
                if match:
                    fields.append((match.group(1).decode("ascii"), raw))
    for name, raw in fields:
        result.metadata_raw_fields.setdefault(name, []).append(raw.hex())
        encoding = "utf-8"
        try:
            fragment = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            # Candidate values are evidence only, unless the human explicitly
            # specified a legacy encoding. UTF-8 fields always take precedence.
            candidates = result.metadata_candidates.setdefault(name, {})
            for candidate in ("cp932", "big5", "cp950"):
                try:
                    text = raw.decode(candidate, errors="strict")
                except UnicodeDecodeError:
                    continue
                probe = ParsedFile(result.path, result.format)
                parser.parse(prefix + text, probe)
                values = probe.source_bpm_fields if name == "BPM" else (probe.artist_fields if name.upper().startswith("ARTIST") else probe.title_fields).get(name, [])
                candidates.setdefault(candidate, []).extend(values)
            if legacy_encoding:
                encoding = codecs.lookup(legacy_encoding).name
                try:
                    fragment = raw.decode(encoding, errors="strict")
                except UnicodeDecodeError:
                    fragment = None
            else:
                fragment = None
            if fragment is None:
                if name != "BPM" and not name.upper().startswith("ARTIST"):
                    result.name_review_required = True
                result.diagnostics.append(Diagnostic("METADATA_ENCODING_REQUIRED", f"{name} 編碼未確認；保留原始 bytes 與候選，不自選讀法。", "info" if name == "BPM" else "warning", name))
                continue
            result.diagnostics.append(Diagnostic("EXPLICIT_LEGACY_ENCODING", f"依明確指定，以 {encoding} 解碼 {name}。", "info", name))
        if "\x00" in fragment:
            raise ValueError("名稱欄位含 NUL，保留原始資料待確認。")
        result.metadata_encodings.setdefault(name, []).append(encoding)
        fragments.append(fragment)
    result.encoding = "metadata"
    parser.parse(prefix + "\n".join(fragments), result)

