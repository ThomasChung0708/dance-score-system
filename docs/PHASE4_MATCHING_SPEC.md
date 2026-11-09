# Phase 4 歌曲與遊戲難度匹配規格

> 本文現況稽核為當時 v0.4.1／schema 4 的唯讀快照。後續官方匯入已獨立完成於 v0.5.0／schema 5；時間欄位與 OCR 匹配仍未實作，本文必要條件不放寬，也不阻塞官方匯入。

> v0.6.0 狀態：Phase 4A 已完成靜態圖片 OCR 與唯讀候選查詢，見 `PHASE4A_OCR.md`。沒有新增時間欄位或實作成績自動綁定；以下完整匹配與多人必要條件繼續適用。本文其餘「尚未實作」與統計為原分析日期的紀錄。

確認日期：2026-10-22。依使用者本次補充制定。這是後續 OCR 與人工匹配的規格及資料模型提案，本次沒有開發 OCR、匹配器、成績功能或執行 migration。

## 1. 現況與結論

目前 v0.4.1／SQLite schema 4。Song 的永久身份、GameDifficulty 外鍵及未配對成績的基本結構足夠；尚缺結構化遊戲時間、六項匹配條件的驗證流程、候選選擇介面及確認證據寫入功能。

- 自製 Song 以既有 song_id 保存身份，根目錄＋SongFolder 路徑的唯一鍵區分歌曲。
- 每個 difficulty_id 只屬於一個 song_id，難度只允許 EASY／NORMAL／HARD。
- play_records.difficulty_id 可為 NULL，match_status 預設 pending，evidence_json 可保留匹配證據。
- song_title_aliases 可保存遊戲顯示名稱與來源，但只表示名稱對應，不能代替譜面匹配。
- difficulty_bests 只納入 match_status=confirmed 且 validation_status=verified 的紀錄。
- 現有資料表沒有 duration_seconds、duration_source、duration_verified。掃描時間 started_at／finished_at 不是歌曲時間。
- 目前沒有 OCR 自動綁定器，SQLite 現有 CHECK 也沒有驗證六項條件或候選唯一性；不能宣稱 schema 4 已完整強制這套規則。

## 2. JOINT 實際案例

唯讀確認 Pack ID=29，名稱 `Otaku's Dream 9th Anime Mix`。

| 項目 | 候選 A | 候選 B |
|---|---|---|
| 現有 Song ID | 990 | 1004 |
| SongFolder | (Erebus Lux) Joint | (Niko) JOINT |
| 原始 Title | JOINT | JOINT |
| 原始 Artist | 川田まみ | Kawada Mami |
| 來源 ID | 1840 | 1854 |
| 使用者提供 HARD LV | 17 | 20 |
| 使用者提供 HARD 音符數 | 821 | 929 |
| 使用者提供遊戲時間 | 1:36＝96 秒 | 1:35＝95 秒 |
| 使用者提供 BPM | 180 | 180 |

兩個完整相對路徑：

- `Otaku's Dream 9th Anime Mix\(Erebus Lux) Joint`
- `Otaku's Dream 9th Anime Mix\(Niko) JOINT`

Song 顯示名稱目前分別包含原始 Subtitle：`JOINT - 灼眼のシャナ II OP1 (2007)`、`JOINT Shakugan no Shana II (2007)`，但兩份來源的原始 Title 都是 JOINT。不能因 GUI 合成顯示名稱不同，就忽略遊戲只顯示主標題時的多候選情況。

正式庫目前這兩首都沒有 GameDifficulty。LV、音符數、BPM 及秒數在本文件僅是使用者提供的規格案例，**本次未寫入正式資料庫，也沒有從 SM 或音樂檔推算**。

Title、Artist、BPM、音符數或時間的相同與不同，都不能重建、合併或替換既有 Song ID。Artist 中日文／羅馬字名稱不作自動同人認定，也不作綁定依據。

