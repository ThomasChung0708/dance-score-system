"""Read-only catalog provenance and CSV versus confirmed result observations."""
from contextlib import closing
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

JUDGMENTS = ('perfect', 'cool', 'bad', 'miss')


def screen_count(values):
    counts = [values.get(k) for k in JUDGMENTS]
    if values.get('completion_status') == 'COMPLETED' and all(
            type(n) is int and n >= 0 for n in counts):
        return sum(counts)
    return None


def analyze(database, snapshot):
    database = Path(database).resolve()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        sources = [dict(r) for r in db.execute('''
            SELECT notes_source, notes_verified, count(*) AS charts
            FROM game_difficulties GROUP BY notes_source, notes_verified
            ORDER BY notes_source, notes_verified''')]
        charts = {(r['song_id'], r['difficulty']): dict(r) for r in db.execute('''
            SELECT d.*, COALESCE(s.saved_title,s.latest_title) AS title
            FROM game_difficulties d JOIN songs s ON s.song_id=d.song_id
            WHERE d.notes_source='official_csv' ''')}
        observations = []

        def add(song_id, difficulty, values, session, source):
            n = screen_count(values)
            if song_id is not None and n is not None and n > 0:
                observations.append(dict(song_id=song_id, difficulty=difficulty,
                                         screen_value=n, session=session, source=source))

        for row in db.execute('''
            SELECT d.song_id,d.difficulty,r.* FROM play_records r
            JOIN game_difficulties d ON d.difficulty_id=r.difficulty_id
            WHERE r.match_status='confirmed' AND r.confirmation_method IN
            ('manual_review','manual_note_exception','verified_auto')'''):
            add(row['song_id'], row['difficulty'], dict(row), row['session_id'], 'confirmed_play')
        for path in sorted(Path(snapshot).glob('*/result-review.json')):
            row = json.loads(path.read_text(encoding='utf8'))
            confirmation = row.get('identity_confirmation') or {}
            draft = row.get('draft') or {}
            values = draft.get('values') or {}
            if (confirmation.get('source') == 'explicit_user_review'
                    and confirmation.get('confirmed_at')
                    and confirmation.get('values') == values
                    and confirmation.get('song_id') == draft.get('song_id')
                    and draft.get('identity_reviewed') is True
                    and draft.get('player_verified') is True):
                add(draft.get('song_id'), values.get('difficulty'), values,
                    row['session_id'], 'confirmed_review_snapshot')
        # A submitted play and its review are one observation, with both provenances.
        unique = {}
        for row in observations:
            key = row['song_id'], row['difficulty'], row['session'], row['screen_value']
            entry = unique.setdefault(key, dict(row, sources=[]))
            entry['sources'].append(row['source'])
        conflicts = []
        csv_observations = []
        for row in unique.values():
            chart = charts.get((row['song_id'], row['difficulty']))
            if chart:
                csv_observations.append(row)
                if chart['total_notes'] != row['screen_value']:
                    conflicts.append(dict(row, title=chart['title'], csv_value=chart['total_notes']))
        result = dict(
            database_mode='mode=ro + PRAGMA query_only=ON',
            database_sha256_before=before, schema_version=db.execute('PRAGMA user_version').fetchone()[0],
            source_distribution=sources, csv_chart_count=len(charts),
            csv_storage=dict(
                chart_fields='game_difficulties.total_notes, notes_source, notes_verified, level_source',
                raw_rows='official_song_sources.raw_row_json (with CSV record number and import_id)',
                import_metadata='official_import_runs (source name, CSV SHA-256, encoding, time and summary)',
                import_assignment="official_import._insert_song sets notes_source='official_csv', level_source='official_csv', notes_verified=1; this flag proves import validation, not a screen confirmation.",
                official_source_rows=db.execute('SELECT count(*) FROM official_song_sources').fetchone()[0],
                import_runs=[dict(r) for r in db.execute('''
                    SELECT import_id,source_name,csv_sha256,encoding,imported_at
                    FROM official_import_runs ORDER BY import_id''')],
            ),
            confirmed_observations_raw=len(observations), confirmed_observations=len(unique),
            raw_observation_sources=dict(Counter(r['source'] for r in observations)),
            csv_confirmed_observations=len(csv_observations),
            csv_observed_charts=len({(r['song_id'], r['difficulty']) for r in csv_observations}),
            conflicting_charts=len({(r['song_id'], r['difficulty']) for r in conflicts}),
            conflict_observations=len(conflicts), examples=conflicts,
            evidence_window='Confirmed completed plays and explicitly confirmed snapshot reviews only; pending OCR and failed partial results excluded.',
        )
    result['database_sha256_after'] = hashlib.sha256(database.read_bytes()).hexdigest()
    result['database_unchanged'] = before == result['database_sha256_after']
    if not result['database_unchanged']:
        raise RuntimeError('Snapshot changed during read-only analysis')
    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('data/library.sqlite3'))
    parser.add_argument('--snapshot', type=Path, default=Path('data/confirmed_results_snapshot'))
    parser.add_argument('--output', type=Path, default=Path('reports/round27-note-conflicts.json'))
    args = parser.parse_args()
    result = analyze(args.database, args.snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
