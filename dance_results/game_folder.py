"""Locate everything the score tool reads inside one game installation.

Only the game folder (the one holding dance.exe) is needed:

* dance_Data/StreamingAssets/song_table.csv  official songs (mode K rows)
* <DATA>/CACHE/external_song_cache.json       every external song the game knows
* <DATA>/PROFILE/**/results.jsonl            one line per finished song, per character

<DATA> is <game>/DATA unless <game>/data_root.txt names another folder on its
first line (the same override the game itself honours, SdoDataRoot.cs).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

RESULTS_FILE = 'results.jsonl'
SONG_TABLE = Path('dance_Data') / 'StreamingAssets' / 'song_table.csv'
SONG_CACHE = Path('CACHE') / 'external_song_cache.json'
OFFLINE_CHARACTER = re.compile(r'^[0-9]{8}$')


class GameFolderError(ValueError):
    pass


@dataclass(frozen=True)
class CharacterResults:
    path: Path
    folder: Path
    name: str
    account: str | None
    key: str = ''
    last_played: str | None = None

    @property
    def label(self) -> str:
        return f'{self.name}（線上 · {self.account}）' if self.account else f'{self.name}（單機）'


@dataclass(frozen=True)
class GameFolder:
    game_dir: Path
    data_dir: Path

    @property
    def song_table(self) -> Path:
        return self.game_dir / SONG_TABLE

    @property
    def song_cache(self) -> Path:
        return self.data_dir / SONG_CACHE

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / 'PROFILE'

    def characters(self) -> list[CharacterResults]:
        return character_results(self.profile_dir)


def _data_root_override(game_dir: Path) -> Path | None:
    marker = game_dir / 'data_root.txt'
    try:
        text = marker.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.strip().splitlines()
    first = lines[0].strip() if lines else ''
    if not first:
        return None
    candidate = Path(first)
    if not candidate.is_absolute():
        candidate = game_dir / candidate
    return candidate if candidate.is_dir() else None


def resolve(game_dir: str | Path) -> GameFolder:
    text = str(game_dir or '').strip().strip('"')
    if not text:
        raise GameFolderError('請輸入遊戲資料夾（有 dance.exe 的那一層）。')
    folder = Path(text).expanduser()
    if folder.is_file():
        folder = folder.parent
    try:
        folder = folder.resolve()
    except OSError as error:
        raise GameFolderError('遊戲資料夾無法讀取：' + str(error)) from error
    if not folder.is_dir():
        raise GameFolderError(f'找不到資料夾：{folder}')
    if not (folder / 'dance.exe').is_file() and not (folder / SONG_TABLE).is_file():
        raise GameFolderError(f'{folder} 裡沒有 dance.exe；請選有 dance.exe 的那一層。')
    data_dir = _data_root_override(folder) or folder / 'DATA'
    return GameFolder(folder, data_dir)


def _profile(folder: Path) -> tuple[str, str | None]:
    """(character name, lastPlayedAt) from the character's own profile.json."""
    try:
        data = json.loads((folder / 'profile.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return folder.name, None
    if not isinstance(data, dict):
        return folder.name, None
    name = data.get('name')
    played = data.get('lastPlayedAt')
    return (name.strip() if isinstance(name, str) and name.strip() else folder.name,
            played if isinstance(played, str) and played else None)


def character_results(profile_dir: Path) -> list[CharacterResults]:
    """Every character folder that may hold results, existing file or not."""
    found: list[CharacterResults] = []
    if not profile_dir.is_dir():
        return found
    for child in sorted(profile_dir.iterdir()):
        if child.is_dir() and OFFLINE_CHARACTER.match(child.name):
            name, played = _profile(child)
            found.append(CharacterResults(child / RESULTS_FILE, child, name, None, child.name, played))
    accounts = profile_dir / 'accounts'
    if accounts.is_dir():
        for account in sorted(accounts.iterdir()):
            if not account.is_dir():
                continue
            for child in sorted(account.iterdir()):
                # _retired holds folders of replaced characters; never a live one.
                if not child.is_dir() or child.name.startswith('_'):
                    continue
                name, played = _profile(child)
                key = f'accounts/{account.name}/{child.name}'
                found.append(CharacterResults(child / RESULTS_FILE, child, name, account.name, key, played))
    return found


def default_character(characters: list[CharacterResults]) -> CharacterResults | None:
    """The character most recently played, preferring ones that already have results."""
    if not characters:
        return None
    with_results = [c for c in characters if c.path.is_file()]
    pool = with_results or characters
    return max(pool, key=lambda c: c.last_played or '')
