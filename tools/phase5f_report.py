"""Final read-only production audit and explicitly scoped acceptance report."""
from contextlib import closing
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
from dance_results import __version__
from dance_results.sqlite_safety import audit,projection,row_limits,check_integrity
from tools.phase5f_validate import distribution

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f'


def load(name):return json.loads((OUT/name).read_text(encoding='utf8'))


def read(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def main():
    before=load('before.json')
    with closing(read(ROOT/'data/library.sqlite3')) as db,closing(read(OUT/'baseline.sqlite3')) as original:
        after=audit(db);original_rows=audit(db,projection(original),row_limits(original))==audit(original)
        assert original_rows;check_integrity(db);schema=db.execute('PRAGMA user_version').fetchone()[0]
    image_hashes={str(p.relative_to(ROOT/'tests/專案測試圖片')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tests/專案測試圖片').rglob('*') if p.is_file() and p.suffix.lower()=='.png'}
    assert image_hashes==before['image_hashes']
    monitor_files={str(p.relative_to(ROOT/'data/monitor')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data/monitor').rglob('*') if p.is_file()}
    changed=[p for p,h in before['monitor_files'].items() if monitor_files.get(p)!=h]
    assert not changed
    log=(OUT/'unittest-baseline.log').read_text(encoding='utf8');assert log.rstrip().endswith('OK') and 'skipped' not in log
    count=int(re.search(r'Ran (\d+) tests',log).group(1));assert count==399
    static=load('static/results.json')['summary']
    assert static['image_count']==27 and all(m['correct']==m['total'] for m in static['metrics'].values())
    ocr=load('result-ocr.json');correct=sum(sum(r['checks'].values()) for r in ocr['results']);total=sum(len(r['checks']) for r in ocr['results'])
    lobby=load('lobby-ocr.json');assert all(r['passed'] for r in lobby['results'])
    bench=load('benchmark.json');replay=load('recorded-replay-v2/results.json');gui=load('gui-audit.json');live=load('live-observation.json')
    diagnostics=[json.loads(line) for line in (OUT/'live/monitor/diagnostics.jsonl').read_text(encoding='utf8').splitlines()]
    live_ocr=distribution([d['ocr_ms'] for d in diagnostics if d.get('event')=='recognition' and 'ocr_ms' in d])
    assert all(c['locked_song_id']==c['expected_preplay_song_id'] for c in replay['cases'])
    assert all(not c['automatic_saved'] and c['automatic_issues'] for c in replay['cases'])
    proof=load('gui/submission-proof.json');event=json.loads(proof['notice']['lastEvent'])
    trace=json.loads(proof['notice']['animationTrace']);assert event['updateKind']=='SCORE_UPDATE' and proof['notice']['deliveryCount']=='1'
    assert any(t['name']=='score-flash' for t in trace) and not any(t['name']=='lamp-upgrade' for t in trace)
    assert load('gui/reload-proof.json')==dict(noRepeatToast=0,submitDisabled=True)
    restart=load('gui/restart-proof.json');assert len(restart['records'])==6 and restart['toast'] is None
    growth=load('gui/growth-proof.json');assert {e['kind'] for e in growth['growth']}=={'DOUBLE_UPDATE','SCORE_UPDATE','LAMP_UPDATE','NO_UPDATE'}
    assert 'EX 2100' in restart['bests'][0] and 'ACP' in restart['bests'][1]
    # The concurrent full scan updates scan markers; source identities must remain stable.
    with closing(read(OUT/'baseline.sqlite3')) as original,closing(read(OUT/'benchmark.sqlite3')) as scanned:
        identities={}
        for table,key,columns in [('songs','song_id','song_id,root_id,pack_id,relative_folder,saved_title'),('packs','pack_id','pack_id,root_id,name,relative_path'),('song_sources','source_id','source_id,song_id,relative_path,saved_title')]:
            rows=original.execute('SELECT '+columns+' FROM '+table+' ORDER BY '+key).fetchall()
            ids=[r[0] for r in rows]
            current=scanned.execute('SELECT '+columns+' FROM '+table+' WHERE '+key+'<=? ORDER BY '+key,(max(ids),)).fetchall()
            assert current==rows;identities[table]=dict(existing_count=len(rows),ids_names_paths_unchanged=True)
    files=['tools/'+n for n in ('phase5f_live.py','phase5f_validate.py','phase5f_replay.py','phase5f_gui.py','phase5f_gui_audit.py','phase5f_report.py')]+['docs/PHASE5F_VALIDATION.md','README.md']
    result=dict(version=__version__,schema=schema,migration_required=False,product_core_changed=False,
        scope='Phase5F automatically executable validation completed; new user gameplay acceptance deferred by user',
        tests=dict(existing_suite=count,passed=count,skipped=0,log='unittest-baseline.log',new_unit_tests=0),
        production=dict(all_tables_unchanged=after==before['tables'],original_rows_unchanged=original_rows,before=before['tables'],after=after,
            original_monitor_files_unchanged=True,original_pngs_unchanged=len(image_hashes),integrity='ok',foreign_key_errors=0),
        static=static,
        full_result_ocr=dict(correct_fields=correct,expected_fields=total,fully_correct_images=sum(r['passed']for r in ocr['results']),images=len(ocr['results']),
            limitation='One multiplayer GAME OVER Cool=1 was below confidence threshold; retained NULL, never defaulted to zero.'),
        fresh_lobby_ocr=dict(passed=2,total=2,new_download_observed=False,reliable_progress_observed=False),
        original_preplay_replay=dict(correct_locked_identities=2,total=2,automatic_submissions=0,issues={str(c['original_record_id']):c['automatic_issues'] for c in replay['cases']},simulated_playing=True,new_real_gameplay=False),
        state_longevity=load('replay.json'),bests=load('bests.json'),gui=gui,gui_restart=restart,
        performance=dict(live_samples=live['samples'],live_cpu_machine=live['cpu_machine_percent'],live_rss=live['rss_mb'],live_metrics=live['monitor']['metrics'],live_ocr_ms=live_ocr,
            benchmark_latency=bench['latency'],scan_seconds=bench['scan_seconds'],concurrent_resources=bench['resource_summary']['scan_and_ocr'],
            interpretation='Screenshot replay load, not game FPS; live sample is short window observation, not a long-duration memory/leak test.'),
        scan=dict(identities=identities,results=bench['scans'],records_unchanged=bench['old_records_unchanged'],
            new_song_interpretation='A newly imported row is not proof of a download during this run; no observed NO MAP episode attributed it.'),
        phase5d=dict(implemented=True,remaining=['real multiplayer NO MAP download to unique identity and verified auto submission','independent Pack selected-versus-hover reference evidence']),
        phase5e=dict(implemented=True,known_unimplemented_items=[],real_game_acceptance_remaining=True),
        modified_files=files,new_real_gameplay_end_to_end=False,formal_test_records_written=0)
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    report=f'''# Phase 5F 驗證報告

日期：2026-10-27。**v{__version__} / SQLite Schema {schema}**。本輪加入可重複執行的驗證工具與報告；沒有修改計分、OCR 門檻、監控架構或資料庫結構。沒有測試成績寫入正式庫。

**自動驗證部分完成，新的實際遊玩端到端驗收尚未完成。** 使用者選擇先完成回放，稍後實玩；下列各類證據分開列示。

## Phase 5D / 5E 現況

Phase5D已實作本人NO MAP、檔案基準快照、PLAYING背景全SONG掃描、本次新增Song候選、安全匹配／人工確認與Recent Update。真實多人下載→鎖定身份→本人結算→VERIFIED_AUTO提交完整鏈仍待驗收。Pack紫色背景仍只能作候選，沒有獨立證據可靠區分Hover／真正選取，不能沿用可能過期的Pack。

Phase5E成績單、獨立最佳、成長履歷、更新摘要與動畫已實作。本輪未發現缺少的5E程式功能；新的遊玩觸發動畫與長時間遊玩驗收尚待使用者操作，不等於已驗收。

## 測試分類與結果

| 類別 | 本輪結果 | 限制 |
|---|---|---|
| 全部既有自動化測試 | **{count}/{count}通過，0略過** | 單元／隔離SQLite／模擬視窗／HTTP測試；不是新實玩 |
| 原始27張PNG靜態分類 | **27/27**；可見選歌欄位29/29、遮擋NULL6/6、本人結算狀態5/5 | 原圖不變，重新執行模型；分類正確不代表所有判定數均可靠 |
| 完整結算OCR | **{correct}/{total}欄位**，{sum(r['passed']for r in ocr['results'])}/5整張符合人工標註 | 包括旁觀者安全NULL，不能將NULL斷言算成數字辨識成功；本人數值24欄中23欄正確 |
| 兩張多人等待室新OCR | **2/2**確認TC在第三格NO MAP | 沒有可信的本人下載綠條；progress保留NULL，不能當真實下載成功 |
| 保存過的選歌→鎖定→結算回放 | **2/2身份正確；0/2自動提交** | PLAYING與時鐘明確模擬；低信心結算欄位安全保留Pending |
| 模擬長停留 | 同一結算重複10,000次只保存1個Session／結果 | 快速狀態模擬，並非長時間遊玩 |
| 新的真實視窗觀測 | 找到dance v2.0.1-dev-r2125-73e3f；604幀／37次辨識 | 約5分鐘選歌／等待室觀測；沒有新PLAYING→RESULT或正式提交 |
| 隔離GUI | 提交、動畫、重整、HTTP重送、整個程式重啟通過 | 明確標記的生成資料，不是真實遊玩 |
| 使用者新一輪實玩E2E | **待進行** | 本輪未宣稱通過 |

完整結算OCR中，多人GAME OVER的Cool=1僅取得0.836信心，保留NULL。旁觀者不擷取他人成績。多人底部LV仍標記為HOST所選譜面，必須使用本人已鎖定難度。

原始先行確認的Custom〈Future Gazer〉鎖定Song101、Official K〈千年之戀〉鎖定Song2333。使用新模型直接重讀原圖，沒有提高信心或事後拿正式Record反推身份。前者grade／LV不夠可靠且grade ROI矛盾，後者Miss讀不到；兩者均阻擋自動提交。不能將這兩次稱為完全自動成績流程通過。

## My Best、Best Lamp、Recent Update

正式庫唯讀副本核對：

- 周大俠／Song2726／HARD：#7 EX3174為My Best，#15 EX3067沒有NEW SCORE；最佳CLEAR保持#7。
- Macuilxochitl／Song127／HARD：My Best2029來自#18；最佳AC來自#19 EX1999。#19只有NEW LAMP、不進入Recent。
- 現有Recent為18組；每組Song ID＋難度唯一，重開資料庫內容一致。30組上限、時區／played_at排序、同名不同ID、FAILED合法刷新、未知版本與完整基準衝突皆有既有測試覆蓋。

隔離GUI新增生成序列：FIRST BEST/CLEAR → 分數上升 → 分數下降但AC → FAILED高EX → 普通儲存 → ACP雙刷新。依次為DOUBLE_UPDATE、SCORE_UPDATE、LAMP_UPDATE、SCORE_UPDATE、NO_UPDATE、DOUBLE_UPDATE。FAILED保留完整MAX與百分比NULL；NORMAL獨立建立。Recent只保留這首歌最新HARD分數刷新與NORMAL首次分數，沒有燈號單獨事件。

正式按鈕提交一次後，HTTP完全相同請求重送返回原Record；沒有第二筆成績或事件。NEW SCORE只播放一次，含score-flash／+EX，沒有lamp-upgrade。重整後按鈕停用，重啟服務仍為相同6筆HARD紀錄、相同最佳與無重播通知。歌曲詳情、成長履歷與成績單一致；瀏覽器未發現warning/error。

## NO MAP 與背景掃描

既有自動化測試涵蓋connect／其他Pack／新Pack、部分檔案延後重試、既有Song補來源不算新增、Barry舊歌不展開、快照前下載不冒充本次新增、第二列本人與HOST分離、安全匹配後同一正式提交服務、重啟後恢復快照與Pending。

本輪額外以真實SONG整庫唯讀掃描至隔離副本，處理3,079個來源，新增匯入1首／7來源，18來源因未完整而延後、0錯誤。這1首是在掃描時首次入庫，不是本輪觀測到下載，不歸入NO MAP確定新增。原Song／Pack／Source身份、名稱、路徑與原正式紀錄均保留。GUI只顯示下載摘要，未展開整個舊歌曲庫。診斷保存在JSON／日誌。

## 效能

16個邏輯CPU；100%單核相當於整機6.25%。以下CPU是本程式占用整機總CPU能力，不是整台機器的總負載。

| 測量情境 | 平均 | p95／峰值 |
|---|---:|---:|
| 被動真實視窗觀測 CPU | {live['cpu_machine_percent']['mean']:.2f}% | p95 {live['cpu_machine_percent']['p95']:.2f}% |
| 被動視窗RSS | {live['rss_mb']['mean']:.1f}MB | 外部固定間隔取樣峰值 {live['rss_mb']['max']:.1f}MB |
| 被動視窗OCR，{live_ocr['n']}樣本 | 平均 {live_ocr['mean']:.0f}ms／中位數 {live_ocr['median']:.0f}ms | p95 {live_ocr['p95']:.0f}ms |
| 選歌圖重播，無掃描，8樣本 | {bench['latency']['without_scan']['all']['mean']:.3f}s | p95 {bench['latency']['without_scan']['all']['p95']:.3f}s |
| 同圖重播＋全SONG掃描，54樣本 | {bench['latency']['during_scan']['all']['mean']:.3f}s | p95 {bench['latency']['during_scan']['all']['p95']:.3f}s |
| 全SONG背景掃描 | {bench['scan_seconds']:.2f}s | 期間OCR持續完成，沒有停止 |
| 掃描＋OCR壓力情境CPU | {bench['resource_summary']['scan_and_ocr']['cpu_machine_percent']['mean']:.2f}% | RSS峰值 {bench['resource_summary']['scan_and_ocr']['rss_mb']['max']:.1f}MB |

ROI請求可能回退全畫面，因此上表是實際選歌辨識整段耗時，不能把請求ROI當作純ROI耗時。掃描期間平均延遲增加約{(bench['latency']['during_scan']['all']['mean']/bench['latency']['without_scan']['all']['mean']-1)*100:.1f}%。未測遊戲FPS，未做30分鐘以上記憶體／熱機驗證，不能由短期RSS推論無洩漏。沒有據此重寫架構。

## 正式資料保護

正式庫原22筆PlayRecord、Session、Song ID、MAX與燈號逐列不變；本輪全部正式表雜湊{'完全相同' if after==before['tables'] else '有使用期間新增，原列保持相同'}。27張原圖雜湊不變、正式monitor既有檔案不變；完整性與外鍵檢查通過。沒有migration、沒有重匯官方CSV、沒有修改遊戲或輸入控制。測試的新增Record只在reports/phase5f隔離庫。

## 問題與本輪處理

1. 真實新OCR暴露小數字與grade的低信心／矛盾：保留Pending，沒有降低門檻、補0或硬編碼答案。後續若改善裁切，必須同時驗證別的玩家列與0／NULL。
2. source_mode=auto不能憑歌名可靠決定Official／Custom，仍需預先確認來源；Custom Pack仍需可靠確認。這些是近乎免操作目標的實際限制。
3. CSV／judged_total差異的VERIFIED_AUTO部署閘門仍存在，需要實玩證據才評估放寬；人工自訂MAX提交仍可用。
4. 現有安全／最佳紀錄／Recent邏輯未發現需要修正的資料錯誤。本輪最小變更是加入可重跑驗證、效能與原資料稽核，核心版本維持不變。

## 稍後實玩驗收清單

先使用隔離庫與獨立monitor目錄；停止其他測試OCR，避免重複耗用資源。正式庫不啟用測試自動提交。

1. **Official K單人**：來源預先選官方K；停在選歌10秒，記錄唯一SongID／難度／歌曲LV；再開始、正常完成，結算停留10秒。核對鎖定前後ID、P/C/B/M、EX、自訂MAX、實際played_at、同Session單一Record、My Best／Lamp／Recent及動畫。低信心應Pending，不能為通過而手動猜身份。
2. **Custom與Pack切換**：選擇Custom、提供未選／Hover／選取後移開滑鼠／清單關閉四種原圖；確認Pack後切換兩張JOINT，分別821／929與96／95秒，驗證不沿用上一首。未有可靠Pack時必須Pending。
3. **GAME OVER**：本人確實GAME OVER，結算停留10秒；FAILED保存部分P/C/B/M與EX，但無獨立完整依據時MAX／百分比NULL，最佳燈號不降級；結算長停留不重複提交。
4. **多人NO MAP**：監控先建立基準，HOST再選本機缺譜；保留本人NO MAP、本人實際進度條與下載後檔案變動證據。PLAYING觀察非阻塞掃描，核對新SongFolder；本人在第二或第三列、HOST不同數值。無唯一身份／完整匹配證據必須Pending，不能只依新增一首或歌名自動選擇。
5. **跨Pack下載與耐久**：分別下載至connect、既有Pack與新Pack；普通遊玩20–30分鐘，期間最小化／失焦／恢復、GUI重整、停止再啟動監控、遊戲關閉重開。沒有結算只INTERRUPTED，不虛構FAILED；核對CPU/RSS趨勢與Session數量。遊戲輸入由使用者操作。

## 檔案與重跑

修改檔案：

{chr(10).join('- '+f for f in files)}

報告資料：validation.json、bests.json、result-ocr.json、lobby-ocr.json、benchmark.json、live-observation.json、recorded-replay-v2/results.json、gui-audit.json、replay.json、static/results.json、unittest-baseline.log。gui/下為實際瀏覽器截圖與DOM／動畫證據。

一般使用仍雙擊LibraryGUI.cmd／MonitorGUI.cmd。測試GUI（隔離庫已建立）執行 `.venv-ocr/Scripts/python.exe -m tools.phase5f_gui`，網址http://127.0.0.1:40511/reviews；不是正式歌曲庫。各準備／benchmark工具拒絕覆寫已有隔離實驗，重跑請先保留舊輸出並選新實驗目錄。

本輪完成自動化與隔離GUI驗證；**Phase5F真實遊玩驗收仍待上述操作，不能宣稱全階段完成或無人介入自動儲存已通過。**
'''
    (OUT/'report.md').write_text(report,encoding='utf8')
    # Use ordinary HTML, no external resources; Markdown source is linked beside it.
    figures=''.join('<figure><img src="gui/'+name+'" alt="'+caption+'"><figcaption>'+caption+'</figcaption></figure>' for name,caption in [('independent-bests.png','隔離副本中的真實保存紀錄：#18最佳分數／#19最佳AC'),('generated-history.png','明確標記的生成測試資料：分數／燈號更新各自獨立'),('monitor-summary.png','真實視窗擷取已停止；NO MAP只顯示摘要')])
    (OUT/'report.html').write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>Phase5F 驗證報告</title><style>body{background:#12131c;color:#eee;font:16px/1.75 "Segoe UI",sans-serif;max-width:1100px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;font:inherit}img{max-width:100%;border:1px solid #645187}figure{margin:35px 0}a{color:#c5b2fa}</style><h1>Phase5F：自動驗證完成，實玩待驗收</h1><p><a href="report.md">Markdown報告</a> · <a href="validation.json">結構化驗證資料</a></p><pre>'+html.escape(report)+'</pre>'+figures+'</html>',encoding='utf8')
    print('Read-only formal-data audit and Phase5F report passed; no production rows changed.')


if __name__=='__main__':main()
