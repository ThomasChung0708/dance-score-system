# Phase 5F.1：真實遊玩數字 OCR 與歌曲鎖定

日期：2026-11-02。**v0.15.1／SQLite Schema 10，無 migration。**

開發前確認仍為 v0.15.0，原始碼、測試及文件中沒有 Phase5F.1 實作。沿用既有 RapidOCR／ONNX CPU 模型、Python 3.11、監控、匹配及正式提交服務，沒有更換框架或新增 OCR 套件。

本次使用 2026-10-27 保存的〈新世界Remix〉選歌 PNG、結算 PNG、Session 及原始 Review。**只有一張該場結算原圖，因此本輪不是新的實際遊玩，也不能證明真實雙幀自動提交已通過。**

## 修正前後

以下為相同選歌／結算原圖的模型讀值；歷史 Session 前值亦另外保存在 JSON。信心是模型輸出，不等於辨識正確率。

| 欄位 | 原始整頁讀值／信心 | 改善後讀值／信心 |
|---|---|---|
| Miss | NULL；舊寬 ROI 曾讀到1，但信心僅約0.74～0.82 | **1／0.98953** |
| 選歌 BPM | 132／0.84377 | **132／0.99751** |
| 結算歌曲 LV | 10／0.90790 | **10／0.99628** |

官方 K 唯一安全匹配為 **Song2730／HARD／LV10／935 notes**。來源來自原 Session 已確認的官方 K 範圍，匹配仍核對歌名、遊戲難度、LV、已觀測音符與 BPM；沒有把「唯一同名搜尋結果」直接當確認。靜態狀態機回放可在 PLAYING 前鎖定 Song2730，但不把兩次讀同一張圖說成新的真實多幀。

靜態計分預覽為 P931／C3／B0／M1，EX1865、自訂MAX1870、MAX -5、CLEAR。沒有把這個預覽寫成正式成績。

## 原因與 ROI 改善

- Miss的單一細數字在75像素寬的儲存格中占比太小，原本整格重讀保留太多背景。先以灰白前景找字形範圍，再裁切、補白邊及放大，能改善模型輸入。
- 選歌BPM的數字為青色，原本與白色BPM標籤一起讀。以實際OCR標籤座標定位，只保留青色前景，不依賴資料庫答案或指定數字。
- 結算LV以實際「LV:」文字錨點定位數字，分開彩色標籤與灰白數字；仍然是歌曲難度等級，沒有解析人物等級。

每個弱數字使用兩種固定前處理與邊界配置。**兩次模型都至少0.95且數值一致才接受，最終信心採實際讀值的較小值。** 同圖的兩種裁切不是獨立遊玩畫面，也不當成統計上的信心提升。與原頁已有數字或另一視圖矛盾時保留待確認；NULL不補0，字母I／l／O不轉成數字。

原始頁面tokens、raw_fields、ROI重讀tokens、原始信心、字形範圍、前處理參數及視圖雜湊均保留。新Session也保存選歌的raw_ocr_fields及數字ROI證據。

## 多幀安全條件

新監控結果額外保存同一Session的兩個新擷取畫面，只有以下條件成立才標記多幀一致：

1. Capture ID與完整畫面雜湊各自不同，間隔大於0且不超過15秒。
2. 本人、歌名、完成狀態、P/C/B/M、Combo、遊戲分數、評級，以及單人歌曲LV均相同。
3. 每個必要欄位在各畫面均有至少0.95的實際模型信心，無錯誤原因；低信心重複再多次也不加分。
4. 原始PNG、像素雜湊、檔案SHA及Session結算畫面關聯在提交時再次驗證。

最多緩存兩張畫面，首次保存新結算時才寫入 `result-observations/`，不保存每一幀。畫面狀態或Session變更、擷取時間過久會重置。不同玩家、數字或LV有矛盾就保留Pending，不挑最高信心來掩蓋衝突。

VERIFIED_AUTO原有身份、本人列、NULL、完成狀態、CSV差異部署閘門、跨場次基準衝突與交易／冪等條件全部保留。新解析器只有一張圖時不能繞過雙幀條件。人工確認流程仍可使用。

## 舊資料與相容性

