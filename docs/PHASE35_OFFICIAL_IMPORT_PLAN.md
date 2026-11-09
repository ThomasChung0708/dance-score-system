# Phase 3.5 官方鍵盤歌單分析與匯入提案

> 以下保留原始規劃供追溯；使用者後續已授權完成 K 匯入，v0.5.0／schema 5 已實作。現況見 [官方匯入說明](OFFICIAL_IMPORT.md) 與 [驗證結果](VALIDATION.md)。

分析日期：2026-10-22（Asia/Tokyo）。專案 v0.4.1、SQLite schema 4。本文件是待確認提案，尚未實作 migration、匯入器或 GUI 變更，未修改正式資料庫或原始 CSV。

## 1. 結論與預計數量

來源：`C:\Users\User\Desktop\song_table.csv`，942,228 bytes，UTF-8 BOM，嚴格解碼成功。38 個欄位、5,052 筆資料，沒有 CSV 欄數錯位。

SHA-256：`08c93228726802f7935895eea66649a14ceddbc8964ac23b7b96c4332503b28b`。

| mode | 資料列數 | 處理 |
|---|---:|---|
| K | 2,538 | 鍵盤來源候選 |
| T | 2,513 | 不匯入正式歌曲庫 |
| 空白 | 1 | 不匯入正式歌曲庫，為「新手教學」 |
| 合計 | 5,052 | 原始 CSV 保持完整 |

K 的 fileId、Title、Artist、BPM 皆非空白，fileId 與有效難度數字皆為正整數。BPM 全部為正值，其中 393 列有小數，範圍 54–260，不應強制轉為整數或捨入。

K 有 **2,538 列合法基本資料、2,537 個不同 fileId**。其中 **2,536 列沒有 fileId 重複，另外 2 列屬於同一個待確認群組**。不能把「不同 fileId 數 2,537」直接當作可安全合併後的歌曲數。

建議的目標是在確認下述複合來源身份後，保留全部 **2,538 個官方來源歌曲項目、7,612 個有效難度**。如果暫緩重複身份群組，則先可匯入 **2,536 首、7,606 個難度**，2 列保持待確認，不擅自留一列或合併。

CSV 有 4 列 K 資料的 hidden=1：10078、12201、12203、12204。依本次「只以 mode=K 篩選」規格，仍納入候選，不另外依 hidden、src 或 enc 過濾。K 的 src 包含 kgn、songname、official、manual、fixed；不能只匯入 src=official 的 24 列。

## 2. fileId 重複調查

只有 fileId=`10026` 重複，兩列都為 K、〈使者〉、Artist=`N.R.G`。

下列序號含 CSV 欄名列，首筆資料序號為 2。

| 欄位 | 第 42 筆記錄 | 第 1,263 筆記錄 |
|---|---|---|
| fileId | 10026 | 10026 |
| mode | K | K |
| gn | sdom0026k.gn | sdom0921k.gn |
| origName | sdom0026K.gm | sdom0026K.gm |
| enc | sdom | rewu |
| offsetMs | -20 | -15 |
| innerOff | 456 | 空白 |
| size | 36800 | 36044 |
| EASY LV / notes | 5 / 279 | 5 / 279 |
| NORMAL LV / notes | 8 / 399 | 8 / 399 |
| HARD LV / notes | 9 / 501 | 9 / 501 |

38 個欄位中，只有 gn、enc、offsetMs、innerOff、size 五項不同。Title、Artist、BPM、三種難度 LV／音符數及 origName 相同。不是完全相同的重複 CSV 列。

**可以確認來源描述不同，無法僅靠 CSV 確認它們是兩個遊戲選歌項目，還是同一首的不同封裝／資源映射。** 相同 LV／音符數不能證明音符配置相同。enc 是來源欄位標記，不是本 CSV 的 UTF-8 文字編碼。

因此不使用 fileId 單獨作唯一鍵，也不採用後一列覆蓋前一列、保留第一列或自行去重。

### 身份候選檢查

| 候選 | K 內不同值數 | 可否區分本檔全部列 |
|---|---:|---|
| fileId | 2,537 | 否 |
| gn | 2,538 | 是，沒有空白，忽略大小寫仍不重複 |
| fileId + gn | 2,538 | 是 |
| fileId + enc | 2,538 | 本次是，但不建議把可能變動的來源標記當主要鍵 |
| origName | 2,283 | 否 |

**提案：`catalog_key + mode + fileId + gn` 作官方來源唯一鍵**，內部仍使用穩定 song_id。catalog_key 是固定的官方歌單命名空間，不是 CSV 路徑或檔案雜湊。原始 fileId 以 TEXT 保存，不因數字轉型失去原值；gn、origName、enc 等也保留原文。

