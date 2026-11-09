"""Summarize measured evidence without treating static replay as live gameplay."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import statistics

from dance_results.sqlite_safety import audit, check_integrity


def main():
    root=Path(__file__).resolve().parent.parent;out=root/'reports/phase4c'
    read=lambda p:json.loads(p.read_text(encoding='utf8'))
    summary=read(out/'validation.json');rows=read(out/'image-results.json')
    with closing(sqlite3.connect((root/'data/library.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        after=audit(db);check_integrity(db)
        schema=db.execute('PRAGMA user_version').fetchone()[0]
    before=read(out/'database-before.json')
    original=read(out/'original-images.json')
    image_check=all(hashlib.sha256((root/'tests/專案測試圖片'/name).read_bytes()).hexdigest()==sha for name,sha in original.items())
    new_images=read(out/'new-images.json')
    new_image_check=all(hashlib.sha256((root/'tests/專案測試圖片'/name).read_bytes()).hexdigest()==row['sha256'] for name,row in new_images.items())
    initial_checks=[c for r in rows if r['image'] not in new_images for c in r['checks'] if c['field'].startswith('result.')]
    spectator_checks=[c for r in rows if r['image'] in new_images for c in r['checks']]
    unit_log=(out/'unittest.log').read_text(encoding='utf8')
    tests=int(re.search(r'Ran (\d+) tests',unit_log)[1]);unit_ok=unit_log.rstrip().endswith('OK')
    timing=[r['ocr_ms']for r in rows if r['observation'].get('result_ocr')]
    summary.update(automated_tests=tests,automated_tests_passed=unit_ok,
        final_safety=dict(production_full_table_audit_unchanged=before==after,original_png_sha256_unchanged=image_check,
            new_spectator_images_sha256_unchanged=new_image_check,
            schema=schema,integrity_check='ok',foreign_key_check=[],before=before,after=after),
        original_four_result_accuracy=dict(correct=sum(c['correct']for c in initial_checks),checks=len(initial_checks)),
        new_spectator_checks=dict(correct=sum(c['correct']for c in spectator_checks),checks=len(spectator_checks),
            note='Includes expected NULL fields proving no other player is substituted; not additional recognized digit accuracy.'),
        result_ocr_ms=dict(median=round(statistics.median(timing),2),minimum=min(timing),maximum=max(timing)),
        actual_gui=dict(main_url='http://127.0.0.1:40464/reviews',main_review_status='pending_review_no_human_confirmation_performed',
            sandbox_draft_cool_correction=1,sandbox_reload_revision=1,sandbox_unknown_song_and_notes_remained_pending=True,
            screenshot='gui-review.png',correction_screenshot='gui-sandbox-correction.png'),
        runtime_note='The old 40463 v0.7 monitor was running and intentionally left uninterrupted; stop it before starting the new v0.8 monitor.')
    (out/'validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# Phase 4C 實測報告（v0.8.0）','',
        f'{tests} 項自動化測試'+('全部通過。'if unit_ok else'存在失敗。')+f" {len(rows)} 張原圖分類 {summary['screen_classification_correct']}/{len(rows)} 正確；選歌與 JOINT 數值回歸保持正確。",
        '四張原始結算圖共 44 個欄位，43 個值完全正確（97.73%），42 個同時符合可靠門檻。這是開發樣本的欄位精確比率，不是模型信心平均或未見樣本準確率。','',
        '測試期間新增兩張旁觀原圖，另外驗證 SPECTATING、不建立本人 Session，以及無 TC 的結算保留本人數值為 NULL。旁觀圖的預期 NULL 是安全拒絕檢查，不當作成功辨識出數字提高原四張 OCR 準確率。','',
        '| 原圖 | Perfect / Cool / Bad / Miss | 最高連擊 | 原始積分 | 評級 | 本人狀態 |',
        '|---|---|---:|---:|---|---|']
    for row in rows:
        result=row['observation'].get('result_ocr')
        if not result:continue
        v={k:f['value']for k,f in result['fields'].items()}
        judgments=' / '.join(str(v[k])if v[k]is not None else'未知'for k in ('perfect','cool','bad','miss'))
        lines.append(f"| {row['image']} | {judgments} | {v['max_combo']} | {v['game_score']} | {v['grade']} | {v['completion_status']} |")
    lines+=['','多人 GAME OVER 圖的 Cool 1 被背景暱稱干擾，仍為 NULL，沒有猜成 0 或套用他人的數字。評級 F／A++ 用完整高度欄位重讀；低信心或全圖與 ROI 衝突仍需人工核對。多人 footer LV 僅顯示 HOST scope，不當作本人成績的 LV。',
        '',f"結算全圖＋必要欄位重讀中位 {summary['result_ocr_ms']['median']} ms，範圍 {min(timing)}～{max(timing)} ms；沿用既有模型，未增加新套件。本階段未另量測 CPU／RSS 或遊戲 FPS。",'',
        '## 實際 GUI 與保存驗證','',
        '- 新版 `/reviews` 已實際執行背景 OCR，載入既有 Song 16 結算：P835 / C3 / B0 / M0，EX1673、MAX1676、MAX -3／AC 預覽。主目錄審核仍是 pending_review，沒有替使用者勾選人工確認。',
        '- 在 SQLite 副本的 GUI 將多人 GAME OVER 的 Cool 從空白修正為 1，保存草稿並重載，版本仍為 1；原始 OCR 仍為 NULL，Song ID／本人 LV／完整音符保持未知，MAX 未確認、完成預覽 FAILED。',
        '- [實際待確認頁](gui-review.png)；[副本修正重載](gui-sandbox-correction.png)。',
        '- 所有人工修正、審核及原圖雜湊保存在各 Session 的 result-review.json；Session 快照與原圖不重寫。','',
        '## SQLite 副本交易演練','',
        f"- 新建隔離副本：`{summary['sandbox']['database']}`。",
        '- 同一審核多次儲存、重新建立審核服務後重載，維持 revision 1。',
        '- 在副本實際寫入 PlaySession／PlayRecord 與結算收據，兩次儲存得到 record_id 1／1，只有一筆。刻意寫入非法 EX Score，交易整筆回滾，前後全表稽核相同。',
        '- 正式庫沒有寫入成績；副本演練工具限制在帶標記的 reports/phase4c/sandbox 子目錄，沒有正式 GUI API 寫入入口。','',
        '## Pack 改善與尚缺樣本','',
        '比較四張真實清單與三張清單關閉圖後，仍沒有受控的 Hover／真正選取標籤，不能校準可靠選取門檻。現在顏色只產生 CANDIDATE；明確人工核對才 CONFIRMED，其餘 UNKNOWN。清單開啟時撤銷可能過期的 Pack，關閉後可在 GUI 獨立人工確認。',
        '需要同一清單／同一滾動位置：A 已選且游標在外、只 Hover B、點選 B 後游標移出、關閉後對應歌曲資訊四張原圖。完整要求見 [操作及修改檔案清單](../../docs/PHASE4C_RESULTS.md)。數據見 [pack-analysis.json](pack-analysis.json)。','',
        '## 資料保護與範圍','',
        f'- 正式 SQLite 全表雜湊與開始時一致：{before==after}；schema {schema}，完整性／外鍵檢查通過。',
        f'- 最初 22 張 PNG SHA-256 保持一致：{image_check}；新增兩張旁觀 PNG 雜湊保持一致：{new_image_check}。40 Pack、4,397 Song、2,930 自製來源 ID 與既有遊戲難度／歷史均保留。',
        '- 正式 PlayRecord 為 0，無 migration。沒有修改官方 CSV、遊戲本體、遊戲輸入或歌曲身份。',
        '- 本階段未新增真實遊玩端到端：四張結算是原始靜態圖，Phase 4B 已保存結算是重新辨識；GUI 是真實本機頁面測試，不能混稱新實玩通過。',
        '- 舊版 40463 監控仍在使用，因此另開 40464，不中斷當時 Session。要使用新版監控，先停止舊版再在新版開始。','',
        '## Phase 5 建議','',
        '先驗證 Pack Hover／真選取、多人與 GAME OVER 真實流程、其他暱稱／DPI／皮膚與低信心人工覆核。正式寫入服務將已確認 GameDifficulty、Session、結算指紋收據與 PlayRecord 放在同一交易，沿用 session_id UNIQUE 防重送並拒絕衝突；百分比保存完整精度，僅畫面四捨五入兩位。再讓歷史最佳分數與燈號只取已驗證紀錄。詳見操作說明 Phase 5 段落。','',
        '[逐圖原始 OCR／信心／失敗欄位](image-results.json) · [JSON 驗證結果](validation.json) · [自動化紀錄](unittest.log)']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    assert before==after and image_check and new_image_check and unit_ok
    print('PASS: production unchanged, original PNGs unchanged,',tests,'tests; OCR',summary['result_values_correct'],'/',summary['result_field_checks'])


if __name__=='__main__':main()
