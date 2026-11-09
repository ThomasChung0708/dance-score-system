# Phase 5C.1：結算判定優先與獨立 MAX 驗證

版本 **0.11.1**。保持現有 OCR、監控、人工確認、Phase 5B 動畫及正式提交服務。

## 成績與三種音符數

P/C/B/M、Combo、遊戲原分、評級、完成狀態來自當次結算 OCR，可人工修正；原始 OCR 與原圖另存保留。EX 永遠以已確認的 `P × 2 + C` 計算，不以 CSV 補判定或強制湊齊總數。

| 數值 | 保存位置 | 用途 |
| --- | --- | --- |
| catalog_total_notes | GameDifficulty 既有 total_notes；Review 與正式 evidence_json.note_sources 的快照 | 官方 CSV／已核對譜面參考數，身分及來源核對 |
| judged_total | 原始 OCR 和修正值分開保存；note_sources.judged_total；正式 P/C/B/M | 當次判定合計，不自動當成完整 MAX |
| max_basis_notes | note_sources.max_basis_notes、max_basis_verification；新正式 Record.total_notes | 僅在有獨立遊戲 MAX 定義證據後保存；未知為 NULL |

選歌觀測 `selected_total_notes`、資料庫快照 `database_total_notes`、原始 `ocr_judged_total` 也繼續保存。既有 `verified_total_notes` 明確限定為譜面參考數驗證，不代表遊戲 MAX。原有 GameDifficulty 不改名或重填；新的正式 Record.total_notes 只快照已驗證 MAX 基礎，不回寫為 catalog。舊紀錄的欄位含義與數值保持原樣，以 scoring_version 區分。

## MAX 與燈號規則

- 無獨立依據時，正式 MAX、差值、百分比全部為 NULL；CSV 等於判定合計也不足以驗證 MAX。
- 正常結算顯示兩種「未驗證」條件式預覽：catalog 參考數、judged_total。它們不進入正式 MAX 或刷新提示。
- 有獨立證據時，在 MAX 區選來源、填基礎音符數與證據，明確勾選另行驗證。證據綁定 Session、Review、截圖、Song、難度、判定與本人列；修改會失效，不能挪用到另一首或另一局。這是人工證據聲明，程式不能自行證明說明內容的真實性。
- GAME OVER 的部分 judged_total 不可用來建立完整 MAX，連條件式完整 MAX 預覽都不提供；可信獨立完整基礎仍可使用。
- 正常完成且有 Bad 或 Miss：CLEAR。沒有 Bad／Miss 而完整判定範圍未確認：PENDING。只有獨立 MAX 證據、範圍一致且無未解釋來源差異，才按既有規則給 AC／ACP。FAILED 規則不變。
- 已確認的正常完成 EX 可參與最高 EX，PENDING 不提升最佳燈號；分數與燈號仍分開。成功提交後才發出 NEW SCORE／NEW LAMP，保留原有冪等與動畫偏好。

## 人工操作

1. 執行 `LibraryGUI.cmd` 或 `MonitorGUI.cmd`，進入「待確認成績」。已有舊服務須在適當時機重啟新版，避免中斷正在遊玩的監控。
2. 核對原圖的本人成績列、Song ID／Pack／SongFolder、EASY／NORMAL／HARD、歌曲 LV 和判定數。官方 K 的可靠資料仍可安全帶入。
3. 查看 catalog、judged 與 max_basis 三欄。若有來源差異，保留正確判定，勾選當次四項差異核對，可附註「原因未知」。
4. 沒有另行驗證的 MAX 定義證據，保持「未知／尚未驗證」，不必為提交成績捏造 MAX。
5. 勾選整體核對，按「確認資料（另存）」；再按「確認並儲存成績」，核對摘要後正式提交。這仍是使用者親自提交，既有待確認紀錄不會自動送出。

MANUAL／VERIFIED_AUTO 偏好保持。VERIFIED_AUTO 僅對模式啟用後的新結算生效，沿用可信身分、Pack、本人列、OCR、參考數及完成狀態檢查。無衝突者可保存 EX 而 MAX 未知，燈號按上述限制；音符差異、低信心或衝突仍轉 Pending Review。不將一次人工例外推廣為通用規則，也不自動重用 MAX 證據。