## 3. 最小時間欄位提案

建議在 **game_difficulties** 新增，而非只在 songs 放單一時間：

| 欄位 | 建議型別與規則 |
|---|---|
| duration_seconds | INTEGER，可 NULL；已確認時為正整數，單位秒 |
| duration_source | TEXT，可 NULL；初期只允許 manual／game_screen，表示手動確認的遊戲值或經驗證的遊戲畫面資料 |
| duration_verified | INTEGER NOT NULL DEFAULT 0，只允許 0／1 |

原因：匹配目標是目前選取的 GameDifficulty，時間與同一難度的 LV、音符數一起比較，不先假定同一首歌三種難度時間一定相同。若未來確認全難度共用，仍可保存每個難度的觀測，不需要改 Song 身份。

未知／尚未確認時，duration_seconds=NULL、duration_source=NULL、duration_verified=0。原始 OCR 字串、候選秒數及衝突留在觀測 evidence_json，不先寫入這個標準時間欄位。已確認後才保存秒數、來源並設 duration_verified=1。

新增欄位應有 CHECK：秒數非 NULL 時必須是正整數；來源只接受核准值；NULL／來源／verified 必須一致，不能沒有時間卻標 verified=1。這三個新增欄位可在一個安全交易內以 ALTER TABLE ADD COLUMN 加入，既有列保持未知，不重建 Song 或重新編號。

時間正規化：

- `1:36`、`01:36` → 96 秒，`1:35` → 95 秒。
- `mm:ss` 的 ss 必須在 00–59；無效值、空白、含糊 OCR 保持未確認，不轉為 0。
- 不能把倒數剩餘時間、已遊玩時間、失敗時長、音訊長度或不同難度時間當成完整遊戲歌曲時間。
- 不自行四捨五入小數秒、允許 ±1 秒或引入容差。若日後畫面提供小數精度，再確認精度規格。
- 95 秒與 96 秒即為衝突，保留兩份證據並交由人工確認。
- 不以原始音樂檔長度、SM／osu 時序或其他工具估算值覆蓋遊戲顯示時間。
- 官方 CSV 雖有 durEasy／durNormal／durHard，也不能在尚未確認其為對應遊戲顯示時間前，自動當作 verified 時間。若日後確認來源可靠，再評估 official_csv 來源值。

本次只提出欄位與限制方案，不更新 schema。Phase 3.5 的 migration 也仍為提案；實際版本號與是否合併 migration，依屆時已執行版本及使用者確認決定，不能現在直接升級。

## 4. 候選查詢與自動綁定

名稱搜尋、候選查詢與最終綁定是三個不同步驟。搜尋可列出多個近似或同名來源；搜尋排序與相似度不構成身份證明。

只有下列全部條件具備可靠證據、互相一致，且完整比對結果只剩 **一個 Song／GameDifficulty**，才允許自動綁定：

1. 來源 Pack 已確認：對應到明確 root_id／pack_id，不能只靠可能重名的 Pack 文字或猜測目前列表位置。
2. 遊戲歌名與可靠的原始 Title，或經確認的遊戲名稱別名匹配。原始 Subtitle 保留；不得自行翻譯、以 Artist 相似度替代或把模糊搜尋命中當作確認。
3. EASY／NORMAL／HARD 是本人的目前選取難度。
4. 遊戲 LV 與該難度已確認的 game_level 相等，不能使用 SM／DWI／osu 的原始難度數字。
5. 完整音符數與同一 difficulty_id 的已確認 total_notes 相等。
6. 完整遊戲時間正規化後，與同一 difficulty_id 的 duration_seconds 相等，且資料來源已確認、duration_verified=1。
7. 全部條件交集只有一個候選；不能因 LIMIT 1、UI 分頁、排序或先前記憶，漏掉另一個符合者。

