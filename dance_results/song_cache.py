"""Reader for the game's external song scan cache.

The game writes DATA/CACHE/external_song_cache.json. Despite the name it is a
C# BinaryWriter stream (magic 'SDOC'), written by ExternalScanCache.cs in the
game repository. It lists every external song the game itself recognised:
DATA/ADDON/SONG plus every AdditionalSongFolders root from config.ini, with
the same titles, LV and judged note counts the song select screen shows.

Only versions listed in SUPPORTED_VERSIONS are read. A newer game may change
the layout; then CacheFormatError is raised instead of guessing.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = 0x53444F43
SUPPORTED_VERSIONS = (35,)
MAX_CACHE_BYTES = 256 * 1024 * 1024

FORMAT_NAMES = {
    1: 'osu',
    2: 'sm',
    3: 'gn_pack',
    4: 'malody',
}


class CacheFormatError(ValueError):
    pass


@dataclass
class CacheChart:
    slot: int
    file: str
    index: int
    notes: int
    level: int
    title: str


@dataclass
class CacheSong:
    root: str
    folder: str
    relative_folder: str
    group: str
    song_key: str
    title: str
    artist: str
    pack_id: str
    format: int
    file_id: int
    charts: list[CacheChart] = field(default_factory=list)


@dataclass
class SongCache:
    version: int
    roots: list[str]
    songs: list[CacheSong]


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _take(self, count: int) -> bytes:
        end = self.pos + count
        if count < 0 or end > len(self.data):
            raise CacheFormatError('快取檔被截斷或格式不符。')
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def u32(self) -> int:
        return struct.unpack('<I', self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack('<i', self._take(4))[0]

    def i64(self) -> int:
        return struct.unpack('<q', self._take(8))[0]

    def f32(self) -> float:
        return struct.unpack('<f', self._take(4))[0]

    def f64(self) -> float:
        return struct.unpack('<d', self._take(8))[0]

    def boolean(self) -> bool:
        return self._take(1)[0] != 0

    def string(self) -> str:
        # .NET BinaryWriter: 7-bit encoded byte length, then UTF-8 bytes.
        length = 0
        shift = 0
        while True:
            byte = self._take(1)[0]
            length |= (byte & 0x7F) << shift
            if byte < 0x80:
                break
            shift += 7
            if shift > 28:
                raise CacheFormatError('字串長度格式不符。')
        try:
            return self._take(length).decode('utf-8')
        except UnicodeDecodeError as error:
            raise CacheFormatError('快取字串不是 UTF-8。') from error


def _join(directory: str, relative: str) -> str:
    # The game stores the relative part together with its leading separator.
    if not relative:
        return directory
    if relative[0] in '\\/' or not directory:
        return directory + relative
    return directory + os.sep + relative


def _path(reader: _Reader, directory: str) -> str:
    relative = reader.boolean()
    value = reader.string()
    if not value:
        return ''
    return _join(directory, value) if relative else value


def _relative_to_root(folder: str, root: str) -> str:
    if root and folder.lower().startswith(root.lower()):
        return folder[len(root):].lstrip('\\/')
    return folder


def parse(data: bytes) -> SongCache:
    reader = _Reader(data)
    if reader.u32() != MAGIC:
        raise CacheFormatError('不是熱舞的歌曲快取檔。')
    version = reader.i32()
    if version not in SUPPORTED_VERSIONS:
        raise CacheFormatError(
            f'歌曲快取版本 {version} 尚未支援（支援：{", ".join(map(str, SUPPORTED_VERSIONS))}）；'
            '請更新成績工具。'
        )
    reader.string()  # parse key
    reader.string()  # difficulty key
    roots = [reader.string() for _ in range(reader.i32())]
    songs: list[CacheSong] = []
    folder_count = reader.i32()
    for _ in range(folder_count):
        root_index = reader.i32()
        relative = reader.string()
        root = roots[root_index] if 0 <= root_index < len(roots) else ''
        folder = _join(root, relative) if root else relative
        reader.string()  # signature
        group = reader.string()
        for _ in range(reader.i32()):
            song_key = reader.string()
            title = reader.string()
            reader.string()  # scan title
            artist = reader.string()
            reader.string()  # search aliases
            for _ in range(7):
                _path(reader, folder)  # audio, image, cd, mot, camera, preview, dps
            pack_id = reader.string()
            reader.f64()  # bpm
            reader.f64()  # longest bpm
            song_format = reader.i32()
            reader.i32()  # preview start
            reader.i32()  # preview length
            reader.i32()  # audio duration
            file_id = reader.i32()
            reader.i64()  # gn seed
            charts = []
            for _ in range(reader.i32()):
                slot = reader.i32()
                chart_file = _path(reader, folder)
                index = reader.i32()
                notes = reader.i32()
                level = reader.i32()
                reader.i32()  # duration
                reader.f32()  # msd
                reader.f64()  # raw star
                reader.f32()  # raw msd
                reader.f64()  # bomb bonus
                reader.f64()  # speed bonus
                reader.f64()  # hold bonus
                reader.i32()  # raw judged count
                reader.i32()  # collapsed judged count
                chart_title = reader.string()
                charts.append(CacheChart(slot, chart_file, index, notes, level, chart_title))
            songs.append(CacheSong(
                root=root,
                folder=folder,
                relative_folder=_relative_to_root(folder, root),
                group=group,
                song_key=song_key,
                title=title,
                artist=artist,
                pack_id=pack_id,
                format=song_format,
                file_id=file_id,
                charts=charts,
            ))
    if reader.pos != len(data):
        raise CacheFormatError('歌曲快取結尾有多餘資料；格式可能已改版。')
    return SongCache(version, roots, songs)


def read(path: Path) -> SongCache:
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_CACHE_BYTES:
        raise CacheFormatError('歌曲快取檔過大。')
    return parse(path.read_bytes())