在使用者確認前，不套用此身份策略。尤其是 10026 的兩列，先視為待確認的兩份來源描述，不宣稱是已驗證的兩張不同遊戲譜面。

未來 CSV 搬位置、重新排序或多增加幾首歌，不應重建 Song ID。記錄行號、CSV SHA-256、Title、Artist、BPM 不可充當歌曲主身份。未來同 gn 改 fileId 或同 fileId 改 gn，也應先列身份變更待確認，不能逕行搬移既有成績。

## 3. 難度與異常值

| 難度 | LV 與音符數皆為正整數 | LV=0 且音符=0 | LV 或音符空白 | 其他格式／正負值異常 |
|---|---:|---:|---:|---:|
| EASY | 2,538 | 0 | 0 | 0 |
| NORMAL | 2,537 | 1 | 0 | 0 |
| HARD | 2,537 | 1 | 0 | 0 |
| 合計難度 | 7,612 | 2 | 0 | 0 |

唯一有 0 的歌曲是記錄 3,404：fileId=`12140`、gn=`sdom2140k.gn`、〈動畫歌曲串燒〉。

- EASY：LV 26、3,417 音符，MAX=6,834。
- NORMAL：LV 0、音符 0。
- HARD：LV 0、音符 0。

同列的 durNormal／durHard 及 measNormal／measHard 也為 0。這支持「CSV 沒有提供這兩個難度的有效資料」，但本次不直接斷言實際遊戲一定沒有它們。

### 建議匯入規則

1. LV、音符數皆為正整數：建立該 E／N／H 難度，以 `official_csv` 標記來源。
2. 兩者皆為 0 或皆空白：不建立 GameDifficulty，原始值保留於來源證據。顯示「未設定（CSV 未提供有效難度）」，不顯示 LV 0／0 音符／MAX 0。待使用者確認語義後，才可改為「不存在」。
3. 只缺其中之一、LV>0 但 notes=0、LV=0 但 notes>0、負值、非整數、非數值：列異常待確認，不猜值、不從其他難度補齊。本 CSV 的 K 列目前沒有這些狀況。
4. 缺少 Title、fileId 或來源身份：暫緩該列。本 CSV 的 K 列目前沒有這些狀況。
5. MAX 僅在有可靠正整數 total_notes 時由 `total_notes * 2` 計算。不要另存重複 MAX 欄位，也不接入成績或燈號規則。

以 10001〈野蠻遊戲〉為驗算例：EASY 510 音符→MAX 1,020，NORMAL 598→1,196，HARD 744→1,488。

## 4. 同名與多語言

K 共有 84 組完全同名標題，涉及 176 列，其中 83 組含不同 fileId，另外一組是上述 10026。全部應依身份保留，不能以歌名去重。

例如〈One Love〉有 fileId 10042（平井堅）及 11844（Super Junior 銀赫 KRY），應保持不同 Song ID。即使歌名與 Artist 都相同，也不應合併不同來源身份。

可直接嚴格讀取 UTF-8 BOM，無須猜測 CP932／Big5，也不得改寫成其他候選語言。原始 Title／Artist 未發現 U+FFFD 或私用區字元，這只是編碼檢查，並非人工校對所有名稱。中文〈野蠻遊戲〉、日文〈ウレシ泣キ〉、英文〈Funky Music〉均可作後續 GUI 驗證樣本。

titleZhCn、titleEn 等保留於原始列資訊；主要顯示名稱使用使用者指定的 title，不自行翻譯或改用別欄。搜尋仍可沿用現有 Unicode NFKC／casefold 搜尋索引，原始名稱不被改寫。

## 5. 現有資料庫檢查

使用 SQLite `mode=ro` 及 connection query_only 開啟，沒有呼叫可觸發 migration 的寫入入口。

| 項目 | 本次實際讀取 |
|---|---:|
| schema | 4 |
| Pack | 40 |
| 自製＋connect Song | 1,859 |
| 一般自製 Song | 1,782 |
| connect Song | 77 |
| SM／DWI／osu Source | 2,930 |
| 官方 Song | 0 |
| GameDifficulty | 0 |
| PlaySession／PlayRecord | 0／0 |
| scan_runs | 6 |

`integrity_check=ok`，`foreign_key_check=[]`。分析前後既有資料逐表稽核相同，CSV SHA-256 也保持相同。雖然正式庫目前沒有難度或歷史，之後仍必須以非空歷史測試 migration 與回滾。

現有架構有三項直接限制：

