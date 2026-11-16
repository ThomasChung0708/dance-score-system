# Phase5E 成績單與成長履歷（0.15.0 / Schema10）

399項測試全部通過、0略過，包含20項新增測試。隔離實際GUI通過四類更新、FAILED高EX、FIRST BEST／FIRST LAMP、雙更新、減少動畫、成長排序、難度隔離、原圖連結及重整不重播。390px成績單與單曲頁無水平溢出；真實#18 EX2029／#19 AC的獨立最佳已唯讀驗證。

沒有migration。開發前20筆成績及Session原值保持；核對時正常使用另增2筆正式成績及2次重掃。原身份、來源及PNG保留，掃描標記／診斷異動與生成測試資料分開稽核。沒有將測試成績寫入正式庫。詳見[說明](PHASE5E_PERFORMANCE_UI.md)與`reports/phase5e/report.html`、`validation.json`。本階段未新增真實遊玩端到端驗收。

---

# Phase 5D 獨立最佳紀錄（0.14.0 / Schema10）

379項自動化測試；包含18項新增獨立刷新、FAILED、來源Record、實際#7/#15快照及migration回滾測試。隔離實際GUI通過分數單獨刷新與燈號單獨升級，動畫追蹤及截圖已保存。正式庫16筆成績與所有原資料保留，備份、完整性及外鍵檢查通過。沒有新增真實遊玩端到端驗收。詳見[說明](PHASE5D2_INDEPENDENT_BESTS.md)與`reports/phase5d2/report.html`。

---

# Phase 5D 補充修正（0.13.1 / Schema 9）

361 項測試全部通過；包含 11 項新增篩選測試與隔離 GUI 的摘要／人工全庫搜尋。既有正式成績及 ID 保留，沒有 Schema 修改。詳細結果見 `reports/phase5d1/report.html` 與 [操作說明](PHASE5D1_NEW_SONG_SCOPE.md)。

---

# Phase 5D 驗證（0.13.0 / Schema 9）

350 項測試全數通過；27 張真實原圖分類正確，新增兩張本人 NO MAP 已實際執行 OCR。正式資料庫逐表內容、8 筆既有成績與 Session 證據保持不變，備份已核對。完成隔離 GUI 的 FIRST BEST、NEW SCORE +5、Recent 同步／空狀態／重啟與不重播檢查。實際多人下載端到端尚未驗收。

詳見 [操作與驗證範圍](PHASE5D_DOWNLOADS_RECENT.md) 及 `reports/phase5d/validation.json`、`report.html`。

---

# Phase 5C.2 驗證（0.12.0 / Schema9）

- 317項自動化測試通過：298項既有回歸＋19項新制度／遷移／衝突保護測試。
- 舊制規則以顯式歷史策略保留測試；正式GUI預設新制度，不接受表單切換回舊制。
- 三張原始PNG重跑OCR，12/12個P/C/B/M欄位符合已知值，低信心評級不被抬高。
- 隔離完整歌曲庫實際GUI：Heart自訂MAX-64、Cagayake人工AC/MAX-3、可信自動新制、1253/1255衝突PENDING與SAVED、舊制分組保留、不觸發錯誤刷新、歷史篩選及標示。
- 正式庫Schema8→9備份與逐表／舊bests稽核通過，8筆原成績及Session保持，測試紀錄不存在正式庫。
- 沒有新的真實遊玩端到端測試；CSV差異自動上線、多人、GAMEOVER與長時間遊玩仍需實玩。

詳見 [Phase5C.2](PHASE5C2_CUSTOM_MAX.md) 及 `reports/phase5c2/validation.json`。

---

以下保留歷史驗證報告：

# Phase 5C.1 驗證（0.11.1／schema 8）

完整結果見 [Phase 5C.1](PHASE5C1_RESULT_PRIORITY.md) 與 `reports/phase5c1/validation.json`。

- 298 項自動化測試通過，包含 20 項新測試，所有測試正式提交使用隔離 SQLite。
- 三張原始結算 PNG 重新 OCR：12/12 個 P/C/B/M 欄位符合已知值；原有低信心評級仍需人工確認。
- 實際瀏覽器在隔離完整歌曲庫副本驗證：1194/1192 差異人工提交 EX2320／CLEAR／MAX NULL；P835/C3/B0/M0 保存 EX1673／PENDING、沒有 NEW LAMP；可信新結算自動提交 EX2461／CLEAR／MAX NULL。
- 舊8筆正式成績及 Session 逐列保持，原始截圖不變；Schema 8 遷移保留所有 ID、表內容與舊 bests，備份可核對。
- 本階段沒有新的真實遊玩端到端測試，沒有證實特殊音符或差異固定規則。

