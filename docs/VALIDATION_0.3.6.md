# Phase 2.5 完成版驗證（0.3.6）

日期：2026-10-22。Windows、Python 3.11.5、SQLite schema 3、報告 JSON schema 5。僅完成 connect 關聯與名稱／來源資料保留，未開發 GUI 或 OCR。

## 修正與原因

舊 scanner 將 connect 的 pack_name 設為 NULL，database.sync 另有排除 connect 的檢查。77 首歌曲、181 個來源一直存在，缺少的是管理 Pack 關聯。

本版新增安全 migration 2→3：

- packs 增加 pack_type，既有 39 個 Pack 為 normal；新增 connect（Pack ID **40**），類型 multiplayer_download。
- 77 首既有 connect Song 掛接至 Pack 40，181 個 Source 經原 song_id 即可透過 Pack 查詢，不重建任何身分。
- song_sources 增加 saved_artist，保存來源最後可確認 Artist。Title／Subtitle、原路徑及編碼等保存於既有欄位與 payload_json。
- 更新掃描與快取載入；即使遷移後尚未重掃，也從 Pack 外鍵正確顯示 connect，不被舊 JSON 的空 Pack 名稱遮蔽。

每次遷移先鎖定寫入、用獨立唯讀連線及 SQLite backup API 建立備份並核對，然後在同一交易修改。稽核涵蓋既有 Pack、Song、Source、掃描快照、原始 BLOB、別名、難度、Session 與紀錄。只有 connect 的 pack_id 是預期變更；既有名稱、來源路徑、ID 與歷史不變。DDL 失敗或後段稽核失敗都會回滾。

## 正式庫 migration 前後

| 項目 | 遷移前（schema 2） | 遷移後與兩次重掃（schema 3） |
|---|---:|---:|
| Pack | 39 | **40**；原 39 個 ID／名稱／路徑原封保留 |
| connect Pack 關聯 | 無 | Pack ID 40，multiplayer_download |
| 可經 connect Pack 查詢的歌曲 | 0 | **77**；原 Song ID 不變 |
| 可經 connect Pack 查詢的來源 | 0 | **181**；原 Source ID 不變 |
| 全部 Song | 1859 | **1859**；ID／相對路徑全相同 |
| 全部 Source | 2930 | **2930**；ID／Song 關聯／來源路徑全相同 |
| scan_runs | 4 | **6**；原 4 筆完整內容保留，另新增 2 次重掃 |
| 原始編碼 bytes 快照 | 43 | **43**；原始 bytes 逐筆相同 |
| GameDifficulty | 0 | 0；未建立檔案難度、LV 或音符數 |
| PlayRecord | 0 | 0；未寫入測試遊玩紀錄 |
| quick_check | ok | **ok** |
| foreign_key_check | 無違反 | **無違反** |

備份：[library.pre-v3.20261022T055728582656Z.b66a0c82.sqlite3](../data/backups/library.pre-v3.20261022T055728582656Z.b66a0c82.sqlite3)。備份仍為 schema 2，內容已核對。

- [Migration 稽核](../data/phase25-final-migration-result.json)：所有原有資料的 before／after fingerprint 一致（排除預期的 connect pack_id 變動及新增欄位／列）。
- [兩次重掃與全部身分／名稱／路徑稽核](../data/phase25-final-verification.json)。

上述核對並非只比對總筆數，也逐筆比較 ID、相對路徑、已存在的 Title／Subtitle／title_fields、已保存名稱、舊快照及來源 SHA-256。先前空白欄位新增可讀值是允許的資料補充，不算既有名稱遺失或覆寫。

## 重新掃描

| 指標 | 第一次 | 第二次 |
|---|---:|---:|
| 新增歌曲 | 0 | 0 |
| 新增來源 | 0 | 0 |
| 更新歌曲 payload | 1859 | 0 |
| 未變歌曲 | 0 | **1859** |
| 更新來源 payload | 2930 | **0** |
| 未找到歌曲／來源 | 0／0 | 0／0 |

第一次更新包含 Artist、編碼資訊、connect 管理 Pack、來源格式提示及名稱可靠性欄位。第二次完全穩定，沒有重複新增。

- [第一次重掃](../reports/20261022-145803-773167/report.html)
- [第二次重掃](../reports/20261022-145839-103681/report.html)
- [不重掃的已存歌曲庫檢視](../reports/20261022-145902-246185/report.html)

所有 2930 個來源的 SHA-256 與遷移前相同，原始檔案未被修改。

## 名稱、Artist 與格式

來源分開保存 Title、Subtitle、Artist／ArtistUnicode 的原值及候選，實際使用的整檔／欄位編碼、原始路徑、SHA-256、待確認旗標也留存。Artist 不統合成歌曲身分，不以作者或名稱合併 Song。共解析出 **2927** 個非空 Artist 值，其中 **2924** 個可確認保存為 saved_artist；其餘有疑似亂碼或未結束標籤，保留來源值供核對，不冒充可靠 Artist。