- Song 強制 root_id、relative_folder、folder_key 非 NULL；GUI／API 把 root 當作本機 SONG 目錄。
- song_sources.format 與 Song.source_format 只接受 sm／dwi／osu，不能硬塞 CSV 或冒充其中一種。
- game_difficulties 的 level_source／notes_source 尚不允許 official_csv，不能把 CSV 冒充 manual 或 game_screen。

因此零 schema 修改雖可把部分資訊塞入 JSON，卻不能正確表達來源及掃描邊界；建議做一次最小 schema 4→5 migration，不換 GUI 框架、不重建專案。

## 6. 最小 Schema 變更提案（尚未執行）

### 6.1 保留既有主表與 ID

沿用 Song → GameDifficulty → PlayRecord，不另建一套官方成績模型。現有 40 Pack、1,859 Song、2,930 Source、所有 difficulty_id、record_id 均保持原值。官方不建立實體 SongFolder，也不占用 connect 或一般自製 Pack。

為避免為了放寬 Song 的 NOT NULL 而重建歌曲主表，提議：

- library_roots 新增 `root_kind`，預設 filesystem，另允許 official_catalog。
- 新增一個 official_catalog 邏輯來源 root，路徑欄保存固定命名空間，例如 `official://dance_online/K`，**不是檔案系統路徑，不得交給 Path 或掃描器開啟**。
- 官方 Song 設 source_type=official、pack_id=NULL、folder_name=NULL、source_format=NULL。
- relative_folder／folder_key 僅為沿用舊 NOT NULL 與唯一鍵的內部邏輯來源鍵，例如 `K/10026/sdom0026k.gn`；不宣稱是真實歌曲資料夾。權威身份另由下面的官方來源表保存與約束。GUI SongFolder 顯示「不適用」，不顯示虛構磁碟路徑。
- GUI 的「歌曲庫位置」繼續表示本機自製 SONG 目錄；官方分類查詢邏輯來源。需要調整 API 的查詢範圍與實際 root_id 綁定，確保選官方分類即可看到全體官方 K 歌曲，不必建立實體資料夾或手動換磁碟路徑。

這是為保留現有主表、全部歷史外鍵及實體 Pack 數量所提出的相容方案，待使用者確認後實作。若使用者希望資料欄位語義完全去除邏輯路徑，可改為放寬官方 Song 的 root／folder NULL，但會多一次 Song 主表重建，非本次建議的最小方案。

### 6.2 新增官方來源表

提議 `official_song_sources`：

| 欄位 | 用途 |
|---|---|
| official_source_id | 新的來源主鍵，與既有 song_sources ID 空間分開 |
| song_id | 唯一外鍵連到既有 Song |
| catalog_key | 穩定官方歌單命名空間，不依 CSV 路徑變動 |
| mode | CHECK mode='K' |
| file_id | 原始 fileId，以 TEXT 保存，**不得單欄 UNIQUE** |
| gn | 原始來源 gn，作待確認的補充身份 |
| bpm_raw | 原始 bpm 字串，保留小數精度 |
| raw_row_json | 完整原始列，包含原始 0、空白、origName、enc 等 |
| first_import_id／last_import_id | 指向匯入批次與原始 CSV 證據 |
| csv_record_number | 查找來源位置，不作身份鍵 |

唯一鍵提案：`UNIQUE(catalog_key, mode, file_id, gn)`。10026 的兩列是否以不同 gn 建立不同 Song，仍是本次待確認事項，不因提案就先匯入。

不擴充既有 song_sources.format，不重編 2,930 個來源 ID，不將官方 CSV 偽裝成 .osu 或 .sm。

### 6.3 匯入批次與難度來源

新增 `official_import_runs`，保存 import_id、catalog_key、來源 CSV 路徑、SHA-256、嚴格解碼編碼、時間及結果統計。原始 CSV 保持不變；正式官方來源表只保存 K 的原始列，T／空白只記錄排除數量，不建立來源、Song 或 GameDifficulty。

game_difficulties 只擴充既有 CHECK，讓 level_source／notes_source 接受 official_csv，既有 manual／game_screen／completed_judgment_sum 保留。不更動 PlayRecord／Session 或燈號規則。

CSV 為使用者確認的遊戲資料來源，正值音符數可設 notes_verified=1，同時明確保留 official_csv 來源；不冒稱 OCR 驗證。空白或 0 不建立難度。MAX 只作計算欄位。

## 7. GUI 顯示提案

沿用現有 HTML／JS 與 Python 本機服務，不新增框架。

