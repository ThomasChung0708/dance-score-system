# Phase 5D：最高 EX 與最佳燈號獨立管理

版本 **0.14.0 / SQLite Schema 10**。本頁規則優先於舊階段文件中「FAILED 不刷新 EX」及「所有新舊 EX 都分組」的描述。EX 與 MAX 的相容性分開檢查。

## 周大俠調查

正式資料的 #7 與 #15 都是 Song ID **2726**、Difficulty ID **2601**、HARD、歌曲 LV22。

| 紀錄 | EX | 原存 MAX | 燈號 | 計分版本 | 判定合計 |
|---|---:|---:|---|---|---:|
| #7 | 3174 | 3232 | CLEAR | iidx-v1 | 1616 |
| #15 | 3067 | 3232 | CLEAR | custom-judged-v1 | 1616 |

原因是 Schema9 的 `difficulty_bests` 在已有新制成績時排除了所有舊制成績。資料沒有被 #15 覆寫；#15 原始提交事件是 SAVED，也沒有錯誤產生 NEW SCORE。

這兩筆各有正式人工確認的歌曲／難度身份、本人成績列、截圖雜湊及判定證據，EX 均符合 `Perfect×2+Cool`，沒有已知跨場次完整判定衝突。因此可比較 **EX**，正確 My Best 是 **#7 / EX3174**，最佳燈號是 **#7 / CLEAR**。不能只因 MAX 都是3232而認定可比較。

#7 的歷史 MAX 沒有後來加入的獨立驗證證據：歷史列仍原樣顯示3232／MAX-58及待稽核標記，但最佳摘要與 Recent Update 不將它當作已驗證 MAX。#15 的自訂 MAX3232與94.89%原樣保留。

## 獨立比較與事件

- 同一 Song ID＋遊戲難度，合格 EX 嚴格提高才有 `SCORE_UPDATE`／NEW SCORE；首次建立有 `first_best=true`。
- 燈號依 ACP > AC > CLEAR > FAILED > UNPLAYED，嚴格升級才有 `LAMP_UPDATE`／NEW LAMP。
- 分數與燈號分別查詢來源 Record ID；同燈號的首次達成按實際遊玩時間，再用 Record ID穩定決定。
- 提交交易內保存 `achievement_feedback.score_update`、`lamp_update` 與各自前後值，延用 evidence_json。沒有刷新時為 SAVED。
- 來源仍須通過正式提交、身份、本人成績列、有效整數判定、EX公式及比較狀態檢查。識別不明、OCR不可靠或跨場次衝突不放行最佳刷新。

EX 相容策略是 `confirmed-perfect2-cool1-v1`。只接受目前已知的 iidx-v1、iidx-v2-independent-max、custom-judged-v1，且逐筆核對正式證據及公式。未知版本不因 MAX 相同就加入。早期沒有正式審核資訊的匯入資料只作「未分類歷史組」保留，不自動當作可信跨制依據。

不同正常完成場次的 judged_total，或同一 MAX 定義內互相矛盾的完整基礎，仍會暫停比較。不同 MAX 定義本身不當作同一計數器；EX 可比較不代表 MAX差值或百分比可以跨制排名。既有成績不重算。

## FAILED

可信 FAILED 可刷新 EX，例如 CLEAR1800 → FAILED1900：分數更新為1900，最佳燈號保留CLEAR，只發 NEW SCORE。首次可信FAILED可建立首次EX與FAILED燈號。

FAILED 的部分 P+C+B+M不會與正常完成整首合計比較，也不會變成完整MAX；缺少另行驗證的完整基礎時，MAX、差值、百分比與完整音符數仍為 NULL。EX 可比較與完整 MAX 是否已驗證是兩件事。

## Recent Update、GUI與動畫

Recent Update只取不可變正式提交中有證據的分數刷新，最多30個Song ID＋難度組合。只有燈號升級不改排序；FAILED刷新以紅色FAILED標示。仍核對舊best→新best，不能由匯入、重整或回推產生新事件。

