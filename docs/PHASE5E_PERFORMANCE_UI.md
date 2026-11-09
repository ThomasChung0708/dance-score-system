# Phase 5E：個人成績單、獨立最佳與成長履歷

**v0.15.0 / SQLite Schema10，沒有 migration。** 沿用 Python、SQLite、HTML、CSS與JavaScript；沒有新增大型框架、字體、音效或 IIDX 官方素材。

## 開發前確認

實際起點為v0.14.0／Schema10。Phase5D已實作NO MAP episode與檔案快照、PLAYING背景全SONG增量掃描、只追蹤本次新增Song ID、下載摘要、安全候選與人工確認；Recent Update只接受正式分數刷新。最高EX與最佳燈號已獨立，FAILED可刷新符合資格的EX。

真實多人NO MAP下載→匹配→自動提交完整鏈，以及Pack反白與Hover的可靠區分，仍沒有足夠端到端驗收。Phase5E不將這些待驗收項目宣稱為完成，也不修改OCR、下載追蹤、計分規則或正式提交交易。

## 成績單

歷史頁重新排列為遊玩日期／Record、歌曲／難度、CLEAR STATUS、SCORE STATUS、P/C/B/M、本次更新與詳情。

- 當次燈號與歷史最佳分開；PENDING明確顯示，不用顏色代替文字。
- 分數以EX為主；只有可靠完整MAX才顯示分母、差值或百分比。FAILED未知完整基礎不產生假MAX。
- 原始歷史MAX與百分比仍保留，可在展開詳情查閱；未驗證的原值不當作主列表的可靠MAX。
- 長路徑、本人暱稱與來源身份收進「來源資訊」；Song ID、Pack、SongFolder仍完整保留。
- 手機寬度改為分組卡片，不要求橫向捲動大型表格。390px成績單與單曲頁皆已實測沒有水平溢出。

## 更新摘要的來源與語意

重用不可變的 `play_records.evidence_json.achievement_feedback`，包括Phase5D的 `score_update`、`lamp_update`、前後值與舊版已保存的更新旗標。新增API欄位 `update_summary` 只是唯讀呈現，不另建事件表、不回填舊紀錄。

| 保存的合格證據 | 顯示 |
|---|---|
| 只有EX嚴格提高 | NEW SCORE；舊EX→新EX、+EX |
| 只有燈號嚴格升級 | NEW LAMP；舊燈號→新燈號 |
| 兩者都有 | DOUBLE UPDATE，同時顯示兩組變化 |
| 兩者都沒有 | 低調的NO UPDATE |
| 首次有效分數／燈號 | FIRST BEST／FIRST LAMP，可同時存在 |
| 沒有保存更新證據 | 更新證據未保存，不倒推 |

舊分數未知時不假設0；首次有效0分與NULL仍可區分。未知計分制度、身份／比較資格不足或當次比較暫停時，不從缺失資料製造更新。

更新摘要核對當時已保存的比較結果，**不是依今天的最佳值重建歷史**。時間線按實際played_at排列；人工延後提交時，標記依然代表當時提交的更新判斷，完整confirmed_at可在詳情追溯。

## 兩個獨立最佳區塊

歌曲詳情頂部提供難度選擇，分開顯示：

1. My Best Score：EX、可靠MAX顯示、取得日期、Record ID及當次燈號。
2. Best Clear Lamp：歷史最佳燈號、首次達成日期、Record ID及當次EX。

歷史頁以Song ID篩選後也顯示相同兩個區塊。兩種來源不互相覆蓋。

真實唯讀案例：**Macuilxochitl jubeat Ripples O.S.T. / Song127 / HARD / LV22**。

| Record | EX | 可靠自訂MAX | 當次燈號 | 當次更新 |
|---|---:|---:|---|---|
| #18 | 2029 | 2036／MAX-7 | CLEAR | FIRST BEST＋FIRST LAMP |
| #19 | 1999 | 2036／MAX-37 | AC | NEW LAMP：CLEAR→AC |

正確結果為My Best2029來自#18，最佳AC來自#19。正式GUI、唯讀API與實際資料庫快照測試一致。

## Progress History / 成長履歷

單曲頁新增獨立時間線，可切換EASY／NORMAL／HARD、最新→最舊或最舊→最新，每頁20筆。每個節點顯示日期、Record、EX／可靠MAX、當次燈號與保存的更新摘要；一般遊玩以較低視覺權重顯示。

點Record可開啟當時原始結算截圖。仍經過原有監控目錄、路徑與截圖雜湊檢查，沒有為顯示圖片放寬權限。載入原圖後會定位至詳情；缺圖時保留可讀紀錄及錯誤原因。

新增受原本本機token保護的唯讀API：

`GET /api/progress-history?song_id=127&difficulty=HARD&order=asc&offset=0&limit=20`

Song ID、難度、排序與分頁均驗證；不同SongFolder的同名歌曲仍是獨立履歷。沒有正式紀錄的難度只顯示空狀態，不生成假資料。

## 動畫與去重

沿用Phase5B的燈號、掃光、光環、toast與減少動畫設定：

