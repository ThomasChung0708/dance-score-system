# Phase 5C.2：結算判定自訂 MAX

版本 **0.12.0 / SQLite Schema 9**。GUI、OCR、監控、備份、交易、冪等與 Phase 5B 動畫沿用現有架構。

## 新舊計分差異

| | Phase 5C.1 | Phase 5C.2 正常完成 |
| --- | --- | --- |
| EX | Perfect × 2 + Cool | 相同 |
| 主要 MAX | 另行驗證遊戲 MAX 定義，否則 NULL | **本系統自訂 MAX = (P+C+B+M) × 2** |
| CSV 與判定不同 | 人工四項差異確認，MAX 仍可能未知 | 顯示差異，不單獨阻擋人工提交 |
| MAX 文字證據 | 另行驗證需要文字說明 | 正常完成不需要；GAME OVER 的獨立完整基礎才需要 |
| AC / ACP | 需確認完整判定範圍 | 依已確認的當次結算判定，明確標示為本系統燈號 |

正常完成、身分／難度與本人列可靠、四項判定有效且 OCR 可信或已人工核對原圖，才可正式提交。預覽不取代確認。NULL 不補 0，低信心原始證據不被覆蓋；評級或其他必要欄位缺失仍須修正。`judged_total=0` 不進行除法，不能產生虛構百分比或 ACP。

EX 與 difference 使用整數，百分比顯示使用 Decimal 四捨五入至兩位。差值 0～150 顯示 MAX -差值，大於150顯示百分比。這是本系統的自訂計分制度，**不是對官方原生 MAX 定義的結論**。

Heart of Witch：P1145/C30/B7/M10 → judged1192、EX2320、自訂 MAX2384、MAX -64。CSV1194保留。1015顆、P1005/C10/B0/M0 → EX2020、MAX2030、MAX -10。

## 三種音符數與來源

- `catalog_total_notes`：GameDifficulty 既有官方／人工參考數，以及每筆 evidence_json.note_sources 快照。結算合計不回寫官方音符數。
- `judged_total`：當次已確認 P/C/B/M 的合計；原始 OCR 與人工修正分開保留。
- `max_basis_notes`：正常完成取當次 judged_total；快照至 Record.total_notes 與 evidence_json.note_sources.max_basis_notes。既有 enum `notes_source=completed_judgment_sum` 可直接使用。
- `evidence_json.scoring_version=custom-judged-v1`、`max_basis_verification.source=completed_result_judged_total`，並保存 `is_official_game_max=false`。
- `lamp_verification.source=system_result_judgments`，`independent_game_lamp_verified=false`。GAME OVER 使用其遊戲失敗證據，與獨立遊戲 AC／ACP 證明分開。
- 保存當次 `comparison` 判斷及跨場次觀測來源；歷史 API 另提供目前比較狀態，不覆寫提交時的證據。

## GAME OVER

保留 P/C/B/M 與 EX，燈號 FAILED。不把部分 judged_total 當完整 MAX，也不把部分數量加入正常完成的跨場次範圍。沒有獨立可信完整基礎時 MAX／百分比保持 NULL；可選的獨立基礎輸入只在 GAME OVER 顯示。FAILED 不刷新正常完成最高分或最佳燈號。

## 跨場次衝突與歷史相容

以 Song ID＋EASY/NORMAL/HARD 分開檢查，優先讀不可改寫的正式完成紀錄；已保存的人工核對及可靠鎖定身分的高信心結算，也可阻止自動提交。同一 Session 不重複計數，不將不同 SongFolder 或來源合併。

同譜面出現不同 judged_total 時：

1. 本筆仍保存自己的判定與自訂 MAX，人工可以確認並保存。
2. 本筆燈號 PENDING，不能提升 AC、ACP 或 CLEAR 最佳燈號。
3. 停止 VERIFIED_AUTO，待人工檢查。
4. 不刷新最佳 EX，不發 NEW SCORE／NEW LAMP／FIRST AC／FIRST ACP。僅顯示 SAVED 與衝突說明。
5. 最佳查詢保留先前可比較的成績，標示比較暫停，隱藏排名用的 MAX 差值／百分比；歷史各筆仍可查看自己的 MAX 與差值，附「不作排名」提示。
6. 回到其中一個舊合計不會自行解除衝突。此版沒有自動裁決或清除歷史差異的按鈕；需取得可信規則後再設計具稽核的解決流程。

舊制與自訂制度分組顯示，保留舊制最佳 EX 與燈號，不混合百分比或 MAX 差值。首次建立自訂組且已有舊制正常紀錄時，僅 SAVED，不宣稱打破舊紀錄；之後在同一新制範圍內才判斷刷新。首次新制沒有任何舊正常紀錄時，仍可正常 FIRST CLEAR／AC／ACP 與 NEW SCORE／LAMP。歷史 Record 的 MAX、燈號、證據和提交時事件均不重算。

