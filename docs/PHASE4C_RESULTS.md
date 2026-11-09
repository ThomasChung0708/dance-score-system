# Phase 4C：結算 OCR 與人工確認（v0.8.0）

0.8.1 已整合全 Custom 候選搜尋、Pending Review 全 SONG 背景重掃與人工本人列確認。最新資料庫／測試結果以 [多人補充](PHASE4C_MULTIPLAYER.md) 為準；下方保留 0.8.0 階段證據。

沿用既有 Python／HTML／JS、RapidOCR 與 WGC。沒有新增套件，沒有 SQLite migration。正式歌曲庫只做讀取；所有成績確認另存監控目錄，不呼叫正式 PlayRecord 寫入。

`game_level` 在選歌 OCR、結算 OCR、Session、Pending Review、Matcher 與 SQLite 都表示歌曲目前難度的等級（Chart Level）。GUI 統一顯示「歌曲 LV」與「遊玩完成狀態」；人物頭頂等級不解析，也沒有 `player_level` 欄位。既有 `local_game_level` 表示玩家所選譜面的歌曲 LV，`host_result_level` 表示多人 HOST 顯示的歌曲 LV，均不是人物等級；保留欄位名稱與既有資料相容，不需 migration。

## 啟動

雙擊 `MonitorGUI.cmd` 或 `LibraryGUI.cmd`，點右上「待確認成績」。也可啟動後直接開 `/reviews`：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.gui
```

本輪另開新版 `http://127.0.0.1:40464/reviews`，沒有中斷使用者當時仍在執行的舊版 40463 監控。要改用新版即時監控，先在舊版按「停止監控」，再於新版 `/monitor` 開始；避免兩個服務同時建立重複遊玩 Session。以後重新啟動上述啟動檔即使用 v0.8.0。

新版監控會重用當次 OCR 的結算資料，隨結算原圖建立待確認項目。舊版已保存的 Session 可在收件匣選取，設定本人暱稱，再按「辨識此結算」；模型在背景執行，不需開始監控。沒有結算原圖的中斷 Session 不列為成績。

測試期間新增的兩張旁觀圖已納入：帶 `quit look on mode` 提示的畫面獨立分類為 `SPECTATING`，不建立本人遊玩 Session；若先前已誤進 PLAYING，會標為中斷並清除來源。旁觀結算沒有本人的成績列時，P／C／B／M 等本人欄位全部保留 NULL，不取其他人的分數。

## 人工確認流程

1. 選結算，核對原始截圖、Session、鎖定的 Song ID／Pack／難度／LV。
2. 不確定歌曲時搜尋名稱，明確選擇正確 Song ID。候選顯示 Pack、Root 與完整 SongFolder；同名 JOINT 不合併、不預選第一項。官方 K 來源與自製／connect 保持各自身份。
3. 核對本人暱稱、Perfect／Cool／Bad／Miss、最高連擊、原始積分、評級、完成狀態與歌曲 LV。空白是 NULL，輸入 0 才是零；黃色欄位與提示指出原 OCR 的缺值或低信心。
4. 完整音符數只能採用已驗證的官方／既有人工資料，或使用者明確核對的遊戲數值。GAME OVER 的判定合計不補成整首音符數。多人底部 LV 是 HOST 所選歌曲的難度等級，單獨保留，不預填成歌曲 LV。
5. 「保存草稿」保存未完成的核對。「確認資料（另存）」要求本人與身份核對、有效數字及必要欄位；錯誤數字／判定總數衝突不能確認。完整音符未知可保存為 `reviewed_pending_full_notes`，不顯示已確認 MAX／百分比。

人工核對不會重寫原始 OCR 或鎖定的 Session；每次修改保留先前草稿、時間、版本與 `explicit_user_review` 來源。歌名或 LV 與 Session 不一致時會提示，需明確人工核對身份。原圖本人的可靠 GAME OVER 證據不能改存成 CLEAR；要重新檢查錯誤本人匹配，保留原項目與證據供後續處理，不覆寫原 OCR。

## 計分預覽與驗證

`EX Score = Perfect × 2 + Cool`。完整音符與身份經確認且資料一致時，`MAX = total_notes × 2`；差距 0～150 顯示 `MAX -差值`，151 以上顯示百分比，保留兩位小數。未知、負數、非整數、連擊超過成功判定、判定合計衝突均保留錯誤。

正常完成須比對判定合計與已驗證總數；GAME OVER 允許部分判定，但不得超過已驗證總數。預覽 ACP 為全部 Perfect、AC 為無 Bad／Miss 且可有 Cool；其餘正常完成為 CLEAR，GAME OVER 必為 FAILED。這些只作預覽，不更新正式燈號或歷史最高分。

實例：既有 Song 16 結算辨識為 P835／C3／B0／M0，EX 1673、MAX 1676，顯示 `MAX -3`／AC 預覽。這是對 Phase 4B 保存原圖的重新辨識，沒有宣稱本階段新增一次真實遊玩驗證。

## Pack 可信狀態

- `CONFIRMED`：本階段由使用者明確人工確認；目前沒有足夠校準圖片支援自動選取確認。
- `CANDIDATE`：讀到可能的 Pack 或紫色列，仍可能是 Hover，不能用來自動綁定成績。
- `UNKNOWN`：沒有可用來源證據。

紫色面積不再直接產生已確認 Pack。開啟清單但無法確認新選取時，立即撤銷舊 Pack；不把上一輪 Pack 套到新 Session。關閉清單後，可在新版監控頁獨立選擇 Pack 並勾選核對，不必先猜 Song ID。人工 Pack 確認要求當前選歌 token、Root／Pack ID，且清單已關閉；重新開清單會撤銷此確認。已開始的 Session 快照不被修改。

