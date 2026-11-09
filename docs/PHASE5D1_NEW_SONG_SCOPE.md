# Phase 5D 補充：只列本次新增歌曲

版本 0.13.1，SQLite Schema 9 不變。沿用現有全 SONG scanner、背景工作、正式提交、匹配與 Recent Update；沒有建立新掃描系統。

## 本次修正

舊版把「檔案不在 song_sources」也算進變動，因此原本在硬碟中的舊來源，可能在掃描時帶入大量 Barry 等歌曲。現在取消這個候選條件。

每個 NO MAP episode 保存檔案 metadata 基準、觀測時的歌曲 ID，以及第一次背景掃描前的歌曲 ID／來源摘要。重試沿用同一份掃描前 ID 基準，不把前次已匯入的新歌漏掉。

本次新歌必須同時符合：

1. Song ID 在這個 episode 的掃描前基準中不存在。
2. 來源檔案在 NO MAP 快照後有新增或變動的觀測證據。
3. 檔案已穩定、已匯入歌曲庫；候選不在本次延後處理的資料夾中。

三類資料分開記錄：

- `candidates`：具備上述證據的本次新 Song ID。
- `existing_updates`：既有 Song 的來源補齊、內容更新或已觀測的變動，不能冒充新歌。
- `uncertain_imports`：新匯入資料庫，但來源在快照前已存在，無法證實屬於這次下載；不計入本次新增。

已有 Song ID 的來源更新仍可供既有嚴格身份匹配使用，不列為新下載。Pack、名稱、難度、LV、完整音符、時間、唯一性與所有自動提交條件均維持。新歌清單的歌名篩選只用於找相關項目，不等於確認 Song ID。

若本輪沒有可觀測的新來源變動，顯示無法確定是否為本次下載。無關的舊壞檔不會讓相同 metadata 不斷觸發全庫重掃；後續真正的檔案變動仍會觸發下一輪。

## GUI

監控頁與 Pending Review 的 NO MAP 區域預設只顯示：本人 NO MAP、下載觀測／追蹤、掃描狀態、新歌數、匹配歌曲、是否待人工確認、是否已正式儲存。

NO MAP 不等於正在下載；若未觀測到可靠進度，不宣稱下載完成。正式儲存狀態由實際 PlayRecord 查詢，不能只根據掃描成功顯示已儲存。

Pending Review 只預設提供與當次歌曲名稱／已確認別名相關的新歌，候選詳情收合。沒有相關項目時，保留待確認並提示可使用既有全歌曲庫搜尋。人工搜尋仍能找到所有 Pack；已選的人工 Song ID 不會因背景候選更新而被替換。原有成績核對表單保留。

舊版下載證據沒有足夠的新增歸因資訊，顯示為無法確定，不再把舊清單全部展開；原始 sidecar 與已提交證據不改寫。

## 日誌與資料安全

完整 episode／來源更新／未明匯入仍保存在 `data/monitor/downloads/<episode>/evidence.json` 及 Session sidecar。新增 `catalog-before-observation.json`、`catalog-before-scan.json`、`sources-before-scan.json`。每次 scanner 的完整報告另存為 `scan-<scan_id>.json`，同時沿用 SQLite 的 scan_runs.report_json。

一般 GUI/API 使用精簡投影，不把完整掃描名單、來源路徑、檔案 metadata 送到 NO MAP 區塊。開發時可直接查看上述日誌。沒有 Schema migration，不重算或覆寫既有成績。

Recent Update 的程式及規則未變。新增、來源更新與掃描本身均不能觸發 My Best 刷新。

## 驗證

隔離資料庫測試包含：35 首 Barry 舊歌來源補齊不計為新歌、快照前已完成的匯入標示未知、connect／新 Pack、既有來源更新、不同歌名的新歌不混入當次人工候選、查無候選時不自動展開全庫、重試維持新增 ID、舊版大量診斷不展開、無新變動不重複掃描，以及 Recent Update 不變。

實際 GUI 使用正式歌曲庫副本與人工 SONG 測試樹：20 首舊 Barry 來源補齊、1 首快照前的未登錄歌曲、2 首快照後新增歌曲。摘要顯示新增 2 首，Pending Review 只提供相關的 1 首；全庫搜尋仍可查到 20 首舊 Barry。這是隔離回放，並非真實新多人下載驗收。

測試結果與畫面位於 `reports/phase5d1/`。測試未向正式庫寫入成績。修改檔案：download_tracking.py、library_service.py、result_review.py、gui.py、monitor.html/js、reviews.html/js、style.css、__init__.py；新增篩選測試及隔離 GUI 工具。原測試中兩項「既有來源補齊」的預期已改為另外記錄，原有安全匹配與提交測試保留。