- 不重跑、不覆寫舊Pending Review。升級自動模式設定時，既有Session加入排除清單；舊待確認資料仍由人工處理。
- 不改既有PlayRecord、Song ID、My Best、Best Lamp或歷史MAX。
- 新欄位在Session／Review JSON及自動模式sidecar中；SQLite仍為Schema10。
- 本輪正式庫與隔離副本全部表雜湊保持相同；原圖、既有Session與Review檔案雜湊保持相同。

## 測試

**414／414通過，0略過：399項原有測試＋15項新增測試。**

新增測試包含：雙視圖高信心、低信心不累加、0／NULL區分、原頁與視圖矛盾、字母不修補為數字、人物文字不是LV錨點、相同畫面不算雙幀、原圖驗證／損壞、多人／數字／LV衝突、跨Session與過期重置、新可信結果透過原交易提交及冪等、舊Pending升級排除、新解析器單圖不能自動提交。

受控雙幀整合測試使用生成圖與模型測試替身，只在一次性資料庫提交；不能宣稱是真實OCR實玩成功。

實際模型重新讀取五張既有結算原圖，**55／55預期欄位符合人工標註**，其中包括旁觀者的安全NULL；兩張JOINT的LV／821與929顆／96與95秒保持正確。Python編譯檢查通過。

證據：[JSON結果](../reports/phase5f1/validation.json)、[全部測試](../reports/phase5f1/final-tests.log)、[開發前測試](../reports/phase5f1/baseline-tests.log)、[實際模型回歸](../reports/phase5f1/real-regression.log)。

## 尚待驗證與下一次實玩

尚未取得修正後的兩張新鮮真實結算畫面，亦尚未實測修正後自動提交、動畫與實際資源占用。不同解析度／角色特效／多人與GAME OVER需另做真實回歸；本輪沒有以靜態或受控測試取代它們。

首次兩個結果畫面仍低信心時會保存Pending，不會在建立Review後悄悄重算、覆寫或自動升級。這是目前保留的安全限制。

下一輪建議：

1. 建立新隔離副本與新monitor目錄，使用官方K及VERIFIED_AUTO；不要重用舊Pending。
2. 選〈新世界Remix〉HARD，停留至少10秒；確認自動鎖定Song2730、LV10、935顆、BPM132，保留選歌原圖。
3. 使用者自行正常完成；結算停留至少15秒，讓兩次新擷取與OCR完成。
4. 核對新Session的兩張result-observations、本人TC、P/C/B/M與單人LV；全部門檻通過後僅在隔離庫自動提交一筆。
5. 核對EX／自訂MAX、played_at、My Best與Lamp各自更新、Recent與動畫；同一結算停留及重新整理不新增第二筆。正式庫不寫入測試紀錄。

可使用既有測試工具（範例輸出目錄必須尚未存在）：

```powershell
.venv-ocr/Scripts/python.exe -m tools.phase5f_live --run-name live-gameplay-20261102-01 --prepare --fresh-production
.venv-ocr/Scripts/python.exe -m tools.phase5f_live --run-name live-gameplay-20261102-01 --source-mode official --mode VERIFIED_AUTO
```

## 修改檔案

- `dance_results/ocr_numeric.py`：像素數字ROI與可稽核雙視圖讀值。
- `dance_results/result_ocr.py`：本人儲存格、歌曲LV重讀及原始資料保留。
- `dance_results/monitor_ocr.py`：選歌BPM分離，保留原始觀測與OCR呼叫數。
- `dance_results/monitor_tracking.py`：鎖定選歌原始／ROI證據。
- `dance_results/ocr_temporal.py`：有界雙幀一致性與原圖再次驗證。
- `dance_results/monitor.py`：新結果觀測、首次保存雙幀證據。
- `dance_results/verified_auto.py`：保留原條件，加入雙幀檢查與升級排除舊Session。
- `dance_results/__init__.py`：版本0.15.1。
- `tests/test_phase5f1.py`：15項隔離回歸。
- `tools/phase5f1_probe.py`、`tools/validate_phase5f1.py`：原圖分析、隔離匹配與正式資料稽核。
- `docs/PHASE5F1_OCR.md`、`README.md`：結果及下一次實玩步驟。
