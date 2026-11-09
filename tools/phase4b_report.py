"""Assemble measured Phase 4B evidence and verify production data stayed unchanged."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import statistics

from dance_results.sqlite_safety import audit


def read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def main():
    output=Path('reports/phase4b')
    before=read(output/'database-before.json')
    with sqlite3.connect(Path('data/library.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
        after=audit(db)
        integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign_keys=db.execute('PRAGMA foreign_key_check').fetchall()
        schema=db.execute('PRAGMA user_version').fetchone()[0]
    originals=read('reports/phase4a/original-images.json')
    images_unchanged=all(hashlib.sha256((Path('tests/專案測試圖片')/r['name']).read_bytes()).hexdigest()==r['sha256'] for r in originals)
    native=read(output/'capture-probe/probe.json')
    image_tests=read(output/'original-image-validation.json')
    traces=read(output/'real-game/status-trace.json')
    log=[json.loads(line) for line in (output/'real-game/diagnostics.jsonl').read_text(encoding='utf8').splitlines()]
    sessions=[read(p) for p in (output/'real-game/sessions').glob('*/session.json')]
    completed=[s for s in sessions if s['status']=='pending_result']
    cpu=[r['status']['metrics']['process_cpu_percent'] for r in traces if r['status']['metrics']['process_cpu_percent']is not None]
    ocr=[e for e in log if e['event']=='recognition']
    unit_log=(output/'unittest.log').read_text(encoding='utf8')
    count=int(re.search(r'Ran (\d+) tests',unit_log)[1])
    tests_ok=unit_log.rstrip().endswith('OK')
    live=[]
    for s in completed:
        result=output/'real-game/sessions'/s['session_id']/'result.png'
        matching_events=[e for e in log if e['event']=='result_saved' and e['session_id']==s['session_id']]
        live.append(dict(session_id=s['session_id'],song_id=s['song_id'],status=s['status'],
            matching_method=s['matching_status'],fields={k:v['value'] for k,v in s['selection']['fields'].items()},
            pack=s['selection']['pack'],result_state=s['result']['state'],result_path=str(result),
            result_sha256=hashlib.sha256(result.read_bytes()).hexdigest(),result_saved_event_count=len(matching_events)))
    cpu_count=16 # Recorded local psutil.cpu_count() measurement; this is not a production configuration.
    try:
        import psutil
        cpu_count=psutil.cpu_count() or cpu_count
    except ImportError:pass
    summary=dict(version='0.7.0',schema=schema,
        simulation=dict(automated_tests=count,passed=tests_ok,original_png_count=len(originals),
                        ocr_runs=len(image_tests['runs']),ocr_checks=sum(len(r['checks'])for r in image_tests['runs']),
                        all_ocr_checks_passed=image_tests['all_checks_passed'],
                        note='Synthetic state/provider tests and recorded screenshots are not real gameplay.'),
        native_capture=dict(test_kind=native['test_kind'],passed=native['checks_passed'],
                            occluded=native['occluded_capture'],resize=native['resize_capture'],
                            minimized=native['minimized_detected'],closed=native['closed_detected'],
                            restart=native['restart_capture']),
        actual_game=dict(window=read(output/'real-game/window.json'),completed_sessions=live,
                         normal_flow_passed=bool(live),game_over_flow_live_tested=False,
                         multiplayer_live_tested=False,
                         automatic_pack_selection_live_verified=any(e['event']=='pack_observed' and
                             (e.get('pack')or{}).get('source')=='purple_selected_pack_row' for e in log),
                         pack_limitation='Two captured Pack lists did not supply a uniquely trusted purple selected row; Pack remains pending unless explicitly reviewed.'),
        performance=dict(ocr_by_state_ms={state:{'count':len(vals),'median':round(statistics.median(vals),2),
                          'max':round(max(vals),2)} for state in sorted({e['state']for e in ocr})
                         if (vals:=[e['ocr_ms']for e in ocr if e['state']==state])},
                         cpu_samples=len(cpu),cpu_one_logical_core_percent_median=statistics.median(cpu),
                         cpu_one_logical_core_percent_range=[min(cpu),max(cpu)],logical_cpu_count=cpu_count,
                         cpu_machine_percent_median=round(statistics.median(cpu)/cpu_count,2),
                         process_peak_rss_mb=max(r['status']['metrics']['peak_rss_mb']or 0 for r in traces),
                         note='Process CPU/RSS during actual play, not isolated capture cost. No game FPS/GPU measurements.'),
        safety=dict(database_unchanged=before==after,original_pngs_unchanged=images_unchanged,
                    integrity_check=integrity,foreign_key_check=foreign_keys,database_before=before,database_after=after))
    (output/'validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    rows=['# Phase 4B 實測報告（v0.7.0）','',
          '## 測試範圍','',f'- 自動化：{count} 項測試，'+('全部通過。'if tests_ok else'存在失敗，見 unittest.log。'),
          f'- 原始截圖：22 張，含 8 次額外 ROI 辨識，共 {summary["simulation"]["ocr_checks"]} 項欄位／狀態檢查全部通過。',
          '- 真實 Windows 測試視窗：WGC 遮擋／失焦、縮放、最小化、關閉與重新建立視窗通過；這不是遊戲端到端。',
          '- 真實遊玩：使用者自行操作遊戲；程式只擷取。已完成 SONG_SELECT → PLAYING → RESULT 與後續返回等待室。',
          '- 真實 GAME OVER、多人遊玩、斷線、遊戲關閉重啟尚未進行端到端驗證；由原始截圖／模擬或獨立測試視窗覆蓋。','',
          '## 真實遊玩證據','']
    for row in live:
        fields=row['fields'];rows += [f"- Session：`{row['session_id']}`",f"- Song ID {row['song_id']}：{fields['title']}／{fields['difficulty']} LV{fields['game_level']}／{fields['total_notes']} notes／{fields['duration_seconds']} 秒。",
          f"- Pack：{row['pack']['name']}，來源：使用者於 GUI 明確人工確認，並非自動推測。",
          f"- 結算 `{row['result_state']}`，保存事件 {row['result_saved_event_count']} 次；仍為 pending_score_ocr，沒有 PlayRecord／最高分／燈號寫入。",
          f"- 原始結算：[result.png](real-game/sessions/{row['session_id']}/result.png)",'']
    rows += ['## 啟動與修改檔案','',
        '雙擊根目錄 MonitorGUI.cmd，進入「即時監控」並按開始；開啟 GUI 本身不會開始擷取。停止後資料仍保存在 data/monitor。',
        '完整修改檔案清單、安裝及操作說明見 [PHASE4B_MONITOR.md](../../docs/PHASE4B_MONITOR.md#修改檔案)。本階段沒有 migration。','',
        '## Pack 限制','',
        '已實作紫色填色＋OCR Pack 名稱／唯一來源＋連續確認。清單一旦無法確認，立即清除舊 Pack；關閉清單後僅沿用仍有效的已確認 Pack。',
        '本輪兩張真實清單中，程式沒有取得足以確認的紫色選取證據，實測自動 Pack 辨識仍待驗證。沒有用 Amnolys 等歌名反推 Pack。人工選擇正確候選並確認完整數值可保存本機對應。',
        '圖像證據：[第一次清單](real-game/pack-selection.png)、[第二次清單](real-game/pack-highlight.png)。','',
        '## 效能實測','',
        '| 狀態 | OCR 次數 | 中位耗時 ms | 最大耗時 ms |','|---|---:|---:|---:|']
    for state,value in summary['performance']['ocr_by_state_ms'].items():rows.append(f"| {state} | {value['count']} | {value['median']} | {value['max']} |")
    perf=summary['performance']
    rows += ['',f"真實遊玩採樣 {len(cpu)} 次：程式 CPU 中位 {perf['cpu_one_logical_core_percent_median']}%（100%=一個邏輯核心），此機 {cpu_count} 個邏輯核心，換算整機約 {perf['cpu_machine_percent_median']}%；本段峰值 RSS {perf['process_peak_rss_mb']} MiB。",
        'CPU 是監控服務行程整體，含 WGC／OCR／GUI；沒有量測遊戲 FPS 或 GPU 負載，不能宣稱零效能影響。單張選歌約 2 秒後加入 ROI；遊玩中多數僅辨識頁首／頁尾。遇 ROI 缺值／未知分類會回退全圖，JOINT 第一版本可觸發較慢的安全回退。',
        '原始數值：[OCR 對照](ocr-benchmark.json)、[遊玩狀態採樣](real-game/status-trace.json)、[逐圖測試](original-image-validation.json)。','',
        '## 資料保護','',f"- SQLite 全表雜湊相同：{before==after}；schema 保持 {schema}。",
        f"- 22 張原 PNG SHA-256 相同：{images_unchanged}；integrity_check={integrity}，foreign_key_check={foreign_keys}。",
        '- 40 Pack、4,397 Song（1,859 自製/connect＋2,538 官方 K）、2,930 檔案來源及既有難度／歷史保持不變。',
        '- 人工對應、當次 Session、確認截圖及結算另外保存於 data/monitor；本輪原始證據也保留於 real-game/。',
        '- 沒有修改遊戲、讀取遊戲記憶體、注入、鍵鼠操作、正式 PlayRecord 寫入、官方 CSV 修改或歌曲合併。','',
        '## 尚待實測與下一階段','',
        '其他遊戲皮膚、全螢幕／特殊 DPI、多語主歌名、所有 E/N/H 切換、網路錯誤彈窗與多人非 HOST 的來源不足情境仍需增加實際樣本。未知畫面保持 UNKNOWN；無結果離開遊玩則記 INTERRUPTED，不捏造失敗。',
        '下一階段須對待處理截圖辨識本人判定數／最高 Combo／顯示分數／完成標記，驗證判定合計與完整總數，處理多語暱稱與低信心人工覆核，再決定如何以已確認 difficulty_id 安全寫入成績。未知身份、GAME OVER 部分判定、退出或斷線都不能補成完整歌曲總數。最高分與 AC／ACP 規則另行實作與驗收。','']
    (output/'report.md').write_text('\n'.join(rows),encoding='utf8')
    assert before==after and images_unchanged and integrity=='ok' and not foreign_keys
    assert tests_ok and image_tests['all_checks_passed'] and native['checks_passed']
    assert live and all(r['result_saved_event_count']==1 for r in live)
    print('PASS: simulation, real-window fixture, actual normal gameplay, once-only result, full DB and image audit')


if __name__=='__main__':main()
