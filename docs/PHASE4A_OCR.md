# Phase 4A：靜態遊戲畫面 OCR

本機原型已實際處理 `tests/專案測試圖片/` 的全部 22 張原始 PNG。只讀取正式 schema 5，沒有執行 migration、掃描或寫入歌曲／難度／成績，沒有即時監控。

## 啟動

本機已安裝好獨立 `.venv-ocr` 與 `.ocr-models`。雙擊專案根目錄的 `OCRStatic.cmd`，或在 PowerShell 執行：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.ocr_static --no-cache --labels tests/fixtures/phase4a_ground_truth.json
```

完成後開啟 `reports/phase4a/report.html` 或 `report.md`。完整逐圖資料、OCR 原文／信心值／座標、欄位證據、候選路徑、缺值與衝突、原圖與資料庫稽核在 `results.json`。

辨識其他單張 PNG 或整個資料夾：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.ocr_static --input 'C:\圖片資料夾' --output reports/another-ocr
```

其他圖片沒有人工標註時，準確率顯示未標註，不用模型信心值冒充準確率。`--no-cache` 保證重新執行模型；預設快取只保存原始 OCR 結果，以圖片 SHA-256＋引擎版本作 key，不以檔名識別圖片。解析與資料庫查詢每次重跑。

`--database` 指定既有 SQLite；`--player-name` 可更換 TC；`--models` 指定本機模型目錄。`--pack-id` 僅用於整批圖片都已人工確認同一 Pack 的情況，例如：

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.ocr_static --input 'tests/專案測試圖片/相同歌名2.PNG' --pack-id 29 --output reports/phase4a/joint-a --labels tests/fixtures/phase4a_ground_truth.json
```

Pack 29 的上下文來自使用者已確認的案例，不是從其他截圖、檔名或 Artist 推測。不同 Pack 圖片應分開執行，不能將這個參數套到混合批次。

## 新環境安裝與相容性

已在 Windows 10 build 19045／CPython 3.11.5 x64 安裝並執行：RapidOCR 3.10.0、ONNX Runtime 1.24.1（cp311-win_amd64 wheel）、PP-OCRv5 mobile detection＋recognition，CPU，無需 CUDA／Paddle／Torch。

```powershell
py -3.11 -m venv .venv-ocr
.\.venv-ocr\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

OCR 套件只安裝在專案的 `.venv-ocr`，不改系統 Python。首次模型下載約 20.5 MiB 至 `.ocr-models`，辨識時不傳送圖片。下載完成後可離線使用；`results.json` 保存實際模型 SHA-256、套件版本與 provider 資訊。

