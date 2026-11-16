# 給朋友的 Windows 安裝與使用說明

這是本機使用的熱舞 Online 成績工具。每個人建立自己的歌曲庫、暱稱和遊玩紀錄。第一次安裝請預留時間下載套件及 OCR 模型。

## 準備

- Windows 10 64 位元（已驗證 build 19045）；Windows 11 可試用，尚未有這輪實機驗收。
- Python 3.11 64 位元，開發環境實際使用 3.11.5。安裝時勾選加入 PATH，保留 Python Launcher 與 tkinter。
- Git for Windows。先請分享者提供實際 GitHub 儲存庫網址；本文件沒有預設公開網址。
- 建議先留 2 GB 空間給程式、虛擬環境和模型，另外預留空間給每首遊玩的結算與兩幀圖片。2 GB 是安裝預留建議，並非量測出的最低需求；歌曲本身另計。
- 自己的遊戲和 SONG 歌曲資料夾。這個儲存庫不附歌曲、私人資料庫或完整測試截圖。

## 取得程式與安裝套件

在檔案總管選擇要放程式的位置，在該處開啟 PowerShell。輸入 `git clone `，後面貼上分享者提供的儲存庫網址，再補上 ` dance-score-system` 後執行。接著執行：

```powershell
cd dance-score-system
py -3.11 -m venv .venv-ocr
.\.venv-ocr\Scripts\python.exe -m pip install -r requirements-monitor.txt
```

`requirements-monitor.txt` 會一併讀取 `requirements-ocr.txt`，包含 OCR 與視窗擷取需要的套件。不要把整個朋友的虛擬環境複製過來，也不需要啟用 PowerShell 腳本權限。

## 下載 OCR 模型

模型設定沿用 [PHASE4A_OCR.md](PHASE4A_OCR.md)：RapidOCR 3.10.0、PP-OCRv5 mobile detection／recognition、ONNX Runtime CPU。首次載入引擎會自動下載約 20.5 MiB 到專案的 `.ocr-models`；不需要 CUDA。安裝完成後，在同一個 PowerShell 執行：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -c "from pathlib import Path; from dance_results.ocr_engine import LocalOCREngine; LocalOCREngine(Path('.ocr-models'))"
```

等候下載結束後就能離線辨識。這條命令不啟動遊戲、不讀歌曲庫，也不傳送結算圖片。下載失敗請保留錯誤文字，確認網路後重試同一條命令。

## 第一次建立自己的歌曲庫

雙擊根目錄的 `Start.cmd`，在資料夾選擇器選擇自己遊戲的 SONG 根目錄。它會唯讀掃描來源歌曲，在本專案建立 `data/library.sqlite3`，並開啟掃描報告；不會開始畫面監控。

雙擊 `MonitorGUI.cmd`，或 `LibraryGUI.cmd`。兩者都開啟本機歌曲庫網頁；MonitorGUI 要求已安裝 `.venv-ocr`，LibraryGUI 會優先使用它。點右上「即時監控」才進入監控頁，按「開始監控」才擷取遊戲。保留啟動視窗；結束整個服務時在該視窗按 Ctrl+C。

自製歌曲新增後，可在歌曲庫介面重新掃描自己的 SONG。官方譜另需在歌曲庫的官方匯入功能匯入你自己的官方 CSV，先預覽再匯入；掃描自製 SONG 不會建立官方歌單。CSV 格式與操作見 [OFFICIAL_IMPORT.md](OFFICIAL_IMPORT.md)。不要索取或覆蓋別人的整份資料庫。

## 每次遊玩

1. 在「即時監控」的「本人暱稱」填寫自己的遊戲 ID，與遊戲顯示相同。欄位離開焦點時保存，開始監控也會保存；監控頁與待確認頁共用最後使用的名字，重開程式仍保留。TC 只是第一次的預設值。
2. 選擇自己的遊戲視窗，按「開始監控」。暱稱或使用者換人時，先停止監控、改暱稱、再開始。
3. 遊戲選曲時，官方譜選「全部」，自製譜選「勁樂」。讓選曲畫面停留幾次取樣，避免立即跳過。這裡指的是遊戲內分頁，不是網頁的來源選單。
4. 結算後若監控頁顯示「這首是官方譜還是自製譜？」，選「官方譜」或「自製譜」。若有多個自製副本，再選正確 Pack／資料夾。只有自製同名多資料夾時，會直接問「這首是哪個資料夾的譜面？」；列表標出難度／LV 相符者，仍請核對資料夾。
5. 來源已可靠確認或規則可唯一判定時不會詢問。選擇只處理這一場，其他檢查照常；自動儲存模式必須已啟用，而且只適用模式涵蓋的新 Session。忽略或按「稍後到待確認處理」，不會丟失結算。

官方譜的目錄音符數可能已過時。身份唯一且結算安全檢查通過時，以結算 P+C+B+M 保存，差異顯示 `CHART_NOTES_CHANGED` 警告；不改官方目錄。自製歌完全沒有譜面資料時，仍需完整結算且 Full Combo 才能自動儲存，非 FC 會進待確認。

## 待確認怎麼處理

進入「待確認成績」，選擇結算，看原圖、本人列、Song ID、Pack／資料夾、難度、LV 和數字。尚未選來源時，這裡有同樣的選項。選來源後若還有低信心、兩張結果不一致或非 FC 門檻，仍需人工核對；不會繞過檢查。

需要修正時，依頁面填寫並保存核對，再看正式提交摘要後儲存。不要只為了消除提示把數字改成目錄數字。不確定可先保留，不必刪除。歷史頁可確認是否已正式儲存，並看到音符變更警告。

## 出問題時提供什麼

記下歌曲名、發生時間、操作、遊戲解析度、預期與實際結果、是否官方／自製，以及畫面上的錯誤。停止監控後，找到該場 `data/monitor/sessions/` 下的 Session 資料夾，複製整個該場資料夾給維護者；內含 `session.json`、`result-review.json`（如果已產生）、`result.png` 和 `result-observations/`（如果有兩幀）。另附 `data/monitor/diagnostics.jsonl` 中對應時段的診斷；若有 `data/ocr_failures/`，只附相關失敗那一筆。這些路徑在執行後才會產生。

請先查看圖片和 JSON：可能含本人／其他玩家暱稱、遊戲畫面、本機路徑。透過雙方同意的私下方式傳送，公開 issue 可先只放錯誤描述。不要寄整份 `data/library.sqlite3`、`data/backups/`、整個 SONG、所有遊玩歷史或整個虛擬環境。

分享者發布前須另外處理 Git 歷史隱私：即使目前 `.gitignore` 排除圖片，早期提交仍可能保留它們；本輪只稽核，不改寫歷史。
