# 已確認需求與分期

## 已確認，後續實作不得擅自更改

- Windows 外部獨立程式；不修改、注入遊戲，不讀取遊戲記憶體，不改遊戲內計分。
- 歌曲庫 `D:\Dance v1.9.0-dev-be622\DATA\ADDON\SONG` 只讀。資料夾名稱含舊版號，但使用者確認正式版也放此位置。
- 以最新 Phase 2.5 為準：本家與自製的遊戲難度最多只有 EASY／NORMAL／HARD，每首不一定全部存在；不可自動補三張譜面。未觀測前沒有難度列不表示不存在。
- SM／DWI／osu 保存 Title／Subtitle／Artist、原始路徑與編碼；DWI 可另讀取基本 BPM 原值，不解析原始難度、模式、音符或 BPM 變化，不做譜面內容去重。
- SONG／Pack／SongFolder；每個 SongFolder 是獨立歌曲，不同資料夾同名不合併。最終確認 850 個 SM＋DWI 共存可保留為一般來源資訊，不因此拆歌或重構格式；特殊 SM＋DWI＋osu 保留並標記人工檢查。
- 遊戲 LV 使用遊戲顯示數字，不能拿 SM／DWI meter 或 osu 難度參數代替；朋友另有難度公式，不要求取得公式。
- TotalNotes 優先使用經確認的遊戲顯示值；非 HOST 看不到顆數時，允許在本人正常完成後，以 P+C+B+M 求得，記錄來源。
- 本人 FAILED、主動退出或中斷時，判定合計不可當完整 TotalNotes。未知或為 0 時不計不可靠百分比。與既有總數不一致時保留異常，不覆寫。
- Perfect=2、Cool=1、Bad=0、Miss=0；EX=P*2+C；MAX=N*2；差值=MAX-EX；百分比=EX/MAX*100。
- 差值 0–150 顯示 `MAX -差值`，>=151 顯示兩位小數百分比。負差值視為異常。
- 正常完成且 P>0、C=B=M=0 為 ACP；正常完成且 P+C>0、B=M=0 且未 ACP 為 AC；其餘正常完成為 CLEAR。
- GAME OVER 為 FAILED，無有效紀錄為 UNPLAYED；最佳燈號 ACP>AC>CLEAR>FAILED>UNPLAYED，只能提升。
- 失敗成績不能覆蓋正常完成個人最佳；最高分與最佳燈號分別管理。
- 主要一人使用，目前暱稱 TC，之後可用其他帳號；不同帳號合併同一份個人最佳及燈號，保留當次帳號資訊。
- BPM／流速不拆分成績比較。是否為同一譜面不能僅憑此宣告等價。

## 多人流程

- 計分程式採人工切換：個人／多人；多人再選自己是否為 HOST。
- 自己為 HOST，可從選歌操作追蹤本家／自製與 Pack；非 HOST 先歸未知，之後人工分類及確認歌曲、Pack、譜面。
- 未配對紀錄需可保存原始成績與截圖，尚不能更新任意譜面的最佳成績及燈號。
- 等待室頂部最多六個玩家卡片，其中一個顯示 HOST。標記可能換人，不固定第一格。
- 自由模式可各自選難度；也有 HOST 統一難度的模式。
- 已由使用者確認：等待室右側 EASY／NORMAL／HARD 切換後，數值難度也隨之變動，可作本人 LV 來源。
- 多人結算底部 LV 是 HOST 資訊，不能替代本人 LV；人物旁 LV 不是歌曲 LV。
- 結算先在表格暱稱欄找本人列；順位不固定，也不能抓背景人物暱稱。
- 本人倒下但有人存活，等歌曲結束；只有全員倒下才提早結束。同一 Session 不提早寫一次、結算再寫一次。
- 本人是否 FAILED 依自己的 GAME OVER 證據，不能用整場有無正常結算判斷。
- 主動退出回等待室；斷線有連線錯誤／中斷彈窗；當機不冒充 GAME OVER。不生成虛構成績。
- connect 是多人下載管理 Pack，pack_type=multiplayer_download，透過 pack_id 關聯歌曲；管理 Pack 不冒充已驗證原始 Pack，尾碼不作已驗證 Chart ID。非 HOST 成績仍須確認歌曲／本人難度，不只依同名或儲存位置配對。

