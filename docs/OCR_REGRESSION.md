# 固定 OCR 回歸考卷與失敗擷取

本輪只增加評估與蒐證，不修改 OCR 分類、解析、信心門檻或監控決策。
這是開發樣本的完全相同比率，不代表未知圖片的泛化準確率。

## 執行

在專案根目錄執行：

```powershell
.\OCRRegression.cmd
.\OCRRegression.cmd --no-cache
# 明確審閱目前結果後，記錄當前基準（可以包含失敗）：
.\OCRRegression.cmd --update-baseline
```

等價 Python 入口：

```powershell
.venv-ocr\Scripts\python.exe -X utf8 -m tools.ocr_regression
.venv-ocr\Scripts\python.exe -X utf8 -m unittest discover -s tests
```

需要既有 `.venv-ocr`、`.ocr-models` 兩個 PP-OCRv5 ONNX 模型，以及
`data/library.sqlite3`。模型不存在時停止，不下載、不安裝套件。預設本人暱稱
`TC`，可用 `--player-name` 指定；標籤中的預期暱稱不會送入辨識器。

工具使用 `dance_results.ocr_static` 既有的模型、`cached_page`、座標正規化、
分類、選歌解析與結算狀態函式。結算欄位另外呼叫既有
`result_ocr.recognize_result`，保留它自己的 ROI 重讀。這是靜態完整頁面路徑；
不宣稱測試了即時監控 ROI、雙幀、Session 或自動提交。

預設重用 `reports/phase4a/cache/` 的原始頁面 tokens，以圖片 SHA-256 和既有
engine profile 為 key；每次重跑分類與解析。`--no-cache` 直接重新推論頁面，
不讀寫頁面快取。結算 badge／數字 ROI 依既有流程重新推論，不快取解析答案。

SQLite 使用靜態入口同一個 `ReadOnlyCatalog`：`mode=ro` 加
`query_only=ON`，不 migration、不寫入歌曲庫；前後做資料表稽核與完整性檢查。
原圖前後核對 SHA-256。

## 輸出與基準

每張標註圖片印出 `PASS`／`FAIL`，失敗列出每個欄位的 expected／got。
本機沒有該圖片時印出 `SKIPPED`，不列入分母，也不因單張圖片缺失而判失敗。
沒有標籤的圖片不加入考卷。相同圖片在兩份既有標籤中的資料合併，只考一次。

`reports/ocr_regression/latest.json` 保存時間、引擎、逐圖比對、缺失、欄位統計、
回歸差異及唯讀稽核結果。`reports/` 已被 Git 忽略。

評分規則：

- 非 null 答案做完全相同比對；不模糊匹配、不改大小寫、不以信心當準確率。
- null 答案記為未評估，沒有進入任何分子／分母。這與舊驗證工具將安全 NULL
  拒絕擷取另作斷言不同；本考卷不靠 NULL 增加正確數。
- `perfect`、`cool`、`bad`、`miss` 分別統計；`judgments` 是這四欄比對合計。
- 已標註的 Perfect 和 Cool 都是非負整數時，自動衍生預期 EX：Perfect × 2 + Cool。
  實際 EX 同樣由辨識值計算，不取遊戲原始分數，也不從標籤補缺值。
  明確標註 `ex_score: null` 時不評估 EX。
- `screen_type`、`title`、`difficulty`、`game_level`、`bpm` 及其他有標註的欄位
  各自統計；沒有可評估答案時顯示 `0/0 (N/A)`。

`tests/fixtures/ocr_regression_baseline.json` 保存各欄 correct／total／accuracy，
以及當時各樣本的 PASS／FAIL、SHA-256 與逐欄比對。預設以整數比例比較欄位準確率；
任一有評估資料的欄位下降，或既有 PASS 樣本現在 FAIL（或標籤被移除），
exit code 為 **1**。新增／既有失敗可存在於誠實基準中，沒有退步時為 **0**。
缺圖的樣本不觸發 PASS→FAIL；比較時同時從基準的分子／分母扣除那些缺圖，
避免只因本機少了一張容易圖片就誤報回歸。報告欄位統計仍是當次實測總數。
標籤衝突、圖片 hash 不合、缺基準、缺模型／SQLite 或安全檢查失敗為 **2**；
hash 不合不拿錯圖計分，也不覆寫基準。