已比較四張真實清單圖片（原始 `1.PNG`、`相同歌名1.PNG`，以及 Phase 4B 兩張擷取）及三張關閉清單圖片。它們不是同一時刻、同一清單的受控 Hover／選取對照，不能拿它們訓練可靠的顏色選取門檻。原始影像不修改，數值分析在 `reports/phase4c/pack-analysis.json`。

需要補拍同一 Pack 清單、同一滾動位置與視窗大小的四張原圖：

1. 目前選取 Pack A，游標移到清單外，並說明哪一列是 A。
2. 只 Hover Pack B，不點擊；A 仍是實際選取，游標留在 B。
3. 點擊 B 後，把游標移出清單；明確標註 B 已選取。
4. 關閉清單後的對應歌曲資訊；請勿在中間換歌、換難度或滾動。

不會依歌曲清單內容、歌名或 Artist 反推 Pack。

## 持久化與冪等性

每個 `sessions/<UUID>/` 保存：原本的 `session.json`／`result.png`，另加 `result-review.json`。審核 ID 是 Session UUID＋原圖 SHA-256 的穩定 UUID5。原圖更動會拒絕覆蓋舊辨識；同一張圖在不同合法 Session 不會誤合併。

同一 Session 重複 OCR 不覆寫既有審核；確認請求依內容雜湊冪等，相同請求重送與重啟後重送維持同一版。不同修改要求符合 revision，過期寫入回傳 409。JSON 使用原子替換，服務內有鎖；正式庫始終保持唯讀。多個 v0.8 GUI 服務共用同一監控目錄的跨行程同步尚未驗證，使用一個服務、多個頁籤即可。

## 驗證、隔離副本與限制

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -X utf8 -m unittest discover -s tests -v
.\.venv-ocr\Scripts\python.exe -X utf8 -m tools.validate_results
```

`tools.validate_results` 在 `reports/phase4c/sandbox/run-.../` 建立新 SQLite 完整副本及明確標示的靜態回放 Session。只在這個受限制路徑演練實際 PlaySession／PlayRecord 交易，保存原始截圖指紋的收據、重送同筆記錄與刻意失敗的整筆回滾。這個測試寫入工具沒有 GUI／正式 API 入口，不可當作已交付的正式成績儲存功能。

四張原始結算圖 44 個欄位值中 43 個正確（97.73%）；只有 42 個同時正確且符合可靠門檻。多人 GAME OVER 的 Cool 1 被背景暱稱干擾，保留 NULL；單人 GAME OVER 的 LV 值正確但信心約 89.5%，需要人工核對。另存的 Phase 4B A++ 評級值讀對但信心約 81%，也要求核對。這些是開發樣本的欄位精確相同比率，模型信心分數不是準確率，也不是新樣本泛化成績。

157 項自動化測試通過。最初 22 張加上新增 2 張旁觀 PNG，共 24 張重新執行模型，分類 24/24 正確；兩個 JOINT 的 LV／音符／時間繼續保持區別，缺資料庫證據仍不自動綁定。新增旁觀結算另有 9 個預期 NULL 的本人欄位拒絕檢查，沒有拿這些空值提高原四張結算的數字辨識準確率。具體耗時、失敗欄位、原始 token、待確認原因與副本路徑在 `reports/phase4c/image-results.json`／`validation.json`／`report.md`。

本階段沒有新增真實遊玩端到端驗證；多人／GAME OVER 的結果是原始靜態圖測試，GUI 是本機實際頁面驗證。不同皮膚／DPI／全螢幕、其他暱稱字體與非 TC 多語暱稱、低清晰度評級、Hover 與真正選取差異仍需更多樣本。未知畫面和不可靠本人識別繼續待確認。

## 修改檔案

| 範圍 | 檔案 |
|---|---|
| 新增結算／審核 | `dance_results/result_ocr.py`、`result_review.py` |
| 既有監控整合 | `dance_results/monitor.py`、`monitor_ocr.py`、`monitor_tracking.py`、`ocr_engine.py`、`ocr_screens.py` |
| GUI 與路由 | `dance_results/gui.py`、`gui_assets/reviews.html`、`reviews.js`、`reviews.css`、`monitor.html`、`monitor.js`、`index.html` |
| 版本與驗證 | `dance_results/__init__.py`、`tests/test_result_review.py`、`tests/fixtures/phase4c_ground_truth.json`、`phase4a_ground_truth.json`（新增兩張旁觀標註）、`tools/validate_results.py`、`phase4c_report.py`、`validate_monitor_images.py`（旁觀狀態映射） |
| 文件 | `README.md`、`docs/VALIDATION.md`、`docs/REQUIREMENTS.md`、本文件、`reports/phase4c/` |

## Phase 5 整合建議

正式儲存前，先完成 Pack／多人／GAME OVER 真實端到端與低信心覆核驗證。將已人工確認的 sidecar 與來源 SHA-256 交給明確的成績寫入服務，要求 Song／GameDifficulty 身份與遊戲值一致，再在一筆 SQLite 交易內保存 Session、審核收據與 PlayRecord。利用既有 `play_records.session_id UNIQUE` 加上結算識別收據，重送回傳既有 record_id，衝突拒絕覆蓋。

百分比保存完整精度、畫面顯示兩位小數；失敗判定合計不能回填完整音符。先釐清人工修正已正式儲存紀錄的版本與稽核規則，再讓最高分／AC／ACP 燈號從已驗證成績計算。GUI 需明確區分「審核資料已保存」與「正式成績已入庫」。本階段未開啟此正式入口。
