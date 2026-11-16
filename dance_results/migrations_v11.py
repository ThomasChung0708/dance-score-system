"""Allow confirmed song-bound results without inventing catalog charts."""
import json
from datetime import datetime, timezone

from .migrations_v10 import validation_sql as previous_validation
from .sqlite_safety import audit, projection, check_integrity, verified_backup, quoted


def validation_sql():
    sql = previous_validation().replace(
        'NEW.difficulty_id IS NULL OR',
        '(NEW.difficulty_id IS NULL AND NEW.song_id IS NULL) OR')
    sql = sql.replace(
        'NOT EXISTS(SELECT 1 FROM game_difficulties d WHERE',
        'NEW.difficulty_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM game_difficulties d WHERE')
    sql = sql.replace('pr.difficulty_id=NEW.difficulty_id AND', """(
        pr.difficulty_id=NEW.difficulty_id OR (pr.song_id=NEW.song_id
        AND json_extract(pr.evidence_json,'$.values.difficulty') IS
            json_extract(NEW.evidence_json,'$.values.difficulty'))) AND""")
    # Uncharted results are scoped to this song/result, never an official chart
    # or a shortcut around known chart conflicts. Original guards still apply.
    return '(' + sql + """ OR (NEW.song_id IS NOT NULL AND (
        NOT EXISTS(SELECT 1 FROM songs s WHERE s.song_id=NEW.song_id
            AND s.source_type='custom'
            AND s.song_id IS json_extract(NEW.evidence_json,'$.song.song_id')
            AND s.root_id IS json_extract(NEW.evidence_json,'$.song.root_id')
            AND s.relative_folder IS json_extract(NEW.evidence_json,'$.song.relative_folder'))
        OR NEW.difficulty_id IS NOT NULL
        OR json_extract(NEW.evidence_json,'$.difficulty') IS NOT NULL
        OR EXISTS(SELECT 1 FROM game_difficulties d WHERE d.song_id=NEW.song_id
            AND (json_extract(NEW.evidence_json,'$.values.difficulty') IS NULL
                OR d.difficulty=json_extract(NEW.evidence_json,'$.values.difficulty')))
    )))"""


def bests_view_sql():
    from .bests_policy import BESTS_VIEW
    identity = """(r.difficulty_id=d.difficulty_id OR (r.song_id=d.song_id AND
        json_extract(r.evidence_json,'$.values.difficulty')=d.difficulty))"""
    return BESTS_VIEW.replace('r.difficulty_id=d.difficulty_id', identity).replace(
        'FROM game_difficulties d', """FROM (
            SELECT difficulty_id,song_id,difficulty FROM game_difficulties
            UNION
            SELECT NULL,r.song_id,json_extract(r.evidence_json,'$.values.difficulty')
            FROM play_records r WHERE r.song_id IS NOT NULL
                AND json_extract(r.evidence_json,'$.values.difficulty') IN ('EASY','NORMAL','HARD')
                AND NOT EXISTS(SELECT 1 FROM game_difficulties gd WHERE gd.song_id=r.song_id
                    AND gd.difficulty=json_extract(r.evidence_json,'$.values.difficulty'))
        ) d""")


def migrate_v10(db, path, *, new_database=False):
    if db.in_transaction:
        raise ValueError('Migration requires an independent transaction.')
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()[0]
    # SQLite's documented table-rebuild procedure: external references retain
    # their original table name, are checked before commit, and are re-enabled
    # even when DDL or copying rolls back. Never use writable_schema.
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        db.execute('BEGIN IMMEDIATE')
        check_integrity(db)
        columns = projection(db)
        before = audit(db, columns)
        bests = [tuple(r) for r in db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id')]
        backup = None if new_database else verified_backup(db, path, 'pre-v11-song-results')
        # Rebuild the CHECK constraint, retaining every historical column/value.
        # Save dependent schema before replacement so no dangling view/trigger
        # can make ALTER TABLE fail. DDL and data replacement are one transaction.
        objects = db.execute("""SELECT type,name,sql FROM sqlite_master
            WHERE type IN ('view','trigger') OR
                (type='index' AND tbl_name='play_records' AND sql IS NOT NULL)
            ORDER BY rowid""").fetchall()
        table_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='play_records'").fetchone()[0]
        for kind, name, _ in objects:
            db.execute('DROP ' + kind + ' ' + quoted(name))
        table_sql = table_sql.replace('play_records', 'play_records_v11', 1)
        table_sql = table_sql.replace(
            'record_id INTEGER PRIMARY KEY,',
            'record_id INTEGER PRIMARY KEY,\n    song_id INTEGER REFERENCES songs(song_id) ON DELETE RESTRICT,')
        old = "CHECK(match_status='pending' OR difficulty_id IS NOT NULL)"
        if old not in table_sql:
            raise ValueError('Unexpected record constraint; migration rolled back.')
        db.execute(table_sql.replace(old,
            "CHECK(match_status='pending' OR difficulty_id IS NOT NULL OR song_id IS NOT NULL)"))
        names = ','.join(map(quoted, columns['play_records']))
        db.execute('INSERT INTO play_records_v11 (' + names + ') SELECT ' + names + ' FROM play_records')
        db.execute('DROP TABLE play_records')
        db.execute('ALTER TABLE play_records_v11 RENAME TO play_records')
        for kind, name, sql in objects:
            if name == 'difficulty_bests':
                sql = bests_view_sql()
            if name in ('formal_record_valid_insert', 'formal_record_valid_update'):
                operation = 'INSERT' if name.endswith('insert') else 'UPDATE'
                sql = ('CREATE TRIGGER ' + name + ' BEFORE ' + operation +
                    ' ON play_records WHEN ' + validation_sql() +
                    " BEGIN SELECT RAISE(ABORT,'formal result MAX or comparison evidence is incomplete'); END")
            db.execute(sql)
        after = audit(db, columns)
        check_integrity(db)
        if before != after or bests != [tuple(r) for r in db.execute('SELECT * FROM difficulty_bests ORDER BY difficulty_id')]:
            raise ValueError('Historical data or bests changed; migration rolled back.')
        result = dict(from_version=10, to_version=11, backup_path=backup,
            before=before, after=after, all_ids_values_and_history_preserved=True,
            old_bests_unchanged=True)
        db.execute('INSERT INTO schema_migrations VALUES(?,?,?,?)',
            (11, datetime.now(timezone.utc).isoformat(), backup, json.dumps(result, ensure_ascii=False)))
        db.execute('PRAGMA user_version=11')
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute('PRAGMA foreign_keys=' + str(foreign_keys))
