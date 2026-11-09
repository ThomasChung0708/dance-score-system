# Phase 4B 即時監控（v0.7.0／SQLite schema 5）

已完成可執行 MVP，沿用現有 HTML／JS／Python GUI。Windows Graphics Capture、OCR、GUI 分別執行；所有監控資料另存，沒有正式歌曲庫 migration 或 PlayRecord 寫入。

## 啟動與停止

雙擊根目錄 `MonitorGUI.cmd` 或 `LibraryGUI.cmd`，進入右上「即時監控」。本機 GUI 預設連接埠自動選擇，頁面路徑 `/monitor`。

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.gui
```

本人暱稱預設 TC，可修改。選擇自動尋找或指定視窗，設定擷取取樣頻率／OCR 最短間隔／連續確認次數，按「開始監控」。預設 2 Hz／2 秒／2 次。遊戲失焦仍可擷取；最小化暫停辨識。按「停止監控」可隨時停止，不操作遊戲，進行中的 Session 會標為 INTERRUPTED。OCR 已開始時不強制殺執行緒；頁面顯示「停止中」，最多等本次辨識與清理完成。

手動視窗選擇預設只列遊戲。勾選「顯示其他視窗」可明確選擇其他可見視窗；不會自動抓取桌面或其他應用。自動搜尋支援 `dance v任意版本...`／`Dance Online`，排除 Dance Launcher；多個符合視窗時要求手動選擇。視窗關閉後重新搜尋，Handle／PID 改變即清除舊來源上下文。

監控預設保存至 `data/monitor/`。可用 `--monitor-directory 路徑` 指定另一目錄。測試後已將使用者確認的 Song 16 對應及當次 Session、確認畫面與結算完整保留至預設目錄；測試原件仍在 `reports/phase4b/real-game/`。啟動不會自動開始擷取。

指定另一份 SQLite 時，預設使用該資料庫旁的獨立監控目錄；`catalog-location.json` 防止不同資料庫共用人工對應及 Session。手動指定目錄若屬於另一資料庫會拒絕啟動，避免相同數字 Song ID 被誤用。

## 安裝與相容性

已在 CPython 3.11.5 x64／Windows 10 build 19045 實跑。

```powershell
py -3.11 -m venv .venv-ocr
.\.venv-ocr\Scripts\python.exe -m pip install -r requirements-monitor.txt
```

沿用 RapidOCR 3.10.0、ONNX CPU、PP-OCRv5；新增 [windows-capture 2.0.1](https://pypi.org/project/windows-capture/2.0.1/) 的 cp39-abi3-win_amd64 wheel 及 psutil 7.2.2。實際 pip check 通過。GUI／官方匯入可使用一般 Python，唯即時監控需此獨立環境；啟動檔會優先使用已建立的 `.venv-ocr`。

[WGC Python 介面](https://github.com/NiiightmareXD/windows-capture/tree/main/windows-capture-python) 使用 HWND 指定單一視窗，不注入、不讀取遊戲記憶體、不傳送鍵盤或滑鼠。Windows 10 不支援 WGC 的 minimum_update_interval 選項，已避免設定；取樣率限制在 Python 的影像拷貝回呼，底層事件仍由 Windows 產生。沒有切換到桌面截圖作為靜默回退。無權擷取、全黑、串流錯誤或長時間沒有新畫面時會顯示診斷，不把舊畫面當新結果。

本次開發工具的隔離桌面不能看見一般桌面；實際測試在一般桌面權限執行。使用者正常從桌面啟動程式即可，沒有新增必須以系統管理員執行的要求。

## 狀態、歌曲與 Session

狀態包含 UNKNOWN、LOBBY、SONG_SELECT、WAITING_ROOM、PROFILE、LOGIN、PLAYING、RESULT、GAME_OVER、INTERRUPTED。連續兩次獨立取樣辨識才切換；同一 sample_id 不重複計數。短暫錯誤不建立新 Session 或結算。

選歌變化會立即撤銷舊確認，連續穩定後才公開新觀測。遊玩開始消耗這輪選歌快照，記錄 UUID、時間、Song ID（可 NULL）、難度／LV／音符／時間／Pack、匹配方式及原始觀測。等待室預覽或遊玩頁尾的可靠名稱／LV 不一致時撤銷舊歌曲身份，不沿用前一首。預覽只用來排除衝突，不補完整音符／Pack。觀測超過 60 秒未更新時不直接用來確認新 Session。

PLAYING 中的鎖定快照不隨背景變化。RESULT／本人 GAME_OVER 出現後只保存一次 `result.png` 與 `session.json`，狀態為 pending_result／pending_score_ocr。程式未做完整判定數辨識、EX Score、最高分或 Clear Lamp。

沒有結算便回等待室／大廳、遊戲關閉、停止監控或工作失敗，記為 INTERRUPTED。重啟時，未正常收尾的 playing Session 改標中斷，既有截圖與已完成的待處理結果不刪除。

多人依設定暱稱尋找本人，不按排名。其他人的 GAME OVER 不代表本人失敗；底部多人 LV 是 HOST 顯示，不當作本人難度。辨識到本人非 HOST 時清除先前來源上下文，資訊不足繼續待確認。connect 的管理 Pack 不推論為 HOST 原始來源。

## Pack 與人工確認

清單開啟時辨識各 Pack 原文，僅在明確紫色列、名稱對應唯一 Pack／root、連續確認均通過時記住 Pack。截斷文字只在候選唯一且選取證據清楚時採用。不知道是否切換時立即清除舊 Pack。關閉清單後可保留仍有效的確認。

**本輪真實測試沒有取得可靠的紫色 Pack 反白證據**：兩次清單畫面均維持待確認，不能宣稱真實自動 Pack 追蹤已通過。顏色／唯一候選／清除舊值邏輯有模擬測試。沒有透過歌名或 Artist 猜 Pack。

必要資料不足時，介面列出全部候選的 Song ID、Pack／完整相對路徑；官方候選保留 K 的 fileId／gn，與自製／connect 分開。可明確人工選擇正確歌曲，確認 E／N／H、LV、完整音符數與時間秒數，勾選核對後儲存。

人工資料另存 `manual-references.json` 與確認截圖，不補寫正式 GameDifficulty、時間或 PlayRecord；沒有隱藏的 migration。同一份完整證據已有不同 Song ID 時拒絕覆蓋。自動重新使用人工對應仍要求完整六項相同、來源身份未變，而且不能藉一筆人工參考排除其他數值未知的同名候選。未取得全部必要證據時維持待人工確認。官方 schema 5 尚無已確認時間，官方分類也不自動冒充原始 Pack，故官方自動身份確認仍受相同保守限制。

這輪〈Cagayake! GIRLS〉是使用者在 GUI 確認 Song 16／Alice 4th complete step／HARD LV20／838 notes／88 秒，來源記錄為 explicit_user_review；並非系統憑相同標題猜中。

若在歌曲庫頁面手動掃描或匯入新來源，請停止再重新開始監控以更新唯讀候選快照。未知歌曲不會由監控自動掃描、匯入或建立正式 ID。

## 效能與資料

原生擷取回呼只保留最新一張影像，OCR 不排隊累積過期幀；GUI 每秒查詢狀態。畫面變化偵測與最短 OCR 間隔共同限制辨識；穩定畫面定期心跳確認，遊玩中以頁首／頁尾 ROI 為主，選歌只處理標籤、選中列及左側資料，定期全圖覆核。ROI 缺必要欄位或分類未知會回退全圖。

真實 NEW! 圖示與歌名曾被合併，現以歌名字欄的像素 ROI 重讀，不以文字刪除或資料庫歌名替換。這個修正有真實畫面證據；ROI 原文、耗時及缺值保留診斷。

只保存確認截圖與結算，不儲存每一幀。診斷 JSONL 10 MiB 輪替，GUI 只回傳最近 50 個事件；人工對應和 Session 長期保留，沒有自動刪除歷史。CPU 採用 psutil 行程值，100% 表示一個邏輯核心，另提供除以核心數的整機比例；RSS 是行程記憶體，不是遊戲占用。沒有量測 GPU／遊戲 FPS。

檔案結構：

```text
data/monitor/
  catalog-location.json
  manual-references.json
  confirmations/<observation-id>.png
  diagnostics.jsonl
  sessions/<session-uuid>/session.json
  sessions/<session-uuid>/result.png
