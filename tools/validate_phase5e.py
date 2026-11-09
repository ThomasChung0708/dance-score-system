"""Read-only safety/GUI evidence audit and Phase5E report generation."""
from contextlib import closing
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
from dance_results.achievements import progress_for_songs
from dance_results.recent_updates import recent_updates
from dance_results.sqlite_safety import audit,projection,row_limits
from dance_results.submissions import SubmissionService

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5e'


def read(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
def load(name):return json.loads((OUT/name).read_text(encoding='utf8'))


def run():
    before=load('before.json');manifest=load('gui/manifest.json')
    with closing(read(ROOT/'data/library.sqlite3')) as db,closing(read(OUT/'baseline.sqlite3')) as baseline:
        db.execute('BEGIN')
        db.row_factory=sqlite3.Row;after=audit(db)
        activity={}
        for table,columns in projection(baseline).items():
            changes={};old_rows=baseline.execute('SELECT * FROM '+table).fetchall()
            for row in old_rows:
                current=db.execute('SELECT * FROM '+table+' WHERE '+columns[0]+'=?',(row[0],)).fetchone()
                assert current is not None,('Existing ID missing',table,row[0])
                for name,old,new in zip(columns,row,current):
                    if old!=new:changes[name]=changes.get(name,0)+1
            # Live user rescans may update their scan marker, never identities.
            allowed={'last_seen_scan_id'} if table in ('songs','song_sources') else set()
            assert not set(changes)-allowed,(table,changes)
            activity[table]=dict(existing_rows=len(old_rows),original_ids_preserved=True,
                                 modified_columns=changes,added_rows=after[table]['count']-len(old_rows))
        assert db.execute('PRAGMA user_version').fetchone()[0]==before['schema']==10
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert not db.execute('SELECT 1 FROM library_roots WHERE path=?',(str(OUT/'gui/SONG'),)).fetchone()
        for case in manifest['cases'].values():
            assert not db.execute('SELECT 1 FROM play_records WHERE session_id=?',(case['session_id'],)).fetchone()
        p=next(p for p in progress_for_songs(db,[127])[127] if p['difficulty']=='HARD')
        assert (p['best_ex_score'],p['best_score_record']['record_id'],p['best_lamp'],p['best_lamp_record_id'])==(2029,18,'AC',19)
        real=[SubmissionService._record(db,n,include_evidence=False) for n in (18,19)]
        added_records=[dict(r) for r in db.execute('SELECT record_id,session_id,confirmation_method,screenshot_path FROM play_records WHERE record_id>? ORDER BY record_id',(max(r[0] for r in baseline.execute('SELECT record_id FROM play_records')),))]
    files={str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data/monitor').rglob('*') if p.is_file()}
    removed=set(before['monitor_files'])-set(files);assert not removed
    changed_files=[name for name,value in before['monitor_files'].items() if files.get(name)!=value]
    assert not any(name.lower().endswith('.png') for name in changed_files)
    with closing(read(OUT/'baseline.sqlite3')) as baseline,closing(read(Path(manifest['database']))) as db:
        db.row_factory=sqlite3.Row
        assert audit(db,projection(baseline),row_limits(baseline))==before['tables']
        cases={name:SubmissionService._record(db,db.execute('SELECT record_id FROM play_records WHERE session_id=?',(case['session_id'],)).fetchone()[0],include_evidence=False) for name,case in manifest['cases'].items()}
        expected=dict(first='DOUBLE_UPDATE',score='SCORE_UPDATE',lamp='LAMP_UPDATE',failed='SCORE_UPDATE',saved='NO_UPDATE',double='DOUBLE_UPDATE',reduced='DOUBLE_UPDATE')
        assert {k:r['update_summary']['kind'] for k,r in cases.items()}==expected
        assert cases['failed']['max_score'] is None and cases['failed']['percentage'] is None
        updates=[r for r in recent_updates(db)['updates'] if r['song_id']==manifest['song_id']]
        assert {(r['difficulty'],r['record_id']) for r in updates}=={('HARD',cases['double']['record_id']),('NORMAL',cases['reduced']['record_id'])}
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
    animations={k:load('gui/'+k+'-animation.json') for k in ('score','lamp','failed','saved','double','reduced')}
    traces={k:[e['name'] for e in json.loads(v['animation']['animationTrace'])] for k,v in animations.items()}
    assert 'score-flash' in traces['score'] and 'lamp-upgrade' not in traces['score']
    assert 'lamp-upgrade' in traces['lamp'] and 'score-flash' not in traces['lamp']
    assert 'score-flash' in traces['failed'] and 'lamp-upgrade' not in traces['failed']
    assert all(n not in traces['saved'] for n in ('score-flash','gain-enter','lamp-upgrade','lamp-ring'))
    assert 'score-flash' in traces['double'] and 'lamp-upgrade' in traces['double']
    assert set(traces['reduced'])=={'fx-fade'}
    mobile=load('gui/mobile-proof.json');mobile_song=load('gui/mobile-song-proof.json')
    assert mobile['clientWidth']==mobile['scrollWidth'] and mobile_song['clientWidth']==mobile_song['scrollWidth']
    image=load('gui/image-link-proof.json');assert image['imageVisible'] and image['imageLoaded'] and image['imageWidth']==1024 and image['detailTop']==0
    reload=load('gui/reload-proof.json');assert reload['notifications'] is None and [r['kind'] for r in reload['kinds']]==['NO_UPDATE','SCORE_UPDATE','LAMP_UPDATE','SCORE_UPDATE','DOUBLE_UPDATE']
    retry=load('gui/submit-reload-proof.json');assert retry['notifications'] is None and retry['disabled']
    restarted=load('gui/service-restart-proof.json');assert restarted['notifications'] is None
    assert [r['kind'] for r in restarted['records']]==['DOUBLE_UPDATE','NO_UPDATE','SCORE_UPDATE','LAMP_UPDATE','SCORE_UPDATE','DOUBLE_UPDATE']
    order=load('gui/growth-order-proof.json');assert order['records']==[str(cases[k]['record_id']) for k in ('first','score','lamp','failed','saved','double')]
    difficulty=load('gui/difficulty-proof.json');assert difficulty['records']==[str(cases['reduced']['record_id'])]
    production_gui=load('gui/production-gui-proof.json');assert 'EX 2029' in production_gui['bests'] and production_gui['notifications'] is None
    production_growth=load('gui/production-growth-proof.json');assert production_growth['records']==['19','18']
    log=(OUT/'unittest-final.log').read_text(encoding='utf8',errors='replace')
    assert log.rstrip().endswith('OK') and 'skipped' not in log
    count=int(re.search(r'Ran (\d+) tests',log).group(1))
    changed=['dance_results/'+x for x in ['__init__.py','performance.py','achievements.py','submissions.py','gui.py','recent_updates.py','gui_assets/history.html','gui_assets/history.js','gui_assets/history.css','gui_assets/lamps.js','gui_assets/lamps.css','gui_assets/app.js','gui_assets/reviews.js','gui_assets/style.css']]
    changed+=['tests/test_phase5e.py','tools/phase5e_gui.py','tools/validate_phase5e.py','docs/PHASE5E_PERFORMANCE_UI.md','docs/VALIDATION.md','README.md']
    result=dict(version='0.15.0',schema=10,migration_required=False,date='2026-10-27',tests=dict(total=count,passed=count,new=20,skipped=0),
                production=dict(before=before['tables'],after=after,all_tables_unchanged=before['tables']==after,
                    activity=activity,original_records_unchanged=before['tables']['play_records']['count'],current_record_count=after['play_records']['count'],
                    user_added_records=added_records,all_original_ids_preserved=True,original_screenshots_unchanged=True,
                    monitor_files_current=len(files),monitor_files_changed=changed_files,monitor_files_added=sorted(set(files)-set(before['monitor_files'])),
                    integrity_check='ok',foreign_key_errors=0,generated_test_data_absent=True,real_case=real,progress=p),
                gui=dict(isolated_cases=cases,animations=animations,mobile=mobile,mobile_song=mobile_song,image_link=image,reload=reload,service_restart=restarted,duplicate_ui=retry,order=order,difficulty=difficulty,recent_updates=updates,production_read_only=production_gui),
                event_source='immutable play_records.evidence_json.achievement_feedback; read-only normalization; no reconstructed updates',
                phase5d=dict(implemented=['NO MAP episode tracking','whole SONG incremental scan with newly observed song filtering','concise download summary','safe candidate/manual confirmation','independent EX/lamp bests','30 distinct score-update chart pairs'],not_validated=['real multiplayer NO MAP download to automatic submission end-to-end','hover versus selected Pack disambiguation with independent game evidence']),
                modified_files=changed,new_real_gameplay=False,system_reduced_motion_setting_changed=False)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    rows=''.join(f'<tr><td>{html.escape(k)}</td><td>{v["count"]}</td><td>{after[k]["count"]}</td><td>{"原ID保留；使用期間更新掃描標記" if activity[k]["modified_columns"] else "原資料保留"}{"；使用期間新增"+str(activity[k]["added_rows"])+"筆" if activity[k]["added_rows"] else ""}</td></tr>' for k,v in before['tables'].items())
    figures=''.join(f'<figure><img src="gui/{name}" alt="{caption}"><figcaption>{caption}</figcaption></figure>' for name,caption in [('production-history.png','正式資料唯讀畫面：#18最高EX2029／#19最佳AC'),('production-progress.png','正式資料唯讀成長履歷'),('double-update.png','隔離生成紀錄：DOUBLE UPDATE +160 EX／AC→ACP'),('failed-score-update.png','隔離生成紀錄：FAILED刷新EX，MAX未知'),('history-mobile.png','390px手機成績單（隔離資料）'),('progress-mobile.png','390px單曲成長履歷（隔離資料）')])
    filelist=''.join('<li><code>'+html.escape(p)+'</code></li>' for p in changed)
    document=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Phase5E 個人成績單與成長履歷</title><style>body{{max-width:1100px;margin:36px auto;padding:0 22px;background:#11121c;color:#e9e3f4;font:16px/1.8 system-ui}}h1,h2{{color:#cfb0ff}}section{{background:#1c1a29;border:1px solid #4b3b5b;padding:22px;border-radius:12px;margin:20px 0}}a{{color:#b2d8ff}}img{{max-width:100%;border:1px solid #514160;border-radius:8px}}figure{{margin:28px 0}}figcaption{{color:#c6b4d7;font-size:13px}}table{{border-collapse:collapse;width:100%;font-size:12px}}td,th{{padding:8px;border-bottom:1px solid #4a3a56;text-align:left}}code{{overflow-wrap:anywhere}}.ok{{color:#87ebbb}}</style>
<h1>個人成績單 × 分數成長 × 燈號成長</h1><p>Phase5E · v0.15.0 · Schema10維持不變 · 2026-10-27</p>
<section><h2>實作與驗證</h2><p class="ok">{count}/{count}項測試通過，包含20項新增測試。開發前{before['tables']['play_records']['count']}筆正式成績與Session原值不變，所有原ID及原始截圖保留。</p><p>使用期間另有{len(added_records)}筆人工提交及{activity['scan_runs']['added_rows']}次重掃；目前正式庫{after['play_records']['count']}筆。這些使用者正常資料與生成測試資料分開核對；生成Session沒有進入正式庫。</p><p>沿用Python／SQLite／HTML／CSS／JavaScript，沒有大型框架或官方素材。原始計分、OCR、監控與提交交易不變。</p><p>NEW SCORE、NEW LAMP、DOUBLE UPDATE、NO UPDATE、FIRST BEST、FIRST LAMP皆由當時的不可變提交證據呈現，不以今天最佳倒推歷史。</p></section>
<section><h2>真實案例與獨立最佳</h2><p>Macuilxochitl／Song127／HARD：#18 EX2029、MAX-7、CLEAR；#19 EX1999、MAX-37、AC。正確最高分仍為#18，最佳AC來自#19，#19只有NEW LAMP。唯讀GUI與資料庫查詢一致。</p><p>成績單分開呈現當次Clear Status、Score Status、判定與更新摘要。長路徑收進來源資訊；舊MAX未驗證時只保留在展開原始資訊，不冒充可靠百分比。</p></section>
<section><h2>GUI與動畫驗證</h2><p>隔離副本依序驗證：首次1800/CLEAR → 1900/CLEAR（僅分數）→ 1800/AC（僅燈號）→ FAILED1940（僅分數、無MAX）→ 1900/CLEAR（無更新）→ 2100/ACP（雙更新）。NORMAL獨立建立首次紀錄。</p><p>實際瀏覽器動畫追蹤確認分數使用score-flash／gain-enter、燈號使用lamp-upgrade／ring／shine，雙更新同時具有兩類效果；NO UPDATE沒有刷新動畫。減少動畫的追蹤只有fx-fade，沒有更改Windows系統偏好。</p><p>時間線排序、難度隔離、原圖連結、390px成績單／單曲頁無水平溢出、重整標記保留且不重播均已驗證。自動化亦涵蓋同Session重送、同名不同Song ID、不可比較版本與基準衝突。</p>{figures}</section>
<section><h2>持久化、Recent Update與Schema</h2><p>沿用evidence_json中的achievement_feedback、score_update、lamp_update及舊版可信前後值。API提供唯讀update_summary與score_status；不新增事件、不回填舊資料。缺少證據顯示「更新證據未保存」。</p><p>Recent仍只取最近30個分數刷新Song ID＋難度，以played_at排序。只升級燈號不進清單；雙更新可附燈號標記但只列一次。</p><p>Schema10未變，沒有migration，開發驗證只查詢正式庫。逐筆核對原資料與ID；正常重掃只更新歌曲／來源的last_seen_scan_id，其餘原值一致。使用期間新增與修改的監控檔案另列於JSON，不冒稱所有檔案雜湊完全相同。所有原始PNG雜湊一致，完整性ok，外鍵0錯誤。</p><table><tr><th>表</th><th>開發前</th><th>核對時</th><th>原值核對</th></tr>{rows}</table></section>
<section><h2>操作與限制</h2><p>執行LibraryGUI.cmd或MonitorGUI.cmd，進「歷史成績」查看新成績單；選取單曲後查看兩個最佳區塊及成長履歷。可切換難度、時間順序與分頁，點Record開原始結算截圖。已開舊頁請重新載入。</p><p>Phase5D已實作NO MAP追蹤、全SONG背景增量掃描、新增候選篩選與摘要；真實多人下載到自動提交完整鏈及Pack選取／Hover獨立判定仍需後續驗收。這次沒有新增真實遊玩或即時OCR測試。</p><p><a href="../../docs/PHASE5E_PERFORMANCE_UI.md">完整操作與修改說明</a> · <a href="unittest-final.log">測試日誌</a> · <a href="validation.json">完整驗證JSON</a></p></section><section><h2>修改檔案</h2><ul>{filelist}</ul></section></html>'''
    (OUT/'report.html').write_text(document,encoding='utf8')
    print(json.dumps(dict(tests=count,schema=10,original_records_unchanged=before['tables']['play_records']['count'],current_records=after['play_records']['count'],current_monitor_files=len(files),report=str(OUT/'report.html')),ensure_ascii=False))


if __name__=='__main__':run()
