from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sqlite3
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from dance_results.monitor_tracking import Tracker, ManualReferences
from dance_results.monitor_ocr import Analyzer, apply_selection, active_pack
from dance_results.monitor import Monitor
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_static import normalize_tokens
from dance_results.library_service import LibraryError
from dance_results.sqlite_safety import audit
from dance_results.window_capture import is_game_title
import test_ocr_static as fixtures


def selected(title='JOINT', level=17, notes=821, seconds=96):
    fields = fixtures.observation(title=title, game_level=level, total_notes=notes, duration_seconds=seconds)
    fields['bpm'] = {'value': 180, 'confidence': .99}
    fields['artist'] = {'value': None, 'confidence': 0}
    return dict(state='SONG_SELECT', fields=fields, source_type='custom', frame_hash=title+str(level),
                matching={'status': 'pending_manual_review', 'candidates': [], 'reasons': []})


def scene(name, **extra):
    return dict(state=name, frame_hash=name, **extra)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.tracker = Tracker(self.base)
        self.i = 0

    def tearDown(self):self.temp.cleanup()

    def send(self, observation, count=2, image=None):
        for _ in range(count):
            self.i += 1
            self.tracker.consume(copy.deepcopy(observation), now=self.i, image=image, sample_id=self.i)

    def test_normal_result_is_saved_once_and_locked_fields_do_not_follow_background(self):
        self.send(selected())
        self.send(scene('PLAYING', playing_preview={'title': 'JOINT', 'game_level': 17}))
        before = copy.deepcopy(self.tracker.session)
        self.assertEqual(before['selection']['fields']['total_notes']['value'], 821)
        self.send(scene('PLAYING', playing_preview={'title': 'Other background'}))
        self.assertEqual(self.tracker.session, before)
        self.send(scene('RESULT', context={'local_player_status': 'completed_observed'}))
        directory = self.base/'sessions'/before['session_id']
        self.assertEqual(json.loads((directory/'session.json').read_text())['status'], 'pending_result')
        saved = (directory/'session.json').read_bytes()
        self.send(scene('RESULT'), count=8)
        self.assertEqual((directory/'session.json').read_bytes(), saved)
        self.assertEqual(len([e for e in self.tracker.events if e['event'] == 'result_saved']), 1)
        self.assertIsNone(self.tracker.selection)

    def test_failed_result_and_interruption_are_distinct(self):
        self.send(selected());self.send(scene('PLAYING'));self.send(scene('GAME_OVER', context={'local_player_status':'failed'}))
        self.assertEqual(self.tracker.last_session['result']['state'], 'GAME_OVER')
        self.send(scene('PLAYING'));self.tracker.interrupt('window_closed')
        self.assertEqual(self.tracker.last_session['status'], 'interrupted')
        self.assertIsNone(self.tracker.last_session['result'])

    def test_single_wrong_frame_cannot_create_session_or_result(self):
        self.send(selected())
        self.send(scene('PLAYING'), count=1)
        self.assertIsNone(self.tracker.session)
        self.send(selected())
        self.send(scene('PLAYING'))
        self.send(scene('GAME_OVER'), count=1)
        self.send(scene('PLAYING'))
        self.assertIsNotNone(self.tracker.session)
        self.assertIsNone(self.tracker.last_result)

    def test_previous_song_cannot_be_used_for_next_round_or_different_playing_title(self):
        self.send(selected());self.send(scene('PLAYING', playing_preview={'title':'Secret','game_level':21}))
        self.assertIsNone(self.tracker.session['selection'])
        self.assertIsNone(self.tracker.session['song_id'])
        self.assertEqual(self.tracker.session['pending_reason'], 'playing_title_differs_from_selection')
        self.send(scene('RESULT'));self.send(scene('PLAYING'))
        self.assertIsNone(self.tracker.session['selection'])

    def test_fresh_selection_expires_and_window_epoch_clears_context(self):
        self.send(selected())
        self.i = 100
        self.send(scene('PLAYING'))
        self.assertEqual(self.tracker.session['pending_reason'], 'selection_expired')
        self.tracker.new_window({'hwnd':2,'pid':3})
        self.assertEqual(self.tracker.state, 'UNKNOWN')
        self.assertEqual(self.tracker.last_session['status'], 'interrupted')
        self.assertIsNone(self.tracker.current_pack)

    def test_pack_is_carried_when_hidden_but_invalidated_on_uncertain_open_list(self):
        a=dict(pack_id=29,name='A',status='confirmed')
        self.tracker.pack_observation(a);self.tracker.pack_observation(a);self.send(selected())
        self.assertEqual(self.tracker.selection['pack']['pack_id'],29)
        self.send(selected(title='Second'))
        self.assertEqual(self.tracker.selection['pack']['pack_id'],29)
        self.tracker.pack_observation(None)
        self.assertIsNone(self.tracker.selection)
        self.assertIsNone(self.tracker.current_pack)
        self.tracker.pack_observation(dict(pack_id=30,name='B',status='confirmed'))
        self.tracker.pack_observation(dict(pack_id=30,name='B',status='confirmed'))
        self.send(selected())
        self.assertEqual(self.tracker.selection['pack']['pack_id'],30)
        self.send(scene('PLAYING'));self.send(scene('RESULT'));self.send(selected(title='Next song'))
        self.assertEqual(self.tracker.selection['pack']['pack_id'],30)
        self.assertIsNone(self.tracker.selection['matching'].get('confirmed_song_id'))

    def test_duplicate_sample_and_room_return_do_not_forge_result(self):
        self.tracker.consume(scene('PLAYING'),now=1,sample_id='same')
        self.tracker.consume(scene('PLAYING'),now=2,sample_id='same')
        self.assertIsNone(self.tracker.session)
        self.send(scene('PLAYING'))
        self.send(scene('WAITING_ROOM'))
        self.assertEqual(self.tracker.last_session['status'],'interrupted')
        self.assertIsNone(self.tracker.last_session['result'])

    def test_manual_binding_locks_exact_id_and_does_not_modify_formal_database(self):
        self.send(selected())
        self.tracker.selection['matching'].update(status='confirmed_manual',confirmed_song_id=990)
        self.send(scene('PLAYING',playing_preview={'title':'JOINT','game_level':17}))
        self.assertEqual(self.tracker.session['song_id'],990)
        self.send(selected(level=20,notes=929,seconds=95),count=1)
        self.assertEqual(self.tracker.session['song_id'],990)

    def test_room_song_change_and_guest_role_invalidate_stale_selection(self):
        self.send(selected())
        self.send(scene('WAITING_ROOM',room_preview={'title':'Other'},context={'local_is_host':True}))
        self.assertIsNone(self.tracker.selection)
        self.send(selected())
        self.send(scene('WAITING_ROOM',room_preview={'title':'JOINT'},context={'local_is_host':False}))
        self.assertIsNone(self.tracker.selection)

    def test_unknown_context_clears_selection_without_forging_failure(self):
        self.send(selected());self.send(scene('UNKNOWN'))
        self.assertIsNone(self.tracker.selection)
        self.assertIsNone(self.tracker.last_result)


