"""Phase 5A.1: isolated catalogs; real screenshot replay is in the report tool."""
import copy
from contextlib import closing
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
import test_result_review as fixture
import test_official_import as official_fixture
from dance_results.library_service import LibraryError, LibraryService
from dance_results.gui import create_server
from dance_results.monitor_ocr import apply_selection
from dance_results.monitor_tracking import ManualReferences, Tracker, atomic_json
from dance_results.note_evidence import note_evidence
from dance_results.ocr_matching import ReadOnlyCatalog
from dance_results.official_import import read_catalog, import_catalog
from dance_results.result_review import ResultReviews
from dance_results.submissions import SubmissionService
from dance_results.sqlite_safety import audit


def fields(**changes):
    values=dict(title='Heart of Witch',difficulty='HARD',game_level=29,total_notes=1194,bpm=170)
    values.update(changes)
    return {k:dict(value=v,confidence=.99,reason=None) for k,v in values.items()}


class NoteEvidenceTests(unittest.TestCase):
    def evidence(self, **changes):
        v=fixture.values(perfect=1145,cool=30,bad=7,miss=10,max_combo=543,total_notes=1194,game_level=29)
        v.update(changes)
        return note_evidence(dict(selection=dict(fields=fields())),v,
            dict(total_notes=1194,notes_verified=1,notes_source='official_csv',difficulty_id=7450),
            identity_confirmed=True,ocr_values=v)

    def test_heart_count_provenance_and_conditional_not_formal_score(self):
        n=self.evidence()
        self.assertEqual([n[k] for k in ('selected_total_notes','database_total_notes','judged_total','verified_total_notes')],
                         [1194,1194,1192,1194])
        self.assertEqual(n['status'],'count_discrepancy_pending')
        self.assertEqual(n['conditional_score']['ex_score'],2320)
        self.assertEqual(n['conditional_score']['max_score'],2388)
        self.assertEqual(n['conditional_score']['display'],'MAX -68')
        self.assertFalse(n['conditional_score']['confirmed'])

    def test_missing_selection_is_unknown_not_filled_from_catalog(self):
        n=note_evidence({},fixture.values(),dict(total_notes=11,notes_verified=1),identity_confirmed=True)
        self.assertIsNone(n['selected_total_notes']);self.assertEqual(n['database_total_notes'],11)

    def test_catalog_enrichment_never_becomes_selected_ocr_evidence(self):
        s=dict(selection=dict(fields=fields(),ocr_fields=fields(total_notes=None)))
        n=note_evidence(s,fixture.values(),None)
        self.assertIsNone(n['selected_total_notes'])
        s['selection'].pop('ocr_fields');s['selection']['fields']['total_notes']['method']='official_catalog_fill'
        self.assertIsNone(note_evidence(s,fixture.values())['selected_total_notes'])

    def test_null_judgment_does_not_become_zero(self):
        n=self.evidence(cool=None)
        self.assertIsNone(n['judged_total']);self.assertIsNone(n['conditional_score'])

    def test_gameover_partial_is_not_full_notes(self):
        n=note_evidence({},fixture.values(completion_status='FAILED',total_notes=None))
        self.assertEqual(n['status'],'failed_partial_judgments')
        self.assertIsNone(n['verified_total_notes']);self.assertIsNone(n['conditional_score'])

    def test_source_conflict_blocks_even_if_sum_matches_database(self):
        n=note_evidence(dict(selection=dict(fields=fields(total_notes=12))),fixture.values(),
                        dict(total_notes=11,notes_verified=1,notes_source='game_screen'),identity_confirmed=True)
        self.assertTrue(n['selected_database_conflict']);self.assertIsNone(n['conditional_score'])

    def test_differences_are_generic_no_two_note_exception(self):
        for count in (1191,1192,1193,1195):
            n=self.evidence(perfect=count-47)
            self.assertEqual(n['status'],'count_discrepancy_pending')

    def test_catalog_difference_visible_before_human_confirmation_remains_conditional(self):
        n=note_evidence({},fixture.values(perfect=1145,cool=30,bad=7,miss=10,max_combo=543,total_notes=1194),
            dict(total_notes=1194,notes_verified=1),identity_confirmed=False)
        self.assertIsNone(n['verified_total_notes'])
        self.assertEqual(n['status'],'count_discrepancy_pending')
        self.assertEqual(n['conditional_score']['total_notes'],1194)
        self.assertEqual(n['conditional_score']['display'],'MAX -68')
        self.assertFalse(n['conditional_score']['confirmed'])


