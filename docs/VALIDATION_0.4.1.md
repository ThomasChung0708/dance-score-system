# Phase 3 GUI 驗證（0.4.0）

日期：2026-10-22。Windows、Python 3.11.5、SQLite schema 4、報告 JSON schema 5。

## 交付與架構

沿用 HTML／CSS／原生 JavaScript、Python 標準函式庫及 SQLite。新增 127.0.0.1 本機 HTTP API，沒有更換框架或新增套件。`LibraryGUI.cmd` 或 `python -m dance_results.gui` 啟動；保留 Start.cmd、ViewLibrary.cmd、既有掃描與離線報告入口。

三欄 GUI 提供 Pack 數量與紫色選取、官方／自製／多人下載分類、connect、搜尋／排序／分頁、完整來源資訊，以及 EASY／NORMAL／HARD 手動編輯。未知值顯示「未設定」，不從 SM／DWI／osu 原始難度填值。遊玩成績、燈號及 OCR 只預留顯示位置。

## 正式資料庫 migration 3 → 4

僅擴充 `game_difficulties.level_source`／`notes_source` 的 CHECK，接受 `manual`。手動輸入不冒充 game_screen。

| 項目 | 遷移前 | 遷移後 |
|---|---:|---:|
| schema | 3 | 4 |
| Pack | 40 | 40 |
| Song | 1,859 | 1,859 |
| Source | 2,930 | 2,930 |
| scan_runs | 6 | 6 |
| GameDifficulty | 0 | 0 |
| PlayRecord | 0 | 0 |

先用 SQLite backup API 建立並驗證備份，再交易替換 difficulty 表。全部使用者資料表逐列雜湊前後完全相同，包含 Song／Pack／Source ID、來源名稱／路徑、舊編碼 bytes、歷次掃描、別名及歷史。唯一新增資料是 migration 4 紀錄。再次開啟不重跑 migration。`integrity_check=ok`、`foreign_key_check=[]`，外鍵 enforcement 已恢復。

備份：`C:\Users\User\Documents\ChatGPT\熱舞online 仿 iidx計分 成績單\data\backups\library.pre-v4.20261022T064109951708Z.c3ccadc0.sqlite3`。

完整遷移證據：`data/phase3-migration-result.json`。正式庫未寫入任何 GUI 測試用 LV 或音符數。

## 測試

```powershell
$env:PYTHONUTF8 = '1'
$env:DANCE_SAMPLE_ROOT = 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -m unittest discover -s tests -v
```

**81 項測試通過，無略過**：原有 69 項持續通過，新增 12 項 GUI／migration 測試。遷移版本斷言隨 schema 4 更新，既有 ID／歷史與多人邊界斷言保留。

新增驗證涵蓋：非空難度／成績外鍵及額外歷史表、view／index、備份一致、晚期失敗回滾；40 Pack 類型查詢邏輯、connect、同名多語名稱、分頁排序；空白卡不建立難度、手動新增修改與清空 NULL、三難度獨立、同名不同 Song 保持未設定；錯誤 Song ID／資料夾／根目錄與過期 token 拒絕；非法數字與原始難度名稱拒絕；背景重掃及新增下載保留值／ID；掃描互斥及根目錄缺失保護；HTTP 儲存後關閉並重新建立伺服器仍讀回資料、靜態頁面、token／Host／Origin 檢查。

JavaScript `node --check dance_results/gui_assets/app.js` 與 Python `compileall` 通過。

## 實際 GUI 操作驗證

使用完整正式庫的 SQLite 副本測試寫入與掃描，再切換正式資料庫唯讀瀏覽確認。證據位於 `reports/phase3-validation-20261022-153306/`。

- 全庫 40 Pack／1,859 Song／2,930 Source；custom 1,782、connect 77、official 0，沒有自行判定官方歌。
- `crossing field` 搜尋 6 個獨立 Song，顯示各自 ID、Pack 及 SongFolder。中文「千本人妻」及日文「不埒な喝采」可查詢顯示；待確認名稱保持狀態，不自動採用解碼候選。
- connect 篩選 77 首，分頁可顯示 61–77；名稱正反排序可操作。
- 在副本 Song 398／Blue's 1st step\eos[Blue] 勾選 HARD，輸入測試 LV 25、音符 153，儲存成功、重新載入讀回，來源為 manual。這些僅為測試值，正式庫仍零難度列。
- 透過 GUI 連續執行兩次完整掃描，均新增歌曲 0／來源 0、更新歌曲 0／來源 0。既有需確認診斷計數 166，不代表新增身份衝突。兩次 complete_scan=true，來源消失 0。
- 兩次掃描後 Pack ID、全部 Song／Source ID、名稱、路徑及來源 SHA-256 與正式庫一致，副本手動值保留。副本 integrity_check=ok、foreign_key_check=[]。新增 connect 歌曲及暫時缺失保留歷史另有自動測試。
- 正式 GUI 選取 Song 398，三個難度均未建立、LV／音符均空白，顯示未設定。瀏覽器 console 無 error／warning。紫色 Pack 選取與三欄版面以 gui.png 留存。

`gui-verification.json` 保存 UI 驗證，`rescan-verification.json` 保存兩次掃描與逐項比較，`tests.txt` 保存測試輸出，`baseline.json` 保存遷移前稽核。初次完整目錄快取約 43 ms；快取後搜尋約 0.4–0.9 ms（服務層測量，不含瀏覽器及 HTTP），GUI 每頁只渲染 60 首。

## 修改檔案

- `LibraryGUI.cmd`：圖形操作入口。
- `dance_results/gui.py`：本機服務及 API 路由。
- `dance_results/library_service.py`：歌曲查詢、精確綁定手動編輯及背景增量掃描。
- `dance_results/gui_assets/index.html`、`style.css`、`app.js`：三欄介面。
- `dance_results/migrations_v4.py`、`migrations.py`、`database.py`：manual 來源及安全遷移、舊報告讀取相容。
- `dance_results/__init__.py`：版本 0.4.0。
- `tests/test_gui.py`、`test_phase25.py`、`test_connect_final.py`：新增驗證及 schema 版本更新。
- README、DATABASE、REQUIREMENTS、VALIDATION：啟動方式、模型與驗證文件；原 0.3.6 驗證保留於 VALIDATION_0.3.6.md。

## 本期限制與後續

這是以本機瀏覽器呈現的 Windows GUI，尚未封裝 EXE。手動修改已建立的難度可清空數值，未提供刪除難度或人工處理名稱候選的功能。官方資料仍需實際來源。OCR、AC／ACP 操作、成績輸入及非 HOST 成績配對未啟用，既有多人模式資料結構與測試持續保留。

## 0.4.1 補充驗證

依使用者補充，DWI 讀取 TITLE／ARTIST／基本 BPM（SUBTITLE 沿用既有保存），不讀音符或 BPM 變化。source_bpm 保存原字串，例如 153.50 不改寫成 153.5，重複不同值不自行選取，未完成標籤僅保留原值；不影響可靠歌名。GUI 原始來源區顯示此基本原值。沒有改 schema、重新掃描正式資料庫或移除來源。

新增 4 項測試，總計 **85 項通過，無略過**：基本 BPM／註解／無效譜面與變化忽略、衝突及缺少分號保留、CP950 逐欄位恢復、SQLite 往返及 ID 穩定／無虛構難度。完整輸出 reports/phase3-validation-20261022-153306/tests-0.4.1.txt。