`--update-baseline` 記錄當次實測值，不要求所有樣本 PASS。
缺圖不寫進基準的樣本清單。只在有意重訂基準時使用；不可用它掩蓋退步。
禁止將答案或檔名送進辨識／匹配器，或針對考卷調碼灌高數字。

## 加入一張新失敗圖

1. 從 `data/ocr_failures/` 或其他本機來源挑選原始 PNG，複製到
   `tests/專案測試圖片/`，例如 `round2/failed-result.png`。也支援子資料夾。
2. 自己目視核對原圖，建立一份 `tests/fixtures/ocr_labels/failed-result.json`。
   一份 JSON 對應一張圖片；不要複製 OCR 結果當作人工答案。
3. 用 `Get-FileHash` 取得該 PNG 的 SHA-256，填入下列格式。
4. 執行考卷，先檢查錯誤原因；不要為了通過而修改原圖、答案或辨識邏輯。

```powershell
Get-FileHash 'tests/專案測試圖片/round2/failed-result.png' -Algorithm SHA256
```

```json
{
  "image": "round2/failed-result.png",
  "sha256": "填入原圖的64位十六進位SHA256",
  "screen_type": "results",
  "fields": {
    "title": "目視核對的歌名",
    "game_level": 10,
    "perfect": 931,
    "cool": 3,
    "bad": 0,
    "miss": 1,
    "max_combo": null,
    "game_score": null,
    "grade": null,
    "completion_status": "COMPLETED",
    "player_name": "TC"
  },
  "annotation": "人工核對方式與畫面遮擋情況"
}
```

上例只存在文件中，不是考卷樣本。`image` 相對於 `tests/專案測試圖片/`，
使用 `/`，不可用絕對路徑或 `..`。`image`、`sha256`、`screen_type`、`fields`
四項必填；不確定的答案用 null 或省略該欄，不猜數字。`annotation` 是備註。
`screen_type` 使用既有分類名稱：`song_select`、`results`、`waiting_room`、
`gameplay`、`spectating`、`lobby`、`profile`、`login`、`inventory`、`unknown`。

選歌欄位沿用 Phase4A：`title`、`difficulty`（EASY／NORMAL／HARD）、
`game_level`、`total_notes`、`bpm`、`duration_seconds`、`artist`。
結算沿用 Phase4C：`title`、`game_level`、四種判定、`max_combo`、`game_score`、
`grade`、`completion_status`、`player_name`。多人結算底部歌曲 LV 是 HOST
顯示值；不能把它標成自己的另一個難度。不能從結算猜出沒有顯示的 E/N/H。
其他欄位可使用同名鍵；解析器沒有輸出時會得到缺值 FAIL，不能偷偷忽略。

可另在 JSON 頂層標 `local_player_status`（沿用 Phase4A 的
`failed`／`completed_observed`／`unknown`），它與結算
`completion_status` 的 `FAILED`／`COMPLETED` 分開計分。
兩份舊 ground truth 維持原樣；若新標籤對同一圖片提供衝突答案／hash，
工具報錯而非依檔案順序覆蓋。

只提交標籤與程式。**永遠不要提交 `tests/專案測試圖片/`、`data/`、`reports/`。**

## 即時監控失敗擷取

正式監控使用 `data/ocr_failures/`；另一份資料庫的隔離監控使用該監控目錄下
的 `ocr_failures/`。第一次需要擷取才建立目錄，不啟動另一個監控程序。

當辨識器拋例外、分類 unknown／error、原始欄位缺值／低信心（<0.95）／
帶解析原因，或既有結算錯誤／雙幀診斷指出 Pending 不確定性時，另存
同名 PNG 和 JSON。這是旁路診斷條件，不修改正式門檻或 Pending／提交決策。
一般 MANUAL 等待審核而沒有 OCR 不確定性不會只因 Pending 就擷取。

JSON 包括 UTC timestamp、reason 列表、screen_type_guess、各欄原始 confidence
及 key parsed fields；不保存整份 tokens／資料庫資料。辨識例外時無法取得的
類型／信心保留 null／空物件，不捏造分類信心。

每個監控實例最短五秒一次，不同原因也受此限制；連續相同原因三十秒內
不再保存。失敗寫入也限制重試頻率。目錄最多 **500 個檔案（通常250組）**，
包含重啟前與不完整檔案，滿了停止新增，絕不刪除或覆蓋既有證據。
所有擷取處理與掛鉤均包住例外；權限、磁碟滿、PNG 或 JSON 寫入錯誤不影響
原本監控的成功／錯誤處理。
