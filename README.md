# 熱舞 Online · 個人成績單與成長履歷

> **第一次使用？** 請看白話版 [使用手冊](docs/USER_GUIDE.md)：安裝、設定遊戲資料夾、看成績、常見問題，一步一步說明。

## 最簡單的用法：遊戲資料夾模式（不用 OCR、不用安裝 Python）

1. 雙擊 `LibraryGUI.cmd`（只需安裝 Python 3.10 以上，不需其他套件）；或執行 `DanceScore.exe`（從原始碼產生：雙擊 `BuildExe.cmd`，產物在 `dist\`，資料放在 exe 旁的 `data`，與原始碼目錄的 `data` 是不同的資料庫）。會開啟本機網頁；保持黑色視窗開著。
2. 按右上「遊戲資料夾」，貼上有 `dance.exe` 的資料夾路徑，按「儲存並同步」。
3. 在「角色」下拉選單選自己的角色。
4. 照常打歌。每首打完，成績自動存進來；官方歌、外部歌（含 StepMania／osu 資料夾）的歌單也跟遊戲同步。

需要遊戲版本會寫 `results.jsonl` 成績紀錄。運作方式、讀哪些檔、格式見 [遊戲資料夾模式](docs/GAME_RESULTS.md)。
以下 OCR 監控與手動流程仍保留，從原始碼執行時可用。

---

朋友在自己的 Windows 電腦首次安裝，請看 [朋友安裝與使用說明](docs/FRIEND_SETUP.md)。

目前版本 **0.15.2 / SQLite Schema 10**。沿用 HTML/CSS/JavaScript、Python、SQLite、增量掃描與 OCR／監控架構。關聯仍為 Pack → Song → GameDifficulty → PlayRecord，同名不同 SongFolder 保持獨立。

2026-11-08 BPM 修補：修正即時 ROI 的 BPM 標籤／數字拆框後未保留定位證據的問題。失敗場次原圖經相同 ROI 路徑讀出132／0.99512，427項測試通過；驗收工具另列身份確認與完整選歌資料狀態，不改正式匹配及提交政策。**這是靜態辨識修復，新的實玩雙幀與自動提交尚未驗收。** 見[根因、證據與下次實玩步驟](docs/PHASE5F1_BPM_ROI.md)。

Phase5F.1已改善小數字ROI、選歌BPM及歌曲LV辨識，並加入不提高原始信心的雙幀一致性檢查。〈新世界Remix〉原圖可讀出Miss1、BPM132、LV10並安全匹配Song2730；414項測試通過。沒有改Schema或既有正式成績，舊Pending不會自動重算／提交。修正後真正雙幀實玩與自動提交仍待新一輪驗證，見[修正比較、檔案及操作](docs/PHASE5F1_OCR.md)與[JSON證據](reports/phase5f1/validation.json)。

Phase5F已完成自動驗證部分：399項測試、27張原圖分類、隔離回放、獨立最佳／Recent、GUI提交去重與重啟、全SONG＋OCR效能，以及正式資料唯讀稽核。本輪沒有修改計分核心或Schema；真實視窗擷取成功，但新的單人／GAME OVER／多人下載到自動提交實玩驗收仍待進行。低信心欄位仍保留Pending，沒有為提高自動率降低門檻。見[驗證狀態與操作](docs/PHASE5F_VALIDATION.md)、[完整報告](reports/phase5f/report.html)與[JSON證據](reports/phase5f/validation.json)。


Phase5E已完成成績單、獨立最佳區塊與Progress History：歷史列表保留NEW SCORE／NEW LAMP／DOUBLE UPDATE及首次紀錄；分數與燈號分別顯示更新前後，原圖入口與手機版面一併整理。重用不可變提交證據，Schema10不變，不倒推或重算舊歷史。399項測試與隔離實際GUI通過；真實Song127的最高EX2029／#18、最佳AC／#19也已唯讀核對。見[操作與修改檔案](docs/PHASE5E_PERFORMANCE_UI.md)及[完整報告](reports/phase5e/report.html)。

以下保留Phase5D及先前階段的設計與歷史驗證紀錄；Phase5D真實多人下載到自動提交完整鏈仍待遊玩驗收。

本版將 My Best Score 與 Best Clear Lamp 完全獨立；可信 FAILED 可刷新 EX，未知完整 MAX 保持 NULL。周大俠 HARD 已依正式身份與 EX 公式證據恢復 #7 的3174，不改寫 #15。Schema9→10先備份、核對所有原資料不變；詳見 [本次修正與操作](docs/PHASE5D2_INDEPENDENT_BESTS.md)及[驗證報告](reports/phase5d2/report.html)。

Phase 5D 補充修正：NO MAP 只計入有本次來源變動證據且真正新增的 Song ID；舊歌更新、快照前來源另外記錄。GUI 預設只顯示摘要，人工需要時才展開相關新歌；全歌曲庫搜尋保留。見 [修正說明](docs/PHASE5D1_NEW_SONG_SCOPE.md)。

Phase 5D 已加入本人 NO MAP 觀測、metadata 快照、PLAYING 背景全 SONG 掃描與 Session 下載候選。來源不足仍人工確認；MANUAL 不自動提交。歌曲庫新增 Recent Update，只列正式首次建立或嚴格刷新 My Best 的最新 30 個 Song ID＋難度；沿用持久化成就證據，不新增 Schema。350 項測試與 27 張原圖回歸通過；真實多人下載端到端仍待驗證。操作與限制見 [Phase 5D 說明](docs/PHASE5D_DOWNLOADS_RECENT.md)，本次報告見 [隔離驗證報告](reports/phase5d/report.html)。

正常完成且判定、歌曲身份與本人成績列已確認時：

- `judged_total = Perfect + Cool + Bad + Miss`
- `EX Score = Perfect × 2 + Cool`
- **本系統自訂 MAX = judged_total × 2**，並非遊戲官方原生 MAX 定義。
- 差值0～150顯示 MAX -差值；更大時顯示百分比（兩位小數）。

CSV／GameDifficulty 的音符數保留為譜面參考，不以結算合計覆寫。CSV 差異只作提示，不再要求正常完成填寫獨立 MAX 證據或四項差異例外。GAME OVER 不以部分判定建立完整 MAX；沒有其他可信完整基礎時維持 NULL。

## 操作與保護

執行 [LibraryGUI.cmd](LibraryGUI.cmd) 或 [MonitorGUI.cmd](MonitorGUI.cmd) → 待確認成績，核對歌曲／難度、本人成績列與判定，另存確認後再正式儲存。保留未知值、原圖、原始OCR及人工修正紀錄。

MANUAL／VERIFIED_AUTO 保留。可靠新結算可自動儲存；低信心、缺失、身份／本人／Session不可靠、跨場次合計衝突都進待確認。**CSV差異的新自動規則仍待實玩驗收，此版先由人工提交這類結果。** 不自動提交啟用模式前的舊待確認資料。

同一 Song ID＋難度出現不同 judged_total 時，保存各筆自己的自訂 MAX，但本筆燈號 PENDING、停止自動提交與最佳刷新，排名用差值／百分比暫停。已知 EX 制度且身份、本人列與公式證據可靠時可以比較 EX；MAX 定義仍分開。未分類舊資料不自動作跨制比較。NEW SCORE／NEW LAMP與原動畫只在成功提交且可比較時觸發。

Schema8→9只修改提交驗證與最佳查詢，先備份並逐表核對；**不重算舊成績、不改 Song ID、不重建歌曲庫**。317項測試、原始PNG OCR回放、隔離GUI、修改檔案、來源保存與限制見 [Phase5C.2 完整說明](docs/PHASE5C2_CUSTOM_MAX.md)；測試產物在 `reports/phase5c2/`。

以下為既有 OCR、歌曲庫與啟動功能；歷史階段報告中的舊 MAX 規則，以上方 Phase5C.2 為準。

## 結算 OCR 與待確認成績

啟動 `MonitorGUI.cmd`／`LibraryGUI.cmd`，點「待確認成績」。既有結算可按「辨識此結算」，新版即時監控會重用當次辨識資料建立待核對項目。支援本人資料列、P／C／B／M、Combo、原始分數、評級、正常完成／GAME OVER；可修正數字、選擇獨立 Song ID、保存草稿與人工核對，重啟後保留。空白不補 0；多人底部歌曲 LV 仍需核對本人所選譜面，不挪用 HOST 的另一難度。

多人無 Pack 時預設搜尋全部 Custom Pack（包含 connect），可分頁查看獨立 Song ID、Pack、SongFolder 與來源檔案；不會自動選第一首。待確認頁新增「重新掃描 SONG 並更新候選」，重用背景增量掃描，涵蓋所有 Pack；未完成來源所屬 SongFolder 延後更新。支援人工選擇本人成績列、核對歌曲時間及保存確認依據。歌曲取得方式未知時不依 Pack 名稱推定。操作、測試與限制見 [多人補充說明](docs/PHASE4C_MULTIPLAYER.md)。

提供共用 EX Score、MAX 差距／百分比與 ACP／AC／CLEAR／FAILED 核心。先保存人工確認，再按「確認並儲存成績」核對完整摘要並正式提交。每次新提交前備份，以單一交易保存；Session／Review 唯一限制避免重複。正常完成必須核對完整音符；FAILED 可保存部分成績，未知 MAX 保持 NULL。歷史頁提供查詢、原始證據及各難度的最高 EX／最佳燈號與刷新提示。見 [Phase 5A 操作與設計](docs/PHASE5A_SUBMISSIONS.md)。

Pack 清單顏色目前只產生 CANDIDATE，不能排除 Hover；可在關閉清單後獨立人工確認 Pack，重開清單會撤銷舊確認。測試結果、修改檔案與需要補拍的四張對照圖見 [Phase 4C 說明](docs/PHASE4C_RESULTS.md)。

## 即時畫面監控

雙擊 `MonitorGUI.cmd`，或啟動歌曲庫後按「即時監控」。可搜尋／選擇遊戲視窗、開始／停止、調整頻率、查看目前歌曲／Pack／Session／候選及最後結算。Windows 取樣、OCR 與 GUI 分開執行；一般失焦不停止擷取。只用視窗影像，不讀取記憶體、不注入、不操作遊戲。

已通過使用者自行遊玩的〈Cagayake! GIRLS〉選歌 → 遊玩 → 結算流程；Song ID 16 是使用者於 GUI 人工確認，結算只保存一次。Session／人工對應／截圖在 `data/monitor`，SQLite、原始 PNG 與官方 CSV 不修改。沒有足夠證據的 Pack／Song ID 保持待確認；本輪真實 Pack 清單未取得可靠紫色選取，不能宣稱已通過真實自動 Pack 追蹤。

原 Phase 4B 安裝、資料位置、效能數字及模擬／真實測試的區分見 [Phase 4B 操作說明](docs/PHASE4B_MONITOR.md)。結算辨識與計分預覽於 Phase 4C 完成；Phase 5A 已加入明確正式提交、最高 EX 與最佳文字燈號查詢。

## 靜態 PNG OCR

雙擊 `OCRStatic.cmd` 辨識 `tests/專案測試圖片/` 的所有 PNG，輸出 `reports/phase4a/results.json` 與可閱讀報告。實際 22 張原圖已完成測試；JOINT 的兩個版本可辨識出不同數值，但缺少已驗證資料庫難度／時間時仍待人工確認，不自動綁定。完整安裝、啟動、精確率定義與限制見 [Phase 4A 說明](docs/PHASE4A_OCR.md)。靜態入口獨立運作，不啟動即時擷取。

## 圖形操作介面

雙擊 **`LibraryGUI.cmd`**，會開啟本機歌曲庫。保持啟動視窗開啟；關閉服務時按 **Ctrl+C**。網址為 `http://127.0.0.1:自動選擇的連接埠/`，只在本機提供服務，不需安裝套件。也可執行：

