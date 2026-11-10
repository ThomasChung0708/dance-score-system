"""Phase 5A: real SQLite transactions on disposable catalogs, never production."""
import copy
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
from unittest.mock import patch
from uuid import uuid4
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from PIL import Image
import test_result_review as fixture
import test_gui as migration_fixture
from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryService,LibraryError
from dance_results.migrations_v4 import migrate_v3
from dance_results.migrations_v5 import migrate_v4
from dance_results.migrations_v6 import migrate_v5
from dance_results.monitor_tracking import atomic_json
from dance_results.result_ocr import parse_result
from dance_results.result_review import ResultReviews
from dance_results.scoring import calculate as _calculate
from functools import partial
calculate=partial(_calculate,max_basis={})
from dance_results.scoring import percentage_display
from dance_results.sqlite_safety import audit,projection,check_integrity
from dance_results.submissions import SubmissionService


class ScoringCoreTests(unittest.TestCase):
    def test_integer_score_and_display_boundaries_with_decimal_half_up(self):
        for p,c,b,expected in [(1995,5,0,'MAX -5'),(1850,150,0,'MAX -150'),(1849,151,0,'96.23%')]:
            v=fixture.values(perfect=p,cool=c,bad=b,miss=0,max_combo=2000,total_notes=2000)
            score=calculate(v,notes_verified=True,identity_confirmed=True,max_basis=dict(notes=v['total_notes'],verified=True))
            self.assertEqual(score['display'],expected)
            self.assertIs(type(score['ex_score']),int);self.assertIs(type(score['difference']),int)
        self.assertEqual(score['percentage'],96.225)

    def test_unknown_zero_negative_and_bool_never_generate_percentage(self):
        for total in (None,0,-1,True):
            score=calculate(fixture.values(total_notes=total),notes_verified=True,identity_confirmed=True)
            self.assertIsNone(score['max_score']);self.assertIsNone(score['percentage'])
        self.assertIsNone(percentage_display(0,0))
        self.assertIsNone(calculate(fixture.values(cool=None),notes_verified=True,identity_confirmed=True)['ex_score'])

    def test_acp_ac_clear_failed_require_verified_completion(self):
        for changes,expected in [({'perfect':11,'cool':0},'ACP'),({},'AC'),
            ({'perfect':9,'bad':1,'max_combo':10},'CLEAR'),({'completion_status':'FAILED'},'FAILED')]:
            self.assertEqual(calculate(fixture.values(**changes),notes_verified=True,identity_confirmed=True,max_basis=dict(notes=11,verified=True))['lamp_preview'],expected)
        for changes in ({'completion_status':None},{'perfect':0,'cool':0,'bad':0,'miss':0,'max_combo':0,'total_notes':None}):
            self.assertIsNone(calculate(fixture.values(**changes),notes_verified=False,identity_confirmed=True)['lamp_preview'])


