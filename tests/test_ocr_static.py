from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dance_results.database import LibraryDatabase
from dance_results.scanner import scan_library
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_screens import classify, duration_seconds, result_context, room_context
from dance_results.ocr_static import evaluate, summarize
from dance_results.sqlite_safety import audit


def token(text, x=100, y=100, confidence=0.99):
    return dict(text=text, confidence=confidence,
                box=[[x-10, y-8], [x+10, y-8], [x+10, y+8], [x-10, y+8]])


def observation(**changes):
    raw = dict(title='JOINT', difficulty='HARD', game_level=17, total_notes=821, duration_seconds=96)
    raw.update(changes)
    return {k: dict(value=v, confidence=0.99) for k, v in raw.items()}


class MatchingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base/'test.sqlite3'
        root = self.base/'SONG'
        for folder, title in [('Pack A/Folder A', 'JOINT'), ('Pack A/Folder B', 'JOINT'),
                              ('Pack B/Folder A', 'JOINT'), ('Pack B/多語言', '中文 日本語 English☆')]:
            directory = root/folder
            directory.mkdir(parents=True)
            (directory/'song.sm').write_text('#TITLE:'+title+';#ARTIST:川田まみ;', encoding='utf-8')
        with LibraryDatabase(self.path) as library:
            library.sync(scan_library(root))
        self.catalog = ReadOnlyCatalog(self.path)
        self.pack_a = next(p['pack_id'] for p in self.catalog.packs if p['name'] == 'Pack A')

    def tearDown(self):
        self.catalog.close()
        self.temp.cleanup()

    def test_same_name_different_folders_and_packs_are_all_candidates(self):
        result = self.catalog.match(observation())
        self.assertEqual(result['candidate_count'], 3)
        self.assertEqual(len({c['song_id'] for c in result['candidates']}), 3)
        self.assertEqual(result['status'], 'pending_manual_review')
        self.assertIsNone(result['bound_song_id'])

    def test_explicit_pack_filters_but_does_not_supply_missing_chart_data(self):
        result = self.catalog.match(observation(), self.pack_a)
        self.assertEqual(result['candidate_count'], 2)
        self.assertTrue(all('verified_game_difficulty' in c['missing'] for c in result['candidates']))
        self.assertTrue(all('verified_database_duration' in c['missing'] for c in result['candidates']))
        self.assertEqual(result['status'], 'pending_manual_review')

    def test_unicode_exact_title_and_one_candidate_still_require_pack_and_duration(self):
        result = self.catalog.match(observation(title='中文 日本語 English☆'))
        self.assertEqual(result['candidate_count'], 1)
        self.assertEqual(result['status'], 'pending_manual_review')
        self.assertIn('confirmed_pack', result['candidates'][0]['missing'])

    def test_read_only_queries_cannot_mutate_or_migrate(self):
        before = audit(self.catalog.db)
        with self.assertRaises(sqlite3.OperationalError):
            self.catalog.db.execute("UPDATE songs SET latest_title='changed'")
        for _ in range(2):
            self.catalog.match(observation(), self.pack_a)
        self.assertEqual(audit(self.catalog.db), before)
        self.assertEqual(self.catalog.db.execute('PRAGMA user_version').fetchone()[0], 11)

    def test_level_notes_and_duration_must_agree_without_tolerance(self):
        # Future duration fields are represented in memory only, not added to any SQLite schema.
        ids = [i for i, s in self.catalog.songs.items() if s['pack_id'] == self.pack_a]
        for i in ids:
            self.catalog.difficulties[(i, 'HARD')] = dict(difficulty_id=i+100, difficulty='HARD',
                game_level=17, level_source='manual', total_notes=821, notes_source='game_screen',
                notes_verified=1, duration_seconds=96, duration_verified=1)
        result = self.catalog.match(observation(), self.pack_a)
        self.assertEqual(result['status'], 'pending_manual_review')  # Two equally matching candidates.
        self.assertIn('multiple_fully_matching_candidates', result['reasons'])
        for key, wrong in [('game_level', 20), ('total_notes', 929), ('duration_seconds', 95)]:
            result = self.catalog.match(observation(**{key: wrong}), self.pack_a)
            self.assertTrue(all(key in c['conflicts'] for c in result['candidates']))
            self.assertIsNone(result['bound_song_id'])
        # Even one fully known row cannot eliminate another candidate whose chart is unknown.
        del self.catalog.difficulties[(ids[1], 'HARD')]
        self.assertEqual(self.catalog.match(observation(), self.pack_a)['status'], 'pending_manual_review')

    def test_low_confidence_and_different_difficulty_never_auto_bind(self):
        fields = observation()
        fields['game_level']['confidence'] = 0.4
        result = self.catalog.match(fields, self.pack_a)
        self.assertTrue(all('reliable_ocr_game_level' in c['missing'] for c in result['candidates']))
        self.assertEqual(self.catalog.match(observation(difficulty='EASY'), self.pack_a)['status'], 'pending_manual_review')


