"""Result authority, named negative gates and isolated append-only provenance."""
import copy
from contextlib import closing
import sqlite3
import unittest

import test_result_review as fixture
import test_phase5c as auto_fixture
import test_phase5f1 as temporal_fixture
from dance_results.library_service import LibraryError
from dance_results.monitor_tracking import atomic_json
from dance_results.ocr_temporal import issues as temporal_issues
from dance_results.result_consistency import observation, screen_catalog, screen_totals, validate
from dance_results.result_ocr import parse_result, row_cell
from dance_results.verified_auto import automatic_request
from tools.note_conflicts import analyze


class ScreenRuleTests(unittest.TestCase):
    def values(self, **changes):
        return fixture.values(**changes)

    def issues(self, **changes):
        return validate(self.values(**changes))['issues']

    def test_valid_screen_without_displayed_total_needs_no_catalog(self):
        self.assertEqual(self.issues(), [])

    def test_negative_judgment_digit_is_refused(self):
        self.assertIn('automatic_consistency_judgment_integers', self.issues(perfect=-10))

    def test_missing_judgment_is_not_zero(self):
        self.assertIn('automatic_consistency_judgment_integers', self.issues(miss=None))

    def test_boolean_is_not_a_judgment_digit(self):
        self.assertIn('automatic_consistency_judgment_integers', self.issues(cool=True))

    def test_completed_empty_result_refused(self):
        self.assertIn('automatic_consistency_nonempty_completed_result',
                      self.issues(perfect=0, cool=0, max_combo=0))

    def test_one_wrong_digit_in_displayed_total_is_refused(self):
        checked = validate(self.values(), [('result', dict(value=12, confidence=.99))])
        self.assertIn('automatic_consistency_judgment_sum_result', checked['issues'])

    def test_completed_sum_equals_visible_total(self):
        self.assertEqual(validate(self.values(), [('result', dict(value=11, confidence=.99))])['issues'], [])

    def test_unreadable_screen_total_is_refused(self):
        checked = validate(self.values(), [('result', dict(value=11, confidence=.94))])
        self.assertIn('automatic_consistency_screen_total_result', checked['issues'])

    def test_failed_partial_sum_may_be_less_than_visible_total(self):
        checked = validate(self.values(completion_status='FAILED'), [('result', dict(value=12, confidence=.99))])
        self.assertEqual(checked['issues'], [])

    def test_failed_partial_cannot_exceed_visible_total(self):
        checked = validate(self.values(completion_status='FAILED'), [('result', dict(value=10, confidence=.99))])
        self.assertIn('automatic_consistency_judgment_sum_result', checked['issues'])

    def test_combo_one_digit_too_large_is_refused(self):
        self.assertIn('automatic_consistency_combo_bound', self.issues(max_combo=12))

    def test_combo_cannot_include_bad_and_miss(self):
        self.assertIn('automatic_consistency_combo_success_bound', self.issues(perfect=9, bad=1))

    def test_negative_combo_refused(self):
        self.assertIn('automatic_consistency_combo_bound', self.issues(max_combo=-1))

    def test_full_combo_marker_valid(self):
        self.assertEqual(self.issues(result_status='FULL COMBO'), [])

    def test_full_combo_with_single_miss_refused(self):
        self.assertIn('automatic_consistency_special_completion', self.issues(result_status='FULL COMBO', miss=1))

    def test_full_combo_with_short_combo_refused(self):
        self.assertIn('automatic_consistency_special_completion', self.issues(result_status='FULL COMBO', max_combo=10))

    def test_all_perfect_with_single_cool_refused(self):
        self.assertIn('automatic_consistency_special_completion', self.issues(result_status='ALL PERFECT'))

    def test_all_perfect_valid(self):
        self.assertEqual(self.issues(result_status='ALL PERFECT', perfect=11, cool=0), [])

    def test_failed_cannot_have_full_combo_completion_marker(self):
        self.assertIn('automatic_consistency_special_completion',
                      self.issues(result_status='FULL COMBO', completion_status='FAILED'))

    def test_unknown_completion_refused(self):
        self.assertIn('automatic_consistency_completion_status', self.issues(completion_status='UNKNOWN'))

    def test_negative_score_refused(self):
        self.assertIn('automatic_consistency_score_integer', self.issues(game_score=-900))

    def test_unproven_grade_formula_explicitly_skipped(self):
        checked = validate(self.values())
        grade = next(r for r in checked['rules'] if r['rule'] == 'grade_formula')
        self.assertEqual((grade['status'], grade['mandatory']), ('skipped', False))

    def test_catalog_filled_result_and_selection_are_never_screen_totals(self):
        item = dict(value=999, confidence=1, method='official_catalog_fill')
        self.assertEqual(screen_totals(dict(total_notes=item), dict(fields=dict(total_notes=item))), [])

    def test_original_selection_wins_over_enrichment(self):
        self.assertEqual(screen_totals({}, dict(
            fields=dict(total_notes=dict(value=999, confidence=1)),
            ocr_fields=dict(total_notes=dict(value=None, confidence=0)))), [])

    def test_catalog_provenance_requires_screen_origin_not_verified_flag(self):
        for source in ('official_csv', 'manual', 'imported', None):
            self.assertFalse(screen_catalog(dict(notes_source=source, notes_verified=1, total_notes=11)))
        self.assertTrue(screen_catalog(dict(notes_source='game_screen', total_notes=11, notes_verified=1)))
        self.assertFalse(screen_catalog(dict(notes_source='game_screen', total_notes=11, notes_verified=0)))

    def test_failed_partial_cannot_be_persisted_as_chart_observation(self):
        self.assertIsNone(observation(self.values(completion_status='FAILED')))
        self.assertEqual(observation(self.values())['provenance'], 'screen_observed')

    def test_right_edge_digit_is_read_without_accuracy_leakage(self):
        tokens = [fixture.token('2', 746.5, 250), fixture.token('99.47 %', 815, 250)]
        self.assertEqual(row_cell(tokens, 671, 746, 250)['value'], 2)
        self.assertIsNone(row_cell([tokens[1]], 671, 746, 250)['value'])
        self.assertIsNone(row_cell([fixture.token('9', 524, 250)], 425, 523, 250)['value'])

    def test_full_combo_badge_has_local_row_ownership(self):
        ocr = parse_result(fixture.result_tokens()+[fixture.token('ALL PERFECT', 60, 250)])
        self.assertEqual(ocr['fields']['result_status']['value'], 'ALL PERFECT')
        ocr = parse_result(fixture.result_tokens()+[fixture.token('ALL PERFECT', 60, 318)])
        self.assertNotIn('result_status', ocr['fields'])


