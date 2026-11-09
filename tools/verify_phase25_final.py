"""Authorized final migration + two rescans, with baseline/backup/identity checks."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from dance_results.database import LibraryDatabase
from dance_results.report import write_report
from dance_results.scanner import scan_library


def snapshot(db):
    return {
        'packs':[list(r) for r in db.execute('SELECT pack_id,root_id,relative_path,path_key,name FROM packs ORDER BY pack_id')],
        'songs':[list(r) for r in db.execute('SELECT song_id,root_id,folder_key,relative_folder,saved_title FROM songs ORDER BY song_id')],
        'sources':[dict(zip(('source_id','song_id','relative_path','path_key','saved_title','saved_subtitle','sha256','payload'),r)) for r in db.execute('SELECT source_id,song_id,relative_path,path_key,saved_title,saved_subtitle,latest_sha256,payload_json FROM song_sources ORDER BY source_id')],
        'scans':[list(r) for r in db.execute('SELECT * FROM scan_runs ORDER BY scan_id')],
        'raw_snapshots':[list(r) for r in db.execute('SELECT source_id,sha256,hex(raw_data) FROM source_raw_snapshots ORDER BY snapshot_id')],
    }


def main():
    path=PROJECT/'data/library.sqlite3'
    verify_existing='--verify-existing' in sys.argv
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        if version!=2 and not (version==3 and verify_existing):
            raise ValueError('Requires schema 2 for migration; --verify-existing only audits an already completed run.')
        root=Path(db.execute('SELECT path FROM library_roots').fetchone()[0])
        if verify_existing:
            migration=json.loads((PROJECT/'data/phase25-final-migration-result.json').read_text(encoding='utf-8'))
            with closing(sqlite3.connect(Path(migration['backup_path']).as_uri()+'?mode=ro',uri=True)) as backup:
                before=snapshot(backup)
        else:
            before=snapshot(db)
    with LibraryDatabase(path,[root]) as library:
        migration=migration if verify_existing else library.migration_result
        assert migration and migration['before']==migration['after']
        (PROJECT/'data/phase25-final-migration-result.json').write_text(json.dumps(migration,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Existing migration verification; backup:' if verify_existing else 'Migration 2 -> 3 complete; verified backup:',migration['backup_path'],flush=True)
        reports=[]
        for iteration in (() if verify_existing else (1,2)):
            print('Full rescan',iteration,'started',flush=True)
            report=scan_library(root,progress=lambda n,p:print('Read',n,'sources',flush=True) if n%1000==0 else None)
            library.sync(report)
            destination=write_report(report,PROJECT/'reports')
            reports.append({'path':str(destination),'database':report.database,'summary':report.to_dict()['summary']})
            print('Full rescan',iteration,'complete:',json.dumps(report.database['changes']),flush=True)
            assert report.database['complete_scan']
            assert report.database['changes']['new_songs']==0
            assert report.database['changes']['new_sources']==0
        db=library.connection
        if verify_existing:
            new_ids=[r[0] for r in db.execute('SELECT scan_id FROM scan_runs WHERE scan_id>? ORDER BY scan_id',(before['scans'][-1][0],))]
            assert len(new_ids)==2,'Verification must cover exactly two rescans'
            found={}
            for candidate in sorted((PROJECT/'reports').glob('20*/report.json'),reverse=True):
                value=json.loads(candidate.read_text(encoding='utf-8'))
                scan_id=(value.get('database') or {}).get('scan_id')
                if scan_id in new_ids and value.get('view_mode')=='scan':
                    found[scan_id]={'path':str(candidate.with_name('report.html')),'database':value['database'],'summary':value['summary']}
                if len(found)==2:break
            reports=[found[i] for i in new_ids]
        after=snapshot(db)
        old_pack_map={r[0]:r for r in before['packs']}
        assert all(r in after['packs'] for r in old_pack_map.values())
        assert [r[:4] for r in before['songs']]==[r[:4] for r in after['songs']]
        assert [(r['source_id'],r['song_id'],r['relative_path'],r['path_key']) for r in before['sources']]==[(r['source_id'],r['song_id'],r['relative_path'],r['path_key']) for r in after['sources']]
        assert after['scans'][:len(before['scans'])]==before['scans']
        assert after['raw_snapshots'][:len(before['raw_snapshots'])]==before['raw_snapshots']
        assert len(after['packs'])==40 and len(after['songs'])==1859 and len(after['sources'])==2930
        changed_metadata=[]
        for old,new in zip(before['sources'],after['sources']):
            a,b=json.loads(old['payload']),json.loads(new['payload'])
            for key in ('title','subtitle','path'):
                if a.get(key) is not None and a.get(key)!=b.get(key):
                    changed_metadata.append({'source_id':old['source_id'],'field':key,'old':a.get(key),'new':b.get(key)})
            for key,value in a.get('title_fields',{}).items():
                if value!=b.get('title_fields',{}).get(key):
                    changed_metadata.append({'source_id':old['source_id'],'field':key,'old':value,'new':b.get('title_fields',{}).get(key)})
            for key in ('saved_title','saved_subtitle'):
                if old[key] is not None:assert old[key]==new[key],(old['source_id'],key)
            assert old['sha256']==new['sha256']
        assert not changed_metadata,changed_metadata
        for old,new in zip(before['songs'],after['songs']):
            if old[4] is not None:assert old[4]==new[4]
        assert reports[1]['database']['changes']['unchanged_songs']==1859
        assert reports[1]['database']['changes']['updated_sources']==0
        connect=db.execute("SELECT p.pack_id,p.name,p.pack_type,count(DISTINCT s.song_id),count(f.source_id) FROM packs p JOIN songs s ON s.pack_id=p.pack_id JOIN song_sources f ON f.song_id=s.song_id WHERE p.pack_type='multiplayer_download' GROUP BY p.pack_id").fetchall()
        assert len(connect)==1 and tuple(connect[0])[3:]==(77,181)
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute('SELECT count(*) FROM game_difficulties').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM play_records').fetchone()[0]==0
        old_encoding=[]
        for old,new in zip(before['sources'],after['sources']):
            a,b=json.loads(old['payload']),json.loads(new['payload'])
            if any(d['code']=='ENCODING_REQUIRED' for d in a['diagnostics']):
                old_encoding.append({'source_id':old['source_id'],'relative_path':old['relative_path'],
                                     'title':b['title'],'name_review_required':b['name_review_required'],
                                     'metadata_encodings':b['metadata_encodings']})
        assert len(old_encoding)==43
        assert sum(not r['name_review_required'] and r['title'] is not None for r in old_encoding)==39
        result={'schema_before':2,'schema_after':3,'backup_path':migration['backup_path'],
                'counts_before':{'packs':len(before['packs']),'songs':len(before['songs']),'sources':len(before['sources']),'scans':len(before['scans'])},
                'counts_after':{'packs':len(after['packs']),'songs':len(after['songs']),'sources':len(after['sources']),'scans':len(after['scans'])},
                'original_39_pack_rows_preserved':True,'all_1859_song_ids_and_paths_preserved':True,
                'all_2930_source_ids_and_paths_preserved':True,'all_previously_parsed_titles_subtitles_fields_preserved':True,
                'all_previously_saved_names_preserved':True,'original_snapshots_and_raw_bytes_preserved':True,
                'source_hashes_unchanged':True,'connect_query':[list(r) for r in connect],'rescans':reports,
                'old_43_encoding_sources':old_encoding,'quick_check':'ok','foreign_key_check':[],
                'artist_parsed_sources':db.execute("SELECT count(*) FROM song_sources WHERE json_extract(payload_json,'$.artist') IS NOT NULL").fetchone()[0],
                'saved_artists':db.execute('SELECT count(saved_artist) FROM song_sources').fetchone()[0],
                'game_difficulties':0,'play_records':0}
        (PROJECT/'data/phase25-final-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('rescans','old_43_encoding_sources')},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
