"""Read-only totals over the owner's saved history, independent of best eligibility.

The library stores only the selected character/local reviewed player. Do not
filter by today's nickname: older saved names are still the owner's history.
"""
import json

from .bests_policy import SCORE_ELIGIBLE, LAMP_ELIGIBLE
from .songs import catalog_display_title, record_display_title, record_display_artist


# History's immutable song snapshot takes precedence, then the explicit binding
# and finally the chart binding used by game-folder and legacy OCR records.
SONG_ID = "COALESCE(json_extract(r.evidence_json,'$.song.song_id'),r.song_id,d.song_id)"
DIFFICULTY = "COALESCE(json_extract(r.evidence_json,'$.values.difficulty'),d.difficulty,p.local_difficulty)"
JOINS = (
    ' FROM play_records r JOIN play_sessions p ON p.session_id=r.session_id '
    'LEFT JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id '
)
LAMP_RANK = {'UNPLAYED': 0, 'FAILED': 1, 'CLEAR': 2, 'AC': 3, 'ACP': 4}


def _song_batches(song_ids):
    ids = sorted(set(song_ids))
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        yield ' WHERE ' + SONG_ID + ' IN (' + ','.join('?' for _ in batch) + ')', batch


def counts_for_songs(db, song_ids):
    """Difficulty breakdowns only for the requested page or detail song."""
    result = {}
    for where, args in _song_batches(song_ids):
        for row in db.execute(
            'SELECT ' + SONG_ID + ' AS song_id,' + DIFFICULTY + ' AS difficulty,COUNT(*) AS plays'
            + JOINS + where + ' GROUP BY 1,2', args,
        ):
            item = result.setdefault(row['song_id'], dict(play_count=0, play_counts={}))
            item['play_count'] += row['plays']
            item['play_counts'][row['difficulty'] or 'UNKNOWN'] = row['plays']
    return result


def totals_for_songs(db):
    """One aggregate for count sorting; no per-difficulty breakdowns."""
    return {row['song_id']: row['plays'] for row in db.execute(
        'SELECT ' + SONG_ID + ' AS song_id,COUNT(*) AS plays' + JOINS + ' GROUP BY 1'
    )}


def played_song_ids(db, song_ids):
    result = set()
    for where, args in _song_batches(song_ids):
        result.update(row['song_id'] for row in db.execute(
            'SELECT DISTINCT ' + SONG_ID + ' AS song_id' + JOINS + where, args,
        ))
    return result


def song_counts(db, song_id):
    return counts_for_songs(db, [song_id]).get(song_id, dict(play_count=0, play_counts={}))


def unbound_identity(db, row):
    evidence = json.loads(row['evidence_json'])
    title = record_display_title(db, evidence, None, row['display_song_title'])
    return title, record_display_artist(evidence)


def unbound_record_ids(db, title, artist=None):
    ids = []
    for row in db.execute(
        'SELECT r.record_id,r.evidence_json,p.display_song_title' + JOINS +
        ' WHERE NOT EXISTS(SELECT 1 FROM songs s WHERE s.song_id=' + SONG_ID + ')'
    ):
        displayed_title, displayed_artist = unbound_identity(db, row)
        if displayed_title == title and (artist is None or displayed_artist == artist):
            ids.append(row['record_id'])
    return ids


def ranked_songs(db):
    catalog = {r['song_id']: dict(r) for r in db.execute('SELECT * FROM songs')}
    source_artists = {}
    for row in db.execute(
        "SELECT song_id,COALESCE(NULLIF(saved_artist,''),"
        "json_extract(payload_json,'$.artist')) AS artist FROM song_sources ORDER BY source_id"
    ):
        if row['artist']:
            source_artists.setdefault(row['song_id'], []).append(row['artist'])
    groups = {}
    for row in db.execute(
        'SELECT r.record_id,r.played_at,julianday(r.played_at) AS played_jd,'
        'r.ex_score,r.lamp_status,r.evidence_json,p.display_song_title,'
        + SONG_ID + ' AS song_id,(' + SCORE_ELIGIBLE + ') AS score_eligible,('
        + LAMP_ELIGIBLE + ') AS lamp_eligible' + JOINS
    ):
        sid = row['song_id'] if row['song_id'] in catalog else None
        if sid is not None:
            key = ('song', sid)
            song = catalog[sid]
            title = catalog_display_title(db, song) if key not in groups else None
            artists = source_artists.get(sid, []) if song['source_type'] != 'official' else []
            artist = ' / '.join(dict.fromkeys(artists)) or song['artist'] or ''
        else:
            title, artist = unbound_identity(db, row)
            key = ('title', title, artist)
        item = groups.setdefault(key, dict(
            song_id=sid, title=title, artist=artist, play_count=0,
            best_lamp='UNPLAYED', best_ex_score=None, last_played_at=None,
            _last=(-1e99, 0),
        ))
        item['play_count'] += 1
        latest = (row['played_jd'] if row['played_jd'] is not None else -1e99, row['record_id'])
        if latest > item['_last']:
            item['_last'] = latest
            item['last_played_at'] = row['played_at']
        if row['score_eligible'] and row['ex_score'] is not None:
            item['best_ex_score'] = max(item['best_ex_score'] or 0, row['ex_score'])
        if row['lamp_eligible'] and LAMP_RANK.get(row['lamp_status'], 0) > LAMP_RANK[item['best_lamp']]:
            item['best_lamp'] = row['lamp_status']
    rows = sorted(groups.values(), key=lambda r: (
        -r['play_count'], -r['_last'][0], -r['_last'][1], r['title'] or '', r['song_id'] or 0,
    ))
    previous = None
    for index, row in enumerate(rows, 1):
        if row['play_count'] != previous:
            rank = index
        row['rank'] = rank
        previous = row['play_count']
        del row['_last']
    return rows
