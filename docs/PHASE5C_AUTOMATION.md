# Phase 5C · 安全自動儲存與音符差異人工審核

版本 **0.11.0**／SQLite **schema 7**。沿用現有 OCR、監控、Pending Review、`SubmissionService`、備份與 Phase 5B 動畫。沒有第二套 PlayRecord 寫入流程，沒有修改遊戲檔案、讀取記憶體或控制遊戲輸入。

## 啟動與模式

啟動 `LibraryGUI.cmd` 或 `MonitorGUI.cmd`，進入「待確認成績」。本次另開的新版介面為 **http://127.0.0.1:40478/reviews**。

預設 **MANUAL**。要啟用 **VERIFIED_AUTO**，在頁首先選模式、勾選「我確認啟用安全自動儲存」，按「套用儲存模式」。停用時選 MANUAL 後套用即可。設定保存於目前監控目錄的 `storage-mode.json`，綁定正式資料庫路徑，跨重啟保留；不同新版程式視窗會重新讀取共用設定，停用會在提交前及備份後再次檢查。

**啟用時已存在的所有 Session 會排除**，包括尚未結算的既有 Session。因此先在等待室啟用，再開始新的一首。舊 Pending Review 仍由人工提交，不能靠「重試自動儲存」繞過。重試只適用啟用後的新 Session，適合暫時備份／寫入失敗；已人工編輯的草稿不轉成自動證據。

正式庫目前保持 MANUAL；本階段沒有由開發測試啟用正式自動模式。舊程式不會熱更新 Python 模組，請在歌曲結束並確認 Session 已保存後，正常停止舊服務，再以啟動檔開啟新版；不要同時在多個監控實例擷取同一局遊戲。

## 人工差異確認操作

以 Heart of Witch 為例：

1. 選擇該 Session，查看原始結算截圖與 OCR 信心度。保留 P1145／C30／B7／M10，不調成 1194。
2. 若尚未鎖定身份，選 HARD、確認本次是 Official K，再按「核對來源並填入已知資料」。可靠且唯一時帶入 Song 4343／LV29／1194。
3. 核對本人成績列、Song ID 與難度，確認完整音符來源。差異區會分開列出選歌值、資料庫值、原始及目前判定合計、經驗證完整數，並保留條件式預覽。
4. 在「音符差異人工審核」選來源，分別勾選四項：原圖 P/C/B/M、Song ID／難度、完整數來源、已注意差異。備註選填；不需要宣稱知道原因。
5. 勾選整體核對，按「確認資料（另存）」，再按「確認並儲存成績」核對摘要及正式提交。

這會以經確認的 1194 計算 EX2320／MAX2388／MAX -68，正常完成且有 Bad／Miss 可保存 CLEAR。記錄明確標示「判定合計差異已人工確認」，差異為 **judged_total − verified_total_notes = -2**。這個負號表示少兩個判定，不是扣除完整音符數。

本案例仍沒有當時的選歌截圖，`selected_total_notes` 保持 NULL；使用者目視敘述不冒充 OCR 證據。CSV 的 1194 與結算的 1192 均保留，**差異原因仍未知**。

四項確認綁定 Session、Review、截圖雜湊、Song ID／路徑、本人列、判定與譜面數值。修改數字、歌曲、本人列或來源資料後需要重新核對；不能把另一局的確認 token 直接重用。原始 OCR、修正值、備註、來源、差異及人工確認時間均另存，不覆寫原證據。

## 燈號待確認及未知 MAX

| 情況 | 正式紀錄／燈號 |
|---|---|
| 正常完成、無差異、資料完整 | 沿用 ACP／AC／CLEAR |
| 人工確認差異，Bad 或 Miss > 0 | CLEAR |
| 人工確認差異，Bad = Miss = 0，AC／ACP 證據不足 | 分數可保存，燈號為 NULL，GUI 顯示 PENDING |
| 正常完成，但完整音符數不可信 | 保留 Pending Review，不放寬完整成績約束 |
| GAME OVER、完整數未知 | FAILED，保存部分判定與 EX；MAX／百分比 NULL |

PENDING 是介面的明確待確認文字，**沒有加入虛構的遊戲燈號值**。使用既有 `lamp_status=NULL`、`validation_status=pending`，且只允許有完整人工差異審核的正式紀錄使用這條路徑。可信完整數與判定可使該分數參與最高 EX，但 NULL 不參與燈號排名，因此既有最佳燈號不下降、也不會錯升 AC／ACP。

