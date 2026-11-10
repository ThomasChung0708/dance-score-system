# 固定 OCR 回歸考卷與失敗擷取

Round 2.5 增加失敗診斷去噪、未完成結算幀處理、評級字形 ROI 與資訊性計時。
Round 2.6 增加完整結算保留、唯一一致候選綁定、Pack 推論持久化與單字元 footer 重讀。
自動提交的信心門檻仍為 0.95，仍要求兩張新鮮且欄位相同的結算幀。
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

每張實際執行 OCR 的樣本另印 `OCR … ms`，JSON 樣本保存 `ocr_ms`；摘要
`timing` 保存 `median_ms` 與最近秩法的 `p90_ms`。計時涵蓋辨識入口（包括
必要 ROI／解析），不包括模型初始化、圖片 hash、評分與資料庫稽核。
**有意義的推論耗時必須用 `--no-cache`**；預設頁面快取會跳過頁面推論，
因此預設數字只表示這次含快取的入口耗時。計時僅供參考，完全不參與
PASS／FAIL、欄位準確率或基準寫入／比較。缺圖／hash 不符沒有 OCR 耗時。

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
另可選填 `selected_pack`，值為畫面上的 Pack 完整名稱；此欄是診斷證據，
不會授權自動綁定或提交成績。
結算沿用 Phase4C：`title`、`game_level`、四種判定、`max_combo`、`game_score`、
`grade`、`completion_status`、`player_name`。多人結算底部歌曲 LV 是 HOST
顯示值；不能把它標成自己的另一個難度。不能從結算猜出沒有顯示的 E/N/H。
其他欄位可使用同名鍵；解析器沒有輸出時會得到缺值 FAIL，不能偷偷忽略。

可另在 JSON 頂層標 `local_player_status`（沿用 Phase4A 的
`failed`／`completed_observed`／`unknown`），它與結算
`completion_status` 的 `FAILED`／`COMPLETED` 分開計分。
兩份舊 ground truth 維持原樣；若新標籤對同一圖片提供衝突答案／hash，
工具報錯而非依檔案順序覆蓋。

動畫中的結算可在頂層加 `"expect_incomplete": true`，`screen_type` 必須是
`results`，`fields` 可為空物件；不要替半畫出的數字猜答案。工具要求仍分類為
results，且解析器明確回報本人列／必要數字不完整，或無法產生整列高信心的
數字、評級、暱稱與完成狀態，才會 PASS。完整高信心列、未知分類、辨識例外
都不能冒充未完成而通過。這個斷言獨立計分，不改動 null 欄位的分母規則。
`result_diagnostics` 另外保留欄位信心、原因與 `incomplete_result`。

四張 realplay 標籤中，`realplay_result_anim_frame0` 使用上述斷言；frame1
標註完成的 Crisscrosser 結算。`realplay_grade_app` 標註 A++ 上標評級；
`realplay_multi_lv23` 只評 TC 本人列，footer 的 HOST LV 以 null 排除。
標籤答案／檔名均不參與辨識。評級低信心時，額外對橘色字形做兩個不同留白
的裁切，保留上標 ++；兩個模型讀值都須達原門檻、彼此與原有非空讀值一致，
信心取實際模型最小值，不透過重複讀取加分。缺字形、低信心或矛盾都不修猜。

即時雙幀檢查忽略缺本人列或必要數字為 null 的幀，也不讓它消耗 tracker 的
結果確認次數或提前保存不可覆寫的結算。仍要求兩張不同 capture／hash、間隔
不超過原有 15 的完整高信心相同讀值；非 null 的真實衝突與低信心照常待審核。
靜態考卷只檢查單圖；雙幀與監控流程由合成測試另外驗證。

只提交標籤與程式。**永遠不要提交 `tests/專案測試圖片/`、`data/`、`reports/`。**

## 即時監控失敗擷取

正式監控使用 `data/ocr_failures/`；另一份資料庫的隔離監控使用該監控目錄下
的 `ocr_failures/`。第一次需要擷取才建立目錄，不啟動另一個監控程序。

當辨識器拋例外、分類 unknown／error、實際送出必要欄位缺值／低信心／
帶解析原因，或既有結算錯誤／雙幀診斷指出 Pending 不確定性時，另存
同名 PNG 和 JSON。這是旁路診斷條件，不修改正式門檻或 Pending／提交決策。
一般 MANUAL 等待審核而沒有 OCR 不確定性不會只因 Pending 就擷取。

欄位列表與 0.95 門檻由 `ocr_policy` 與 `verified_auto`／雙幀檢查共用。
選歌檢查 title／difficulty／game_level；已知 custom 來源才另檢查 total_notes／
duration_seconds。artist／bpm 不是必要欄位，不因它們缺值／低信心而擷取。
`pack_list_occludes_metadata`（連同該欄預期缺值）不擷取；Pack 訊號衝突仍擷取，
但 `selected_pack_uncertain` 與選歌 title／difficulty／game_level 缺值且理由僅為
`not_readable` 的過場訊號不擷取。多人結算的 HOST level 警告與欄位不是
自動送出阻擋條件，不擷取；其他結算 gate 原因仍保留。