```powershell
python -m dance_results.gui
```

可加 `--database 路徑` 指定既有資料庫、`--no-browser` 不自動開瀏覽器、`--port 8765` 指定本機連接埠。尚未建庫時先使用 `Start.cmd` 匯入。

- 左側選取 Pack，紫色顯示目前選取項目；官方分類只顯示已匯入的 K 模式歌單，不自動從自製來源推測。connect 獨立列於「多人下載」。
- 中間搜尋中文、日文、英文名稱，也可搜尋 Pack、資料夾及保留的來源名稱。支援名稱正反排序，每頁 60 首。同名歌曲依 Song ID、Pack 與資料夾分開顯示。
- 右側查看 Artist、來源格式、原始路徑、名稱差異與編碼待確認資訊；展開「原始來源資訊」可查看各來源 Title／Subtitle／Artist／編碼及候選，DWI 另顯示來源 BPM 基本原值。
- 編輯前核對 **Song ID 與 SongFolder**，勾選「確認此難度存在」，輸入 EASY／NORMAL／HARD 的 LV 與總音符數，再按該難度的儲存。未知欄位可留白，已儲存欄位可清空再儲存為 NULL。此版不提供刪除已建立難度。
- 每次儲存會核對歌曲 ID、根目錄、相對路徑與版本 token；其他視窗已修改時要求重新載入，避免覆蓋或寫到同名歌曲。手動數值標記 `manual`，不冒充 OCR。
- 「更新歌曲庫」在背景執行既有增量掃描，顯示新增歌曲／來源、更新歌曲／來源及警告數。期間仍可瀏覽，暫停難度寫入；結束後自動更新清單。來源消失只標記，不刪除歷史。

