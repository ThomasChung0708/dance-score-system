"""Real scratch submissions, mutable official charts and session source choices."""
import copy
from contextlib import closing
import sqlite3
import unittest

from dance_results.library_service import LibraryError
from dance_results.monitor_tracking import atomic_json
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.player_settings import PlayerSettings
from dance_results.source_choice import offer
from dance_results.verified_auto import automatic_request
import test_phase5c as auto_fixture
import test_phase5f1 as temporal_fixture


class FollowupTests(unittest.TestCase):
    setUp = auto_fixture.Phase5CTests.setUp
    tearDown = auto_fixture.Phase5CTests.tearDown
    official_auto = auto_fixture.Phase5CTests.official_auto
    temporal_row = temporal_fixture.AutomaticIntegrationTests.temporal_row
    store = auto_fixture.Phase5CTests.store

    def persist(self, row):
        auto_fixture.attach_auto_frames(self, row)
        return row

    def submit(self):
        store = self.store()
        store.auto_allowed = lambda sid: True
        return store.submit_verified(self.sid)['record']

    def prompt(self):
        return self.service.detail(self.sid)['source_prompt']

    def choice(self, **changes):
        return dict(session_id=self.sid, review_id=self.row['review_id'], revision=0, **changes)

    def ambiguous(self, chart=True, custom_only=False):
        self.service.scoring_policy = 'custom-judged-v1'
        row = self.temporal_row()
        self.official_id = self.session['song_id']
        with closing(sqlite3.connect(self.db)) as db:
            ids = (1, 2) if custom_only else (1,)
            for sid in ids:
                db.execute("INSERT INTO song_title_aliases(song_id,display_title,confirmed,source) VALUES(?,'Auto Official',1,'game_screen')", (sid,))
                if chart:
                    db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(?,'HARD',17,'game_screen',11,'game_screen',1)", (sid,))
            if custom_only:
                db.execute('DELETE FROM song_title_aliases WHERE song_id=?', (self.official_id,))
                db.execute("UPDATE songs SET saved_title='Other official',latest_title='Other official' WHERE song_id=?", (self.official_id,))
                db.execute("UPDATE song_sources SET saved_title='Other official' WHERE song_id=?", (self.official_id,))
            db.commit()
        self.session.update(song_id=None, matching_status='pending_manual_review')
        self.session['selection'].update(source_type='unknown', pack=None)
        row['draft']['song_id'] = None
        self.row = self.persist(row)
        return row

    def test_changed_official_notes_save_result_and_warning_without_catalog_write(self):
        self.service.scoring_policy = 'custom-judged-v1'
        row = self.temporal_row()
        self.session['selection']['fields']['total_notes'].update(value=12, confidence=.944)
        self.persist(row)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE game_difficulties SET total_notes=13,notes_source='game_screen' WHERE song_id=?", (self.session['song_id'],))
            db.commit()
        self.assertEqual(automatic_request(self.service, row)[1], [])
        record = self.submit()
        self.assertEqual((record['total_notes'], record['notes_source']), (11, 'result_screen_judged_sum'))
        warning = next(w for w in record['warnings'] if w['code'] == 'CHART_NOTES_CHANGED')
        self.assertEqual((warning['catalog_notes'], warning['selection_notes'], warning['observed_notes'], warning['catalog_difference']), (13, 12, 11, -2))
        self.assertNotIn('CHART_NOTES_CHANGED', [i['code'] for i in self.service.detail(self.sid)['preview']['issues']])
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT total_notes FROM game_difficulties WHERE song_id=?', (self.session['song_id'],)).fetchone()[0], 13)

    def test_official_notes_warning_does_not_rescue_result_frame_disagreement(self):
        row = self.temporal_row()
        row['ocr']['temporal_validation']['samples'][0]['fields']['perfect']['value'] = 9
        self.assertIn('automatic_temporal_field_conflict', automatic_request(self.service, row)[1])

    def test_strict_two_result_titles_corroborate_slightly_low_selection_only(self):
        row = self.temporal_row()
        self.session['selection']['fields']['title']['confidence'] = .9495
        self.persist(row)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        row['ocr']['temporal_validation']['samples'][0]['fields']['title']['confidence'] = .949
        self.assertIn('automatic_selection_title_required', automatic_request(self.service, row)[1])

    def test_ambiguous_both_sources_prompt_official_choice_submits_normally(self):
        self.ambiguous()
        self.assertEqual(self.prompt()['kind'], 'source')
        choice = self.choice(source_type='official')
        self.service.choose_source(choice)
        record = self.submit()
        self.assertEqual(record['song_id'], self.official_id)
        self.assertEqual(record['confirmation_method'], 'verified_auto')
        self.assertTrue(self.service.choose_source(choice)['idempotent'])

    def test_custom_pack_choice_submits_using_same_service(self):
        self.ambiguous()
        self.service.choose_source(self.choice(source_type='custom', song_id=1))
        self.assertEqual(self.submit()['song_id'], 1)

    def test_choice_still_requires_two_frames(self):
        row = self.ambiguous()
        row['ocr']['temporal_validation']['samples'].pop()
        atomic_json(self.output/'sessions'/self.sid/'result-review.json', row)
        self.service.choose_source(self.choice(source_type='official'))
        with self.assertRaisesRegex(LibraryError, 'two_result_frames'):
            self.submit()
        self.assertEqual(self.service._read(self.sid)[1]['draft']['song_id'], self.official_id)

    def test_dismiss_stays_pending_and_review_keeps_choice(self):
        self.ambiguous()
        self.service.choose_source(self.choice(dismiss=True))
        self.assertTrue(self.prompt()['dismissed'])
        self.assertEqual(self.service.source_prompts(), dict(prompts=[]))
        self.assertEqual(self.service._read(self.sid)[1]['status'], 'pending_review')

    def test_confident_source_or_pack_single_source_and_rule_resolved_no_prompt(self):
        self.ambiguous(chart=False)
        self.assertIsNone(self.prompt())
        row = self.service._read(self.sid)[1]
        catalog = ReadOnlyCatalog(self.db)
        try:
            for source, pack in [('official', None), ('custom', dict(status='confirmed', pack_id=1))]:
                row['locked_session']['selection'].update(source_type=source, pack=pack)
                self.assertIsNone(offer(catalog, row))
        finally:
            catalog.close()

    def test_custom_only_multiple_folders_list_and_choice(self):
        self.ambiguous(chart=False, custom_only=True)
        prompt = self.prompt()
        self.assertEqual(prompt['kind'], 'folder')
        self.assertEqual({c['song_id'] for c in prompt['candidates']}, {1, 2})
        self.service.choose_source(self.choice(source_type='custom', song_id=2))
        self.assertEqual(self.submit()['song_id'], 2)

    def test_single_custom_copy_needs_no_prompt(self):
        row = self.ambiguous(chart=False, custom_only=True)
        catalog = ReadOnlyCatalog(self.db)
        try:
            catalog.index['auto official'].discard(2)
            self.assertIsNone(offer(catalog, row))
        finally:
            catalog.close()

    def test_custom_folder_choice_uncharted_non_fc_still_pending(self):
        row = self.ambiguous(chart=False, custom_only=True)
        row['ocr']['fields']['max_combo']['value'] = 9
        row['ocr']['player_rows'][0]['fields']['max_combo']['value'] = 9
        self.persist(row)
        self.service.choose_source(self.choice(source_type='custom', song_id=1))
        with self.assertRaisesRegex(LibraryError, 'uncharted_complete_combo_required'):
            self.submit()

    def test_exact_one_custom_chart_resolves_multiple_folders(self):
        self.ambiguous(chart=True, custom_only=True)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('UPDATE game_difficulties SET game_level=18 WHERE song_id=2')
            db.commit()
        self.assertIsNone(self.prompt())

    def test_stale_or_invalid_choice_cannot_change_binding(self):
        self.ambiguous()
        for changes in (dict(revision=1, source_type='official'), dict(source_type='custom', song_id=9999)):
            body = self.choice()
            body.update(changes)
            with self.assertRaises(LibraryError):
                self.service.choose_source(body)
        self.assertNotIn('source_choice', self.service._read(self.sid)[1])

    def test_player_setting_first_run_persisted_and_shared(self):
        settings = self.service.player_settings
        self.assertEqual(settings.get(), 'TC')
        settings.set(' Friend☆ ')
        self.assertEqual(PlayerSettings(settings.path).get(), 'Friend☆')
        self.assertEqual(settings.path.parent, self.db.parent)
        with self.assertRaises(LibraryError):
            settings.set(' ')
