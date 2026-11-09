"""Render the completed local investigation from its recorded evidence."""
import html
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'reports/phase5a1'
FILES=['dance_results/note_evidence.py','dance_results/ocr_matching.py','dance_results/result_review.py',
       'dance_results/monitor_ocr.py','dance_results/monitor_tracking.py','dance_results/monitor.py',
       'dance_results/submissions.py','dance_results/gui.py','dance_results/gui_assets/reviews.html',
       'dance_results/gui_assets/reviews.js','dance_results/gui_assets/reviews.css','dance_results/__init__.py',
       'tests/test_note_assistance.py','tools/validate_phase5a1.py','tools/build_phase5a1_report.py',
       'README.md','docs/PHASE5A1_NOTES.md','docs/PHASE4_MATCHING_SPEC.md','docs/VALIDATION.md']


def main():
    report=json.loads((OUT/'latest.json').read_text(encoding='utf8'))
    audit=json.loads((OUT/'production-after.json').read_text(encoding='utf8'))
    log=(OUT/'unittest.log').read_text(encoding='utf8')
    count,seconds=re.search(r'Ran (\d+) tests in ([\d.]+)s',log).groups()
    assert log.rstrip().endswith('OK') and int(count)==226
    h=report['heart'];samples=report['samples'];e=html.escape
    sample_rows=''.join('<tr><td>'+e(name)+'</td><td>'+str(s['ocr_ms'])+' ms</td><td>'+e(s['fields']['title']['value'] or '')+'</td><td>'+e(str(s['fields']['player_name']['value']))+'</td></tr>' for name,s in samples.items())
    judgment_rows=''.join(f'<tr><td>{e(key)}</td><td>{item["value"]}</td><td>{item["confidence"]*100:.3f}%</td><td>{e(str(item["evidence"][0]["box"]))}</td></tr>' for key,item in h['saved_judgments'].items())
    files=''.join(f'<li><a href="../../{e(p)}">{e(p)}</a></li>' for p in FILES)
    test=dict(total=int(count),new=27,original=199,seconds=float(seconds),skipped=0,passed=True,
              fresh_gameplay=False,production_changed=False)
    (OUT/'summary.json').write_text(json.dumps(dict(version='0.9.1',schema=6,tests=test,
        changed_files=FILES,investigation=str((OUT/'latest.json').resolve()),
        gui_test_database=str((OUT/'run-28fe38a086/library.sqlite3').resolve()),
        gui_url='http://127.0.0.1:40470/reviews',production_audit=audit),ensure_ascii=False,indent=2),encoding='utf8')
    body=(ROOT/'docs/PHASE5A1_NOTES.md').read_text(encoding='utf8')
    body+='\n\n## 修改檔案\n\n'+'\n'.join('- `'+f+'`' for f in FILES)
    body+='\n\n## 本次精確結果\n\n'
    body+=f'自動化 {count}/{count} 通過，耗時 {seconds}s，無跳過。隔離演練：`{report["run_path"]}`。\n\n'
    body+='\n'.join(f'- {name}：{sample["ocr_ms"]}ms，原圖 `{sample["path"]}`。' for name,sample in samples.items())
    body+='\n\n[調查 JSON](latest.json) · [摘要 JSON](summary.json) · [完整測試紀錄](unittest.log) · [正式庫前後核對](production-after.json)\n\n'
    body+='![隔離副本中的差異核對](gui-note-verification.jpg)\n'
    (OUT/'report.md').write_text(body,encoding='utf8')
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase 5A.1 驗證報告 · 0.9.1</title><style>
body{{margin:0;background:#11101a;color:#eeeaf7;font:16px/1.8 'Segoe UI','Microsoft JhengHei',sans-serif}}main{{max-width:1080px;margin:auto;padding:40px 24px}}h1{{font-size:32px}}h2{{font-size:23px;color:#c6a4ff}}a{{color:#c6a4ff}}section{{background:#1b1826;border:1px solid #393143;border-radius:14px;margin:24px 0;padding:24px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #40374a;padding:10px;text-align:left;overflow-wrap:anywhere}}th{{color:#c6a4ff}}code{{overflow-wrap:anywhere}}.tag{{color:#83d4b0}}.pending{{border-left:4px solid #f4bf72;padding:15px;background:#30251f}}.muted{{color:#bcb0c9}}img{{max-width:100%;border-radius:9px}}.preview{{font-size:23px;font-weight:600}}.proof{{max-width:430px}}.files{{font-size:14px}}@media(max-width:650px){{main{{padding:18px}}section{{padding:14px}}table{{font-size:12px}}}}
</style><main><span class="tag">0.9.1 · SQLITE SCHEMA 6 · {count} TESTS PASSED</span><h1>完整音符數核對與安全自動填入</h1>
<p>功能與隔離驗證完成。Heart of Witch 的 1194／1192 差異保留待確認，沒有修改正式成績或原始證據。</p>
<p><a href="report.md">完整說明與修改檔案</a> · <a href="latest.json">調查 JSON</a> · <a href="summary.json">驗證摘要</a> · <a href="unittest.log">測試紀錄</a></p>
<section><h2>Heart of Witch：證實了哪些資訊</h2><table><tr><th>來源</th><th>數值與狀態</th></tr>
<tr><td>當次選歌 OCR</td><td>未知。Session selection=null，使用者確認未保留選歌圖。1194／1:33 來自使用者提供的遊玩資訊。</td></tr>
<tr><td>官方 K CSV／SQLite</td><td>fileId15007／sdom5007k.gn，Song4343、HARD Difficulty7450、LV29、notes1194。原 CSV 第4945筆與匯入原值相同，SHA-256 相同。</td></tr>
<tr><td>結算原圖及重新 OCR</td><td>P1145 + C30 + B7 + M10 =1192。原始與新 OCR 一致。</td></tr>
<tr><td>程式是否覆寫完整數</td><td>沒有。原 Session 沒有1194選歌值；判定合計另算，不回填完整數。</td></tr></table>
<p class="pending">尚未驗證修改版遊戲的計數規則或兩顆差異原因，也缺少當次選歌鎖定證據。不能據此減掉完整數、修改判定或建立「差2就通過」特例。</p>
<p class="preview">EX2320；假設完整數1194適用 → MAX2388 → MAX -68</p><p>僅為條件式算式。正式 MAX／百分比／燈號仍未確認；Heart 的確認與正式提交均已在副本測試阻擋。</p>
<details><summary>原始結算的數字與信心度／框線</summary><table><tr><th>欄位</th><th>數字</th><th>信心度</th><th>正規化框線</th></tr>{judgment_rows}</table></details>
<p><a href="../../data/monitor/sessions/944a2977-fe34-4112-a990-e864c317287c/result.png">查看未修改的結算原圖</a></p></section>
<section><h2>已完成的操作改善</h2><ul><li>四種音符來源分開顯示與保存；NULL 不補0，判定合計不取代完整數。</li><li>官方來源、難度及可靠畫面資料唯一匹配後填入 Song ID／LV／完整數，顯示 fileId、Artist、BPM。</li><li>Custom 完整人工對應可重用；須核對 Pack、完整畫面數值、SongFolder與原始證據。未知同名候選仍保留。</li><li>已有人工數值不被自動覆蓋；來源或數字衝突保持待確認。有效的差異草稿可以保存與重啟載入。</li><li>計分公式及正式提交限制沒有放寬；使用者須明確確認與提交。</li></ul>
<p>啟動 MonitorGUI.cmd → 待確認成績。Heart 這筆選 HARD、確認官方 K、按「核對來源並填入已知資料」。新介面：<a href="http://127.0.0.1:40470/reviews">開啟待確認成績</a>；只有服務運作時可用。</p>
<a href="gui-heart-review.jpg"><img class="proof" src="gui-note-verification.jpg" alt="隔離副本的音符來源核對、條件式MAX-68與停用提交"></a><p class="muted">實際 GUI 截圖，操作對象為隔離副本；可點擊查看完整頁面。正式庫中的既有 Review 未保存或提交。</p></section>
<section><h2>測試結果</h2><p class="tag">{count}/{count} 自動化通過 · {seconds}s · 無跳過（原199＋新27）</p>
<p>新增測試涵蓋差異來源、條件式計分、NULL／GAME OVER、官方唯一及同名候選、低信心與無效數值、完整人工對應重用、原圖變更、HTTP權限／唯讀提案。原有冪等提交、備份及交易回滾測試持續通過。</p>
<table><tr><th>新 OCR 原圖</th><th>本次耗時</th><th>辨識歌名</th><th>本人</th></tr>{sample_rows}</table>
<p>多人結算讀取 TC 列；底部 LV 的 HOST 範圍標記保留，不冒充本人的難度。這些是保存的真實圖片重新辨識，不是新一次多人遊玩。</p>
<p>副本 Heart 草稿保存／重載通過，確認與提交拒絕。副本 Cagayake 提交 MAX -3／AC，重送回同一筆 Record。GUI 實際操作核對與填入1194、保存草稿、重載及停用提交通過。</p>
<p class="muted">HTTP 初次回歸曾出現 Windows 連線提前中斷；修正拒絕請求前的小型 POST 內容限量丟棄，權限條件不變。保留 <a href="unittest-before-http-fix.log">修正前紀錄</a>與<a href="http-tests.log">針對性測試紀錄</a>，最終完整結果見 unittest.log。</p></section>
<section><h2>正式資料保留</h2><p>41 Pack／4415 Song（1877 Custom/connect＋2538 Official）／2966 檔案來源／7612 難度。PlayRecord0、PlaySession0。這是本次開工時原有數量。</p>
<p>Schema6不變、無migration、正式庫全表內容雜湊前後相同。原始 Session／Review／PNG 及官方 CSV 保留。新分離資訊使用既有 JSON 證據欄位，沒有改動 Song ID 或增加重複 SQL 結構。</p>
<p><a href="before.json">開工基線</a> · <a href="production-after.json">完成後完整性核對</a></p></section>
<section><h2>仍需驗證與 Phase5B</h2><p>沒有新增一次實際遊玩端到端；未證實兩顆差異的遊戲原因。後續需要同一版本的選歌／完整遊玩／結算成組資料，或修改版遊戲的計數實作。Pack Hover／真實 GAME OVER／更多多人情境仍保留原限制。</p><p>Phase5B可先做已驗證紀錄的基本燈號 GUI，保留待確認及FAILED區分。Heart不得當作已驗證正式成績，也不納入最高分。差異例外與自動大量提交需另行驗證與設計。</p></section>
<section><h2>修改檔案</h2><ul class="files">{files}</ul></section></main></html>'''
    (OUT/'report.html').write_text(page,encoding='utf8')
    print(json.dumps(test))


if __name__=='__main__':main()