## Pending Review 操作

1. 開啟 `LibraryGUI.cmd` 或 `MonitorGUI.cmd` → 待確認成績。
2. 選結算，安全帶入官方 K 或已確認的 Custom 身分；核對 Song ID、SongFolder、難度、歌曲 LV 與本人成績列。
3. 確認 P/C/B/M、Combo、原始積分、評級及正常完成／FAILED。畫面自動顯示 judged_total、EX、自訂 MAX、差值／百分比和 CSV 參考數。
4. 一般正常完成不填獨立 MAX 文字，也不需四項 CSV 差異例外勾選；參考音符數確認是選填。
5. 勾選整體核對，按「確認資料（另存）」；再按「確認並儲存成績」檢查摘要，最後正式儲存。低信心欄位需先目視核對，燈號才可作已確認預覽。

原始圖、OCR 值／信心度、修正值及人工確認依據分開保存。重整、重啟或重送仍只有同一 Session／Review 的一筆成績，成功動畫不重播。

## 自動模式

MANUAL／VERIFIED_AUTO 與啟用範圍不變，不自動提交啟用前的舊 Pending Review。可信自動條件保留原先的唯一身分、難度／LV、可信 Pack（Custom）、本人列、完成狀態、必要欄位、原圖／Session 及日期驗證，並新增跨場次判定範圍檢查。

**CSV 與判定不同的新自動規則尚未完成實玩驗收，此版先不自動送出這類紀錄。** 顯示 `automatic_custom_difference_policy_pending_live_validation`，不是將差異宣稱為 OCR 錯誤；人工提交已完全不依此相等條件。這是本次採用的保守上線保護，不會從一筆人工例外自動學成通用規則。

## Schema 8→9

必要原因：Schema8 的 AC／ACP 與差異提交 trigger 仍要求獨立 MAX／人工差異授權，無法接受本次新制度；最佳 view 也會混用制度。

9 僅替換兩個正式驗證 trigger 與 difficulty_bests view，**沒有新欄位／表、沒有資料列遷移或歷史回算**。舊規則仍用於既存獨立 MAX／FAILED 相容驗證。正式 GUI 固定使用新制度；保留顯式歷史策略供舊制預覽／回歸測試，不接受 HTTP 表單切回舊制。

遷移先取得交易鎖、完整性／外鍵檢查與可核對備份；要求逐表內容雜湊及舊 bests 不變才 commit，失敗 rollback。提交同樣先備份、重新驗證 token 與比較狀態、SQLite 唯一鍵／trigger 防重及防偽造。比較 token 對數值衝突敏感，同一範圍的重複證據不造成無意義的並行提交失敗。

## 驗證範圍與產物

317 項自動化測試通過，包括保留的298項回歸與19項新預設制度測試。與規格相反的舊 MAX 測試以顯式歷史策略保留，而非刪除；Schema 期待更新為9。新測試涵蓋預設新制度、來源差異、各燈號、FAILED、低信心／NULL、跨場次衝突、分組、偽造資料、冪等、交易／migration回滾及非空舊紀錄保留。

三張原始 PNG 重新 OCR，12/12個 P/C/B/M 欄位符合既有人工已知值，沒有改圖或抬高低信心值。這不是全域 OCR 準確率。

隔離完整歌曲庫的實際 GUI：Heart自訂MAX-64、Cagayake人工核對後AC/MAX-3、Amnolys可信自動提交及新組SAVED、人工修正造成1253/1255衝突後PENDING/SAVED、不刷新最佳。衝突例的P1214明確是隔離控制輸入，原始OCR1212仍保留，不宣稱是新一次真實遊玩。

原始 Amnolys Pack 在真實 Session 不可靠；本次回放用可信身分固定於副本，不宣稱已解決真實自動 Pack。新一輪真實單人／多人、GAME OVER、長時間監控、跨場次合計穩定性及CSV差異自動上線仍待實玩；不控制遊戲輸入，沒有新增CPU／記憶體量測。

`reports/phase5c2/`：before.json、baseline.sqlite3、production-migration.json、unittest.log、real-ocr.json、gui-checks.json、validation.json、report.md／report.html、gui/截圖。測試正式紀錄只在 gui/PHASE5C1-TEST.sqlite3（沿用隔離工具檔名，以 manifest 為準），不能當正式庫。

## 修改檔案

- 核心：scoring.py、score_comparison.py（新）、result_review.py、submissions.py、achievements.py、verified_auto.py。
- Schema：migrations.py、migrations_v9.py（新）、database.py。
- GUI：reviews.html／reviews.js、history.html／history.js、lamps.js；保留原CSS動畫。
- 測試：test_phase5c2.py（新）；既有舊策略fixture／核心相容、Schema期待與trigger訊息斷言。
- 工具：phase5c2_gui.py、phase5c2_real_ocr.py、validate_phase5c2.py；版本、README、VALIDATION及本文件。