---

以下為前階段報告，保留當時測試與規則的歷史背景；最新 MAX 規則以上方 Phase 5C.1 為準。

# Phase 5C 驗證（0.11.0／schema 7）

**278 項自動化全部通過**（原 244＋新 34，無略過）。三張真實原圖重新執行 OCR，P/C/B/M 12／12 相同。完整 SQLite 副本的實際 GUI 驗證 Heart 人工差異提交、可信新 Session 自動保存、低信心待確認、PENDING 不升 AC／ACP、重啟保留模式與證據、不同 Session 同分 SAVED、跨頁不重播及本地時間。

Schema 6→7 的 pre-v7 備份驗證及遷移逐表內容核對通過；原始 Pack／Song／來源／難度 ID 保留。開發期間正式程式另有掃描與四筆人工提交，全部保留，沒有還原至開工快照；隔離測試 ID 均不在正式庫。原始截圖雜湊相同。正式自動模式保持 MANUAL，沒有代替使用者啟用或提交。這次沒有受控的新一次真實遊玩端到端測試。

詳見 [Phase 5C 操作、修改檔案與實玩驗證步驟](PHASE5C_AUTOMATION.md)、`reports/phase5c/validation.json`、`production-migration.json`、`gui-checks.json`、`real-ocr.json` 與 `unittest.log`。

# Phase 5B 驗證（0.10.0／schema 6）

**244 項自動化測試全部通過**（原 226＋新 18）。完整 SQLite 隔離副本的實際 Web GUI 操作驗證首次 CLEAR、AC／ACP 升級、同時 NEW SCORE／NEW LAMP、同分 SAVED、FAILED 保留最佳、跨分頁同步、未儲存編輯保留、重整不重播及減少動畫。瀏覽器記錄實際 animationstart 事件；Heart of Witch 差異仍阻止提交，沒有成功提示。

無 migration；正式庫逐表內容、Schema SQL 與既有 Session／Review／原始截圖雜湊相同，PlayRecord 0。沒有新增真實遊玩端到端測試；八筆 GUI 提交全是清楚標示的合成資料，僅存在隔離副本。詳見 [Phase 5B](PHASE5B_LAMPS.md)、`reports/phase5b/validation.json`、`gui-checks.json`、`unittest.log` 與截圖。

# Phase 5A.1 驗證（0.9.1／schema 6）

**226 項自動化測試全部通過**（原 199＋新 27）。三張真實結算原圖重新 OCR、完整 SQLite 副本演練與實際 GUI 操作通過。Heart of Witch 保留 1194／1192 差異草稿，確認與正式提交均阻擋；Cagayake 在副本提交 MAX -3／AC，重送冪等。沒有新增一次真實遊玩端到端。

無 migration，正式庫全表雜湊與開工時相同：41 Pack／4415 Song／2966 檔案來源／2538 官方來源／7612 難度／PlayRecord0；原始 Session／Review／圖片保持不變。新下載後的既有數量不回退至歷史報告。差異原因仍未確認，不放行特例。詳見 [Phase 5A.1](PHASE5A1_NOTES.md) 與 reports/phase5a1/。

# Phase 5A 正式提交驗證（0.9.0／schema 6）

**199 項自動化測試通過**（原 173＋新增 26）。完整 SQLite 副本完成 5→6 migration、Cagayake／Astral 原圖回放提交、相同 Review 重送、多 Session 同分保存與 Record 後故意失敗整筆回滾；GUI 核對摘要、正式提交、NULL Cool 停用、歷史與實際後端重啟查詢通過。沒有新增一次真實遊玩端到端。

正式庫已備份並安全遷移 schema 6；舊資料逐表投影相同、所有 ID／名稱／路徑保留，40 Pack／4,397 Song／2,930 檔案來源／2,538 官方來源／7,612 難度不變。正式 PlayRecord 仍為 0，既有 Pending Review 未自動提交。備份與驗證見 reports/phase5a/production-migration.json；測試與 GUI 證據見 reports/phase5a/report.md。操作與修改檔案見 [Phase 5A](PHASE5A_SUBMISSIONS.md)。

# Phase 4C 多人補充驗證（0.8.1）

