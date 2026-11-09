# Phase 4C 多人下載、候選搜尋與人工確認（0.8.1）

本次沿用 ResultReviews、既有 scanner／LibraryService、HTML／JS 及即時監控架構。SQLite 維持 schema 5，不新增取得方式或時間欄位、不重建正式庫。原始 OCR、鎖定 Session 與結算原圖保持不變；人工資料保存於原有 result-review.json sidecar。

## 操作方式

雙擊 MonitorGUI.cmd 或 LibraryGUI.cmd，進入「待確認成績」。本輪另開 `http://127.0.0.1:40466/reviews` 供使用；沒有停止原有 40463／40464 監控。更新 Python 後，已啟動的舊服務不會自動載入新版後端；切換監控前先在舊頁面停止，避免多個服務同時產生 Session。新版頁面只開啟審核，不會自行開始監控。

1. 選取結算原圖，核對標頭的 Session 及原本的鎖定資料。缺少 Pack、Song ID 或搜尋不到歌曲，仍保留收件匣項目。
2. 預設搜尋「全部 Custom Pack（包含 connect）」。可改為官方 K 或全部來源；每頁 60 首、提供上一頁與下一頁，沒有隱藏第 100 首之後的候選。候選顯示 Song ID、Root、Pack、SongFolder、來源檔案、編碼，以及已知的遊戲難度數值。
3. 明確選擇 Song ID。不預選第一個或唯一同名項目；查詢、分頁、重新掃描都以 Song ID 保留既有選擇，不依列表索引。改選另一 Song 時撤銷身份、完整音符與時間的核對勾選。
4. 歌曲剛下載時，選擇已登錄的 SONG 根目錄，按「重新掃描 SONG 並更新候選」。掃描整個根目錄，涵蓋 connect、新 Pack 及既有 Pack 新增的 SongFolder，使用同一增量掃描及資料庫寫入鎖。GUI 不被掃描阻塞。
5. 完成後顯示新增 Pack／歌曲／來源、更新、警告、延後來源數與候選數變化。候選更新不修改草稿中的 Song ID、數字、核對狀態或鎖定 Session，不自動確認身份。
6. 多人本人辨識不可靠時，從「人工選擇本人成績列」明確選擇。帶入該列暱稱、數字與完成狀態後仍要人工核對；預設選項保持空白，排名第一不被當作本人。
7. 填入目前難度 EASY／NORMAL／HARD、歌曲 LV、經確認的完整音符與遊戲歌曲時間（秒）。時間未知保持空白，不讀音樂時長代填。核對依據可另行輸入。
8. 保存草稿或「確認資料（另存）」。後者保存明確 Song／Root／SongFolder、數值、本人列 ID、原圖 SHA-256、來源 explicit_user_review、時間及備註。原始 OCR 不被修正值覆寫；內容相同重送不增加版本。

確認資料仍有缺失時保留待確認，不會產生正式 PlayRecord。完整音符或時間未知可保留人工審核資料，但不能表示已具備 Phase 5 正式儲存條件。畫面分別顯示 OCR、歌曲身份、本人列、完整音符、時間與正式儲存尚未開啟的狀態。

## 匹配與資料保護

Custom 的永久身份仍為內部 Song ID，加 Root／Pack／SongFolder 路徑；Title、Artist、BPM 相似不合併。自動匹配仍要求已確認 Pack、歌曲名稱、E/N/H、遊戲 LV、經驗證完整音符、遊戲時間全部吻合且候選唯一。候選比較只列缺值或衝突，永遠不在審核搜尋 API 自動綁定。資料庫缺值不能視為匹配成功。

遊戲 LV 始終是歌曲難度等級，沒有 player_level，不解析人物頭頂等級。多人結算底部歌曲 LV 可能屬於 HOST 所選的另一難度，保留原始觀測並提示，不自動填成本人自由模式難度。

掃描前以單次 250 ms 間隔觀察檔案大小與 mtime；解析本身另檢查讀取期間變動。空白、仍變動或無可靠名稱的來源延後處理，整個 SongFolder 暫停更新，避免略過來源後把名稱歧義誤變成可靠標題。延後時不將既有來源當作消失。這只能確認名稱 metadata 可讀且短暫穩定，不能保證遊戲已下載完全部音樂／譜面內容；下一次重掃再檢查。原檔不修改，既有歷史不刪除。