## 本次授權範圍

Phase 1（使用者釐清後）：唯讀掃描、從 SM／DWI／osu 讀歌名、每個歌曲資料夾一列、歌名衝突提示、HTML／JSON 報告、Windows 啟動入口。不再解析或比較音符內容。EASY／NORMAL／HARD 為唯一遊戲難度分類，數值 LV 依遊戲畫面；畫面未取得前不填值。尚不實作 OCR、SQLite、正式歌曲管理 GUI、成績輸入／紀錄或燈號。

後續依原階段：Phase 2 歌曲庫資料庫 → Phase 3 桌面 GUI → Phase 4 畫面辨識 → Phase 5 成績 → Phase 6 燈號 → Phase 7 最佳化。每階段先交付測試結果，不擅自一次完成。

Phase 2 已完成 SQLite 歌曲庫。Phase 2.5 授權內容為：安全 migration，Pack → Song → GameDifficulty → PlayRecord 關聯，保留 ID／快照；來源檔只建歌名庫，不建立遊戲難度。每難度獨立保存 LV／完整音符數／紀錄，最高 EX 與最佳燈號以獨立查詢取得；尚無畫面證據保持 NULL。支援 Unicode、原始編碼待確認與 bytes 保存，保留原始 Title 與遊戲顯示名稱對應。加入多人未知配對、HOST／本人 LV、等待結算／退出／斷線的資料結構及限制；不提前開始 GUI、OCR 或成績擷取功能。

Phase 2.5 最後修正：補上 connect Pack；舊 39 Pack／1859 Song／2930 Source ID 保留。保存來源 Title／Subtitle／Artist／路徑／編碼；可靠名稱與非名稱欄位解碼錯誤分開，多解及疑似亂碼保留待確認。已完成版本 0.3.6／schema 3。

## 尚待決策／實測

- 譜面內容更新後，最佳成績／燈號是否跨版本延續。
- 完成率分母、失敗／退出／未配對紀錄是否計入各統計。
- 判定總數異常時是否暫緩燈號／最佳更新。
- 兩位小數的精確捨入規則（原例 96.225→96.23）。
- 非 HOST 的來源／譜面人工補認操作。
- 真實選歌總音符數、畫面歌名與歌曲庫的配對、擷取相容性及顯示縮放。不再需要檔案原始難度映射或朋友的 LV 公式。

## 格式參考

以格式行為為參考，並未將 StepMania／osu 的遊戲規則當作熱舞規則：