完整自動化 **173 項通過**（新增 12 項多人／背景重掃／本人列測試）。25 張原始 PNG 分類 25/25，另重辨識保存的 Astral Paradox 真實 Session 原圖 11/11 欄位正確。舊多人 GAME OVER 圖的 Cool=1 仍保留 NULL；未宣稱所有數字皆可靠。正式庫逐表雜湊保持一致、schema 5 不變、PlayRecord 0；原始 PNG 保持相同 SHA-256。

實際 SONG 全目錄在 SQLite 副本重掃兩次；既有 40 Pack／4,397 Song（含官方）／2,930 檔案來源的 ID、名稱與路徑保留。新檔案只匯入副本，不複製回正式庫。GUI 隔離回放驗證背景重掃、connect／其他 Pack 候選、人工選第二列與修正草稿後重新載入。這不是新一次真實多人遊玩端到端。最終統計及限制見 [多人補充](PHASE4C_MULTIPLAYER.md) 與 `reports/phase4c/multiplayer/report.md`。

# Phase 4C 結算 OCR 與人工確認驗證（0.8.0，歷史報告）

LV 語意修正後完整回歸為 **161 項通過**（原 157＋4）。確認 game_level 原為歌曲難度等級，修正 GUI 的「歌曲 LV」「遊玩完成狀態」及說明；人物等級不解析、沒有 player_level 欄位，未變更 Schema／ID／Session／待確認紀錄。實際 Cagayake 選歌與結算圖均為 LV20，EX1673／MAX1676、MAX -3、AC 預覽。新增證據見 `reports/phase4c/lv-semantics/report.md`／`validation.json`。

157 項自動化測試通過，包含原有 130 項。最初 22 張與新增 2 張旁觀 PNG 全數重新執行模型，24/24 分類正確；四張原始結算圖 44 個欄位中 43 個值正確（97.73%），42 個同時符合可靠門檻。多人 GAME OVER 的 Cool 1 受背景文字干擾而保留 NULL；低信心評級仍要求人工核對。新增旁觀畫面分類為 SPECTATING、不建立本人 Session；沒有 TC 的旁觀結算保留 9 個本人欄位為 NULL，不算成其他人的成績。

隔離 SQLite 完整副本已測試審核、重新載入、重送同一筆 PlayRecord（ID 1／1）及寫入失敗整筆回滾。這不是正式成績寫入入口；正式 SQLite 全表保持不變，原始截圖未修改，沒有 migration。Phase 4B 的既有結算另作重新辨識，EX1673／MAX1676，預覽 MAX -3／AC，不將它計為本階段新實玩。

Pack 顏色不再自動確認，加入 CONFIRMED／CANDIDATE／UNKNOWN 與獨立人工 Pack 確認；缺少受控 Hover／點選對照，仍需補拍。本階段未新增即時遊玩端到端驗證。完整修改、操作與限制見 [Phase 4C](PHASE4C_RESULTS.md)，逐圖 JSON 與副本演練見 `reports/phase4c/`。

# Phase 4B 即時監控驗證（0.7.0，歷史報告）

130 項自動化測試通過，原有 110 項持續通過；22 張原 PNG 的全圖與 8 次額外 ROI 共 30 次模型實跑、100 項欄位／狀態檢查全部通過。獨立 Windows 測試視窗驗證 WGC 遮擋／失焦、縮放、最小化、關閉與重啟；這與遊戲端到端分開計算。

使用者自行操作真實遊戲，已完成 SONG_SELECT → PLAYING → RESULT → WAITING_ROOM。Song 16〈Cagayake! GIRLS〉／HARD LV20／838 音符／88 秒，由使用者於 GUI 明確人工確認；Session `38f201bf-025a-4eae-9e73-a78d13fa9b3c` 的結算原圖只保存一次。仍為待處理結果，不更新 PlayRecord、最高分或燈號。

真實 GAME OVER、多人、網路錯誤與遊戲本體重啟尚未做端到端；本輪兩張 Pack 清單沒有足夠紫色選取證據，自動 Pack 辨識仍待實際驗證，保持待人工確認。沒有以歌名反推 Pack 或第一個候選綁定。

完整證據、實際耗時／CPU／RSS、正式庫全表與原圖雜湊稽核在 `reports/phase4b/validation.json`／`report.md`；操作與修改模組說明見 `PHASE4B_MONITOR.md`。schema 保持 5，正式庫未改動。本次實測的人工對應與 Session 已完整保留到預設 `data/monitor`，來源副本仍在測試報告目錄。

以下保留 Phase 4A 的歷史紀錄。

# Phase 4A 靜態 OCR 驗證（0.6.0）