歌曲列表／詳情與歷史頁共用最佳資料。歷史頁新增最高EX取得日期、Record ID、當次燈號及可靠MAX，以及最佳燈號首次達成日期、Record ID、其當次EX。歷史列表按實際遊玩時間排序，與最佳分數獨立。

Phase5B的NEW SCORE／NEW LAMP依各自事件播放。FAILED可有NEW SCORE提示，但FAILED燈號僅簡單紅色效果，不播放燈號升級光環。沿用Session／Review唯一限制、交易、備份與localStorage通知去重。重送及重整不重播成就。

## 安全 migration

Schema9→10：替換 `difficulty_bests` view與兩個正式資料驗證trigger；沒有新增資料欄位或事件表。

1. 取得寫入交易鎖並檢查完整性。
2. 建立並驗證 pre-v10-independent-bests SQLite備份。
3. 修改view／trigger，逐表雜湊核對原資料與ID全部不變。
4. 通過後才更新schema_migrations及user_version；任何失敗回滾。

正式遷移保存 **16筆正式成績**，所有歌曲／來源／難度ID、原MAX、燈號、evidence_json及監控檔案保留；integrity_check與foreign_key_check通過。備份位置與逐表統計見 `reports/phase5d2/production-migration.json`。原始快照15筆與遷移前16筆的差異來自使用者在開發期間的正常新增；本次沒有將任何生成測試成績寫入正式庫。

舊事件不改寫、不補發，不為新規則製造過去的Recent Update。只有衍生最佳查詢結果更正，遷移報告列出變動。

## 驗證與操作

完整測試執行：

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
.venv-ocr/Scripts/python.exe -m unittest discover -s tests -v
```

379項測試包含18項新增獨立比較／實際紀錄快照／migration回滾測試。既有測試依本次明確的新規則更新FAILED及Schema預期；未移除安全檢查。

隔離GUI使用正式庫唯讀快照，生成資料只存在 `reports/phase5d2/gui/ISOLATED.sqlite3`：

- 真實#7/#15查詢：3174保持最高，原歷史不變。
- FAILED由1900到1920，只NEW SCORE，最佳燈號仍CLEAR，MAX未知。
- 較低EX1900取得AC，只FIRST AC／NEW LAMP；最高分1920及Recent的#18保持。
- 最終分數來源#18 FAILED、最佳燈號來源#19 AC；重整保持且無通知重播。
- 實際記錄CSS toast-enter、lamp-upgrade、lamp-ring、lamp-shine事件。

截圖與動畫追蹤在 `reports/phase5d2/gui/`。這些是**實際瀏覽器操作隔離資料的驗證**，不是新的一次真實GAME OVER或多人遊玩驗收。

開啟 `LibraryGUI.cmd` 或 `MonitorGUI.cmd`，進入歌曲詳情／歷史成績即可查看。已開啟的舊頁面請重新載入。既有VERIFIED_AUTO設定保留，監控原本停止就維持停止，不自動提交舊待確認資料。

仍待真實遊玩驗證：多人本人FAILED的連續端到端流程、不同歌曲下載後自動匹配及長時間監控。未知計分版本與基準衝突仍需人工調查，不自動解決。

## 修改檔案

- 核心：`bests_policy.py`（新增）、`achievements.py`、`score_comparison.py`、`submissions.py`、`recent_updates.py`、`result_review.py`。
- Migration：`migrations_v10.py`（新增）、`database.py`、`migrations.py`、`__init__.py`。
- GUI：`gui_assets/lamps.js`、`history.html`、`history.js`、`history.css`、`style.css`。
- 測試：新增`tests/test_phase5d2.py`；調整既有achievement／submission／Phase5C2／Phase5D測試及最新Schema／備份數預期。
- 工具與文件：`tools/phase5d2_gui.py`、`tools/phase5d2_validate.py`、本說明、README、VALIDATION與隔離報告。
