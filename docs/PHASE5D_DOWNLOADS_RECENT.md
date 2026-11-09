# Phase 5D：NO MAP 追蹤與 Recent Update

版本 **0.13.0 / Schema 9**，2026-10-26。沿用現有 Python、SQLite、HTML/JS、OCR、監控狀態機及正式提交服務。沒有新增資料表、索引或 migration。

## 操作

1. 執行 `MonitorGUI.cmd`，或 `LibraryGUI.cmd` → 即時監控。輸入目前本人暱稱，開始監控；本次交付的本機服務為 http://127.0.0.1:40490/ 。正式庫維持 MANUAL，未啟動正式監控。
2. 本人 NO MAP 連續觀測可靠時，保存原始畫面、本人位置、信心度及首次時間。監控頁直接顯示下載追蹤狀態；展開「效能與診斷資料」查看事件與快照耗時。
3. 進入 PLAYING 後，由獨立工作執行全 SONG 增量掃描。connect、新 Pack、既有 Pack、新 SongFolder、既有資料夾補上的來源都在範圍內。
4. 身份不足時，到「待確認成績」查看可閱讀的下載候選：Song ID、Title、Artist、Pack、完整相對路徑、來源檔案、符合／缺少／衝突條件及觀測時間。手動選定正確歌曲及難度，核對本人成績列，再走既有另存與正式提交流程。
5. 檔案尚未完成時先保留結算原圖與 Pending Review。背景候選完成後頁面會更新候選，不改寫已輸入數字或自行替換人工 Song ID。也可選 SONG 根目錄，按「重新掃描 SONG 並更新候選」；重啟後仍可載入下載 sidecar 與 baseline 重試。
6. 歌曲庫左側「Recent Update」顯示最新 30 個可驗證的 My Best 更新組合。每列只有該次難度；點列開啟對應 Song ID、難度及 Record 詳情。列表不使用搜尋或名稱排序，固定依刷新遊玩時間排序。

## 觀測與背景工作

新增事件：MULTIPLAYER_LOBBY、SELF_NO_MAP、SELF_READY、SELF_DOWNLOAD_PROGRESS_OBSERVED、DOWNLOAD_CANDIDATES_FOUND、DOWNLOAD_SCAN_COMPLETED。保留原 WAITING_ROOM 狀態，不另建互相競爭的狀態機。

本人暱稱必須唯一且高信心；NO MAP／READY 必須落在該暱稱的同一卡片範圍。HOST 或其他人的標籤不會移到本人。進度條僅記錄「觀測到」，百分比維持 NULL；READY 不代表檔案完整。

監控開始先建立 armed metadata；NO MAP 再建立獨立 episode baseline。只讀檔案名稱、相對路徑、大小、mtime 與副檔名，不讀歌曲內容做全目錄雜湊。以 DirEntry 快取 metadata，略過連結與 reparse points。baseline 可能已晚於下載開始，此限制會保存；armed 快照若更早，可補充已開始的檔案變動證據。

預設背景檢查間隔 5 秒、quiet window 2 秒；不在每個 frame 掃描。與 GUI 共用 LibraryService 的寫入鎖及單一掃描工作，同根目錄穩定掃描可共用，其他工作等待排隊。最多 4 次自動掃描嘗試；結果後自動追蹤最多 120 秒，之後保留人工重試。手動重試不必重啟監控。

原 scanner 會延後正在變動、空白或名稱尚無法可靠解析的來源，SM+DWI 同資料夾仍為一首 Song。現在會回報延後的相對資料夾，避免無關舊壞檔擋住已完成的新候選；本次變動資料夾仍延後時不能確認。掃描失敗／忙碌不丟結算，既有 ID 與歷史保留。

資料保存於監控目錄 `downloads/<episode>/baseline.json`、`evidence.json`、`self-no-map.png`，以及 `sessions/<session>/download-evidence.json`。不修改原本鎖定的 Session 或原始 OCR。正式提交時把本次下載證據另存進既有不可覆寫的 PlayRecord evidence_json。

## 身份匹配與自動儲存

新增路徑、單一候選、同名或修改時間都只產生候選。自動確認仍須 Pack、名稱、EASY/NORMAL/HARD、遊戲 LV、完整音符數及遊戲時間全部可靠、唯一且無衝突。歌曲庫的 NULL 不算匹配；不從來源檔案解析遊戲 LV，不以結算合計補身份欄位。

已保存的可信譜面對應可重用。下載來源必須已匯入且整個 SongFolder metadata 在核對時仍一致。自動身份寫在下載證據，原始 Session 不倒填 Song ID。新譜若尚無可信 LV、時間、完整音符或 Pack，這版正常進入人工確認，無法承諾所有首次下載都完全自動。

MANUAL 絕不自動正式提交。VERIFIED_AUTO 由使用者啟用，只處理符合既有資格的新 Session：唯一身份、可靠 OCR、本人列、完成狀態、Session 與原圖、無跨場次合計衝突等。掃描較晚完成時會重新排入同一自動提交服務，仍重新驗證全部條件。CSV 與判定不同的新自動規則仍保留 Phase 5C.2 的實玩部署保護，轉人工處理。

多人底部 LV 是 HOST 譜面時，保留原 OCR 提示，不拿來覆寫本人的難度；該提示本身不再一律擋住所有多人成績。本人難度必須有獨立可靠來源。測試已確認 HOST 第一列、本人第二列時只儲存本人。

EX、自訂 MAX、GAME OVER、跨場次比較與燈號規則維持 Phase 5C.2。提交沿用備份、交易、冪等、不可覆寫機制；不自動提交舊待確認資料。監控頁亦接入既有 Phase 5B 成就元件，只播放實際提交成功的事件。

