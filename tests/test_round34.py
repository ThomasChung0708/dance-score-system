"""Japanese retrieval, bounded preview tolerance and current result-only scope."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from dance_results.database import LibraryDatabase
from dance_results.monitor import Monitor
from dance_results.monitor_tracking import Tracker
from dance_results.ocr_matching import ReadOnlyCatalog, probably_same_title, same_song_title_key
from dance_results.result_title_binding import bind_result_title, context_selection
from dance_results.scanner import scan_library
from dance_results.source_choice import offer
from test_monitor import selected, scene
from test_ocr_static import observation


FIXTURE = json.loads((Path(__file__).parent/'fixtures/round34/catalog.json').read_text(encoding='utf8'))


def catalog():
    result = ReadOnlyCatalog.__new__(ReadOnlyCatalog)
    result.songs = {s['song_id']: copy.deepcopy(s) for s in FIXTURE['songs']}
    result.index = {}
    result.retrieval_index = {}
    result.difficulties = {}
    result.official_sources = {}
    result.reviews = set()
    for source in FIXTURE['sources']:
        result._index(source['saved_title'], source['song_id'])
    return result


def session(age=20, pack_id=34):
    start = '2026-11-16T10:16:07+00:00'
    return dict(session_id='round34', epoch=1, started_at=start, selection=None,
        identity_context=dict(epoch=1, captured_at=start, pack=dict(pack_id=pack_id, status='confirmed'),
            pack_context_current=True,
            source_type='custom', pack_age_seconds=age, source_age_seconds=None, max_age_seconds=60))


def result(title='揺れる恋は波の如く', level=18):
    fields = observation(title=title, game_level=level, difficulty=None, total_notes=None)
    return dict(fields=fields, multiplayer=False,
        temporal_validation=dict(status='confirmed', samples=[dict(fields=copy.deepcopy(fields)) for _ in range(2)]))


class PreviewTests(unittest.TestCase):
    def test_japari_visual_typo_and_one_long_title_edit(self):
        self.assertTrue(probably_same_title('ようこそジャパリパークへ', 'ようニそジャパリパークへ')['same'])
        self.assertTrue(probably_same_title('abcdef', 'abcxef')['same'])
        self.assertFalse(probably_same_title('abcdef', 'abcxyf')['same'])

    def test_short_titles_and_different_titles_do_not_overmatch(self):
        for a, b in [('ABC', 'ABD'), ('Prayer', 'Qubism'), ('', ''), ('abcde', 'abcdef')]:
            self.assertFalse(probably_same_title(a, b)['same'])

    def test_confusables_are_never_retrieval_aliases(self):
        c = catalog()
        self.assertEqual(c.title_ids('ようニそジャパリパークへ'), set())
        self.assertNotEqual(same_song_title_key('カーヘ'), same_song_title_key('力-へ'))

    def test_unique_different_catalog_song_overrides_one_edit(self):
        c = catalog()
        c.songs[9000] = dict(c.songs[679], song_id=9000)
        c._index('ようニそジャパリパークへ', 9000)
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            s = selected('ようこそジャパリパークへ')
            s.update(pack=dict(pack_id=18), matching=dict(confirmed_song_id=679))
            self.assertFalse(t.playing_title_agrees(s, 'ようニそジャパリパークへ', c))
            self.assertEqual(t.events[-1]['method'], 'different_unique_catalog_song')

    def test_japari_survives_recovery_and_tracker_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.current_pack = dict(pack_id=18, status='confirmed')
            s = selected('ようこそジャパリパークへ', level=23)
            s['matching'] = dict(confirmed_song_id=679)
            t.update_selection(s, 1)
            t.update_selection(s, 2)
            m = object.__new__(Monitor)
            m.tracker = t
            frames = Mock()
            frames.before.return_value = []
            p = scene('PLAYING', playing_preview=dict(title='ようニそジャパリパークへ'))
            m._recover_fast_selection(Mock(), frames, p, 3, catalog())
            for now in (3, 4):
                t.consume(copy.deepcopy(p), now=now, sample_id=now)
            self.assertEqual(t.session['song_id'], 679)
            self.assertTrue(any(e['event'] == 'playing_title_comparison' for e in t.events))


class RetrievalTests(unittest.TestCase):
    def test_kyato_space_and_all_tildes_retrieve_1249(self):
        c = catalog()
        for tilde in ('~', '～', '〜', '〰', '∼'):
            f = observation(title='キャトられ恋はモ'+tilde+'モク', game_level=31)
            self.assertEqual(c.match_identity(f, 34, 'custom').get('confirmed_song_id'), 1249)

    def test_spacing_collision_keeps_both_candidates(self):
        c = catalog()
        c.songs[9000] = dict(c.songs[1249], song_id=9000)
        c._index('キャトられ恋はモ~モク', 9000)
        match = c.match_identity(observation(title='キャトられ 恋はモ～モク', game_level=31), 34, 'custom')
        self.assertEqual(match['candidate_count'], 2)
        self.assertIsNone(match.get('confirmed_song_id'))

    def test_dwi_subtitle_is_stripped_only_from_explicit_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'SONG'
            song = root/'Pack'/'Folder'
            song.mkdir(parents=True)
            (song/'song.sm').write_text('#TITLE:本当の 歌名;#SUBTITLE:Extra credit;', encoding='utf8')
            (song/'song.dwi').write_text('#TITLE:別の歌名 Extra credit;', encoding='utf8')
            db = Path(temp)/'catalog.sqlite3'
            with LibraryDatabase(db) as library:
                library.sync(scan_library(root))
            c = ReadOnlyCatalog(db)
            try:
                self.assertEqual(len(c.title_ids('本当の歌名')), 1)
                self.assertEqual(len(c.title_ids('別の歌名')), 1)
                self.assertEqual(c.title_ids('Extra credit'), set())
            finally:
                c.close()


class ResultScopeTests(unittest.TestCase):
    def test_yureru_unique_in_fresh_pack_and_jack_without_selection(self):
        c = catalog()
        for title, level, sid, pack in [('揺れる恋は波の如く', 18, 1240, 34), ("Jack o' Lantern", 37, 662, 18)]:
            recovered = bind_result_title(c, session(pack_id=pack), result(title, level), {})
            self.assertEqual(recovered['matching']['confirmed_song_id'], sid)
            self.assertEqual(recovered['result_title_binding']['method'], 'result_title_in_confirmed_pack')

    def test_without_pack_namesakes_stay_ambiguous_and_prompt(self):
        c = catalog()
        s = session()
        s['identity_context'].update(pack=None, source_age_seconds=10)
        self.assertIsNone(bind_result_title(c, s, result(), {}))
        row = dict(locked_session=s, ocr=result(), status='pending_review', revision=0,
                   session_id='round34', review_id='review')
        self.assertEqual(offer(c, row)['kind'], 'folder')

    def test_revoked_wrong_epoch_and_unfrozen_scope_are_rejected(self):
        c = catalog()
        for change in ('revoked', 'legacy', 'epoch', 'timestamp', 'absent'):
            s = session()
            if change == 'revoked':
                s['identity_context']['pack_context_current'] = False
            elif change == 'legacy':
                s['identity_context'].pop('pack_context_current')
            elif change == 'epoch':
                s['identity_context']['epoch'] = 0
            elif change == 'timestamp':
                s['identity_context']['captured_at'] = 'other-session'
            else:
                s.pop('identity_context')
            self.assertEqual(context_selection(s), {})
            self.assertIsNone(bind_result_title(c, s, result(), dict(pack=dict(pack_id=34, status='confirmed'), source_type='custom')))

    def test_one_frame_low_confidence_and_multiplayer_host_level_do_not_bind(self):
        c = catalog()
        for change in ('frame', 'confidence', 'multiplayer'):
            o = result()
            if change == 'frame':
                o['temporal_validation']['samples'].pop()
            elif change == 'confidence':
                o['fields']['title']['confidence'] = .89
            else:
                o['multiplayer'] = True
            self.assertIsNone(bind_result_title(c, session(), o, {}))

    def test_existing_chart_must_agree_with_result_lv(self):
        c = catalog()
        c.difficulties[(1240, 'HARD')] = dict(difficulty_id=4, difficulty='HARD', game_level=18,
            level_source='manual', total_notes=None, notes_source=None, notes_verified=0)
        self.assertIsNone(bind_result_title(c, session(), result(level=19), {}))
        self.assertEqual(bind_result_title(c, session(), result(), {})['matching']['confirmed_song_id'], 1240)

    def test_anchored_partner_uses_original_strong_title_sample(self):
        o = result()
        o['fields']['title'].update(confidence=.91, reason='low_confidence')
        o['temporal_validation']['samples'][1]['fields']['title'] = copy.deepcopy(o['fields']['title'])
        recovered = bind_result_title(catalog(), session(), o, {})
        self.assertEqual(recovered['matching']['confirmed_song_id'], 1240)
        self.assertEqual(recovered['fields']['title']['confidence'], .99)
        self.assertEqual(o['fields']['title']['confidence'], .91)

    def test_current_pack_binds_after_long_idle_and_missing_pack_frames(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.pack_observation(dict(pack_id=34, status='confirmed'), inferred=True, now=1)
            for now in (62, 601, 3601):
                t.pack_observation(None, now=now)
            for now in (3601, 3602):
                t.consume(scene('PLAYING'), now=now, sample_id=now)
            self.assertIsNone(t.session['selection'])
            self.assertEqual(t.session['identity_context']['pack_age_seconds'], 3600)
            recovered = bind_result_title(catalog(), t.session, result(), {})
            self.assertEqual(recovered['matching']['confirmed_song_id'], 1240)
            self.assertEqual(sum(e['event'] == 'pack_observed' for e in t.events), 1)

    def test_room_preview_and_role_changes_clear_selection_but_keep_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.pack_observation(dict(pack_id=34, status='confirmed'), inferred=True, now=1)
            for now in (2, 3):
                t.consume(selected('Old song'), now=now, sample_id=now)
            t.last_room_role = True
            for now in (600, 601):
                t.consume(scene('WAITING_ROOM', room_preview=dict(title='New song'),
                    context=dict(local_is_host=False)), now=now, sample_id=now)
                self.assertIsNone(t.selection)
                self.assertIsNone(t.selection_candidate)
                self.assertEqual(t.current_pack['pack_id'], 34)
            for now in (610, 611):
                t.consume(scene('PLAYING'), now=now, sample_id=now)
            self.assertEqual(bind_result_title(catalog(), t.session, result(), {})[
                'matching']['confirmed_song_id'], 1240)

    def test_context_changes_revoke_pack_before_result_title_can_bind(self):
        for change in ('LOBBY', 'LOGIN', 'window', 'category', 'uncertain', 'pack'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                t = Tracker(temp)
                t.pack_observation(dict(pack_id=34, status='confirmed'), inferred=True, now=1)
                if change in ('LOBBY', 'LOGIN'):
                    for now in (100, 101):
                        t.consume(scene(change), now=now, sample_id=now)
                elif change == 'window':
                    t.new_window('other-window')
                elif change == 'category':
                    t.category_observation('all', confidence=.99, now=100)
                elif change == 'uncertain':
                    t.pack_observation(None, uncertain=True, now=100)
                else:
                    t.pack_observation(dict(pack_id=18, status='confirmed'), inferred=True, now=100)
                for now in (110, 111):
                    t.consume(scene('PLAYING'), now=now, sample_id=now)
                self.assertIsNone(bind_result_title(catalog(), t.session, result(), {}))

    def test_guest_without_confirmed_pack_still_revokes_source(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            for now in (1, 2):
                t.category_observation('all', confidence=.99, now=now)
            t.invalidate_selection('multiplayer_guest_source_not_confirmed')
            self.assertEqual(t.current_source, 'unknown')
            self.assertIsNone(t.current_pack)


class DebounceTests(unittest.TestCase):
    def test_spacing_tilde_flicker_confirms_without_resetting(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.update_selection(selected('キャトられ 恋はモ～モク'), 1)
            t.update_selection(selected('キャトられ恋はモ〜モク'), 2)
            self.assertIsNotNone(t.selection)
            self.assertEqual(t.selection_count, 2)
            self.assertEqual(t.events[0]['title_key'], 'キャトられ恋はモ~モク')
            t.update_selection(selected('キャトられ恋はモ〜モク', level=18), 3)
            self.assertIsNone(t.selection)

    def test_single_handoff_16_seconds_but_not_after_context_change_or_ttl(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Tracker(temp)
            t.pack_observation(dict(pack_id=34, status='confirmed'), inferred=True, now=0)
            t.state = 'SONG_SELECT'
            t.update_selection(selected(), 1)
            self.assertIsNotNone(t.fast_selection(17))
            self.assertIsNone(t.fast_selection(62))
            t.pack_observation(dict(pack_id=18, status='confirmed'), inferred=True, now=18)
            self.assertIsNone(t.fast_selection(19))
            t.update_selection(selected(), 20)
            t.new_window('another-window')
            self.assertIsNone(t.fast_selection(21))
