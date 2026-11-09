"""Build a readable report from completed audits and isolated test evidence."""
from contextlib import closing
import html
import json
from pathlib import Path
import sqlite3
from dance_results.achievements import progress_for_songs
from dance_results.recent_updates import recent_updates

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5d2'


def run():
    log=(OUT/'unittest-final.log').read_text(encoding='utf8',errors='replace')
    assert 'Ran 379 tests' in log and log.rstrip().endswith('OK')
    migration=json.loads((OUT/'production-migration.json').read_text(encoding='utf8'))
    manifest=json.loads((OUT/'gui/manifest.json').read_text(encoding='utf8'))
    with closing(sqlite3.connect(Path(manifest['database']).as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        p=next(p for p in progress_for_songs(db,[manifest['song_id']])[manifest['song_id']] if p['difficulty']=='HARD')
        rows=[dict(r) for r in db.execute('SELECT * FROM play_records WHERE record_id IN (18,19) ORDER BY record_id')]
        score,lamp=rows;sf=json.loads(score['evidence_json'])['achievement_feedback'];lf=json.loads(lamp['evidence_json'])['achievement_feedback']
        assert sf['events']==['NEW_SCORE'] and sf['score_update']['improvement']==20 and sf['lamp_update'] is None
        assert lf['events']==['FIRST_AC','NEW_LAMP'] and lf['score_update'] is None
        assert score['max_score'] is None and score['percentage'] is None and score['max_difference'] is None
        assert (p['best_ex_score'],p['best_score_record']['record_id'],p['best_lamp'],p['best_lamp_record_id'])==(1920,18,'AC',19)
        recent=next(r for r in recent_updates(db)['updates'] if r['song_id']==manifest['song_id'])
        assert recent['record_id']==18 and recent['lamp_status']=='FAILED'
        with closing(sqlite3.connect((OUT/'baseline.sqlite3').as_uri()+'?mode=ro',uri=True)) as baseline:
            original=baseline.execute('SELECT * FROM play_records ORDER BY record_id').fetchall()
            assert original==[tuple(r) for r in db.execute('SELECT * FROM play_records WHERE record_id<=15 ORDER BY record_id')]
    restart=json.loads((OUT/'gui/restart-proof.json').read_text(encoding='utf8'))
    assert 'EX 1920' in restart['bests'] and 'AC · #19' in restart['bests'] and restart['notifications'] is None
    animation=json.loads((OUT/'gui/lamp-submit-proof.json').read_text(encoding='utf8'))
    assert json.loads(animation['notification']['lastEvent'])['events']==['FIRST_AC','NEW_LAMP']
    production_gui=json.loads((OUT/'gui/production-gui-proof.json').read_text(encoding='utf8'))
    assert 'BEST EX 3174' in production_gui['list'] and 'My Best Score：EX 3174 · #7' in production_gui['details']
    runtime=json.loads((OUT/'runtime-checks.json').read_text(encoding='utf8'))
    gui=dict(isolated_only=True,new_real_gameplay=False,score_only_feedback=sf,lamp_only_feedback=lf,
             independent_progress=p,recent_score_record_id=18,restart_no_notification_replay=True,
             original_snapshot_records_unchanged=15,animation=animation,
             screenshots=['zhou-independent.png','isolated-failed-best.png','isolated-score-submit.png','isolated-lamp-submit.png','isolated-independent-final.png','isolated-recent-failed.png'])
    (OUT/'gui-checks.json').write_text(json.dumps(gui,ensure_ascii=False,indent=2),encoding='utf8')
    changed=['dance_results/'+x for x in ['__init__.py','database.py','migrations.py','migrations_v10.py','bests_policy.py','achievements.py','score_comparison.py','result_review.py','submissions.py','recent_updates.py','gui_assets/lamps.js','gui_assets/history.html','gui_assets/history.js','gui_assets/history.css','gui_assets/style.css']]
    changed+=['tests/test_phase5d2.py','tests/test_achievements.py','tests/test_phase25.py','tests/test_phase5d.py','tests/test_phase5c2.py','tests/test_submissions.py']
    changed+=['tests/'+x for x in ['test_song_titles.py','test_result_review.py','test_phase5d1.py','test_phase5c1.py','test_phase5c.py','test_official_import.py','test_ocr_static.py','test_note_assistance.py','test_multiplayer_review.py','test_monitor.py','test_gui.py','test_database.py','test_connect_final.py']]
    changed+=['tools/phase5d2_gui.py','tools/phase5d2_validate.py','tools/phase5d2_report.py','README.md','docs/VALIDATION.md','docs/PHASE5D2_INDEPENDENT_BESTS.md']
    result=dict(version='0.14.0',schema=10,date='2026-10-27',tests=dict(total=379,passed=379,skipped=0,new_tests=18,log='unittest-final.log'),
                gui=gui,production_gui=production_gui,runtime=runtime,production=migration,changed_files=changed,
                limitations=['No new real GAME OVER/multiplayer end-to-end test.','Unclassified or unknown versions do not bridge automatically.','MAX definitions are not ranked together; unresolved cross-session conflicts pause refreshes.'])
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    stats=''.join(f'<tr><td>{html.escape(k)}</td><td>{v["count"]}</td><td>{migration["after"][k]["count"]}</td><td>原值保留</td></tr>' for k,v in migration['before'].items() if k!='schema_migrations')
    images=''.join(f'<figure><img src="gui/{name}" alt="隔離 GUI 驗證"><figcaption>{html.escape(name)}（測試副本）</figcaption></figure>' for name in ['zhou-independent.png','isolated-independent-final.png','isolated-lamp-submit.png','isolated-recent-failed.png'])
    files=''.join('<li><code>'+html.escape(p)+'</code></li>' for p in changed)
    document=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Phase5D 最高分／燈號獨立驗證</title>
<style>body{{max-width:1060px;margin:40px auto;padding:0 24px;background:#12121b;color:#edeaf8;font:17px/1.7 system-ui}}h1,h2{{color:#bd9bff}}section{{padding:22px;background:#1b1a28;margin:24px 0;border:1px solid #403450;border-radius:14px}}a{{color:#b8cfff}}td,th{{padding:8px 18px;border-bottom:1px solid #403450;text-align:left}}table{{border-collapse:collapse}}img{{max-width:100%;border-radius:8px}}figure{{margin:25px 0}}code{{overflow-wrap:anywhere}}.pass{{color:#7ee7ad}}figcaption{{color:#c4b9d3}}li{{margin-bottom:5px}}</style>
<h1>My Best Score × Best Clear Lamp</h1><p>v0.14.0 · Schema10 · 2026-10-27</p><section><h2>完成結果</h2><p class="pass">379／379項自動化測試通過，18項新增測試。隔離實際 GUI 提交與動畫驗證通過。</p><p>分數與燈號各自保存來源Record。FAILED可刷新可信EX，但不推算完整MAX。Recent只跟隨分數刷新。</p></section>
<section><h2>周大俠 #7／#15</h2><p>同Song2726／Difficulty2601／HARD／LV22。舊view因新制紀錄存在排除了舊制；不是歷史被覆寫，也不是#15產生NEW SCORE。</p><table><tr><th>紀錄</th><th>EX</th><th>原MAX</th><th>版本</th></tr><tr><td>#7</td><td>3174</td><td>3232</td><td>iidx-v1</td></tr><tr><td>#15</td><td>3067</td><td>3232</td><td>custom-judged-v1</td></tr></table><p>兩筆身份、本人成績列與EX公式證據可靠，因此最高EX保持#7的3174。最佳CLEAR也首次來自#7。MAX相等不是比較依據；舊MAX未有獨立證據，摘要不冒充驗證值，原歷史保留。</p><a href="zhou-investigation.json">原始調查JSON</a></section>
<section><h2>實際瀏覽器操作隔離副本</h2><p>FAILED #18 EX1920只NEW SCORE；其後正常完成#19 EX1900取得AC，只FIRST AC／NEW LAMP。最終最高EX來源#18，最佳燈號來源#19。Recent仍指向#18 FAILED。重整不重播通知。</p><p>已記錄toast-enter、lamp-upgrade、lamp-ring、lamp-shine事件。生成紀錄及畫面僅存在隔離副本，不代表新的真實遊玩驗收。</p>{images}<a href="gui-checks.json">GUI與動畫證據JSON</a></section>
<section><h2>正式資料安全</h2><p>Schema9→10只替換view與驗證trigger，先建立並驗證備份。{migration['existing_records_unchanged']}筆正式成績原值不變，{migration['monitor_files_unchanged']}個監控檔案核對通過。沒有生成測試資料進入正式庫。</p><p>integrity_check：ok；foreign_key_check：0錯誤；舊事件不改寫、不補發。</p><table><tr><th>資料表</th><th>遷移前</th><th>遷移後</th><th>逐表核對</th></tr>{stats}</table><p>備份：<code>{html.escape(migration['migration']['backup_path'])}</code></p><a href="production-migration.json">完整遷移與雜湊稽核</a></section>
<section><h2>操作與限制</h2><p>開啟LibraryGUI.cmd／MonitorGUI.cmd，進入歌曲詳情或歷史頁。舊頁面重新載入即可。VERIFIED_AUTO設定保留，原先停止的監控維持停止。</p><p>跨場次完整判定／同一定義MAX衝突仍暫停比較。未知計分版本不自動加入。沒有新增真實GAME OVER、多人下載或長時間監控端到端驗收。</p><a href="../../docs/PHASE5D2_INDEPENDENT_BESTS.md">操作與相容策略</a> · <a href="unittest-final.log">完整379項測試日誌</a> · <a href="validation.json">驗證JSON</a></section>
<section><h2>修改檔案</h2><ul>{files}</ul></section></html>'''
    (OUT/'report.html').write_text(document,encoding='utf8');print('Report and verified GUI/production audit summaries written.')


if __name__=='__main__':run()