2026-10-22，CPython 3.11.5 x64／Windows 10，SQLite schema 保持 5。**110 項測試全部通過，無略過**（原有 98 項＋12 項靜態 OCR／匹配安全測試）。另外完整執行原始 22 張 PNG 的 ONNX CPU 推論，沒有使用快取替代本輪模型執行，約 23 秒。

實際畫面分類 22／22；可見選歌欄位完全相同 29／29（主要欄位 26＋Artist 3）；遮蔽欄位保持 NULL 6／6；TC 結算狀態 4／4。這是本批開發樣本的已標註欄位比率，不是全畫面文字、Pack 選取或未知圖片的泛化準確率。

JOINT 的兩個版本讀出 HARD LV17／821／96秒及 HARD LV20／929／95秒；全庫同名候選 5 首，人工指定 Pack 29 的兩次獨立實跑均返回 Song 990／1004，因正式庫仍缺已確認難度／時間而 pending，不產生綁定。

正式庫仍為 40 Pack、4,397 Song（1,859 自製／connect＋2,538 官方 K）、2,930 檔案來源、2,538 官方來源、7,612 GameDifficulty、0 PlayRecord；開始前、OCR 前後與獨立驗證的逐表完整雜湊皆相同。22 張 PNG SHA-256 全相同；integrity_check=ok，foreign_key_check=[]。

結果：`reports/phase4a/results.json`、`report.md`、`report.html`、`unittest.log`；Pack 29 的獨立實跑在 `joint-a/`、`joint-b/`。人工標註在 `tests/fixtures/phase4a_ground_truth.json`，只在辨識完成後作評估；完整限制與可執行命令見 `PHASE4A_OCR.md`。

```powershell
.\.venv-ocr\Scripts\python.exe -X utf8 -m dance_results.ocr_static --no-cache --labels tests/fixtures/phase4a_ground_truth.json
python -X utf8 -m tools.validate_phase4a
$env:DANCE_SAMPLE_ROOT = 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -X utf8 -m unittest discover -s tests -v
```

以下保留官方匯入時的歷史驗證。

# Phase 3.5 官方 K 匯入驗證（0.5.0）

日期：2026-10-22（Asia/Tokyo）。Windows、Python 3.11.5，SQLite schema 5。官方 CSV 匯入已完成，與自製 OCR／時間匹配開發分開，不以尚未實作的 OCR 作前置條件。

## 正式匯入結果

| 項目 | 匯入前 | 匯入後 |
|---|---:|---:|
| schema | 4 | 5 |
| 實體 Pack | 40 | 40 |
| 原有自製／connect Song | 1,859 | 1,859 |
| 官方 K Song | 0 | 2,538 |
| 全部 Song | 1,859 | 4,397 |
| 原有檔案來源 | 2,930 | 2,930 |
| 新官方來源 | 0 | 2,538 |
| GameDifficulty | 0 | 7,612 |
| PlayRecord | 0 | 0 |

只匯入 K=2,538，排除 T=2,513、空白=1。4 列 hidden=1 的 K 依規格保留，不依 src／enc 另行篩選。fileId 10026 的 sdom0026k.gn／sdom0921k.gn 分別保存，沒有合併或覆蓋。84 組同名標題仍按來源身份分開。

fileId 12140〈動畫歌曲串燒〉只建立 EASY LV26／3417 音符／MAX6834；NORMAL／HARD 的 0／0 保留原始證據，不建立假難度或 MAX0。BPM 393 列小數與原始多語名稱保留。

正式庫同一 CSV 再匯入：新增 Song=0、來源=0、Difficulty=0，相同 Song=2,538；完整資料稽核與重匯前完全相同。原有 40 Pack／1,859 Song／2,930 檔案 Source ID 逐項比對保持穩定，沒有寫入 JOINT 範例數值或自製難度。

原始 CSV SHA-256：`08c93228726802f7935895eea66649a14ceddbc8964ac23b7b96c4332503b28b`，匯入後仍一致。integrity_check=ok、foreign_key_check=[]。正式庫沒有執行自製歌曲重掃。

## 備份與交易

schema 4→5 備份：`data/backups/library.pre-v5.20261022T100147277140Z.8428073f.sqlite3`（本機執行產物，不隨專案分享）。

官方匯入前 schema 5 備份：`data/backups/library.pre-official-import.20261022T100149064082Z.572193af.sqlite3`（本機執行產物，不隨專案分享）。