class ResultAuthorityTests(unittest.TestCase):
    setUp = auto_fixture.Phase5CTests.setUp
    tearDown = auto_fixture.Phase5CTests.tearDown
    official_auto = auto_fixture.Phase5CTests.official_auto
    temporal_row = temporal_fixture.AutomaticIntegrationTests.temporal_row
    store = auto_fixture.Phase5CTests.store

    def chart(self, **changes):
        with closing(sqlite3.connect(self.db)) as db:
            names = ','.join(key+'=?' for key in changes)
            db.execute('UPDATE game_difficulties SET '+names+' WHERE song_id=? AND difficulty=?',
                       (*changes.values(), self.session['song_id'], 'HARD'))
            db.commit()

    def persist(self, row):
        auto_fixture.attach_auto_frames(self, row)
        return row

    def submit(self):
        store = self.store()
        store.auto_allowed = lambda sid: True
        return store.submit_verified(self.sid)['record']

    def test_csv_mismatch_saves_screen_observation_and_keeps_catalog(self):
        row = self.temporal_row()
        self.chart(total_notes=13)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        record = self.submit()
        self.assertEqual(record['evidence']['screen_note_observation']['total_notes'], 11)
        self.assertEqual(record['evidence']['screen_note_observation']['provenance'], 'screen_observed')
        self.assertEqual(record['evidence']['catalog_note_disagreement']['catalog_value'], 13)
        self.assertEqual(record['evidence']['full_notes_source'], 'screen_observed')
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT total_notes,notes_source FROM game_difficulties WHERE song_id=? AND difficulty=?',
                                        (self.session['song_id'], 'HARD')).fetchone(), (13, 'official_csv'))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 11)

    def test_manual_catalog_mismatch_does_not_block_screen(self):
        row = self.temporal_row()
        self.chart(total_notes=13, notes_source='manual')
        self.assertEqual(automatic_request(self.service, row)[1], [])
        self.assertEqual(self.submit()['confirmation_method'], 'verified_auto')

    def test_unverified_catalog_notes_do_not_gate_auto(self):
        row = self.temporal_row()
        self.chart(total_notes=None, notes_source=None, notes_verified=0)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        self.assertEqual(self.submit()['evidence']['screen_note_observation']['total_notes'], 11)

    def test_confirmed_screen_catalog_mismatch_keeps_pending(self):
        row = self.temporal_row()
        self.chart(total_notes=13, notes_source='game_screen')
        self.assertIn('automatic_screen_catalog_notes_conflict', automatic_request(self.service, row)[1])
        with self.assertRaisesRegex(LibraryError, 'screen_catalog_notes_conflict'):
            self.submit()
        self.assertEqual(self.service._read(self.sid)[1]['status'], 'pending_review')
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM play_records').fetchone()[0], 0)

    def test_result_judgments_conflict_with_screen_catalog_even_without_selection_total(self):
        row = self.temporal_row()
        row['locked_session']['selection']['fields']['total_notes']['value'] = None
        self.session = copy.deepcopy(row['locked_session'])
        self.persist(row)
        self.chart(total_notes=13, notes_source='game_screen')
        self.assertIn('automatic_screen_catalog_notes_conflict', automatic_request(self.service, row)[1])

    def test_wrong_digit_in_screen_sum_refuses_auto_with_matching_temporal_frames(self):
        row = self.temporal_row()
        row['ocr']['fields']['perfect']['value'] = 11
        row['ocr']['player_rows'][0]['fields']['perfect']['value'] = 11
        self.persist(row)
        self.assertEqual(temporal_issues(row['ocr'], row['locked_session'], self.output/'sessions'/self.sid), [])
        self.assertIn('automatic_consistency_judgment_sum_selection', automatic_request(self.service, row)[1])
        with self.assertRaisesRegex(LibraryError, 'judgment_sum_selection'):
            self.submit()

    def test_wrong_combo_digit_refuses_auto(self):
        row = self.temporal_row()
        row['ocr']['fields']['max_combo']['value'] = 12
        row['ocr']['player_rows'][0]['fields']['max_combo']['value'] = 12
        self.persist(row)
        self.assertIn('automatic_consistency_combo_bound', automatic_request(self.service, row)[1])
        with self.assertRaisesRegex(LibraryError, 'combo_bound'):
            self.submit()

    def test_catalog_fill_does_not_fake_a_screen_total(self):
        row = self.temporal_row()
        item = row['locked_session']['selection']['fields']['total_notes']
        item.update(value=13, method='official_catalog_fill')
        self.session = copy.deepcopy(row['locked_session'])
        self.persist(row)
        self.chart(total_notes=13)
        self.assertEqual(automatic_request(self.service, row)[1], [])
        self.assertEqual(self.submit()['evidence']['screen_note_observation']['total_notes'], 11)

    def test_unbound_song_stays_pending_review(self):
        row = self.temporal_row()
        row['ocr']['fields']['title']['value'] = 'Missing Song'
        self.session['song_id'] = None
        self.session['selection']['fields']['title']['value'] = 'Missing Song'
        self.persist(row)
        self.assertIn('automatic_unique_verified_identity_required', automatic_request(self.service, row)[1])
        with self.assertRaises(LibraryError):
            self.submit()
        self.assertEqual(self.service._read(self.sid)[1]['status'], 'pending_review')

    def test_legacy_payload_without_two_frames_cannot_auto_save(self):
        row = self.temporal_row()
        row['ocr'].pop('temporal_validation')
        row['ocr'].pop('parser_version', None)
        self.assertIn('automatic_temporal_confirmation_required', automatic_request(self.service, row)[1])

    def test_optional_visible_total_must_match_across_two_frames(self):
        row = self.temporal_row()
        row['ocr']['fields']['total_notes'] = dict(value=11, confidence=.99)
        self.persist(row)
        row['ocr']['temporal_validation']['samples'][0]['fields']['total_notes']['value'] = 12
        self.assertIn('automatic_temporal_field_conflict', automatic_request(self.service, row)[1])

    def test_disappearing_optional_badge_does_not_confirm_two_identical_results(self):
        row = self.temporal_row()
        row['ocr']['temporal_validation']['samples'][0]['fields']['result_status'] = dict(
            value='FULL COMBO', confidence=.99)
        self.assertIn('automatic_temporal_field_conflict', automatic_request(self.service, row)[1])

    def test_every_mandatory_screen_rule_refuses_automatic_request(self):
        cases = [
            (dict(perfect=-1), 'judgment_integers'),
            (dict(perfect=0, cool=0, max_combo=0), 'nonempty_completed_result'),
            (dict(completion_status='UNKNOWN'), 'completion_status'),
            (dict(perfect=9, bad=1), 'combo_success_bound'),
            (dict(game_score=-1), 'score_integer'),
            (dict(result_status='ALL PERFECT'), 'special_completion'),
        ]
        for changes, rule in cases:
            with self.subTest(rule=rule):
                row = self.temporal_row()
                for key, value in changes.items():
                    row['ocr']['fields'][key] = dict(value=value, confidence=.99)
                    if key in row['ocr']['player_rows'][0]['fields']:
                        row['ocr']['player_rows'][0]['fields'][key] = dict(value=value, confidence=.99)
                self.persist(row)
                body, problems = automatic_request(self.service, row)
                self.assertIsNone(body)
                self.assertIn('automatic_consistency_'+rule, problems)

    def test_confirmed_play_conflict_blocks_without_catalog_authority(self):
        self.temporal_row()
        self.submit()
        with closing(sqlite3.connect(self.db)) as db:
            result = analyze(self.db, self.output/'sessions')
            self.assertTrue(result['database_unchanged'])
            self.assertEqual(result['conflicting_charts'], 0)
        row = self.temporal_row()
        self.sid = 'different-session'
        # This fixture represents a new unsubmitted play, independent of the
        # prior committed review (whose sidecar now correctly says saved).
        row.update(status='pending_review', writes_play_record=False)
        folder = self.output/'sessions'/self.sid
        folder.mkdir()
        import shutil
        shutil.copyfile(self.output/'sessions'/row['session_id']/'result.png', folder/'result.png')
        row['session_id'] = self.sid
        self.session['session_id'] = self.sid
        self.session['selection']['fields']['total_notes']['value'] = 12
        row['ocr']['fields']['perfect']['value'] = 11
        row['ocr']['player_rows'][0]['fields']['perfect']['value'] = 11
        self.persist(row)
        self.assertIn('automatic_cross_session_basis_conflict', automatic_request(self.service, row)[1])

    def test_conflict_scanner_counts_unique_confirmed_screen_and_keeps_hash(self):
        self.temporal_row()
        self.chart(total_notes=13)
        self.submit()
        result = analyze(self.db, self.output/'sessions')
        self.assertTrue(result['database_unchanged'])
        self.assertEqual(result['conflicting_charts'], 1)
        self.assertEqual(result['examples'][0]['screen_value'], 11)
        self.assertEqual(result['examples'][0]['csv_value'], 13)