class ReferenceTests(unittest.TestCase):
    def setUp(self):fixtures.MatchingSafetyTests.setUp(self)
    def tearDown(self):fixtures.MatchingSafetyTests.tearDown(self)
    def test_manual_reference_persists_and_conflicting_song_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            refs=ManualReferences(directory)
            songs=[s for s in self.catalog.songs.values() if s['pack_id']==self.pack_a]
            fields=selected()['fields']
            refs.add(fields,songs[0],'frame')
            with self.assertRaises(ValueError):refs.add(fields,songs[1],'otherframe')
            restored=ManualReferences(directory)
            self.assertEqual(restored.lookup(fields,self.pack_a,'custom',self.catalog.songs)['song_id'],songs[0]['song_id'])
            wrong=copy.deepcopy(fields);wrong['duration_seconds']['value']=95
            self.assertIsNone(restored.lookup(wrong,self.pack_a,'custom',self.catalog.songs))

    def test_purple_pack_selection_and_ambiguous_rows(self):
        from PIL import Image,ImageDraw
        image=Image.new('RGB',(1024,800),(110,110,110));draw=ImageDraw.Draw(image)
        draw.rectangle((50,280,330,320),fill=(100,50,180))
        selection={'visible_packs':[{'text':'Pack A','evidence':fixtures.token('Pack A',180,300)},
                                    {'text':'Pack B','evidence':fixtures.token('Pack B',180,350)}]}
        pack,_=active_pack(selection,image,self.catalog)
        self.assertEqual(pack['pack_id'],self.pack_a)
        draw.rectangle((50,330,330,370),fill=(100,50,180))
        self.assertIsNone(active_pack(selection,image,self.catalog)[0])

    def test_reference_cannot_eliminate_unknown_same_name_candidate(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            refs=ManualReferences(directory);tracker=Tracker(directory)
            tracker.current_pack=dict(pack_id=self.pack_a,name='Pack A',status='confirmed')
            tracker.current_source='custom'
            songs=[s for s in self.catalog.songs.values() if s['pack_id']==self.pack_a]
            fields=selected()['fields'];refs.add(fields,songs[0],'first')
            observation=dict(state='SONG_SELECT',selection={'fields':fields,'pack_list_visible':False},frame_hash='x')
            result=apply_selection(copy.deepcopy(observation),Image.new('RGB',(1024,800)),tracker,self.catalog,refs)
            self.assertEqual(result['matching']['status'],'pending_manual_review')
            other=selected(level=20,notes=929,seconds=95)['fields'];refs.add(other,songs[1],'second')
            result=apply_selection(copy.deepcopy(observation),Image.new('RGB',(1024,800)),tracker,self.catalog,refs)
            self.assertEqual(result['matching']['status'],'confirmed_manual_reference')
            self.assertEqual(result['matching']['confirmed_song_id'],songs[0]['song_id'])

    def test_confirmation_rejects_stale_or_changed_identity_and_only_writes_sidecar(self):
        monitor=Monitor(self.path,self.base/'monitor');monitor.running=True
        monitor.tracker=Tracker(monitor.directory);monitor.references=ManualReferences(monitor.directory)
        observation=selected();observation['matching']=self.catalog.match(observation['fields'])
        monitor.tracker.consume(observation,now=1,sample_id=1);monitor.tracker.consume(observation,now=2,sample_id=2)
        current=monitor.tracker.selection;song=next(s for s in self.catalog.songs.values() if s['pack_id']==self.pack_a)
        body=dict(observation_id=current['observation_id'],song_id=song['song_id'],root_id=song['root_id'],
                  relative_path=song['relative_folder'],acknowledge=True,difficulty='HARD',game_level=17,total_notes=821,duration_seconds=96)
        with self.assertRaises(LibraryError):monitor.confirm(dict(body,observation_id='stale'))
        with self.assertRaises(LibraryError):monitor.confirm(dict(body,relative_path='wrong'))
        before=audit(self.catalog.db)
        monitor.confirm(body)
        self.assertEqual(monitor.tracker.selection['matching']['confirmed_song_id'],song['song_id'])
        # Subsequent unchanged OCR must not discard a deliberately confirmed Pack/context.
        monitor.tracker.consume(observation,now=3,sample_id=3)
        self.assertEqual(monitor.tracker.selection['matching']['status'],'confirmed_manual')
        self.assertEqual(audit(self.catalog.db),before)
        self.assertTrue(monitor.references.path.exists())


class MonitorControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def test_missing_window_can_be_stopped_promptly_and_restarted(self):
        class EmptyWindows:
            def list(self,*_):return []
        # Use the real read-only catalog fixture, no native capture/OCR packages.
        fixture=fixtures.MatchingSafetyTests();fixture.setUp()
        try:
            monitor=Monitor(fixture.path,self.base,windows_factory=EmptyWindows,engine_factory=lambda:None)
            monitor.start({})
            deadline=time.monotonic()+2
            while monitor.connection=='initializing' and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(monitor.status()['connection'],'window_not_found')
            started=time.monotonic();monitor.stop(wait=2)
            self.assertLess(time.monotonic()-started,.5)
            self.assertFalse(monitor.running)
            monitor.start({});monitor.stop(wait=2)
            self.assertFalse(monitor.running)
        finally:fixture.tearDown()

    def test_settings_validation_and_stale_confirmation(self):
        monitor=Monitor(self.base/'unused.sqlite3',self.base)
        for body in [{'capture_hz':float('nan')},{'ocr_interval':0},{'confirmations':1},{'player_name':''}, {'window_hwnd':-1}]:
            with self.assertRaises(LibraryError):monitor.start(body)
        with self.assertRaises(LibraryError):monitor.confirm({'observation_id':'old'})

    def test_window_title_versions_are_flexible_but_launcher_is_excluded(self):
        for title in ['dance v2.0.0-dev-abc','dance v10.0','DANCE v3','Dance Online']:
            self.assertTrue(is_game_title(title))
        for title in ['Dance Launcher','My dance report','dance_results.gui']:
            self.assertFalse(is_game_title(title))

    def test_window_loss_minimize_unfocus_and_restart_with_simulated_provider(self):
        from PIL import Image
        class FakeWindows:
            exists=True;minimized=False;pid=10
            def info(self,hwnd):
                return dict(hwnd=1,pid=self.pid,title='dance v99',minimized=self.minimized,
                            foreground=False,width=1024,height=800,is_game=True) if self.exists else None
            def list(self,*_):return [self.info(1)] if self.exists else []
        class FakeCapture:
            def __init__(self,*_):self.closed=False;self.error=None;self.copy_ms=.1;self.last=0
            def take(self):
                now=time.monotonic()
                if now-self.last<.1:return None
                self.last=now
                return now,Image.new('RGB',(1024,800),'gray')
            def stop(self):self.closed=True
        class FakeEngine:
            def recognize(self,*_,**__):return [fixtures.token('歌曲名：Secret',160,780),fixtures.token('時間：',400,780)]
        def wait_for(check):
            deadline=time.monotonic()+4
            while not check() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(check())
        fixture=fixtures.MatchingSafetyTests();fixture.setUp();windows=FakeWindows()
        monitor=Monitor(fixture.path,self.base,windows_factory=lambda:windows,capture_factory=FakeCapture,engine_factory=FakeEngine)
        try:
            monitor.start({'ocr_interval':.5});wait_for(lambda:monitor.tracker.session is not None)
            session=monitor.tracker.session['session_id'];self.assertFalse(monitor.window['foreground'])
            windows.minimized=True;wait_for(lambda:monitor.connection=='minimized')
            self.assertEqual(monitor.tracker.session['session_id'],session)
            windows.minimized=False;wait_for(lambda:monitor.connection=='connected')
            windows.exists=False;wait_for(lambda:monitor.connection=='window_not_found')
            self.assertEqual(monitor.tracker.last_session['status'],'interrupted')
            windows.pid=11;windows.exists=True
            wait_for(lambda:monitor.tracker.session is not None)
            self.assertNotEqual(monitor.tracker.session['session_id'],session)
            self.assertIsNone(monitor.tracker.session['song_id'])
        finally:monitor.stop(wait=3);fixture.tearDown()

    def test_http_monitor_page_auth_start_stop_and_catalog_integrity(self):
        from dance_results.gui import create_server
        fixture=fixtures.MatchingSafetyTests();fixture.setUp()
        server=create_server(fixture.path,monitor_directory=self.base)
        class EmptyWindows:
            def list(self,*_):return []
        server.monitor.windows_factory=EmptyWindows;server.monitor.engine_factory=lambda:None
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path,body=None,headers=None):
            return urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                headers={'X-Dance-Token':server.session_token,'Content-Type':'application/json',**(headers or {})}))
        before=audit(fixture.catalog.db)
        try:
            self.assertIn('開始監控',urlopen(base+'/monitor').read().decode('utf8'))
            with self.assertRaises(HTTPError) as denied:urlopen(base+'/api/monitor/status')
            self.assertEqual(denied.exception.code,403)
            self.assertTrue(json.load(request('/api/monitor/start',{}))['running'])
            with self.assertRaises(HTTPError) as stale:request('/api/monitor/confirm',{'observation_id':'stale'})
            self.assertEqual(stale.exception.code,409)
            request('/api/monitor/stop',{})
            server.monitor.stop(wait=2)
            self.assertFalse(json.load(request('/api/monitor/status'))['running'])
            self.assertEqual(audit(fixture.catalog.db),before)
        finally:server.shutdown();server.server_close();fixture.tearDown()