本版不提供強制 AC／ACP 按鈕；若要解除待確認，需要之後建立可信的額外證據與審核規則。不能僅因畫面 Bad／Miss 為 0 就猜測全判定已完成，也不能更改不可覆寫的舊 Record。

## 自動儲存安全條件

背景工作只接收新結算的 `ResultReviews.ingest` 通知，不在 OCR／GUI 執行緒做備份。自動請求重新檢查原始 Session、截圖指紋、結算畫面 hash、時區與時間順序、來源、唯一身份、難度／LV／完整數、完成狀態及本人列。

- Official K 重用既有來源確認及 `match_official`；不是依歌名或候選排序決定身份。
- Custom 必須有確認 Pack，完整比對歌名、難度、LV、完整數、時間及現有人工對應；保留 Pack／SongFolder／Song ID。無 Pack 的多人仍可搜尋整個 Custom 庫，但需人工確認，不猜下載位置。
- 所有必要 OCR 欄位、本人成績列及選歌必填資料採保守 **0.95** 門檻，且不能有 NULL、原因警告或來源衝突。Official 的可選選歌欄位亦須符合既有 matcher 的可靠性檢查。
- 正常完成必須判定合計一致。有差異即待確認，任何人工例外都不會進入 VERIFIED_AUTO。
- 多人必須唯一識別要求的本人暱稱與資料列；不把 HOST 或第一名當作本人。單人結算 LV 也要與選歌一致。
- 已人工編輯的 Review 不自動提交；可重試暫時錯誤，但仍完整重驗。

自動與人工都走同一個 `SubmissionService.submit`，以同一交易鎖、pre-submit 備份、摘要 token、備份後重驗及 Session／Review 唯一限制保存。確認方法分為 `manual_review`、`manual_note_exception`、`verified_auto`；自動證據不偽裝成人工核對。相同 Session 重送回原紀錄，不新增第二筆；不同 Session 同分仍各保存。

## 時間來源

新 Record 的 `played_at` 優先採用 **Session.ended_at**，其次是結算內明確的 `captured_at`／`observed_at`。必須為有時區的時間字串；不再拿 `started_at` 或正式提交時間填補。`confirmed_at` 為提交交易的 UTC 時間，歷史與提交摘要轉為瀏覽器本地時區顯示，包含時區標示。

缺少可靠結算時間時禁止正式提交，保留待確認。舊 Record 不會回填或改寫；沒有已保存可信時間來源的舊紀錄會標示時間未知，而非把舊值重新宣稱成已驗證結束時間。

## Schema 6→7

沒有新增資料表、欄位或第二套最高分資料。差異審核重用 Review／`note_sources` 與 `evidence_json.note_exception`，模式設定重用監控目錄的設定檔。

只更換兩個 formal validation trigger 與 `difficulty_bests` view，容許具備完整證據的人工差異例外及可信自動提交。保留原始 NOT NULL、數字、EX／MAX、外鍵、唯一鍵與正式紀錄不可更新／刪除限制。PENDING 分數的排名由明確的人工例外證據限定；一般未審核資料仍不參與。

遷移先以 `BEGIN IMMEDIATE` 鎖定、驗證並建立 `pre-v7` 備份；逐表內容遷移前後一致才提交，任何失敗回滾。實際備份與稽核見 `reports/phase5c/production-migration.json`。

正式庫在開發期間由另一個使用中的程式完成了遷移、掃描及四筆人工提交，與隔離測試 Session 不同。因此沒有把正式庫還原至開工快照，也不宣稱它整天未變。最終核對保留開工的所有 Pack／Song／來源／難度 ID，原始截圖相同；遷移備份驗證通過，既有及新人工成績保持完整。

## Phase 5B 整合

成就事件仍在正式交易內比較與保存。只有提交成功後，人工頁面或自動事件輪詢才呼叫既有 DanceFX。NEW SCORE、NEW LAMP、首次成就及 SAVED 沿用原動畫與減少動畫偏好；差異紀錄附加標記，PENDING 不播放燈號升級。

同網站的跨分頁通知以資料庫／Session／Review／Record 身份及 localStorage 防重播，背景分頁不搶播。重新整理、新開歷史頁、同一提交重送及後端重啟不會重新當作刷新成績。待確認、失敗與只保存草稿都不播放成功動畫。

## 測試與實際 GUI

**278 項自動化全部通過**（原 244＋新 34），完整約 43.79 秒，沒有略過。既有測試只更新最新 Schema 期待值及模擬 Session 的明確結束時間，保留原有驗證。新增差異四項核對、跨 Session 拒絕、PENDING、不可信 MAX、Official／Custom 匹配、同名、防重送、多人才本人、低信心、併發設定停用、寫入／遷移故障回滾及時間分離測試。四份 JS 通過語法檢查。