## Heart of Witch 證據

官方 K Song #4343、fileId 15007、HARD LV29，資料庫參考 1194；原始結算 PNG 和重新執行 OCR 均為 P1145、C30、B7、M10，合計 1192，EX 2320。該 Session.selection=null，沒有選歌截圖，不能聲稱已驗證選歌 OCR 的 1194。

以 1194 假設 MAX2388 → MAX -68；以 1192 假設 MAX2384 → MAX -64。兩者均未驗證，實際 GUI 副本人工差異提交保存 EX2320／CLEAR／MAX NULL。沒有硬編碼歌名或差2特例。

## 舊成績與最小遷移

Schema **7→8 確有必要**：7 的正式提交 trigger 強制正常完成有 MAX、且把 Record.total_notes 與 GameDifficulty 參考數綁在一起，無法保存本次要求的正常完成 EX-only 紀錄。8 僅替換兩個正式提交驗證 trigger 與最佳成績 view；沒有新增欄位／表或重建歌曲庫。

遷移取得交易鎖、完整性／外鍵檢查、建立可核對備份，再替換資料結構；逐表雜湊及舊 bests 必須一致才 commit，任何錯誤 rollback。舊 Record、Session、證據及 MAX 完全不重算。歷史舊 MAX 標記「歷史 MAX 基礎待稽核」，仍保留原排名與燈號。

開工快照為 42 Pack、4423 Song、2992 檔案來源、2538 官方來源、7616 GameDifficulty、8 正式 Record；詳細稽核位於 `reports/phase5c1/legacy-audit.json` 與 `.md`。8 筆舊成績沒有已保存的獨立 MAX 定義證明，此狀態不等於宣告舊值錯誤。

## 尚待遊戲實證

CSV 是否計全部物件、結算是否計執行事件、特殊音符是否造成差異，都只是未證實假設。現有正式證據沒有同 Song ID＋難度的多次獨立正常完成組，不能斷言 judged_total 穩定或差異固定，也不能推論 AC／ACP 一定與參考數相同。

需要保存同一譜面多次完整遊玩的選歌及結算原圖，含 Pack／官方身份、SongFolder、難度、LV、時間、選歌音符數和本人列；涵蓋有 Bad/Miss、AC、ACP，另保存 GAME OVER 對照。若能取得修改版遊戲的物件計數及判定事件程式碼／說明，可進一步驗證。不得從原始音樂長度或未經驗證的 SM/osu 音符數反推。

## 測試與檔案

298 項自動化測試通過，包含 20 項新增 Phase 5C.1 測試；測試正式寫入只在隔離 SQLite。三張原始 PNG 重新 OCR 的12個 P/C/B/M 欄位全部符合人工已知值，保留低信心評級；這不是整個 OCR 的全域準確率。

實際瀏覽器 GUI 在隔離完整歌曲庫副本驗證：Heart 差異人工提交 EX-only、AC 候選 PENDING 不升燈、可信新結算自動提交 EX-only／SAVED、重載不重播、歷史來源顯示。沒有宣稱新的真實遊玩端到端測試。

核心修改：`scoring.py`、`max_basis.py`、`note_evidence.py`、`result_review.py`、`submissions.py`、`achievements.py`、`database.py`、`migrations.py`、`migrations_v8.py`；GUI：`reviews.html`、`reviews.js`、`app.js`、`lamps.js`、`history.js`；版本、README、驗證文件、相關既有測試及 `tests/test_phase5c1.py`。

驗證工具：`tools/audit_phase5c1.py`（唯讀）、`tools/phase5c1_real_ocr.py`（原圖重跑）、`tools/phase5c1_gui.py`（隔離 GUI 回放）、`tools/validate_phase5c1.py`（稽核）。產物為 `reports/phase5c1/validation.json`、`unittest.log`、`real-ocr.json`、`gui-checks.json` 與 GUI 截圖。請勿把測試副本當成正式資料庫。