舊資料庫會逐版安全遷移；schema 4→5 先建立並驗證 `data/backups/*.pre-v5.*.sqlite3` 備份。官方首次匯入前再建立 pre-official-import 備份；既有 ID、名稱、路徑、掃描快照及歷史逐表核對保留。

## 官方 K 模式歌曲匯入

> 網頁上的「匯入官方 CSV」按鈕已移除：設定遊戲資料夾後，官方歌會自動跟著遊戲的 `song_table.csv` 更新（見 [遊戲資料夾模式](docs/GAME_RESULTS.md)）。下面的命令列匯入仍可使用。

原本的手動匯入：預覽 K 筆數、排除數量、新增歌曲／難度及來源衝突，再正式匯入。同一份資料重複匯入新增為 0，來源內容或已存難度不同則整批停止，不覆寫既有資料。

也能獨立使用 CLI，不需要 OCR、時間欄位、自製歌曲掃描或 GUI：

```powershell
# 唯讀預覽，不執行 migration
python -m dance_results.official_import --csv '.\song_table.csv'

# 先備份，再執行安全 migration 與交易匯入
python -m dance_results.official_import --csv '.\song_table.csv' --import
```

可加 `--database 路徑` 指定獨立資料庫，`--result 路徑.json` 保存核對結果。GUI 的「更新歌曲庫」仍只更新自製／connect 資料夾，不會把官方來源當作消失。