class StaticScreenTests(unittest.TestCase):
    def test_duration_normalization_rejects_ambiguous_or_invalid_times(self):
        for raw, expected in [('1:36', 96), ('01:36', 96), ('1:35', 95), ('１：３６', 96)]:
            self.assertEqual(duration_seconds(raw), expected)
        for raw in ['', '1:60', '0:00', '1:36.5', '1：3', '—:—', '95']:
            self.assertIsNone(duration_seconds(raw))

    def test_profile_overlay_wins_over_room_preview_and_unknown_stays_unknown(self):
        self.assertEqual(classify([token('PERSONAL INFORMATION'), token('SPEED'), token('準備')])[0], 'profile')
        self.assertEqual(classify([token('unrelated desktop image')])[0], 'unknown')

    def test_multiplayer_gameover_applies_only_to_configured_player_row(self):
        tokens = [token('TC', 180, 250), token('874', 365, 250),
                  token('Other', 180, 390), token('453', 365, 390),
                  token('GAME', 22, 380), token('OVER', 22, 400)]
        own = result_context(tokens, 'TC')
        self.assertTrue(own['multiplayer'])
        self.assertEqual(own['local_player_status'], 'completed_observed')
        self.assertEqual(own['footer_level_scope'], 'host_only_not_local_chart')
        self.assertEqual(result_context(tokens, 'Other')['local_player_status'], 'failed')
        self.assertEqual(result_context(tokens, 'Unknown')['local_player_status'], 'unknown')
        self.assertFalse(own['generates_play_record'])

    def test_host_is_taken_from_roster_badge_not_nickname_in_scene(self):
        tokens = [token('TC', 270, 250), token('Other', 430, 250), token('HOST', 430, 200)]
        self.assertFalse(room_context(tokens, 'TC')['local_is_host'])
        self.assertTrue(room_context(tokens, 'Other')['local_is_host'])
        self.assertIsNone(room_context([token('TC', 530, 370)], 'TC')['local_is_host'])
        self.assertIsNone(room_context([token('TC', 270, 250)], 'TC')['local_is_host'])

    def test_evaluation_requires_exact_image_hash_and_never_guesses_accuracy(self):
        result = dict(image=dict(name='a.png', sha256='a'), screen_type='unknown', selection=None)
        self.assertIsNone(evaluate(result, {})['accuracy'])
        labels = {'a.png': dict(sha256='b', screen_type='song_select')}
        self.assertEqual(evaluate(result, labels)['status'], 'image_hash_mismatch')
        labels['a.png']['sha256'] = 'a'
        self.assertEqual(evaluate(result, labels)['accuracy'], 0)
        self.assertEqual(result['screen_type'], 'unknown')

    def test_ground_truth_manifest_covers_every_original_without_changing_pixels(self):
        base = Path(__file__).parent
        images = list((base/'專案測試圖片').glob('*.PNG'))
        if not images:
            self.skipTest('Local screenshots not distributed with this checkout')
        labels = json.loads((base/'fixtures'/'phase4a_ground_truth.json').read_text(encoding='utf-8'))['images']
        # This immutable manifest covers the original set. Additional local PNGs
        # may have separate ocr_labels entries, or await manual annotation.
        images = [path for path in images if path.name in labels]
        self.assertEqual({p.name for p in images}, set(labels))
        for path in images:
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), labels[path.name]['sha256'])


if __name__ == '__main__':
    unittest.main()
