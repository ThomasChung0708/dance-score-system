"""Validate a real Phase 4A result, independently of its claimed metric totals.

Run from project root: python -m tools.validate_phase4a
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from dance_results.sqlite_safety import audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, default=Path('reports/phase4a/results.json'))
    args = parser.parse_args()
    labels = json.loads(Path('tests/fixtures/phase4a_ground_truth.json').read_text(encoding='utf-8'))['images']
    report = json.loads(args.report.read_text(encoding='utf-8'))
    images = {r['image']['name']: r for r in report['images']}
    assert set(images) == set(labels), 'Missing or extra screenshot results'
    for name, expected in labels.items():
        result = images[name]
        assert hashlib.sha256(Path(result['image']['path']).read_bytes()).hexdigest() == expected['sha256'], name
        assert result['screen_type'] == expected['screen_type'], name
        for key, wanted in expected.get('fields', {}).items():
            assert result['selection']['fields'][key]['value'] == wanted, (name, key)
        if 'local_player_status' in expected:
            assert result['context']['local_player_status'] == expected['local_player_status'], name
        if result['screen_type'] == 'song_select':
            assert result['matching']['status'] == 'pending_manual_review'
            assert result['matching']['bound_song_id'] is None
            assert result['selection']['selected_pack_id'] is None
    for name in ['相同歌名1.PNG', '相同歌名2.PNG', '相同歌名3.PNG']:
        assert {990, 1004}.issubset({c['song_id'] for c in images[name]['matching']['candidates']})
    assert report['safety']['database_unchanged'] and report['safety']['original_images_unchanged']
    with sqlite3.connect(Path('data/library.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
        current = audit(db)
        assert current == report['safety']['database_before'] == report['safety']['database_after']
        baseline = Path('reports/phase4a/database-before.json')
        if baseline.exists():
            assert current == json.loads(baseline.read_text(encoding='utf-8')), 'Database changed since phase start'
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    print('PASS: all 22 actual images, 35 selection field checks, 4 local result states, JOINT candidates, original hashes and full DB audit')


if __name__ == '__main__':
    main()
