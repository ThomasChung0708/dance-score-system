"""Phase 4C supplement: isolated filesystem/HTTP/session simulations, not live play."""
import copy
import json
import sqlite3
import threading
import time
from contextlib import closing
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import unittest

import test_result_review as fixtures
from test_result_review import result_tokens, token, values
from dance_results.database import LibraryDatabase
from dance_results.gui import create_server
from dance_results.library_service import LibraryService, LibraryError
from dance_results.result_ocr import parse_result
from dance_results.result_review import ResultReviews, score_preview
from dance_results.scanner import scan_library
from dance_results.sqlite_safety import check_integrity


class MultiplayerReviewTests(unittest.TestCase):
    setUp = fixtures.ReviewPersistenceTests.setUp
    tearDown = fixtures.ReviewPersistenceTests.tearDown
    body = fixtures.ReviewPersistenceTests.body
    assert_database_unchanged = fixtures.ReviewPersistenceTests.assert_database_unchanged

    def add(self, pack, folder, title='JOINT', contents=None):
        p=self.base/'SONG'/pack/folder/'song.sm';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(contents if contents is not None else '#TITLE:'+title+';',encoding='utf8')
        return p

    def sync(self):
        with LibraryDatabase(self.db) as db:
            report=scan_library(self.base/'SONG',defer_not_ready=True);db.sync(report)
            check_integrity(db.connection)
            return report.database['changes']

    def unknown_pack(self):
        path=self.output/'sessions'/self.sid/'result-review.json'
        path.unlink();session=copy.deepcopy(self.session)
        session.update(song_id=None,selection=None,matching_status='pending_manual_review')
        from dance_results.monitor_tracking import atomic_json
        atomic_json(path.parent/'session.json',session)
        self.service.ingest(session,self.ocr)

    def test_no_pack_searches_connect_and_other_packs_without_auto_binding(self):
        self.unknown_pack()
        self.add('connect','Download');self.add('One Song Pack','Only Song')
        self.sync()
        result=self.service.candidates('JOINT')
        self.assertEqual(result['count'],4)
        self.assertEqual({s['pack_name'] for s in result['candidates']},{'Pack A','connect','One Song Pack'})
        self.assertEqual(len({s['song_id'] for s in result['candidates']}),4)
        self.assertIsNone(result['auto_selected_song_id'])
        for song in result['candidates']:
            self.assertEqual(song['acquisition_method'],'unknown')
            self.assertFalse(song['acquisition_verified'])
            self.assertTrue(song['sources'][0]['relative_path'])
            self.assertFalse(song['comparison']['automatic_binding'])
        detail=self.service.detail(self.sid)
        self.assertIsNone(detail['draft']['song_id'])
        self.assertIsNone(detail['locked_session']['selection'])
        self.assertIsNone(detail['preview']['max_score'])

    def test_all_candidate_pages_are_accessible_and_never_select_first(self):
        for n in range(125):self.add('Many','Folder '+str(n),'Repeat')
        self.sync();ids=[]
        for offset in (0,60,120):
            result=self.service.candidates('Repeat',offset=offset)
            ids.extend(s['song_id']for s in result['candidates'])
            self.assertEqual(result['count'],125)
            self.assertIsNone(result['auto_selected_song_id'])
        self.assertEqual(len(set(ids)),125)
        self.assertFalse(result['has_more'])

    def test_rescan_new_pack_and_existing_pack_preserves_old_ids_and_history(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            gd=db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,total_notes,notes_verified,notes_source,level_source) VALUES(1,'HARD',17,11,1,'manual','manual')").lastrowid
            db.execute("INSERT INTO play_sessions(session_id,player_name,play_mode,room_status,player_status) VALUES('history-fixture','TC','multiplayer','result','failed')")
            db.execute("INSERT INTO play_records(session_id,difficulty_id,played_at,completion_status,perfect,cool,bad,miss,ex_score,lamp_status,match_status,validation_status) VALUES('history-fixture',?,'2026-10-23','FAILED',1,0,0,1,2,'FAILED','confirmed','verified')",(gd,))
            db.commit()
            before={t:list(db.execute('SELECT * FROM '+t)) for t in ('play_records','game_difficulties')}
            songs=dict(db.execute('SELECT song_id,relative_folder FROM songs'))
            sources=dict(db.execute('SELECT source_id,relative_path FROM song_sources'))
            packs=dict(db.execute('SELECT pack_id,relative_path FROM packs'))
        review=(self.output/'sessions'/self.sid/'result-review.json').read_bytes()
        self.add('New Download Pack','New Song');self.add('Pack A','New Folder')
        result=self.sync();self.assertEqual(result['new_packs'],1);self.assertEqual(result['new_songs'],2)
        again=self.sync();self.assertEqual(again['new_songs'],0);self.assertEqual(again['new_sources'],0)
        with closing(sqlite3.connect(self.db)) as db:
            for table,old,column,key in [('songs',songs,'relative_folder','song_id'),('song_sources',sources,'relative_path','source_id'),('packs',packs,'relative_path','pack_id')]:
                current=dict(db.execute('SELECT '+key+','+column+' FROM '+table))
                self.assertEqual({i:current[i]for i in old},old)
            for t,rows in before.items():self.assertEqual(list(db.execute('SELECT * FROM '+t)),rows)
        self.assertEqual((self.output/'sessions'/self.sid/'result-review.json').read_bytes(),review)

    def test_unready_download_deferred_then_imported_once_without_losing_old_source(self):
        old=self.base/'SONG'/'Pack A'/'Folder A'/'song.sm';old.write_text('',encoding='utf8')
        self.add('Pack A','Folder A',contents='')
        (old.parent/'other.sm').write_text('#TITLE:Do not choose this partial folder title;',encoding='utf8')
        new=self.add('New Pack','Incomplete',contents='')
        result=self.sync();self.assertEqual(result['new_songs'],0)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT saved_title,is_present FROM song_sources WHERE source_id=1').fetchone(),('JOINT',1))
            self.assertEqual(db.execute('SELECT saved_title FROM songs WHERE song_id=1').fetchone()[0],'JOINT')
        new.write_text('#TITLE:Finished;',encoding='utf8')
        self.assertEqual(self.sync()['new_songs'],1)
        self.assertEqual(self.sync()['new_songs'],0)

    def test_file_changing_during_stability_interval_is_deferred(self):
        new=self.add('New Pack','Changing')
        def change(_):new.write_text('#TITLE:Changed during download;',encoding='utf8')
        with patch('dance_results.scanner.time.sleep',side_effect=change):
            report=scan_library(self.base/'SONG',defer_not_ready=True)
        self.assertTrue(any(d.code=='SOURCE_NOT_READY' for d in report.diagnostics))
        self.assertFalse(any(f.path==str(new) for f in report.files))

    def test_no_candidate_result_and_manual_draft_survive_restart(self):
        self.unknown_pack()
        saved=self.service.save(self.body(song_id=None,confirm=False,confirmation_note='Pack unknown; keep original'))
        restored=ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(self.sid)
        self.assertEqual(restored['draft']['confirmation_note'],'Pack unknown; keep original')
        self.assertIsNone(restored['draft']['song_id'])
        self.assertEqual(restored['ocr'],saved['ocr'])
        self.assertEqual(self.service.candidates('Not on disk')['count'],0)
        self.assert_database_unchanged()

    def test_manual_identity_duration_evidence_and_idempotency_survive_restart(self):
        self.unknown_pack()
        body=self.body(values=values(duration_seconds=96),duration_verified=True,confirmation_note='Compared actual Pack A / Folder A, HARD 17')
        saved=self.service.save(body);again=self.service.save(body)
        restored=ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(self.sid)
        self.assertEqual(saved['revision'],again['revision'])
        self.assertIsNone(restored['identity_confirmation']['observed_pack'])
        self.assertEqual(restored['identity_confirmation']['song_id'],1)
        self.assertTrue(restored['preview']['readiness']['ready_for_phase5_review'])
        self.assertFalse(restored['preview']['readiness']['formal_storage_enabled'])
        self.assert_database_unchanged()

    def test_missing_or_conflicting_verified_metadata_is_not_a_match(self):
        candidate=self.service.candidates('JOINT',values=values(duration_seconds=96))['candidates'][0]
        self.assertIn('verified_game_level',candidate['comparison']['missing'])
        self.assertIn('verified_duration_seconds',candidate['comparison']['missing'])
        self.assertFalse(candidate['comparison']['automatic_binding'])
        with self.assertRaises(LibraryError):self.service.candidates('JOINT',source_type='connect')

    def test_manual_player_row_second_failed_while_first_completed(self):
        rows=result_tokens()
        rows=[dict(t,text='Other') if t['text']=='TC' else t for t in rows]
        rows += [token('TC',185,318),token('238',368,318),token('585',480,318),token('32',556,318),
                 token('2',635,318),token('45',713,318),token('35575',888,318),token('F',980,318),token('OVER',23,318)]
        ocr=parse_result(rows,'Unconfigured');ocr['tokens']=rows
        self.assertEqual(ocr['local_player_status'],'UNKNOWN')
        self.assertIsNone(ocr['fields']['perfect']['value'])
        self.assertEqual([r['name']for r in ocr['player_rows']],['Other','TC'])
        self.assertEqual(ocr['player_rows'][0]['fields']['completion_status']['value'],'COMPLETED')
        path=self.output/'sessions'/self.sid/'result-review.json';path.unlink();self.service.ingest(self.session,ocr)
        body=self.body(player_row_id='row-1',notes_verified=False,values=values(perfect=585,cool=32,bad=2,miss=45,
            max_combo=238,game_score=35575,grade='F',game_level=38,completion_status='FAILED',total_notes=None,player_name='TC'))
        result=self.service.save(body)
        self.assertEqual(result['identity_confirmation']['player_row_id'],'row-1')
        self.assertEqual(result['preview']['ex_score'],1202)
        self.assertEqual(result['preview']['judged_notes'],664)
        self.assertEqual(result['preview']['lamp_preview'],'FAILED')
        self.assertIsNone(result['preview']['max_score'])
        self.assertIsNone(result['preview']['percentage'])
        self.assert_database_unchanged()

    def test_invalid_manual_player_row_rejected_and_gameover_cannot_clear(self):
        with self.assertRaises(LibraryError):self.service.preview(self.body(player_row_id='row-99'))
        rows=result_tokens()+[token('OVER',23,250)]
        ocr=parse_result(rows);ocr['tokens']=rows
        (self.output/'sessions'/self.sid/'result-review.json').unlink();self.service.ingest(self.session,ocr)
        self.assertFalse(self.service.preview(self.body(player_row_id='row-0'))['can_confirm'])

    def test_http_rescan_reuses_background_full_root_and_updates_candidates_only(self):
        server=create_server(self.db,monitor_directory=self.output)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        headers={'X-Dance-Token':server.session_token,'Content-Type':'application/json'}
        def request(path,body=None):
            return json.load(urlopen(Request(base+'/api/reviews'+path,headers=headers,
                data=None if body is None else json.dumps(body).encode())))
        try:
            roots=request('/scan')['roots'];self.assertEqual(len(roots),1)
            review=(self.output/'sessions'/self.sid/'result-review.json').read_bytes()
            self.add('Outside connect','Network download','Newly Downloaded')
            with self.assertRaises(HTTPError):urlopen(Request(base+'/api/reviews/rescan',data=b'{}',headers={'Content-Type':'application/json'}))
            with self.assertRaises(HTTPError):request('/rescan',{'session_id':self.sid,'review_id':'wrong','root_id':1})
            job=request('/rescan',{'session_id':self.sid,'review_id':self.row['review_id'],'root_id':roots[0]['root_id']})
            self.assertEqual(job['scan_scope'],'whole_song_root')
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                status=request('/scan')['scan']
                if status['status']!='running':break
                time.sleep(.03)
            self.assertEqual(status['status'],'completed')
            self.assertEqual(status['changes']['new_packs'],1)
            self.assertEqual(request('/candidates?q=Newly%20Downloaded')['count'],1)
            self.assertEqual((self.output/'sessions'/self.sid/'result-review.json').read_bytes(),review)
        finally:server.shutdown();server.server_close();thread.join(3)

    def test_inventory_overlay_does_not_use_underlying_room_chart(self):
        from dance_results.ocr_screens import classify
        self.assertEqual(classify([token('更衣間 INVENTORY',200,140),token('SPEED',880,480),token('難度38',900,630)])[0],'inventory')


if __name__=='__main__':unittest.main()
