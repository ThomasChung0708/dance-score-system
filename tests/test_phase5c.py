"""Phase 5C: disposable catalogs, explicit human evidence and queued auto storage."""
import copy
from contextlib import closing
import json
import shutil
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_result_review as fixture
import test_submissions as submissions_fixture
import test_official_import as official
from dance_results.library_service import LibraryService,LibraryError
from dance_results.monitor_tracking import atomic_json,ManualReferences
from dance_results.official_import import import_catalog,read_catalog
from dance_results.result_review import ResultReviews
from dance_results.submissions import SubmissionService
from dance_results.verified_auto import AutomaticStorage,automatic_request
from dance_results.sqlite_safety import audit,check_integrity
from dance_results.migrations_v7 import migrate_v6


class Phase5CTests(unittest.TestCase):
    setUp=fixture.ReviewPersistenceTests.setUp
    tearDown=fixture.ReviewPersistenceTests.tearDown
    body=fixture.ReviewPersistenceTests.body
    store=submissions_fixture.SubmissionTests.store
    commit=submissions_fixture.SubmissionTests.commit
    clone_session=submissions_fixture.SubmissionTests.clone_session

    def mismatch(self,**changes):
        body=self.body(values=fixture.values(perfect=9,cool=1,bad=1,max_combo=10,total_notes=13,**changes))
        p=self.service.preview(body)
        body['note_review']=dict(context=p['note_exception']['context'],source='manual',judgments=True,
            identity=True,source_confirmed=True,difference=True,note='目視核對；原因尚未確認')
        return body

    def save_commit(self,body):
        saved=self.service.save(body);body.update(revision=saved['revision'],play_mode='single')
        return self.commit(body)['record']

    def official_auto(self,*,title='Auto Official',notes=11):
        cat=read_catalog(official.csv_bytes([official.raw_row(title=title,lvHard='17',notesHard=str(notes))]),'auto.csv')
        result=import_catalog(self.db,cat)
        with closing(sqlite3.connect(self.db)) as db:
            sid=db.execute("SELECT song_id FROM songs WHERE source_type='official'").fetchone()[0]
        self.session['song_id']=sid;self.session['matching_status']='confirmed_official_catalog'
        self.session['result']['frame_hash']='a'*64
        self.session['selection']['source_type']='official'
        self.session['selection']['fields']['title']['value']=title
        self.session['selection']['fields']['total_notes']['value']=notes
        self.ocr['fields']['title']['value']=title
        self.ocr['errors']=[]
        for f in self.ocr['fields'].values():f.update(confidence=.99,reason=None)
        for row in self.ocr['player_rows']:
            for f in row['fields'].values():f.update(confidence=.99,reason=None)
        atomic_json(self.output/'sessions'/self.sid/'session.json',self.session)
        row=self.service._read(self.sid)[1];row.update(locked_session=copy.deepcopy(self.session),ocr=copy.deepcopy(self.ocr))
        row['draft']['song_id']=sid
        atomic_json(self.output/'sessions'/self.sid/'result-review.json',row)
        self.row=row
        return row

    def auto_store(self):
        store=self.store();store.auto_allowed=lambda sid:True
        return store

    def test_manual_count_difference_saves_real_values_and_evidence(self):
        body=self.mismatch();r=self.save_commit(body)
        self.assertEqual((r['perfect'],r['cool'],r['bad'],r['miss']),(9,1,1,0))
        self.assertEqual((r['ex_score'],r['max_score'],r['lamp_status']),(19,26,'CLEAR'))
        self.assertEqual(r['confirmation_method'],'manual_note_exception')
        self.assertTrue(r['note_exception']['authorized']);self.assertEqual(r['note_exception']['difference'],-2)
        self.assertEqual(r['evidence']['review']['ocr'],self.ocr)
        self.assertFalse(r['note_exception']['counting_rule_verified'])

    def test_heart_1194_1192_manual_exception_is_generic(self):
        b=self.mismatch();b['values']=fixture.values(perfect=1145,cool=30,bad=7,miss=10,max_combo=543,total_notes=1194,game_level=29)
        b['note_review']['context']=self.service.preview({**b,'note_review':{}})['note_exception']['context']
        b['max_basis']=dict(context=self.service.preview({**b,'max_basis':{}})['max_basis_verification']['context'],source='independent',notes=1194,verified=True,evidence='ISOLATED TEST: MAX definition stipulated separately')
        r=self.save_commit(b)
        self.assertEqual((r['ex_score'],r['max_score'],r['display']),(2320,2388,'MAX -68'))
        self.assertEqual(r['evidence']['note_sources']['judged_total'],1192)

    def test_each_of_four_acknowledgments_is_required(self):
        for key in ('judgments','identity','source_confirmed','difference'):
            b=self.mismatch();b['note_review'][key]=False
            self.assertFalse(self.service.preview(b)['can_confirm'])

    def test_context_cannot_be_reused_after_number_identity_or_row_change(self):
        b=self.mismatch();b['values']['perfect']=8
        self.assertFalse(self.service.preview(b)['note_exception']['authorized'])
        b=self.mismatch();self.clone_session();b['session_id']=self.sid;b['review_id']=self.row['review_id']
        self.assertFalse(self.service.preview(b)['note_exception']['authorized'])

    def test_unknown_notes_completed_remains_pending_not_fake_max(self):
        b=self.body(values=fixture.values(total_notes=None),notes_verified=False)
        self.service.save(b)
        b.update(revision=1,play_mode='single')
        self.assertTrue(self.store().prepare(b)['can_submit'])
        self.assertIsNone(self.service.preview(b)['max_score'])

    def test_difference_all_perfect_has_pending_lamp_no_acp_event(self):
        b=self.mismatch();b['values']=fixture.values(perfect=11,cool=0,bad=0,miss=0,total_notes=13)
        b['note_review']['context']=self.service.preview({**b,'note_review':{}})['note_exception']['context']
        r=self.save_commit(b)
        self.assertIsNone(r['lamp_status']);self.assertTrue(r['lamp_pending'])
        self.assertNotIn('NEW_LAMP',r['achievement_feedback']['events'])
        p=next(p for p in LibraryService(self.db).detail(1,1)['progress'] if p['difficulty']=='HARD')
        self.assertEqual(p['best_lamp'],'UNPLAYED');self.assertEqual(p['best_ex_score'],22)

    def test_difference_only_cool_does_not_prove_ac(self):
        b=self.mismatch();b['values']=fixture.values(perfect=10,cool=1,bad=0,total_notes=13)
        b['note_review']['context']=self.service.preview({**b,'note_review':{}})['note_exception']['context']
        self.assertIsNone(self.save_commit(b)['lamp_status'])

    def test_exception_survives_restart_and_cannot_mutate_record(self):
        b=self.mismatch();r=self.save_commit(b)
        restarted=SubmissionService(LibraryService(self.db),ResultReviews(self.db,self.output,scoring_policy='independent-max-v2'))
        self.assertEqual(restarted.detail(r['record_id'])['note_exception'],r['note_exception'])
        with closing(sqlite3.connect(self.db)) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute('UPDATE play_records SET perfect=0')

    def test_exception_cannot_reuse_untrusted_source_or_over_max(self):
        b=self.mismatch();b['note_review']['source']='catalog'
        self.assertFalse(self.service.preview(b)['can_confirm'])
        b=self.mismatch();b['values']['perfect']=1000;b['note_review']['context']=self.service.preview({**b,'note_review':{}})['note_exception']['context'];b['max_basis']['context']=self.service.preview({**b,'max_basis':{}})['max_basis_verification']['context']
        self.assertFalse(self.service.preview(b)['can_confirm'])

    def test_official_automatic_zero_manual_input_and_saved_method(self):
        self.official_auto();r=self.auto_store().submit_verified(self.sid)['record']
        self.assertEqual((r['ex_score'],r['lamp_status'],r['confirmation_method']),(21,None,'verified_auto'))
        self.assertTrue(r['evidence']['automatic']['authorized']);self.assertEqual(r['review_revision'],0)
        self.assertFalse(self.service.detail(self.sid)['draft']['player_verified'])

    def test_auto_disabled_in_service_by_default(self):
        self.official_auto()
        with self.assertRaises(LibraryError):self.store().submit_verified(self.sid)

    def test_auto_low_confidence_null_and_wrong_local_player_remain_pending(self):
        row=self.official_auto()
        for key,value in [('cool',None),('perfect',10)]:
            candidate=copy.deepcopy(row);candidate['ocr']['fields'][key].update(value=value,confidence=.6)
            self.assertTrue(automatic_request(self.service,candidate)[1])
        row['ocr']['local_player_status']='UNKNOWN'
        self.assertIn('automatic_local_player_uncertain',automatic_request(self.service,row)[1])

    def test_auto_never_allows_note_difference_or_prior_human_exception(self):
        row=self.official_auto(notes=13)
        self.assertIn('automatic_note_discrepancy',automatic_request(self.service,row)[1])
        row['draft']['note_review']=dict(authorized=True);row['revision']=1
        self.assertTrue(automatic_request(self.service,row)[1])

    def test_auto_source_and_result_conflicts_are_not_title_matches(self):
        row=self.official_auto()
        for key,value in [('source_type','unknown')]:
            r=copy.deepcopy(row);r['locked_session']['selection'][key]=value
            self.assertTrue(automatic_request(self.service,r)[1])
        conflict=copy.deepcopy(row);conflict['locked_session']['result']['state']='GAME_OVER'
        self.assertIn('automatic_completion_session_conflict',automatic_request(self.service,conflict)[1])
        row['ocr']['fields']['game_level']['value']=99
        self.assertIn('automatic_result_level_conflict',automatic_request(self.service,row)[1])

    def test_same_auto_session_retry_and_different_session_same_score(self):
        self.official_auto();store=self.auto_store()
        first=store.submit_verified(self.sid);again=store.submit_verified(self.sid)
        self.assertTrue(again['idempotent']);self.assertEqual(first['record']['record_id'],again['record']['record_id'])
        self.clone_session();second=store.submit_verified(self.sid)
        self.assertNotEqual(first['record']['record_id'],second['record']['record_id'])
        self.assertEqual(second['record']['achievement_feedback']['events'],['SAVED'])

    def test_auto_failure_rolls_back_then_retries_without_false_event(self):
        self.official_auto();store=self.auto_store()
        with patch.object(store,'_checkpoint',side_effect=RuntimeError('simulated failure')):
            with self.assertRaises(RuntimeError):store.submit_verified(self.sid)
        self.assertEqual(store.history()['total'],0)
        r=store.submit_verified(self.sid)['record'];self.assertTrue(r['achievement_feedback']['first_record'])

    def test_mode_persists_manual_default_no_historical_backlog(self):
        row=self.official_auto();mode=AutomaticStorage(self.store())
        try:
            self.assertEqual(mode.status()['mode'],'MANUAL')
            with self.assertRaises(LibraryError):mode.configure({'mode':'VERIFIED_AUTO'})
            mode.configure(dict(mode='VERIFIED_AUTO',acknowledge=True))
            self.assertFalse(mode.allowed(self.sid))
        finally:mode.close()
        restored=AutomaticStorage(self.store())
        try:
            self.assertEqual(restored.status()['mode'],'VERIFIED_AUTO');self.assertFalse(restored.allowed(self.sid))
            restored.configure(dict(mode='MANUAL'));self.assertFalse(restored.allowed(str(uuid4())))
        finally:restored.close()

    def test_other_gui_process_can_disable_auto_for_existing_worker(self):
        first=AutomaticStorage(self.store());second=AutomaticStorage(self.store())
        try:
            first.configure(dict(mode='VERIFIED_AUTO',acknowledge=True))
            self.assertTrue(second.allowed(str(uuid4())))
            second.configure(dict(mode='MANUAL'))
            self.assertFalse(first.allowed(str(uuid4())))
            self.assertEqual(first.status()['mode'],'MANUAL')
        finally:first.close();second.close()

    def test_background_ingest_submits_new_result_without_browser(self):
        self.official_auto();mode=AutomaticStorage(self.store())
        try:
            mode.configure(dict(mode='VERIFIED_AUTO',acknowledge=True));self.clone_session()
            self.assertTrue(mode.allowed(self.sid))
            mode.queue.join()
            self.assertEqual(mode.status()['diagnostics'][self.sid]['status'],'saved')
            self.assertEqual(len(mode.status()['events']),1)
            mode.enqueue(self.sid);mode.queue.join();self.assertEqual(len(mode.status()['events']),1)
        finally:mode.close()

    def test_missing_result_time_blocks_never_fills_with_confirmation_time(self):
        self.session.pop('ended_at');atomic_json(self.output/'sessions'/self.sid/'session.json',self.session)
        row=self.service._read(self.sid)[1];row['locked_session']=self.session
        atomic_json(self.output/'sessions'/self.sid/'result-review.json',row)
        b=self.body();saved=self.service.save(b);b.update(revision=saved['revision'],play_mode='single')
        p=self.store().prepare(b)
        self.assertIn('session_result_time_required',[i['code'] for i in p['issues']])

    def test_delayed_submit_preserves_timezone_aware_result_date(self):
        b=self.body();r=self.save_commit(b)
        self.assertEqual(r['played_at'],'2026-10-22T00:01:36+00:00')
        self.assertNotEqual(r['played_at'][:10],r['confirmed_at'][:10])
        self.assertEqual(r['played_at_source'],'session.ended_at')

    def test_custom_unconfirmed_pack_is_not_auto_and_keeps_all_candidates(self):
        row=self.official_auto();row['locked_session']['selection']['source_type']='custom'
        self.assertIn('automatic_pack_unconfirmed',automatic_request(self.service,row)[1])
        self.assertEqual(self.service.candidates('JOINT')['count'],2)

    def test_custom_saved_references_match_full_chart_not_first_namesake(self):
        self.session['result']['frame_hash']='a'*64
        self.session['selection'].update(source_type='custom',pack=dict(pack_id=1,name='Pack A',status='confirmed'))
        atomic_json(self.output/'sessions'/self.sid/'session.json',self.session)
        row=self.service._read(self.sid)[1];row['locked_session']=copy.deepcopy(self.session)
        atomic_json(self.output/'sessions'/self.sid/'result-review.json',row)
        self.service.save(self.body(values=fixture.values(duration_seconds=96),duration_verified=True))
        self.clone_session()
        self.service.save(self.body(song_id=2,relative_path='Pack A\\Folder B',
            values=fixture.values(perfect=12,cool=1,total_notes=13,game_level=20,duration_seconds=95),duration_verified=True))
        self.clone_session();request,issues=automatic_request(self.service,self.row)
        self.assertFalse(issues,issues);self.assertEqual(request['song_id'],1)
        r=self.auto_store().submit_verified(self.sid)['record']
        self.assertEqual(r['song_id'],1);self.assertEqual(r['confirmation_method'],'verified_auto')

    def test_official_namesakes_never_pick_first_candidate(self):
        row=self.official_auto()
        extra=read_catalog(official.csv_bytes([official.raw_row(title='Auto Official',gn='other.gn',
            fileId='999999',lvHard='17',notesHard='11')]),'other.csv')
        import_catalog(self.db,extra)
        self.assertIn('automatic_unique_verified_identity_required',automatic_request(self.service,row)[1])

    def test_multiplayer_local_second_row_is_auto_and_host_not_substituted(self):
        self.ocr=fixture.parse_result(fixture.result_tokens()+[
            fixture.token('HOST',185,318),fixture.token('39',368,318),fixture.token('99',480,318)],'TC')
        # Row ordering does not confer ownership. Move local evidence to second
        # row in the explicit row list; raw local OCR stays independently TC.
        self.ocr['player_rows'].reverse()
        row=self.official_auto();r=self.auto_store().submit_verified(self.sid)['record']
        self.assertEqual(r['player_name'],'TC');self.assertEqual(r['perfect'],10)
        self.assertEqual(r['play_mode'],'multiplayer')

    def test_auto_evidence_change_during_backup_blocks_submission(self):
        self.official_auto();store=self.auto_store()
        from dance_results.submissions import verified_backup
        def change(*args,**kwargs):
            result=verified_backup(*args,**kwargs)
            session=copy.deepcopy(self.session);session['ended_at']='2026-10-23T00:00:00+00:00'
            atomic_json(self.output/'sessions'/self.sid/'session.json',session)
            return result
        with patch('dance_results.submissions.verified_backup',side_effect=change):
            with self.assertRaises(LibraryError):store.submit_verified(self.sid)
        self.assertEqual(store.history()['total'],0)

    def test_disabling_auto_during_backup_blocks_commit(self):
        self.official_auto();store=self.auto_store();enabled=[True];store.auto_allowed=lambda sid:enabled[0]
        from dance_results.submissions import verified_backup
        def disable(*args,**kwargs):
            result=verified_backup(*args,**kwargs);enabled[0]=False;return result
        with patch('dance_results.submissions.verified_backup',side_effect=disable):
            with self.assertRaises(LibraryError):store.submit_verified(self.sid)
        self.assertEqual(store.history()['total'],0)

    def test_auto_requires_capture_frame_and_ordered_aware_times(self):
        row=self.official_auto();row['locked_session']['result'].pop('frame_hash')
        self.assertIn('automatic_frame_evidence_missing',automatic_request(self.service,row)[1])
        row['locked_session']['ended_at']='2020-01-01T00:00:00+00:00'
        self.assertIn('automatic_session_time_invalid',automatic_request(self.service,row)[1])

    def test_auto_worker_failure_can_retry_without_duplicate_feedback(self):
        self.official_auto();mode=AutomaticStorage(self.store())
        try:
            mode.configure(dict(mode='VERIFIED_AUTO',acknowledge=True))
            with patch.object(mode.submissions,'_checkpoint',side_effect=RuntimeError('temporary failure')):
                self.clone_session();mode.queue.join()
            self.assertEqual(mode.status()['events'],[])
            self.assertEqual(mode.status()['diagnostics'][self.sid]['status'],'pending_review')
            mode.enqueue(self.sid);mode.queue.join()
            self.assertEqual(len(mode.status()['events']),1)
        finally:mode.close()

    def test_manual_difference_rolls_back_and_keeps_pending_evidence(self):
        body=self.mismatch();saved=self.service.save(body);body.update(revision=saved['revision'],play_mode='single')
        store=self.store()
        with patch.object(store,'_checkpoint',side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):self.commit(body,store)
        self.assertEqual(store.history()['total'],0)
        self.assertTrue(ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(self.sid)['preview']['note_exception']['authorized'])

    def test_auto_edited_human_draft_is_never_silently_replaced(self):
        row=self.official_auto();row['revision']=2;row['draft']['values']['perfect']=0
        self.assertIn('automatic_review_already_edited',automatic_request(self.service,row)[1])

    def test_failed_unknown_max_stays_null_and_never_uses_judged_total(self):
        body=self.body(values=fixture.values(completion_status='FAILED',total_notes=None),notes_verified=False)
        r=self.save_commit(body)
        self.assertEqual(r['lamp_status'],'FAILED');self.assertIsNone(r['max_score']);self.assertIsNone(r['total_notes'])

    def test_difference_lamp_pending_does_not_lower_existing_ac(self):
        body=self.body();self.save_commit(body);self.clone_session()
        b=self.mismatch();b['values']=fixture.values(perfect=10,cool=0,bad=0,max_combo=10,total_notes=11)
        # Use the existing trusted 11 notes; only judgments differ.
        b['note_review']['context']=self.service.preview({**b,'note_review':{}})['note_exception']['context']
        self.save_commit(b)
        p=next(p for p in LibraryService(self.db).detail(1,1)['progress'] if p['difficulty']=='HARD')
        self.assertEqual(p['best_lamp'],'AC');self.assertIsNone(p['latest_record']['lamp_status'])

    def test_migration7_failure_restores_schema_and_nonempty_history(self):
        # Build a schema 6 copy via the recorded pre-v7 backup from a real submit's catalog.
        import test_submissions as sf
        other=sf.SubmissionMigrationTests();other.setUp()
        try:
            with closing(sqlite3.connect(other.path)) as db:
                from dance_results.migrations_v6 import migrate_v5
                migrate_v5(db,other.path,new_database=True);before=audit(db)
                with patch('dance_results.migrations_v7.check_integrity',side_effect=[None,RuntimeError('late failure')]):
                    with self.assertRaises(RuntimeError):migrate_v6(db,other.path,new_database=True)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],6);self.assertEqual(audit(db),before)
                result=migrate_v6(db,other.path)
                self.assertEqual(result['before'],result['after']);check_integrity(db)
                with closing(sqlite3.connect(result['backup_path'])) as source:self.assertEqual(audit(source),before)
        finally:other.tearDown()
