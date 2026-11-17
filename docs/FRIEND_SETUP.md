# 給朋友的 Windows 安裝與使用說明

這是本機使用的熱舞 Online 成績工具。每個人建立自己的歌曲庫、暱稱和遊玩紀錄，資料只存在自己電腦。第一次安裝請預留時間下載套件及 OCR 模型。

## 準備

- Windows 10 64 位元（已驗證 build 19045）；Windows 11 可試用，尚未有實機驗收。
- Python 3.11 64 位元，開發環境實際使用 3.11.5。安裝時勾選「Add python.exe to PATH」，保留 Python Launcher（`py`）與 tkinter。
- Git for Windows（或在 GitHub 頁面按 Code → Download ZIP 下載後解壓縮）。
- 儲存庫網址：<https://github.com/ThomasChung0708/dance-score-system>
- 建議先留 2 GB 空間給程式、虛擬環境和模型，另外預留空間給每首遊玩的結算圖片。這是建議值，不是量測出的最低需求。
- 自己的熱舞 Online 遊戲資料夾（例如 `D:\Dance v1.9.0-dev-xxxx`）。這個儲存庫不附歌曲、官方歌單、私人資料庫或測試截圖。

## 取得程式與安裝套件

在檔案總管選擇要放程式的位置，在該處開啟 PowerShell，執行：

```powershell
git clone https://github.com/ThomasChung0708/dance-score-system.git
cd dance-score-system
py -3.11 -m venv .venv-ocr
.\.venv-ocr\Scripts\python.exe -m pip install -r requirements-monitor.txt
```

`requirements-monitor.txt` 會一併讀取 `requirements-ocr.txt`，包含 OCR 與視窗擷取需要的套件。不要複製別人的虛擬環境，也不需要更改 PowerShell 腳本權限。

## 下載 OCR 模型

模型設定沿用 [PHASE4A_OCR.md](PHASE4A_OCR.md)：RapidOCR 3.10.0、PP-OCRv5 mobile、ONNX Runtime CPU，不需要 CUDA。首次載入會自動下載約 20.5 MiB 到專案的 `.ocr-models`。在同一個 PowerShell 執行：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -c "from pathlib import Path; from dance_results.ocr_engine import LocalOCREngine; LocalOCREngine(Path('.ocr-models'))"
```

等下載結束後就能離線辨識。這條命令不啟動遊戲、不讀歌曲庫，也不傳送任何圖片。下載失敗請保留錯誤文字，確認網路後重試。

## 第一次建立自己的歌曲庫

1. **自製譜（勁樂）**：雙擊根目錄的 `Start.cmd`，在資料夾選擇器選自己遊戲的 SONG 根目錄，通常是 `<遊戲資料夾>\DATA\ADDON\SONG`。它會唯讀掃描歌曲，在本專案建立 `data/library.sqlite3` 並開啟掃描報告；不會開始畫面監控。
2. **官方譜（全部）**：官方歌單 CSV 隨遊戲用戶端一起安裝，位置是 `<遊戲資料夾>\dance_Data\StreamingAssets\song_table.csv`。雙擊 `LibraryGUI.cmd`，按「匯入官方 CSV」，選這個檔案，先看預覽再按「匯入 K 模式」。CSV 格式與細節見 [OFFICIAL_IMPORT.md](OFFICIAL_IMPORT.md)。
   - 這份 CSV 是遊戲本身的資料，本儲存庫不附上也不轉發。請用你自己遊戲裡的那份；找不到時，先確認遊戲版本／資料夾，再私下問分享者，不要公開上傳。
   - 官方譜的音符數之後可能被改版。結算畫面才是準：身分唯一、檢查通過時以結算 P+C+B+M 保存，並顯示 `CHART_NOTES_CHANGED` 提示，不會改官方目錄。
3. 之後新增自製歌，可在歌曲庫介面重新掃描 SONG；不要索取或覆蓋別人的整份資料庫。

## 啟動網頁與監控

雙擊 `MonitorGUI.cmd`（需要已安裝 `.venv-ocr`）或 `LibraryGUI.cmd`，會開啟本機網頁（`http://127.0.0.1:<自動選的埠>/`）。點右上「即時監控」進入監控頁，按「開始監控」才會擷取遊戲畫面。保留啟動視窗；要結束時在該視窗按 Ctrl+C。

## 每次遊玩

1. 在「即時監控」的「本人暱稱」填自己的遊戲 ID，要和遊戲顯示的完全相同。欄位離開焦點或按開始監控時保存，重開程式仍保留；預設值 TC 只是第一次的範例，請改成自己的。暱稱換人時先停止監控、改暱稱、再開始。
2. 選擇遊戲視窗，按「開始監控」。
3. 單人選曲時，官方譜在遊戲內「全部」分頁選，自製譜在「勁樂」分頁選。讓選曲畫面停留一兩秒，避免立即跳過。
4. 結算後若監控頁問「這首是官方譜還是自製譜？」，選「官方譜」或「自製譜」；若有多個自製副本，再選正確 Pack／資料夾。只有自製同名多資料夾時會直接問「這首是哪個資料夾的譜面？」。
5. 來源已可靠確認或規則能唯一判定時不會詢問。忽略或按「稍後到待確認處理」不會丟失結算。

### 多人房間

- **自己是房主**：和單人一樣，程式會看到你的選曲畫面。
- **自己不是房主**：程式改用房間右側面板的曲名、你自己的「難度設置」與結算畫面的音符總數比對。只有「唯一一首官方譜相符、沒有同名自製譜、也沒看到自製譜線索」時才自動儲存，其他情況會跳出官方／自製選擇。
- **房主選了你沒有的自製譜**：你的頭像框會出現 NO MAP，接著是綠色下載條（房主那邊是藍色上傳條）。程式看到這些就把這首記為自製譜。遊戲下載完、程式掃描好（約 90 秒）後，成績會對到新下載的資料夾；FC 會自動存，非 FC 進待確認並預填歌名。掃描期間待確認頁會顯示「下載中／掃描中」。
- 沒下載下來的自製譜目前無法存成績（之後版本處理）。

自製歌完全沒有譜面資料時，仍需完整結算且 Full Combo 才會自動儲存，非 FC 進待確認。

## 待確認怎麼處理

進入「待確認成績」，選擇結算，看原圖、本人列、Song ID、Pack／資料夾、難度、LV 和數字。尚未選來源時這裡有同樣的選項。選來源後若還有低信心、兩張結果不一致或非 FC 門檻，仍需人工核對；不會繞過檢查。不要只為了消除提示把數字改成目錄數字。不確定可以先保留。歷史頁可以確認是否已正式儲存。

## 出問題時提供什麼

記下歌曲名、發生時間、單人或多人（房主／非房主）、官方或自製、遊戲解析度、預期與實際結果，以及畫面上的錯誤。停止監控後提供：

- 該場的 Session 資料夾：`data\monitor\sessions\<session-id>\`（內含 `session.json`、`result-review.json`、`result.png`、`result-observations\`）。依資料夾修改時間找對應那場。
- 診斷紀錄：`data\monitor\diagnostics.jsonl` 中對應時段（整個檔案也可以，但可能很大）。
- 如果有 `data\ocr_failures\`，只附相關那一筆。

這些路徑在第一次監控後才會產生。傳送前請先看圖片和 JSON：可能含自己／其他玩家暱稱、聊天、本機路徑。請私下傳給維護者，不要貼到公開 issue。不要寄整份 `data\library.sqlite3`、`data\backups\`、整個 SONG、遊戲的 `song_table.csv` 或虛擬環境。