兩份均由 SQLite backup API 建立並驗證全部資料／完整性。Migration 前後以舊欄位完整投影核對，保留原有名稱、路徑、bytes、歷史及 ID，只新增 root_kind、官方表及 official_csv 難度來源 CHECK。匯入前後以既有列範圍逐表雜湊核對，原有資料不變。

Migration 與匯入是兩個安全交易；已遷移成功後，若歌曲匯入失敗，匯入整筆回滾但 schema 可維持 5。沒有宣稱跨兩個已提交交易全部退回 schema 4。

正式證據：`data/phase35-official-import-result.json`；完整副本與 GUI 證據：`reports/phase35-validation-20261022-185130/`。

## 測試結果

**98 項測試全部通過，無略過**。原有 85 項持續通過，新增 13 項官方匯入驗證；舊 migration 的最後版本斷言更新為 5，舊欄位身份稽核保留。schema 4 本身的備份／回滾測試仍獨立執行。

```powershell
$env:PYTHONUTF8 = '1'
$env:DANCE_SAMPLE_ROOT = 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -m unittest discover -s tests -v
```

涵蓋 K／T／空白排除、同 fileId 不同 gn、同名不同 Song、多語與 BPM 原值、0／空白難度、非法數值／解碼／重複來源拒絕；相同 CSV、換路徑／排序重匯 no-op；來源變更與人工值衝突不覆寫；中途失敗全回滾及已驗證備份；schema 5 非空歷史／額外外鍵／view／index 保留與晚期 DDL 失敗回滾；獨立無自製庫匯入與重啟讀取；HTTP 預覽、超過 32 KiB 的上傳資料、匯入與 SHA 預覽核對；官方禁止被自製手動編輯／掃描流程覆寫；資料庫在 SONG 內時寫入前拒絕。

JavaScript node --check 與 Python compileall 通過。測試輸出保存於上述目錄的 tests.txt。

## 完整副本與實際 GUI

先以正式庫唯讀 SQLite backup 建立副本，完成 migration、全量 2,538／7,612 匯入及重匯，再操作 GUI。

- 官方分類 2,538、自製 1,782、connect 77；原實體 Pack 為 40。正式 GUI 中文〈野蠻遊戲〉、日文〈ウレシ泣キ〉、英文〈Funky Music〉可搜尋顯示。
- 10026 的兩個獨立 Song ID 為 1880／2493，各自顯示 gn。12140 未建立 NORMAL／HARD，GUI 顯示未設定。
- 10001〈野蠻遊戲〉三難度 MAX 分別 1020／1196／1488。官方詳情明確標示 mode K、fileId、BPM、CSV 路徑與記錄位置，Pack／SongFolder 不適用。
- 副本連續重掃實體 SONG。遊戲期間 connect 持續增加下載：第一次新增 1 Song／3 Source，第二次新增 1 Song／1 Source。兩次沒有標記來源消失；原有 Pack、1,859 Song、2,930 Source 的 ID、名稱、路徑與 SHA-256 全部保留，官方來源、7,612 個難度及歷史逐列保持相同。原有來源 payload 首次重掃更新 2,930 筆，是補入 0.4.1 新的基本 BPM metadata 欄位，不是 ID 重建。
- 兩個新增下載為 Love Is Beautiful Pain、ラブレター，僅存在測試副本；正式匯入沒有把副本覆蓋回正式庫。
- 瀏覽器 console 無 error／warning。GUI 截圖 gui-official.png，操作證據 gui-verification.json；重掃與逐項比較見 rescan-verification.json。
- 4,397 首整體目錄初次服務層查詢約 89 ms（副本測量，不含瀏覽器／HTTP），仍每頁渲染 60 首。

GUI 檔案選擇以標準瀏覽器 FileReader 上傳至本機；預覽／匯入／大檔 payload 已以 HTTP 整合測試驗證。實際瀏覽器驗證包含歌曲瀏覽、搜尋、難度顯示及背景更新。

## 交付與後續邊界

新增 official_import.py、migrations_v5.py、sqlite_safety.py、tests/test_official_import.py；更新 database／library_service／gui 及 HTML／JS／CSS。LibraryGUI.cmd 與既有 Start.cmd／ViewLibrary.cmd 保留。官方可獨立 CLI 匯入，不要求已有自製 SongFolder；GUI 新增匯入預覽／執行入口。

正式版本 0.5.0／schema 5。時間欄位、JOINT 六條件匹配、OCR、成績紀錄與 Clear Lamp 開發均未開始；其規格另保留在 PHASE4_MATCHING_SPEC.md，不阻塞官方匯入。原有多人與個人最佳邊界測試持續通過。
