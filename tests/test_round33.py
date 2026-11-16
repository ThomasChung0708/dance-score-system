"""Category/source authority and cross-source chart identity regressions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw

from dance_results.monitor_ocr import apply_selection
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_matching import ReadOnlyCatalog, normalize, result_identity_fields
from dance_results.ocr_screens import category_tab, category_label, parse_selection, CATEGORY_ROI
from dance_results.pack_selection import infer_selected_pack
from test_result_review import token

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT/'tests/fixtures/round33/session9.json').read_text(encoding='utf8'))


def catalog():
    c = ReadOnlyCatalog.__new__(ReadOnlyCatalog)
    c.songs = {}
    c.index = {}
    c.difficulties = {}
    c.official_sources = {}
    c.reviews = set()
    c.packs = [dict(pack_id=35, root_id=1, name='Custom Pack')]
    for row in FIXTURE.values():
        sid = row['expected_song_id']
        f = row['fields']
        title = f['title']['value']
        c.songs[sid] = dict(song_id=sid, source_type='official', pack_id=None, root_id=2,
            saved_title=title, latest_title=title, pack_name=None, folder_name=title, relative_folder=title)
        c.index[normalize(title)] = {sid}
        c.difficulties[(sid, 'HARD')] = dict(difficulty_id=sid+100, difficulty='HARD',
            game_level=f['game_level']['value'], total_notes=f['total_notes']['value'],
            notes_source='official_csv', notes_verified=1, level_source='official_csv')
        c.official_sources[sid] = dict(mode='K', bpm_raw=str(f['bpm']['value']))
    c.official_sources[4341]['bpm_raw'] = '158.5'
    c.songs[1358] = dict(c.songs[4357], song_id=1358, source_type='custom', pack_id=35, root_id=1)
    c.index['libera me'].add(1358)
    return c


def selection(fields=None, category=None):
    return dict(fields=copy.deepcopy(fields or next(iter(FIXTURE.values()))['fields']),
        category=category, category_confidence=.95 if category else 0,
        song_title_tokens=[token(t, 500, 250+i*30) for i, t in enumerate(('Libera Me', 'Uso'))],
        visible_packs=[], pack_list_visible=False, pack_row_hint=None)


class CategoryTests(unittest.TestCase):
    def test_all_native_crop(self):
        with Image.open(ROOT/'tests/fixtures/round33/tab-row-all.png') as image:
            result = category_tab([], image, (328, 73, 905, 117), geometry=True)
        self.assertEqual(result['category'], 'all')
        self.assertGreater(result['category_evidence']['margin'], 24)

    def test_full_window_custom_fixtures(self):
        tabs = [token(t, x, 160) for t, x in zip(('EASY', 'NORMAL', 'HARD'), (457, 640, 824))]
        for name in ('pack_select_blue6th_full.png', 'pack_select_osutransfer_full.png', 'realplay3_dearest_select.png'):
            with self.subTest(image=name):
                path = next((ROOT/'tests').rglob(name), None)
                if path is None:
                    self.skipTest('Local full-window screenshots unavailable')
                with Image.open(path) as image:
                    result = parse_selection(tabs, image.convert('RGB').resize((1024, 800)))
                self.assertEqual(result['category'], 'jinyue')

    def test_ocr_variants(self):
        for text, expected in [('全 部', 'all'), ('劲乐', 'jinyue'), ('懷旧', 'nostalgia'),
                               ('随机', 'random'), ('收藏', 'favorite'), ('最新', 'newest')]:
            self.assertEqual(category_label(text), expected)

    def test_unanchored_blank_tied_and_wrong_theme_abstain(self):
        for colour in ('black', (245, 139, 124)):
            image = Image.new('RGB', (1024, 800), colour)
            self.assertIsNone(category_tab([], image, geometry=True)['category'])
        with Image.open(ROOT/'tests/fixtures/round33/tab-row-all.png') as image:
            self.assertIsNone(category_tab([], image, (328, 73, 905, 117))['category'])

    def test_each_slot_and_ocr_geometry_disagreement(self):
        for i, category in enumerate(('all', 'random', 'favorite', 'newest', 'jinyue', 'nostalgia')):
            image = Image.new('RGB', (1024, 800), (245, 139, 124))
            left, top, right, bottom = CATEGORY_ROI
            width = (right-left)/6
            ImageDraw.Draw(image).rectangle((int(left+i*width)+3, top, int(left+(i+1)*width)-3, bottom),
                                             fill=(249, 171, 155))
            self.assertEqual(category_tab([], image, geometry=True)['category'], category)
            wrong = [token('勁樂' if i == 0 else '全部', left+(i+.5)*width, 216)]
            self.assertIsNone(category_tab(wrong, image, geometry=True)['category'])


class SourceTests(unittest.TestCase):
    def test_all_debounces_revokes_stale_pack_and_skips_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = Tracker(temp)
            tracker.current_pack = dict(pack_id=35, root_id=1, status='confirmed')
            tracker.current_source = 'custom'
            for i, source in enumerate(('unknown', 'official')):
                observation = apply_selection(dict(state='SONG_SELECT', selection=selection(category='all')),
                    Image.new('RGB', (1024, 800)), tracker, catalog())
                self.assertEqual(tracker.current_source, source)
                self.assertIsNone(tracker.current_pack)
                self.assertIsNone(observation['selection']['selected_pack_id'])
            self.assertEqual(observation['source_type'], 'official')

    def test_jinyue_debounces_and_keeps_pack_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = Tracker(temp)
            tracker.category_observation('jinyue', .95, 1)
            self.assertEqual(tracker.current_source, 'unknown')
            tracker.category_observation('jinyue', .95, 2)
            self.assertEqual(tracker.current_source, 'custom')
            tracker.pack_observation(dict(pack_id=35, root_id=1, status='confirmed'), inferred=True, now=3)
            tracker.category_observation('jinyue', .95, 4)
            self.assertEqual(tracker.current_pack['pack_id'], 35)

    def test_noise_unsupported_and_window_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = Tracker(temp)
            tracker.category_observation('all', .95, 1)
            tracker.category_observation(None, 0, 2)
            tracker.category_observation('all', .95, 3)
            self.assertEqual(tracker.current_source, 'unknown')
            for category in ('random', 'favorite', 'newest', 'nostalgia'):
                tracker.category_observation(category, .95, 4)
                self.assertEqual(tracker.current_source, 'unknown')
            tracker.category_observation('all', .95, 5)
            tracker.category_observation('all', .95, 6)
            tracker.new_window('test')
            self.assertIsNone(tracker.current_category)

    def test_overlap_does_not_count_unknown_but_group_and_jinyue_preserve_it(self):
        c = catalog()
        c.songs[1359] = dict(c.songs[4350], song_id=1359, source_type='custom', pack_id=35)
        c.index['uso'].add(1359)
        s = selection()
        self.assertIsNone(infer_selected_pack(s, c)['selected_pack_id'])
        s['category'] = 'jinyue'
        s['category_confidence'] = .95
        self.assertEqual(infer_selected_pack(s, c)['selected_pack_id'], 35)
        s['category'] = None
        s['pack_list_visible'] = True
        self.assertEqual(infer_selected_pack(s, c)['selected_pack_id'], 35)

    def test_official_majority_vetoes_two_nonoverlap_titles(self):
        c = catalog()
        for i, title in enumerate(('custom one', 'custom two')):
            c.index[title] = {800+i}
            c.songs[800+i] = dict(source_type='custom', pack_id=35)
        s = selection()
        s['song_title_tokens'] = [token(t, 500, 250) for t in (*c.index,)]
        self.assertIsNone(infer_selected_pack(s, c)['selected_pack_id'])

    def test_unknown_official_list_revokes_only_title_inferred_stale_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            for explicit_group in (False, True):
                tracker = Tracker(Path(temp)/str(explicit_group))
                tracker.current_source = 'custom'
                tracker.current_pack = dict(pack_id=35, root_id=1, status='confirmed',
                    source='right_song_list_unique_pack', evidence=dict(visible_pack_ids=[35] if explicit_group else []))
                observation = apply_selection(dict(state='SONG_SELECT', selection=selection()),
                    Image.new('RGB', (1024, 800)), tracker, catalog())
                self.assertEqual(tracker.current_pack is not None, explicit_group)
                if not explicit_group:
                    self.assertEqual(tracker.current_source, 'unknown')

    def test_explicit_custom_mode_preserves_preconfirmed_group_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            tracker = Tracker(temp)
            tracker.current_pack = dict(pack_id=35, root_id=1, status='confirmed')
            apply_selection(dict(state='SONG_SELECT', selection=selection(category='jinyue')),
                Image.new('RGB', (1024, 800)), tracker, catalog(), source_mode='custom')
            self.assertEqual(tracker.current_pack['pack_id'], 35)


class IdentityTests(unittest.TestCase):
    def test_three_recorded_selections_unknown_and_official(self):
        c = catalog()
        for row in FIXTURE.values():
            fields = row['fields']
            identity = c.match_identity(fields)
            official = c.match_official(fields, source_confirmed=True)
            self.assertEqual(identity['confirmed_song_id'], row['expected_song_id'])
            self.assertEqual(official['confirmed_song_id'], row['expected_song_id'])

    def test_real_custom_exact_copy_blocks_official(self):
        c = catalog()
        c.difficulties[(1358, 'HARD')] = dict(c.difficulties[(4357, 'HARD')], difficulty_id=99)
        fields = next(r['fields'] for r in FIXTURE.values() if r['expected_song_id'] == 4357)
        self.assertIsNone(c.match_identity(fields).get('confirmed_song_id'))

    def test_missing_low_confidence_or_changed_notes_do_not_block_unique_official(self):
        fields = next(r['fields'] for r in FIXTURE.values() if r['expected_song_id'] == 4357)
        for change in (dict(value=None), dict(confidence=.7), dict(value=1796), dict(reason='uncertain')):
            f = copy.deepcopy(fields)
            f['total_notes'].update(change)
            self.assertEqual(catalog().match_identity(f).get('confirmed_song_id'), 4357)

    def test_notes_break_tie_between_official_title_difficulty_level_matches(self):
        c = catalog()
        c.songs[9000] = dict(c.songs[4357], song_id=9000)
        c.official_sources[9000] = dict(c.official_sources[4357])
        c.index['libera me'].add(9000)
        c.difficulties[(9000, 'HARD')] = dict(c.difficulties[(4357, 'HARD')],
            difficulty_id=9001, total_notes=1796)
        fields = copy.deepcopy(next(r['fields'] for r in FIXTURE.values() if r['expected_song_id'] == 4357))
        fields['total_notes']['value'] = 1796
        self.assertEqual(c.match_identity(fields)['confirmed_song_id'], 9000)
        fields['total_notes']['value'] = None
        self.assertIsNone(c.match_identity(fields).get('confirmed_song_id'))

    def test_pure_ruby_four_uncharted_namesakes_stay_pending(self):
        c = catalog()
        c.index['pure ruby'] = {810, 811, 812, 813}
        for sid in c.index['pure ruby']:
            c.songs[sid] = dict(c.songs[1358], song_id=sid, saved_title='Pure Ruby')
        fields = copy.deepcopy(next(iter(FIXTURE.values()))['fields'])
        fields['title']['value'] = 'Pure Ruby'
        result = c.match_identity(fields)
        self.assertEqual(result['candidate_count'], 4)
        self.assertIsNone(result.get('confirmed_song_id'))

    def test_custom_only_unique_still_custom(self):
        c = catalog()
        c.index['libera me'] = {1358}
        fields = next(r['fields'] for r in FIXTURE.values() if r['expected_song_id'] == 4357)
        self.assertEqual(c.match_identity(fields)['confirmed_song_id'], 1358)

    def test_result_sum_is_identity_evidence_only_when_complete_and_missing(self):
        raw = {k:dict(value=n, confidence=.99) for k, n in
               [('perfect', 10), ('cool', 1), ('bad', 0), ('miss', 0), ('completion_status', 'COMPLETED')]}
        selection = dict(fields={})
        self.assertEqual(result_identity_fields(selection, dict(fields=raw))['total_notes']['value'], 11)
        raw['completion_status']['value'] = 'FAILED'
        self.assertNotIn('total_notes', result_identity_fields(selection, dict(fields=raw)))
        selection['fields']['total_notes'] = dict(value=12, confidence=.99)
        self.assertEqual(result_identity_fields(selection, dict(fields=raw))['total_notes']['value'], 12)


if __name__ == '__main__':
    unittest.main()