```

## 修改檔案

| 範圍 | 檔案 | 用途 |
|---|---|---|
| 新增監控核心 | `dance_results/window_capture.py`、`monitor.py`、`monitor_tracking.py`、`monitor_ocr.py` | WGC、服務生命週期、狀態與 Session、ROI OCR 與安全匹配 |
| 新增介面 | `dance_results/gui_assets/monitor.html`、`monitor.js` | 即時狀態、設定、人工確認、結算與診斷 |
| 沿用介面與 OCR 調整 | `dance_results/gui.py`、`ocr_matching.py`、`ocr_engine.py`、`ocr_screens.py`、`__init__.py` | 監控 API、來源篩選、低延遲參數、文字空白修正、版本 |
| 啟動與依賴 | `MonitorGUI.cmd`（新增）、`LibraryGUI.cmd`、`requirements-monitor.txt`（新增） | 優先使用 OCR 環境，保持原有歌曲庫啟動方式 |
| 新增測試與驗證工具 | `tests/test_monitor.py`、`tools/validate_monitor_images.py`、`monitor_capture_probe.py`、`collect_monitor_status.py`、`phase4b_report.py` | 狀態與 API 測試、實際模型逐圖辨識、原生擷取探測、效能與完整性報告 |
| 文件與證據 | `README.md`、`VALIDATION.md`、`REQUIREMENTS.md`、本文件、`reports/phase4b/` | 啟動說明、已驗證範圍與未驗證項目、JSON／截圖證據 |

以上未包含環境套件安裝檔與執行時產生的 `data/monitor/`；正式 SQLite 沒有改動。本階段沒有 Schema Migration。

## 測試與剩餘範圍

實測報告：`reports/phase4b/report.md`／`validation.json`。130 項自動化通過；22 張原 PNG＋8 次 ROI 共 100 項狀態／欄位檢查通過。真實測試視窗覆蓋遮擋、失焦、縮放、最小化、關閉與重啟。使用者自行操作的一輪真實正常遊玩完成，結算只保存一次。

真實 GAME OVER、多人、斷線彈窗、遊戲本體關閉後重啟、全螢幕／不同皮膚、特殊 DPI、所有 E/N/H 與中文／日文主歌名仍需要遊玩實測。不能將模擬／圖片回放或測試視窗通過當成這些實際遊玩通過。unsupported 畫面保持 UNKNOWN；來源不足保留候選。

重跑方式（測試視窗工具只操作自己的視窗）：

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -X utf8 -m unittest discover -s tests -v
.\.venv-ocr\Scripts\python.exe -X utf8 -m tools.validate_monitor_images
.\.venv-ocr\Scripts\python.exe -X utf8 -m tools.monitor_capture_probe
python -X utf8 -m tools.phase4b_report
```

下一階段結算 OCR：辨識本人判定數／最高 Combo／遊戲分數／完成證據，低信心與衝突交人工；驗證正常完成的總顆數，失敗部分判定不可當完整總數；再確認 difficulty_id 與驗證狀態，才規劃正式成績寫入、最高分與燈號。這次沒有提前實作這些功能。