- 左側保留「官方歌曲／自製歌曲／多人下載」，connect 維持 77 首的獨立管理 Pack。官方列在自己的分類，不硬加成第 41 個自製 Pack。
- 全部歌曲可涵蓋官方及目前選定 SONG 範圍的自製／connect；各分類數量分開顯示，Pack 數仍為實際 40，不把 CSV 當 Pack。若全部 2,538 項確認匯入，總歌曲數預期 4,397。
- 歌曲清單繼續分頁、名稱排序及多語搜尋。官方列顯示 Official／K／fileId；同 fileId 的不同 gn 另外顯示 gn，並使用不同 Song ID。
- 右側顯示原始 fileId、Title、Artist、BPM、mode=K、gn、來源 CSV 及記錄序號。Pack／SongFolder／來源格式分別顯示「不適用／不適用／官方 CSV」，不拿 CSV 的 origName 冒充實際磁碟路徑。
- E／N／H 卡顯示官方 LV、總音符數、MAX。CSV 沒有有效值時顯示未設定及原始 0／空白證據，不製造難度。
- 官方 CSV 卡先以來源檢視為主；保留原有自製手動編輯。未來若需手動更正官方值，另以 manual override 處理，不讓重匯悄悄蓋掉使用者資料。
- 「更新歌曲庫」只掃描 filesystem root；「匯入官方 CSV」是獨立流程，先預覽 K／排除數量、身份重複與難度異常，確認後才提交。不能因掃描自製 SONG 找不到官方邏輯路徑就將官方資料標記消失。

## 8. 確認後才執行的安全流程與測試

本次尚未進行資料庫副本匯入測試，因為使用者明確要求先交付分析與規劃、確認後再實作。以下是下一步驗收方案，不能當成已完成結果。

1. 從正式庫以 SQLite backup API 建立測試副本，核對所有表、ID、名稱、來源路徑、既有難度與歷史。
2. 先在副本執行 schema 4→5；擴充 difficulty CHECK 使用既有安全表替換方法，保留 difficulty_id、外鍵、索引、trigger、view，失敗整筆回滾。
3. 正式 migration 前另建驗證過的 pre-v5 備份。匯入前再建立已核對的備份或將首次 migration＋import 合併在可回滾交易中；兩個階段的失敗邊界要明確，不宣稱匯入失敗時 schema 也必然回到 4。
4. K 以外不建立歌曲，完整保存原始 fileId、mode、gn 及來源欄位。所有來源與難度在一個匯入交易內寫入，任一步錯誤全部回滾，不留半套 Song／Source／Difficulty。
5. 對同一 CSV 重匯兩次：第二次新增 Song／OfficialSource／Difficulty 均為 0，所有內部 ID 不變。只可新增批次日誌，不可用 INSERT OR REPLACE 重建歌曲或難度。
6. CSV 路徑改變、列順序改變不換身份。模擬 fileId 重複、複合鍵重複或 gn 變動，必須在預覽阻擋，不能後列覆蓋前列。
7. 已有難度／歷史全部保留；相同值為 no-op，CSV 與既有手動／已驗證值不同時列衝突，不自動覆寫。使用含非空人工難度與歷史的測試庫驗證，不只測正式庫目前的零紀錄情況。
8. 測試正值、0／0、空白／空白、半缺值、非整數、負值及錯誤編碼。以 12140 驗證只建立 EASY，其他兩個不補值；以 10001 驗算 MAX。
9. 模擬來源／難度寫到一半拋錯，核對匯入前後完整資料一致；執行 integrity_check／foreign_key_check。
10. 重掃本機 SONG 後，官方歌曲與難度保持；原有 40 Pack、1,859 Song、2,930 Source ID 與任何歷史完全保留。連線下載仍由 connect 自己的 Pack 管理。
11. GUI 驗證官方中文／日文／英文、同名不同官方身份、fileId／gn、BPM 小數、各難度與 MAX；重啟後仍可查詢。原有測試全部持續通過。

## 9. 等待確認的事項

1. **10026 的兩列如何管理？** 建議保留兩份來源並採 fileId＋gn 區分，不自動合併；但是否視為兩個官方選歌項目，仍需使用者或提供清單的朋友確認。也可先暫緩這兩列，處理另外 2,536 首。
2. 是否接受上述 schema 5 最小方案：保留歌曲／歷史主表，新增邏輯來源種類、官方來源／匯入批次，僅擴充難度來源 CHECK？
3. 0／0 的預設建議為「未提供有效難度」，不建立難度列；若使用者確認它明確代表遊戲不存在該難度，GUI 才可標示「不存在」。

分析明細與唯讀稽核：`reports/phase35-analysis-20261022/analysis.json`。本次沒有變更應用程式版本、SQLite schema、正式資料或 CSV。