[RapidOCR 3.10.0](https://pypi.org/project/rapidocr/3.10.0/) 支援 Python 3.11；[ONNX Runtime 1.24.1](https://pypi.org/project/onnxruntime/1.24.1/#files) 有對應 Windows wheel。[PP-OCRv5 文件](https://paddlepaddle.github.io/PaddleOCR/v3.0.0/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5.html) 說明繁中、日文與英文能力；[RapidOCR 設定方式](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/how_to_use_ppocrv5/) 用於指定這個模型版本，避免套件預設變更成其他模型。

GUI／官方匯入／歌曲掃描均不需要這份 requirements；使用一般 Python 可繼續執行，不會匯入 OCR 引擎或下載模型。

## 模組與資料流

- `ocr_engine.py`：可選套件的延遲載入、ONNX CPU 模型、文字／信心值／座標與圖片雜湊快取。
- `ocr_screens.py`：OCR 錨點分類；選歌高亮行與 E／N／H 分頁；左側遊戲基本資料；可見 Pack 清單；本人結算列與房主標記。分類與解析不接收檔名或人工答案。
- `ocr_matching.py`：直接以 SQLite `mode=ro`＋`query_only=ON` 查詢所有同名來源 Title／已確認別名；保留全部 Song ID 與路徑，不遷移、不選第一首、不合併。
- `ocr_static.py`：單張或批次 CLI、逐圖錯誤隔離、JSON／Markdown／HTML、人工標註比對、全表與原圖前後稽核。

`tests/fixtures/phase4a_ground_truth.json` 是逐張目視標註，使用原圖 SHA-256 限制評估對象。答案只在所有 OCR／匹配完成後用於計算準確率，不能輸入辨識或候選決策。

## 實測與限制

開發樣本實測：畫面分類 22／22；可見選歌欄位 29／29（包含 3 個 Artist）；被 Pack 清單遮住的 6 個欄位全保持 NULL；本人結算狀態 4／4。六項主要選歌欄位中可見者為 26／26。這些是同一批開發樣本完全相同率，不代表未知畫面的泛化準確率。

逐圖比率只包含已標註的畫面類型、選歌欄位與本人結算狀態，不代表全畫面文字正確率。可見 Pack 原文及截斷名稱補全候選另列，沒有冒充已選 Pack，也未納入這 29 個欄位的分母。

5 張選歌截圖全部是 HARD。EASY／NORMAL 使用同一分頁高亮流程，但缺少實際截圖驗證。這批已選主標題都是英文；日文 Artist「川田まみ」、中文 UI 與 Pack「osu音樂」已讀到，中文／日文主歌名仍需要真實選歌樣本。不能僅依使用者說明，宣稱已有官方選歌畫面與三難度的完整測試。

選歌清單的紫色高亮決定目前行，不能把第一行當已選歌曲。JOINT 兩個版本分別讀得 HARD LV17／821／96秒與 HARD LV20／929／95秒，第二個版本並非第一行。

目前正式庫有 5 首原始 Title 為 JOINT；來源 Pack 未確認時全部列出。明確指定 Pack 29 後只剩 990／1004，但兩者都沒有 GameDifficulty，schema 5 也沒有已驗證歌曲時間，因此仍待人工確認。使用者提供的 JOINT 數值只作測試對照，不偷偷當成已驗證資料庫值。

黑色清單中的名稱可能截斷，例如 `Otaku's Dream 9th Anime M`；JSON 同時保留原文與可能的完整 Pack 候選，不能將補全結果或可見清單位置當成目前選中 Pack。隱藏清單的單張截圖不會自動沿用上一張 Pack。

其他限制與失敗原因：

- 約 1024×800、含 Windows 標題列的這款皮膚。其他解析度僅按比例正規化；未驗證裁切、全螢幕、其他皮膚或 UI 佈局。
- 原圖不改寫；縮放／裁切僅在記憶體處理，另外產生的檢視圖存於 reports，並核對原始 SHA-256。
- 字形、遮蔽、低信心、未知 Pack、缺少資料庫遊戲值都保留原因。繁簡字、OCR 誤字不自動翻譯／改成資料庫名稱；未命中保持待確認。
- 多人結算依暱稱找到本人，別人的 GAME OVER 不影響 TC；底部 LV 不作本人難度。遊戲中不提前宣告失敗或完成，也不計算成績。
- 官方歌曲可查到候選，但不以 Official 分類代替已確認來源 Pack，也不以 CSV dur* 或音樂長度代替遊戲時間。
- 不開發候選人工選擇 GUI、不保存成績綁定、不修改正式庫。後續人工確認、時間欄位 migration 與即時流程仍依既有 Phase 4 規格處理。

## 回歸測試

```powershell
$env:DANCE_SAMPLE_ROOT = 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -X utf8 -m unittest discover -s tests -v
```

新增測試覆蓋同名不同資料夾／Pack、多語名稱、缺 Pack／難度／時間、±1 秒衝突、低信心、多候選、非本人 GAME OVER、房主標記、唯讀拒絕寫入、人工答案雜湊不符與全部原圖完整性。正式 DB 的 ID、名稱、路徑、難度、歷史以全表稽核驗證不變。

不依賴報告自稱的準確率，可再獨立驗證原圖、所有人工欄位、JOINT 候選與目前正式庫稽核：

```powershell
python -X utf8 -m tools.validate_phase4a
```
