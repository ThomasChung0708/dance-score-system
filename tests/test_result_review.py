from __future__ import annotations

import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryError
from dance_results.monitor import Monitor
from dance_results.monitor_ocr import active_pack, room_preview
from dance_results.monitor_tracking import Tracker, atomic_json
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.ocr_screens import classify
from dance_results.result_ocr import parse_result, cell
from dance_results.result_review import ResultReviews, score_preview
from dance_results.scanner import scan_library
from dance_results.sqlite_safety import audit


def token(text, x, y, confidence=.99):
    return dict(text=text, confidence=confidence, box=[[x-9,y-8],[x+9,y-8],[x+9,y+8],[x-9,y+8]])


def result_tokens():
    return [token(n,x,200) for n,x in [('Perfect',475),('Cool',555),('Bad',630),('Miss',704)]] + [
        token('TC',185,250),token('11',368,250),token('10',480,250),token('1',556,250),
        token('0',635,250),token('0',713,250),token('900',888,250),token('A+',980,250),
        token('歌曲名：',55,780),token('JOINT',140,780),token('LV:17',300,780)]


def values(**changes):
    result=dict(perfect=10,cool=1,bad=0,miss=0,max_combo=11,game_score=900,grade='A+',
                completion_status='COMPLETED',game_level=17,total_notes=11,difficulty='HARD')
    result.update(changes)
    return result


class ResultParserTests(unittest.TestCase):
    def test_zero_is_distinct_from_missing_and_negative_not_normalized(self):
        self.assertEqual(cell([token('0',100,100)])['value'],0)
        self.assertIsNone(cell([])['value'])
        for raw in ['-1','O','1.0','12 34']:
            self.assertIsNone(cell([token(raw,100,100)])['value'])

    def test_local_row_is_not_ranking_and_other_player_failure_does_not_leak(self):
        rows=result_tokens()+[token('Other',185,318),token('39',368,318),token('99',480,318),token('OVER',23,318)]
        own=parse_result(rows,'TC')
        self.assertEqual(own['fields']['perfect']['value'],10)
        self.assertEqual(own['fields']['completion_status']['value'],'COMPLETED')
        self.assertEqual(own['footer_level_scope'],'host_only_not_local_chart')
        self.assertEqual(parse_result(rows,'Other')['fields']['completion_status']['value'],'FAILED')

    def test_unknown_duplicate_or_low_confidence_local_player_never_confirmed(self):
        rows=result_tokens()
        self.assertIsNone(parse_result(rows,'Nobody')['fields']['perfect']['value'])
        duplicate=rows+[token('TC',185,318),token('39',368,318)]
        self.assertIsNone(parse_result(duplicate,'TC')['fields']['perfect']['value'])
        rows[4]['confidence']=.6
        result=parse_result(rows,'TC')
        self.assertEqual(result['local_player_status'],'UNKNOWN')
        self.assertIsNone(result['fields']['completion_status']['value'])

    def test_headers_required_and_unknown_numbers_never_default_to_zero(self):
        self.assertIn('result_headers_not_supported',parse_result([], 'TC')['errors'])
        rows=[t for t in result_tokens() if t['text']!='1']
        self.assertIsNone(parse_result(rows)['fields']['cool']['value'])

    def test_spectator_banner_precedes_gameplay_and_no_own_result_is_guessed(self):
        tokens=[token('Press Ctrl+Q to quit look on mode',230,50),token('歌曲名：Pokemon',130,780),token('時間：',400,780)]
        self.assertEqual(classify(tokens)[0],'spectating')
        rows=[dict(t,text='Other') if t['text']=='TC' else t for t in result_tokens()]
        result=parse_result(rows,'TC')
        self.assertEqual(result['local_player_status'],'UNKNOWN')
        for key in ('perfect','cool','bad','miss','max_combo','game_score','grade','completion_status'):
            self.assertIsNone(result['fields'][key]['value'])

    def test_song_level_uses_footer_and_room_chart_not_avatar_level(self):
        rows=[dict(t,text='LV:20') if t['text']=='LV:17' else t for t in result_tokens()]
        rows.extend([token('LV:200',500,350),token('人物等級 200',180,450)])
        parsed=parse_result(rows,'TC')
        self.assertEqual(parsed['fields']['game_level']['value'],20)
        self.assertNotIn('player_level',parsed['fields'])
        room=room_preview([token('TC LV:200',500,300),token('難度20 BPM170',900,405)])
        self.assertEqual(room['game_level'],20)

    def test_gui_uses_song_level_and_play_completion_labels(self):
        assets=Path(__file__).resolve().parents[1]/'dance_results/gui_assets'
        for name in ('reviews.html','reviews.js','monitor.html','monitor.js','app.js'):
            text=(assets/name).read_text(encoding='utf8')
            self.assertNotIn('本人 LV',text)
            self.assertNotIn('本人完成狀態',text)
        html=(assets/'reviews.html').read_text(encoding='utf8')
        self.assertIn('歌曲 LV',html)
        self.assertIn('遊玩完成狀態',html)
        self.assertIn('本人暱稱',html)


