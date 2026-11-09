"""Original screenshot OCR + real directory rescan on a SQLite COPY only.

Creates a separate clearly labelled GUI replay fixture, never modifies game files
or the production catalog and never claims a fresh live multiplayer playthrough.
Run with .venv-ocr/Scripts/python -m tools.validate_multiplayer_review.
"""
from contextlib import closing
from pathlib import Path
import copy
import hashlib
import json
import shutil
import sqlite3
import time
from uuid import uuid4

from PIL import Image
from dance_results.database import LibraryDatabase
from dance_results.library_service import LibraryService
from dance_results.monitor_ocr import Analyzer
from dance_results.monitor_tracking import atomic_json
from dance_results.ocr_engine import LocalOCREngine
from dance_results.result_review import ResultReviews, score_preview
from dance_results.scanner import scan_library
from dance_results.sqlite_safety import audit, check_integrity

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/phase4c/multiplayer'
PRODUCTION=ROOT/'data/library.sqlite3'
ASTRAL=ROOT/'data/monitor/sessions/f6bba0ba-582a-4e3f-a66a-71b06bff3da5/result.png'


def identities(db):
    return {table:{str(row[0]):list(row[1:]) for row in db.execute('SELECT '+fields+' FROM '+table)}
            for table,fields in [('packs','pack_id,root_id,relative_path'),('songs','song_id,root_id,relative_folder'),
                                 ('song_sources','source_id,song_id,relative_path')]}