- [StepMania SM loader](https://github.com/stepmania/stepmania/blob/5_1-new/src/NotesLoaderSM.cpp)
- [StepMania DWI loader](https://github.com/stepmania/stepmania/blob/5_1-new/src/NotesLoaderDWI.cpp)
- [osu 官方格式文件](https://osu.ppy.sh/wiki/en/Client/File_formats/osu_%28file_format%29)

## Phase 3：歌曲資料庫 GUI

已授權並實作版本 0.4.0：沿用 HTML／JS、Python 與 SQLite，以本機 HTTP 服務提供三欄歌曲庫。Pack 分類與歌曲數、connect 獨立顯示；官方資料尚無則保持空白，不猜分類。歌曲支援多語搜尋、排序與分頁，同名依 Song ID／Pack／SongFolder 保持獨立。

右側顯示各來源 Title／Subtitle／Artist／編碼／原始路徑與待確認資訊。使用者可確認 EASY／NORMAL／HARD 存在，手動儲存及修改 LV／總音符數；空白為 NULL。schema 3→4 僅擴充 manual 來源，先驗證備份、前後逐表稽核；不重建歌曲身分或清空資料。GUI 增量掃描保留既有難度與歷史。

成績、AC／ACP、OCR 與非 HOST 配對只預留擴充位置；多人 HOST／本人難度及 LV 分離、未知配對與等待結算規則繼續保留，不在本期加入新判定。

補充確認（0.4.1）：SM 與 DWI 一起存在為正常情況，osu 來源一般不與 DWI 共存。既有特殊混合資料夾繼續保留人工檢查，不自動拆分或刪除。DWI 只讀名稱／作者與基本 BPM，來源 BPM 以原字串保存於 source payload，不當作遊戲觀測值，也不影響 Song 身分或成績比較。

## Phase 4 補充：同名歌曲與安全匹配

規格詳見 [Phase 4 歌曲與遊戲難度匹配](PHASE4_MATCHING_SPEC.md)。本次只檢查模型及記錄需求，不開發 OCR、不執行正式庫 migration。

- Otaku's Dream 9th Anime Mix 的兩首 JOINT 已有獨立 Song ID 990、1004。不同 SongFolder 永遠保持獨立，Artist 的日文／羅馬字、Title 或 BPM 相同都不能作合併依據。
- 自動綁定必須同時確認 Pack、歌曲名稱、本人 E／N／H、該難度遊戲 LV、完整音符數、完整遊戲時間，且只剩唯一 Song／GameDifficulty。任何缺值、衝突或多個候選一律 pending，禁止第一筆、排序或 Artist 相似度決定身份。
- 時間格式統一為秒，例如 1:36=96 秒；95 與 96 秒視為衝突，不自行設定容差。原始音樂長度不能覆蓋遊戲時間。
- 目前無時間欄位；最小提案是在 game_difficulties 加 duration_seconds／duration_source／duration_verified。未確認時秒數與來源為 NULL、verified=0，原始觀測另存證據，不回填猜測值。此提案尚未 migration。
- 未來 GUI 顯示全部候選的 Song ID、Pack、SongFolder 及各難度數值，人工選擇後保存 difficulty_id、確認方式及證據；不改 Song ID、不合併來源、不把單次人工決策變成同名歌曲的全域對應。
- 多人本人／HOST 資料分離、非 HOST Pack 未知、connect 管理 Pack 與原始來源不等價、失敗／退出／等待結算規則全部繼續適用。

## Phase 3.5 已授權：熱舞官方譜面獨立匯入

使用者已要求先完成 song_table.csv 的 K 模式匯入，不與自製 OCR／時間匹配規格互相阻塞。v0.5.0 使用 schema 5，獨立 CLI 與 GUI 預覽／匯入，只建立 mode=K，T／空白排除。fileId 保留原值，K 內不同 gn 的重複 fileId 保持獨立，不合併、不覆蓋。官方來源與自製／connect 分開，既有 40 Pack、1,859 Song 及 2,930 檔案來源 ID 保留。

E／N／H 的 LV 與 notes 皆正整數才建立難度，0／空白不補假值；MAX=notes×2。官方 CSV 來源另標 official_csv，不冒充手動或 OCR。重匯相同資料為 no-op，衝突整批停止，備份、回滾及非空歷史保留均需驗證。GUI 官方來源卡為檢視，自製歌曲手動編輯仍保留。

Phase 4 的 JOINT、六條件唯一匹配、時間及人工確認規格繼續有效，但尚未實作，不是官方歌單匯入的前置條件。

## Phase 4A：靜態 PNG OCR（0.6.0）

依本次授權，完成 22 張原始 PNG 的逐圖檢視、可執行本機 OCR、七種畫面分類、選歌文字／E／N／H／LV／完整音符／BPM／時間擷取、可見 Pack 原文與候選查詢。原圖與正式 schema 5 只讀取，沒有 migration、資料回填、即時監控或成績綁定。

以可見紫色行與分頁高亮判斷選中內容；不以檔名、第一行、Artist 或資料庫數值填入 OCR 答案。Pack 清單可見不代表已選 Pack，截斷文字只提供補全候選；未確認 Pack 或資料庫遊戲值不足時一律待人工確認。JOINT 兩版本的數值可區分，但 990／1004 仍缺已確認難度／時間，不能放行自動綁定。

多人結算定位設定暱稱（預設 TC），他人 GAME OVER 與 HOST LV 不挪作本人資料；遊戲中不判最終完成／失敗。官方匯入保持獨立。模型／套件安裝於獨立可選環境，原有 GUI／掃描不依賴 OCR。

驗收依人工逐圖標註計算完全相同比率，JSON 與可讀報告列出各圖結果、候選、缺值與失敗原因；不以模型信心值冒充準確率。完整操作、實測數字與 EASY／NORMAL、多語主歌名、其他皮膚等測試缺口見 [Phase 4A](PHASE4A_OCR.md)。

## Phase 4C：結算 OCR 與人工確認（0.8.1）

多人無 Pack 時必須保留結算並搜尋全 Custom 庫，包含 connect、其他 Pack 及新 Pack；不能以 Pack 名稱或歌曲數量推定取得方式。Pending Review 重用背景掃描更新整個 SONG，保留 ID／歷史／草稿，缺失或變動來源所屬整個 SongFolder 延後，不自動綁定候選。來源分頁不截斷第 100 首後的歌曲。人工 Song ID／本人列／遊戲時間與確認依據另存 sidecar，原始 OCR 不覆寫。完整規則及測試見 [多人補充](PHASE4C_MULTIPLAYER.md)。

沿用監控 OCR／Session 與 GUI。本人結算資料列必須依設定暱稱定位，不以排名；多人 footer LV 保留 HOST scope。數字缺值保留 NULL，不補成 0；正常完成與驗證總數比較，GAME OVER 部分判定不能當完整音符。原圖、原 OCR／信心、修改歷程與錯誤原因保留。

待確認頁提供獨立 Song ID／路徑選擇、人工數字修正、草稿與審核保存，以及 EX Score／MAX／百分比／AC／ACP／FAILED 預覽。正式 PlayRecord 與最佳成績不自動寫入。Session＋原圖 SHA-256 穩定識別、版本衝突與相同儲存重送冪等；測試副本演練正式表交易與回滾。

遊玩旁觀提示獨立分類 SPECTATING，不建立本人 Session；旁觀結算若沒有設定的本人暱稱，判定與分數維持 NULL，不套用排名第一名或其他玩家。

Pack 顏色僅 CANDIDATE；人工或後續經驗證方法才可 CONFIRMED。重新開清單／可能切換時撤銷舊 Pack，清單關閉後可獨立人工確認，不能由歌名猜來源。受控未選取／Hover／已選取／關閉對照尚缺，所需樣本與 Phase 5 寫入整合建議見 [Phase 4C](PHASE4C_RESULTS.md)。

## Phase 4B：即時監控（0.7.0）

以 WGC 被動擷取指定視窗，支援版本標題搜尋／手動選擇、縮放、失焦、最小化與重新發現。擷取、OCR、GUI 分離，以最新幀、變化偵測、ROI／辨識間隔及連續確認控制耗用與誤判。

狀態機追蹤選歌、房間、遊玩、本人結果與中斷。Pack 清單可見但未取得明確選取證據時撤銷舊 Pack；關閉後只沿用有效確認，不從歌名推測。SONG_SELECT→PLAYING 鎖定本次快照；下一首不能沿用前一輪 Song ID。結算只保存一次；沒有結算的退出／關閉／監控中斷不生成假結果。

人工匹配另存完整六項證據、來源身份及確認截圖；不改正式 SQLite，不覆蓋其他人工對應。官方只查既有 K 來源，不與 Custom／connect 合併。缺資料庫遊戲值與時間時不能宣稱自動匹配成功。

實測種類必須分開：自動化／原圖回放、真實測試視窗、使用者真實遊玩。正常遊玩與一次性結算已實測；自動 Pack 反白、多人的真實端到端等未完成的驗證如實列入限制。完整資料在 [Phase 4B](PHASE4B_MONITOR.md)。


## Phase 5A：明確正式提交（0.9.0）

先保存人工確認，再核對正式摘要與明確勾選後才寫入 PlayRecord。正式必要資料含穩定 Song／E-N-H／歌曲 LV、可靠 Session／原圖、本人模式／資料列、有效判定與完成狀態；NULL 不補0。正常完成須完整音符已驗證且合計一致；FAILED 可保存未知 MAX 的部分成績，但不提升正常完成最高 EX。EX／MAX／差值整數計算、百分比顯示兩位；ACP／AC／CLEAR／FAILED 核心與預覽共用。最高分與最佳燈號分別查詢。

Schema6 追加當次數值與確認證據欄位、Review 唯一限制及正式資料約束，保留既有 ID；新提交前備份，PlaySession／必要 GameDifficulty／Record 同一交易，失敗回滾。相同 Session／Review 重送返回既有紀錄，不依歌曲／分數／截圖雜湊合併不同 Session。正式紀錄不可被草稿覆寫。基本歷史可查詢、查看原圖與原始／修正後證據。未自動提交既有 Pending Review，未製作完整動畫或儀表板。見 [Phase5A](PHASE5A_SUBMISSIONS.md)。
