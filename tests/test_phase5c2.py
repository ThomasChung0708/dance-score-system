"""Actual custom policy on isolated SQLite, immutable history and range conflicts."""
from contextlib import closing
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_result_review as fixture
import test_submissions as submit_fixture
import test_phase5c as auto_fixture
from dance_results.scoring import calculate
from dance_results.monitor_tracking import atomic_json
from dance_results.sqlite_safety import audit
from dance_results.score_comparison import comparison
from dance_results.migrations_v9 import migrate_v8
from dance_results.library_service import LibraryError


class CustomMaxTests(unittest.TestCase):
    tearDown=fixture.ReviewPersistenceTests.tearDown
    body=fixture.ReviewPersistenceTests.body
    store=submit_fixture.SubmissionTests.store
    commit=submit_fixture.SubmissionTests.commit
    clone_session=submit_fixture.SubmissionTests.clone_session
    auto_store=auto_fixture.Phase5CTests.auto_store

    def setUp(self):
        fixture.ReviewPersistenceTests.setUp(self)
        self.service.scoring_policy='custom-judged-v1'
        self.base_selection=copy.deepcopy(self.session['selection'])
        self.session['selection']=None
        self.sync_session()

    def official_auto(self):
        self.session['selection']=copy.deepcopy(self.base_selection)
        return auto_fixture.Phase5CTests.official_auto(self)

    def sync_session(self):
        folder,row=self.service._read(self.sid)
        row['locked_session']=copy.deepcopy(self.session)
        atomic_json(folder/'session.json',self.session);atomic_json(folder/'result-review.json',row)

    def approved(self,**values):
        b=self.body(max_basis={},values=fixture.values(**values))
        if self.session.get('song_id')!=1:
            with self.store().library.open() as lib:
                song=lib.connection.execute('SELECT * FROM songs WHERE song_id=?',(self.session['song_id'],)).fetchone()
                b.update(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'])
        saved=self.service.save(b);b.update(revision=saved['revision'],play_mode='single')
        return b

    def test_1015_notes_2020_ex_custom_max_2030_minus10(self):
        r=self.commit(self.approved(perfect=1005,cool=10,bad=0,miss=0,max_combo=1015,total_notes=1015))['record']
        self.assertEqual((r['ex_score'],r['max_score'],r['max_difference'],r['display']),(2020,2030,10,'MAX -10'))
        self.assertEqual(r['lamp_status'],'AC');self.assertEqual(r['max_policy'],'custom-judged-v1')
        self.assertEqual(r['max_basis_notes'],1015)
        self.assertFalse(r['evidence']['max_basis_verification']['is_official_game_max'])

    def test_heart_manual_submission_needs_no_max_proof_or_exception(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(1,'HARD',29,'manual',1194,'manual',1)");db.commit()
        r=self.commit(self.approved(perfect=1145,cool=30,bad=7,miss=10,max_combo=543,game_level=29,total_notes=1194))['record']
        self.assertEqual((r['judged_total'],r['ex_score'],r['max_score'],r['display']),(1192,2320,2384,'MAX -64'))
        self.assertEqual(r['catalog_total_notes'],1194);self.assertEqual(r['confirmation_method'],'manual_review')
        self.assertFalse(r['note_exception']['authorized'])
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(db.execute('SELECT total_notes FROM game_difficulties').fetchone()[0],1194)

    def test_failed_partial_count_never_becomes_max(self):
        r=self.commit(self.approved(perfect=2,cool=1,bad=0,miss=2,max_combo=3,total_notes=None,completion_status='FAILED'))['record']
        self.assertEqual((r['ex_score'],r['lamp_status']),(5,'FAILED'));self.assertIsNone(r['max_score'])
        self.assertIsNone(r['max_basis_notes']);self.assertIsNone(r['percentage'])

    def test_custom_acp_ac_clear_and_percentage_boundary(self):
        for values,lamp in [(dict(perfect=11,cool=0,bad=0,miss=0),'ACP'),(dict(perfect=10,cool=1,bad=0,miss=0),'AC'),(dict(perfect=8,cool=1,bad=1,miss=1),'CLEAR')]:
            r=calculate(dict(values,completion_status='COMPLETED'),identity_confirmed=True)
            self.assertEqual(r['lamp_preview'],lamp);self.assertEqual(r['max_score'],22)
        a=calculate(dict(perfect=125,cool=0,bad=75,miss=0,completion_status='COMPLETED'),identity_confirmed=True)
        b=calculate(dict(perfect=124,cool=1,bad=75,miss=0,completion_status='COMPLETED'),identity_confirmed=True)
        self.assertEqual((a['display'],b['display']),('MAX -150','62.25%'))

    def test_same_chart_different_counts_preserves_records_and_freezes_bests(self):
        first=self.commit(self.approved(perfect=1005,cool=10,max_combo=1015,total_notes=1015))['record']
        first_bytes=json.dumps(first['evidence'],sort_keys=True)
        self.clone_session();self.session['selection']=None;self.sync_session()
        b=self.approved(perfect=1013,cool=0,max_combo=1013,total_notes=1015)
        p=self.store().prepare(b);self.assertTrue(p['can_submit'])
        self.assertTrue(p['summary']['score']['max_score']==2026)
        r=self.commit(b)['record']
        self.assertIsNone(r['lamp_status']);self.assertTrue(r['comparison']['ranking_paused'])
        self.assertEqual(r['achievement_feedback']['events'],['SAVED'])
        self.assertEqual(self.store().detail(first['record_id'])['max_score'],2030)
        self.assertEqual(json.dumps(self.store().detail(first['record_id'])['evidence'],sort_keys=True),first_bytes)
        with self.store().library.open() as library:
            row=library.connection.execute('SELECT highest_ex_score,best_clear_lamp FROM difficulty_bests').fetchone()
            self.assertEqual(tuple(row),(2020,'AC'))

    def test_conflict_cannot_be_evaded_by_returning_to_first_count(self):
        self.commit(self.approved())
        self.clone_session();self.session['selection']=None;self.sync_session()
        self.commit(self.approved(perfect=12,cool=0,max_combo=12,total_notes=11))
        self.clone_session();self.session['selection']=None;self.sync_session()
        r=self.commit(self.approved(perfect=11,cool=0,max_combo=11))['record']
        self.assertTrue(r['comparison']['ranking_paused']);self.assertIsNone(r['lamp_status'])
        self.assertFalse(r['achievement_feedback']['new_score']);self.assertFalse(r['achievement_feedback']['new_lamp'])

    def test_low_confidence_remains_pending_until_manual_confirmation(self):
        folder,row=self.service._read(self.sid);row['ocr']['fields']['cool']['confidence']=.4
        atomic_json(folder/'result-review.json',row)
        b=self.body(max_basis={});p=self.service.preview(b)
        self.assertIn('cool',p['ocr_fields_requiring_review']);self.assertIsNone(p['lamp_preview'])
        r=self.commit(self.approved())['record'];self.assertEqual(r['lamp_status'],'AC')
        self.assertEqual(r['evidence']['review']['ocr']['fields']['cool']['confidence'],.4)

    def test_null_judgment_never_becomes_zero(self):
        b=self.body(max_basis={},values=fixture.values(cool=None))
        p=self.service.preview(b);self.assertFalse(p['can_confirm']);self.assertIsNone(p['max_score'])

    def test_same_session_idempotency_and_failed_transaction_rollback(self):
        b=self.approved();store=self.store();p=store.prepare(b)
        request={**b,'submit_acknowledge':True,'submission_token':p['submission_token']}
        with closing(sqlite3.connect(self.db)) as db:before=audit(db)
        with patch.object(store,'_checkpoint',side_effect=lambda name:(_ for _ in ()).throw(RuntimeError('rollback')) if name=='after_record' else None):
            with self.assertRaises(RuntimeError):store.submit(request)
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(audit(db),before)
        first=store.submit(request);second=store.submit(request)
        self.assertEqual(first['record']['record_id'],second['record']['record_id']);self.assertTrue(second['idempotent'])

    def test_trusted_automatic_result_uses_custom_max_without_proof(self):
        self.official_auto();r=self.auto_store().submit_verified(self.sid)['record']
        self.assertEqual(r['max_score'],22);self.assertEqual(r['lamp_status'],'AC')
        self.assertEqual(r['confirmation_method'],'verified_auto')

    def test_csv_difference_automatic_rollout_remains_conservative(self):
        self.official_auto();folder,row=self.service._read(self.sid)
        # Both raw own-row and extracted fields must be consistently changed.
        row['ocr']['fields']['bad']['value']=1
        for pr in row['ocr']['player_rows']:
            if pr['name']=='TC':pr['fields']['bad']['value']=1
        atomic_json(folder/'result-review.json',row)
        with self.assertRaisesRegex(LibraryError,'pending_live_validation'):
            self.auto_store().submit_verified(self.sid)

    def test_cross_session_conflict_blocks_automatic_even_if_catalog_matches(self):
        self.official_auto()
        # Earlier independently confirmed observation on the same Song/difficulty.
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            self.assertFalse(comparison(db,1,'HARD',11)['ranking_paused'])
        saved=self.approved();self.commit(saved)
        self.clone_session();self.session['selection']=None;self.sync_session()
        self.commit(self.approved(perfect=12,cool=0,max_combo=12,total_notes=11))
        self.clone_session();self.official_auto()
        with self.assertRaisesRegex(LibraryError,'cross_session_basis_conflict'):
            self.auto_store().submit_verified(self.sid)

    def test_other_song_or_other_difficulty_does_not_conflict(self):
        self.commit(self.approved())
        with self.store().library.open() as lib:
            self.assertFalse(comparison(lib.connection,2,'HARD',1013)['ranking_paused'])
            self.assertFalse(comparison(lib.connection,1,'EASY',1013)['ranking_paused'])

    def test_history_reload_keeps_policy_source_and_judgments(self):
        r=self.commit(self.approved())['record'];again=self.store().detail(r['record_id'])
        self.assertEqual(again['max_policy'],'custom-judged-v1')
        self.assertEqual(again['evidence']['max_basis_verification']['source'],'completed_result_judged_total')
        self.assertEqual(again['lamp_verification']['source'],'system_result_judgments')
        self.assertFalse(again['lamp_verification']['independent_game_lamp_verified'])

    def test_failed_partial_total_does_not_create_cross_session_conflict(self):
        self.commit(self.approved())
        self.clone_session();self.session['selection']=None;self.sync_session()
        r=self.commit(self.approved(perfect=2,cool=1,bad=0,miss=2,max_combo=3,total_notes=11,completion_status='FAILED'))['record']
        self.assertFalse(r['evidence']['comparison']['ranking_paused'])
        self.assertEqual(r['lamp_status'],'FAILED');self.assertIsNone(r['max_score'])

    def test_recognized_ex_bridge_keeps_higher_old_score_and_updates_lamp_only(self):
        self.service.scoring_policy='independent-max-v2'
        old=self.commit(self.approved())['record']
        self.service.scoring_policy='custom-judged-v1'
        self.clone_session();self.session['selection']=None;self.sync_session()
        new=self.commit(self.approved(perfect=9,cool=2,max_combo=11))['record']
        self.assertEqual(new['achievement_feedback']['events'],['FIRST_AC','NEW_LAMP'])
        self.assertFalse(new['achievement_feedback'].get('new_comparison_group',False))
        self.assertEqual(self.store().detail(old['record_id'])['ex_score'],21)
        from dance_results.achievements import progress_for_songs
        with self.store().library.open() as lib:
            p=next(p for p in progress_for_songs(lib.connection,[1])[1] if p['difficulty']=='HARD')
            self.assertEqual(p['legacy_bests']['best_ex'],21)
            self.assertEqual(p['best_ex_score'],21)
            self.assertEqual(p['comparison_scope'],'confirmed-perfect2-cool1-v1')

    def test_database_rejects_forged_conflict_eligibility(self):
        self.commit(self.approved())
        from uuid import uuid4
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            ps=dict(db.execute('SELECT * FROM play_sessions').fetchone());ps['session_id']=str(uuid4())
            db.execute('INSERT INTO play_sessions('+','.join(ps)+') VALUES('+','.join('?'for _ in ps)+')',tuple(ps.values()))
            r=dict(db.execute('SELECT * FROM play_records').fetchone())
            r.update(record_id=999,session_id=ps['session_id'],review_id=str(uuid4()),perfect=12,cool=0,max_combo=12,ex_score=24,total_notes=12,max_score=24,max_difference=0,percentage=100,lamp_status='ACP')
            e=json.loads(r['evidence_json']);e['note_sources'].update(judged_total=12,max_basis_notes=12)
            e['max_basis_verification']['notes']=12;e['comparison']['eligible']=True
            r['evidence_json']=json.dumps(e)
            with self.assertRaisesRegex(sqlite3.IntegrityError,'comparison evidence'):
                db.execute('INSERT INTO play_records('+','.join(r)+') VALUES('+','.join('?'for _ in r)+')',tuple(r.values()))


class CustomMigrationTests(unittest.TestCase):
    def run_migration(self,fail=False):
        f=submit_fixture.SubmissionMigrationTests();f.setUp()
        try:
            with closing(sqlite3.connect(f.path)) as db:
                from dance_results.migrations_v6 import migrate_v5
                from dance_results.migrations_v7 import migrate_v6
                from dance_results.migrations_v8 import migrate_v7
                migrate_v5(db,f.path,new_database=True);migrate_v6(db,f.path,new_database=True);migrate_v7(db,f.path,new_database=True)
                before=audit(db);bests=db.execute('SELECT * FROM difficulty_bests').fetchall()
                if fail:
                    with patch('dance_results.migrations_v9.check_integrity',side_effect=[None,RuntimeError('migration rollback')]):
                        with self.assertRaises(RuntimeError):migrate_v8(db,f.path,new_database=True)
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8);self.assertEqual(audit(db),before)
                else:
                    result=migrate_v8(db,f.path)
                    self.assertEqual(result['before'],result['after']);self.assertTrue(result['old_bests_unchanged'])
                    self.assertEqual(bests,db.execute('SELECT * FROM difficulty_bests').fetchall())
                    with closing(sqlite3.connect(result['backup_path'])) as backup:self.assertEqual(audit(backup),before)
        finally:f.tearDown()

    def test_v8_to_v9_preserves_nonempty_legacy_records_ids_and_bests(self):self.run_migration()

    def test_v9_migration_failure_rolls_back_all_changes(self):self.run_migration(True)


if __name__=='__main__':unittest.main()
