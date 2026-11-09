# Phase 5A：正式提交與 IIDX 計分（0.9.0／SQLite schema 6）

沿用既有 Python／HTML／JS、OCR、Session 與 Pending Review。計分核心獨立為 scoring.py，預覽及正式提交共用；沒有新增套件，沒有自動提交既有結算。

## 操作與啟動

雙擊 MonitorGUI.cmd／LibraryGUI.cmd，或執行：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.gui
```

本輪新版服務為 `http://127.0.0.1:40468/`，待確認頁 `/reviews`，歷史頁 `/history`。已開啟的舊版 Python 服務不會載入新程式；請使用新版頁面。切換到新版監控前，先在舊頁面停止監控，再啟動新版，避免多個服務建立重複 Session。本輪沒有替使用者停止原有監控。

1. 在待確認頁選擇結算，核對原圖、Song ID、Pack／SongFolder、EASY／NORMAL／HARD、歌曲 LV 與本人成績列。
2. 修正 Perfect／Cool／Bad／Miss、最高連擊、原始積分、評級與遊玩完成狀態。未知保持空白；Cool 空白不能當作 0。
3. 正常完成須核對可靠的完整音符數及判定合計。FAILED 可保留未知完整音符，此時 MAX／差值／百分比都保持 NULL。
4. 勾選本人、歌曲身份與必要數字核對，先按「確認資料（另存）」。這一步只保存審核，不寫正式成績；草稿或尚未保存的修正不能正式提交。
5. 確認「本次遊玩模式」。只有必要條件通過，「確認並儲存成績」才可操作。按下後顯示完整摘要，包含歌曲、Pack、Song ID／SongFolder、難度／LV、本人、判定、連擊、完成狀態、EX、MAX 與文字燈號。
6. 在摘要明確勾選「我已核對以上歌曲與本人成績，確認正式儲存」，再按「正式儲存」。此時建立備份並執行資料庫交易。
7. 成功後顯示 Record ID 與「查看正式成績」。已提交項目不能再次建立成績；相同請求返回既有 Record，內容不同則拒絕覆寫。
8. 歷史頁可依 Song ID、難度及本人暱稱篩選，分頁查詢，查看原圖、確認資料與原始 OCR。指定 Song ID 時可查看該歌曲各難度的正常完成最高 EX 與最佳文字燈號。

不同 SongFolder／Official 身份仍各自保存。多人無 Pack 也可人工確認正確 Song ID；全 Custom 搜尋與重新掃描 SONG 保留。新譜面缺少已驗證 LV／音符／時間時，不能自動匹配。人工明確選定歌曲後，遊戲時間未知不阻擋本階段人工提交，但保留 NULL，不能因此建立自動匹配規則。

## 計分核心

`EX = Perfect × 2 + Cool`，Bad、Miss 各 0 分且中斷 Combo。EX、MAX、差值均為整數。已驗證完整音符才有 `MAX = total_notes × 2`。

差值 0～150 顯示 MAX -差值；151 起顯示兩位小數百分比。顯示採十進位四捨五入，3849／4000 顯示 96.23%，SQLite 保存未截短的比例。MAX 未知或無效不除法、不產生百分比。

- ACP：正常完成，Perfect>0，Cool／Bad／Miss=0，完整音符已驗證且判定合計相符。
- AC：正常完成，Perfect+Cool>0，Bad／Miss=0，完整音符已驗證且相符，未達 ACP。
- CLEAR：正常完成但存在 Bad 或 Miss，完整音符與合計已通過正式提交檢查。
- FAILED：GAME OVER。部分判定不回填完整音符，不提升正常完成最高 EX。
- UNPLAYED：尚無符合 confirmed／verified 條件的紀錄；由既有 difficulty_bests 查詢提供。

最高分與最佳燈號沿用獨立聚合 view，不維護容易被 FAILED 覆蓋的單一快取。每個 GameDifficulty 分開計算；全歷史保留。預設聚合本機保存的所有本人暱稱，歷史列表另可篩選單一暱稱。

## 最小資料庫變更

新增 migrations_v6.py，以 ALTER TABLE 追加 play_records 欄位：

| 欄位 | 用途 |
|---|---|
| game_level | 遊玩當下歌曲難度 LV 快照，不是人物等級 |
| max_combo、game_score、grade | 確認後的連擊、原始積分與評級 |
| max_difference | 已確認 MAX − EX；未知為 NULL |
| screenshot_path、screenshot_sha256 | 當次結算來源與指紋 |
| review_id、review_revision | 已確認審核的穩定身份及版本 |
| confirmed_at、confirmation_method | 正式確認時間與 manual_review 來源 |
| submission_hash | 依實際請求內容辨識重送或衝突 |

Song／難度仍透過既有 difficulty_id 關聯，不增加重複 Song 或另一套成績表。舊欄位、所有 ID、view、外部 FK、索引及舊歷史完整保留；新欄位的既有值為 NULL，不猜測回填。

保留 session_id UNIQUE，新增 review_id 非 NULL 的唯一索引。新正式紀錄有額外資料庫檢查：整數判定、EX 公式、MAX／差值／百分比一致、正常完成音符合計、GameDifficulty 的已知資料一致。提交後的正式 Record 不允許 UPDATE／DELETE；更正正式紀錄的稽核流程留待另行設計，不能用審核草稿悄悄覆寫。