三張真實保存的 Heart／Amnolys／Cagayake 原圖重新執行 OCR，P/C/B/M 與已知值 **12／12 相同**；不是全畫面 OCR 準確率或未知圖片的泛化保證。Cagayake 的原始評級仍低信心，不予自動放行。

完整正式庫副本的實際 GUI 操作：

1. Heart 原圖與原始值，四項確認後保存 MAX -68／CLEAR，保留差異、CSV 來源與原始 OCR。
2. Amnolys 真實 OCR＋**控制實驗用 Pack／人工對應**，透過正常 ingest 通知自動保存 EX2461／MAX -45／CLEAR，顯示 NEW SCORE／NEW LAMP。
3. 明確標示的合成全 Perfect 差異測試，保存分數 EX2502，但燈號 PENDING，只有 NEW SCORE；最佳燈號仍是另一筆 CLEAR。
4. Cagayake 真實低信心資料保留待確認；另一個模擬 Session 同分保存並只顯示 SAVED。
5. 重啟保留模式、成績及差異證據；新開頁不重播自動成就，歷史顯示本地時間及獨立最佳。

隔離副本最終四筆 Record；這些測試 ID 不存在正式庫。**上述是原圖回放、模擬 Session、實際 GUI 操作，不是新一次真實遊玩端到端驗證。** 原始 Amnolys Pack 來源是 UNKNOWN，實驗用明確對應只存在副本，不宣稱現實自動 Pack 已改善。

測試記錄在 `reports/phase5c/unittest.log`、`real-ocr.json`、`gui-checks.json`、`validation.json` 與截圖。驗證時正式庫 42 Pack／4423 Song／2992 檔案來源／2538 官方來源／7615 難度／4 筆人工正式紀錄；統計是當時快照，之後使用者遊玩新增仍需保留。沒有由本次開發測試代替使用者正式提交。

## 新一次真實遊玩驗證步驟

1. 在歌曲結束並保存 Session 後停止舊服務，啟動本版，只開一個監控實例。
2. 先保持 MANUAL，查看既有四筆歷史並確認遊玩日期、分數與燈號。
3. 在等待室明確啟用 VERIFIED_AUTO，再進入新歌曲。Official K 選歌來源、難度與 LV 要可靠；Custom 要先有可信 Pack 與完整已確認對應。
4. 自行遊玩一首，結算停留約十秒，不輸入成績。觀察自動成功或具體待確認原因，核對 Session、原圖、本人列及正式歷史。
5. 相同結算停留及重整，應只有一筆 Record，沒有重播成就。切回 MANUAL 確認停止自動提交。
6. 另以真正存在的音符差異原圖，使用四項核對流程；不能為測試而造判定。多人、GAME OVER、失焦／關閉、重啟後自動提交也需分別實玩驗證。

不確定 Pack、低信心評級、未知結算時間、沒有完整數的正常完成、差異 AC／ACP 的額外證據仍是已知保守限制。沒有新量測 CPU／RSS／FPS。

## 修改檔案

- `dance_results/note_review.py`：當次差異授權與可靠結算時間。
- `dance_results/note_evidence.py`、`scoring.py`、`result_review.py`：來源分離、人工差異預覽／另存、PENDING、ingest 通知。
- `dance_results/verified_auto.py`：可靠性檢查、持久模式、背景佇列及跨程式停用。
- `dance_results/submissions.py`、`achievements.py`：共用提交、時間／證據、NULL 燈號與獨立最佳。
- `dance_results/migrations_v7.py`、`migrations.py`、`database.py`：安全最小 migration。
- `dance_results/gui.py`：模式／重試 API 與服務生命週期。
- `dance_results/gui_assets/reviews.html`、`reviews.js`、`reviews.css`、`history.js`、`lamps.js`、`lamps.css`：差異操作、模式控制、時間、成就整合與跨頁防重播。
- `tests/test_phase5c.py`：34 項新測試；`test_result_review.py`、`test_submissions.py`、`test_connect_final.py`、`test_ocr_static.py`、`test_official_import.py`、`test_phase25.py`：既有測試的時間／Schema 期待更新。
- `tools/phase5c_gui.py`、`validate_phase5c.py`：隔離 GUI 實驗與唯讀稽核。
- `dance_results/__init__.py`、`README.md`、`docs/VALIDATION.md`、本文件：版本及操作紀錄。