def main():
    OUT.mkdir(parents=True,exist_ok=True);run=OUT/('run-'+uuid4().hex[:10]);run.mkdir()
    images=ROOT/'tests/專案測試圖片'
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in images.glob('*.PNG')}
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        baseline=audit(db);ids=identities(db)
        with closing(sqlite3.connect(run/'library.sqlite3')) as dest:db.backup(dest)
    atomic_json(run/'production-baseline.json',baseline)
    catalog=run/'library.sqlite3';service=LibraryService(catalog)
    roots=[r for r in service.bootstrap()['roots']if r['root_kind']=='filesystem']
    assert len(roots)==1
    scans=[]
    previous_ids=ids
    for index in range(2):
        root=Path(roots[0]['path'])
        filesystem_before={str(p.relative_to(root)):dict(size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns)
                           for p in root.glob('*/*/*') if p.is_file() and p.suffix.lower() in ('.sm','.dwi','.osu')}
        start=time.perf_counter();service.start_scan(roots[0]['root_id'],defer_not_ready=True)
        while service.scan_status()['status']=='running':
            atomic_json(run/'scan-progress.json',service.scan_status());time.sleep(.5)
        status=service.scan_status();assert status['status']=='completed',status
        filesystem_after={str(p.relative_to(root)):dict(size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns)
                          for p in root.glob('*/*/*') if p.is_file() and p.suffix.lower() in ('.sm','.dwi','.osu')}
        status['filesystem_new_during_scan']=sorted(set(filesystem_after)-set(filesystem_before))
        status['filesystem_changed_during_scan']=sorted(p for p in filesystem_before.keys()&filesystem_after.keys()
                                                       if filesystem_before[p]!=filesystem_after[p])
        with closing(sqlite3.connect(catalog)) as db:
            current_ids=identities(db)
            assert all(current_ids[t][i]==v for t,rows in previous_ids.items() for i,v in rows.items())
            assert db.execute('SELECT count(*) FROM (SELECT root_id,folder_key FROM songs GROUP BY root_id,folder_key HAVING count(*)>1)').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM (SELECT song_id,path_key FROM song_sources GROUP BY song_id,path_key HAVING count(*)>1)').fetchone()[0]==0
        previous_ids=current_ids
        status['elapsed_seconds']=round(time.perf_counter()-start,3);scans.append(status)
        atomic_json(run/f'scan-{index+1}.json',status)
        print('COPY scan',index+1,status,flush=True)
    with closing(sqlite3.connect(catalog)) as db:
        check_integrity(db);after_ids=identities(db)
        assert all(after_ids[t][i]==v for t,rows in ids.items() for i,v in rows.items())
        for table in ('game_difficulties','play_records','official_song_sources'):
            assert audit(db)[table]==baseline[table]
        copy_counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ids}
    # The real game may download new files between/during scans. Idempotency here
    # means every existing path keeps its ID and no path is duplicated. The unit
    # fixture separately proves zero additions when the filesystem is unchanged.
    candidates=ResultReviews(catalog,run/'scan-monitor').candidates('Pokemon')
    atomic_json(run/'rescan.json',dict(scans=scans,copy_counts=copy_counts,old_ids_preserved=True,pokemon_candidates=candidates))

    analyzer=Analyzer(LocalOCREngine(ROOT/'.ocr-models',low_latency=True))
    labels=json.loads((ROOT/'tests/fixtures/phase4a_ground_truth.json').read_text(encoding='utf8'))['images']
    results=json.loads((ROOT/'tests/fixtures/phase4c_ground_truth.json').read_text(encoding='utf8'))['images']
    rows=[]
    for path in sorted(images.glob('*.PNG')):
        start=time.perf_counter();observation=analyzer.analyze(Image.open(path))
        checks=[dict(field='screen_type',expected=labels[path.name]['screen_type'],actual=observation['screen_type'])]
        for key,expected in labels[path.name].get('fields',{}).items():
            checks.append(dict(field='selection.'+key,expected=expected,actual=observation['selection']['fields'][key]['value']))
        for key,expected in results.get(path.name,{}).get('fields',{}).items():
            checks.append(dict(field='result.'+key,expected=expected,actual=observation['result_ocr']['fields'][key]['value']))
        for c in checks:c['correct']=c['expected']==c['actual']
        rows.append(dict(image=path.name,kind='original_static_image',sha256=hashes[path.name],
                         ocr_ms=round((time.perf_counter()-start)*1000,2),checks=checks,observation=observation))
        print(path.name,sum(c['correct']for c in checks),'/',len(checks),flush=True)
    start=time.perf_counter();astral=analyzer.analyze(Image.open(ASTRAL));elapsed=round((time.perf_counter()-start)*1000,2)
    expected=dict(title='Astral Paradox',game_level=38,player_name='TC',perfect=585,cool=32,bad=2,miss=45,
                  max_combo=238,game_score=35575,grade='F',completion_status='FAILED')
    fields=astral['result_ocr']['fields']
    checks=[dict(field=k,expected=v,actual=fields[k]['value'],correct=v==fields[k]['value'])for k,v in expected.items()]
    preview=score_preview({k:v['value']for k,v in fields.items()},False,False)
    assert preview['ex_score']==1202 and preview['judged_notes']==664 and preview['lamp_preview']=='FAILED'
    assert preview['max_score'] is None and preview['percentage'] is None
    rows.append(dict(image=str(ASTRAL.relative_to(ROOT)),kind='saved_real_session_image_static_reanalysis',
                     sha256=hashlib.sha256(ASTRAL.read_bytes()).hexdigest(),ocr_ms=elapsed,checks=checks,observation=astral,preview=preview,
                     difficulty_note='HARD is supplied by the user, not read from the result screenshot.'))
    atomic_json(run/'image-results.json',rows)

    # A separate mini catalog and simulated session for browser interaction tests.
    fixture=run/'gui-replay';songroot=fixture/'SONG';p=songroot/'Seed Pack'/'Seed'/'song.sm';p.parent.mkdir(parents=True)
    p.write_text('#TITLE:Seed Song;',encoding='utf8')
    testdb=fixture/'library.sqlite3'
    with LibraryDatabase(testdb) as db:db.sync(scan_library(songroot))
    sid=str(uuid4());monitor=fixture/'monitor';folder=monitor/'sessions'/sid;folder.mkdir(parents=True)
    shutil.copyfile(ASTRAL,folder/'result.png')
    session=dict(session_id=sid,status='pending_result',started_at='2026-10-23T00:00:00+00:00',song_id=None,
                 selection=None,matching_status='pending_manual_review',result={'state':'GAME_OVER','screenshot':'result.png'},
                 evidence_source='GUI TEST REPLAY of saved screenshot, NOT live gameplay')
    atomic_json(folder/'session.json',session);ResultReviews(testdb,monitor).ingest(session,astral['result_ocr'])
    # Files are added AFTER the first import, ready for the GUI rescan button.
    for pack in ('connect','New Download Pack'):
        p=songroot/pack/'Astral Test Folder'/'song.sm';p.parent.mkdir(parents=True)
        p.write_text('#TITLE:Astral Paradox;#ARTIST:TEST FIXTURE;',encoding='utf8')
    atomic_json(run/'gui-fixture.json',dict(database=str(testdb),monitor=str(monitor),session_id=sid,kind='isolated_GUI_simulation'))
    with closing(sqlite3.connect(PRODUCTION.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        production_after=audit(db);check_integrity(db)
    assert baseline==production_after
    assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in images.glob('*.PNG')}
    summary=dict(run_directory=str(run),static_images=len(rows),checks=sum(len(r['checks'])for r in rows),
        passed_checks=sum(c['correct']for r in rows for c in r['checks']),
        classifications_correct=sum(r['checks'][0]['correct']for r in rows[:-1]),original_classifications=len(rows)-1,
        production_unchanged=True,original_images_unchanged=True,schema_changed=False,scans=scans,
        counts_before={k:v['count']for k,v in baseline.items()},copy_counts_after=copy_counts,
        astral_preview=preview,gui_fixture=str(run/'gui-fixture.json'),new_live_gameplay_tested=False)
    atomic_json(OUT/'validation.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
