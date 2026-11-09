"""Persisted progress projections and real isolated submission/HTTP integration."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import test_phase5d2 as fixture
from dance_results.achievements import empty_progress,feedback
from dance_results.gui import create_server
from dance_results.library_service import LibraryError
from dance_results.performance import update_summary,score_status
from dance_results.achievements import progress_for_songs
from dance_results.submissions import SubmissionService
from dance_results.sqlite_safety import audit


class SummaryTests(unittest.TestCase):
    def summary(self,old,old_lamp,new,new_lamp,**options):
        p=empty_progress('HARD');p.update(best_ex_score=old,best_lamp=old_lamp,record_count=0 if old is None else 1,completed_count=1)
        f=feedback(p,ex_score=new,lamp=new_lamp,completion='FAILED' if new_lamp=='FAILED' else 'COMPLETED',**options)
        return update_summary(dict(ex_score=new,lamp_status=new_lamp),f),f

    def test_all_four_independent_kinds(self):
        for args,kind in [((1900,'CLEAR',2029,'CLEAR'),'SCORE_UPDATE'),((2029,'CLEAR',1999,'AC'),'LAMP_UPDATE'),((1900,'CLEAR',2029,'AC'),'DOUBLE_UPDATE'),((2029,'ACP',1999,'AC'),'NO_UPDATE')]:
            with self.subTest(kind=kind):self.assertEqual(self.summary(*args)[0]['kind'],kind)

    def test_failed_high_ex_is_only_score(self):
        s,f=self.summary(1999,'AC',2029,'FAILED')
        self.assertEqual(s['score'],dict(previous=1999,current=2029,delta=30,first=False,label='NEW SCORE'))
        self.assertIsNone(s['lamp']);self.assertEqual(f['best_lamp'],'AC')

    def test_ac_to_acp_is_double_and_has_both_arrows(self):
        s,_=self.summary(1999,'AC',2029,'ACP')
        self.assertEqual((s['score']['previous'],s['score']['current'],s['score']['delta']),(1999,2029,30))
        self.assertEqual((s['lamp']['previous'],s['lamp']['current']),('AC','ACP'))

    def test_first_best_and_first_lamp_do_not_invent_zero_old_score(self):
        s,_=self.summary(None,'UNPLAYED',0,'FAILED')
        self.assertEqual(s['badges'],['FIRST BEST','FIRST LAMP'])
        self.assertIsNone(s['score']['previous']);self.assertIsNone(s['score']['delta'])
        self.assertEqual(s['score']['current'],0)

    def test_no_feedback_is_unknown_not_reconstructed(self):
        self.assertEqual(update_summary(dict(ex_score=9999,lamp_status='ACP'),None)['kind'],'UNAVAILABLE')

    def test_bad_typed_score_evidence_is_not_repaired(self):
        _,f=self.summary(1900,'CLEAR',2029,'CLEAR');f['score_update']['previous']=0
        s=update_summary(dict(ex_score=2029,lamp_status='CLEAR'),f)
        self.assertEqual(s['kind'],'NO_UPDATE');self.assertIsNone(s['score'])

    def test_missing_lamp_before_or_pending_cannot_claim_lamp_update(self):
        _,f=self.summary(1900,'CLEAR',1900,'AC');f['old_best_lamp']=None
        self.assertIsNone(update_summary(dict(ex_score=1900,lamp_status='AC'),f)['lamp'])
        f['old_best_lamp']='CLEAR'
        self.assertIsNone(update_summary(dict(ex_score=1900,lamp_status=None),f)['lamp'])

    def test_paused_and_unproven_comparability_produce_no_refresh(self):
        s,_=self.summary(1900,'CLEAR',2029,'ACP',comparison_paused=True)
        self.assertEqual(s['kind'],'NO_UPDATE')
        _,f=self.summary(1900,'CLEAR',2029,'ACP')
        self.assertEqual(update_summary(dict(ex_score=2029,lamp_status='ACP'),f,eligible=False)['kind'],'NO_UPDATE')

    def test_verified_max_only_in_main_score_status_preserves_raw_values(self):
        r=dict(ex_score=3174,max_score=3232,max_status='legacy_basis_unreviewed',display='MAX -58')
        before=copy.deepcopy(r);s=score_status(r)
        self.assertEqual(r,before);self.assertIsNone(s['max_score']);self.assertIsNone(s['display'])
        r['max_status']='confirmed';self.assertEqual(score_status(r)['display'],'MAX -58')
        r['comparison']=dict(ranking_paused=True);self.assertIsNone(score_status(r)['max_score'])


class ProgressIntegrationTests(unittest.TestCase):
    setUp=fixture.IndependentBestsTests.setUp
    tearDown=fixture.IndependentBestsTests.tearDown
    body=fixture.IndependentBestsTests.body
    store=fixture.IndependentBestsTests.store
    commit=fixture.IndependentBestsTests.commit
    approved=fixture.IndependentBestsTests.approved
    clone_session=fixture.IndependentBestsTests.clone_session
    sync_session=fixture.IndependentBestsTests.sync_session
    save=fixture.IndependentBestsTests.save
    next=fixture.IndependentBestsTests.next
    progress=fixture.IndependentBestsTests.progress
    recent=fixture.IndependentBestsTests.recent

    def sequence(self):
        rows=[self.save(850,100,50)]
        for values in [(950,0,3,7,True),(900,100,0,0,False),(1000,0,0,0,False),(900,100,0,0,False)]:
            self.next();rows.append(self.save(*values))
        return rows

    def test_persisted_four_way_progress_survives_service_restart(self):
        rows=self.sequence();history=self.store().progress_history(song_id=1,difficulty='HARD',order='asc')
        self.assertEqual([r['update_summary']['kind'] for r in history['records']],['DOUBLE_UPDATE','SCORE_UPDATE','LAMP_UPDATE','DOUBLE_UPDATE','NO_UPDATE'])
        self.assertEqual(history['records'][0]['update_summary']['badges'],['FIRST BEST','FIRST LAMP'])
        self.assertEqual(history['records'][1]['score_status']['max_score'],None)
        self.assertEqual([r['update_summary'] for r in rows],[r['update_summary'] for r in history['records']])
        self.assertFalse(history['reconstructed_events'])

    def test_best_source_ids_independent_and_lamp_only_not_recent(self):
        first=self.save(850,100,50);self.next();failed=self.save(950,0,3,7,True);self.next();ac=self.save(900,100)
        history=self.store().progress_history(song_id=1,difficulty='HARD')
        best=history['bests'][0]
        self.assertEqual(best['my_best_score']['record_id'],failed['record_id'])
        self.assertEqual(best['best_lamp_record']['record_id'],ac['record_id'])
        self.assertEqual(self.recent()[0]['record_id'],failed['record_id'])
        self.assertEqual(ac['update_summary']['lamp']['previous'],'CLEAR')

    def test_actual_time_and_timezone_order_not_confirmation_time(self):
        times=['2026-10-27T09:00:00+09:00','2026-10-27T00:01:00+00:00','2026-10-26T20:02:00-04:00'];ids=[]
        for n,t in enumerate(times):
            if n:self.next()
            self.session['ended_at']=t;self.sync_session();ids.append(self.save(850,100,50)['record_id'])
        a=self.store().progress_history(song_id=1,difficulty='HARD',order='asc',limit=2)
        b=self.store().progress_history(song_id=1,difficulty='HARD',order='asc',offset=2,limit=2)
        self.assertEqual([r['record_id'] for r in a['records']+b['records']],ids)
        self.assertEqual([r['record_id'] for r in self.store().progress_history(song_id=1,order='desc')['records']],list(reversed(ids)))

    def test_namesakes_and_difficulties_never_mix_progress(self):
        one=self.save(850,100,50);self.next();self.session['song_id']=2;self.sync_session();two=self.save(850,100,50)
        self.next();normal=self.save(850,100,50,difficulty='NORMAL')
        self.assertEqual([r['record_id'] for r in self.store().progress_history(song_id=1)['records']],[one['record_id']])
        self.assertEqual([r['record_id'] for r in self.store().progress_history(song_id=2,difficulty='HARD')['records']],[two['record_id']])
        self.assertEqual([r['record_id'] for r in self.store().progress_history(song_id=2,difficulty='NORMAL')['records']],[normal['record_id']])

    def test_cross_session_conflict_no_new_update_preserves_historical_marks(self):
        old=self.save(850,100,50);self.next();conflict=self.save(850,100,49)
        self.assertEqual(conflict['update_summary']['kind'],'NO_UPDATE')
        history=self.store().progress_history(song_id=1,order='asc')
        self.assertTrue(history['records'][0]['score_status']['comparison_paused'])
        self.assertEqual(history['records'][0]['update_summary'],old['update_summary'])

    def test_duplicate_submit_retains_one_event_and_record(self):
        b=self.approved(perfect=850,cool=100,bad=50,max_combo=950,total_notes=1000)
        store=self.store();p=store.prepare(b);body={**b,'submit_acknowledge':True,'submission_token':p['submission_token']}
        a=store.submit(body);b=store.submit(body)
        self.assertTrue(b['idempotent']);self.assertEqual(a['record']['update_summary'],b['record']['update_summary'])
        self.assertEqual(self.store().progress_history(song_id=1)['total'],1)

    def test_progress_reads_preserve_schema_all_ids_and_evidence(self):
        self.sequence()
        with closing(sqlite3.connect(self.db)) as db:before=audit(db)
        for order in ('asc','desc'):self.store().progress_history(song_id=1,order=order)
        self.store().history(song_id=1);self.recent()
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(before,audit(db));self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)

    def test_recent_double_update_keeps_one_difficulty_entry(self):
        self.sequence();r=self.recent()
        self.assertEqual(len(r),1);self.assertEqual(r[0]['difficulty'],'HARD')
        self.assertEqual(r[0]['update_summary']['kind'],'DOUBLE_UPDATE')

    def test_unknown_chart_empty_history_no_fake_records(self):
        r=self.store().progress_history(song_id=1,difficulty='EASY')
        self.assertEqual(r['records'],[]);self.assertEqual(r['total'],0)
        for args in [dict(song_id=1,order='bad'),dict(song_id=1,limit=101),dict(song_id=None),dict(song_id=999)]:
            with self.subTest(args=args),self.assertRaises(LibraryError):self.store().progress_history(**args)

    def test_http_progress_auth_projection_image_and_ui_contract(self):
        saved=self.save(850,100,50);server=create_server(self.db,monitor_directory=self.output)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start();base=f'http://127.0.0.1:{server.server_port}'
        def get(path,auth=True):
            with urlopen(Request(base+path,headers={'X-Dance-Token':server.session_token} if auth else {})) as response:return response.read()
        try:
            with self.assertRaises(HTTPError) as error:get('/api/progress-history?song_id=1',False)
            self.assertEqual(error.exception.code,403)
            progress=json.loads(get('/api/progress-history?song_id=1&difficulty=HARD&order=asc'))
            self.assertEqual(progress['records'][0]['record_id'],saved['record_id'])
            self.assertEqual(get('/api/history/image?record_id='+str(saved['record_id'])),self.store().image(saved['record_id']))
            markup=get('/history').decode('utf8');css=get('/history.css').decode('utf8');js=get('/lamps.js').decode('utf8')
            for value in ['CLEAR STATUS','SCORE STATUS','本次更新']:self.assertIn(value,markup)
            self.assertIn('max-width:760px',css);self.assertIn('grid-column:1/-1',css)
            for value in ['progressHistory','DOUBLE UPDATE','score-refresh','idempotent||seen.has(key)']:self.assertIn(value,js)
            motion=get('/lamps.css').decode('utf8')
            self.assertIn('html[data-reduced-motion=true] .score-refresh',motion)
            self.assertIn('prefers-reduced-motion:reduce',motion)
        finally:server.shutdown();server.server_close();worker.join()


class RealSnapshotTests(unittest.TestCase):
    def test_actual_2029_clear_and_1999_ac_are_independent_with_unchanged_evidence(self):
        snapshot=Path('reports/phase5e/baseline.sqlite3').resolve()
        if not snapshot.exists():self.skipTest('Read-only real-record snapshot unavailable')
        with closing(sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;before=audit(db)
            p=next(p for p in progress_for_songs(db,[127])[127] if p['difficulty']=='HARD')
            self.assertEqual((p['best_ex_score'],p['best_score_record']['record_id']),(2029,18))
            self.assertEqual((p['best_lamp'],p['best_lamp_record_id']),('AC',19))
            old=SubmissionService._record(db,18);new=SubmissionService._record(db,19)
            self.assertEqual(old['score_status']['display'],'MAX -7')
            self.assertEqual(new['score_status']['display'],'MAX -37')
            self.assertEqual(new['update_summary']['kind'],'LAMP_UPDATE')
            self.assertIsNone(new['update_summary']['score'])
            self.assertEqual((new['update_summary']['lamp']['previous'],new['update_summary']['lamp']['current']),('CLEAR','AC'))
            self.assertEqual(before,audit(db))
