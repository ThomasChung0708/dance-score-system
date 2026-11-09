# Phase 5F：真實遊玩前的隔離驗證

目前仍為 **v0.15.0 / SQLite Schema 10**。本輪沒有修改產品核心、判定門檻、Schema、正式成績或官方 CSV。

**已完成可自動執行的回歸、原圖 OCR、隔離資料庫回放與真實 GUI 驗證；新的使用者實際遊玩端到端驗收尚待進行。** 使用者選擇先完成回放，稍後實玩。

後續追加真實遊玩：官方K〈新世界Remix〉HARD／LV10已完成選歌→PLAYING→RESULT→結算保存→Pending防護。原圖Miss=1沒有可靠讀到，選歌BPM與結算LV信心也不足，安全阻擋自動提交；正式庫與隔離庫均未新增成績。**完整自動儲存仍未驗收通過。** 見[本場實玩報告](../reports/phase5f/live-gameplay-20261027-01/report.md)與[證據](../reports/phase5f/live-gameplay-20261027-01/verification.json)。下列總報告保留先前回放階段的時間點，不將此次實玩混算成原有399項自動化測試。

完整結果與證據：

- [驗證報告](../reports/phase5f/report.md)
- [HTML 報告](../reports/phase5f/report.html)
- [JSON 結果](../reports/phase5f/validation.json)
- [完整測試日誌](../reports/phase5f/unittest-baseline.log)

## 結果摘要

- 全部既有 **399 項測試通過，0 略過**。沒有用重複測試灌增測試數量。
- 27 張原圖分類正確；完整結算 OCR 的 55 個預期欄位中 54 個符合標註，包括旁觀者應保留 NULL 的安全案例。一張多人 GAME OVER 的 Cool=1 信心不足，保留 NULL，不能視為完整數字辨識通過。
- 兩張原始等待室圖的新模型 OCR 均確認本人 TC 的 NO MAP。沒有可靠的本人進度條證據，不能宣稱真實下載已驗證。
- 保存過的 Custom／Official K 選歌圖均在結果前鎖定正確 Song ID；明確模擬 PLAYING 的回放因真實低信心結算欄位保留 Pending，沒有自動提交。
- 正式唯讀案例：周大俠 #7 保持 3174，#15 不刷新；Macuilxochitl #18 保持 EX2029，#19 提升 AC，只 NEW LAMP，不進 Recent。
- 隔離 GUI 的分數刷新、燈號刷新、雙刷新、FAILED、普通儲存、HTTP 重送、頁面重整與整個服務重啟均通過。新測試紀錄明確標記為生成資料，只存在報告目錄的隔離庫。
- 604 幀新被動視窗擷取成功；只有選歌／等待室，沒有新的實玩完成證據。停止監控後未繼續擷取。
- 全 SONG 背景掃描約 107.5 秒，期間 OCR 持續執行；選歌圖平均辨識由 1.36 秒增至 1.48 秒。CPU、記憶體、p95 與取樣限制見完整報告。
- 正式所有資料表雜湊不變；原 22 筆 PlayRecord、Session、Song ID、MAX／燈號，以及 27 張測試原圖均保留。完整性及外鍵檢查通過。

## 仍需實玩

1. 官方 K 正常完成：先確認來源，再選歌停留 10 秒；遊玩及結算停留 10 秒。核對當次身份、判定數、分數、實際時間、正式提交及動畫。
2. 自製 Pack 切換及兩張 JOINT：確認 Pack，驗證重新開啟清單會撤銷舊確認；不以同名或排序決定身份。
3. 真實 GAME OVER：保存 FAILED，未知完整 MAX 保留 NULL，不把部分判定數當整首歌。
4. 多人本人 NO MAP → 實際進度條 → 下載到 connect／其他 Pack／新 Pack → PLAYING 背景掃描 → 本人第二／第三列結算 → 安全自動提交或 Pending。
5. 20–30 分鐘耐久及最小化、失焦、恢復、遊戲關閉重啟。未有結算只 INTERRUPTED，不捏造 FAILED。

Pack Hover 與真正選取仍需四種原圖：未選取、Hover、選取後游標移開、清單關閉。自動來源未知、Pack 未確認、低信心數字、跨場次基準衝突及 CSV 差異的保守自動部署閘門仍存在。這些限制不能用本輪模擬通過取代。

## 啟動與驗證工具

一般使用仍雙擊 `LibraryGUI.cmd` 或 `MonitorGUI.cmd`。隔離測試介面：

```powershell
.venv-ocr/Scripts/python.exe -m tools.phase5f_gui
```

網址為 `http://127.0.0.1:40511/reviews`。這個介面是測試副本，不是正式歌曲庫。

本輪工具分工：

- `phase5f_live.py`：隔離副本、獨立 monitor、被動視窗及固定間隔資源取樣。
- `phase5f_validate.py`：正式快照唯讀最佳／Recent 核對、實際模型 OCR、全 SONG 與 OCR 並行效能、重複結果模擬；`--lobby` 額外驗證等待室原圖。
- `phase5f_replay.py`：保存過的先行選歌確認與結算原圖，新 OCR 值不替換；明確模擬 PLAYING／時鐘。
- `phase5f_gui.py`：明確標記的生成成績及隔離 GUI。
- `phase5f_gui_audit.py`：完全相同 HTTP 重送、獨立更新與原資料保存檢查。
- `phase5f_report.py`：正式庫／原圖最終唯讀稽核與報告。

準備與掃描工具拒絕覆寫既有隔離實驗；需要新一輪時保留原證據、另選輸出目錄，不將舊實驗覆蓋回正式庫。一般自動化測試可直接重跑：

```powershell
$env:DANCE_SAMPLE_ROOT='D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG'
.venv-ocr/Scripts/python.exe -m unittest discover -s tests -v
```