資料庫中欄位為 NULL 或來源未確認，不能當成任意值匹配。即使只找到一首歌，只要少一項必要條件，仍為 pending。資料庫的待確認名稱、衝突數值、未驗證觀測不能當作自動綁定依據。

若有 0 個候選，或有任一缺值、衝突、多個候選：保存原始觀測、缺失／衝突欄位及候選清單，標記待確認。不得把分數寫到搜尋結果第一首。不能為了讓比對成功而自動覆蓋資料庫數值。

實際綁定時必須在交易內重新查詢候選及版本，避免 UI 開啟後來源或難度被修改，使用過期結果綁定。最後保存明確 difficulty_id，其所屬 song_id 必須等於選定 Song ID，原始 Song ID 不改變。

## 5. 人工確認與保存

未來 GUI 應列出每個候選的 Song ID、Pack、完整 SongFolder、E／N／H、LV、音符數、遊戲時間、資料來源及未確認／衝突欄位。

示例（尚非現有資料庫內已儲存數值）：

- Song 990／(Erebus Lux) Joint：HARD LV 17／821 notes／1:36。
- Song 1004／(Niko) JOINT：HARD LV 20／929 notes／1:35。

所有數值相同仍列兩列；使用者可明確選定 Song ID 及 GameDifficulty，不自動合併。人工在缺值／衝突下確認是手動決策，不冒充六項條件已自動匹配。

最小方案可先沿用已有欄位，不額外建立匹配表：

- `play_records.difficulty_id`：保存實際選定的遊戲難度關聯。
- `play_records.match_status`：人工或完整規則確認後才可設 confirmed；待確認時保持 pending，未選擇可保持 difficulty_id=NULL。
- `play_records.evidence_json`：保存 match_method=manual／automatic、規則版本、確認時間、selected_song_id／difficulty_id、Pack／SongFolder、原始觀測、標準化時間、候選 ID 與當時數值、缺失／衝突、確認來源或畫面證據引用。更正時保留舊決策證據，不悄悄覆寫來歷。
- `play_sessions.evidence_json`：可保存結算前的選歌／房間觀測，不為了保存匹配證據提早生成成績。

人工確認只綁定該筆紀錄及其明確證據。不能因此把同名歌永久全域重導到同一 Song，或把不一致時間自動改成一致。若要保存可重用規則，必須連同 Pack、難度、LV、音符數、時間及版本範圍，另行設計與確認；單一 title alias 不足以放行。

匹配 confirmed 與成績 validation_status=verified 是不同檢查。人工選到正確 Song 不代表判定數、完成狀態或其他成績資料已通過驗證，不可因此更新個人最佳或燈號。本次不新增這些功能。

## 6. 多人規則繼續適用

- Phase 4C 最新補充：無 Pack 的多人結算先搜尋全部 Custom Pack，包含 connect、其他既有 Pack 與新 Pack；預設沒有自動 Song ID。待確認頁可背景重掃整個已登錄 SONG 更新候選，仍由使用者明確確認。不依 Pack 名稱或單首 Pack 推定取得方式，未知時保留 unknown。操作與實作見 [多人補充](PHASE4C_MULTIPLAYER.md)。
- 自由模式以本人難度／LV／音符／時間為準，不能挪用 HOST 或其他玩家資料補齊。
- 非 HOST 的來源 Pack 未確認時，必要條件缺失，保持 pending，不能因剩一個同名候選而自動綁定。
- connect 是儲存／管理 Pack，不等於已確認 HOST 原始來源 Pack。不可只看下載位置或檔名尾碼宣告與其他 Pack 譜面相同。
- 非 HOST 的完整音符數只能在本人正常完成且判定可信時，以既定 P+C+B+M 流程提供候補；失敗部分判定不能補完整總數。匹配模組不另行放寬這個限制。
- 本人先倒下但其他人存活，仍等結算；quit／斷線／中斷不冒充失敗或生成結果。保存候選或觀測不等於已產生成績。
- 官方來源尚無可確認實體 Pack 時，本次不自行以 Official 分類代替「Pack 已確認」。若未來要以官方 catalog／fileId 來源範圍定義對應條件，另行確認；未確認前仍禁止缺少必要條件的自動綁定。

