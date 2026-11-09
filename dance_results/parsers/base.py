from __future__ import annotations

import codecs
import hashlib
import unicodedata
from pathlib import Path
from typing import Protocol

from ..models import Diagnostic, ParsedFile

MAX_FILE_BYTES = 64 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".sm", ".dwi", ".osu"}


class SongTitleReader(Protocol):
    def parse(self, text: str, result: ParsedFile) -> None: ...


def parse_file(path: Path, legacy_encoding: str | None = None) -> ParsedFile:
    result = ParsedFile(str(path), path.suffix.lower().lstrip("."))
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        result.diagnostics.append(Diagnostic("UNSUPPORTED_EXTENSION", "不屬於支援的譜面副檔名。", "error"))
        return result
    try:
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError("檔案超過 64 MiB，已跳過，請人工檢查。")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        after = path.stat()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("檔案超過 64 MiB。")
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("檔案在讀取時改變，可能仍在下載；請稍後重新掃描。")
        result.sha256 = hashlib.sha256(data).hexdigest()
        from .sm import SMParser
        from .dwi import DWIParser
        from .osu import OsuParser
        parser: SongTitleReader = {"sm": SMParser, "dwi": DWIParser, "osu": OsuParser}[result.format]()
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
        try:
            text = data.decode(encoding, errors="strict")
        except UnicodeDecodeError as decode_error:
            # Strict candidate decodings are evidence, never a guessed title. Keep
            # the exact bytes even if an explicit legacy decoding later fails.
            result.raw_data = data
            result.raw_data_preserved = True
            for candidate in ("cp932", "big5", "cp950"):
                try:
                    data.decode(candidate, errors="strict")
                    result.encoding_candidates.append(candidate)
                except UnicodeDecodeError:
                    pass
            from .metadata_encoding import recover_metadata
            recover_metadata(data, result, parser, legacy_encoding)
            result.diagnostics.append(Diagnostic(
                "FILE_ENCODING_PARTIAL" if result.title else "ENCODING_REQUIRED",
                f"整檔 {encoding} 嚴格解碼失敗：byte {decode_error.start}，{decode_error.reason}；僅逐欄位讀取可確認的名稱，原始資料完整保留。",
                "warning" if result.title else "error", f"byte:{decode_error.start}"))
        else:
            result.encoding = encoding
            if "\x00" in text:
                raise ValueError("內容含 NUL，不視為可讀文字譜面。")
            parser.parse(text, result)
            for name, values in {**result.title_fields, **result.artist_fields}.items():
                result.metadata_encodings[name] = [encoding] * len(values)
            if result.source_bpm_fields:
                result.metadata_encodings['BPM'] = [encoding] * len(result.source_bpm_fields)
        for value, is_name in ((result.display_title, True), (result.artist, False)):
            if value and any(unicodedata.category(c) == "Co" or c == "\ufffd" for c in value):
                if is_name:
                    result.name_review_required = True
                result.raw_data, result.raw_data_preserved = data, True
                result.diagnostics.append(Diagnostic(
                    "SUSPECTED_NAME_ENCODING" if is_name else "SUSPECTED_ARTIST_ENCODING",
                    "原始名稱包含私用區／替代字元，保留原值供人工確認，不採用其他候選覆蓋。"))
        if not result.title:
            result.diagnostics.append(Diagnostic("MISSING_TITLE", "沒有可確認的歌曲名稱；不以資料夾或譜面難度名稱代替。", "error"))
    except (OSError, ValueError, LookupError) as error:
        result.diagnostics.append(Diagnostic("READ_OR_PARSE_ERROR", str(error), "error"))
    return result