只建立 **mode=K** 的 Song；T／空白排除，不修改原始 CSV。官方身份使用 catalog＋mode＋fileId＋gn，保留原始 fileId；同名、同 fileId 不同 gn 不合併。來源保存在獨立 official_song_sources，不改原有 2,930 個檔案來源 ID，也不增加虛構 Pack／SongFolder。

官方詳情顯示 fileId、Title、Artist、BPM 原值及 E／N／H 的遊戲 LV、完整音符數、MAX=音符×2。LV／音符皆 0 或空白時不建立難度；半缺值或非法數字拒絕整批匯入。官方卡目前為來源檢視，自製歌曲仍可手動編輯。CSV 的時間欄位尚不匯入遊戲時間，不阻塞後續 Phase 4 規格。

完整流程、身份與限制見 [官方匯入說明](docs/OFFICIAL_IMPORT.md)。

## 掃描與舊版離線報告

1. 雙擊本目錄的 **`Start.cmd`**，掃描／更新歌曲庫。
2. 選擇 SONG 根目錄，預設為 `D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG`。
3. 完成後自動保存到 `data/library.sqlite3`，並嘗試在預設瀏覽器開啟報告。若未自動開啟，依命令視窗路徑開啟 `report.html`。
4. 報告每個歌曲資料夾一列，可搜尋歌名、資料夾、來源檔名，以儲存群組／歌名讀取狀態篩選。