缺少 Custom GameDifficulty 時，在同一提交交易內建立使用者明確選擇的 E/N/H 及確認數值。已存在的 GameDifficulty 不覆寫；若已知數值衝突則拒絕。官方 CSV 未提供的難度不新增。每筆 Record 保存當次 LV／完整音符快照；不依後來的畫面、來源檔案或人工資料變動重算舊成績。

完整確認快照保存於既有 evidence_json：原始 OCR、原始 Session、完整審核及修正值、確認時間／來源／備註、實際本人列、來源 Song／GameDifficulty、截圖指紋、遊玩模式、完整音符來源與計分版本。原 sidecar／原圖不被正式提交修改。既有 notes_source 列舉沿用 game_screen 表示已確認的遊戲完整音符；實際 official_csv／manual_game_screen 等細部來源另存 evidence_json.full_notes_source，並非宣稱全部值皆由 OCR 取得。

## 備份、交易與冪等性

Migration 先取得 BEGIN IMMEDIATE 寫入鎖，執行完整性檢查、SQLite backup API 備份與逐表雜湊核對，再增補欄位／索引／觸發器。核對全部舊欄位投影相同後提交；出錯全部回滾，保留備份。

每次新正式提交：

1. 重新載入合法 Session、結算原圖及已保存的人工作業版本，確認原圖 SHA-256 與 Session 關聯。
2. 核對使用者看到的摘要 token 與目前資料相同；若 Review、路徑或已知 GameDifficulty 改變，要求重新確認。
3. 共用 LibraryService 的寫入鎖，SQLite BEGIN IMMEDIATE 處理跨服務競爭。先查 session_id／review_id；相同提交返回既有紀錄，衝突拒絕。
4. 在修改前建立並驗證 pre-submit 備份，再次檢查審核及證據未在備份期間變更。
5. 同一交易內建立必要難度、PlaySession、PlayRecord 及提交來源快照。任何階段失敗整筆回滾。

資料庫是正式儲存收據的唯一依據，不靠提交後更新 sidecar 才判定成功。即使 HTTP 回覆遺失或程式在 commit 後關閉，相同請求重送仍返回同一 Record。不同 Session 可以有同歌曲、同分數、同截圖指紋，各自建立合法紀錄。

備份位於資料庫旁 backups/。每次新提交會建立完整 SQLite 備份，本階段沒有自動清除備份策略。SQLite 備份不包含外部 PNG，保留 data/monitor/sessions 的截圖與 sidecar；原圖暫時不存在時歷史成績仍可查詢，原圖指紋不符則拒絕顯示替代影像。

## 測試與交付證據

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -X utf8 -m unittest discover -s tests -v
.\.venv-ocr\Scripts\python.exe -X utf8 -m tools.validate_phase5a
```

199 項測試通過，含原 173 項與新增 26 項：計分邊界、NULL／負數／零／布林數字、基本燈號、官方 K 與不存在難度、多 Session 同分、唯一 Review、跨服務併發、本人第二列、HOST 暱稱衝突、來源變更、備份失敗、四個階段的交易回滾、重啟查詢、Schema migration 非空歷史／外部 FK／view 保留及晚期失敗。

完整資料庫副本使用既有 Cagayake 與 Astral 原圖重新 OCR，再以標明 ISOLATED TEST 的人工確認演練正式表提交。Cagayake：EX1673、MAX1676、MAX -3、AC；Astral 第二列 TC：EX1202、FAILED、完整音符／MAX／差值／百分比全為 NULL。故意在 Record 寫入後拋錯，全部資料表雜湊回復原狀。GUI 實際點擊摘要／提交、NULL Cool 停用、歷史與後端重啟查詢已驗證。

這是靜態原圖、隔離副本、模擬 Session 與實際 GUI 操作，沒有新增一次真實遊玩端到端。正式庫只執行安全 migration，PlayRecord 保持 0，真實紀錄留給使用者親自確認。逐項結果、備份路徑及 GUI 圖見 reports/phase5a/。

## 修改檔案與 Phase 5B

- 核心：dance_results/scoring.py、submissions.py、migrations_v6.py；database.py、migrations.py 接入 schema 6；migrations_v3.py 的歷史稽核排除後續新增欄位。
- 既有預覽：result_review.py 改用共用計分核心；OCR／監控狀態機未重構。
- GUI：gui.py、gui_assets/reviews.html／reviews.js／reviews.css、history.html／history.js／history.css、index.html、monitor.html。
- 測試與工具：tests/test_submissions.py、tools/validate_phase5a.py；四個舊測試檔調整目前最終 schema 斷言，保留歷史 migration 的獨立驗證。
- 版本與說明：dance_results/__init__.py、README.md、docs/VALIDATION.md、REQUIREMENTS.md、PHASE4_MATCHING_SPEC.md 及本文件。

Phase 5B 可直接用 difficulty_bests 與正式歷史做文字／靜態 Clear Lamp 圖示，不必改成績計算或覆寫 Record；最高 EX 與最佳燈號保留兩個欄位。先保留 UNKNOWN／待確認與 UNPLAYED 的區別，再處理動畫。正式紀錄的修訂流程、跨機器搬移證據、更多暱稱／皮膚、真實多人／GAME OVER 完整端到端仍需後續驗證；Pack Hover 自動辨識與多人下載位置規則仍未確認。
