"""Phase 5B: real transactions and HTTP on disposable databases only."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen

import test_submissions as fixture
import test_result_review as review_fixture
from dance_results.achievements import feedback,empty_progress,record_summary
from dance_results.gui import create_server
from dance_results.library_service import LibraryService,LibraryError
from dance_results.result_review import ResultReviews
from dance_results.sqlite_safety import audit
from dance_results.submissions import SubmissionService


class AchievementRulesTests(unittest.TestCase):
    def test_unplayed_first_clear_has_score_and_lamp_events(self):
        f=feedback(empty_progress('HARD'),ex_score=160,lamp='CLEAR',completion='COMPLETED')
        self.assertEqual(f['primary'],'FIRST CLEAR')
        self.assertTrue(f['new_score'] and f['new_lamp'] and f['first_record'] and f['first_clear'])

    def test_acp_takes_precedence_over_simultaneous_new_score(self):
        p=empty_progress('HARD');p.update(best_lamp='AC',best_ex_score=100,record_count=2,completed_count=2)
        f=feedback(p,ex_score=110,lamp='ACP',completion='COMPLETED')
        self.assertEqual(f['primary'],'FIRST ACP');self.assertEqual(f['score_delta'],10)
        self.assertEqual(f['events'],['FIRST_ACP','NEW_SCORE','NEW_LAMP'])

    def test_failed_can_improve_score_without_faking_clear(self):
        f=feedback(empty_progress('HARD'),ex_score=200,lamp='FAILED',completion='FAILED')
        self.assertEqual(f['tone'],'SCORE');self.assertTrue(f['new_score'] and f['new_lamp'])
        self.assertEqual(f['best_lamp'],'FAILED');self.assertEqual(f['best_ex_score'],200)

    def test_equal_or_worse_record_is_saved_not_new(self):
        p=empty_progress('HARD');p.update(best_lamp='ACP',best_ex_score=100,record_count=2,completed_count=2)
        for ex,lamp in [(100,'ACP'),(90,'CLEAR'),(99,'AC')]:
            f=feedback(p,ex_score=ex,lamp=lamp,completion='COMPLETED')
            self.assertEqual(f['events'],['SAVED']);self.assertEqual(f['best_lamp'],'ACP')

    def test_legacy_record_without_feedback_does_not_invent_new(self):
        r=record_summary(dict(record_id=1,ex_score=21,max_score=22,feedback_json=None))
        self.assertIsNone(r['feedback']);self.assertIsNone(r['display']);self.assertEqual(r['max_status'],'legacy_basis_unreviewed')

    def test_score_only_update_preserves_better_lamp(self):
        p=empty_progress('HARD');p.update(best_lamp='AC',best_ex_score=160,record_count=2,completed_count=2)
        f=feedback(p,ex_score=180,lamp='CLEAR',completion='COMPLETED')
        self.assertEqual(f['primary'],'NEW SCORE');self.assertEqual(f['score_delta'],20)
        self.assertEqual(f['events'],['NEW_SCORE']);self.assertEqual(f['best_lamp'],'AC')


class AchievementSubmissionTests(unittest.TestCase):
    setUp=fixture.SubmissionTests.setUp
    tearDown=fixture.SubmissionTests.tearDown
    body=fixture.SubmissionTests.body
    approved=fixture.SubmissionTests.approved
    store=fixture.SubmissionTests.store
    commit=fixture.SubmissionTests.commit
    clone_session=fixture.SubmissionTests.clone_session

    def next_record(self,**values):
        self.clone_session()
        return self.commit(self.approved(values=review_fixture.values(**values)))['record']

    def test_clear_ac_acp_transitions_and_independent_best_records(self):
        clear=self.commit(self.approved(values=review_fixture.values(perfect=8,cool=0,bad=3,max_combo=8)))['record']
        self.assertEqual(clear['achievement_feedback']['old_best_lamp'],'UNPLAYED')
        ac=self.next_record(perfect=2,cool=9)
        f=ac['achievement_feedback'];self.assertEqual(f['old_best_lamp'],'CLEAR')
        self.assertTrue(f['new_lamp']);self.assertFalse(f['new_score'])
        detail=LibraryService(self.db).detail(1,1)
        hard=next(p for p in detail['progress'] if p['difficulty']=='HARD')
        self.assertEqual(hard['best_score_record']['record_id'],clear['record_id'])
        self.assertEqual(hard['best_lamp_record_id'],ac['record_id'])
        acp=self.next_record(perfect=11,cool=0)
        f=acp['achievement_feedback'];self.assertEqual(f['old_best_lamp'],'AC')
        self.assertEqual(f['primary'],'FIRST ACP');self.assertEqual(f['score_delta'],6)
        self.assertTrue(f['new_lamp'] and f['new_score'])

    def test_failed_higher_ex_replaces_score_only(self):
        first=self.commit(self.approved(values=review_fixture.values(perfect=8,cool=0,bad=3,max_combo=8)))['record']
        failed=self.next_record(perfect=10,cool=0,bad=0,miss=1,max_combo=10,completion_status='FAILED')
        f=failed['achievement_feedback'];self.assertEqual(f['events'],['NEW_SCORE'])
        self.assertEqual((f['best_ex_score'],f['best_lamp']),(20,'CLEAR'))
        p=next(p for p in LibraryService(self.db).detail(1,1)['progress'] if p['difficulty']=='HARD')
        self.assertEqual(p['best_score_record']['record_id'],failed['record_id'])
        self.assertEqual(p['latest_record']['record_id'],failed['record_id'])

    def test_higher_clear_score_and_better_ac_lamp_have_different_record_ids(self):
        ac=self.commit(self.approved(values=review_fixture.values(perfect=2,cool=9)))['record']
        clear=self.next_record(perfect=9,cool=0,bad=2,max_combo=9)
        self.assertEqual(clear['achievement_feedback']['events'],['NEW_SCORE'])
        p=next(p for p in LibraryService(self.db).detail(1,1)['progress'] if p['difficulty']=='HARD')
        self.assertEqual(p['best_score_record']['record_id'],clear['record_id'])
        self.assertEqual(p['best_lamp_record_id'],ac['record_id'])

    def test_first_failed_then_first_clear(self):
        self.commit(self.approved(values=review_fixture.values(completion_status='FAILED')))
        r=self.next_record(perfect=8,cool=0,bad=3,max_combo=8)
        f=r['achievement_feedback'];self.assertFalse(f['first_record']);self.assertTrue(f['first_clear'])
        self.assertEqual(f['old_best_lamp'],'FAILED');self.assertEqual(f['best_lamp'],'CLEAR')

    def test_equal_score_after_acp_only_saved(self):
        self.commit(self.approved(values=review_fixture.values(perfect=11,cool=0)))
        r=self.next_record(perfect=11,cool=0)
        self.assertEqual(r['achievement_feedback']['events'],['SAVED'])

    def test_retry_retains_original_feedback_even_after_later_play(self):
        body=self.approved();r=self.commit(body)['record']
        self.next_record(perfect=11,cool=0)
        retried=self.store().submit({**body,'submit_acknowledge':True})
        self.assertTrue(retried['idempotent'])
        self.assertEqual(retried['record']['achievement_feedback'],r['achievement_feedback'])
        self.assertEqual(self.store().history()['total'],2)

    def test_list_detail_and_restarted_service_agree(self):
        r=self.commit(self.approved())['record']
        service=LibraryService(self.db)
        listed=next(s for s in service.songs(1)['songs'] if s['song_id']==1)
        self.assertEqual(listed['progress'],service.detail(1,1)['progress'])
        fresh=LibraryService(self.db).detail(1,1)
        hard=next(p for p in fresh['progress'] if p['difficulty']=='HARD')
        self.assertEqual(hard['best_lamp'],'AC');self.assertEqual(hard['best_ex_score'],21)
        self.assertEqual(hard['latest_record']['feedback'],r['achievement_feedback'])
        self.assertTrue(hard['recently_updated'])
        for p in fresh['progress']:
            if p['difficulty']!='HARD':self.assertEqual(p['best_lamp'],'UNPLAYED')

    def test_namesakes_and_different_difficulties_do_not_share_lamps(self):
        self.commit(self.approved())
        self.clone_session()
        b=self.approved(song_id=2,relative_path='Pack A\\Folder B',values=review_fixture.values(perfect=11,cool=0))
        self.commit(b)
        self.clone_session();self.commit(self.approved(values=review_fixture.values(difficulty='NORMAL',perfect=8,cool=0,bad=3,max_combo=8)))
        service=LibraryService(self.db)
        one={p['difficulty']:p for p in service.detail(1,1)['progress']}
        two={p['difficulty']:p for p in service.detail(1,2)['progress']}
        self.assertEqual((one['HARD']['best_lamp'],one['NORMAL']['best_lamp'],two['HARD']['best_lamp']),('AC','CLEAR','ACP'))
        self.assertEqual(two['NORMAL']['best_lamp'],'UNPLAYED')

    def test_unsubmitted_pending_confirmation_is_unplayed(self):
        self.service.save(self.body())
        self.assertTrue(all(p['best_lamp']=='UNPLAYED' for p in LibraryService(self.db).detail(1,1)['progress']))

    def test_rollback_does_not_save_or_announce_achievement(self):
        b=self.approved();store=self.store();p=store.prepare(b)
        with closing(sqlite3.connect(self.db)) as db:before=audit(db)
        def fail(name):
            if name=='before_commit':raise RuntimeError('test rollback')
        with patch.object(store,'_checkpoint',side_effect=fail):
            with self.assertRaises(RuntimeError):store.submit({**b,'submit_acknowledge':True,'submission_token':p['submission_token']})
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(audit(db),before)
        r=self.commit(b)['record'];self.assertTrue(r['achievement_feedback']['first_record'])

    def test_concurrent_equal_upgrades_have_one_new_score_and_one_new_lamp(self):
        self.commit(self.approved())
        bodies=[]
        for _ in range(2):
            self.clone_session();bodies.append(self.approved(values=review_fixture.values(perfect=11,cool=0)))
        requests=[]
        for b in bodies:requests.append({**b,'submit_acknowledge':True,'submission_token':self.store().prepare(b)['submission_token']})
        results=[];errors=[]
        def submit(b):
            try:results.append(SubmissionService(LibraryService(self.db),ResultReviews(self.db,self.output,scoring_policy='independent-max-v2')).submit(b)['record'])
            except Exception as error:errors.append(error)
        workers=[threading.Thread(target=submit,args=(b,)) for b in requests]
        for worker in workers:worker.start()
        for worker in workers:worker.join(10)
        self.assertFalse(errors);self.assertEqual(len(results),2)
        self.assertEqual(sum(r['achievement_feedback']['new_score'] for r in results),1)
        self.assertEqual(sum(r['achievement_feedback']['new_lamp'] for r in results),1)

    def test_http_assets_progress_and_reduced_motion_contract(self):
        self.commit(self.approved());server=create_server(self.db,monitor_directory=self.output)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urlopen(Request(base+path,headers={'X-Dance-Token':server.session_token})) as response:return response.read()
        try:
            for page in ['/','/reviews','/history']:
                markup=get(page).decode('utf8');self.assertIn('/lamps.js',markup);self.assertIn('/lamps.css',markup)
            css=get('/lamps.css').decode('utf8');js=get('/lamps.js').decode('utf8')
            self.assertIn('prefers-reduced-motion:reduce',css);self.assertIn('data-reduced-motion',css)
            self.assertIn('idempotent||seen.has(key)',js)
            self.assertNotIn('http',css)
            listing=json.loads(get('/api/songs?root_id=1'));detail=json.loads(get('/api/songs/1?root_id=1'))
            self.assertEqual(next(s for s in listing['songs'] if s['song_id']==1)['progress'],detail['progress'])
        finally:server.shutdown();server.server_close();thread.join()
