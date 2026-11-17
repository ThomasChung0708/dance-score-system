# 遊戲資料夾模式：直接讀遊戲的成績與歌單

不用 OCR，也不用人工確認。只要告訴工具「遊戲資料夾」（有 `dance.exe` 的那一層），再選要記錄的角色。

## 工具讀哪些檔

| 檔案 | 用途 |
|---|---|
| `dance_Data\StreamingAssets\song_table.csv` | 官方歌（mode=K）。CSV 改了就照新內容更新，不會因為跟舊資料不同而停住。 |
| `<DATA>\CACHE\external_song_cache.json` | 遊戲自己掃到的所有外部歌：`DATA\ADDON\SONG`、`config.ini` 的 `AdditionalSongFolders`（StepMania／osu 資料夾）、.gn 歌包。歌名、LV、音符數跟選歌畫面一致。 |
| `<DATA>\PROFILE\...\<角色>\results.jsonl` | 每打完一首歌，遊戲多寫一行成績。 |

`<DATA>` 預設是 `<遊戲資料夾>\DATA`；遊戲資料夾裡有 `data_root.txt` 時，用它第一行指定的資料夾（跟遊戲自己的規則相同）。

外部歌快取不是 JSON（檔名是歷史遺留），而是遊戲的二進位格式；讀取程式在 `dance_results/song_cache.py`，只接受已知的版本（目前 35）。遊戲改了格式時，工具會明確說「版本尚未支援」，不會亂讀。

網頁不再有「匯入官方 CSV」：遊戲資料夾已經跟著 song_table.csv 走（手動匯入遇到資料不同會整批停止，不適合常改的 CSV；需要時仍可用 `python -m dance_results.official_import`）。設定了遊戲資料夾後，「↻ 更新歌曲庫」也會隱藏：背景每 3 秒就會檢查歌單與成績。

歌曲清單不分位置：官方歌、`DATA\ADDON\SONG`、StepMania、osu 的歌一起列出、一起搜尋。左側「自製歌曲」依來源分組（遊戲 ADDON／StepMania3.9／osu!…），可以展開到各歌包。多人房自動下載的 `connect` 就是遊戲 ADDON 底下的一般歌包。

## 角色

下拉選單列出 `PROFILE` 裡的每個角色（名字來自各自的 `profile.json`），單機角色標「單機」、線上角色標帳號。
預設選「有成績檔、而且最近玩過」的角色。只匯入選中角色的成績：最佳成績與燈號是整個資料庫共用的，混進別的角色就不準了。
選中的角色名字也會寫進原本 OCR 流程用的「本人暱稱」。

## results.jsonl 一行的格式

```json
{"v":1,"at":"2026-11-17T12:42:16Z","player":"Eithwa","song":{"kind":"official","title":"Silly-Go-Round","artist":"Yuuka","gn":"sdom5023k.gn","fileId":15023},"difficulty":"hard","level":18,"chartJudged":747,"perfect":735,"cool":12,"bad":0,"miss":0,"maxCombo":747,"score":52703,"accuracy":100,"grade":"A++","failed":false,"online":true,"host":false,"mode":1,"players":2}
```

外部歌的 `song` 是 `{"kind":"external","title","artist","packId","folder","songKey","chart","chartIndex","format"}`。

- 中途離開、旁觀、錄像播放、譜面編輯器試打、開過自動的那一局，遊戲不寫。
- `chartJudged` 是整首打完的判定次數（長條算頭尾兩次）。工具的 MAX ＝ `chartJudged × 2`，EX ＝ Perfect×2 ＋ Cool。
- `failed: true` 是血歸零過（GAME OVER），記成 FAILED 燈號。
- 只認得 `v` = 1；遇到不認得的版本那一行會列在「未存」，不會猜。

## 工具怎麼存

- 每 3 秒看一次檔案有沒有變長；只讀到最後一個換行為止（遊戲寫到一半的那行下次再讀）。讀檔用一般的開檔方式，不會擋到遊戲寫入。
- 每一行用自己的 SHA-256 當身分，同一行永遠只會存一次；讀取位置遺失也不會重複。
- 成績以既有的「自動驗證（verified_auto）」身分正式寫入，跟 OCR 自動儲存的紀錄走同一套資料表與觸發器，所以最佳成績、燈號、Progress History、Recent Update 都照常運作。
- 遊戲回報的 LV 或判定數跟目前歌單不同時，以遊戲為準，順便更新該難度的資料。
- 官方歌還不在歌單（CSV 還沒更新）時，那一行先放「等待中」，歌單更新後自動補存。外部歌不在快取時，照那一行的資料新建歌曲，不會丟成績。
- 一批有新成績才備份一次資料庫（`data\backups\library.pre-game-sync.*`），只保留最近 10 份。

## 執行檔

`BuildExe.cmd` 產生 `dist\DanceScore.exe`（約 10 MB，單一檔案，不需要安裝 Python）。
exe 不含 OCR 套件，所以「待確認成績」「即時監控」兩頁會自動隱藏；資料放在 exe 旁邊的 `data` 資料夾。
