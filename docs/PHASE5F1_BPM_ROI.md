# Phase5F.1 選歌 BPM 拆框修補

2026-11-08，v0.15.2／Schema10，無 migration。本輪僅修正辨識與直接相關的驗收診斷；沒有啟動實玩監控、建立遊玩 Session／Review、提交成績或改寫正式庫。

## 已確認根因

使用 `reports/phase5f/live-accept-20261108-01/selection-evidence/670ac9d410896e882c7fde2c1ae3797efeda199a056ea282da6d4e2c24bcf35d.png` 原圖，沿 `Analyzer.analyze(image, state='SONG_SELECT', full=False)` 重現，確實為 ROI 模式，沒有全畫面 fallback。

該路徑的座標已正確還原到1024×800標準畫面；原始模型框為：

| token | 模型信心 | 原始框四點 |
|---|---:|---|
| BPM | 0.94900 | (67,448), (125,448), (125,468), (67,468) |
| 132 | 0.99808 | (126,450), (173,450), (173,471), (126,471) |

`parse_selection` 原先只在**同一個 token**找 `BPM` 後接數字。拆框時兩個 token 都無法符合，因此輸出 NULL／not_readable／空 evidence。後續數字 ROI 以 evidence 作錨點，空證據使重讀沒有觸發。失去值及可用錨點的位置是選歌欄位解析，並非資料庫缺 BPM，也不是座標還原失敗。

全畫面與 ROI 拼接輸入會得到不同的偵測分框，是本次實際觀測；沒有進一步宣稱模型為何分框的內部機制已被證實。全畫面讀值不作即時路徑通過的替代證據。

## 最小修改

- 保留既有合框格式（含合併其他 metadata 的 token）。新增 BPM 標籤與右側唯一純數字的配對，使用框高度、行重疊、水平間距與中間阻擋欄位；不跨行收取音符數，不跳過 LV 等標籤，不將時間當數字。多候選保留 NULL。
- 配對保留兩個原始 token、座標及較低信心，不用數字框的高信心覆蓋標籤信心。標籤不可靠或解析歧義時不觸發數字重讀。
- 合法配對提供錨點，沿用既有青色數字 ROI／兩種視圖模型辨識。兩次都至少0.95且數值一致才接受，最終信心取較低值。沒有降低既有門檻，也沒有依重複次數加分。
- 不加入歌曲或數字答案，不從資料庫補 BPM。132／2730只出現在案例預期與測試斷言中。

## 真實原圖、實際模型結果

使用既有 RapidOCR3.10.0／PP-OCRv5 mobile／ONNX Runtime1.24.1 CPU，無新增套件。相同即時 ROI 呼叫路徑、未修改原 PNG：

| 項目 | 修正前 | 修正後 |
|---|---|---|
| 原始模型文字 | 分離 BPM／132 | 分離 BPM／132 |
| 欄位解析 | NULL，無 evidence | 132，0.949，保留兩個框 |
| 數字 ROI | 未觸發 | 132／0.99882、132／0.99512 |
| 最終 BPM | NULL／not_readable | **132／0.99512** |
| 實際 ROI／全畫面 fallback | true／無 | true／無 |

此次原圖處理耗時0.863秒（單次測量，不是平均或高分位效能）。沒有量測 CPU／記憶體，不提供推估值。

最後原圖讀值另包含：新世界Remix／0.99386、HARD／0.99482、LV10／0.99896、935顆／0.99544。歌曲時間2:10／0.94254保留原始低信心，未修改；本次官方 K 選歌驗收檢查不將時間列為必要欄位。這些是同一張舊原圖，不能當成新的獨立畫面。

## 身份確認與驗收契約

既有 `confirmed_official_catalog` 代表：已確認 official 來源、可信歌名／難度／LV與官方 K 資料唯一匹配；已觀測的音符數／BPM也須相符且可信。原契約允許沒有觀測到 BPM，故修正前後皆可得到 Song2730。它**不代表完整選歌驗收通過**。

本輪不改 `ReadOnlyCatalog.match_official`、正式提交或 VERIFIED_AUTO 政策。若日後要讓正式提交一律要求 BPM，應另行評估 metadata 遮擋、舊流程及不同畫面的相容性，不在這次修補擴大規則。

驗收工具新增 `preplay_readiness`：分開列出 `matching_identity_confirmed`、`observed_metadata_complete`、`complete_selection_observation` 與原因。完整選歌觀測要求歌名、難度、LV、音符數、BPM各自有效且信心至少0.95，沒有錯誤原因。因此本例修正前身份 confirmed，但完整觀測 false／`bpm_missing_or_invalid`；修正後兩者 true。

此欄僅是選歌資料準備狀態，仍須另核對預期歌曲、PLAYING前鎖定、兩張新結算原圖及所有提交閘門；不是整場實玩 PASS。它記錄在新實驗的 `observations.jsonl`、`selection-evidence/*.json`、`status.json`，沒有新增大型 GUI。

## 測試、證據與資料保護

實際執行（工作目錄為專案根目錄）：

