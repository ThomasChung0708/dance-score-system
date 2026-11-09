"""Read-only Phase 2.5 investigation; writes analysis artifacts, never imports/syncs."""
from __future__ import annotations

import collections
import hashlib
import itertools
import json
import os
import re
import sqlite3
import sys
import unicodedata
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from dance_results.models import ParsedFile
from dance_results.parsers.common import normalized_title
from dance_results.parsers.dwi import DWIParser
from dance_results.parsers.sm import SMParser
from dance_results.scanner import is_link


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_state(path):
    stat = path.stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': digest(path)}


def json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def group_summary(groups):
    ids = set().union(*(set(g['song_ids']) for g in groups)) if groups else set()
    return {'groups': len(groups), 'distinct_songs': len(ids)}


def encoding_analysis(source, raw):
    full_candidates, full_errors = [], {}
    for encoding in ('utf-8-sig', 'cp932', 'big5', 'cp950'):
        try:
            raw.decode(encoding, errors='strict')
            full_candidates.append(encoding)
        except UnicodeDecodeError as error:
            prior_tags = list(re.finditer(rb'(?im)^[ \t]*#([A-Z0-9_]+):', raw[:error.start]))
            full_errors[encoding] = {'offset': error.start, 'end': error.end,
                                    'line': raw[:error.start].count(b'\n')+1,
                                    'tag_context': prior_tags[-1].group(1).decode('ascii') if prior_tags else None,
                                    'reason': error.reason, 'bytes_hex': raw[error.start:error.end].hex()}
    # Analysis only: independently decode complete, terminated TITLE/SUBTITLE
    # lines. All errors in the rest of the file remain explicitly reported above.
    # This is not a general replacement parser or an approved import policy.
    matches = list(re.finditer(rb'(?im)^[ \t]*#(TITLE|SUBTITLE):([^\r\n]*)', raw))
    fields = [{'tag': m.group(1).decode('ascii').upper(), 'offset': m.start(2),
               'line': raw[:m.start(1)].count(b'\n')+1, 'raw_hex': m.group(2).hex()}
              for m in matches]
    candidates = {}
    for encoding in ('utf-8-sig', 'cp932', 'big5', 'cp950'):
        try:
            lines = [m.group(0).decode(encoding, errors='strict') for m in matches]
        except UnicodeDecodeError:
            continue
        result = ParsedFile(source['relative_path'], source['format'])
        parser = SMParser() if source['format']=='sm' else DWIParser()
        parser.parse('\n'.join(lines), result)
        if result.title and not result.diagnostics and all(line.rstrip().endswith(';') for line in lines):
            candidates[encoding] = {'title': result.title, 'subtitle': result.subtitle,
                                    'display_title': result.display_title,
                                    'private_use_characters': sum(unicodedata.category(c)=='Co' for c in result.display_title),
                                    'replacement_characters': result.display_title.count('\ufffd')}
    utf8 = candidates.get('utf-8-sig')
    if utf8:
        status = 'utf8_fields_suspected_preexisting_mojibake' if utf8['private_use_characters'] or utf8['replacement_characters'] else 'utf8_title_fields_readable'
    elif set(candidates)=={'cp932'}:
        status = 'cp932_title_candidate'
    elif set(candidates)=={'big5','cp950'} and candidates['big5']==candidates['cp950']:
        status = 'big5_cp950_title_candidate'
    else:
        status = 'ambiguous_title_candidates'
    return {'source_id': source['source_id'], 'song_id': source['song_id'], 'relative_path': source['relative_path'],
            'sha256': hashlib.sha256(raw).hexdigest(), 'whole_file_strict_candidates': full_candidates,
            'whole_file_errors': full_errors, 'title_fields_raw': fields,
            'title_field_candidates': candidates, 'analysis_status': status, 'applied_to_database': False}


