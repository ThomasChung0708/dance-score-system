"""Independent score/lamp updates on isolated databases, including real snapshots."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import test_phase5c2 as fixture
from dance_results.achievements import progress_for_songs
from dance_results.database import LibraryDatabase
from dance_results.recent_updates import recent_updates
from dance_results.sqlite_safety import audit,check_integrity
from dance_results.migrations_v10 import migrate_v9


class IndependentBestsTests(unittest.TestCase):
    setUp=fixture.CustomMaxTests.setUp
    tearDown=fixture.CustomMaxTests.tearDown
    body=fixture.CustomMaxTests.body
    store=fixture.CustomMaxTests.store
    commit=fixture.CustomMaxTests.commit
    approved=fixture.CustomMaxTests.approved
    clone_session=fixture.CustomMaxTests.clone_session
    sync_session=fixture.CustomMaxTests.sync_session

    def save(self,p,c,b=0,m=0,failed=False,difficulty='HARD'):
        notes=p+c+b+m
        with self.store().library.open() as lib:
            gd=lib.connection.execute('SELECT total_notes FROM game_difficulties WHERE song_id=1 AND difficulty=? AND notes_verified=1',(difficulty,)).fetchone()
        if gd:notes=gd[0] # Reference remains unchanged even when judged totals conflict.
        elif failed:notes=None
        return self.commit(self.approved(perfect=p,cool=c,bad=b,miss=m,max_combo=p+c,
            total_notes=notes,completion_status='FAILED' if failed else 'COMPLETED',difficulty=difficulty))['record']

    def next(self):self.clone_session();self.session['selection']=None;self.sync_session()

    def progress(self):
        with self.store().library.open() as lib:return next(p for p in progress_for_songs(lib.connection,[1])[1] if p['difficulty']=='HARD')

    def recent(self):
        with self.store().library.open() as lib:return recent_updates(lib.connection)['updates']

    def test_lower_score_better_lamp_separate_sources_and_lamp_only_event(self):
        first=self.save(1400,200,400);self.next();second=self.save(900,1100)
        p=self.progress();self.assertEqual((p['best_ex_score'],p['best_lamp']),(3000,'AC'))
        self.assertEqual(p['best_score_record']['record_id'],first['record_id'])
        self.assertEqual(p['best_lamp_record']['record_id'],second['record_id'])
        f=second['achievement_feedback'];self.assertIsNone(f['score_update']);self.assertEqual(f['lamp_update']['type'],'LAMP_UPDATE')
        self.assertEqual(self.recent()[0]['record_id'],first['record_id'])

    def test_higher_score_same_lamp_score_only(self):
        self.save(800,100,100);self.next();r=self.save(850,100,50)
        f=r['achievement_feedback'];self.assertEqual(f['events'],['NEW_SCORE'])
        self.assertEqual(f['score_update']['improvement'],100);self.assertIsNone(f['lamp_update'])

    def test_higher_score_lower_lamp_score_only(self):
        first=self.save(700,300);self.next();r=self.save(900,0,100)
        self.assertEqual(r['achievement_feedback']['events'],['NEW_SCORE'])
        p=self.progress();self.assertEqual(p['best_lamp'],'AC');self.assertEqual(p['best_lamp_record_id'],first['record_id'])

    def test_failed_1900_beats_clear_1800_without_max_or_lamp_downgrade(self):
        old=self.save(850,100,50);self.next();r=self.save(950,0,3,7,True)
        self.assertEqual(r['ex_score'],1900);self.assertEqual(r['lamp_status'],'FAILED')
        for k in ('max_score','max_difference','percentage','total_notes'):self.assertIsNone(r[k])
        self.assertEqual(r['achievement_feedback']['events'],['NEW_SCORE'])
        p=self.progress();self.assertEqual((p['best_ex_score'],p['best_lamp']),(1900,'CLEAR'))
        self.assertEqual(p['best_lamp_record_id'],old['record_id'])
        recent=self.recent()[0];self.assertEqual(recent['lamp_status'],'FAILED');self.assertIsNone(recent['display'])

    def test_failed_lower_score_changes_neither(self):
        old=self.save(850,100,50);self.next();r=self.save(500,0,3,7,True)
        self.assertEqual(r['achievement_feedback']['events'],['SAVED'])
        self.assertEqual(self.progress()['best_ex_score'],1800);self.assertEqual(self.recent()[0]['record_id'],old['record_id'])

    def test_first_failed_establishes_ex_and_failed_lamp_with_no_max(self):
        r=self.save(20,1,2,7,True);f=r['achievement_feedback']
        self.assertTrue(f['score_update']['first_best']);self.assertEqual(f['lamp_update']['current'],'FAILED')
        self.assertIsNone(r['max_score']);self.assertEqual(self.progress()['best_ex_score'],41)
        self.assertEqual(self.progress()['best_lamp'],'FAILED');self.assertEqual(self.recent()[0]['update_type'],'FIRST BEST')

    def test_ac_acp_both_then_lower_ac_neither(self):
        self.save(990,10);self.next();acp=self.save(1000,0)
        f=acp['achievement_feedback'];self.assertTrue(f['score_update'] and f['lamp_update'])
        self.next();r=self.save(990,10);self.assertEqual(r['achievement_feedback']['events'],['SAVED'])
        self.assertEqual(self.recent()[0]['record_id'],acp['record_id'])

    def test_equal_ex_better_lamp_only_equal_both_saved(self):
        first=self.save(900,0,100);self.next();r=self.save(800,200)
        self.assertEqual(r['achievement_feedback']['events'],['FIRST_AC','NEW_LAMP'])
        self.assertEqual(self.recent()[0]['record_id'],first['record_id'])
        self.next();r=self.save(800,200);self.assertEqual(r['achievement_feedback']['events'],['SAVED'])

    def test_conflicting_completed_totals_block_even_higher_failed_ex(self):
        old=self.save(850,100,50);self.next();self.save(850,100,49)
        self.next();r=self.save(950,0,3,7,True)
        self.assertTrue(r['comparison']['ranking_paused']);self.assertEqual(r['achievement_feedback']['events'],['SAVED'])
        self.assertEqual(self.progress()['best_score_record']['record_id'],old['record_id'])
        self.assertEqual(self.recent(),[])

    def test_recognized_max_policies_bridge_ex_not_max(self):
        self.service.scoring_policy='independent-max-v2';old=self.save(850,100,50)
        self.service.scoring_policy='custom-judged-v1';self.next();new=self.save(800,100,100)
        self.assertEqual(new['achievement_feedback']['events'],['SAVED'])
        self.assertEqual(self.progress()['best_ex_score'],1800)
        self.assertEqual(self.progress()['best_score_record']['record_id'],old['record_id'])

    def test_retry_keeps_typed_updates_and_latest_low_score_is_not_best(self):
        b=self.approved(perfect=850,cool=100,bad=50,max_combo=950,total_notes=1000)
        store=self.store();p=store.prepare(b);req={**b,'submit_acknowledge':True,'submission_token':p['submission_token']}
        first=store.submit(req);second=store.submit(req);self.assertTrue(second['idempotent'])
        self.assertEqual(first['record']['achievement_feedback'],second['record']['achievement_feedback'])
        self.next();last=self.save(800,100,100);p=self.progress()
        self.assertEqual(p['best_score_record']['record_id'],first['record']['record_id'])
        self.assertEqual(p['latest_record']['record_id'],last['record_id'])

    def test_difficulties_independent_and_no_rewrite_of_prior_evidence(self):
        old=self.save(850,100,50)
        with self.store().library.open() as lib:before=lib.connection.execute('SELECT * FROM play_records WHERE record_id=?',(old['record_id'],)).fetchone()
        self.next();self.save(1000,0,difficulty='NORMAL')
        with self.store().library.open() as lib:self.assertEqual(tuple(before),tuple(lib.connection.execute('SELECT * FROM play_records WHERE record_id=?',(old['record_id'],)).fetchone()))
        self.assertEqual(self.progress()['best_ex_score'],1800)

    def test_same_max_with_unrecognized_version_is_not_comparable(self):
        self.save(850,100,50)
        from uuid import uuid4
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            ps=dict(db.execute('SELECT * FROM play_sessions').fetchone());ps['session_id']=str(uuid4())
            db.execute('INSERT INTO play_sessions('+','.join(ps)+') VALUES('+','.join('?' for _ in ps)+')',tuple(ps.values()))
            r=dict(db.execute('SELECT * FROM play_records').fetchone());r.pop('record_id')
            r.update(session_id=ps['session_id'],review_id=str(uuid4()),perfect=950,cool=0,ex_score=1900,max_difference=100,percentage=95)
            e=json.loads(r['evidence_json']);e['scoring_version']='unknown-future-rule';e['values'].update(perfect=950,cool=0)
            r['evidence_json']=json.dumps(e)
            db.execute('INSERT INTO play_records('+','.join(r)+') VALUES('+','.join('?'for _ in r)+')',tuple(r.values()));db.commit()
        self.assertEqual(self.progress()['best_ex_score'],1800)

    def test_best_lamp_uses_first_achievement_date_not_latest_score(self):
        self.save(990,10);self.next()
        self.session['ended_at']='2026-10-22T00:01:00+00:00';self.sync_session()
        earlier=self.save(990,10)
        self.assertEqual(earlier['achievement_feedback']['events'],['SAVED'])
        self.assertEqual(self.progress()['best_lamp_record_id'],earlier['record_id'])

    def test_failed_partial_count_not_compared_to_completed_full_range(self):
        self.save(850,100,50);self.next();r=self.save(951,0,0,0,True)
        self.assertFalse(r['comparison']['ranking_paused'])
        self.assertIsNone(r['max_score']);self.assertEqual(r['achievement_feedback']['score_delta'],102)

    def test_conflicting_full_max_within_one_policy_is_explicitly_paused(self):
        from dance_results.score_comparison import comparison
        self.save(850,100,50)
        with self.store().library.open() as lib:
            c=comparison(lib.connection,1,'HARD',max_basis_notes=1001,max_basis_policy='custom-judged-v1')
            self.assertTrue(c['ranking_paused']);self.assertEqual(c['verified_full_bases']['custom-judged-v1'],[1000,1001])


class SnapshotMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'copy.sqlite3'
        source=Path('reports/phase5d2/baseline.sqlite3').resolve()
        if not source.exists():self.skipTest('Read-only production snapshot not available')
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(self.path)) as dst:src.backup(dst)

    def tearDown(self):self.temp.cleanup()

    def test_actual_zhou_records_same_identity_keep_3174_and_record7_lamp(self):
        with closing(sqlite3.connect(self.path)) as db:before=audit(db)
        with LibraryDatabase(self.path) as lib:
            m=lib.migration_results[-1];self.assertEqual(m['before'],m['after'])
            self.assertEqual(m['from_version'],9);self.assertTrue(Path(m['backup_path']).exists())
            p=next(p for p in progress_for_songs(lib.connection,[2726])[2726] if p['difficulty']=='HARD')
            self.assertEqual(p['best_ex_score'],3174);self.assertEqual(p['best_score_record']['record_id'],7)
            self.assertEqual(p['best_lamp_record_id'],7);self.assertEqual(p['best_lamp'],'CLEAR')
            self.assertIsNone(p['best_score_record']['display']) # Legacy MAX proof absent; EX is still confirmed.
            self.assertEqual(lib.connection.execute('SELECT ex_score FROM play_records WHERE record_id=15').fetchone()[0],3067)
            changes=m['derived_bests_changes'];self.assertTrue(any(c['before'][0]==2601 for c in changes))
            recent=next(r for r in recent_updates(lib.connection)['updates'] if r['song_id']==2726)
            self.assertEqual(recent['record_id'],7)
            self.assertIsNone(recent['display'])

    def test_migration_rollback_restores_view_triggers_and_all_rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            before=audit(db);ddl=db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
            with patch('dance_results.migrations_v10.check_integrity',side_effect=[None,RuntimeError('rollback')]):
                with self.assertRaisesRegex(RuntimeError,'rollback'):migrate_v9(db,self.path)
            self.assertEqual(audit(db),before);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],9)
            self.assertEqual(ddl,db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall());check_integrity(db)