```powershell
.venv-ocr/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_bpm_selection.py -v
.venv-ocr/Scripts/python.exe -X utf8 -m tools.validate_bpm_roi
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
.venv-ocr/Scripts/python.exe -X utf8 -m unittest discover -s tests -v
```

- 新增13項回歸全數通過：拆框、合框、小數、缺值、低信心、鄰近音符數／LV／時間／遠處數字、中間欄位阻擋、多候選歧義、低信心標籤不觸發重讀、身份與完整觀測分離。這組使用手工 token、快取實際 token及局部模型替身，**不是新的實際模型或實玩測試**。
- **427／427通過，0略過，124.825秒**，含原有414項。見 `final-tests.log`。
- 實際模型另測本次失敗原圖，相同即時 ROI 成功；五張既有選歌原圖各跑全畫面及 ROI 請求，共 **10／10 BPM預期值正確**。JSON逐項列實際 ROI／fallback，未把有 fallback 的案例冒稱成純 ROI。原圖雜湊全部保持相同。
- 正式庫與新隔離副本全部資料表的筆數及內容雜湊相同；正式 PlayRecord **23→23**、PlaySession **23→23**。原失敗 run與既有正式 Session／Review等226個基準檔案雜湊不變。My Best／Lamp沒有重算，Schema10不變。
- 本輪未執行新視窗擷取、真實遊玩、真實雙幀、自動提交、動畫或 GUI實玩驗證。

證據位於 `reports/phase5f1-bpm-20261108/`：`before.json`、`before-roi.json`、`final-roi.json`、`validation.json`、`final-audit.json`、`targeted-tests.log`、`final-tests.log`、`model-regression.log`、`ISOLATED.sqlite3`。原始模型 tokens、框、信心、原欄位、數字 ROI視圖及最終欄位均在 JSON保留。

模型工具是本次一次性蒐證工具，保護既有 `ISOLATED.sqlite3`，重跑會拒絕覆寫；若需另做模型實驗，先另建新的輸出位置及基準，不能刪除舊證據來重跑。

## 下一次隔離實玩（尚未啟動）

建議新名稱 `live-accept-20261108-02`；本輪檢查該目錄尚不存在。實際開始前再次檢查，已存在就換新名稱。先保存當時正式庫唯讀基準，確認新隔離庫與 monitor路徑、無舊 Pending；不能僅依本輪基準推定正式庫之後沒有變動。

```powershell
.venv-ocr/Scripts/python.exe -m tools.phase5f_live --run-name live-accept-20261108-02 --prepare --fresh-production
.venv-ocr/Scripts/python.exe -m tools.phase5f_live --run-name live-accept-20261108-02 --source-mode official --mode VERIFIED_AUTO
```

`--prepare --fresh-production`唯讀備份當時正式 SQLite到新隔離庫；只複製人工對應資訊，不複製舊 Session／Review。`serve`將資料庫與 monitor根目錄傳入隔離服務。開始前仍須檢查新目錄與服務路徑；其他程序不停止。

1. 開啟工具實際列印的 `/monitor`網址（預定40510，若占用先回報，不擅停其他服務），檢查 `status.json`與已保存的選歌證據；不能只看 GUI Song ID confirmed。
2. 使用者選〈新世界Remix〉HARD停留至少10秒，先不要開始。確認 Song2730／HARD／LV10／935 notes／BPM132，完整觀測 true且均達門檻，在PLAYING前已穩定鎖定；錯誤就停止此成功案例。
3. 確認環境後才由使用者正常完成，結算停留至少15秒並保留畫面；核對本人列、當次P/C/B/M、Combo、遊戲分數、評級、完成狀態及歌曲LV，不能沿用舊場數字。
4. 兩張真正新擷取的完整結算圖：Capture ID及完整畫面雜湊分別不同，時間差>0且≤15秒，必要欄位各自≥0.95且一致，通過原提交閘門後隔離庫才新增一筆。任一不足保留Pending，不重算覆寫舊Review湊成功。
5. 核對當次EX、自訂MAX、played_at、My Best、Lamp、Recent、動畫與刷新去重，再比對正式庫當時的測前／測後基準；區分自動檢查與使用者目視核對。

新的實玩自動提交驗收**仍未通過／尚未執行**，靜態辨識已修復不是其替代證據。等待使用者配合，沒有在背景啟動本輪監控。

## 獨立待查事項

UI來源顯示與後端來源不一致、停止後服務exit code1，以及正式monitor日誌另有活動，均維持獨立待查；本輪未順手修正或停止其他程序。

## 修改檔案

- `dance_results/ocr_screens.py`：拆框BPM配對與原始錨點。
- `dance_results/monitor_ocr.py`：僅對有效且無解析錯誤的BPM錨點啟動既有數字重讀。
- `tools/selection_acceptance.py`（新增）、`tools/phase5f_live.py`：完整選歌觀測診斷，與身份確認分開。
- `tests/test_bpm_selection.py`、`tests/fixtures/phase5f1_bpm_split.json`（新增）：13項回歸及真實快取tokens。
- `tools/validate_bpm_roi.py`（新增）：實際模型、唯讀匹配與隔離稽核。
- `dance_results/__init__.py`、`README.md`、本文件：版本及證據說明。
