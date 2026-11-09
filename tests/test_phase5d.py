"""Isolated download/SQLite sequences and real-image cached OCR, not live play."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from PIL import Image,ImageDraw
import test_result_review as fixture
import test_submissions as submit_fixture
import test_phase5c2 as custom_fixture
from dance_results.database import LibraryDatabase
from dance_results.library_service import LibraryService,LibraryError
from dance_results.monitor_tracking import Tracker,atomic_json,ManualReferences
from dance_results.scanner import scan_library
from dance_results.lobby_download_ocr import lobby_download
from dance_results.download_tracking import DownloadTracker,metadata_snapshot,session_download,verified_download_binding
from dance_results.recent_updates import recent_updates
from dance_results.ocr_screens import classify

BASE=Path(__file__).parent


class LobbyImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.cache=json.loads((BASE/'fixtures/phase5d_lobby_tokens.json').read_text(encoding='utf8'))

    def image(self,name):return Image.open(BASE/'專案測試圖片'/name).convert('RGB').resize((1024,800))

    def test_both_original_pngs_self_tc_no_map_and_no_invented_progress(self):
        for name,c in self.cache.items():
            self.assertEqual(hashlib.sha256((BASE/'專案測試圖片'/name).read_bytes()).hexdigest(),c['sha256'])
            self.assertEqual(classify(c['tokens'])[0],'waiting_room')
            r=lobby_download(c['tokens'],self.image(name),'TC',c['badge_tokens'])
            self.assertTrue(r['self_no_map']);self.assertFalse(r['self_ready']);self.assertEqual(r['slot'],2)
            self.assertTrue(r['multiplayer_observed']);self.assertIsNone(r['progress_percent'])
            self.assertFalse(r['progress_observed'])

    def test_host_or_other_ready_does_not_receive_tc_no_map(self):
        name='下載條.PNG';c=self.cache[name]
        host=lobby_download(c['tokens'],self.image(name),'玩家乙',c['badge_tokens'])
        other=lobby_download(c['tokens'],self.image(name),'玩家甲',c['badge_tokens'])
        self.assertFalse(host['self_no_map']);self.assertTrue(other['self_ready']);self.assertFalse(other['self_no_map'])

    def test_absent_or_duplicate_local_nickname_remains_unknown(self):
        name='下載條.PNG';c=self.cache[name];im=self.image(name)
        self.assertIsNone(lobby_download(c['tokens'],im,'Nobody')['self_no_map'])
        duplicated=[*c['tokens'],fixture.token('TC',700,203)]
        self.assertEqual(lobby_download(duplicated,im,'TC',c['badge_tokens'])['local_player_status'],'UNKNOWN')

    def test_synthetic_local_green_strip_is_observation_not_percentage(self):
        name='下載條.PNG';c=self.cache[name];im=self.image(name)
        ImageDraw.Draw(im).rectangle((390,183,440,185),fill=(0,230,0))
        r=lobby_download(c['tokens'],im,'TC',c['badge_tokens'])
        self.assertTrue(r['progress_observed']);self.assertIsNone(r['progress_percent'])

    def test_spectator_banner_split_into_two_ocr_boxes_is_not_playing(self):
        tokens=[fixture.token('Press Ctrl+o to quit look',180,50),fixture.token('on mode',410,50),fixture.token('歌曲名',50,770),fixture.token('時間',400,770)]
        self.assertEqual(classify(tokens)[0],'spectating')


class DownloadTests(unittest.TestCase):
    tearDown_fixture=fixture.ReviewPersistenceTests.tearDown
    body=fixture.ReviewPersistenceTests.body

    def setUp(self):
        fixture.ReviewPersistenceTests.setUp(self)
        self.library=LibraryService(self.db);self.callbacks=[]
        self.dl=DownloadTracker(self.library,self.service,quiet_seconds=0,interval=.02,on_update=self.callbacks.append)
        self.root=self.base/'SONG'
        self.lobby=dict(player_name='TC',slot=2,local_player_status='CONFIRMED',multiplayer_observed=True,self_no_map=True,confidence=.99)

    def tearDown(self):self.dl.close();self.tearDown_fixture()

    def begin(self):
        self.dl._begin(dict(lobby=self.lobby,room=dict(title='JOINT'),epoch=1,image=Image.new('RGB',(1024,800)),observed_at='2026-10-26T00:00:00+00:00'))
        self.dl._associate(copy.deepcopy(self.session))

    def add(self,pack='connect',folder='new',ext='sm',title='JOINT'):
        p=self.root/pack/folder/('source.'+ext);p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('#TITLE:'+title+';' if ext in ('sm','dwi') else 'osu file format v14\n[Metadata]\nTitle:'+title+'\nArtist:Writer\n',encoding='utf8')
        return p

    def poll(self):
        self.dl._poll();self.dl._poll()

    def test_connect_sm_dwi_single_folder_idempotent_import(self):
        self.begin();self.add();self.add(ext='dwi');self.poll()
        evidence=session_download(self.output,self.sid)
        self.assertTrue(evidence['scan_completed']);self.assertEqual(len(evidence['candidates']),1)
        c=evidence['candidates'][0];self.assertEqual(c['pack'],'connect');self.assertEqual(len(c['source_files']),2)
        with closing(sqlite3.connect(self.db)) as db:
            before=[tuple(r) for r in db.execute('SELECT song_id,relative_folder FROM songs ORDER BY song_id')]
            self.assertEqual(len(before),4)
        self.dl.active['_scanned']=None;self.poll()
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(before,db.execute('SELECT song_id,relative_folder FROM songs ORDER BY song_id').fetchall())
        self.assertIsNone(verified_download_binding(self.service,self.service._read(self.sid)[1]))

    def test_new_single_song_pack_and_existing_pack_both_scanned(self):
        self.begin();self.add('Brand New Pack','Only','osu');self.add('Pack A','Added');self.poll()
        candidates=session_download(self.output,self.sid)['candidates']
        self.assertEqual({c['pack'] for c in candidates},{'Brand New Pack','Pack A'})
        self.assertEqual(len({c['song_id'] for c in candidates}),2)
        self.assertNotIn('confirmed_song_id',session_download(self.output,self.sid))

    def test_source_completion_inside_existing_folder_preserves_song_id(self):
        self.begin();self.add('Pack A','Folder A','dwi');self.poll()
        self.assertEqual(session_download(self.output,self.sid)['candidates'],[])
        c=session_download(self.output,self.sid)['existing_updates'][0]
        self.assertEqual(c['song_id'],1)
        self.assertEqual(c['relative_path'],str(Path('Pack A/Folder A')))

    def test_already_started_change_uses_armed_metadata_not_title_guess(self):
        self.dl.armed=self.dl._snapshots()
        (self.root/'Pack A/Folder A/song.sm').write_text('#TITLE:JOINT;#ARTIST:changed;',encoding='utf8')
        self.begin();self.poll()
        self.assertEqual(self.dl.active['candidates'],[])
        self.assertEqual([c['song_id'] for c in self.dl.active['existing_updates']],[1])
        self.assertIsNone(verified_download_binding(self.service,self.service._read(self.sid)[1]))

    def test_downloading_partial_file_deferred_then_retried(self):
        self.begin();p=self.add();p.write_text('#TITLE:unfinished',encoding='utf8');self.poll()
        self.assertFalse(self.dl.active['scan_completed'])
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(db.execute('SELECT count(*) FROM songs').fetchone()[0],3)
        p.write_text('#TITLE:JOINT;',encoding='utf8');self.poll()
        self.assertTrue(self.dl.active['scan_completed'])

    def test_unrelated_old_incomplete_source_does_not_block_new_download(self):
        old=self.add('Old Broken Pack','Unrelated');old.write_text('#TITLE:unfinished',encoding='utf8')
        self.begin();self.add('New Ready Pack','Complete');self.poll()
        self.assertTrue(self.dl.active['scan_completed'])
        self.assertTrue(self.dl.active['scan_results'][-1]['deferred_sources'])
        self.assertEqual([c['pack'] for c in self.dl.active['candidates']],['New Ready Pack'])

    def test_metadata_and_audio_changes_reset_stability_no_content_hashes(self):
        self.begin();self.add();self.dl.quiet_seconds=100
        audio=self.root/'connect/new/a.mp3';audio.write_bytes(b'a')
        self.dl._poll();audio.write_bytes(b'ab');self.dl._poll()
        self.assertEqual(self.dl.active['attempts'],0)
        snap=metadata_snapshot(self.root)
        self.assertEqual(snap['content_hashes_computed'],0)
        self.assertEqual(snap['files'][str(audio.relative_to(self.root))]['size'],2)

    def test_scan_busy_retry_preserves_result_and_never_submits_manual(self):
        self.begin();self.add()
        with patch.object(self.library,'start_scan',side_effect=LibraryError('busy',409)):self.poll()
        self.assertTrue((self.output/'sessions'/self.sid/'result.png').exists())
        self.poll();self.assertTrue(self.dl.active['scan_completed'])
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(db.execute('SELECT count(*) FROM play_records').fetchone()[0],0)
        self.assertEqual(self.callbacks,[self.sid])

    def test_result_before_scan_kept_with_updated_manual_candidates(self):
        self.begin();self.add('Other Pack','Downloaded');self.poll()
        r=self.service.detail(self.sid)
        self.assertEqual(r['review_id'],self.row['review_id'])
        self.assertTrue(r['download_evidence']['scan_completed'])
        self.assertIn('Other Pack',{c['pack_name'] for c in r['candidates']})

    def test_duplicate_no_map_event_and_ocr_observe_is_nonblocking(self):
        tracker=Tracker(self.output,confirmations=2)
        o=dict(state='WAITING_ROOM',frame_hash='a',download_lobby=self.lobby,room_preview={'title':'JOINT'},context={})
        elapsed=[]
        with patch.object(self.dl,'_snapshots',side_effect=lambda:(time.sleep(.2) or {})):
            for i in range(5):
                tracker.consume(o,sample_id=i)
                start=time.perf_counter();self.dl.observe(o,tracker);elapsed.append(time.perf_counter()-start)
            self.dl.queue.join()
        self.assertLess(max(elapsed),.1)
        self.assertEqual(sum(e['event']=='SELF_NO_MAP' for e in self.dl.active['events']),1)

    def test_unreliable_own_row_never_triggers_snapshot(self):
        tracker=Tracker(self.output,confirmations=2);tracker.count=2
        o=dict(state='WAITING_ROOM',download_lobby=dict(self.lobby,local_player_status='UNKNOWN',self_no_map=None),room_preview={})
        self.dl.observe(o,tracker);self.assertIsNone(self.dl.active);self.assertTrue(self.dl.queue.empty())

    def test_metadata_only_trigger_does_not_invent_no_map_and_restart_baseline(self):
        self.dl.armed=self.dl._snapshots();self.dl.last_lobby=dict(self.lobby,self_no_map=False)
        self.add('Fresh Pack','No Badge');self.dl._associate(copy.deepcopy(self.session))
        self.assertFalse(self.dl.active['self_no_map_confirmed'])
        self.assertNotIn('SELF_NO_MAP',[r['event'] for r in self.dl.active['events']])
        saved=json.loads(Path(self.dl.active['baseline_path']).read_text(encoding='utf8'))
        self.assertEqual(saved,self.dl.armed)
        self.poll();self.assertTrue(self.dl.active['scan_completed'])
        self.assertIsNone(verified_download_binding(self.service,self.service._read(self.sid)[1]))

    def test_expired_result_window_does_not_attribute_later_files(self):
        self.begin();self.add();self.dl.active['_result_deadline']=time.monotonic()-1;self.poll()
        self.assertFalse(self.dl.active['scan_completed']);self.assertEqual(self.dl.active['attempts'],0)

    def test_explicit_retry_after_expiry_reopens_bounded_wait_while_stopped(self):
        self.begin();self.add();self.dl.active['_result_deadline']=time.monotonic()-1
        self.dl.retry(self.sid);self.dl.queue.join();deadline=time.monotonic()+5
        while time.monotonic()<deadline and not self.dl.status().get('scan_completed'):time.sleep(.02)
        self.assertTrue(self.dl.status()['scan_completed'])
        self.assertFalse(self.dl.active['_manual_retry'])

    def test_restart_manual_retry_loads_snapshot_and_preserves_review(self):
        self.begin();self.add();self.poll();episode=self.dl.active['episode_id'];self.dl.close()
        self.dl=DownloadTracker(self.library,self.service,quiet_seconds=0,interval=.02)
        self.dl.retry(self.sid)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not self.dl.status().get('scan_completed'):time.sleep(.02)
        self.assertTrue(self.dl.status()['scan_completed']);self.assertEqual(self.dl.active['episode_id'],episode)
        self.assertEqual(self.service._read(self.sid)[1]['review_id'],self.row['review_id'])

    def test_single_changed_folder_without_pack_lv_notes_time_never_binds(self):
        self.begin();self.add('Solo Pack','One');self.poll()
        self.assertEqual(len(self.dl.active['candidates']),1)
        self.assertIsNone(verified_download_binding(self.service,self.service._read(self.sid)[1]))

    def test_verified_download_binding_second_local_row_uses_existing_submit_service(self):
        from dance_results.ocr_matching import ReadOnlyCatalog
        from dance_results.result_ocr import parse_result
        from dance_results.submissions import SubmissionService
        from dance_results.ocr_screens import center
        self.service.scoring_policy='custom-judged-v1'
        (self.root/'Pack A/Folder B/song.sm').write_text('#TITLE:Other;',encoding='utf8')
        with LibraryDatabase(self.db) as lib:
            lib.sync(scan_library(self.root))
            lib.connection.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(1,'HARD',17,'manual',11,'manual',1)")
            lib.connection.commit()
        self.session['selection'].update(source_type='unknown',pack=dict(pack_id=1,status='confirmed',name='Pack A'))
        self.session.update(song_id=None,matching_status='pending_manual_review')
        self.session['result']['frame_hash']='a'*64
        fields=self.session['selection']['fields']
        cat=ReadOnlyCatalog(self.db)
        try:ManualReferences(self.output,self.db).add(fields,cat.songs[1],'controlled_prior_verified_mapping')
        finally:cat.close()
        tokens=fixture.result_tokens()
        tokens=[dict(t,box=[[x,y+68]for x,y in t['box']]) if center(t)[1]==250 else t for t in tokens]
        tokens.extend(fixture.token(text,x,250) for text,x in [('Host',185),('99',368),('99',480),('0',556),('0',635),('0',713),('9999',888),('A+',980)])
        self.ocr=parse_result(tokens,'TC');self.ocr['tokens']=tokens
        folder,row=self.service._read(self.sid);row.update(locked_session=copy.deepcopy(self.session),ocr=self.ocr)
        row['draft']['song_id']=None
        atomic_json(folder/'session.json',self.session);atomic_json(folder/'result-review.json',row)
        self.begin();source=self.add('Pack A','Folder A','dwi');self.poll()
        binding=verified_download_binding(self.service,self.service._read(self.sid)[1])
        self.assertEqual(binding['song_id'],1)
        store=SubmissionService(self.library,self.service);store.auto_allowed=lambda sid:True
        response=store.submit_verified(self.sid);r=response['record']
        self.assertEqual((r['player_name'],r['perfect'],r['cool'],r['ex_score']),('TC',10,1,21))
        self.assertEqual(r['evidence']['download_evidence']['episode_id'],self.dl.active['episode_id'])
        self.assertEqual(r['confirmation_method'],'verified_auto')
        source.write_text('#TITLE:JOINT;#ARTIST:changed;',encoding='utf8')
        self.assertIsNone(verified_download_binding(self.service,self.service._read(self.sid)[1]))

    def test_no_map_playing_result_sequence_preserves_pending_capture(self):
        tr=Tracker(self.output,confirmations=2);self.dl.enabled=True
        waiting=dict(state='WAITING_ROOM',frame_hash='wait',download_lobby=self.lobby,room_preview=dict(title='JOINT'),context={})
        image=Image.new('RGB',(1024,800),'gray')
        for i in (1,2):tr.consume(waiting,sample_id=i,image=image);self.dl.observe(waiting,tr,image)
        self.dl.queue.join();self.add('Late Pack','New Song')
        playing=dict(state='PLAYING',frame_hash='playing',playing_preview={'title':'JOINT','game_level':17})
        for i in (3,4):tr.consume(playing,sample_id=i,image=image);self.dl.observe(playing,tr,image)
        sid=tr.session['session_id']
        result=dict(state='RESULT',frame_hash='result',context={})
        for i in (5,6):tr.consume(result,sample_id=i,image=image);self.dl.observe(result,tr,image)
        self.service.ingest(tr.last_session,self.ocr)
        self.dl.queue.join();deadline=time.monotonic()+5
        while time.monotonic()<deadline and not self.dl.status().get('scan_completed'):time.sleep(.03)
        self.assertEqual(self.dl.active['session_id'],sid)
        self.assertTrue((self.output/'sessions'/sid/'result.png').exists())
        self.assertTrue(self.service.detail(sid)['download_evidence']['scan_completed'])
        self.assertIsNone(tr.last_session['song_id'])


class RecentTests(unittest.TestCase):
    setUp=custom_fixture.CustomMaxTests.setUp
    tearDown=custom_fixture.CustomMaxTests.tearDown
    body=custom_fixture.CustomMaxTests.body
    sync_session=custom_fixture.CustomMaxTests.sync_session
    approved=custom_fixture.CustomMaxTests.approved
    clone_session=custom_fixture.CustomMaxTests.clone_session
    store=custom_fixture.CustomMaxTests.store
    commit=custom_fixture.CustomMaxTests.commit

    def recent(self):
        with self.store().library.open() as lib:return recent_updates(lib.connection)

    def save_score(self,p,c,b=0,m=0,difficulty='HARD'):
        return self.commit(self.approved(perfect=p,cool=c,bad=b,miss=m,max_combo=p+c,total_notes=p+c+b+m,difficulty=difficulty))['record']

    def next(self):self.clone_session();self.session['selection']=None;self.sync_session()

    def test_first_best_and_strict_new_score_not_equal_or_lower(self):
        first=self.save_score(500,500);row=self.recent()['updates'][0]
        self.assertEqual((row['update_type'],row['previous_best_ex'],row['new_best_ex']),('FIRST BEST',None,1500))
        self.next();second=self.save_score(600,400);row=self.recent()['updates'][0]
        self.assertEqual((row['record_id'],row['update_type'],row['improvement']),(second['record_id'],'NEW SCORE',100))
        self.next();self.save_score(600,400);self.next();self.save_score(400,600)
        self.assertEqual(self.recent()['updates'][0]['record_id'],second['record_id'])

    def test_ac_to_acp_updates_acp_to_ac_does_not(self):
        self.save_score(990,10);self.next();acp=self.save_score(1000,0)
        self.assertEqual(acp['lamp_status'],'ACP');self.assertEqual(self.recent()['updates'][0]['improvement'],10)
        self.next();self.save_score(990,10)
        self.assertEqual(self.recent()['updates'][0]['record_id'],acp['record_id'])

    def test_zero_formal_best_is_distinct_from_no_record(self):
        self.assertEqual(self.recent()['updates'],[])
        self.save_score(0,0,1000);self.assertEqual(self.recent()['updates'][0]['update_type'],'FIRST BEST')
        self.next();self.save_score(0,1,999)
        r=self.recent()['updates'][0];self.assertEqual((r['previous_best_ex'],r['improvement']),(0,1))

    def test_lamp_only_update_does_not_replace_score_update(self):
        first=self.save_score(810,180,10);self.next();ac=self.save_score(700,300)
        self.assertTrue(ac['achievement_feedback']['new_lamp']);self.assertFalse(ac['achievement_feedback']['new_score'])
        self.assertEqual(self.recent()['updates'][0]['record_id'],first['record_id'])

    def test_pending_scan_do_not_make_recent_but_valid_failed_score_does(self):
        self.approved();self.assertEqual(self.recent()['updates'],[]);self.next()
        r=self.commit(self.approved(completion_status='FAILED'))['record'];self.assertEqual(self.recent()['updates'][0]['lamp_status'],'FAILED')
        with self.store().library.open() as lib:self.assertEqual(lib.connection.execute('PRAGMA user_version').fetchone()[0],10)

    def test_cross_session_conflict_suppresses_uncomparable_chart(self):
        self.save_score(1005,10);self.next()
        self.commit(self.approved(perfect=1013,cool=0,max_combo=1013,total_notes=1015))
        self.assertEqual(self.recent()['updates'],[])

    def test_difficulties_are_separate_single_rows(self):
        self.save_score(500,500);self.next();self.save_score(500,500,difficulty='NORMAL')
        rows=self.recent()['updates'];self.assertEqual(len(rows),2)
        self.assertEqual({r['difficulty'] for r in rows},{'HARD','NORMAL'})

    def test_known_max_policies_with_confirmed_same_ex_formula_bridge_safely(self):
        self.service.scoring_policy='independent-max-v2';self.save_score(500,500)
        self.service.scoring_policy='custom-judged-v1';self.next();r=self.save_score(600,400)
        self.assertTrue(r['achievement_feedback']['new_score']);self.assertEqual(self.recent()['updates'][0]['improvement'],100)

    def test_repeated_submit_and_repeated_query_preserve_recent(self):
        b=self.approved();store=self.store();p=store.prepare(b)
        request={**b,'submit_acknowledge':True,'submission_token':p['submission_token']}
        store.submit(request);before=self.recent();reply=store.submit(request)
        self.assertTrue(reply['idempotent']);self.assertEqual(before,self.recent())

    def test_latest_30_distinct_same_title_song_ids_and_played_at_order(self):
        root=self.base/'SONG'
        for i in range(35):
            d=root/'Same Title Pack'/str(i);d.mkdir(parents=True);(d/'a.sm').write_text('#TITLE:JOINT;',encoding='utf8')
        with LibraryDatabase(self.db) as lib:
            lib.sync(scan_library(root));ids=[r[0] for r in lib.connection.execute("SELECT song_id FROM songs WHERE relative_folder LIKE 'Same Title Pack%' ORDER BY song_id")]
        for i,sid in enumerate(ids):
            if i:self.next()
            self.session.update(song_id=sid,ended_at=f'2026-10-22T00:{i:02d}:00+00:00');self.sync_session()
            self.save_score(10,1)
        rows=self.recent()['updates'];self.assertEqual(len(rows),30);self.assertEqual(len({r['song_id'] for r in rows}),30)
        self.assertEqual(rows[0]['song_id'],ids[-1]);self.assertEqual(rows[-1]['song_id'],ids[5])
        # Late submission of an old play sorts by played_at, never confirmed_at.
        self.next();self.session.update(song_id=1,ended_at='2026-10-22T00:01:00+00:00');self.sync_session();self.save_score(10,1)
        self.assertEqual(self.recent()['updates'][0]['song_id'],ids[-1])