def investigate():
    path = PROJECT / 'data/library.sqlite3'
    before = file_state(path)
    app_before = {str(p.relative_to(PROJECT)): digest(p) for p in (PROJECT/'dance_results').rglob('*.py')}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = PROJECT / 'reports' / ('phase25-investigation-'+stamp)
    output.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        roots = {r['root_id']:dict(r) for r in db.execute('SELECT * FROM library_roots')}
        packs = [dict(r) for r in db.execute('SELECT * FROM packs ORDER BY pack_id')]
        songs = {r['song_id']:dict(r) for r in db.execute('SELECT * FROM songs ORDER BY song_id')}
        sources = [dict(r) for r in db.execute('SELECT * FROM song_sources ORDER BY source_id')]
        counts = {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0]
                  for t in ('packs','songs','song_sources','scan_runs','game_difficulties','play_records','source_raw_snapshots')}
        by_song = collections.defaultdict(list)
        for f in sources:
            f['payload'] = json.loads(f['payload_json'])
            by_song[f['song_id']].append(f)
        def pack_key(sid):
            s = songs[sid]
            return (s['root_id'],PureWindowsPath(s['relative_folder']).parts[0])
        def reference(sid):
            s = songs[sid]
            return {'song_id':sid,'pack_id':s['pack_id'],'logical_pack_path':pack_key(sid)[1],
                    'relative_folder':s['relative_folder']}
        def source_detail(f):
            return {'source_id':f['source_id'],'relative_path':f['relative_path'],'format':f['format'],
                    'title':f['payload'].get('title'),'subtitle':f['payload'].get('subtitle'),
                    'title_fields':f['payload'].get('title_fields',{})}
        conflicts, conflict_types = [], collections.Counter()
        title_groups = collections.defaultdict(set)
        same_title_source_folders = []
        for sid,s in songs.items():
            files = by_song[sid]
            titles = {f['payload'].get('title') for f in files if f['payload'].get('title')}
            for title in titles:
                title_groups[title].add(sid)
            repeated = collections.Counter(f['payload'].get('title') for f in files if f['payload'].get('title'))
            if any(n>1 for n in repeated.values()):
                same_title_source_folders.append(reference(sid))
            payload = json.loads(s['payload_json'])
            if not any(d['code']=='SONG_TITLE_CONFLICT' for d in payload['diagnostics']):
                continue
            display = {' '.join(x for x in (f['payload'].get('title'),f['payload'].get('subtitle')) if x)
                       for f in files if f['payload'].get('title')}
            assert len({normalized_title(t) for t in display})>1
            if len(titles)==1:
                kind = 'same_exact_title_different_subtitle'
            elif len(files)==2:
                a,b = [normalized_title(f['payload'].get('title') or '') for f in files]
                kind = 'different_titles_prefix_relation' if a.startswith(b+' ') or b.startswith(a+' ') or a.startswith(b+'(') or b.startswith(a+'(') else 'different_titles_other'
            else:
                kind = 'multiple_sources_different_titles'
            conflict_types[kind]+=1
            conflicts.append({**reference(sid),'classification':kind,'display_candidates':sorted(display),
                              'sources':[source_detail(f) for f in files]})
        same_names = [{'title':title,'song_ids':sorted(ids),'songs':[reference(sid) for sid in sorted(ids)]}
                      for title,ids in sorted(title_groups.items()) if len(ids)>1]
        cross_pack = [g for g in same_names if len({pack_key(sid) for sid in g['song_ids']})>1]
        within_groups = collections.defaultdict(set)
        for title,ids in title_groups.items():
            for sid in ids:
                within_groups[(*pack_key(sid),title)].add(sid)
        within_pack = [{'root_id':key[0],'logical_pack_path':key[1],'title':key[2],
                        'song_ids':sorted(ids),'songs':[reference(sid) for sid in sorted(ids)]}
                       for key,ids in sorted(within_groups.items()) if len(ids)>1]
        pairs = {pair for g in same_names for pair in itertools.combinations(g['song_ids'],2)}
        cross_pairs = {p for p in pairs if pack_key(p[0])!=pack_key(p[1])}
        same_name_summary = {'all':{**group_summary(same_names),'distinct_song_pairs':len(pairs)},
                             'cross_pack':{**group_summary(cross_pack),'distinct_song_pairs':len(cross_pairs)},
                             'within_pack':{**group_summary(within_pack),'distinct_song_pairs':len(pairs-cross_pairs)},
                             'same_title_multiple_sources_within_one_song_folder':len(same_title_source_folders),
                             'title_basis':'Exact, nonempty per-source selected TITLE/TitleUnicode. Deduplicate within Song ID. Includes candidates in pending folders, excludes 43 undecoded sources. Not a game-title or chart-identity assertion.'}

        # Inventory real song directories; never recurse into backup subfolders or
        # follow links. Hash sources without parsing notes/difficulties.
        source_map = {}
        for f in sources:
            source_map[os.path.normcase(str(Path(f['payload']['path']).resolve()))]=f
        disk_source_paths, folder_inventory, excluded_links = set(), [], []
        raw_for_analysis, hash_mismatches, read_errors = {}, [], []
        suffix_counts = collections.Counter()
        for root in roots.values():
            root_path = Path(root['path'])
            for directory,dirs,names in os.walk(root_path,followlinks=False):
                base = Path(directory)
                depth = len(base.relative_to(root_path).parts)
                kept=[]
                for name in dirs:
                    child=base/name
                    if is_link(child): excluded_links.append(str(child))
                    elif depth<2 and name not in {'.git','__pycache__'}: kept.append(name)
                dirs[:]=kept
                inventory=collections.defaultdict(list)
                for name in sorted(names):
                    p=base/name
                    if is_link(p): excluded_links.append(str(p)); continue
                    suffix=p.suffix.lower() or '(no extension)'
                    suffix_counts[suffix]+=1
                    inventory[suffix].append(name)
                    if suffix not in {'.sm','.dwi','.osu'}: continue
                    key=os.path.normcase(str(p.resolve()))
                    disk_source_paths.add(key)
                    f=source_map.get(key)
                    if f is None: continue
                    try:
                        stat_before=p.stat(); raw=p.read_bytes(); stat_after=p.stat()
                        if (stat_before.st_size,stat_before.st_mtime_ns)!=(stat_after.st_size,stat_after.st_mtime_ns):
                            raise ValueError('changed while reading')
                        if hashlib.sha256(raw).hexdigest()!=f['latest_sha256']:
                            hash_mismatches.append({'source_id':f['source_id'],'path':str(p)})
                        if any(d['code']=='ENCODING_REQUIRED' for d in f['payload']['diagnostics']) or f['source_id']==1184 or f['song_id']==762:
                            raw_for_analysis[f['source_id']]=raw
                    except (OSError,ValueError) as error:
                        read_errors.append({'source_id':f['source_id'],'path':str(p),'error':str(error)})
                if any(suffix in inventory for suffix in ('.sm','.dwi','.osu')):
                    folder_inventory.append({'root_id':root['root_id'],'relative_folder':str(base.relative_to(root_path)),
                                             'extensions':dict(inventory)})
        missing_paths=sorted(set(source_map)-disk_source_paths)
        new_paths=sorted(disk_source_paths-set(source_map))
        format_counts=collections.Counter()
        mixed=[]
        for sid,s in songs.items():
            formats=sorted({f['format'] for f in by_song[sid] if f['is_present']})
            format_counts['+'.join(formats)]+=1
            if len(formats)>1:
                mixed.append({**reference(sid),'formats':formats,'sources':[source_detail(f) for f in by_song[sid]]})
        disk_format_counts=collections.Counter('+'.join(sorted(e[1:] for e in item['extensions'] if e in {'.sm','.dwi','.osu'})) for item in folder_inventory)
        osb_folders=[item['relative_folder'] for item in folder_inventory if '.osb' in item['extensions']]
        osu_with_osb=[item['relative_folder'] for item in folder_inventory if '.osb' in item['extensions'] and '.osu' in item['extensions']]
        assert not any(f['format'] not in ('sm','dwi','osu') for f in sources)

        encodings=[]
        for f in sources:
            if not any(d['code']=='ENCODING_REQUIRED' for d in f['payload']['diagnostics']): continue
            raw=raw_for_analysis[f['source_id']]
            archived=db.execute('SELECT raw_data FROM source_raw_snapshots WHERE source_id=? AND sha256=?', (f['source_id'],f['latest_sha256'])).fetchone()
            assert archived and archived[0]==raw
            record=encoding_analysis(f,raw)
            record['archived_bytes_equal_current_file']=True
            record['same_folder_other_sources']=[source_detail(other) for other in by_song[f['song_id']] if other['source_id']!=f['source_id']]
            encodings.append(record)
        encoding_status=collections.Counter(e['analysis_status'] for e in encodings)
        encoding_whole=collections.Counter('+'.join(e['whole_file_strict_candidates']) or 'none' for e in encodings)
        first_utf8_tags=collections.Counter(e['whole_file_errors']['utf-8-sig']['tag_context'] for e in encodings)

        checks={
            'duplicate_song_id_groups': 'SELECT song_id FROM songs GROUP BY song_id HAVING count(*)>1',
            'duplicate_song_folder_key_groups':'SELECT root_id,folder_key FROM songs GROUP BY root_id,folder_key HAVING count(*)>1',
            'duplicate_pack_path_key_groups':'SELECT root_id,path_key FROM packs GROUP BY root_id,path_key HAVING count(*)>1',
            'duplicate_source_identity_groups':'SELECT song_id,path_key FROM song_sources GROUP BY song_id,path_key HAVING count(*)>1',
            'orphan_sources':'SELECT f.source_id FROM song_sources f LEFT JOIN songs s ON s.song_id=f.song_id WHERE s.song_id IS NULL',
            'pack_root_mismatch':'SELECT s.song_id FROM songs s JOIN packs p ON p.pack_id=s.pack_id WHERE s.root_id!=p.root_id',
        }
        integrity={name:[tuple(row) for row in db.execute(sql)] for name,sql in checks.items()}
        integrity['quick_check']=[r[0] for r in db.execute('PRAGMA quick_check')]
        integrity['foreign_key_check']=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')]
        integrity['source_parent_mismatch']=[f['source_id'] for f in sources if PureWindowsPath(f['relative_path']).parent!=PureWindowsPath(songs[f['song_id']]['relative_folder'])]
        connect=[sid for sid in songs if pack_key(sid)[1].casefold()=='connect']
        connect_data={'song_count':len(connect),'source_count':sum(len(by_song[sid]) for sid in connect),
                      'pack_rows':[p for p in packs if p['relative_path'].casefold()=='connect'],
                      'song_pack_ids':sorted({songs[sid]['pack_id'] for sid in connect},key=str),
                      'songs':[reference(sid) for sid in connect],
                      'same_title_with_other_packs':[g for g in cross_pack if any(sid in connect for sid in g['song_ids'])],
                      'same_title_within_connect':[g for g in within_pack if g['logical_pack_path'].casefold()=='connect']}
        fragment=next(f for f in sources if f['source_id']==1184)
        fragment_text=raw_for_analysis[1184].decode('utf-8-sig',errors='strict')
        fragment_info={'source_id':1184,'relative_path':fragment['relative_path'],'size':len(raw_for_analysis[1184]),
                       'first_line':fragment_text.splitlines()[0],
                       'has_standard_header':bool(re.match(r'osu file format v\d+',fragment_text)),
                       'has_metadata_section':'[Metadata]' in fragment_text,
                       'has_timing_section':'[TimingPoints]' in fragment_text,'has_objects_section':'[HitObjects]' in fragment_text,
                       'conclusion':'osu extension and timing/object fragment coexist with SM/DWI; game loader choice and full osu source status unconfirmed.'}
        multi_audio=[]
        for f in by_song[762]:
            text=raw_for_analysis[f['source_id']].decode('utf-8-sig',errors='strict')
            match=re.search(r'(?m)^AudioFilename\s*:\s*(.*)$',text)
            audio=match.group(1).strip() if match else None
            multi_audio.append({**source_detail(f),'audio_filename':audio,
                                'audio_exists':bool(audio and (Path(f['payload']['path']).parent/audio).is_file())})
        schema_version=db.execute('PRAGMA user_version').fetchone()[0]
        db.rollback()
    after=file_state(path)
    app_after={str(p.relative_to(PROJECT)):digest(p) for p in (PROJECT/'dance_results').rglob('*.py')}
    assert before==after, 'Database file changed during read-only investigation'
    assert app_before==app_after, 'Application code changed during investigation'
    result={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'schema_version':schema_version,'counts':counts,
            'database_before':before,'database_after':after,'database_byte_identical':True,'application_code_unchanged':True,
            'same_name_summary':same_name_summary,'same_name_groups':same_names,'cross_pack_groups':cross_pack,'within_pack_groups':within_pack,
            'same_title_multiple_sources_within_one_folder':same_title_source_folders,
            'old_101_conflict_classification':dict(conflict_types),'old_101_conflicts':conflicts,
            'source_format_folder_counts':dict(format_counts),'disk_source_format_folder_counts':dict(disk_format_counts),
            'mixed_format_folders':mixed,'all_disk_extensions':dict(sorted(suffix_counts.items())),
            'osb_folders':osb_folders,'osu_with_osb_folders':osu_with_osb,'osu_fragment':fragment_info,
            'multi_title_folder_762':multi_audio,'encoding_status':dict(encoding_status),'whole_file_encoding_candidates':dict(encoding_whole),
            'first_utf8_error_tag_counts':dict(first_utf8_tags),'encoding_sources':encodings,'connect':connect_data,
            'integrity':integrity,'filesystem_verification':{'db_source_count':len(sources),'disk_source_count':len(disk_source_paths),
                 'cached_hash_matches':len(sources)-len(hash_mismatches)-len(read_errors)-len(missing_paths),
                 'hash_mismatches':hash_mismatches,'read_errors':read_errors,'missing_paths':missing_paths,
                 'new_unimported_paths':new_paths,'excluded_links':excluded_links}}
    assert len(conflicts)==sum(conflict_types.values())
    assert len(encodings)==sum(encoding_status.values())
    assert len(cross_pairs)+len(pairs-cross_pairs)==len(pairs)
    json_write(output/'details.json',result)
    summary={key:result[key] for key in ('counts','same_name_summary','old_101_conflict_classification','source_format_folder_counts',
        'disk_source_format_folder_counts','all_disk_extensions','encoding_status','whole_file_encoding_candidates',
        'first_utf8_error_tag_counts','integrity','filesystem_verification','database_byte_identical','application_code_unchanged')}
    summary['connect']={k:connect_data[k] for k in ('song_count','source_count','pack_rows','song_pack_ids')}
    summary['connect_cross_pack_title_groups']=len(connect_data['same_title_with_other_packs'])
    summary['connect_same_pack_title_groups']=len(connect_data['same_title_within_connect'])
    summary['osb_folder_count']=len(osb_folders)
    summary['osu_with_osb_folder_count']=len(osu_with_osb)
    json_write(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('OUTPUT',output)


if __name__=='__main__':
    investigate()
