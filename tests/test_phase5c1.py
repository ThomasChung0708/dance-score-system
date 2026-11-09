"""Independent MAX proof, result-priority EX and immutable legacy snapshots."""
from contextlib import closing
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_result_review as fixture
import test_submissions as submit_fixture
import test_phase5c as auto_fixture
from dance_results.scoring import calculate as _calculate
from functools import partial
calculate=partial(_calculate,max_basis={})
from dance_results.library_service import LibraryService,LibraryError
from dance_results.result_review import ResultReviews
from dance_results.submissions import SubmissionService
from dance_results.migrations_v8 import migrate_v7
from dance_results.sqlite_safety import audit,check_integrity


class ResultPriorityTests(unittest.TestCase):
    setUp=fixture.ReviewPersistenceTests.setUp
    tearDown=fixture.ReviewPersistenceTests.tearDown
    body=fixture.ReviewPersistenceTests.body
    store=submit_fixture.SubmissionTests.store
    commit=submit_fixture.SubmissionTests.commit
    clone_session=submit_fixture.SubmissionTests.clone_session
    official_auto=auto_fixture.Phase5CTests.official_auto
    auto_store=auto_fixture.Phase5CTests.auto_store

    def reviewed(self,**changes):
        b=self.body(max_basis={},**changes);saved=self.service.save(b)
        b.update(revision=saved['revision'],play_mode='single');return b

    def heart(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(1,'HARD',29,'manual',1194,'manual',1)")
            db.commit()
        self.session['selection']=None
        from dance_results.monitor_tracking import atomic_json
        atomic_json(self.output/'sessions'/self.sid/'session.json',self.session)
        row=self.service._read(self.sid)[1];row['locked_session']=copy.deepcopy(self.session)
        atomic_json(self.output/'sessions'/self.sid/'result-review.json',row)
        b=self.body(max_basis={},values=fixture.values(perfect=1145,cool=30,bad=7,miss=10,
            max_combo=543,total_notes=1194,game_level=29))
        p=self.service.preview(b)
        b['note_review']=dict(context=p['note_exception']['context'],source='catalog',judgments=True,
            identity=True,source_confirmed=True,difference=True,note='正確原圖數字；差異原因未知')
        return b

    def save(self,b):
        saved=self.service.save(b);b.update(revision=saved['revision'],play_mode='single')
        return self.commit(b)['record']

    def prove(self,b,notes,source='independent'):
        p=self.service.preview({**b,'max_basis':{}})
        b['max_basis']=dict(context=p['max_basis_verification']['context'],notes=notes,
            source=source,verified=True,evidence='ISOLATED TEST ONLY: independently stipulated game-MAX definition')
        return b

    def test_catalog_agreement_does_not_prove_max_or_ac(self):
        b=self.body(max_basis={});p=self.service.preview(b)
        self.assertEqual(p['ex_score'],21);self.assertIsNone(p['max_score']);self.assertIsNone(p['lamp_preview'])
        self.assertEqual(p['note_sources']['judged_total'],11)

    def test_heart_two_unverified_previews_and_three_independent_counts(self):
        p=self.service.preview(self.heart());n=p['note_sources']
        self.assertEqual((n['catalog_total_notes'],n['judged_total'],n['max_basis_notes']),(1194,1192,None))
        self.assertEqual([c['max_score'] for c in n['conditional_previews']],[2388,2384])
        self.assertEqual([c['display'] for c in n['conditional_previews']],['MAX -68','MAX -64'])
        self.assertTrue(all(not c['confirmed'] for c in n['conditional_previews']))
        self.assertIsNone(p['max_score']);self.assertEqual(p['ex_score'],2320)

    def test_manual_difference_confirmation_saves_ex_without_claiming_max(self):
        r=self.save(self.heart())
        self.assertEqual((r['perfect'],r['cool'],r['bad'],r['miss']),(1145,30,7,10))
        self.assertEqual(r['ex_score'],2320);self.assertEqual(r['lamp_status'],'CLEAR')
        for k in ('max_score','max_difference','percentage','total_notes','max_basis_notes'):self.assertIsNone(r[k])
        self.assertEqual(r['note_sources']['catalog_total_notes'],1194)
        self.assertTrue(r['note_exception']['authorized']);self.assertEqual(r['max_status'],'unconfirmed')
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(db.execute('SELECT total_notes FROM game_difficulties WHERE song_id=1').fetchone()[0],1194)

    def test_proven_basis_can_differ_from_catalog_without_overwriting_it(self):
        r=self.save(self.prove(self.heart(),1192,'judged'))
        self.assertEqual((r['max_score'],r['ex_score'],r['max_difference']),(2384,2320,64))
        self.assertEqual(r['note_sources']['catalog_total_notes'],1194)
        self.assertEqual(r['note_sources']['max_basis_notes'],1192)
        self.assertEqual(r['max_status'],'confirmed')

    def test_reference_alone_is_not_independent_proof(self):
        b=self.heart();b['max_basis']=dict(notes=1194,source='catalog',verified=True,evidence='')
        p=self.service.preview(b);self.assertIsNone(p['max_score'])
        b['max_basis']['evidence']='Only CSV agrees; no independent proof'
        self.assertFalse(self.service.preview(b)['max_basis_verification']['verified'])

    def test_failed_partial_sum_has_no_judged_max_preview(self):
        b=self.body(max_basis={},values=fixture.values(perfect=2,cool=1,bad=0,miss=2,
            max_combo=3,completion_status='FAILED',total_notes=11))
        p=self.service.preview(b)
        self.assertNotIn('judged',[c['source'] for c in p['note_sources']['conditional_previews']])
        self.assertIsNone(calculate(b['values'],identity_confirmed=True,max_basis=dict(verified=True,notes=5,source='judged'))['max_score'])
        self.prove(b,5,'judged');p=self.service.preview(b)
        self.assertTrue(p['max_basis_verification']['partial_judgments_forbidden'])
        r=self.save(b);self.assertEqual(r['lamp_status'],'FAILED');self.assertIsNone(r['max_score'])

    def test_unknown_max_normal_clear_can_commit_and_make_new_score(self):
        b=self.reviewed(values=fixture.values(perfect=8,cool=0,bad=3,max_combo=8))
        r=self.commit(b)['record'];self.assertIn('NEW_SCORE',r['achievement_feedback']['events'])
        self.assertEqual(r['lamp_status'],'CLEAR');self.assertIsNone(r['percentage'])

    def test_acp_looking_result_stays_pending_without_max_scope(self):
        r=self.commit(self.reviewed(values=fixture.values(perfect=11,cool=0)))['record']
        self.assertTrue(r['lamp_pending']);self.assertNotIn('NEW_LAMP',r['achievement_feedback']['events'])
        p=next(p for p in LibraryService(self.db).detail(1,1)['progress'] if p['difficulty']=='HARD')
        self.assertEqual(p['best_ex_score'],22);self.assertEqual(p['best_lamp'],'UNPLAYED')

    def test_independent_max_does_not_resolve_discrepant_acp_scope(self):
        b=self.heart();b['values'].update(perfect=1192,cool=0,bad=0,miss=0,max_combo=1192)
        b['note_review']['context']=self.service.preview({**b,'note_review':{}})['note_exception']['context']
        r=self.save(self.prove(b,1192))
        self.assertEqual(r['max_score'],2384);self.assertIsNone(r['lamp_status'])

    def test_max_proof_not_reused_after_values_or_session_change(self):
        b=self.prove(self.body(max_basis={}),11)
        b['values']['perfect']=9;self.assertIsNone(self.service.preview(b)['max_score'])
        b=self.prove(self.body(max_basis={}),11);self.clone_session()
        b.update(session_id=self.sid,review_id=self.row['review_id'])
        self.assertIsNone(self.service.preview(b)['max_score'])

    def test_auto_consistent_result_can_store_ex_with_unknown_max(self):
        self.official_auto();r=self.auto_store().submit_verified(self.sid)['record']
        self.assertEqual(r['ex_score'],21);self.assertEqual(r['confirmation_method'],'verified_auto')
        self.assertIsNone(r['max_score']);self.assertIsNone(r['max_basis_notes']);self.assertIsNone(r['lamp_status'])

    def test_auto_discrepancy_still_pending_never_inherits_human_exception(self):
        self.official_auto(notes=13)
        with self.assertRaises(LibraryError):self.auto_store().submit_verified(self.sid)
        self.assertEqual(self.store().history()['total'],0)

    def test_resave_restart_keeps_source_confirmation_separate_from_max(self):
        b=self.heart();saved=self.service.save(b)
        fresh=ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(self.sid)
        self.assertEqual(fresh['note_sources'] if 'note_sources' in fresh else fresh['preview']['note_sources'],saved['note_sources'])
        self.assertTrue(fresh['preview']['note_exception']['authorized'])
        self.assertIsNone(fresh['preview']['max_score'])

    def test_csv_never_changes_raw_ocr_or_corrected_judgments(self):
        original=copy.deepcopy(self.ocr);b=self.heart();r=self.save(b)
        self.assertEqual(r['evidence']['review']['ocr'],original)
        self.assertEqual(r['evidence']['values']['perfect'],1145)
        self.assertEqual(r['note_sources']['catalog_total_notes'],1194)

    def test_score_and_percentage_use_only_independently_verified_basis(self):
        v=fixture.values(perfect=1145,cool=30,bad=7,miss=10,max_combo=543,total_notes=1194)
        unknown=calculate(v,notes_verified=True,identity_confirmed=True)
        known=calculate(v,notes_verified=True,identity_confirmed=True,max_basis=dict(verified=True,notes=1192),note_exception=True)
        self.assertEqual(unknown['ex_score'],known['ex_score']);self.assertIsNone(unknown['percentage'])
        self.assertEqual(known['display'],'MAX -64')

    def test_higher_score_without_max_does_not_lose_existing_lamp(self):
        b=self.body(values=fixture.values(perfect=8,cool=0,bad=3,max_combo=8));self.save(b)
        self.clone_session();r=self.commit(self.reviewed(values=fixture.values(perfect=11,cool=0)))['record']
        self.assertEqual(r['achievement_feedback']['events'],['NEW_SCORE'])
        self.assertEqual(r['achievement_feedback']['best_lamp'],'CLEAR');self.assertIsNone(r['max_score'])

    def test_repeated_ex_only_submission_is_idempotent_and_events_frozen(self):
        b=self.reviewed();p=self.store().prepare(b);request={**b,'submission_token':p['submission_token'],'submit_acknowledge':True}
        first=self.store().submit(request);second=self.store().submit(request)
        self.assertTrue(second['idempotent']);self.assertEqual(first['record']['record_id'],second['record']['record_id'])

    def test_database_rejects_unverified_max_even_with_valid_ex(self):
        r=self.commit(self.reviewed())['record']
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            session=dict(db.execute('SELECT * FROM play_sessions').fetchone());session['session_id']=str(uuid4())
            db.execute('INSERT INTO play_sessions('+','.join(session)+') VALUES('+','.join('?'for _ in session)+')',tuple(session.values()))
            record=dict(db.execute('SELECT * FROM play_records').fetchone())
            record.update(record_id=999,session_id=session['session_id'],review_id=str(uuid4()),total_notes=11,
                notes_source='game_screen',max_score=22,max_difference=1,percentage=100*21/22)
            with self.assertRaisesRegex(sqlite3.IntegrityError,'MAX.*evidence'):
                db.execute('INSERT INTO play_records('+','.join(record)+') VALUES('+','.join('?'for _ in record)+')',tuple(record.values()))

    def test_legacy_max_and_bests_remain_immutable_across_v8_migration(self):
        fixture_db=submit_fixture.SubmissionMigrationTests();fixture_db.setUp()
        try:
            with closing(sqlite3.connect(fixture_db.path)) as db:
                from dance_results.migrations_v6 import migrate_v5
                from dance_results.migrations_v7 import migrate_v6
                migrate_v5(db,fixture_db.path,new_database=True);migrate_v6(db,fixture_db.path,new_database=True)
                before=audit(db);bests=db.execute('SELECT * FROM difficulty_bests').fetchall()
                result=migrate_v7(db,fixture_db.path)
                self.assertEqual(result['before'],result['after']);self.assertTrue(result['old_bests_unchanged'])
                self.assertEqual(bests,db.execute('SELECT * FROM difficulty_bests').fetchall());check_integrity(db)
                with closing(sqlite3.connect(result['backup_path'])) as backup:self.assertEqual(audit(backup),before)
        finally:fixture_db.tearDown()

    def test_v8_failed_migration_rolls_back_to_v7(self):
        fixture_db=submit_fixture.SubmissionMigrationTests();fixture_db.setUp()
        try:
            with closing(sqlite3.connect(fixture_db.path)) as db:
                from dance_results.migrations_v6 import migrate_v5
                from dance_results.migrations_v7 import migrate_v6
                migrate_v5(db,fixture_db.path,new_database=True);migrate_v6(db,fixture_db.path,new_database=True)
                before=audit(db)
                with patch('dance_results.migrations_v8.check_integrity',side_effect=[None,RuntimeError('late failure')]):
                    with self.assertRaises(RuntimeError):migrate_v7(db,fixture_db.path,new_database=True)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],7);self.assertEqual(audit(db),before)
        finally:fixture_db.tearDown()