from functools import partial
score_preview=partial(score_preview,max_basis={})

class ScorePreviewTests(unittest.TestCase):
    def test_cagayake_hard_chart_level_20_has_max_minus_3_ac(self):
        result=score_preview(values(perfect=835,cool=3,bad=0,miss=0,max_combo=838,
            total_notes=838,game_level=20),True,True,max_basis=dict(notes=838,verified=True))
        self.assertEqual(result['ex_score'],1673)
        self.assertEqual(result['max_score'],1676)
        self.assertEqual(result['display'],'MAX -3')
        self.assertEqual(result['lamp_preview'],'AC')

    def test_max_boundary_150_and_151(self):
        a=score_preview(values(perfect=125,cool=0,bad=75,total_notes=200,max_combo=100),True,True,max_basis=dict(notes=200,verified=True))
        b=score_preview(values(perfect=124,cool=1,bad=75,total_notes=200,max_combo=100),True,True,max_basis=dict(notes=200,verified=True))
        self.assertEqual(a['display'],'MAX -150')
        self.assertEqual(b['display'],'62.25%')

    def test_acp_ac_clear_failed_are_only_previews(self):
        self.assertEqual(score_preview(values(perfect=11,cool=0),True,True,max_basis=dict(notes=11,verified=True))['lamp_preview'],'ACP')
        self.assertEqual(score_preview(values(),True,True,max_basis=dict(notes=11,verified=True))['lamp_preview'],'AC')
        self.assertEqual(score_preview(values(perfect=9,cool=1,bad=1,max_combo=10),True,True)['lamp_preview'],'CLEAR')
        failed=score_preview(values(perfect=0,cool=0,bad=0,miss=2,max_combo=0,completion_status='FAILED'),True,True,max_basis=dict(notes=11,verified=True))
        self.assertEqual(failed['lamp_preview'],'FAILED')
        self.assertEqual(failed['judged_notes'],2)
        self.assertEqual(failed['max_score'],22)
        self.assertFalse(failed['writes_play_record'])

    def test_unknown_identity_notes_or_mismatch_suppress_max_and_percent(self):
        for data,verified,identity in [(values(total_notes=None),False,True),(values(),False,True),
                                       (values(),True,False),(values(total_notes=12),True,True)]:
            preview=score_preview(data,verified,identity)
            self.assertIsNone(preview['max_score'])
            self.assertIsNone(preview['percentage'])
            self.assertIsNone(preview['lamp_preview'])

    def test_invalid_numbers_bool_float_negative_and_combo(self):
        for number in [-1,True,1.2,float('nan'),'0']:
            preview=score_preview(values(perfect=number),True,True)
            self.assertTrue(preview['errors'])
            self.assertIsNone(preview['max_score'])
        self.assertTrue(score_preview(values(max_combo=12),True,True)['errors'])
        self.assertIsNone(score_preview(values(cool=None),True,True)['ex_score'])


class ReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.db=self.base/'library.sqlite3';root=self.base/'SONG'
        for name,title in [('Folder A','JOINT'),('Folder B','JOINT'),('多語言','中文 日本語 English☆')]:
            directory=root/'Pack A'/name;directory.mkdir(parents=True)
            (directory/'song.sm').write_text('#TITLE:'+title+';',encoding='utf8')
        with LibraryDatabase(self.db) as db:
            db.sync(scan_library(root))
            self.before=audit(db.connection)
        self.output=self.base/'monitor';self.service=ResultReviews(self.db,self.output,scoring_policy='independent-max-v2')
        self.sid=str(uuid4());folder=self.output/'sessions'/self.sid;folder.mkdir(parents=True)
        Image.new('RGB',(1024,800),'gray').save(folder/'result.png')
        self.session=dict(session_id=self.sid,status='pending_result',started_at='2026-10-22T00:00:00+00:00',ended_at='2026-10-22T00:01:36+00:00',
            song_id=1,matching_status='confirmed_manual',result={'state':'RESULT','screenshot':'result.png'},
            selection=dict(pack={'pack_id':1,'name':'Pack A'},fields={k:{'value':v,'confidence':.99} for k,v in
                dict(title='JOINT',difficulty='HARD',game_level=17,total_notes=11,duration_seconds=96).items()}))
        atomic_json(folder/'session.json',self.session)
        self.ocr=parse_result(result_tokens(),'TC');self.ocr['tokens']=result_tokens()
        self.row=self.service.ingest(self.session,self.ocr)

    def tearDown(self):
        self.temp.cleanup()

    def body(self,**changes):
        catalog=ReadOnlyCatalog(self.db)
        try:song=catalog.songs[1]
        finally:catalog.close()
        body=dict(session_id=self.sid,review_id=self.row['review_id'],revision=0,values=values(),song_id=1,
            root_id=song['root_id'],relative_path=song['relative_folder'],notes_verified=True,
            player_verified=True,identity_reviewed=True,acknowledge=True,confirm=True)
        body.update(changes)
        # Controlled math/regression fixture: an independently stipulated MAX
        # definition, not a production inference from the catalog or judgments.
        if 'max_basis' not in changes and body.get('notes_verified') and type(body['values'].get('total_notes')) is int:
            preview=self.service.preview(body)
            body['max_basis']=dict(context=preview['max_basis_verification']['context'],notes=body['values']['total_notes'],source='independent',verified=True,evidence='ISOLATED TEST ONLY: independently stipulated game MAX definition')
        return body

    def assert_database_unchanged(self):
        with closing(sqlite3.connect(self.db)) as db:self.assertEqual(audit(db),self.before)

    def test_save_confirm_reload_and_repeated_click_idempotency(self):
        saved=self.service.save(self.body())
        self.assertEqual(saved['status'],'confirmed_manual')
        repeated=self.service.save(self.body())
        self.assertEqual(repeated['revision'],1)
        restored=ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(self.sid)
        self.assertEqual(restored['review_id'],saved['review_id'])
        self.assertEqual(len(restored['history']),1)
        self.assert_database_unchanged()

    def test_manual_correction_preserves_raw_ocr_and_locked_session(self):
        corrected=values(perfect=9,cool=2)
        saved=self.service.save(self.body(values=corrected))
        self.assertEqual(saved['ocr']['fields']['perfect']['value'],10)
        self.assertEqual(saved['draft']['values']['perfect'],9)
        self.assertEqual(saved['locked_session'],self.session)
        self.assertEqual(saved['preview']['ex_score'],20)
        self.assert_database_unchanged()

    def test_repeated_ocr_cannot_overwrite_manual_correction(self):
        self.service.save(self.body(values=values(perfect=9,cool=2)))
        self.service.ingest(self.session,self.ocr)
        self.assertEqual(self.service.detail(self.sid)['draft']['values']['perfect'],9)
        self.assertEqual(len(self.service.list()['results']),0)

    def test_stale_revision_conflicting_song_path_or_image_rejected(self):
        self.service.save(self.body())
        with self.assertRaises(LibraryError):self.service.save(self.body(values=values(perfect=9,cool=2)))
        with self.assertRaises(LibraryError):self.service.save(self.body(relative_path='Pack A/Folder B'))
        with (self.output/'sessions'/self.sid/'result.png').open('ab') as f:f.write(b'changed')
        with self.assertRaises(LibraryError):self.service.detail(self.sid)

    def test_same_title_folders_and_unicode_are_separate_candidates(self):
        candidates=self.service.candidates('JOINT')['candidates']
        self.assertEqual(len(candidates),2)
        self.assertEqual(len({c['relative_path'] for c in candidates}),2)
        self.assertEqual(len(self.service.candidates('日本語')['candidates']),1)
        self.assertIsNone(self.service.preview(self.body(song_id=None))['max_score'])

    def test_selection_result_and_matcher_use_same_chart_level_without_player_column(self):
        catalog=ReadOnlyCatalog(self.db)
        try:
            song=next(s for s in catalog.songs.values() if s['folder_name']=='多語言')
            fields={k:dict(value=v,confidence=.99) for k,v in dict(title=song['saved_title'] or song['latest_title'],
                difficulty='HARD',game_level=20,total_notes=838,duration_seconds=88).items()}
            # Verified game metadata in memory only: this test does not alter the catalog.
            catalog.difficulties[(song['song_id'],'HARD')]=dict(difficulty_id=9001,game_level=20,level_source='manual',
                total_notes=838,notes_source='manual',notes_verified=1,duration_seconds=88,duration_verified=1)
            selected=catalog.match(fields,song['pack_id'],'custom')
            rows=[dict(t,text='LV:20') if t['text']=='LV:17' else t for t in result_tokens()]
            rows.append(token('LV:200',500,350))
            result_fields=copy.deepcopy(fields);result_fields['game_level']=parse_result(rows)['fields']['game_level']
            result=catalog.match(result_fields,song['pack_id'],'custom')
            self.assertEqual(selected['status'],'unique_verified_candidate')
            self.assertEqual(result['status'],selected['status'])
            self.assertEqual(result['candidates'][0]['song_id'],song['song_id'])
            for row in catalog.db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                columns=[c[1] for c in catalog.db.execute('PRAGMA table_info("'+row[0]+'")')]
                self.assertNotIn('player_level',columns)
            self.assertIn('game_level',[c[1]for c in catalog.db.execute('PRAGMA table_info(game_difficulties)')])
        finally:catalog.close()
        self.assert_database_unchanged()

    def test_unknown_pack_identity_and_notes_never_auto_confirm(self):
        session=copy.deepcopy(self.session);session['song_id']=None;session['selection']['pack']=None
        session['matching_status']='pending_manual_review'
        sid=str(uuid4());session['session_id']=sid
        folder=self.output/'sessions'/sid;folder.mkdir();Image.new('RGB',(10,10),'gray').save(folder/'result.png')
        atomic_json(folder/'session.json',session);self.service.ingest(session,self.ocr)
        detail=self.service.detail(sid)
        self.assertIsNone(detail['draft']['song_id'])
        self.assertEqual(detail['status'],'pending_review')
        self.assertIsNone(detail['preview']['max_score'])

    def test_result_title_level_conflicts_require_explicit_identity_review(self):
        self.ocr['fields']['title']['value']='Different song'
        self.service._read(self.sid)[0].joinpath('result-review.json').unlink()
        self.service.ingest(self.session,self.ocr)
        preview=self.service.preview(self.body(identity_reviewed=False))
        self.assertFalse(preview['can_confirm'])
        self.assertIsNone(preview['max_score'])
        self.assertTrue(any(i['code']=='result_session_conflict' for i in preview['warnings']))
        self.assertTrue(self.service.preview(self.body())['can_confirm'])

    def test_unknown_notes_can_be_reviewed_without_fabricating_max(self):
        body=self.body(values=values(total_notes=None),notes_verified=False)
        saved=self.service.save(body)
        self.assertEqual(saved['status'],'reviewed_pending_max_basis')
        self.assertIsNone(saved['preview']['max_score'])

    def test_failed_counts_do_not_set_full_notes_and_partial_judgments_allowed(self):
        body=self.body(values=values(perfect=0,cool=0,bad=0,miss=2,max_combo=0,completion_status='FAILED'))
        result=self.service.save(body)
        self.assertEqual(result['preview']['lamp_preview'],'FAILED')
        self.assertEqual(result['draft']['values']['total_notes'],11)

    def test_gameover_cannot_be_previewed_or_saved_as_clear(self):
        self.ocr['fields']['completion_status']['value']='FAILED'
        path=self.service._read(self.sid)[0]/'result-review.json'
        path.unlink();self.service.ingest(self.session,self.ocr)
        preview=self.service.preview(self.body())
        self.assertEqual(preview['lamp_preview'],'FAILED')
        self.assertIsNone(preview['max_score'])
        self.assertFalse(preview['can_confirm'])
        with self.assertRaises(LibraryError):self.service.save(self.body())

    def test_manual_player_name_keeps_unicode_and_case(self):
        body=self.body(values={**values(),'player_name':'新暱稱 Name★'})
        result=self.service.save(body)
        self.assertEqual(result['draft']['values']['player_name'],'新暱稱 Name★')
        self.assertEqual(result['ocr']['fields']['player_name']['value'],'TC')

    def test_missing_player_negative_values_or_inconsistent_totals_cannot_confirm(self):
        for body in [self.body(player_verified=False),self.body(values=values(perfect=-1)),
                     self.body(values=values(total_notes=100))]:
            with self.assertRaises(LibraryError):self.service.save(body)

    def test_interrupted_session_is_not_listed_as_a_result(self):
        sid=str(uuid4());folder=self.output/'sessions'/sid;folder.mkdir()
        atomic_json(folder/'session.json',dict(session_id=sid,status='interrupted'))
        self.assertEqual(len(self.service.list()['results']),1)
        with self.assertRaises(LibraryError):self.service.process(sid)

    def test_spectating_never_creates_own_session_and_interrupts_incorrect_playing(self):
        tracker=Tracker(self.base/'spectator')
        for index in range(2):tracker.consume(dict(state='SPECTATING',frame_hash='s'),sample_id=index)
        self.assertIsNone(tracker.session)
        for index in range(2,4):tracker.consume(dict(state='PLAYING',frame_hash='p'),sample_id=index)
        self.assertIsNotNone(tracker.session)
        for index in range(4,6):tracker.consume(dict(state='SPECTATING',frame_hash='s'),sample_id=index)
        self.assertIsNone(tracker.session)
        self.assertEqual(tracker.last_session['status'],'interrupted')
        for index in range(6,8):tracker.consume(dict(state='RESULT',frame_hash='r'),sample_id=index)
        self.assertIsNone(tracker.last_result)

    def test_process_is_background_and_existing_review_not_reread(self):
        self.assertEqual(self.service.process(self.sid)['status'],'already_processed')
        sid=str(uuid4());session=copy.deepcopy(self.session);session['session_id']=sid
        folder=self.output/'sessions'/sid;folder.mkdir();Image.new('RGB',(10,10),'gray').save(folder/'result.png')
        atomic_json(folder/'session.json',session)
        self.service.engine_factory=lambda:object()
        with patch('dance_results.result_ocr.recognize_result',return_value=self.ocr):
            self.assertEqual(self.service.process(sid)['status'],'running')
            self.service.worker.join(5)
        self.assertEqual(self.service.jobs[sid]['status'],'completed')
        self.assertEqual(ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(sid)['status'],'pending_review')

    def test_pack_color_is_candidate_and_manual_pack_cleared_on_open(self):
        catalog=ReadOnlyCatalog(self.db)
        try:
            image=Image.new('RGB',(1024,800),(20,20,20))
            for y in range(250,275):
                for x in range(52,327):image.putpixel((x,y),(70,20,170))
            found,_=active_pack({'visible_packs':[dict(text='Pack A',evidence=token('Pack A',180,262))]},image,catalog)
            self.assertEqual(found['confidence_state'],'CANDIDATE')
            tracker=Tracker(self.base/'tracker');tracker.current_pack=dict(pack_id=1,status='confirmed',source='explicit_user_review')
            tracker.pack_observation(found)
            self.assertIsNone(tracker.current_pack)
        finally:catalog.close()

    def test_http_review_page_auth_and_persistence_routes(self):
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        server=create_server(self.db,monitor_directory=self.output)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            self.assertIn('待確認成績',urlopen(base+'/reviews').read().decode('utf8'))
            with self.assertRaises(HTTPError):urlopen(base+'/api/reviews')
            headers={'X-Dance-Token':server.session_token,'Content-Type':'application/json'}
            response=json.load(urlopen(Request(base+'/api/reviews/save',data=json.dumps(self.body()).encode(),headers=headers)))
            self.assertEqual(response['revision'],1)
            detail=json.load(urlopen(Request(base+'/api/reviews/detail?session_id='+self.sid,headers=headers)))
            self.assertEqual(detail['status'],'confirmed_manual')
            self.assert_database_unchanged()
        finally:server.shutdown();server.server_close();thread.join(3)

    def test_manual_pack_confirmation_requires_current_closed_list_and_resets_selection(self):
        monitor=Monitor(self.db,self.output)
        monitor.tracker=Tracker(self.output)
        monitor.running=True;monitor.tracker.state='SONG_SELECT'
        monitor.tracker.selection=dict(observation_id='current')
        body=dict(observation_id='current',pack_id=1,root_id=1,acknowledge=True)
        monitor.tracker.pack_list_visible=True
        with self.assertRaises(LibraryError):monitor.confirm_pack(body)
        monitor.tracker.pack_list_visible=False
        result=monitor.confirm_pack(body)
        self.assertEqual(result['tracking']['pack_confidence'],'CONFIRMED')
        self.assertEqual(result['tracking']['pack']['source'],'explicit_user_review')
        self.assertIsNone(monitor.tracker.selection)
        self.assert_database_unchanged()


if __name__=='__main__':unittest.main()
