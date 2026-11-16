# 官方 K 模式歌曲匯入

版本 0.5.0，SQLite schema 5。官方匯入是獨立功能，不需要 OCR、自製譜匹配、歌曲時間欄位或預先掃描自製歌曲。

## 使用 GUI

1. 雙擊 `LibraryGUI.cmd`。
2. 按「匯入官方 CSV」，選擇 UTF-8／UTF-8 BOM 的 song_table.csv（上限 4 MiB）。
3. 查看 K 筆數、排除筆數、新增歌曲／難度、既有相同歌曲，以及重複 fileId 或資料衝突。
4. 按「匯入 K 模式」。成功後自動顯示官方分類；有衝突時不會整批寫入，也不會覆寫舊值。

官方卡顯示原始 fileId、Title、Artist、BPM、gn、CSV 來源及 E／N／H 的 LV、總音符數、MAX。Pack／SongFolder 顯示不適用，來源是官方 CSV，沒有虛構磁碟目錄。官方卡目前只供來源檢視，自製歌曲仍可手動編輯。

## 使用獨立 CLI

```powershell
# 只讀預覽，舊資料庫也不會 migration
python -m dance_results.official_import --csv '.\song_table.csv'

# 備份、必要 migration，再交易匯入
python -m dance_results.official_import --csv '.\song_table.csv' --import

# 可指定另一個資料庫，完全不依賴自製歌曲庫
python -m dance_results.official_import --csv '.\song_table.csv' --database 'data/official-test.sqlite3' --import
```

`--result 路徑.json` 可保存來源 SHA-256、備份、變更數及舊資料稽核。結果路徑不能覆寫 CSV 或資料庫。匯入與結果檔輸出分開回報，避免已提交卻誤稱整批失敗。

## 篩選、身份與難度

- **只建立 mode=K**。T 與空白排除；hidden=1 的 K 仍保留，src／enc 不拿來額外篩選。原始 CSV 只讀。
- 官方身份為固定 catalog＋K＋原始 fileId＋gn，另有永久內部 Song ID。fileId 以 TEXT 原值保存，不單欄唯一。同名、同 Artist、同 BPM 都不合併。
- 本次 fileId 10026 的兩份 K 來源（sdom0026k.gn／sdom0921k.gn）分別保存。這只保留不同來源身份，不宣稱其音符配置已驗證等價或不同。
- gn 在同官方 catalog／mode 必須唯一。相同 gn、來源身份變動或既有內容不同均阻擋，不保留第一列或讓後列覆蓋。
- LV／notes 皆為正整數才建立對應 EASY／NORMAL／HARD。兩者皆 0／空白時不建立；半缺值、負值、非整數或非法 BPM 拒絕整批匯入。
- 未提供有效難度顯示「未設定」，不是 LV 0／0 音符，也不直接宣稱遊戲一定不存在。fileId 12140 只建立 EASY 26／3417，MAX 6834。
- MAX 由已確認音符數×2 即時計算，不另存易失同步的重複欄位。
- BPM 原字串與小數精度保留。原始 title／artist 不翻譯、不改語言。
- 來源標示 official_csv，不冒充 manual 或 game_screen。CSV durEasy／durNormal／durHard 尚不匯入已驗證遊戲時間。

## 安全與重複執行

schema 4→5 及首次匯入前，分別透過 SQLite backup API 建立並核對 pre-v5／pre-official-import 備份。Migration 保留既有 Song／Source／Difficulty／Record ID、外鍵、索引、trigger、view、快照及內容；檢查不過就回滾。

匯入先在 BEGIN IMMEDIATE 內檢查全部來源與衝突，才寫入批次、新官方 Song、獨立官方來源及有效難度。任一步失敗，整個歌曲匯入交易回滾。Migration 是另一筆安全交易；若它已成功、匯入後來失敗，schema 可維持 5，但舊資料不變，不留下部分官方歌曲。

相同資料重匯為 no-op，Song／來源／難度與批次內容不重建。CSV 改路徑或排序也不換身份；來源內容、身份或既有難度不同則報衝突，保留舊值，不自動覆寫手動設定。CSV 少了來源也不刪除已有歌曲或成績。

官方不插入既有 song_sources，2,930 個檔案來源 ID 保持原值。新 official_source_id 是獨立空間。GUI Sources 是「檔案來源＋官方來源」總數，API 另提供兩個分項。

## 與自製歌曲及未來 OCR 分開

自製／connect 仍以既有 root＋Pack／SongFolder 身份掃描，官方是不能交給檔案系統掃描的邏輯來源。GUI 的「更新歌曲庫」只操作實體 filesystem root，找不到官方實體資料夾不會刪除或標記官方消失。

官方匯入沒有 OCR 呼叫，也不要求 Phase 4 時間或 JOINT 匹配規格先完成。原有多人、本人／HOST 分離、未知配對及人工確認規格持續保留；本版不綁定或產生成績，也不執行 Clear Lamp 判定。

實際正式匯入、完整副本、重匯、回滾與 GUI 驗證見 [驗證結果](VALIDATION.md)。