只看上次已保存資料時，雙擊 **`ViewLibrary.cmd`**，不會重新掃描歌曲檔案。此入口使用預設 SONG 路徑；其他路徑可用下方命令。

歌曲庫與 GUI 需要 Python 3.10 以上，本機有 Python 3.11.5 及 tkinter，不需額外套件。OCR 另外使用已驗證的 Python 3.11 獨立環境。這是 Python 工具加啟動檔，尚未打包 EXE，GUI 使用本機瀏覽器呈現；即時監控、結算 OCR 及人工確認已接入，正式成績寫入與燈號操作尚未實作。

## 資料來源

| 項目 | 取得方式 |
|---|---|
| 歌名 | SM／DWI 的 TITLE，SM 的 SUBTITLE 隨原始標題保留；osu 的 TitleUnicode／Title |
| Artist | 保存各來源 ARTIST／ArtistUnicode／Artist，不統合不同來源作者 |
| Pack／資料夾 | SONG 目錄結構；connect 為管理 Pack，pack_type=multiplayer_download，不冒充已驗證原始 Pack |
| EASY／NORMAL／HARD | 固定遊戲分類；實際存在狀態由使用者確認，後續可接畫面證據 |
| 遊戲 LV | 官方使用已確認 CSV 的遊戲數值；自製手動輸入，後續從本人畫面取得，不讀 SM／DWI／osu 難度數字 |
| 完整總音符數 | 官方 CSV 或手動遊戲值；後續可從本人畫面取得，非 HOST 可依已確認規則在正常完成後由判定合計 |

每個 SongFolder 是獨立歌曲，以根目錄及 Pack／SongFolder 相對路徑識別；同名、同 Artist、同 BPM 均不跨資料夾合併。SM＋DWI 保留為一般來源資訊，不因此拆歌；特殊 SM＋DWI＋osu 資料夾標記人工檢查，不選優先格式。檔案內的 Challenge、SMANIAC、osu Version 等不進入遊戲難度欄位。

若來源歌名不一致，保留候選，不自行選定；不同 Pack 或資料夾的同名歌不自動合併。無歌名時維持未知，不以資料夾或難度名稱補值。檔案 SHA-256 只作來源／唯讀核對，不是遊戲 Chart ID。

## 命令列

```powershell
python -m dance_results --root 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
```

只掃描指定樣本：

```powershell
python -m dance_results --folder "Blue's 1st step\eos[Blue]" --folder 'osu音樂\Gotta Get Thru This (Jauz RetroFuture Remix) (Nightcore [2b5a993e]'
```

| 選項 | 用途 |
|---|---|
| `--choose-directory` | 開啟 SONG 資料夾選擇器 |
| `--folder` | 只掃描 SONG 內指定的資料夾，可重複指定 |
| `--output` | 指定報告上層目錄，不可在 SONG 內 |
| `--open-report` | 完成後嘗試開啟報告 |
| `--legacy-encoding cp950` | UTF-8 失敗時依明確指定編碼解碼，不自動猜字 |
| `--database` | 指定 SQLite 檔案；預設 `data/library.sqlite3`，不可放在 SONG 裡 |
| `--library-only` | 只讀已保存資料產生清單；不掃描，不能搭配 `--folder` |

直接開啟已保存歌曲庫：

```powershell
python -m dance_results --library-only --open-report
```

舊版 `--include-events` 已移除，因為本版不解析音符。

每次報告新增於 `reports／時間戳記／`，包括 HTML 與 JSON。原始歌曲檔案只讀，報告不寫入 SONG。Ctrl+C 正常中止時保留部分結果，強制關閉可能不產生報告。