同一 SongFolder 的不同 Title／Subtitle 不拆成多首 Song。850 個 SM＋DWI 共存改為資訊提示；仍有 851 個多格式來源組合，但不把它當成 851 個錯誤。特殊資料夾

`Blue's 5th step(no video)\Lachryma[ReQueenM][Blue]`

保留 Song 603 及 Source 1183／1184／1185，以 SOURCE_FORMAT_REVIEW 標記供日後人工檢查，沒有移動、刪除或拆分。

原有 101 個來源名稱警示全部保留。從先前無法讀取的來源恢復名稱後，以下另 4 個資料夾出現來源名稱差異，共 **105** 個待確認，並非新增 ID 衝突：

- Song 275：`Barry\Kimi ∞ connect`
- Song 371：`Barry\VOY＠GER`
- Song 1239：`TkC-Y2's 1st Renerve Pack\10`
- Song 1249：`TkC-Y2's 1st Renerve Pack\8`

所有遷移前已解析或已保存的非空名稱均保留。上述歧義不自動選優先來源、不覆寫成另一候選。

## 原 43 份編碼來源

整檔嚴格解碼失敗時，保留原始 bytes、錯誤 offset 及原因，再對需要的名稱欄位獨立嚴格解碼。已可讀的 UTF-8 名稱不因 MUSIC／FILE 等其他欄位舊編碼而遺失；明確指定 CP932 也不覆寫已有效的 UTF-8 名稱。

| 結果 | 數量 | 正式處理 |
|---|---:|---|
| 可確認 UTF-8 Title／Subtitle | **39** | 保存名稱及欄位編碼；整檔非名稱欄位錯誤仍保留 |
| 多解（Source 674） | 1 | 原始 bytes、CP932／Big5 候選並存，待人工確認 |
| 疑似既有亂碼（Source 740） | 1 | 保留原 Title／Artist 及診斷，不覆寫已保存名稱 |
| CP932 候選（Source 880） | 1 | 未自動採用，待確認 |
| Big5／CP950 候選（Source 2265） | 1 | 未自動採用，待確認 |

39 份恢復名稱與另外 4 份待確認的逐來源資訊在 final-verification JSON。另保留此次匯入前的 [編碼試讀結果](../data/phase25-final-encoding-preview.json)。未知編碼未使用 ignore／replace；原始 43 份 BLOB 皆保留。

## 自動測試及範圍

```powershell
$env:PYTHONUTF8='1'
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -B -m unittest discover -s tests -q
python -m compileall -q dance_results tests tools
```

**69 項全部通過，無跳過**，含使用者提供的真實 SM／DWI／osu 樣本。compileall 通過。既有 HTML 實際搜尋／組合篩選腳本的 Node DOM fixture 檢查通過；沒有宣稱已完成瀏覽器視覺驗證。

本次新增 12 項針對性測試：

- schema 2→3 備份與既有 ID／名稱／路徑／歷史保留，重開庫不重跑；唯讀入口不遷移。
- DDL 失敗與掛接後的晚期稽核失敗，都回滾新增 Pack／關聯／欄位。
- 已存在 connect Pack 時重用其 ID，不重複建立。
- 兩次重掃、connect 新增同名歌曲正常匯入；原有同名不同資料夾保持獨立。
- connect 來源暫時消失／恢復時保留身分與既有遊玩紀錄。
- 多解及疑似亂碼不覆寫已知 Title／Subtitle／Artist；其他欄位舊編碼不抹去可靠 UTF-8 名稱。
- osu ArtistUnicode、UTF-8 BOM、逐欄位編碼及明確指定舊編碼的保留規則。
- SM＋DWI 資訊提示與三格式人工檢查標記，不拆歌。

## 修改檔案

- `dance_results/migrations_v3.py`：新增安全 connect／saved_artist migration；`migrations.py`：schema 3 及舊版稽核相容。
- `database.py`、`scanner.py`：Pack 掛接、增量匯入、快取依外鍵載入及來源名稱／Artist 保留。
- `models.py`、`songs.py`：欄位編碼／候選／可靠性、多格式資訊及人工檢查標記。
- `parsers/common.py`、`parsers/osu.py`：Artist 基本欄位；`parsers/base.py`、新增 `parsers/metadata_encoding.py`：嚴格逐欄位讀取及原始 bytes 保留。
- `report.py`：既有報告顯示 Artist／編碼及更精確的名稱提示；`__main__.py`：逐版本 migration 訊息；`__init__.py`：版本 0.3.6。
- 新增 `tests/test_connect_final.py`；調整原資料庫／名稱／migration 測試。
- `tools/verify_phase25_final.py`：一次性正式遷移／雙重掃描稽核；`--verify-existing` 只使用備份與現有兩次結果再核對，不增加掃描。
- README、DATABASE、REQUIREMENTS、本文件；前版驗證保留為 VALIDATION_0.3.5.md。

Phase 2.5 已完成，可準備 Phase 3。待確認名稱、4 份編碼、特殊三格式及調查中的混置資料夾保持原狀；沒有提前開發 GUI／OCR、人工修正介面或成績擷取。