## 7. 後續 migration 與驗收

時間欄位 migration 應先備份正式庫，在副本測試，再以交易新增欄位，核對所有 Song／Source／Difficulty／Record ID、名稱、路徑、現有難度值、歷史、完整性與外鍵一致。既有時間全為 NULL，不回填猜測值。新增欄位失敗需全部回滾。

以下為 Phase 4 待實作測試案例，**不是本次已執行的 OCR 測試**：

| 案例 | 預期 |
|---|---|
| JOINT 兩個不同 SongFolder、不同語言 Artist | 永久保留 Song 990／1004 |
| Pack＋JOINT＋HARD＋17＋821＋96，且資料已確認 | 唯一對應 A 時才可自動綁定 |
| Pack＋JOINT＋HARD＋20＋929＋95，且資料已確認 | 唯一對應 B 時才可自動綁定 |
| 全部欄位相同且存在兩個 Song／Difficulty | pending，人工選擇 |
| 任一必要欄位缺失或資料庫值未確認 | pending |
| 17／821 配 95 秒，或 20／929 配 96 秒 | 衝突，pending，不接受 ±1 秒 |
| 1:36 與 01:36 | 都標準化為 96 秒 |
| 1:60、含糊字串、0 或未確認秒數 | 不寫入 verified 時間 |
| 歌曲相同但選成 EASY 或使用另一難度的數值 | 不自動綁到 HARD |
| 排序／分頁改變，或 Artist 類似度最高 | 不改變候選與決策 |
| 人工選擇 B | 保存 B 的 song_id／difficulty_id 及人工來源，不合併 A |
| 人工選擇後下次只有同名觀測 | 不自動重用上次決策 |
| UI 開啟後資料被修改 | 交易重新驗證或要求重選 |
| 非 HOST 缺 Pack，或自由模式只有 HOST LV | pending |
| 音樂檔時長與已確認遊戲時間不同 | 不覆蓋遊戲時間 |

本次唯讀稽核：40 Pack、1,859 Song、2,930 Source，schema 4；integrity_check=ok、foreign_key_check=[]，資料逐表稽核相同。完整讀取證據保存在 `reports/phase4-matching-analysis-20261022/inspection.json`。


## Phase 5A 人工提交與自動匹配邊界

0.9.0 可將使用者明確核對的 Song ID／SongFolder／E-N-H／歌曲 LV 與本人結算正式提交。缺少觀測 Pack 或遊戲時間時仍不可自動匹配，但使用者已明確確認身份後，可依 Phase5A 必要條件人工提交；未知時間保留NULL，不因此建立可重用自動規則或填入驗證時間。FAILED 的部分判定不補完整音符，MAX可NULL。正式記錄保存人工快照與原圖指紋，不改寫原始 OCR／鎖定 Session。完整流程見 [Phase5A](PHASE5A_SUBMISSIONS.md)。


## Phase 5A.1 安全填入補充

0.9.1 區分來源確認與搜尋篩選。官方 K 在明確來源、可靠歌名／E-N-H／歌曲LV及所有可見音符／BPM核對後，唯一候選才可綁定，不要求 Custom 的 Pack 或尚未取得的遊戲時間；原始 OCR 與歌曲庫補充值分開保存在 Session。Custom 仍需完整 Pack／歌名／E-N-H／LV／完整音符／時間，缺值或未知同名候選保持人工確認。完整人工審核對應可重用，須核對原圖指紋與穩定 Song ID／Root／SongFolder。判定合計不回填完整音符，計數差異未驗證時不放行正式提交。見 [Phase5A.1](PHASE5A1_NOTES.md)。