- NEW SCORE：EX短暫發光、增量浮現、文字標記淡入。
- NEW LAMP：顯示前後燈號，新燈號發光與掃光。
- DOUBLE UPDATE：兩種效果都有，toast中的燈號稍後開始，避免遮擋數字。
- NO UPDATE：只有SAVED通知及靜態紀錄，不播放分數刷新或燈號升級效果。
- FAILED可有NEW SCORE，但維持FAILED文字及紅色效果，沒有假CLEAR或華麗燈號升級。
- 減少動畫模式使用簡化淡入；系統prefers-reduced-motion與手動設定皆保留。

通知沿用Session／Review／Record識別與localStorage去重。冪等提交不發布新的更新事件；歌曲頁只對新提交發布的事件播放刷新動畫，初次開頁、重新整理或查看歷史只顯示靜態更新標記。

沒有新增逐幀OCR或背景輪詢。成長履歷僅在選歌、切換／分頁及正式提交後載入，最多100筆API上限；視覺效果以CSS為主。沒有額外量測CPU／記憶體，故不將流暢觀察當作資源效能數據。

## Recent Update

原有比較資格、最近30個Song ID＋難度、played_at排序及分數刷新限定都維持。只NEW LAMP不進清單；雙更新仍只出現一次，可附加NEW LAMP。下載或掃描不會建立Recent項目。

隔離GUI中，HARD雙更新#26與NORMAL首次#27分開列出；HARD單獨燈號升級#23不會取代分數更新。

## 驗證結果與資料安全

- **399項自動化測試通過，0略過**：379項既有測試＋20項新增測試。包含四種更新、首次NULL語意、同名／難度隔離、時間與時區排序、分頁、原圖API、重送、衝突、未知MAX與真實#18/#19快照。
- 隔離副本實際GUI依序完成FIRST、NEW SCORE、NEW LAMP、FAILED高EX、NO UPDATE、DOUBLE UPDATE及減少動畫NORMAL首次紀錄。
- 實際記錄score-flash／gain-enter、lamp-upgrade／ring／shine；NO UPDATE沒有刷新動畫；減少動畫追蹤只有fx-fade。
- 成長履歷排序／難度切換、原圖連結、重新載入持久化標記及不重播、桌面與390px手機版面皆已檢查。
- 正式GUI僅讀取真實資料作顯示驗證，沒有替使用者提交待確認成績。

開發前快照包含20筆正式成績。核對時正式庫為22筆：期間另有兩筆正常人工提交（Cosmic Cat、Creamy Sky）及兩次重掃。逐筆稽核確認：

- 原20筆PlayRecord與play_sessions所有欄位保持不變。
- 原Song、Pack、來源及GameDifficulty ID全部保留；原歌曲／來源僅last_seen_scan_id隨重掃更新。
- 原始PNG雜湊全部一致；正常新增／更新的Session及下載診斷檔另列於報告，不冒稱整個監控目錄完全不變。
- 測試使用的生成Session與隔離SONG根目錄沒有出現在正式庫。
- integrity_check為ok，foreign_key_check無錯誤。Schema維持10，不遷移或重算舊成績。

以上數量為稽核當時的快照；持續使用程式產生的新紀錄保留原有正常流程。詳細前後統計、異動欄位、GUI動畫追蹤及截圖見 `reports/phase5e/validation.json`、`report.html` 與 `gui/`。

## 操作

開啟 `LibraryGUI.cmd` 或 `MonitorGUI.cmd`：

1. 「歷史成績」查看新成績單，按Song ID／難度篩選可顯示獨立最佳。
2. 在歌曲庫選歌，右側頂部選擇最佳紀錄難度；下方「成長履歷」可切換難度及時間順序。
3. 點Record查看原始截圖；來源與原始MAX資訊可展開查閱。
4. Pending Review仍需依目前MANUAL／VERIFIED_AUTO與原有安全條件提交。正式成功後顯示更新前後摘要，歷史標記會保留。
5. 「動畫：標準／減少」控制呈現；舊頁面請重新載入新版資源。

自動化重跑：

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
.venv-ocr/Scripts/python.exe -m unittest discover -s tests -v
```

隔離GUI與JSON報告驗證不是新的真實遊玩端到端測試。多人新下載、GAME OVER連續監控、長時間監控與系統減少動畫偏好的實機切換仍待後續遊玩驗收；本次已實測的是手動減少動畫模式與相應CSS。

## 修改檔案

- `dance_results/performance.py`（新增）：唯讀更新摘要及可靠MAX呈現。
- `achievements.py`、`submissions.py`、`recent_updates.py`：重用保存的更新證據，加入成長查詢與呈現資料。
- `gui.py`：受原本token保護的成長履歷API；`__init__.py`：版本0.15.0。
- `gui_assets/history.html/js/css`：成績單、獨立最佳、原圖詳情與手機版面。
- `gui_assets/lamps.js/css`：共用最佳區塊、更新摘要、時間線及Phase5B動畫整合。
- `gui_assets/app.js`：單曲詳情、難度選擇、履歷、更新去重及跨歌曲庫深連結。
- `gui_assets/reviews.js`：提交後新摘要與冪等事件防護；`style.css`：手機最小寬度修正。
- `tests/test_phase5e.py`：20項新增測試。
- `tools/phase5e_gui.py`、`tools/validate_phase5e.py`：隔離資料生成與唯讀驗證報告。
- README、VALIDATION及本文件。沒有修改Migration、計分核心、遊戲本體或原始CSV。