JSON 包括 UTC timestamp、reason 列表、screen_type_guess、各欄原始 confidence
及 key parsed fields；不保存整份 tokens／資料庫資料。辨識例外時無法取得的
類型／信心保留 null／空物件，不捏造分類信心。

每個監控實例最短五秒一次，不同原因也受此限制；連續相同原因三十秒內
不再保存。unknown／error 的 `screen_unrecognized` 另有六十秒去重，不受中間
其他原因打斷；同幀例外仍保留。失敗寫入也限制重試頻率。目錄最多 **500 個檔案（通常250組）**，
包含重啟前與不完整檔案，滿了停止新增，絕不刪除或覆蓋既有證據。
所有擷取處理與掛鉤均包住例外；權限、磁碟滿、PNG 或 JSON 寫入錯誤不影響
原本監控的成功／錯誤處理。

可用 `python -X utf8 -m tools.replay_ocr_failures` 回放唯讀的
`data/ocr_failures_snapshot/*.json`，不執行 OCR、不讀寫曲庫。測試在快照目錄
缺失時跳過。121 份 Round 1 JSON 原本全部帶原因；以新規則重建為 31 份，
套用同一個六十秒 unknown 去重後為 **30 份**。輸出同時保留這兩個數字，
以免把去重誤認為解析改善。快照缺少 tokens／來源／完整模式資訊，不能據此
重建所有即時 gate 狀態；工具不猜 custom 來源，只有 HOST 警告可證明多人模式。

## Pack 選取證據與裁切樣本

完整「房主設置 SETTING」目前分類為 `song_select`。除了完整遊戲視窗，
選歌解析亦用 EASY／NORMAL／HARD 與 Group 錨點對齊單獨擷取的完整對話框。
只有左側清單的 ROI 樣本需明確標為 `screen_type: "pack_list_crop"`；
考卷在此模式只執行 Pack 清單分析，使用原圖座標，不執行全畫面分類。
這個模式是辨識路由，不把標籤中的 Pack 答案送進解析器。

`fields.selected_pack` 是選填的完全相同比對欄位：

- 名稱完全相同：PASS。
- 沒有值（None）或明確 uncertain：欄位為 UNCERTAIN，另計 uncertain 數量；
  預設整張樣本 FAIL。
- 標籤頂層可填 `"allow_uncertain": ["selected_pack"]`。只有此欄的不確定性
  被允許時，樣本狀態為 UNCERTAIN；其他任何欄失敗仍是 FAIL。
- 輸出不同 Pack 一律 FAIL，即使設定 allow_uncertain。allow_uncertain 目前僅接受
  selected_pack，合併標籤時仍檢查衝突。null 答案仍不評分。
- 欄位 correct／total 保留所有非 null 答案，UNCERTAIN 不算 correct；另外輸出
  uncertain 數量。既有 PASS 退為 UNCERTAIN、或允許的 UNCERTAIN 退為 FAIL
  仍視為回歸，不能因兩者 correct 都是 0 而掩蓋錯誤 Pack。

例如只有左列、目視無法證明選取狀態的圖片：

```json
{
  "image": "pack-list-crop.png",
  "sha256": "填入原圖的64位十六進位SHA256",
  "screen_type": "pack_list_crop",
  "allow_uncertain": [
    "selected_pack"
  ],
  "fields": {
    "selected_pack": "人工確認的完整 Pack 名稱"
  }
}
```

主要證據為右側可見歌名的精確正規化曲庫匹配：至少兩個不同歌名、兩首不同歌曲
同屬一個 Pack；若左側清單可見，還必須與其名稱候選一致。重複 OCR、同曲別名
或同名歌曲多筆資料不能單獨湊足兩首。多 Pack 符合時預設不確定，只有唯一且
一致的左列提示可作平手裁決。page n/m 保留原始 token、信心與頁碼作證據，
不以頁碼推測歌曲或曲庫缺項。未知歌名、截斷歌名不做模糊補字。

曲庫仍透過既有 ReadOnlyCatalog 的 `mode=ro`、`query_only=ON` 開啟；
重用 songs、來源原始歌名與已確認別名索引。缺少 Pack／歌曲映射時輸出
selected_pack_uncertain 與匹配診斷，絕不從標籤或檔名補答案。

左列提示保留相鄰掃描線的成對邊緣法：要求上下方向相反、橫跨大部分列寬，
而且強度顯著高於其他列。另加入保守的局部背景正規化色差法：

- 在列中心上下 0.38 倍列距內取樣，排除 OCR 文字框及其外擴 2 像素，
  並略去左右各 5% 的面板邊界。至少 24 個橫向位置且覆蓋 60% 列寬才有效。
- 以 RGB 中位數去除整列偏色後比較空間紋理；90% 像素的最大通道偏差
  必須不超過 12，且色度 MAD 換算的標準差不超過 6。只涵蓋平滑背景，
  不試圖從高變異唱片圖案猜選取狀態。
