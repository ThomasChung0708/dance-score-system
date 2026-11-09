"""Downloaded-song attribution and compact GUI projections, isolated DB only."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import unittest
import test_phase5d as fixtures
from dance_results.database import LibraryDatabase
from dance_results.scanner import scan_library
from dance_results.download_tracking import download_view,related_new_songs,session_download
from dance_results.recent_updates import recent_updates


class DownloadScopeTests(unittest.TestCase):
    def setUp(self):
        fixtures.DownloadTests.setUp(self)
        self.session.update(song_id=None,matching_status='pending_manual_review')
        folder,row=self.service._read(self.sid)
        row['locked_session']=copy.deepcopy(self.session)
        row['draft'].update(song_id=None,identity_reviewed=False,player_verified=False)
        fixtures.atomic_json(folder/'session.json',self.session)
        fixtures.atomic_json(folder/'result-review.json',row)
    tearDown=fixtures.DownloadTests.tearDown
    tearDown_fixture=fixtures.DownloadTests.tearDown_fixture
    begin=fixtures.DownloadTests.begin
    add=fixtures.DownloadTests.add
    poll=fixtures.DownloadTests.poll
    body=fixtures.DownloadTests.body

    def test_barry_old_unregistered_sources_never_become_new_downloads(self):
        for i in range(35):self.add('Barry',str(i),title='Old '+str(i))
        with LibraryDatabase(self.db) as lib:lib.sync(scan_library(self.root))
        with closing(sqlite3.connect(self.db)) as db:before=db.execute('SELECT song_id,relative_folder FROM songs ORDER BY song_id').fetchall()
        # Old sources already on disk, but not yet registered in SQLite.
        for i in range(35):self.add('Barry',str(i),'dwi',title='Old '+str(i))
        self.begin();self.add();self.poll()
        e=session_download(self.output,self.sid)
        self.assertEqual(len(e['candidates']),1);self.assertEqual(e['candidates'][0]['pack'],'connect')
        self.assertEqual(len(e['existing_updates']),35)
        ui=self.service.detail(self.sid)
        self.assertNotIn('Barry',json.dumps(ui['download_evidence'],ensure_ascii=False))
        self.assertEqual(ui['download_evidence']['new_song_count'],1)
        self.assertEqual(len(ui['candidates']),1)
        self.assertTrue(any(c['pack_name']=='Barry' for c in self.service.candidates('Old')['candidates']))
        with closing(sqlite3.connect(self.db)) as db:
            after=dict(db.execute('SELECT song_id,relative_folder FROM songs'))
            self.assertTrue(all(after[sid]==folder for sid,folder in before))
        scan_id=e['scan_results'][0]['scan_id']
        report=json.loads((self.output/'downloads'/e['episode_id']/f'scan-{scan_id}.json').read_text(encoding='utf8'))
        self.assertGreater(len(report['songs']),35)

    def test_completed_before_snapshot_is_unknown_even_when_imported_now(self):
        self.add('Old On Disk','Not Observed');self.begin();self.poll()
        e=session_download(self.output,self.sid)
        self.assertEqual(e['candidates'],[]);self.assertEqual(len(e['uncertain_imports']),1)
        ui=self.service.detail(self.sid)
        self.assertTrue(ui['download_evidence']['acquisition_uncertain'])
        self.assertEqual(ui['download_evidence']['new_song_count'],0)
        self.assertEqual(ui['candidates'],[])

    def test_no_observed_change_and_old_bad_source_does_not_repeat_full_scan(self):
        p=self.add('Old Broken','Old');p.write_text('#TITLE:partial',encoding='utf8')
        self.begin();self.poll()
        self.assertTrue(self.dl.active['scan_completed'])
        self.assertTrue(self.dl.status()['acquisition_uncertain'])
        scans=len(self.dl.active['scan_results']);self.poll()
        self.assertEqual(len(self.dl.active['scan_results']),scans)

    def test_existing_song_source_update_is_separate_and_id_stable(self):
        self.begin();self.add('Pack A','Folder A','dwi');self.poll()
        e=session_download(self.output,self.sid)
        self.assertEqual(e['candidates'],[])
        self.assertEqual([c['song_id'] for c in e['existing_updates']],[1])
        self.assertEqual(self.dl.status()['new_song_count'],0)
        self.assertNotIn('existing_updates',self.dl.status())
        self.assertTrue(any(r['song_id']==1 and r['change']=='added' for r in e['source_changes']))

    def test_only_related_new_titles_default_other_packs_searchable(self):
        self.begin();self.add('connect','This','sm','JOINT');self.add('New Pack','Other','osu','Unrelated');self.poll()
        detail=self.service.detail(self.sid)
        self.assertEqual(detail['download_evidence']['new_song_count'],2)
        self.assertEqual(len(detail['candidates']),1)
        self.assertEqual(detail['candidates'][0]['title'],'JOINT')
        self.assertNotIn('Unrelated',json.dumps(detail['download_evidence']))
        self.assertEqual(self.service.candidates('Unrelated')['count'],1)
        self.assertIsNone(detail['draft']['song_id'])

    def test_no_matching_title_has_no_automatic_full_library_fallback(self):
        self.begin();self.add('New Pack','Wrong','sm','Other');self.poll()
        d=self.service.detail(self.sid)
        self.assertEqual(d['candidates'],[])
        self.assertEqual(d['candidate_count'],0)
        self.assertGreater(self.service.candidates('JOINT')['count'],1)

    def test_missing_title_never_expands_all_new_songs(self):
        self.begin();self.add();self.poll();e=session_download(self.output,self.sid)
        e['room_observation']={};row=dict(locked_session={},ocr={})
        self.assertEqual(related_new_songs(e,row),[])

    def test_retry_new_song_ids_survive_partial_first_scan(self):
        self.begin();self.add('Ready','One');p=self.add('Still Writing','Two');p.write_text('#TITLE:incomplete',encoding='utf8')
        self.poll();self.assertFalse(self.dl.active['scan_completed'])
        p.write_text('#TITLE:JOINT;',encoding='utf8');self.poll()
        self.assertEqual(len(self.dl.active['candidates']),2)
        self.dl.active['_scanned']=None;self.poll()
        self.assertEqual(len(self.dl.active['candidates']),2)

    def test_legacy_diagnostics_remain_on_disk_but_no_huge_ui_list(self):
        legacy=dict(session_id=self.sid,status='pending_identity',scan_completed=True,self_no_map_confirmed=True,
                    candidates=[dict(song_id=i,title='Barry old',relative_path='Barry/old/'+str(i)) for i in range(200)],events=[])
        projection=download_view(legacy)
        self.assertEqual(projection['new_song_count'],0)
        self.assertEqual(projection['candidates'],[])
        self.assertTrue(projection['acquisition_uncertain'])
        self.assertNotIn('Barry',json.dumps(projection))
        self.assertEqual(len(legacy['candidates']),200)

    def test_scans_and_downloads_do_not_change_recent(self):
        with self.library.open() as lib:before=recent_updates(lib.connection)
        self.begin();self.add();self.poll()
        with self.library.open() as lib:self.assertEqual(before,recent_updates(lib.connection))

    def test_formal_saved_summary_requires_actual_record_receipt(self):
        self.begin();self.add();self.poll();e=session_download(self.output,self.sid)
        pending=download_view(e)
        self.assertFalse(pending['formal_saved']);self.assertTrue(pending['needs_manual_review'])
        saved=download_view(e,receipt=dict(record_id=9,song_id=e['candidates'][0]['song_id'],title='JOINT'))
        self.assertTrue(saved['formal_saved']);self.assertEqual(saved['record_id'],9)
        self.assertFalse(saved['needs_manual_review'])


if __name__=='__main__':unittest.main()