## Recent Update 的持久化證據

沿用已存在的 `PlayRecord.evidence_json.achievement_feedback`、scoring_version、played_at、difficulty_id 與判定資料，不另建事件表。

查詢重驗證正常完成資格、P×2+C、同制度／同 MAX 基準、不可比較衝突狀態、提交前 best、提交後 best、NEW_SCORE 事件及嚴格大於。前 best 為 NULL 才是 FIRST BEST；有效 0 分是已有紀錄。SAVED、FAILED、Pending、下載、掃描、燈號單獨提升、相同／較低 EX、跨制度新組與衝突都不製造刷新。

依 Song ID＋難度去重，只取該組最後一次合法刷新；依該正式紀錄的 played_at（核對原 Session 結束／結算時間）排序，同時間以 record_id 次排序，最多 30 組。缺時間或缺持久化刷新證據就不猜測。尚有完整基準衝突的難度暫不列入可比較的 Recent Update，不改寫舊紀錄。

正式庫原有 8 筆紀錄都已有可驗證的刷新證據，可原樣重建 8 列，保留原遊玩時間，不視為今天新增。Phase 5B 動畫與列表共用正式提交結果；頁面讀取與重新整理不重新發送成就。不同 Session 相同分數可保存歷史，但列表不移動。

## 驗證與限制

完整結果見 `reports/phase5d/report.html`、`validation.json`、`unittest.log`、`static-final/report.html`、`gui-checks.json`、`benchmark.json`。實際 GUI 截圖在 `reports/phase5d/gui/`；均為隔離副本，控制修正案例不能當成真實新遊玩。

本次 350 項測試全數通過，包含外部 SM／osu 原始樣本。33 項新測試覆蓋 NO MAP 本人列、metadata-only 弱觸發不捏造 NO MAP、背景穩定掃描、connect／其他 Pack／新 Pack、來源補齊、忙碌重試、結算先保存、重啟恢復、舊不完整來源隔離、可信多人自動提交與所有 Recent 分數規則。

27 張原圖重新執行實際模型：分類 27/27、可見選歌欄位 29/29、遮蔽欄位保留 NULL 6/6、本人結算狀態 5/5。新增兩張 NO MAP 原圖均識別 TC 為第三欄；badge ROI 信心約 .9176／.9169，READY 在其他玩家。沒有足夠清晰的本人綠條證據，沒有產生下載百分比。回歸也修正旁觀提示被 OCR 切成兩框時仍應是 SPECTATING，避免當成本人 PLAYING。

實際隔離 GUI 通過：候選不自行選定、官方來源人工選擇、FIRST BEST、EX 2320→2325 的 NEW SCORE +5（刻意測試修正）、列表仍為同難度一列、原遊玩時間排序、刷新不重播、服務重啟後可查 Record、Recent 空狀態，以及無遊戲視窗時開始／停止監控。沒有進行本次真實多人遊玩／下載或 GAME OVER 端到端驗收。

真實 SONG 只讀量測：21,702 檔、2,212 目錄；原 metadata 快照 3.06～3.78 秒，優化後三次約 0.500～0.566 秒，指紋一致、內容雜湊 0。全 SONG 掃描約 90.33 秒；隔離副本新增 23 Song、94 來源、2 Pack，18 來源延後，正式庫未匯入這些測試結果。掃描測試程序（不含 OCR 模型）平均 CPU 約 69.68% 單一邏輯核心、約 4.36% 的 16 核整機容量；峰值 RSS 114.46 MiB。無並行熱舞 FPS 量測，不保證實玩零影響。

仍需真實多人遊玩驗證：本人清晰綠色下載條、本人 READY、下載至 connect／其他新 Pack 的前後連續畫面、進入 PLAYING 時檔案仍變動、結算早於下載完成、本人非第一列、真正 GAME OVER、實際 FPS／資源，以及使用者啟用 VERIFIED_AUTO 後符合全部條件的新一場成績。可先以 MANUAL 操作，缺失資料仍留待確認。

## 修改檔案

- 核心新增：`download_tracking.py`、`lobby_download_ocr.py`、`recent_updates.py`。
- 監控／提交：`monitor.py`、`monitor_ocr.py`、`ocr_screens.py`、`result_review.py`、`verified_auto.py`、`submissions.py`、`gui.py`。
- 掃描回報：`models.py`、`scanner.py`、`library_service.py`；只補延後資料夾清單，資料庫結構不變。
- GUI：`app.js`、`history.js`、`monitor.html/js`、`reviews.html/js`；沿用既有 CSS／動畫。
- 版本與驗證：`__init__.py`、`tests/test_phase5d.py`、兩份 OCR fixture、`tools/phase5d_benchmark.py`、`tools/phase5d_gui.py`、`tools/validate_phase5d.py`、README、VALIDATION 與本文件。


## 搜尋不到視窗的啟動環境檢查

2026-10-26 實際排查：桌面已有 `dance v2.0.0-dev-50bc9`，但先前從受限制桌面環境啟動的服務連其他視窗也無法列舉，因此並非視窗標題規則不符。改從可存取使用者桌面的環境啟動同一服務後，已實際找到 1040×807 遊戲視窗並擷取、執行 OCR；維持 MANUAL。服務重啟後須重新整理舊頁面以取得新的工作階段。

平常請從檔案總管開啟 `MonitorGUI.cmd`。如果所有視窗清單都空白，先檢查啟動環境是否可存取互動桌面，而非直接放寬遊戲名稱篩選。這次驗證是實際視窗擷取／OCR，仍不代表完整多人下載端到端驗收。診斷與畫面在 `reports/phase5d/desktop-window-fix/`。