- 只比較三個列距以內的列（另容許 0.1 列距的 OCR 基線誤差），平均 RGB 亮度差
  須不超過 24；相同橫向位置的
  去色偏背景輪廓，其 90% 差異須不超過 12，且仍須有 60%／24 個共同位置。
- 色度採 `(R-G, B-G)`，消除共同亮度變化。候選列的色度強度必須比**所有**
  可比較鄰列至少多 8，色度向量距離至少多 12；至少需要兩個可比較鄰列。
  兩項門檻都再取與 `3 * hypot(自身雜訊, 鄰列雜訊)` 的較大值，且須嚴格超過。
  使用完整空間雜訊，不因像素數增加而縮小誤差。兩列同樣偏色不產生提示，
  不會把剩下的未偏色列反選為候選。色差法只接受唯一且 OCR 信心至少 0.9 的候選。

8／12 是 8-bit 通道範圍約 3%／5% 的最低可辨對比預算，遠大於單一量化階；
6 的雜訊上限及 12 的紋理容忍度將方法限制在平滑面板，24 的亮度容忍度約為
全範圍 10%，避免跨越明暗背景。這些是保守的工程門檻，並非選取正確率保證；
選用於合成測試與平滑背景假設，沒有依 Osu Transfer 圖片的測量值降低門檻。
三個列距保留局部性，兩個以上參照列避免單一鄰列充當基準；任何可比較鄰列
未達對比門檻都棄權，不能只挑最有利的兩列。

兩種方法提供不同候選時回傳 None。相同邊框、相似偏色或繁雜背景通常沒有唯一提示。
圖案自身若形成矩形仍可能造成視覺候選，因此提示不能獨立確認選取或區分 hover。
Blue 與 Osu Transfer 裁切圖沒有右側歌單，最終 selected_pack 仍只能 UNCERTAIN，
其標籤明確允許這個欄位棄權；正確的人工標籤保留不變。

selection 輸出 `selected_pack`、`selected_pack_id`、`selected_pack_confidence`、
`selected_pack_evidence`、`pack_reason`。confidence 是支持歌名 OCR 的最低信心，
不是選取正確率。主要與次要訊號衝突時清空名稱／ID，理由為
selected_pack_disagreement；不足時為 selected_pack_uncertain。
即時監控的 ocr_failures 旁路只因 disagreement 擷取，不再因 uncertain 擷取。
成功的右側歌單推論會保留到 tracker 的 Pack 與開局消耗的 selection，包含來源和證據；
推論演算法維持原樣。開啟清單而沒有成功推論仍清除舊 Pack 上下文。

## Round 2.6 結算保留與 Session 3 回放

本人列、必要數字、評級、歌名等必要欄位至少有一張完整且信心達 0.95 時，
tracker 保留最佳完整樣本。若在雙幀結算防抖完成前離場、關窗或停止監控，
該 Session 保存為 `pending_result`，以保留的原圖／OCR 寫入 `pending_review`。
這條保留路徑強制 temporal pending，不能自動提交。沒有完整高信心樣本仍沿用
interrupted 行為。黑色／近黑載入幀不進行辨識，也不清除或延長 selection 的 TTL。

唯一一致的歌名／難度／LV 候選可以綁定身份，已知 notes／duration 必須與畫面一致；
未知譜面資料不能排除同名候選。Pack 可以消除歧義，但矛盾的 Pack 不能強行綁定。
官方歌曲的唯一一致身份不要求 Pack。身份綁定與已驗證譜面資料分開：自動提交仍要求
原有的完整音符／譜面證據，不能把身份唯一性當成已驗證譜面。

```powershell
python -X utf8 -m tools.replay_realplay3
```

工具唯讀載入 `data/realplay3_snapshot/` 的 diagnostics 順序、Session／review JSON
與失敗 PNG／JSON，使用目前的解析、tracker、review 與 automatic_request。輸出
`reports/round26-replay.json`。所有重建 Session 寫入暫存目錄，不寫曲庫或來源快照。
缺少快照時回報 skipped。未保存的 OCR payload 不計入雙幀，不能從狀態日誌猜數字。

本機回放：DEAREST DROP 綁定 649、Pack 18，雙幀 confirmed；但快照曲庫缺少
649 的 HARD 譜面，仍因 `automatic_chart_evidence_unverified` 與
`automatic_full_notes_unverified` 無法自動提交。Burst The Gravity、Yasashisa no Riyuu、
地獄之門Remix 均保留結算並寫入待審核，最後一首綁定官方 2873，沒有 Pack。
「A」的 footer 在相同原圖重讀後雙幀 confirmed。快照沒有它在黑屏前的 selection
payload，所以只用 playing_preview 作保留性探針（信心為 0、無自動權限）；
black_capture 保留探針成立，不宣稱還原了遺失的完整選歌證據。

四張 realplay3 標籤使用原圖 SHA-256 與使用者提供的真值；既有真值與 baseline 不變。
舊測試只有三處配合規則調整：Pack uncertain 不再擷取、成功推論可進 tracker、
JOINT 原圖的唯一右側歌單 Pack 現在保留。另有一處假 catalog 更新為新匹配介面，
其 unreadable-selection 保留斷言不變。