connect 保留既有 multiplayer_download 的管理分類；這是位置分類，不能當作每首歌的取得證明。候選與人工確認證據的 acquisition_method 暫為 unknown／acquisition_verified=false，沒有新增 SQLite 欄位。即使 Pack 只有一首，仍不推定為多人下載。可在備註保存個別下載的人工證據。

## Astral Paradox 與預覽

保存的真實 Session 原圖顯示 ChuniPenguin 第一列正常完成、TC 第二列 GAME OVER。重新執行靜態 OCR 得到 TC：P585／C32／B2／M45、最高連擊 238、遊戲原始總分 35575、F，底部歌曲 LV38。11 個標註欄位皆相符。HARD 是使用者補充資料，不能宣稱從結算畫面 OCR 讀出。

EX=1202，當次已判定 664，FAILED。完整音符未知，MAX／百分比／差值保持 NULL；664 不回填完整音符。不改成 AC、ACP 或 CLEAR。已有完整可靠資料時才顯示 MAX-0～150 或兩位小數百分比。正式最佳成績及燈號仍未啟用。

## 驗證與證據

執行命令：

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -X utf8 -m unittest discover -s tests -v
.\.venv-ocr\Scripts\python.exe -X utf8 -m tools.validate_multiplayer_review
```

自動化涵蓋全 Custom 搜尋、connect／其他／單首新 Pack、同 Pack 不同資料夾同名、超過 100 首分頁、無候選保留、變動／空白來源延後、非空歷史／ID 保留、背景 HTTP 重掃、原圖與人工值分開保存、人工第二列、第一列正常而本人失敗、重啟與冪等性。新增「更衣間」原圖分類為 inventory，監控狀態保守為 UNKNOWN，不沿用被遮擋的等待室歌曲欄位。

原始測試圖片 25 張重新執行 OCR，分類 25/25；另重新辨識實際 Astral Session 原圖。舊多人 GAME OVER 圖的 Cool=1 仍被背景干擾，保留 NULL 待人工修正，不補成 0。詳細準確率、耗時、資料庫副本統計與修改清單見 `reports/phase4c/multiplayer/report.md`、`validation.json`、各 run 的 image-results.json 與 rescan.json。

GUI 另外使用 reports 下隔離的測試 SONG／SQLite／Session，真實點擊重掃讓候選從 0 變 2、明確選擇非 connect Song 3／第二列 TC，刻意將 P585 改成 P586 作為草稿保存測試，再重掃及重開頁面確認保留。這個測試值不代表實際成績，未寫入正式資料。GUI 證據為 gui-verification.json、gui-rescan-test.png；正式介面只讀檢視實際 Astral 的證據為 gui-actual-review-top.png。

本輪是真實檔案的資料庫副本掃描、靜態原圖重辨識及模擬 Session／GUI 驗證，沒有新增一次真實多人遊玩端到端測試。

## 修改檔案與 Phase 5

| 用途 | 檔案 |
|---|---|
| 全候選、比較、本人列與證據 | dance_results/result_review.py、result_ocr.py |
| 共用增量掃描與安全延後 | dance_results/scanner.py、database.py、library_service.py |
| GUI API 與操作 | dance_results/gui.py、gui_assets/reviews.html、reviews.js、reviews.css |
| 新增遮擋畫面分類 | dance_results/ocr_screens.py、tests/fixtures/phase4a_ground_truth.json |
| 回歸與實際原圖／副本工具 | tests/test_multiplayer_review.py、tools/validate_multiplayer_review.py |
| 版本與說明 | dance_results/__init__.py、README.md、docs/REQUIREMENTS.md、VALIDATION.md、PHASE4_MATCHING_SPEC.md、本文件 |

Phase 5 可接收已核對的 sidecar，仍需實作明確正式儲存交易、穩定結算收據與衝突拒絕，並定義時間／來源證據的正式保存。必要資料缺失不可升級為已驗證成績。Pack Hover／選取的受控對照、非 TC 暱稱及真正多人遊玩完整流程仍需驗證；多人下載位置規則尚未確認，不依檔案時間或 Pack 名稱自動判定取得方式與當次歌曲。正式歷史最佳與燈號由後續已驗證紀錄計算。
