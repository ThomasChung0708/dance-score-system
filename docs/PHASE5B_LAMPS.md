# Phase 5B · 燈號動畫與成績刷新提示

版本 **0.10.0**，SQLite **schema 6 不變**。沿用既有監控、OCR、人工確認、備份及正式提交機制。沒有新增動畫框架、外部字型、圖片或音效；燈號與動畫使用自行設計的 CSS 幾何圖形。

## 啟動與操作

雙擊專案根目錄 `LibraryGUI.cmd`；需要 OCR／監控時使用 `MonitorGUI.cmd`。或執行：

```powershell
python -m dance_results.gui
```

目前已另開新版服務：**http://127.0.0.1:40474/**。原本 40470 的監控服務保持運作，沒有強制重啟或停止；新版也沒有自行開始監控。舊服務的 Python 模組不會熱更新，之後不在遊玩時可正常關閉舊服務，再以啟動檔開啟新版。

1. 歌曲列表每首顯示 EASY／NORMAL／HARD 的正式最佳燈號、BEST EX 與該最佳分數紀錄的 MAX 差值／百分比。
2. 歌曲詳情的「正式成績 / CLEAR LAMPS」另外列出歌曲 LV、已驗證音符數、最佳分數與最佳燈號各自的 Record ID、最近一次紀錄及上次提交事件。未建立的難度只顯示灰色 UNPLAYED 與未設定，**不建立資料庫難度或假數值**。
3. Pending Review 的確認條件與摘要核對步驟不變。正式提交成功後才出現通知與持久結果摘要；禁止提交、草稿與單純 OCR 均不播放成功提示。
4. 頁首「動畫：標準／減少」切換手動偏好，保存於目前網站的瀏覽器設定。同時遵守系統 `prefers-reduced-motion`；系統要求減少時，手動標準不能覆蓋它。
5. 同一網站的歌曲庫分頁在正式提交後更新列表與詳情，保留捲動及尚未儲存的難度編輯。重新聚焦時亦會適度刷新；不逐幀輪詢。

## 顯示與動畫

| 燈號 | 顏色 | 行為 |
|---|---|---|
| ACP | 金黃 | 最強的短暫升級亮光 |
| AC | 冷藍 | 短暫升級亮光 |
| CLEAR | 綠 | 首次點亮／升級 |
| FAILED | 紅 | 簡單閃爍，無升級環或掃光 |
| UNPLAYED | 灰 | 文字與空心幾何標記 |

一般進場 360ms；升級約 600ms，搭配約 520ms 文字掃光及短暫外環。已點亮的 CLEAR／AC／ACP 只有微弱呼吸光，僅在可見範圍運作。Reduced motion 改為 120ms 淡入，停用放大、呼吸、外環及掃光。

右上角只保留一個合併通知，約 2.6 秒後淡出。主提示依成就優先顯示 FIRST ACP、FIRST AC、FIRST CLEAR、NEW LAMP、NEW SCORE、FIRST RECORD 或 SAVED；副標籤可同時顯示 NEW SCORE 與 NEW LAMP，並列出 EX、MAX／百分比、BEST EX 增量及舊→新燈號。FAILED 首次可顯示 FIRST RECORD，但不顯示 NEW SCORE／NEW LAMP。

最近十分鐘內的提交顯示 NEW（刷新分數或燈號）或 RECENT（只有新增歷史）；不是永久未讀標記。詳情保留上次事件文字。首次分數因無舊值不顯示虛構的「+增量」。歷史最佳分數與最佳燈號始終獨立。

## 儲存與安全

沒有 Schema、索引或計分規則變更。`achievements.py` 僅讀取既有正式且驗證通過的歷史：最高 EX 只取正常完成，最佳燈號沿用既有 `difficulty_bests`。FAILED 不能降低燈號或刷新正常完成最高分。

在原有 `BEGIN IMMEDIATE` 提交交易內，讀取舊最佳、產生事件，與新成績一起存入既有 `play_records.evidence_json.achievement_feedback`。沒有新增冗餘最高分欄位，也沒有改寫舊成績。併發提交依交易順序比較；相同 Review／Session 重送回原 Record 與原事件，不重新計算成就。