class SubmissionTests(unittest.TestCase):
    setUp=fixture.ReviewPersistenceTests.setUp
    tearDown=fixture.ReviewPersistenceTests.tearDown
    body=fixture.ReviewPersistenceTests.body

    def store(self):return SubmissionService(LibraryService(self.db),self.service)

    def approved(self,**changes):
        body=self.body(**changes);saved=self.service.save(body)
        body.update(revision=saved['revision'],play_mode='single')
        return body

    def commit(self,body,store=None):
        store=store or self.store();preview=store.prepare(body)
        self.assertTrue(preview['can_submit'],preview.get('issues'))
        return store.submit({**body,'submission_token':preview['submission_token'],'submit_acknowledge':True})

    def clone_session(self):
        was_automatic = bool(getattr(self, 'row', {}).get('ocr', {}).get('temporal_validation'))
        sid=str(uuid4());session=copy.deepcopy(self.session);session['session_id']=sid
        folder=self.output/'sessions'/sid;folder.mkdir(parents=True)
        shutil.copyfile(self.output/'sessions'/self.sid/'result.png',folder/'result.png')
        self.sid=sid;self.session=session
        ocr = self.ocr
        if was_automatic:
            from test_phase5c import attach_auto_frames
            prepared = attach_auto_frames(self, dict(ocr=copy.deepcopy(ocr)), persist_review=False)
            ocr = prepared['ocr']
        atomic_json(folder/'session.json',session);self.row=self.service.ingest(session,ocr)

    def test_completed_submission_backup_snapshot_core_and_history(self):
        body=self.approved();result=self.commit(body);record=result['record']
        self.assertEqual(record['ex_score'],21);self.assertEqual(record['max_score'],22)
        self.assertEqual(record['max_difference'],1);self.assertEqual(record['lamp_status'],'AC')
        self.assertEqual(record['game_level'],17);self.assertEqual(record['max_combo'],11)
        self.assertEqual(record['review_id'],self.row['review_id']);self.assertEqual(record['confirmation_method'],'manual_review')
        self.assertEqual(record['evidence']['review']['ocr'],self.ocr)
        self.assertTrue(Path(result['backup_path']).exists())
        with closing(sqlite3.connect(result['backup_path'])) as backup:
            self.assertEqual(backup.execute('SELECT count(*) FROM play_records').fetchone()[0],0)
        self.assertEqual(self.store().history()['total'],1)
        self.assertEqual(self.store().image(record['record_id']),self.service.image(self.sid))

    def test_same_review_retries_after_restart_return_same_record(self):
        body=self.approved();store=self.store();prepared=store.prepare(body)
        request={**body,'submission_token':prepared['submission_token'],'submit_acknowledge':True}
        first=store.submit(request);again=self.store().submit(request)
        self.assertEqual(first['record']['record_id'],again['record']['record_id']);self.assertTrue(again['idempotent'])
        self.assertEqual(self.store().history()['total'],1)
        self.assertEqual(len(list((self.base/'backups').glob('*.pre-submit.*'))),1)

    def test_concurrent_services_use_sqlite_unique_session_and_review(self):
        body=self.approved();preview=self.store().prepare(body)
        request={**body,'submission_token':preview['submission_token'],'submit_acknowledge':True}
        results=[];errors=[]
        def work():
            try:
                independent=SubmissionService(LibraryService(self.db),ResultReviews(self.db,self.output,scoring_policy='independent-max-v2'))
                results.append(independent.submit(copy.deepcopy(request)))
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=work)for _ in range(3)]
        for t in threads:t.start()
        for t in threads:t.join(15)
        self.assertEqual(errors,[]);self.assertEqual(len(results),3)
        self.assertEqual(len({r['record']['record_id']for r in results}),1)
        self.assertEqual(sum(r['idempotent']for r in results),2)

    def test_same_score_same_image_different_sessions_are_separate(self):
        first=self.commit(self.approved())['record'];self.clone_session()
        second=self.commit(self.approved())['record']
        self.assertNotEqual(first['record_id'],second['record_id'])
        self.assertEqual(first['screenshot_sha256'],second['screenshot_sha256'])
        self.assertEqual(self.store().history()['total'],2)

    def test_changed_submission_never_overwrites_existing_record(self):
        body=self.approved();first=self.commit(body)['record']
        changed={**body,'values':fixture.values(perfect=9,cool=2),'submit_acknowledge':True}
        with self.assertRaises(LibraryError):self.store().submit(changed)
        self.assertEqual(self.store().detail(first['record_id'])['ex_score'],21)

    def test_missing_cool_unknown_status_or_unsaved_correction_blocked(self):
        body=self.approved()
        for changes in ({'cool':None},{'completion_status':None},{'perfect':9,'cool':2}):
            preview=self.store().prepare({**body,'values':fixture.values(**changes)})
            self.assertFalse(preview['can_submit'])
        self.assertEqual(self.store().history()['total'],0)

    def test_only_saved_human_confirmation_can_submit(self):
        body=self.body(play_mode='single')
        self.assertFalse(self.store().prepare(body)['can_submit'])
        self.service.save({**body,'confirm':False})
        self.assertFalse(self.store().prepare({**body,'revision':1})['can_submit'])

    def test_completed_unknown_or_mismatched_full_notes_cannot_submit(self):
        body=self.approved(values=fixture.values(total_notes=None),notes_verified=False)
        preview=self.store().prepare(body)
        self.assertTrue(preview['can_submit'])
        self.assertIsNone(preview['summary']['score']['max_score'])
        with self.assertRaises(LibraryError):self.service.save(self.body(revision=1,values=fixture.values(total_notes=12)))

    def test_failed_unknown_full_notes_stores_null_max_and_valid_ex_best(self):
        v=fixture.values(perfect=585,cool=32,bad=2,miss=45,max_combo=238,game_score=35575,grade='F',
                         game_level=38,total_notes=None,completion_status='FAILED')
        body=self.approved(values=v,notes_verified=False)
        record=self.commit(body)['record']
        self.assertEqual(record['ex_score'],1202);self.assertEqual(record['lamp_status'],'FAILED')
        for key in ('total_notes','max_score','max_difference','percentage'):self.assertIsNone(record[key])
        best=self.store().history(song_id=1)['bests'][0]
        self.assertEqual(best['highest_ex_score'],1202);self.assertEqual(best['best_clear_lamp'],'FAILED')

    def test_best_ex_and_lamp_are_independent_and_verified_failed_improves_score(self):
        self.commit(self.approved(values=fixture.values(perfect=8,cool=0,bad=3,max_combo=8)))
        self.clone_session();self.commit(self.approved(values=fixture.values(perfect=2,cool=9,max_combo=11)))
        self.clone_session();self.commit(self.approved(values=fixture.values(perfect=10,cool=0,miss=1,max_combo=10,completion_status='FAILED')))
        best=self.store().history(song_id=1)['bests'][0]
        self.assertEqual(best['highest_ex_score'],20);self.assertEqual(best['best_clear_lamp'],'AC')
        self.assertEqual(self.store().history()['total'],3)

    def test_difficulties_and_same_title_song_ids_stay_independent(self):
        self.commit(self.approved())
        self.clone_session();self.commit(self.approved(values=fixture.values(difficulty='EASY')))
        self.clone_session()
        song=self.service.candidates('JOINT')['candidates'][1]
        self.commit(self.approved(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_path']))
        self.assertEqual(self.store().history(song_id=1)['total'],2)
        self.assertEqual(self.store().history(song_id=1,difficulty='EASY')['total'],1)
        self.assertEqual(self.store().history(song_id=song['song_id'])['total'],1)

    def test_official_k_submission_preserves_identity_and_does_not_invent_difficulty(self):
        import test_official_import as official_fixture
        from dance_results.official_import import import_catalog,read_catalog
        row=official_fixture.raw_row(title='JOINT',lvEasy='0',notesEasy='0',lvNormal='0',notesNormal='0',lvHard='17',notesHard='11')
        import_catalog(self.db,read_catalog(official_fixture.csv_bytes([row]),'official-test.csv'))
        song=self.service.candidates('JOINT','official')['candidates'][0]
        record=self.commit(self.approved(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_path']))['record']
        self.assertEqual(record['source_type'],'official')
        self.assertEqual(record['song_id'],song['song_id']);self.assertIsNone(record['pack_name'])
        self.assertEqual(record['evidence']['full_notes_source'],'official_csv')
        self.assertEqual(self.store().history(song_id=1)['total'],0)
        self.clone_session()
        with self.assertRaises(LibraryError):
            self.approved(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_path'],values=fixture.values(difficulty='EASY'))

    def test_multiplayer_second_row_only_and_host_cannot_be_saved_under_own_name(self):
        rows=fixture.result_tokens()
        rows=[dict(t,text='HOST')if t['text']=='TC' else t for t in rows]
        rows += [fixture.token('TC',185,318),fixture.token('10',368,318),fixture.token('9',480,318),fixture.token('2',556,318),
                 fixture.token('0',635,318),fixture.token('0',713,318),fixture.token('850',888,318),fixture.token('A+',980,318)]
        ocr=parse_result(rows,'Nobody');ocr['tokens']=rows
        (self.output/'sessions'/self.sid/'result-review.json').unlink();self.service.ingest(self.session,ocr)
        body=self.approved(values={**fixture.values(perfect=9,cool=2,max_combo=10,game_score=850),'player_name':'TC'},player_row_id='row-1')
        body['play_mode']='multiplayer';record=self.commit(body)['record']
        self.assertEqual(record['player_name'],'TC');self.assertEqual(record['perfect'],9)
        self.assertEqual(record['evidence']['player_row_id'],'row-1');self.assertEqual(record['play_mode'],'multiplayer')
        self.clone_session();(self.output/'sessions'/self.sid/'result-review.json').unlink();self.service.ingest(self.session,ocr)
        wrong=self.approved(values={**fixture.values(),'player_name':'TC'},player_row_id='row-0');wrong['play_mode']='multiplayer'
        preview=self.store().prepare(wrong)
        self.assertFalse(preview['can_submit']);self.assertIn('player_row_name_conflict',{i['code']for i in preview['issues']})

    def test_catalog_metadata_change_after_preview_rejected(self):
        body=self.approved();store=self.store();preview=store.prepare(body)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source) VALUES(1,'HARD',99,'manual')");db.commit()
        with self.assertRaises(LibraryError):store.submit({**body,'submission_token':preview['submission_token'],'submit_acknowledge':True})
        self.assertEqual(store.history()['total'],0)

    def test_modified_image_or_session_blocks_submit(self):
        body=self.approved();store=self.store();preview=store.prepare(body)
        path=self.output/'sessions'/self.sid/'session.json';session=json.loads(path.read_text(encoding='utf8'));session['started_at']='2026-10-23T01:00:00+00:00';atomic_json(path,session)
        self.assertFalse(store.prepare(body)['can_submit'])
        with self.assertRaises(LibraryError):store.submit({**body,'submission_token':preview['submission_token'],'submit_acknowledge':True})
        with (path.parent/'result.png').open('ab') as f:f.write(b'changed')
        with self.assertRaises(LibraryError):store.prepare(body)

    def test_transaction_failure_rolls_back_session_difficulty_and_record(self):
        body=self.approved();store=self.store();prepared=store.prepare(body)
        with closing(sqlite3.connect(self.db)) as db:before=audit(db)
        for checkpoint in ('after_difficulty','after_session','after_record','before_commit'):
            def fail(name):
                if name==checkpoint:raise RuntimeError('injected '+checkpoint)
            with patch.object(store,'_checkpoint',side_effect=fail):
                with self.assertRaises(RuntimeError):store.submit({**body,'submission_token':prepared['submission_token'],'submit_acknowledge':True})
            with closing(sqlite3.connect(self.db)) as db:self.assertEqual(audit(db),before);check_integrity(db)
        self.assertEqual(self.commit(body)['record']['record_id'],1)

    def test_backup_failure_does_not_write_anything(self):
        body=self.approved();store=self.store();prepared=store.prepare(body)
        with closing(sqlite3.connect(self.db)) as db:before=audit(db)
        with patch('dance_results.submissions.verified_backup',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):store.submit({**body,'submission_token':prepared['submission_token'],'submit_acknowledge':True})
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(audit(db),before)

    def test_database_guards_formal_record_immutability(self):
        record=self.commit(self.approved())['record']
        with closing(sqlite3.connect(self.db)) as db:
            for sql in ('UPDATE play_records SET perfect=0','DELETE FROM play_records'):
                with self.assertRaises(sqlite3.IntegrityError):db.execute(sql)
                db.rollback()
        self.assertEqual(self.store().detail(record['record_id'])['perfect'],10)

    def test_database_unique_review_also_blocks_another_session(self):
        record=self.commit(self.approved())['record']
        with closing(sqlite3.connect(self.db)) as db:
            db.row_factory=sqlite3.Row
            db.execute("INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) VALUES('different-session','TC','single','result','completed')")
            source=dict(db.execute('SELECT * FROM play_records WHERE record_id=?',(record['record_id'],)).fetchone())
            source.pop('record_id');source['session_id']='different-session'
            with self.assertRaisesRegex(sqlite3.IntegrityError,'review_id'):
                db.execute('INSERT INTO play_records('+','.join(source)+') VALUES('+','.join('?'for _ in source)+')',tuple(source.values()))
            db.rollback()
        self.assertEqual(self.store().history()['total'],1)

    def test_missing_source_image_preserves_readable_history(self):
        record=self.commit(self.approved())['record']
        (self.output/'sessions'/self.sid/'result.png').unlink()
        self.assertEqual(self.store().detail(record['record_id'])['ex_score'],21)
        with self.assertRaises(LibraryError):self.store().image(record['record_id'])

    def test_http_confirmation_auth_history_restart_and_duplicate_submit(self):
        body=self.approved();server=create_server(self.db,monitor_directory=self.output)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';headers={'X-Dance-Token':server.session_token,'Content-Type':'application/json'}
        def post(path,data):return json.load(urlopen(Request(base+path,data=json.dumps(data).encode(),headers=headers)))
        try:
            self.assertIn('歷史成績',urlopen(base+'/history').read().decode())
            preview=post('/api/reviews/submission-preview',body);request={**body,'submission_token':preview['submission_token'],'submit_acknowledge':True}
            with self.assertRaises(HTTPError):urlopen(Request(base+'/api/reviews/submit',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'}))
            saved=post('/api/reviews/submit',request);again=post('/api/reviews/submit',request)
            self.assertEqual(saved['record']['record_id'],again['record']['record_id'])
            history=json.load(urlopen(Request(base+'/api/history',headers=headers)));self.assertEqual(history['total'],1)
        finally:server.shutdown();server.server_close();thread.join(3)
        restored=SubmissionService(LibraryService(self.db),ResultReviews(self.db,self.output,scoring_policy='independent-max-v2'))
        self.assertEqual(restored.history()['total'],1)


class SubmissionMigrationTests(unittest.TestCase):
    def setUp(self):
        migration_fixture.ManualMigrationTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db:
            migrate_v3(db,self.path,new_database=True);migrate_v4(db,self.path,new_database=True)
            self.columns=projection(db);self.before=audit(db)

    def tearDown(self):migration_fixture.ManualMigrationTests.tearDown(self)

    def test_schema6_preserves_nonempty_history_external_fks_views_and_backup(self):
        with LibraryDatabase(self.path) as library:
            db=library.connection;migration=library.migration_results[0]
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],10)
            self.assertEqual(audit(db,self.columns)['play_records'],self.before['play_records'])
            self.assertEqual(db.execute('SELECT * FROM user_difficulties WHERE difficulty_id=301').fetchone()[0],301)
            self.assertEqual(db.execute('SELECT difficulty_id FROM extra_history').fetchone()[0],301)
            self.assertEqual(migration['before'],migration['after']);check_integrity(db)
        with closing(sqlite3.connect(migration['backup_path'])) as backup:
            self.assertEqual(audit(backup),self.before);self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],5)

    def test_late_migration_failure_rolls_back_all_added_columns_and_indexes(self):
        with closing(sqlite3.connect(self.path)) as db:
            def fail(_):raise RuntimeError('injected migration failure')
            with patch('dance_results.migrations_v6.check_integrity',side_effect=[None,RuntimeError('injected migration failure')]):
                with self.assertRaises(RuntimeError):migrate_v5(db,self.path)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
            self.assertNotIn('review_id',[r[1]for r in db.execute('PRAGMA table_info(play_records)')])
            self.assertEqual(audit(db),self.before)


if __name__=='__main__':unittest.main()
