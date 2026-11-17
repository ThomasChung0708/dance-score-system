"""Game folder mode: songs and results come straight from the game, no OCR.

The game (65_remake, PlayResultLog.cs) appends one JSON line per finished song
to <DATA>/PROFILE/<character>/results.jsonl. Each line already names the exact
chart (official gn + fileId, or external packId + folder), the difficulty, the
LV, the chart's full judged count and the player's own judgments, so nothing
has to be confirmed by hand. Format: docs/GAME_RESULTS.md.

Songs are kept in step with the game too: official songs from
song_table.csv (mode K), external songs from the game's own scan cache, which
already covers every AdditionalSongFolders root and .gn song packs.

Records are written with the same columns, triggers and evidence shape as the
tool's existing automatic ('verified_auto') submissions, so bests, lamps,
progress history and Recent Update treat them like any other formal result.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import song_cache
from .achievements import empty_progress, feedback, progress_for_songs
from .bests_policy import EX_POLICY
from .database import LibraryDatabase, json_text, path_key
from .game_folder import CharacterResults, GameFolder, GameFolderError, default_character, resolve
from .official_import import CATALOG_KEY, CATALOG_ROOT, MAX_CSV_BYTES, _insert_song, read_catalog
from .score_comparison import comparison
from .scoring import calculate
from .sqlite_safety import check_integrity, verified_backup

RESULT_VERSIONS = (1,)
DIFFICULTY_LABELS = {'easy': 'EASY', 'normal': 'NORMAL', 'hard': 'HARD'}
SLOT_LABELS = ('EASY', 'NORMAL', 'HARD')
MAX_POLICY = 'game-chart-judged-v1'
# Bump when sync_official/sync_external change what they write: every library re-syncs once.
CATALOG_SYNC_VERSION = 2
SOURCE = 'game_results_log'
REVIEW_PREFIX = 'game-log:'
SESSION_PREFIX = 'game-'
BACKUP_LABEL = 'pre-game-sync'
KEEP_BACKUPS = 10
SOURCE_FORMATS = {1: 'osu', 2: 'sm'}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def song_folder_key(relative_folder: str, song_key: str = '') -> str:
    """Same key the SONG scanner uses; a folder holding several songs adds the song key."""
    key = path_key(Path(relative_folder))
    return key + '|' + os.path.normcase(song_key) if song_key else key


def _split_root(folder: str, roots: list[tuple[int, str]]) -> tuple[int, str] | None:
    """Longest registered root that contains folder → (root_id, relative folder)."""
    folder = os.path.normpath(folder)
    normalized = os.path.normcase(folder)
    best = None
    for root_id, root in roots:
        root = os.path.normpath(root)
        prefix = os.path.normcase(root)
        if normalized.startswith(prefix.rstrip('\\/') + os.sep) and (best is None or len(prefix) > len(best[2])):
            best = (root_id, folder[len(root):].lstrip('\\/'), prefix)
    return (best[0], best[1]) if best else None


# ---------------------------------------------------------------- catalog


def _ensure_root(db, root: str, kind: str = 'filesystem') -> int:
    key = CATALOG_ROOT if kind == 'official_catalog' else path_key(Path(root))
    db.execute(
        'INSERT INTO library_roots(path,path_key,created_at,root_kind) VALUES(?,?,?,?) '
        'ON CONFLICT(path_key) DO NOTHING',
        (root, key, utc_now(), kind),
    )
    row = db.execute('SELECT root_id,root_kind FROM library_roots WHERE path_key=?', (key,)).fetchone()
    if row['root_kind'] != kind:
        raise ValueError(f'{root} 已被登錄成另一種歌曲來源。')
    return row['root_id']


def _ensure_pack(db, root_id: int, group: str, *, game_song_root: bool = False) -> int | None:
    """game_song_root: this is the game's own SONG folder, whose 'connect' receives multiplayer downloads
    (NetSongFetcher.DefaultGroup). A 'connect' pack anywhere else is just a pack with that name."""
    if not group:
        return None
    key = path_key(Path(group))
    pack_type = 'multiplayer_download' if key == 'connect' and game_song_root else 'normal'
    db.execute(
        'INSERT INTO packs(root_id,relative_path,path_key,name,pack_type) VALUES(?,?,?,?,?) '
        'ON CONFLICT(root_id,path_key) DO UPDATE SET pack_type=excluded.pack_type',
        (root_id, group, key, group, pack_type),
    )
    return db.execute('SELECT pack_id FROM packs WHERE root_id=? AND path_key=?', (root_id, key)).fetchone()[0]


def _game_payload(title: str, details: dict) -> str:
    return json_text({'diagnostics': [], 'title_candidates': [title] if title else [], 'game_cache': details})


def _insert_custom_song(db, root_id, pack_id, relative_folder, song_key, group, title, artist, song_format, details):
    folder_name = Path(relative_folder).name or relative_folder
    return db.execute(
        'INSERT INTO songs(root_id,pack_id,relative_folder,folder_key,storage_group,source_type,latest_title,'
        'saved_title,latest_status,payload_json,created_at,folder_name,artist,source_format,format_status) '
        "VALUES(?,?,?,?,?,'custom',?,?,'ok',?,?,?,?,?,'single')",
        (
            root_id, pack_id, relative_folder, song_folder_key(relative_folder, song_key), group or None,
            title or None, title or None, _game_payload(title, details), utc_now(), folder_name,
            artist or None, SOURCE_FORMATS.get(song_format),
        ),
    ).lastrowid


def _upsert_game_difficulty(db, song_id, label, level, notes, *, keep_manual=True):
    """Store LV/judged count the game reported; returns True when something changed."""
    level = level if isinstance(level, int) and level > 0 else None
    notes = notes if isinstance(notes, int) and notes > 0 else None
    row = db.execute('SELECT * FROM game_difficulties WHERE song_id=? AND difficulty=?', (song_id, label)).fetchone()
    if row is None:
        db.execute(
            'INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,level_source,notes_source,notes_verified) '
            'VALUES(?,?,?,?,?,?,?)',
            (song_id, label, level, notes, 'game_screen' if level else None, 'game_screen' if notes else None,
             1 if notes else 0),
        )
        return True
    if keep_manual and 'manual' in (row['level_source'], row['notes_source']):
        return False
    new_level = level if level is not None else row['game_level']
    new_notes = notes if notes is not None else row['total_notes']
    if (new_level, new_notes) == (row['game_level'], row['total_notes']) and (new_notes is None or row['notes_verified']):
        return False
    db.execute(
        'UPDATE game_difficulties SET game_level=?,total_notes=?,level_source=?,notes_source=?,notes_verified=? '
        'WHERE difficulty_id=?',
        (
            new_level, new_notes,
            'game_screen' if level is not None else row['level_source'],
            'game_screen' if notes is not None else row['notes_source'],
            1 if new_notes is not None else 0,
            row['difficulty_id'],
        ),
    )
    return True


def sync_official(db, csv_path: Path) -> dict:
    """Follow song_table.csv as it is now: add new K songs, update changed ones in place."""
    stats = {'official_added': 0, 'official_updated': 0, 'official_difficulties_changed': 0}
    if not csv_path.is_file():
        return stats
    if csv_path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError('song_table.csv 超過 4 MiB。')
    catalog = read_catalog(csv_path.read_bytes(), str(csv_path))
    root_id = _ensure_root(db, CATALOG_ROOT, 'official_catalog')
    existing = {
        row['gn']: row
        for row in db.execute('SELECT * FROM official_song_sources WHERE catalog_key=?', (CATALOG_KEY,))
    }
    import_id = None
    for row in catalog.rows:
        raw = row['raw']
        old = existing.get(raw['gn'])
        if old is None:
            if import_id is None:
                db.execute(
                    'INSERT INTO official_import_runs(catalog_key,source_name,csv_sha256,encoding,imported_at,summary_json) '
                    'VALUES(?,?,?,?,?,?) ON CONFLICT(catalog_key,csv_sha256) DO NOTHING',
                    (CATALOG_KEY, catalog.source_name, catalog.sha256, 'utf-8-sig', utc_now(), json_text(catalog.summary)),
                )
                import_id = db.execute(
                    'SELECT import_id FROM official_import_runs WHERE catalog_key=? AND csv_sha256=?',
                    (CATALOG_KEY, catalog.sha256),
                ).fetchone()[0]
            _insert_song(db, root_id, row, import_id)
            stats['official_added'] += 1
            continue
        song_id = old['song_id']
        if old['raw_row_json'] != json_text(raw):
            db.execute(
                'UPDATE official_song_sources SET bpm_raw=?,raw_row_json=?,csv_record_number=? WHERE official_source_id=?',
                (raw['bpm'], json_text(raw), row['record_number'], old['official_source_id']),
            )
            db.execute(
                'UPDATE songs SET latest_title=?,saved_title=?,artist=? WHERE song_id=?',
                (raw['title'], raw['title'], raw['artist'], song_id),
            )
            stats['official_updated'] += 1
        for difficulty in row['difficulties']:
            current = db.execute(
                'SELECT * FROM game_difficulties WHERE song_id=? AND difficulty=?', (song_id, difficulty['difficulty'])
            ).fetchone()
            wanted = (difficulty['game_level'], difficulty['total_notes'])
            if current is None:
                db.execute(
                    'INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,level_source,notes_source,notes_verified) '
                    "VALUES(?,?,?,?,'official_csv','official_csv',1)",
                    (song_id, difficulty['difficulty'], *wanted),
                )
                stats['official_difficulties_changed'] += 1
            elif old['raw_row_json'] != json_text(raw) and (current['game_level'], current['total_notes']) != wanted:
                # Only a changed CSV row moves the catalog; values learnt from a
                # played result stay until the CSV itself changes again.
                db.execute(
                    'UPDATE game_difficulties SET game_level=?,total_notes=?,level_source=?,notes_source=?,notes_verified=1 '
                    'WHERE difficulty_id=?',
                    (*wanted, 'official_csv', 'official_csv', current['difficulty_id']),
                )
                stats['official_difficulties_changed'] += 1
    return stats


def sync_external(db, cache: song_cache.SongCache) -> dict:
    """Every external song the game lists, one row per (folder, song key)."""
    stats = {'external_added': 0, 'external_updated': 0, 'external_difficulties_changed': 0}
    root_ids = {root: _ensure_root(db, root) for root in cache.roots}
    # The game lists its own DATA/ADDON/SONG first (ExternalSongLibrary.Roots), then the extra folders.
    game_song_root = cache.roots[0] if cache.roots else None
    packs: dict[tuple[int, str], int | None] = {}
    known: dict[int, dict[str, object]] = {}
    for root_id in set(root_ids.values()):
        known[root_id] = {
            row['folder_key']: dict(row)
            for row in db.execute('SELECT song_id,folder_key,payload_json,latest_title,artist FROM songs WHERE root_id=?', (root_id,))
        }
    for song in cache.songs:
        root_id = root_ids.get(song.root)
        if root_id is None or not song.relative_folder:
            continue
        group = song.group or Path(song.relative_folder).parts[0]
        if (root_id, group) not in packs:
            packs[(root_id, group)] = _ensure_pack(db, root_id, group, game_song_root=song.root == game_song_root)
        details = {'pack_id': song.pack_id, 'song_key': song.song_key, 'format': song.format, 'file_id': song.file_id}
        key = song_folder_key(song.relative_folder, song.song_key)
        row = known[root_id].get(key)
        if row is None:
            song_id = _insert_custom_song(
                db, root_id, packs[(root_id, group)], song.relative_folder, song.song_key, group,
                song.title, song.artist, song.format, details,
            )
            known[root_id][key] = {'song_id': song_id, 'folder_key': key, 'payload_json': None,
                                   'latest_title': song.title or None, 'artist': song.artist or None}
            stats['external_added'] += 1
        else:
            song_id = row['song_id']
            # Rows the old SONG scanner created keep its titles; ours follow the game.
            payload = json.loads(row['payload_json']) if row['payload_json'] else {}
            if 'game_cache' in payload and (row['latest_title'], row['artist']) != (song.title or None, song.artist or None):
                db.execute(
                    'UPDATE songs SET latest_title=?,saved_title=?,artist=?,payload_json=?,is_present=1 WHERE song_id=?',
                    (song.title or None, song.title or None, song.artist or None, _game_payload(song.title, details), song_id),
                )
                stats['external_updated'] += 1
        for chart in song.charts:
            if 0 <= chart.slot < len(SLOT_LABELS):
                if _upsert_game_difficulty(db, song_id, SLOT_LABELS[chart.slot], chart.level, chart.notes):
                    stats['external_difficulties_changed'] += 1
    return stats


# ---------------------------------------------------------------- results


class ResultLineError(ValueError):
    """A line that can never be imported (malformed or contradictory)."""


class Unresolved(ValueError):
    """The song is not in the catalog yet; retried on the next sync."""


def parse_line(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as error:
        raise ResultLineError('不是合法的 JSON') from error
    if not isinstance(data, dict) or data.get('v') not in RESULT_VERSIONS:
        raise ResultLineError('不支援的成績格式版本：' + str(data.get('v') if isinstance(data, dict) else None))
    song = data.get('song')
    if not isinstance(song, dict) or song.get('kind') not in ('official', 'external'):
        raise ResultLineError('缺少歌曲身分')
    if data.get('difficulty') not in DIFFICULTY_LABELS:
        raise ResultLineError('難度不正確')
    for key in ('perfect', 'cool', 'bad', 'miss', 'maxCombo', 'score', 'level', 'chartJudged'):
        if type(data.get(key)) is not int or data[key] < 0:
            raise ResultLineError(key + ' 必須是非負整數')
    if not isinstance(data.get('at'), str):
        raise ResultLineError('缺少時間')
    try:
        datetime.fromisoformat(data['at'].replace('Z', '+00:00'))
    except ValueError as error:
        raise ResultLineError('時間格式不正確') from error
    return data


def _played_at(data: dict) -> str:
    return datetime.fromisoformat(data['at'].replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()


class _Resolver:
    """Line → song_id. Loads the game cache only if a line needs it."""

    def __init__(self, db, folder: GameFolder | None):
        self.db = db
        self.folder = folder
        self._cache = None

    def cache(self):
        if self._cache is None and self.folder is not None and self.folder.song_cache.is_file():
            try:
                self._cache = song_cache.read(self.folder.song_cache)
            except (OSError, song_cache.CacheFormatError):
                self._cache = False
        return self._cache or None

    def official(self, song: dict) -> int:
        gn = str(song.get('gn') or '')
        row = self.db.execute(
            'SELECT song_id FROM official_song_sources WHERE catalog_key=? AND lower(gn)=lower(?)', (CATALOG_KEY, gn)
        ).fetchone()
        if row is None:
            raise Unresolved(f'官方歌 {gn} 不在 song_table.csv 裡')
        return row['song_id']

    def _roots(self):
        return [(r['root_id'], r['path']) for r in self.db.execute("SELECT root_id,path FROM library_roots WHERE root_kind='filesystem'")]

    def _by_folder(self, folder: str, song_key: str):
        split = _split_root(folder, self._roots())
        if split is None:
            return None, None
        root_id, relative = split
        row = self.db.execute(
            'SELECT song_id FROM songs WHERE root_id=? AND folder_key=?', (root_id, song_folder_key(relative, song_key))
        ).fetchone()
        return (row['song_id'] if row else None), (root_id, relative)

    def external(self, song: dict) -> int:
        folder = str(song.get('folder') or '')
        song_key = str(song.get('songKey') or '')
        song_id, location = self._by_folder(folder, song_key) if folder else (None, None)
        if song_id is not None:
            return song_id
        pack_id = str(song.get('packId') or '')
        cache = self.cache() if pack_id else None
        if cache is not None:
            # The folder may have moved since the song was played; packId does not change.
            for candidate in cache.songs:
                if candidate.pack_id == pack_id:
                    found, _ = self._by_folder(candidate.folder, candidate.song_key)
                    if found is not None:
                        return found
        if not folder:
            raise Unresolved('外部歌沒有資料夾也找不到 packId')
        if location is None:
            # A folder outside every known root: register <root>/<group>/<song>'s root.
            root = Path(os.path.normpath(folder)).parent.parent
            location = _split_root(folder, [(_ensure_root(self.db, str(root)), str(root))])
            if location is None:
                raise Unresolved('外部歌資料夾無法登錄：' + folder)
        root_id, relative = location
        group = Path(relative).parts[0] if Path(relative).parts else ''
        details = {'pack_id': pack_id, 'song_key': song_key, 'format': song.get('format'), 'from_result_line': True}
        root_path = self.db.execute('SELECT path FROM library_roots WHERE root_id=?', (root_id,)).fetchone()['path']
        cache = self.cache()
        if cache is not None and cache.roots:
            game_root = path_key(Path(root_path)) == path_key(Path(cache.roots[0]))
        else:
            game_root = [p.lower() for p in Path(root_path).parts[-2:]] == ['addon', 'song']
        return _insert_custom_song(
            self.db, root_id, _ensure_pack(self.db, root_id, group, game_song_root=game_root), relative, song_key, group,
            str(song.get('title') or ''), str(song.get('artist') or ''), song.get('format'), details,
        )


def line_identity(raw: bytes) -> tuple[str, str, str]:
    """(line sha256, review_id, session_id): the same line always maps to the same record."""
    line_sha = hashlib.sha256(raw).hexdigest()
    return line_sha, REVIEW_PREFIX + line_sha, SESSION_PREFIX + line_sha[:40]


def already_imported(db, raw: bytes) -> bool:
    _, review_id, session_id = line_identity(raw)
    return db.execute('SELECT 1 FROM play_records WHERE review_id=? OR session_id=?', (review_id, session_id)).fetchone() is not None


def import_line(db, data: dict, raw: bytes, *, source_path: str, offset: int, resolver: _Resolver) -> dict | None:
    """Insert one result line; None when it was already imported."""
    line_sha, review_id, session_id = line_identity(raw)
    if already_imported(db, raw):
        return None
    song_info = data['song']
    official = song_info['kind'] == 'official'
    song_id = resolver.official(song_info) if official else resolver.external(song_info)
    label = DIFFICULTY_LABELS[data['difficulty']]
    level = data['level'] if data['level'] > 0 else 1
    chart_judged = data['chartJudged']
    failed = data.get('failed') is True
    completion = 'FAILED' if failed else 'COMPLETED'
    judged = data['perfect'] + data['cool'] + data['bad'] + data['miss']
    if chart_judged <= 0 or judged > chart_judged:
        raise ResultLineError(f'判定數 {judged} 超過整首判定數 {chart_judged}')
    if not failed and judged != chart_judged:
        raise ResultLineError(f'正常完成但判定數 {judged} ≠ 整首判定數 {chart_judged}')
    if data['maxCombo'] > data['perfect'] + data['cool']:
        raise ResultLineError('最高連擊超過 Perfect + Cool')

    _upsert_game_difficulty(db, song_id, label, level, chart_judged, keep_manual=False)
    gd = db.execute('SELECT * FROM game_difficulties WHERE song_id=? AND difficulty=?', (song_id, label)).fetchone()
    song = db.execute(
        'SELECT s.*,p.name AS pack_name FROM songs s LEFT JOIN packs p ON p.pack_id=s.pack_id WHERE s.song_id=?', (song_id,)
    ).fetchone()

    values = {
        'player_name': str(data.get('player') or ''),
        'difficulty': label,
        'game_level': level,
        'perfect': data['perfect'], 'cool': data['cool'], 'bad': data['bad'], 'miss': data['miss'],
        'max_combo': data['maxCombo'],
        'game_score': data['score'],
        'grade': str(data.get('grade') or '') or None,
        'completion_status': completion,
        'total_notes': chart_judged,
        'title': song_info.get('title'),
    }
    max_basis = {
        'verified': True, 'notes': chart_judged, 'source': SOURCE, 'policy': MAX_POLICY,
        'scope': 'game_reported_chart', 'is_official_game_max': True,
        'verification_method': 'game_results_log', 'label': '遊戲回報的整首判定數 × 2',
    }
    comp = comparison(
        db, song_id, label, judged if not failed else None,
        max_basis_notes=chart_judged, max_basis_policy=MAX_POLICY, confirmed_only=True,
    )
    comp.pop('observations', None)
    score = calculate(
        values, notes_verified=True, identity_confirmed=True, max_basis=max_basis,
        comparison_conflict=comp['ranking_paused'],
    )
    if score['errors']:
        raise ResultLineError('成績數值不合理：' + ', '.join(e['code'] for e in score['errors']))
    progress = progress_for_songs(db, [song_id])[song_id]
    previous = next((p for p in progress if p['difficulty'] == label), None) or empty_progress(label)
    lamp = score['lamp_preview']
    stamp = utc_now()
    played_at = _played_at(data)
    online = data.get('online') is True
    play_mode = 'multiplayer' if online else 'single'
    raw_text = raw.decode('utf-8')
    evidence = {
        'review_id': review_id, 'review_revision': 0, 'review': {},
        'session': {'source': SOURCE, 'played_at': played_at},
        'screenshot_sha256': line_sha,
        'screenshot_path': f'{source_path}#{offset}',
        'song': dict(song), 'difficulty': dict(gd), 'values': values, 'notes_verified': True,
        'note_sources': {
            'status': SOURCE, 'max_basis_notes': chart_judged, 'catalog_total_notes': gd['total_notes'],
            'judged_total': judged, 'note_count_difference': 0,
        },
        'note_exception': {'authorized': False},
        'max_basis_verification': max_basis,
        'comparison': comp,
        'lamp_verification': {'source': SOURCE, 'independent_game_lamp_verified': True},
        'played_at_source': SOURCE,
        'result_verification': {'confirmed': True, 'source': SOURCE},
        'automatic': {'authorized': True, 'method': SOURCE},
        'full_notes_source': SOURCE, 'notes_source': SOURCE,
        'play_mode': play_mode, 'player_row_id': None,
        'game_result': data, 'game_result_line': raw_text,
        'game_result_file': source_path, 'game_result_offset': offset,
        'warnings': [],
        'submitted_at': stamp, 'request_hash': line_sha,
        'ex_comparison_policy': EX_POLICY, 'scoring_version': 'iidx-v2-independent-max',
    }
    evidence['achievement_feedback'] = feedback(
        previous, ex_score=score['ex_score'], lamp=lamp, completion=completion,
        comparison_paused=comp['ranking_paused'], new_policy=False,
    )
    db.execute(
        'INSERT INTO play_sessions(session_id,player_name,play_mode,is_host,source_type,local_difficulty,local_game_level,'
        "host_result_level,display_song_title,display_pack_name,room_status,player_status,evidence_json) "
        "VALUES(?,?,?,?,?,?,?,NULL,?,?,'result',?,?)",
        (
            session_id, values['player_name'], play_mode,
            (1 if data.get('host') is True else 0) if online else None,
            song['source_type'], label, level,
            str(song_info.get('title') or song['latest_title'] or ''), song['pack_name'],
            'failed' if failed else 'completed',
            json_text({'source': SOURCE, 'game_result': data, 'file': source_path, 'offset': offset}),
        ),
    )
    total_notes = chart_judged if score['max_score'] is not None else None
    record = {
        'session_id': session_id, 'difficulty_id': gd['difficulty_id'], 'song_id': None, 'played_at': played_at,
        'completion_status': completion,
        'perfect': data['perfect'], 'cool': data['cool'], 'bad': data['bad'], 'miss': data['miss'],
        'ex_score': score['ex_score'], 'max_score': score['max_score'], 'max_difference': score['difference'],
        'percentage': score['percentage'], 'lamp_status': lamp,
        'total_notes': total_notes, 'notes_source': 'game_screen' if total_notes is not None else None,
        'match_status': 'confirmed', 'validation_status': 'verified' if lamp else 'pending',
        'game_level': level, 'max_combo': data['maxCombo'], 'game_score': data['score'], 'grade': values['grade'],
        'screenshot_path': evidence['screenshot_path'], 'screenshot_sha256': line_sha,
        'review_id': review_id, 'review_revision': 0, 'confirmed_at': stamp, 'confirmation_method': 'verified_auto',
        'submission_hash': line_sha, 'evidence_json': json.dumps(evidence, ensure_ascii=False),
    }
    columns = ','.join(record)
    marks = ','.join('?' for _ in record)
    record_id = db.execute('INSERT INTO play_records(' + columns + ') VALUES(' + marks + ')', tuple(record.values())).lastrowid
    return {'record_id': record_id, 'song_id': song_id, 'difficulty': label, 'lamp': lamp, 'ex_score': score['ex_score']}


# ---------------------------------------------------------------- service


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _stamp(path: Path):
    try:
        stat = path.stat()
        return [stat.st_mtime_ns, stat.st_size]
    except OSError:
        return None


def prune_backups(database: Path, keep: int = KEEP_BACKUPS) -> None:
    directory = database.parent / 'backups'
    backups = sorted(directory.glob(f'{database.stem}.{BACKUP_LABEL}.*.sqlite3'))
    for old in backups[:-keep] if keep > 0 else backups:
        try:
            old.unlink()
        except OSError:
            pass


class GameSync:
    """Background follower of one game folder. Thread-safe status for the GUI."""

    def __init__(self, database: Path, data_dir: Path, *, write_lock: threading.Lock | None = None, interval: float = 3.0):
        self.database = Path(database)
        self.settings_path = Path(data_dir) / 'game-settings.json'
        self.state_path = Path(data_dir) / 'game-sync.json'
        self.write_lock = write_lock or threading.Lock()
        self.interval = interval
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._status = {
            'state': 'idle', 'last_sync_at': None, 'last_error': None, 'imported_total': 0,
            'last_imported': [], 'pending': [], 'rejected': [], 'catalog': None, 'characters': [], 'last_run': None,
        }

    # -- settings

    def _settings(self) -> dict:
        settings = _read_json(self.settings_path, {})
        return settings if isinstance(settings, dict) else {}

    def game_dir(self) -> str:
        value = self._settings().get('game_dir')
        return value if isinstance(value, str) else ''

    def character(self) -> str:
        value = self._settings().get('character')
        return value if isinstance(value, str) else ''

    def configure(self, game_dir: str, character: str | None = None, *, wake: bool = True) -> dict:
        """Save the game folder and whose results to follow (default: the most recently played)."""
        folder = resolve(game_dir)
        characters = folder.characters()
        keys = {c.key for c in characters}
        if character:
            if character not in keys:
                raise GameFolderError('這個遊戲資料夾裡沒有這個角色。')
            chosen = character
        elif self.game_dir() == str(folder.game_dir) and self.character() in keys:
            chosen = self.character()
        else:
            picked = default_character(characters)
            chosen = picked.key if picked else ''
        _write_json(self.settings_path, {'game_dir': str(folder.game_dir), 'character': chosen})
        self._set(characters=self._character_list(characters, chosen))
        if wake:
            self.trigger()
        return self.status()

    def save_and_sync(self, game_dir: str, character: str | None = None) -> dict:
        """Save, run a pass, and report everything that arrived since the click: a background pass
        that happens to run first still counts, instead of the click reporting "nothing new"."""
        folder = resolve(game_dir)
        if self.game_dir() != str(folder.game_dir):
            # Another game: what was shown for the previous one no longer applies.
            self._set(imported_total=0, last_imported=[], rejected=[], pending=[], catalog=None, last_run=None)
        with self._lock:
            imported_before = self._status['imported_total']
            catalog_before = (self._status['catalog'] or {}).get('synced_at')
        self.configure(game_dir, character, wake=False)
        status = self.sync_now()
        if status.get('last_run') is not None:
            status['last_run'] = dict(
                status['last_run'],
                imported=status['imported_total'] - imported_before,
                catalog_changed=(status.get('catalog') or {}).get('synced_at') != catalog_before,
            )
        return status

    @staticmethod
    def _character_list(characters: list[CharacterResults], chosen: str) -> list[dict]:
        return [
            {'key': c.key, 'name': c.name, 'account': c.account, 'label': c.label,
             'has_results': c.path.is_file(), 'last_played': c.last_played, 'selected': c.key == chosen}
            for c in characters
        ]

    def selected_character(self, folder: GameFolder) -> CharacterResults | None:
        """The configured character; an unset or vanished choice falls back to the default and is saved."""
        characters = folder.characters()
        chosen = next((c for c in characters if c.key == self.character()), None)
        if chosen is None:
            chosen = default_character(characters)
            if chosen is not None:
                _write_json(self.settings_path, {'game_dir': str(folder.game_dir), 'character': chosen.key})
        return chosen

    # -- thread

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name='game-sync', daemon=True)
            self._thread.start()

    def close(self, wait: float = 3) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(wait)

    def trigger(self) -> None:
        self._wake.set()

    def sync_now(self) -> dict:
        """Run one pass right away (waiting for a background pass in progress) and report it."""
        try:
            return self.run_once()
        except Exception as error:  # shown to the user, like the background loop does
            self._set(state='error', last_error=str(error))
            return self.status()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.game_dir():
                try:
                    self.run_once()
                except Exception as error:  # keep following; the GUI shows the reason
                    self._set(state='error', last_error=str(error))
            self._wake.wait(self.interval)
            self._wake.clear()

    def _set(self, **changes) -> None:
        with self._lock:
            self._status.update(changes)

    def status(self) -> dict:
        with self._lock:
            status = json.loads(json.dumps(self._status, ensure_ascii=False))
        status['game_dir'] = self.game_dir()
        status['character'] = self.character()
        selected = next((c for c in status.get('characters', []) if c.get('selected')), None)
        status['character_name'] = selected['name'] if selected else None
        status['character_label'] = selected['label'] if selected else None
        return status

    # -- one pass

    @contextmanager
    def _write(self):
        if not self.write_lock.acquire(timeout=30):
            raise RuntimeError('歌曲庫正在更新，稍後自動重試。')
        try:
            with LibraryDatabase(self.database) as library:
                db = library.connection
                db.execute('BEGIN IMMEDIATE')
                try:
                    yield db
                    check_integrity(db)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        finally:
            self.write_lock.release()

    def run_once(self) -> dict:
        with self._run_lock:
            game_dir = self.game_dir()
            if not game_dir:
                return self.status()
            try:
                folder = resolve(game_dir)
            except GameFolderError as error:
                self._set(state='error', last_error=str(error))
                return self.status()
            state = _read_json(self.state_path, {})
            if state.get('game_dir') != str(folder.game_dir):
                state = {'game_dir': str(folder.game_dir), 'files': {}, 'catalog': {}, 'pending': {}}
            state.setdefault('files', {})
            state.setdefault('catalog', {})
            state.setdefault('pending', {})
            self._set(state='syncing', last_error=None)
            catalog_error = None
            try:
                catalog_stats = self._sync_catalog(folder, state)
            except (OSError, ValueError, sqlite3.DatabaseError) as error:
                catalog_stats = None
                catalog_error = '歌單同步失敗：' + str(error)
            imported, rejected = self._sync_results(folder, state)
            _write_json(self.state_path, state)
            with self._lock:
                self._status['imported_total'] += len(imported)
                if imported:
                    self._status['last_imported'] = (imported + self._status['last_imported'])[:20]
                self._status['rejected'] = (rejected + self._status['rejected'])[:20]
                self._status['pending'] = [
                    {'file': key.rsplit('#', 1)[0], 'reason': item.get('reason')} for key, item in state['pending'].items()
                ][:20]
                if catalog_stats is not None:
                    self._status['catalog'] = catalog_stats
                self._status['characters'] = self._character_list(folder.characters(), self.character())
                self._status['last_run'] = {
                    'imported': len(imported), 'rejected': len(rejected), 'pending': len(state['pending']),
                    'catalog_changed': catalog_stats is not None,
                }
                self._status.update(state='watching', last_sync_at=utc_now(), last_error=catalog_error)
            return self.status()

    def _sync_catalog(self, folder: GameFolder, state: dict) -> dict | None:
        stamps = {'song_table': _stamp(folder.song_table), 'song_cache': _stamp(folder.song_cache)}
        if (stamps == state['catalog'].get('stamps') and state['catalog'].get('stats')
                and state['catalog'].get('version') == CATALOG_SYNC_VERSION):
            return None
        stats = {}
        with self._write() as db:
            stats.update(sync_official(db, folder.song_table))
            if folder.song_cache.is_file():
                cache = song_cache.read(folder.song_cache)
                stats.update(sync_external(db, cache))
                stats['cache_version'] = cache.version
                stats['cache_roots'] = cache.roots
            else:
                stats['cache_missing'] = str(folder.song_cache)
        stats['synced_at'] = utc_now()
        state['catalog'] = {'stamps': stamps, 'stats': stats, 'version': CATALOG_SYNC_VERSION}
        return stats

    def _sync_results(self, folder: GameFolder, state: dict):
        imported: list[dict] = []
        rejected: list[dict] = []
        work = []
        # Only the chosen character: bests and lamps are per library, not per player.
        selected = self.selected_character(folder)
        for character in [selected] if selected is not None else []:
            path = character.path
            if not path.is_file():
                continue
            key = str(path)
            entry = state['files'].get(key, {})
            offset = entry.get('offset', 0)
            size = path.stat().st_size
            if size < offset:
                offset = 0  # replaced or truncated: start over; already imported lines are skipped
            pending = {k: v for k, v in state['pending'].items() if k.rsplit('#', 1)[0] == key}
            if size == offset and not pending:
                continue
            with open(path, 'rb') as handle:  # plain open never blocks the game's appends
                handle.seek(offset)
                data = handle.read()
            end = data.rfind(b'\n')
            lines = []
            if end >= 0:
                position = offset
                for raw in data[:end + 1].split(b'\n')[:-1]:
                    lines.append((position, raw.rstrip(b'\r')))
                    position += len(raw) + 1
            work.append((key, offset + end + 1 if end >= 0 else offset, lines, pending))
        if not work:
            return imported, rejected

        backed_up = False
        with self._write() as db:
            batches = []
            for key, new_offset, lines, pending in work:
                retry = [(int(k.rsplit('#', 1)[1]), v['line'].encode('utf-8')) for k, v in pending.items()]
                todo = [(position, raw) for position, raw in retry + lines if raw.strip()]
                batches.append((key, new_offset, todo))
            if any(not already_imported(db, raw) for _, _, todo in batches for _, raw in todo):
                # The backup must see exactly what this transaction sees: take it before any write.
                verified_backup(db, self.database, BACKUP_LABEL)
                backed_up = True
            resolver = _Resolver(db, folder)
            for key, new_offset, todo in batches:
                for position, raw in todo:
                    pending_key = f'{key}#{position}'
                    try:
                        data = parse_line(raw)
                        db.execute('SAVEPOINT game_line')
                        try:
                            result = import_line(db, data, raw, source_path=key, offset=position, resolver=resolver)
                            db.execute('RELEASE game_line')
                        except BaseException:
                            db.execute('ROLLBACK TO game_line')
                            db.execute('RELEASE game_line')
                            raise
                        state['pending'].pop(pending_key, None)
                        if result is not None:
                            song = data['song']
                            imported.append({
                                **result, 'title': song.get('title'), 'player': data.get('player'), 'at': data.get('at'),
                            })
                    except Unresolved as error:
                        state['pending'][pending_key] = {'line': raw.decode('utf-8', 'replace'), 'reason': str(error)}
                    except (ResultLineError, ValueError, sqlite3.IntegrityError) as error:
                        state['pending'].pop(pending_key, None)
                        rejected.append({'file': key, 'offset': position, 'reason': str(error)})
                state['files'][key] = {'offset': new_offset}
        if backed_up:
            prune_backups(self.database)
        return imported, rejected
