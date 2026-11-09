from __future__ import annotations

import re
import unicodedata

from ..models import Diagnostic, ParsedFile


def normalized_title(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def unique_field(values: list[str], name: str, result: ParsedFile) -> str | None:
    nonempty = list(dict.fromkeys(value for value in values if value))
    if len(nonempty) > 1:
        code = "CONFLICTING_ARTIST_FIELD" if name.upper().startswith("ARTIST") else "CONFLICTING_TITLE_FIELD"
        result.diagnostics.append(Diagnostic(code, f"{name} 有多個不同值，未自行選取。"))
        return None
    return nonempty[0] if nonempty else None


def read_msd_titles(text: str, result: ParsedFile, *, unescape: bool) -> None:
    """Read name fields and DWI basic BPM; skip notes, meter and timing changes.

    Non-title tags cannot cause chart-structure warnings in this metadata reader.
    A # at the beginning of a new line allows recovery after an unterminated tag.
    """
    wanted = {"TITLE", "SUBTITLE", "ARTIST"}
    if result.format == "dwi":
        wanted.add("BPM")
    state = "idle"
    name = ""
    buffer: list[str] = []
    index = 0
    line_has_content = False

    def finish(terminated: bool) -> None:
        if state == "value" and name in wanted:
            if name == "BPM":
                result.source_bpm_fields.append("".join(buffer).strip())
                if not terminated:
                    result.diagnostics.append(Diagnostic("UNTERMINATED_SOURCE_BPM", "DWI BPM 標籤缺少分號，僅保留原值。", "info"))
                return
            fields = result.artist_fields if name == "ARTIST" else result.title_fields
            fields.setdefault(name, []).append("".join(buffer).strip())
            if not terminated:
                code = "UNTERMINATED_ARTIST_TAG" if name == "ARTIST" else "UNTERMINATED_TITLE_TAG"
                result.diagnostics.append(Diagnostic(code, f"{name} 標籤缺少分號，請核對。"))

    while index < len(text):
        char = text[index]
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        if unescape and char == "\\" and index + 1 < len(text):
            if state == "name" or (state == "value" and name in wanted):
                buffer.append(text[index + 1])
            index += 2
            line_has_content = True
            continue
        if char == "#" and (state == "idle" or not line_has_content):
            finish(False)
            state, name, buffer = "name", "", []
        elif state == "name" and char == ":":
            name = "".join(buffer).strip().upper()
            buffer, state = [], "value"
        elif char == ";":
            finish(True)
            state, name, buffer = "idle", "", []
        elif state == "name" or (state == "value" and name in wanted):
            buffer.append(char)
        if char == "\n":
            line_has_content = False
        elif not char.isspace():
            line_has_content = True
        index += 1
    finish(False)
    result.title = unique_field(result.title_fields.get("TITLE", []), "TITLE", result)
    result.subtitle = unique_field(result.title_fields.get("SUBTITLE", []), "SUBTITLE", result)
    result.artist = unique_field(result.artist_fields.get("ARTIST", []), "ARTIST", result)
    bpms = list(dict.fromkeys(v for v in result.source_bpm_fields if v))
    if len(bpms) == 1 and not any(d.code == "UNTERMINATED_SOURCE_BPM" for d in result.diagnostics):
        result.source_bpm = bpms[0]
    elif len(bpms) > 1:
        result.diagnostics.append(Diagnostic("CONFLICTING_SOURCE_BPM", "DWI BPM 有多個不同原值，未自行選取；不影響歌名識別。", "info"))