## 資料庫與重掃

- 同一 SONG 根目錄與歌曲資料夾保留同一歌曲 ID；重掃不會重複新增相同路徑。
- 每次掃描保存快照與診斷，來源檔案各有 ID。上次完整讀到的歌名與本次結果分開保存；本次失敗／衝突不蓋掉已保存歌名，也不將舊歌名冒充本次已確認結果。
- 完整掃描找不到的來源標記為未找到，不刪除歌曲、來源或遊戲譜面關聯。再次出現相同路徑時恢復原 ID。
- `--folder` 只更新指定範圍；中止、目錄讀取錯誤或略過連結時，不把未讀到的來源當作消失。
- 改名／搬移到不同歌曲資料夾時暫不自動合併：新位置建立新項目，舊位置保留待確認。相同檔案雜湊不能單獨證明是同一張遊戲譜面。
- `charts` 安全改名為 `game_difficulties`，只允許 EASY／NORMAL／HARD，實際可只有部分難度；由使用者確認存在後建立，未確認不自動建立。LV、總數未設定時為 NULL。
- `play_sessions`／`play_records` 預留多人 HOST、本人難度與 HOST LV 分離、本人失敗等待結算，以及來源未知待人工配對。未配對／未驗證不進入最高 EX 與最佳燈號；本期只建立資料結構及查詢，沒有成績擷取／輸入功能。
- 原始 Title 與遊戲顯示歌名透過 `song_title_aliases` 分開保存，不用顯示歌名當唯一識別。
- `ViewLibrary.cmd` 顯示的是保存資料，可能來自不同次掃描；未找到的來源會明確提示。這不代表目前已重新檢查。
- schema 2→3 補上 connect Pack 與來源 saved_artist；先用 SQLite backup API 備份及稽核，交易內遷移，失敗回滾。schema 1 可依序升級；不覆寫不相容資料庫。

本期已提供歌曲庫 GUI 與遊戲難度手動編輯；沒有接入 OCR、成績輸入／擷取或非 HOST 成績人工配對。資料表、分難度最佳查詢與多人邊界見 [docs/DATABASE.md](docs/DATABASE.md)。

## 掃描邊界

- 依 `SONG／Pack／Song` 層級掃描；不遞迴歌曲內的 FileBackup 等子目錄，會列出提示。
- 只從 `.sm`、`.dwi`、`.osu` 讀歌名；`.old`、`.osb`、`.dps`、`sdoinfo.dat` 另列，不當歌曲／譜面來源。
- UTF-8／UTF-8 BOM、UTF-16 BOM 可直接讀取；整檔因其他欄位編碼失敗時，逐欄位嚴格讀取名稱，保留完整錯誤及原始 bytes。CP932／Big5／CP950 僅依明確指定使用；多解或疑似亂碼待人工確認，不自選候選覆寫。建議搭配 `--folder` 限定範圍。
- 單檔上限 64 MiB；讀取期間檔案大小／修改時間變動則等重掃。不追蹤目錄／檔案連結及 Windows reparse point。
- 舊版 HTML 報告離線運作；GUI 以本機 HTTP API 存取 SQLite。來源文字以 textContent 呈現，沒有外部網路資源。

## 測試與文件

```powershell
$env:PYTHONUTF8 = '1'
$env:DANCE_SAMPLE_ROOT = 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -m unittest discover -s tests -v
```

測試涵蓋安全 migration／備份／回滾、同名不同資料夾、多語編碼及原始 bytes、缺少難度、重掃 ID／歷史保留、分難度最佳 EX／燈號及多人未配對／中斷邊界。這些不是 OCR 正確率測試。

- [已確認需求](docs/REQUIREMENTS.md)
- [新版驗證結果](docs/VALIDATION.md)

舊版 0.1.0 報告及 [歷史測試紀錄](docs/VALIDATION_0.1.md) 保留作追溯，原先的來源譜面數、物件數與去重結果不再用於現在的歌曲庫。