class RealScreenshotReplayTests(unittest.TestCase):
    def test_original_image_state_sequences_and_joint_changes(self):
        # This is recorded-image replay, not real gameplay. All pixels are original screenshots.
        from PIL import Image
        base=Path(__file__).resolve().parent.parent
        source=base/'reports/phase4a/raw-pages.json'
        database=base/'data/library.sqlite3'
        if not source.exists() or not database.exists():self.skipTest('Local development screenshots/catalog unavailable')
        pages=json.loads(source.read_text(encoding='utf8'))
        catalog=ReadOnlyCatalog(database)
        try:
            with tempfile.TemporaryDirectory() as directory:
                tracker=Tracker(directory);analyzer=Analyzer(None)
                scenes={};images={}
                names=['相同歌名1.PNG','相同歌名2.PNG','相同歌名3.PNG','遊玩畫面開始.PNG',
                       '遊玩畫面1.PNG','結算畫面.PNG','多人遊戲結算畫面.PNG','多人遊戲GAMEOVER結算畫面.PNG']
                for name in names:
                    with Image.open(base/'tests/專案測試圖片'/name) as im:
                        image=im.convert('RGB').resize((1024,800));images[name]=image
                        tokens=normalize_tokens(pages[name],*im.size)
                        observation=analyzer.from_tokens(tokens,image)
                        scenes[name]=observation
                n=0
                for name in ['相同歌名1.PNG','相同歌名2.PNG','相同歌名3.PNG']:
                    for _ in range(2):
                        n+=1;obs=apply_selection(copy.deepcopy(scenes[name]),images[name],tracker,catalog)
                        tracker.consume(obs,now=n,image=images[name],sample_id=n)
                self.assertEqual(tracker.selection['fields']['game_level']['value'],20)
                self.assertEqual(tracker.selection['fields']['total_notes']['value'],929)
                self.assertEqual(tracker.selection['fields']['duration_seconds']['value'],95)
                self.assertIsNone(tracker.current_pack) # No clear selected Pack in original list.
                self.assertIsNone(tracker.selection['matching'].get('confirmed_song_id'))
                for name in ['遊玩畫面開始.PNG','結算畫面.PNG']:
                    for _ in range(2):
                        n+=1;tracker.consume(scenes[name],now=n,image=images[name],sample_id=n)
                self.assertEqual(tracker.last_session['status'],'pending_result')
                self.assertIsNone(tracker.last_session['song_id'])
                self.assertEqual(tracker.last_session['pending_reason'],'playing_title_differs_from_selection')
                self.assertTrue((Path(directory)/'sessions'/tracker.last_session['session_id']/'result.png').exists())
                self.assertEqual(scenes['多人遊戲結算畫面.PNG']['state'],'RESULT')
                self.assertEqual(scenes['多人遊戲GAMEOVER結算畫面.PNG']['state'],'GAME_OVER')
        finally:catalog.close()


if __name__=='__main__':unittest.main()
