# 歷史驗證：Phase 2.5（0.3.5）

日期：2026-10-22，Asia/Tokyo；Windows、Python 3.11.5，未安裝額外 Python 套件。資料庫 schema 2，報告 JSON schema 4。

## 原始狀態稽核

修改前已讀取原始碼與實際 `data/library.sqlite3`。schema 1 的 charts 已只允許 EASY／NORMAL／HARD，且實際為零筆，未發現原始譜面等級污染遊戲 LV。匯入器也已是歌名用途，沒有從原始檔解析難度或音符。

既有資料：根目錄 1、Pack 39、Song 1859、歌名來源 2930、掃描快照 2。quick_check=ok、foreign_key_check 無違反。

## 修改的檔案

| 檔案 | 修改 |
|---|---|
| dance_results/migrations.py（新增） | schema 1→2、SQLite 備份驗證、ID／歷史稽核、交易回滾；GameDifficulty、PlayRecord、Session、別名、原始 bytes、最佳查詢 |
| dance_results/database.py | 開庫 migration、保留 Song／Pack ID、格式狀態、原始 bytes 保存、遊戲數值快取讀取 |
| dance_results/models.py | 格式狀態、編碼候選／原始 bytes 傳遞、JSON schema 4 |
| dance_results/songs.py | 一資料夾一歌；混合格式保留並提示，不選優先 |
| dance_results/parsers/base.py | 舊編碼候選、原始 bytes 保留；仍只解析歌名 |
| dance_results/report.py | 既有報告統計改讀 game_difficulties；沒有新增 GUI |
| dance_results/__main__.py | 自動 migration 時輸出備份路徑 |
| dance_results/__init__.py | 版本 0.3.5 |
| tests/test_phase25.py（新增） | 20 項 migration、Unicode、部分難度、重掃及多人資料邊界測試 |
| tests/test_database.py、tests/test_song_titles.py | 新表名、混合格式待確認、JSON schema 更新 |
| README.md、docs/DATABASE.md、docs/REQUIREMENTS.md | 最新模型、使用方式與多人規則 |
| docs/VALIDATION.md、docs/VALIDATION_0.3.md | 本次驗證及保留前版驗證紀錄 |

## 自動測試

```powershell
$env:PYTHONUTF8 = '1'
$env:DANCE_SAMPLE_ROOT = 'D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
python -m unittest discover -s tests -v
python -m compileall -q dance_results tests
```

**57 項全部通過，無跳過**，含實際提供的 SM／DWI／osu 樣本，樣本全部檔案前後 SHA-256 不變。compileall 通過。

測試涵蓋：

- schema 1 非空 fixture 的 root／Pack／Song／Source／Difficulty ID、數值及歷史外鍵保留；備份仍是 schema 1；再次開庫不重複遷移。
- migration 中途 DDL 失敗時完整回滾；既有外鍵有錯時先停止；唯讀模式不偷偷升級舊庫。
- 同名同 Pack 不同 SongFolder，以及不同 Pack／根目錄的同名歌保持不同 ID；重掃不重複。
- 同格式多來源仍一首歌；混合格式保留所有來源且不選優先。
- 原始 Challenge／9999／osu Version 等不建立遊戲難度或 LV；可只有一、兩種難度；未觀測值 NULL。
- 已有畫面 LV／總數／歷史不受原始來源變動或重掃影響。
- 繁中、日文、英文、混合語言、特殊符號、組合字元、UTF-8 BOM、CP932、Big5 保留；未確認編碼不猜字，原始 BLOB 與 SHA-256 一致且重掃不重複。
- 遊戲別名不覆蓋原始 Title；中文可以直接查詢。
- 各難度分開取最佳；完成 PB 與最佳燈號分開；不同暱稱共用最佳；未配對／未驗證不更新最佳。
- 非 HOST 本人難度／LV 與 HOST 結算 LV 分欄，重掃不根據這些 Session 自動配對。
- 本人倒下等待他人時不得提前寫最終結果；同 Session 唯一結果；退出／斷線／中斷不冒充 FAILED。
- 失敗判定合計不能當完整音符數；完成合計保存來源；不合法 EX／MAX／百分比／燈號被拒絕。

既有 HTML 的實際 JavaScript 使用 Node 的 DOM fixture 檢查初始計數、搜尋、組合篩選及無結果狀態。這是腳本測試，並非瀏覽器視覺驗證。

## 實際 migration 與二次完整重掃

先用現有資料庫的 SQLite 備份副本完整遷移，確認既有識別／歌名來源／歷史快照雜湊一致後，再處理實際資料庫。實際 migration 在交易中完成，沒有重建專案或清空任何資料表。

- [遷移前 SQLite 備份](../data/backups/library.pre-v2.20261022T014744625456Z.cf0d4dd1.sqlite3)
- [遷移稽核 JSON](../data/phase25-migration-result.json)：before／after 雜湊完全相同。
- [重掃稽核 JSON](../data/phase25-rescan-result.json)：全部原始 ID 與兩次舊掃描完整內容一致。

| 項目 | 遷移前 | 遷移後及兩次重掃 |
|---|---:|---:|
| Pack | 39 | 39，全部 ID 不變 |
| Song | 1859 | 1859，全部 ID 不變 |
| 歌名來源 | 2930 | 2930，全部 ID 不變 |
| 原始掃描快照 | 2 | 2 筆原文保留，另新增 2 次驗證重掃，共 4 |
| GameDifficulty | 0 | 0，沒有補造三難度 |
| PlayRecord | 原無此表 | 0，沒有測試資料混入正式庫 |
| 舊編碼原始 bytes 快照 | 原無此表 | 43 筆，478766 bytes，雜湊全部一致 |
| 第二次重掃新增歌曲／來源 | — | 0／0 |
| 第二次重掃未變歌曲／來源更新 | — | 1859／0 |
| quick_check | ok | ok |
| foreign_key_check | 無違反 | 無違反 |

第一次重掃因新增格式／編碼欄位，1859 首與 2930 個來源的 metadata payload 更新；第二次完全穩定。沒有檔案難度、LV 或音符數流入 GameDifficulty。

- [第一次重掃報告](../reports/20261022-104834-642704/report.html)
- [第二次重掃報告](../reports/20261022-105019-010579/report.html)

## 仍待確認與本期邊界

- **851 個 SongFolder 實際同時有多種格式**，與最新單一格式規格不符。尚待使用者確認哪些是殘留檔；目前一首仍一個 ID，source_format=NULL、format_status=mixed，不刪除或選擇檔案。
- **43 份來源的舊編碼未確認**；已保存原始 bytes，等待指定 CP932／Big5 等後重掃。全庫原有 101 個來源歌名衝突也維持待確認。
- 沒有遊戲畫面觀測，因此真實 GameDifficulty／LV／總數依舊未知。本期沒有 GUI、OCR、人工配對介面或自動成績／燈號功能。
- 異常觀測衝突、譜面實質改版後歷史是否延續、資料夾搬移／改名的人工掛接與顯示捨入規則留待後續確認。
- 未實際點擊 Windows 啟動檔／資料夾選擇器。先前內建瀏覽器不支援 file 協定，未繞過限制；不宣稱完成 HTML 視覺驗證。沒有執行線上遊玩或 OCR 準確率測試。