前端另以資料庫／Session／Review／Record 身份防止同一通知重播。重新整理只讀持久摘要，不重新宣布成功；新舊服務或分頁各有生命週期，不依分數、歌名或截圖雜湊判重。舊成績缺少事件時只顯示既有分數、燈號及「舊紀錄未保存刷新事件」，不追溯捏造成就。

Heart of Witch 的 1194／1192 差異仍阻止確認與正式提交；本階段沒有增加任何差異特例。

## 測試結果與範圍

**244 項自動化測試全部通過**：原有 226＋新增 18，完整執行約 40.17 秒。包含交易、回滾、重送、併發刷新、同名不同 Song ID、不同難度、FAILED、不完整音符及 HTTP 介面。四份 JavaScript 均通過語法檢查。

實際操作本機 Web GUI，在完整 SQLite **隔離副本**建立八筆清楚標示的合成測試紀錄，驗證：

- UNPLAYED→CLEAR、CLEAR→AC、AC→ACP 的實際 CSS 動畫事件。
- 較低 EX 的 AC 升級燈號，保留較高 CLEAR 分數的另一筆 Record。
- ACP 同時 NEW SCORE／NEW LAMP，BEST EX +276；同分再提交只有 SAVED。
- FAILED 的實際動畫只有紅色閃爍，不出現升級環或掃光；HARD 仍為最佳 ACP／EX1676。
- 手動減少動畫時，瀏覽器實際只發出 `fx-fade`，外環與呼吸均停用。
- 跨分頁更新保留未儲存的 LV 編輯；列表與詳情一致；重整保留成績，不重播升級。
- Heart 真實保存證據在副本顯示判定差 -2，確認及提交停用，沒有成功通知。
- 新版正式 GUI 顯示 60 首／180 個 UNPLAYED 燈號，沒有瀏覽器錯誤。

正式庫逐表內容及 Schema SQL 與開工基線完全相同：41 Pack／4415 Song／2966 檔案來源／2538 官方來源／7612 難度／PlayRecord 0。既有 Session／Review／截圖 SHA-256 不變；正式及副本的完整性與外鍵檢查通過。沒有自動提交任何真實待確認紀錄。

以上是**自動化測試與實際 GUI 操作**，不是新一次真實遊玩端到端。沒有重新實測真實多人、GAME OVER、遊戲視窗生命週期或 OS 級減少動畫設定；沿用既有安全門檻。CPU／記憶體／FPS 未實測。完整資料庫副本上五次 60 首查詢的中位數約 10ms，僅是本機服務查詢耗時，不代表瀏覽器渲染或遊玩效能。

證據：`reports/phase5b/validation.json`、`gui-checks.json`、`unittest.log` 與截圖；可執行 `python -m tools.validate_phase5b` 重新進行唯讀核對（若使用者之後正式提交或遊玩產生新資料，基線差異需個別調查，不要還原）。

## 修改檔案

- `dance_results/achievements.py`：正式歷史摘要與交易內成就比較。
- `dance_results/submissions.py`：保存及返回成就事件，沿用備份、交易、回滾與冪等機制。
- `dance_results/library_service.py`：當前頁面與詳情的正式成績摘要。
- `dance_results/gui.py`：提供共用動畫資源。
- `dance_results/gui_assets/lamps.js`、`lamps.css`：原創燈號、動畫、通知、偏好與防重播。
- `dance_results/gui_assets/index.html`、`reviews.html`、`history.html`：共用資源與正式摘要位置。
- `dance_results/gui_assets/app.js`、`reviews.js`、`history.js`：列表／詳情／提交回饋／歷史整合。
- `dance_results/__init__.py`、`README.md`、`docs/VALIDATION.md`、本說明：版本及交付紀錄。
- `tests/test_achievements.py`：18 項新增回歸測試。
- `tools/prepare_phase5b_gui.py`、`append_phase5b_motion_fixtures.py`、`validate_phase5b.py`：隔離 GUI 資料與唯讀驗證。

## 下一版可加強

先讓使用者正常遊玩後親自核對並提交一筆正式紀錄，檢查持續監控與成就提示的完整串接。其次可增加通知音量／效果強度偏好、永久未讀標記、手機窄畫面排版及專用瀏覽器動畫回歸工具。這些都不需要變更歌曲身份或放寬音符差異門檻。