class AssistanceTests(unittest.TestCase):
    setUp=fixture.ReviewPersistenceTests.setUp
    tearDown=fixture.ReviewPersistenceTests.tearDown
    body=fixture.ReviewPersistenceTests.body

    def official(self):
        row=official_fixture.raw_row(fileId='15007',gn='heart.gn',title='Heart of Witch',bpm='170',
            lvHard='29',notesHard='1194')
        import_catalog(self.db,read_catalog(official_fixture.csv_bytes([row]),'test-only.csv'))
        catalog=ReadOnlyCatalog(self.db)
        try:return next(s for s in catalog.songs.values() if s['source_type']=='official')
        finally:catalog.close()

    def heart_review(self):
        song=self.official()
        folder=self.output/'sessions'/self.sid
        row=copy.deepcopy(self.row);session=copy.deepcopy(self.session)
        session.update(selection=None,song_id=None,matching_status='pending_manual_review')
        row['locked_session']=session
        changes=dict(title='Heart of Witch',game_level=29,perfect=1145,cool=30,bad=7,miss=10,max_combo=543)
        for k,v in changes.items():row['ocr']['fields'][k]['value']=v
        row['draft']['song_id']=None
        row['draft']['values'].update({k:v for k,v in changes.items() if k!='title'})
        row['draft']['values'].update(total_notes=None,difficulty=None,duration_seconds=None)
        atomic_json(folder/'session.json',session);atomic_json(folder/'result-review.json',row)
        self.row=row
        b=self.body(song_id=None,root_id=None,relative_path=None,values=copy.deepcopy(row['draft']['values']),
            notes_verified=False,identity_reviewed=False,player_verified=False,confirm=False)
        return song,b

    def test_official_unique_chart_autofill_requires_explicit_source_and_difficulty(self):
        song,b=self.heart_review()
        b['official_source_confirmed']=True
        self.assertIsNone(self.service.assist(b)['selected_song'])
        b['values']['difficulty']='HARD';b['official_source_confirmed']=False
        self.assertIsNone(self.service.assist(b)['selected_song'])
        b['official_source_confirmed']=True
        before=(self.output/'sessions'/self.sid/'result-review.json').read_bytes()
        result=self.service.assist(b)
        self.assertEqual(result['selected_song']['song_id'],song['song_id'])
        self.assertEqual(result['patch'],dict(total_notes=1194))
        self.assertEqual(result['selected_song']['official_source']['file_id'],'15007')
        self.assertEqual(result['selected_song']['official_source']['bpm_raw'],'170')
        self.assertFalse(result['confirmation_flags_changed'])
        self.assertEqual(before,(self.output/'sessions'/self.sid/'result-review.json').read_bytes())

    def test_heart_draft_restart_preserves_difference_and_prevents_confirm_and_submit(self):
        song,b=self.heart_review()
        b.update(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'],
                 identity_reviewed=True,player_verified=True)
        b['values'].update(total_notes=1194,difficulty='HARD')
        p=self.service.preview(b)
        self.assertFalse(p['can_confirm']);self.assertIsNone(p['display'])
        self.assertEqual(p['note_sources']['conditional_score']['display'],'MAX -68')
        saved=self.service.save(b)
        self.assertEqual(saved['status'],'draft')
        fresh=ResultReviews(self.db,self.output,scoring_policy='independent-max-v2').detail(self.sid)
        self.assertEqual(fresh['note_sources']['judged_total'],1192)
        self.assertEqual(fresh['draft']['values']['total_notes'],1194)
        b['revision']=saved['revision'];b['confirm']=True
        with self.assertRaises(LibraryError):self.service.save(b)
        prepared=SubmissionService(LibraryService(self.db),self.service).prepare({**b,'play_mode':'single'})
        self.assertFalse(prepared['can_submit'])
        self.assertIn('note_source_difference_requires_review',{i['code'] for i in prepared['issues']})
        with closing(sqlite3.connect(self.db)) as db, db:self.assertEqual(db.execute('select count(*) from play_records').fetchone()[0],0)

    def test_existing_values_are_never_overwritten_and_conflicts_are_listed(self):
        song,b=self.heart_review()
        b.update(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'])
        b['values'].update(difficulty='HARD',total_notes=1192)
        result=self.service.assist(b)
        self.assertIn('total_notes',result['conflicts']);self.assertNotIn('total_notes',result['patch'])

    def test_explicit_song_uses_verified_level_and_notes_but_never_invents_duration(self):
        song,b=self.heart_review()
        b.update(song_id=song['song_id'],root_id=song['root_id'],relative_path=song['relative_folder'])
        b['values'].update(difficulty='HARD',game_level=None)
        result=self.service.assist(b)
        self.assertEqual(result['patch'],dict(game_level=29,total_notes=1194))
        self.assertNotIn('duration_seconds',result['patch'])

    def test_official_namesake_custom_never_chosen_from_title_only(self):
        self.official()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE songs SET saved_title='Heart of Witch',latest_title='Heart of Witch' WHERE song_id=1")
            db.execute("UPDATE song_sources SET saved_title='Heart of Witch' WHERE song_id=1")
        catalog=ReadOnlyCatalog(self.db)
        try:
            f=fields()
            self.assertEqual(catalog.match(f)['candidate_count'],2)
            self.assertIsNone(catalog.match_official(f)['confirmed_song_id'])
            f['game_level']['confidence']=.5
            self.assertIsNone(catalog.match_official(f,source_confirmed=True)['confirmed_song_id'])
        finally:catalog.close()

    def test_official_notes_are_advisory_for_unique_title_difficulty_level(self):
        self.official();catalog=ReadOnlyCatalog(self.db)
        try:
            f=fields();f['total_notes']['confidence']=.6
            self.assertIsNotNone(catalog.match_official(f,source_confirmed=True)['confirmed_song_id'])
            f=fields(total_notes=1192)
            self.assertIsNotNone(catalog.match_official(f,source_confirmed=True)['confirmed_song_id'])
            for gd in catalog.difficulties.values():
                gd['notes_source'] = 'game_screen'
            self.assertIsNotNone(catalog.match_official(f,source_confirmed=True)['confirmed_song_id'])
        finally:catalog.close()

    def test_live_official_selection_retains_original_missing_notes_and_locks_correct_song(self):
        song=self.official();catalog=ReadOnlyCatalog(self.db);tracker=Tracker(self.output)
        try:
            obs=dict(state='SONG_SELECT',selection=dict(fields=fields(total_notes=None),pack_list_visible=False),frame_hash='frame')
            result=apply_selection(obs,Image.new('RGB',(1024,800)),tracker,catalog,source_mode='official')
            self.assertEqual(result['fields']['total_notes']['value'],1194)
            self.assertIsNone(result['ocr_fields']['total_notes']['value'])
            for t in (1,2):tracker.consume(result,now=t,sample_id=t)
            self.assertEqual(tracker.selection['matching']['confirmed_song_id'],song['song_id'])
            self.assertIsNone(tracker.selection['ocr_fields']['total_notes']['value'])
            for t in (3,4):tracker.consume(dict(state='PLAYING',frame_hash='playing',playing_preview=dict(title='Heart of Witch',game_level=29)),now=t,sample_id=t)
            self.assertEqual(tracker.session['song_id'],song['song_id'])
        finally:catalog.close()

    def test_two_fully_matching_official_identities_still_require_manual_choice(self):
        self.official()
        raw=official_fixture.raw_row(fileId='15008',gn='other.gn',title='Heart of Witch',bpm='170',lvHard='29',notesHard='1194')
        import_catalog(self.db,read_catalog(official_fixture.csv_bytes([raw]),'other.csv'))
        c=ReadOnlyCatalog(self.db)
        try:self.assertIsNone(c.match_official(fields(),source_confirmed=True)['confirmed_song_id'])
        finally:c.close()

    def test_missing_metadata_on_namesake_cannot_eliminate_it(self):
        self.official()
        raw=official_fixture.raw_row(fileId='15008',gn='unknown.gn',title='Heart of Witch',bpm='170',lvHard='29',notesHard='1194')
        import_catalog(self.db,read_catalog(official_fixture.csv_bytes([raw]),'other.csv'))
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE game_difficulties SET total_notes=NULL,notes_verified=0,notes_source=NULL WHERE song_id=(SELECT song_id FROM official_song_sources WHERE gn='unknown.gn') AND difficulty='HARD'")
        c=ReadOnlyCatalog(self.db)
        try:self.assertIsNone(c.match_official(fields(),source_confirmed=True)['confirmed_song_id'])
        finally:c.close()

    def test_complete_custom_manual_confirmation_can_be_reused_after_restart(self):
        b=self.body(duration_verified=True)
        b['values']['duration_seconds']=96
        self.service.save(b)
        refs=ManualReferences(self.output,self.db);c=ReadOnlyCatalog(self.db)
        f=fields(title='JOINT',game_level=17,total_notes=11,duration_seconds=96,bpm=None)
        try:
            self.assertEqual(refs.lookup(f,1,'custom',c.songs)['song_id'],1)
            # An unknown same-title SongFolder is never eliminated by one reference.
            self.assertIsNone(refs.safe_lookup(f,1,'custom',c,c.match(f,1,'custom')))
            self.assertIsNone(refs.safe_lookup(f,None,'custom',c,c.match(f,None,'custom')))
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE songs SET saved_title='Other',latest_title='Other' WHERE song_id=2")
                db.execute("UPDATE song_sources SET saved_title='Other' WHERE song_id=2")
            c.close();c=ReadOnlyCatalog(self.db)
            self.assertEqual(refs.safe_lookup(f,1,'custom',c,c.match(f,1,'custom'))['song_id'],1)
            changed=copy.deepcopy(f);changed['duration_seconds']['value']=95
            self.assertIsNone(refs.safe_lookup(changed,1,'custom',c,c.match(changed,1,'custom')))
        finally:c.close()

    def test_incomplete_or_edited_evidence_does_not_create_trusted_reusable_mapping(self):
        self.service.save(self.body())
        self.assertNotIn('reusable_mapping',self.service.detail(self.sid))
        b=self.body(duration_verified=True,revision=1);b['values']['duration_seconds']=96
        self.service.save(b)
        (self.output/'sessions'/self.sid/'result.png').write_bytes(b'changed')
        self.assertFalse(ManualReferences(self.output,self.db).data)

    def test_draft_only_or_missing_cool_never_becomes_trusted(self):
        b=self.body(duration_verified=True,confirm=False);b['values'].update(cool=None,duration_seconds=96)
        self.service.save(b)
        self.assertFalse(ManualReferences(self.output,self.db).data)

    def test_old_complete_confirmation_is_read_only_reusable_without_changing_sidecar(self):
        b=self.body(duration_verified=True);b['values']['duration_seconds']=96
        saved=self.service.save(b)
        path=self.output/'sessions'/self.sid/'result-review.json'
        saved.pop('reusable_mapping',None)
        # Saved API detail includes computed fields; use only original sidecar.
        row=json.loads(path.read_text(encoding='utf8'));row.pop('reusable_mapping',None)
        atomic_json(path,row);before=path.read_bytes()
        refs=ManualReferences(self.output,self.db)
        c=ReadOnlyCatalog(self.db)
        try:
            f=fields(title='JOINT',game_level=17,total_notes=11,duration_seconds=96)
            self.assertEqual(refs.lookup(f,1,'custom',c.songs)['song_id'],1)
            self.assertEqual(path.read_bytes(),before)
        finally:c.close()

    def test_custom_assistance_reuses_complete_unique_mapping_but_not_unknown_namesake(self):
        b=self.body(duration_verified=True);b['values']['duration_seconds']=96
        self.service.save(b)
        sid=str(uuid4());folder=self.output/'sessions'/sid;folder.mkdir(parents=True)
        session=copy.deepcopy(self.session)
        session.update(session_id=sid,song_id=None,matching_status='pending_manual_review')
        session['selection'].update(source_type='custom',pack=dict(pack_id=1,status='confirmed',name='Pack A'))
        atomic_json(folder/'session.json',session);Image.new('RGB',(1024,800),'gray').save(folder/'result.png')
        row=self.service.ingest(session,self.ocr)
        request=dict(session_id=sid,song_id=None,values=copy.deepcopy(row['draft']['values']))
        self.assertIsNone(self.service.assist(request)['selected_song'])
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE songs SET saved_title='Different',latest_title='Different' WHERE song_id=2")
            db.execute("UPDATE song_sources SET saved_title='Different' WHERE song_id=2")
        result=self.service.assist(request)
        self.assertEqual(result['selected_song']['song_id'],1)
        self.assertEqual(result['method'],'confirmed_manual_reference')
        self.assertTrue(result['applies_automatically'])

    def test_source_conflict_rejects_formal_even_when_judgments_match_canonical(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(1,'HARD',17,'manual',12,'game_screen',1)")
        b=self.body(values=fixture.values(perfect=11,cool=1,max_combo=12,total_notes=12))
        p=self.service.preview(b)
        self.assertIn('selected_database_notes_conflict',{i['code'] for i in p['issues']})
        self.assertFalse(p['can_confirm']);self.assertIsNone(p['display'])
        with self.assertRaises(LibraryError):self.service.save(b)

    def test_assist_http_authentication_and_no_implicit_database_or_review_writes(self):
        song,b=self.heart_review();b['official_source_confirmed']=True;b['values']['difficulty']='HARD'
        path=self.output/'sessions'/self.sid/'result-review.json';before=path.read_bytes()
        with closing(sqlite3.connect(self.db)) as db:database_before=audit(db)
        server=create_server(self.db,monitor_directory=self.output)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}/api/reviews/assist'
        try:
            request=Request(url,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as error:urlopen(request)
            self.assertEqual(error.exception.code,403)
            request.add_header('X-Dance-Token',server.session_token)
            with urlopen(request) as response:r=json.load(response)
            self.assertEqual(r['selected_song']['song_id'],song['song_id'])
            self.assertEqual(r['patch'],dict(total_notes=1194))
            self.assertEqual(path.read_bytes(),before)
            with closing(sqlite3.connect(self.db)) as db:self.assertEqual(audit(db),database_before)
        finally:server.shutdown();server.server_close();thread.join()

    def test_low_confidence_original_level_is_not_promoted_by_automatic_form_values(self):
        _,b=self.heart_review();b.update(official_source_confirmed=True)
        b['values']['difficulty']='HARD'
        path=self.output/'sessions'/self.sid/'result-review.json'
        r=json.loads(path.read_text(encoding='utf8'));r['ocr']['fields']['game_level']['confidence']=.6
        atomic_json(path,r)
        self.assertIsNone(self.service.assist(b)['selected_song'])

    def test_invalid_level_cannot_identify_but_optional_notes_do_not_decide_unique_chart(self):
        self.official();c=ReadOnlyCatalog(self.db)
        try:
            for key,number in [('game_level',True),('game_level',0),('game_level','29'),
                               ('total_notes',True),('total_notes',0),('total_notes',1194.0)]:
                f=fields(**{key:number})
                matched = c.match_official(f,source_confirmed=True)['confirmed_song_id']
                if key == 'game_level':
                    self.assertIsNone(matched)
                else:
                    self.assertIsNotNone(matched)
        finally:c.close()

    def test_manual_reference_cannot_override_conflicting_verified_catalog_notes(self):
        b=self.body(duration_verified=True);b['values']['duration_seconds']=96
        self.service.save(b)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO game_difficulties(song_id,difficulty,game_level,level_source,total_notes,notes_source,notes_verified) VALUES(1,'HARD',17,'manual',12,'manual',1)")
        refs=ManualReferences(self.output,self.db);c=ReadOnlyCatalog(self.db)
        try:
            f=fields(title='JOINT',game_level=17,total_notes=11,duration_seconds=96)
            self.assertIsNone(refs.safe_lookup(f,1,'custom',c,c.match(f,1,'custom')))
        finally:c.close()
