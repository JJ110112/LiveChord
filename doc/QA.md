# LiveChord 品管文件

> 版本: 3.5 | 日期: 2026-04-05
> 和弦引擎: BTC Transformer (ISMIR 2019)
> 對應規格書: SPEC.md v2.0

---

## 1. 測試策略

| 測試層級 | 範圍 | 工具 | 執行時機 |
|----------|------|------|----------|
| API 測試 | 所有 REST endpoint (6 組) | pytest + urllib | 每次後端修改 |
| UI 測試 | 頁面交互流程 | Playwright | 每次前端修改 |
| 和弦準確度 | 偵測 vs 參考答案 | run_test.py (Lv1-Lv5) | 每次演算法修改 |
| 效能測試 | 回應時間、記憶體、GPU | DevTools + 伺服器 log | 重大變更 |
| 迴歸測試 | 全量測試 | pytest (66+ cases) | 上線前 |

---

## 2. 和弦偵測準確度（核心 KPI）

### 2.1 分級標準

| 級別 | 難度 | Key 正確率 | 和弦正確率 | 時間誤差 |
|------|------|-----------|-----------|----------|
| Lv1 | 三和弦流行 | 100% | ≥ 80% | < 1.0s |
| Lv2 | 七和弦 R&B | 100% | ≥ 70% | < 1.5s |
| Lv3 | 爵士標準曲 | ≥ 80% | ≥ 60% | < 2.0s |
| Lv4 | 複雜 Fusion | ≥ 70% | ≥ 50% | < 2.0s |
| Lv5 | 極難挑戰 | ≥ 60% | ≥ 40% | N/A |

### 2.2 測試曲目總覽（已放入 data/test_songs/）

#### Lv1 — 三和弦流行

| # | 曲目 | Key(偵測) | Key(參考) | 主要和弦(偵測) | 主要和弦(參考) | 耗時 |
|---|------|-----------|-----------|----------------|----------------|------|
| 1 | Dancing Queen - ABBA | A | A | A, D, E, Db, Gbm, B | A, D, E, F#m, Bm | 2.5s |
| 2 | Hotel California - Eagles (Live) | Bm | Bm | Bm7, A, Em, Gb7, D, G | Bm, F#, A, E, G, D, Em | 4.2s |
| 3 | Shape of You - Ed Sheeran | E | C#m | Dbm, Gbm, A, B | C#m, F#m, A, B | 2.3s |
| 4 | Mamma Mia - ABBA | D | D | D, G, A, Bb | D, A, G, Bm | 2.2s |
| 5 | Billie Jean - Michael Jackson | Gbm | F#m | Gbm, Dbm, Bm | F#m, G#m | 5.7s |

> 備註：BTC 使用 # 而非 b 的標記（Gb=F#, Db=C#），Key 偵測 4/5 正確，Shape of You 偵為 E 而非 C#m（相對大調，可接受）

#### Lv2 — 七和弦 R&B

| # | 曲目 | Key(偵測) | Key(參考) | 主要和弦(偵測) | 耗時 |
|---|------|-----------|-----------|----------------|------|
| 1 | Desperado - Eagles (Live) | G | G | G, G7, C, Cm, Em, A7, D7, Bm7 | 2.5s |
| 2 | Castle on the Hill - Ed Sheeran | D | D | D, G, Bm7, Asus4, Gmaj7 | 2.7s |
| 3 | Afterglow - Ed Sheeran | Abm | C | B, E, Gb, Abm7 | 3.6s |
| 4 | Don't Stop 'Til You Get Enough - MJ | E | B | B, A | 4.9s |
| 5 | I Can't Tell You Why - Eagles (Live) | D | Db | Bm, A, Gbm, Dmaj7, Gmaj7, Em7 | 3.1s |

> 備註：Desperado 七和弦辨識優秀（G7, A7, D7, Cm6）。Afterglow/Don't Stop Key 偏移

#### Lv3 — 爵士標準曲

| # | 曲目 | Key(偵測) | Key(參考) | 主要和弦(偵測) | 耗時 |
|---|------|-----------|-----------|----------------|------|
| 1 | Get It On - Brian Culbertson | Cm ✓ | Cm | Cm, Dm7, G7, Cm7, Fm7 | 4.1s |
| 2 | My Foolish Heart - Bill Evans | Gbm | Bb | Dbm7, Gbm7, Bm7, E7, Amaj7 | 2.9s |
| 3 | Waltz for Debby - Bill Evans | Dm | F | F, Dm7, Gm7, C7, A7, D7, Bbm6 | 4.3s |
| 4 | Night Train - Oscar Peterson | Am | G (blues) | G7, D7, Dm7, Eb7, Fmaj7 | 3.8s |
| 5 | Jazz Hands - Bob James | A | (待定) | Gbm, Dbm, B, Gbm7 | 2.0s |

> 備註：Get It On 已驗證與 Chordify 吻合。Waltz for Debby ii-V-I 進行辨識良好

#### Lv4 — 複雜 Fusion

| # | 曲目 | Key(偵測) | Key(參考) | 主要和弦(偵測) | 耗時 |
|---|------|-----------|-----------|----------------|------|
| 1 | So What - Miles Davis | Am | Dm→Ebm | Am, Dm, G, Eb, Ebm, Gb | 10.3s |
| 2 | Blue in Green - Miles Davis | Dm | Bb | Cm7, Bbmaj7, A7, Dm7, Em7 | 6.4s |
| 3 | Mediterranean Sundance - Al Di Meola | Em | Am/Em | Am, Em, B, C, G, D | 12.8s |
| 4 | Mister Magic - Bob James | Gm | (待定) | Cm, Gm, F7, Ebmaj7, Abmaj7 | 3.5s |
| 5 | For Once In My Life - Stevie Wonder | F | F | F, Faug, Gm7, C7, Bbmaj7, Dm7 | 1.7s |

> 備註：For Once In My Life 和弦辨識出色（含 Faug, Gbdim7 等複雜和弦）

#### Lv5 — 極難挑戰

| # | 曲目 | Key(偵測) | 主要和弦(偵測) | 耗時 |
|---|------|-----------|----------------|------|
| 1 | Chopin Polonaise Op.53 | Fm | Ab, Fm, Eb7, Db, Bbm7, C7 | 4.0s |
| 2 | Freddie Freeloader - Miles Davis | Cm | Gm, Fm, Bb7, Eb7, Ab | 10.9s |
| 3 | Journey In Satchidananda - Alice Coltrane | C | E, Bb, Am, G, Dm | 11.7s |
| 4 | Orient Express - Logic System | Am | Am, F, Dm6, E7, B7 | 2.6s |
| 5 | Fantasia Suite - Al Di Meola | D | Em, G, D, A, Am | 9.9s |

> 備註：Chopin 偵為 Fm（正確為 Ab 大調，其相對小調），偵測未崩潰。電子音樂 Orient Express 辨識合理

### 2.3 和弦正確率計算方式

```
正確率 = 正確和弦時長 / 總有和弦時長 × 100%

判定「正確」的條件（任一滿足即算正確）：
1. 完全正確：根音 + 品質皆正確（Cm7 = Cm7）
2. 根音正確：根音對但品質略偏（Cm7 vs Cm → 算 0.7 分）
3. 時間容差：和弦開始時間在 ±容差 秒內
```

### 2.4 測試執行

```bash
# 單級測試
cd data/test_songs
python run_test.py Lv1

# 全級測試
python run_test.py all

# 單曲命令列測試
cd backend
python chord_detect.py "Z:/POP/E-POP/ABBA/ABBA - ABBA Gold/Dancing Queen.flac"
```

### 2.5 基準測試摘要（BTC v4, 2026-03-28）

| 指標 | 結果 |
|------|------|
| 測試曲目 | 25 首（Lv1×5, Lv2×5, Lv3×5, Lv4×5, Lv5×5） |
| 成功率 | 25/25 (100%, 無崩潰) |
| 總耗時 | 124.6 秒（平均 5.0s/首） |
| Key 正確率 (Lv1) | 4/5 (80%) — Shape of You 偵為 E (= C#m 相對大調) |
| Key 正確率 (Lv2) | 2/5 (40%) — 需改進 |
| Key 正確率 (Lv3) | 2/5 (40%) — Get It On ✓, Waltz for Debby 偏差 |
| Key 正確率 (Lv4) | 1/5 (20%) — For Once In My Life ✓ |

#### 已與 Chordify 比對驗證

| 曲目 | Key | 和弦進行 | 比對結果 |
|------|-----|----------|----------|
| Get It On - Brian Culbertson | Cm ✓ | Cm→Dm7→G7→Cm ✓ | 與 Chordify 完全吻合 |
| Desperado - Eagles | G ✓ | G→G7→C→Cm→Em→A7→D7 ✓ | 七和弦辨識出色 |
| Hotel California - Eagles | Bm ✓ | Bm7→A→Em→F#7→D→G ✓ | 進行正確 |
| Dancing Queen - ABBA | A ✓ | A→D→E→F#m→Bm ✓ | 基本正確 |

#### 已知問題

| 問題 | 影響 | 優先級 |
|------|------|--------|
| Key 偵測採用和弦加權法，BTC 的 # 命名(Gb/Db)影響匹配 | Lv2-Lv4 Key 偏低 | P1 |
| So What 的 Dm Dorian 偵為 Am | Modal 曲目 key 不準 | P2 |
| 片段過多（100-300 個/首）| 顯示碎片化 | P2 |

---

## 3. 後端 API 測試

### 3.1 音樂庫 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| M-01 | /api/browse | GET | 根目錄瀏覽（多磁碟 @0/@1） | 回傳虛擬根目錄清單 | ☐ |
| M-02 | /api/browse?path=@0/Jazz | GET | 子目錄瀏覽 | 回傳 Artist 清單 | ☐ |
| M-03 | /api/browse 深層 | GET | 瀏覽到 Track | 回傳 .flac 清單 | ☐ |
| M-04 | /api/search?q=blackpink | GET | 關鍵字搜尋（標題/演出者/專輯/檔名） | 回傳匹配結果 | ☐ |
| M-05 | /api/search?q= | GET | 空搜尋 | 回傳空清單 | ☐ |
| M-06 | /api/track/info | GET | 單曲 metadata | 回傳 title/artist/duration/sample_rate | ☐ |
| M-07 | /api/track/stream | GET | FLAC 串流 | 回傳音訊，支援 Range | ☐ |
| M-08 | /api/track/stream (Range) | GET | 分段串流 | 206 + Content-Range | ☐ |
| M-09 | /api/track/cover | GET | 封面圖片（外部 jpg） | 回傳 JPEG | ☐ |
| M-10 | /api/track/cover (內嵌) | GET | FLAC 內嵌封面 | 回傳圖片 | ☐ |
| M-11 | /api/track/cover (無封面) | GET | 無圖時 | 404 | ☐ |
| M-12 | /api/library/stats | GET | 庫統計 | 回傳 total_tracks, scan_time | ☐ |
| M-13 | /api/library/scan | POST | 啟動增量掃描 | 回傳 ok + 背景執行 | ☐ |
| M-14 | /api/library/scan/status | GET | 掃描進度 | 回傳 running/progress/new/updated/deleted | ☐ |
| M-15 | /api/settings | GET | 取得設定 | 回傳 music_roots, midi_root | ☐ |
| M-16 | /api/settings | POST | 更新設定（多磁碟路徑） | ok: true | ☐ |

### 3.2 和弦 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| C-01 | /api/chord/info/Cmaj7 | GET | 和弦資訊 | 回傳 notes, jianpu | ☐ |
| C-02 | /api/chord/info/Unknown | GET | 未知和弦 | 404 | ☐ |
| C-03 | /api/chord/diagram/guitar/Am | GET | 吉他和弦圖 | 回傳 strings, baseFret | ☐ |
| C-04 | /api/chord/diagram/ukulele/C | GET | 烏克麗麗和弦圖 | 回傳 4 弦資料 | ☐ |
| C-05 | /api/chords?path=... | GET | 取得和弦譜 | 回傳 chords 陣列 + key + source | ☐ |
| C-06 | /api/chords (無資料) | GET | 未偵測曲目 | exists: false | ☐ |
| C-07 | /api/chords | POST | 儲存和弦譜 | ok: true | ☐ |
| C-08 | /api/chords/detect | POST | 單曲 BTC 偵測 | 回傳 key + chords | ☐ |
| C-09 | /api/chords/detect (Viterbi fallback) | POST | BTC 失敗曲目（如 chiptune） | 自動 fallback Viterbi，回傳結果 | ☐ |
| C-10 | /api/chords/midi-search | GET | MIDI 檔案搜尋 | 回傳匹配的 MIDI 路徑清單 | ☐ |
| C-11 | /api/chords/midi-search (無匹配) | GET | 無匹配 MIDI | 回傳空清單 | ☐ |
| C-12 | /api/chords/midi-import | POST | MIDI 和弦匯入 | 回傳 key + chords，source="midi" | ☐ |
| C-13 | /api/chords/midi-import (key 不符) | POST | MIDI key ≠ 音訊 key | 回傳警告 + fallback BTC | ☐ |
| C-14 | /api/chords/midi-upload | POST | 上傳 MIDI 匯入 | ok: true | ☐ |
| C-15 | /api/chords/batch-midi-import | POST | 批次 MIDI 匯入 | 背景執行 | ☐ |
| C-16 | /api/chords/batch-detect | POST | 批次 BTC 偵測啟動 | 背景執行 | ☐ |
| C-17 | /api/chords/batch-detect/status | GET | 批次進度 | done/total/current/errors | ☐ |
| C-18 | /api/chords/tracks | GET | 全曲目和弦狀態 | 回傳清單含 source/key/count | ☐ |
| C-19 | /api/chords/stats | GET | 和弦統計 | total/coverage/batch_status | ☐ |

### 3.3 使用者 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| U-01 | /api/favorites | GET | 取得最愛（含和弦摘要） | 回傳 favorites 清單 | ☐ |
| U-02 | /api/favorites | POST | 新增最愛 | ok: true | ☐ |
| U-03 | /api/favorites | DELETE | 移除最愛 | ok: true | ☐ |
| U-04 | /api/recent | GET | 最近播放 | 回傳 recent 清單（最多 50） | ☐ |
| U-05 | /api/recent | POST | 新增紀錄（自動去重） | ok: true | ☐ |

### 3.4 AI API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| AI-01 | /api/ai/suggest | GET | Markov 預測下一和弦 | 回傳 top_k 候選 + 機率 | ☐ |
| AI-02 | /api/ai/suggest (空 chords) | GET | 無上下文 | 回傳預設建議或空 | ☐ |
| AI-03 | /api/ai/generate | GET | 生成和弦進行 | 回傳 length 長度的和弦序列 | ☐ |
| AI-04 | /api/ai/similar | GET | Chord2Vec 相似和弦 | 回傳相似和弦 + 距離 | ☐ |
| AI-05 | /api/ai/groove | GET | Groove Dictionary | 回傳常見 pattern | ☐ |
| AI-06 | /api/ai/jazzify | POST | Jazzify L1 重配和 | 回傳修改後和弦 + 變更數 | ☐ |
| AI-07 | /api/ai/jazzify L2 | POST | Jazzify L2 (ii-V-I) | 和弦數增加，含 ii-V 插入 | ☐ |
| AI-08 | /api/ai/jazzify L3 | POST | Jazzify L3 (tritone sub) | 高級替換，不崩潰 | ☐ |
| AI-09 | /api/ai/melody | GET | 旋律萃取 | 回傳 MIDI note 序列 [{start,end,midi}] | ☐ |
| AI-10 | /api/ai/melody (無音訊) | GET | 不存在的檔案 | 404 或錯誤訊息 | ☐ |
| AI-11 | /api/ai/sections | GET | 段落偵測 | 回傳 sections [{start,end,type,label}] | ☐ |
| AI-12 | /api/ai/patterns | GET | Pattern 辨識 | 回傳辨識結果 (ii-V-I 等) | ☐ |
| AI-13 | /api/ai/emission | GET | HMM emission matrix | 回傳矩陣 | ☐ |
| AI-14 | /api/ai/viterbi | POST | Viterbi 解碼 | 回傳和弦路徑 + log probability | ☐ |
| AI-15 | /api/ai/accompaniment | GET | 伴奏生成 (Block) | 回傳 left_hand + right_hand events | ☐ |
| AI-16 | /api/ai/accompaniment (Arpeggio, L2) | GET | 伴奏生成（指定風格+難度） | 回傳分解和弦 pattern + 指法 | ☐ |
| AI-17 | /api/ai/accompaniment (無和弦) | GET | 尚未偵測的曲目 | 錯誤訊息或空 | ☐ |
| AI-18 | /api/ai/retrain | POST | 重新訓練模型 | ok: true，模型更新 | ☐ |
| AI-19 | /api/ai/stats | GET | 模型統計 | 回傳各模型資訊 | ☐ |
| AI-20 | /api/ai/evaluate | GET | 模型評估 | 回傳 perplexity, accuracy | ☐ |

### 3.5 自動工作器 API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| W-01 | /api/auto/status | GET | 工作器狀態 | running: false (預設) | ☐ |
| W-02 | /api/auto/settings | GET | 取得設定 | 回傳 scan_interval, max_per_cycle, skip_genres | ☐ |
| W-03 | /api/auto/settings | POST | 更新設定 | ok: true | ☐ |
| W-04 | /api/auto/start | POST | 啟動工作器 | ok: true | ☐ |
| W-05 | /api/auto/stop | POST | 停止工作器 | ok: true | ☐ |
| W-06 | /api/auto/trigger | POST | 手動觸發一次週期 | ok: true | ☐ |
| W-07 | /api/auto/log | GET | 活動紀錄 | 回傳 log 陣列 | ☐ |

### 3.6 Benchmark API

| # | 端點 | 方法 | 測試內容 | 預期 | 通過 |
|---|------|------|----------|------|------|
| BM-01 | /api/benchmark/songs | GET | 測試歌曲清單 | 回傳 Lv1-Lv5 分組清單 | ☐ |
| BM-02 | /api/benchmark/ground-truth/{lv}/{song} | GET | 載入 ground truth | 回傳和弦標注 | ☐ |
| BM-03 | /api/benchmark/ground-truth | POST | 儲存 ground truth | ok: true（含驗證） | ☐ |
| BM-04 | /api/benchmark/detect/{lv}/{song} | POST | 執行偵測 | 回傳偵測結果 | ☐ |
| BM-05 | /api/benchmark/detection/{lv}/{song} | GET | 取得偵測結果 | 回傳已存結果 | ☐ |
| BM-06 | /api/benchmark/score/{lv}/{song} | GET | 單曲評分 | 回傳 root_accuracy, full_accuracy | ☐ |
| BM-07 | /api/benchmark/score-all | GET | 全域聚合評分 | 回傳各級別聚合分數 | ☐ |

---

## 4. 前端 UI 測試

### 4.1 Dashboard 首頁

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| H-01 | 載入首頁 | 顯示 Dashboard：最近播放/最愛橫向卡片 + 搜尋列 | ☐ |
| H-02 | 目錄瀏覽 | Genre → Artist → Album → Track 逐層展開 | ☐ |
| H-03 | 搜尋歌曲 | 輸入關鍵字，300ms debounce 後顯示結果 | ☐ |
| H-04 | 搜尋結果含難度 | 顯示難度星級 + 和弦資訊 | ☐ |
| H-05 | 點擊結果 | 跳轉至播放頁 | ☐ |
| H-06 | 最愛卡片 | 橫向卡片，顯示封面+歌名，可點擊播放 | ☐ |
| H-07 | 最近播放卡片 | 橫向卡片，最多 50 首 | ☐ |
| H-08 | 拖拉慣性滾動 | 滑鼠拖拉最愛/最近區塊，鬆手後有動量滾動 | ☐ |
| H-09 | 封面縮圖 | Album 有 cover.jpg 時顯示 | ☐ |
| H-10 | 和弦覆蓋率 | 顯示全庫和弦偵測覆蓋率統計 | ☐ |
| H-11 | 空搜尋結果 | 顯示「找不到結果」 | ☐ |

### 4.2 播放頁 — 基礎播放

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| P-01 | 載入播放頁 | 顯示封面、歌曲資訊、播放器 | ☐ |
| P-02 | 播放/暫停 | 正常播放 FLAC，暫停/恢復正常 | ☐ |
| P-03 | 進度條 seek | 點擊/拖曳進度條跳轉正確位置 | ☐ |
| P-04 | 音量調整 | 滑桿調整音量，localStorage 持久化 | ☐ |
| P-05 | 上一首/下一首 | 同目錄內換曲 | ☐ |
| P-06 | 收藏切換 | 愛心 toggle，狀態持久 | ☐ |
| P-07 | 歌曲 metadata | 標題/演出者/專輯/取樣率/位元深度 | ☐ |
| P-08 | 播放結束 | 進度歸零，高亮消失 | ☐ |
| P-09 | 大檔案播放 | >100MB FLAC 不中斷 | ☐ |
| P-10 | 循環模式 | 三模式切換：關閉 / 單曲 / 最愛循環 | ☐ |
| P-11 | 最愛循環 | 最愛清單內依序播放 | ☐ |
| P-12 | 播放速度切換 | 0.5x/0.75x/1x/1.25x/1.5x/2x 點擊循環 | ☐ |
| P-13 | 速度持久化 | 重載後速度設定保留 | ☐ |
| P-14 | 播放器內搜尋 | header 搜尋框，結果含難度，可直接切歌 | ☐ |

### 4.3 播放頁 — 三分頁模式

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| T-01 | 分頁切換 | Overview / Diagrams / 88-Key 三分頁正常切換 | ☐ |
| T-02 | 分頁記憶 | 重載後記住上次選擇的分頁 | ☐ |
| T-03 | Overview 和弦時間軸 | 橫向和弦方塊，自動高亮當前和弦並捲動置中 | ☐ |
| T-04 | Overview 大字顯示 | 上方大字當前和弦名 + 簡譜/圖 | ☐ |
| T-05 | Overview 點擊跳轉 | 點擊和弦卡片跳轉到該時間 | ☐ |
| T-06 | Diagrams ribbon | 水平 ribbon，和弦按時間定位，中心線標示播放位置 | ☐ |
| T-07 | Diagrams 佈局 | 和弦方塊不重疊，寬度合理計算 | ☐ |
| T-08 | 88-Key Canvas | 88 鍵全鍵盤渲染，含八度標記、C4 指示線 | ☐ |
| T-09 | 88-Key ribbon | 鍵盤上方水平和弦方塊列，含和弦名/簡譜/旋律簡譜 | ☐ |

### 4.4 播放頁 — 和弦與簡譜

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| CS-01 | 簡譜顯示 | C-relative 簡譜（1-7），上標升降記號 | ☐ |
| CS-02 | 鋼琴和弦圖 | Canvas 鍵盤，core=紅點，ext=藍點 | ☐ |
| CS-03 | 吉他和弦圖 | 顯示指法、barre、baseFret | ☐ |
| CS-04 | 烏克麗麗和弦圖 | 4 弦和弦圖 | ☐ |
| CS-05 | 樂器模式切換 | Piano/Guitar/Ukulele 切換正常 | ☐ |
| CS-06 | 即時高亮 | 播放時當前和弦高亮 + 自動捲動 | ☐ |
| CS-07 | 時間軸連動 | seek 後高亮更新到正確和弦 | ☐ |
| CS-08 | 移調 +/- | 所有和弦正確移調，Key 顯示更新 | ☐ |
| CS-09 | Capo 設定 | Capo 數值影響顯示和弦，僅吉他/烏克麗麗模式可見 | ☐ |
| CS-10 | 和弦來源標記 | MIDI/BTC badge 正確顯示 | ☐ |
| CS-11 | 無和弦時 | 顯示「請按偵測按鈕」提示 | ☐ |

### 4.5 播放頁 — 88 鍵鋼琴模式

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| K-01 | 手部模式切換 | 雙手/左手/右手 三模式正常切換 | ☐ |
| K-02 | 左手和弦標示 | 和弦音以紅色標示在琴鍵上 | ☐ |
| K-03 | 右手旋律標示 | 旋律音以綠色菱形標示在琴鍵上 | ☐ |
| K-04 | Voice Leading | 和弦切換時音符最小移動距離 | ☐ |
| K-05 | Sustain 視覺化 | 前一和弦音符灰色漸出 (~0.5s) | ☐ |
| K-06 | 手部模式記憶 | 重載後記住選擇 (localStorage) | ☐ |
| K-07 | Canvas 響應式 | 視窗大小變化時鍵盤正確縮放 (ResizeObserver) | ☐ |
| K-08 | DPI 適配 | 高 DPI 螢幕 (devicePixelRatio) 不模糊 | ☐ |
| K-09 | 瀑布流白/黑鍵色階 | 同一手音符落在白鍵用淺色、黑鍵用深色（左手淺藍/深藍、右手淺橘/深橘） | ☐ |
| K-10 | 琴鍵高亮清晰度 | 按下琴鍵實色填充，頂部有淡白洗色 3D 感，非漸層淡出 | ☐ |
| K-11 | 琴鍵底部發光 | 按下琴鍵底部呈現光暈 + 亮白光點（Synthesia 風格） | ☐ |

### 4.6 播放頁 — 縮放與全螢幕

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| Z-01 | 縮放控制 | -/+/重置 按鈕正常運作 | ☐ |
| Z-02 | 縮放範圍 | 50%~300%，13 段 preset | ☐ |
| Z-03 | 分頁獨立記憶 | 每個分頁各自保存縮放等級 | ☐ |
| Z-04 | 全螢幕進入 | 點擊全螢幕按鈕正確進入 | ☐ |
| Z-05 | 全螢幕 mini player | 顯示播放/暫停、前後曲、速度、標題跑馬燈 | ☐ |
| Z-06 | 全螢幕縮放獨立 | 全螢幕 zoom 與一般模式分開記憶 | ☐ |
| Z-07 | 退出全螢幕 | 恢復原縮放等級 | ☐ |

### 4.7 播放頁 — AI 互動功能

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| AI-U01 | 偵測按鈕 | 手動點擊後顯示 overlay 進度 | ☐ |
| AI-U02 | 自動偵測 | 首次播放無和弦曲目時自動觸發偵測 | ☐ |
| AI-U03 | MIDI 搜尋 | 偵測時優先搜尋匹配 MIDI | ☐ |
| AI-U04 | BTC fallback | 無 MIDI 時使用 BTC 偵測 | ☐ |
| AI-U05 | Viterbi fallback | BTC 偵測 0 和弦時自動 Viterbi fallback | ☐ |
| AI-U06 | 偵測失敗 | 錯誤訊息 + 重試選項 | ☐ |
| AI-U07 | Jazzify OFF→L1 | 點擊切換，和弦更新，顯示變更數量 | ☐ |
| AI-U08 | Jazzify L1→L2→L3 | 逐級切換，和弦複雜度遞增 | ☐ |
| AI-U09 | Jazzify L3→OFF | 還原原始和弦 | ☐ |
| AI-U10 | Jazzify 橘色指示 | 啟用時工具列背景橘色 | ☐ |
| AI-U11 | AI 和弦建議 | 點擊按鈕，Toast 顯示 Top 5 候選 + 機率 + degree | ☐ |
| AI-U12 | 段落標記顯示 | 段落色彩標記在和弦方塊上方 | ☐ |
| AI-U13 | 段落標籤 | 第一個和弦顯示段落名稱（前奏/主歌/副歌...） | ☐ |
| AI-U14 | 難度星級 | 根據和弦數量顯示 1-4 星 | ☐ |

### 4.8 和弦編輯器

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| E-01 | 載入編輯器 | 顯示時間軸 + 和弦列表 | ☐ |
| E-02 | 新增和弦 | 點擊時間軸新增和弦標記 | ☐ |
| E-03 | 修改和弦名 | 雙擊編輯和弦名稱 | ☐ |
| E-04 | 拖曳調整時間 | 拖曳和弦標記調整 time/end | ☐ |
| E-05 | 刪除和弦 | 選取後刪除 | ☐ |
| E-06 | 儲存 | 儲存後播放頁可讀取 | ☐ |

### 4.9 Admin 管理頁

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| A-01 | 載入管理頁 | 顯示統計儀表板 | ☐ |
| A-02 | 音樂庫路徑設定 | 顯示/新增/移除多磁碟路徑 | ☐ |
| A-03 | 音樂庫掃描 | 增量/全量掃描，進度即時更新 | ☐ |
| A-04 | 批次和弦偵測 | 啟動/停止，進度即時更新 | ☐ |
| A-05 | 自動排程設定 | 調整間隔/每週期數/跳過曲風 | ☐ |
| A-06 | 工作器啟停 | 啟動/停止/立即觸發 | ☐ |
| A-07 | 活動紀錄 | 顯示最近操作 log | ☐ |
| A-08 | 覆蓋率統計 | 顯示總曲數、已偵測、覆蓋率 % | ☐ |

### 4.10 Benchmark 評測頁

| # | 測試項目 | 預期結果 | 通過 |
|---|----------|----------|------|
| BM-U01 | 載入評測頁 | 顯示 Lv1-Lv5 測試歌曲分組 | ☐ |
| BM-U02 | 執行偵測 | 選曲後執行 BTC 偵測 | ☐ |
| BM-U03 | 對比顯示 | 並列顯示 ground truth vs 偵測結果 | ☐ |
| BM-U04 | 評分顯示 | 顯示 root accuracy + full accuracy | ☐ |
| BM-U05 | 全域統計 | 跨所有等級的聚合分數 | ☐ |

---

## 5. 安全性檢查

| # | 項目 | 檢查內容 | 預期 | 通過 |
|---|------|----------|------|------|
| S-01 | 路徑穿越 | path=../../etc/passwd | 403 拒絕 | ☐ |
| S-02 | 路徑穿越 (encoded) | path=%2e%2e%2f | 403 拒絕 | ☐ |
| S-03 | 檔案類型 | stream 非 .flac 檔案 | 400 拒絕 | ☐ |
| S-04 | XSS | 搜尋含 `<script>` 的字串 | 正確 escape | ☐ |
| S-05 | 輸入驗證 | 超長字串、特殊字元 | 不崩潰 | ☐ |
| S-06 | 多磁碟路徑穿越 | @99 不存在的磁碟索引 | 404 或錯誤訊息 | ☐ |

---

## 6. 效能基準

| 指標 | 目標值 | 實測值 | 通過 |
|------|--------|--------|------|
| 首頁載入 | < 1s | | ☐ |
| 搜尋回應 (78K tracks) | < 500ms | | ☐ |
| 串流首次播放 | < 2s | | ☐ |
| 目錄瀏覽 | < 500ms | | ☐ |
| 單曲和弦偵測 (BTC, GPU) | < 15s | | ☐ |
| 單曲和弦偵測 (BTC, CPU) | < 60s | | ☐ |
| 批次偵測吞吐量 (RTX 5080) | ≥ 2 tracks/s | | ☐ |
| 旋律萃取 | < 30s | | ☐ |
| 段落偵測 | < 5s | | ☐ |
| Jazzify 重配和 | < 1s | | ☐ |
| 伴奏生成 + 指法推導 | < 2s | | ☐ |
| 增量掃描 (無變動) | < 3min | | ☐ |
| 全量掃描 (78K tracks) | < 30min | | ☐ |
| 伺服器記憶體 (idle) | < 500MB | | ☐ |
| 伺服器記憶體 (偵測中) | < 2GB | | ☐ |
| GPU 記憶體 (偵測中) | < 4GB | | ☐ |

---

## 7. 相容性

| 環境 | 測試項目 | 通過 |
|------|----------|------|
| Chrome 120+ | 全功能（含 Canvas 88-key） | ☐ |
| Edge 120+ | 全功能 | ☐ |
| Firefox 120+ | 全功能 | ☐ |
| Safari | FLAC 不支援，應顯示提示 | ☐ |
| Mobile Chrome | 基本播放+和弦檢視（非 88-key） | ☐ |
| 4K 高 DPI | Canvas 不模糊，縮放正常 | ☐ |

---

## 8. 已知限制

| 項目 | 說明 | 影響 |
|------|------|------|
| FLAC 播放 | Safari 不支援原生 FLAC 解碼 | 需改用 Opus 或提示 |
| NAS 離線 | 音樂庫磁碟不可用時功能失效 | 應顯示錯誤訊息 |
| 並行寫入 | JSON 檔無寫入鎖 | 私人用途可接受 |
| BTC 模型限制 | 170 種和弦詞彙，無法辨識 add9, 13 等變體 | 視情況 fallback Viterbi |
| GPU 架構 | 不支援的 CUDA 架構自動 fallback CPU | 速度較慢但不崩潰 |
| 古典音樂 | 和弦概念不適用古典和聲 | Lv5 僅參考 |
| 旋律萃取 | pYIN 對純器樂曲效果較差 | 人聲曲目最佳 |
| 88-Key Mobile | 88 鍵 Canvas 在小螢幕難以操作 | 建議桌面使用 |
| 教學模式 | Phase 10 開發中，伴奏生成/瀑布流尚未完成 | 預計後續迭代 |
| 沉浸模式縮放 | Overview/Diagrams 縮放後自動捲動需除以 scale 係數 | 已修復 v3.3 |
| 下拉選單文字 | 深色主題下 select option 繼承白底白字 | 已修復 v3.3 |

---

## 9. 缺陷分級

| 等級 | 定義 | 處理時限 | 範例 |
|------|------|----------|------|
| P0-Critical | 無法使用核心功能 | 立即修復 | 無法播放、偵測崩潰、伺服器無回應 |
| P1-Major | 功能異常但有替代 | 24h 內 | 和弦高亮不同步、搜尋失效、MIDI 匯入錯誤 |
| P2-Minor | UI 瑕疵 | 下次迭代 | 和弦圖缺失、樣式偏移、Canvas 渲染閃爍 |
| P3-Enhancement | 改善建議 | 排入 backlog | 新增和弦類型、UI 美化、新伴奏型態 |

---

## 10. 驗收流程

```
1. 將測試曲目 FLAC 放入 data/test_songs/Lv1~Lv5/
2. 啟動伺服器: start.bat
3. 執行 API 測試: cd tests && pytest
4. 執行和弦準確度: cd data/test_songs && python run_test.py all
5. 執行 Playwright UI 測試: npx playwright test
6. 手動 UI 測試: 開啟 http://localhost:8800 逐項驗收
   - Dashboard: 搜尋、最愛、最近播放、難度星級
   - 播放頁: 三分頁切換、88 鍵、Jazzify、段落標記
   - 管理頁: 掃描、批次偵測、自動排程
   - Benchmark: 執行偵測、對比評分
7. 佈署同步: AI agent 負責將變更的 backend/frontend 檔案複製到 W:\ 供人類驗證品質
8. 重啟生產伺服器，確認 W:\ 上運行正常
9. 記錄結果: 勾選上方清單，未通過項開 issue
10. 全部 P0/P1 修復後 → 版本通過
```

---

## 11. AI 協作開發品管鐵律 (AI QA Protocol)

為解決「AI 生成程式碼後未經測試導致 Script Error」之問題，特設立 AI 協作鐵律。未來由 AI 輔助開發的任何新功能、模組，AI 代理在回報「完成」前，強制約束以下防線：

### 防線 1：寫完必測 (Run-Before-Speak)
AI 在修改任何後端 Python 腳本後，**必須利用終端機執行過一次**。證明其無語法錯誤 (SyntaxError) 及型別錯誤 (TypeError)。
- **Python**：強制透過 `python -m py_compile` 或直接帶入 `if __name__ == "__main__":` 進行 dry-run。若出現錯訊，AI 應自行打回修正。
- **前端 JS**：若無法於終端機執行，必須提供測試建議，並誠實表明這是一段「未經環境動態編譯」的原始碼。

### 防線 2：自動化測試驅動 (Test-Driven)
結合上述的第 3 與第 4 大節：
- 每當新增或異動了後端 API、函式庫時，AI 必須一併撰寫或修改 `pytest`，並利用 CLI 執行測試。
- **只有在看見全 Passed (綠字) 狀態後**，AI 才能向人類使用者交接。

### 防線 3：邊界防呆 (Edge Case Handling)
未經防呆的快速程式碼是嚴格禁止的：
- **API 與 JSON**：不得假設外部或快取檔案必然包含所有的 Key。
- **DOM 操作**：執行 `document.querySelector` 之後必須伴隨 `if (!elem)` 檢查。
- **背景任務**：於 GPU 批次處理或是自動掃描的迴圈內，必須加入 `try...except` 並完整輸出 `logging.error`。

### 防線 4：誠實交接 (Honest Handover)
AI 的每次提交必須附上兩種清單：
1. **✅ AI 已驗證列表**（終端機與 Pytest 已確定通過之邏輯）。
2. **⚠️ 需人類驗收列表**（視覺渲染、動畫、MIDI 實體外接等 AI 無法觸及之 QA 工作）。

> **駁回權力**：當人類開發者回復「退回！你沒有跑測試就說寫完了，自己檢查 Script Error。」時，AI 必須重新啟動偵錯迴圈，執行自我測試。

### 防線 5：佈署同步 (Deploy Sync)
程式碼提交後，**必須同步至生產伺服器 W:\**。開發環境 (`C:\Users\hitea\Claude\LiveChord`) 與生產環境 (`W:\`) 是分離的，伺服器從 `W:\backend` 和 `W:\frontend` 執行。
- **後端變更**：將修改的 `.py` 檔案複製到 `W:\backend\` 對應路徑。
- **前端變更**：將修改的 `.html`、`.js`、`.css` 檔案複製到 `W:\frontend\` 對應路徑。
- **新增檔案**：確認目標目錄存在，必要時建立子目錄。
- **驗證**：同步後用 `ls -la` 確認檔案大小與時間戳正確。
- **不同步的項目**：`data/`（生產環境有自己的資料）、`doc/`、`tests/`、`.git/`。
- AI 在完成程式碼修改並通過測試後，**主動提醒或執行**佈署同步，不需使用者額外要求。

---

## 12. AI 鋼琴老師：伴奏與指法「生成-驗證」雙環架構 (Generator-Critic Architecture)

為發展極致的「AI 鋼琴老師」體驗（消除不自然的伴奏佈局及人類無法彈奏的「外星人指法」），LiveChord 規劃引入類似 **Actor-Critic (強化學習機制)** 與 **LLM-as-a-Judge (用 AI 評估 AI)** 的雙核心驗證框架。

### 12.1 生成階段 (Generator)
- **AI 指法生成器 (Fingering Generator)**
  - **技術選型**：基於 PIG Dataset 訓練的 Transformer 或 Bi-LSTM 模型，或結合傳統 HMM (隱藏式馬可夫模型) 成本計算。
  - **任務**：輸入 MIDI 音序列，根據前後文預測最省力的 1~5 根手指分配。
- **AI 伴奏生成器 (Accompaniment Generator)**
  - **技術選型**：MusicBERT、Anticipatory Music Transformer (AMT) 等符號音樂語言模型，或是將 MIDI 轉譯成文字譜交由大型語言模型（LLM）處理。
  - **任務**：接收主旋律與和弦，生成流暢且具高音樂性的流行伴奏譜（MIDI 形式）。

### 12.2 驗證與過濾階段 (The Critic Loop)
避免生成神經網路產生「音樂上好聽但人類無法彈奏」的輸出，必須建立循環驗證防線：
1. **Rule-based 的人類手指約束器 (Physical Filter)**：
   - 快速剔除致命錯誤（例如：1至5指瞬間跨度大於12半音、不合理的手部嚴重交叉、大拇指被頻繁且不自然地分配到黑鍵等）。一旦違規，即刻退回重做。
2. **LLM-as-a-Judge 評判器 (Critic AI)**：
   - 將通過物理防護網的符號樂譜轉譯後送給高階 LLM。
   - **檢驗焦點**：要求 AI 擔任「資深鋼琴名師」，根據和聲終止式的飽滿度、指法順暢度、伴奏織體（Texture）給予 1~100 評分與具體修改建議。
3. **自我優化 (Reflection & Self-Refine)**：
   - Generator 收到不達標的低分或建議，重新對局部小節進行再生，直到品質達標。

### 12.3 落地與實作藍圖 (Roadmap)
鑑於雙 AI 循環計算成本過高無法即時生成，實作將採三階段策略：
- **階段一 (物理基底)**：[已完成] 先開發強健的 Rule-based 物理約束器（人體工學驗證 `fingering_evaluator.py`）。這是一道防彈防線，遇到「不合理手部交叉」或「大跨度同指跳躍」時，會立刻觸發「安全降級 (Fallback)」壓縮八度並調回保守指法，確保出來的產物「絕對能被真人彈奏」。
- **階段二 (離線資料工廠)**：[已完成] 開發 `batch_accompaniment_worker.py` 將曲目投入這套 `Generator -> Critic -> Refine` 引擎進行批次處理。已達成使用 12 執行緒於 113.7 秒內將 8281 首曲目全部預先生成數萬種組合 (L1/L2, Arpeggio/Block)且錯誤率為 0，徹底實現了伺服器的「零延遲」提供服務。
- **階段三 (模型優化與終端呈現)**：[進行中] 前端 `LiveChord` 介面已實裝載入運算好的成品。未來重點轉向置換底層的 Generator，引入語言模型或 LSTM 產生更大膽且更具流暢音樂性的伴奏音符分配，並由 Critic 把關。

---

## 13. 邊緣運算與雙引擎批次處理架構 (Super Worker Edge Architecture)

為了解決 NAS NAS/伺服器 CPU 效能不足以應付數萬首曲目的 BTC 和弦推論與 pYIN 旋律擷取之問題，系統導入了高階 PC 作為「超級運算節點 (Super Worker)」的分散式處理策略：
- **CPU/GPU 雙重引擎**：透過 `batch_super_worker.py` 在配備了旗艦級 CPU (如 i9) 與 GPU (如 RTX 5080) 的 PC 上運行，使用 `ThreadPoolExecutor` 提供 12~24 執行緒全速處理 CPU 密集的旋律擷取。
- **Semaphore 保護機制**：利用 `threading.Semaphore(2)` 限制進入 PyTorch 的並發數，維持 GPU 滿載同時杜絕 VRAM OOM 崩潰。
- **資料庫無縫連接**：運算結果直接透過網路磁碟寫入 NAS 共用目錄 (`W:\data`)，Server 徹底轉型為輕量級 Web 與 API 提供者，達成 **Zero-CPU Server** 目標。

---

## 14. 前端 UI 架構鐵律 (UI Architecture Rules)

> 來自 2026-04-04 ~ 04-05 session 的慘痛教訓：一次 AI 鋼琴老師功能開發，因違反以下規則導致 10+ 次 UI 迴歸修復。

### 規則 1：縮放狀態隔離 (Zoom State Isolation)

| 模式 | 縮放比率 | 儲存 |
|------|---------|------|
| 一般模式（所有 tab） | **永遠 100%** | 不儲存 |
| 沉浸/全螢幕模式 | 預設 200%，使用者可調 | `localStorage` per-tab |

- 使用 `_tabZoomFs` 物件儲存全螢幕縮放，`_isFullscreen()` 判斷上下文
- `_applyZoom()` 在非全螢幕時直接回傳 100%，不寫入 `localStorage`
- **禁止**共用一組 zoom state 給兩種模式

### 規則 2：CSS 選擇器隔離 (CSS Specificity Isolation)

- **Canvas 元素**：必須用 `canvas#specificId` 選擇器，不可用 `.parent canvas`（會誤中同容器內其他 canvas）
  - ❌ `.chord-88-keys canvas { display: block }` → 覆蓋了 `.waterfall-canvas { display: none }`
  - ✅ `.chord-88-keys canvas#piano88Canvas { display: block }`
- **Flex 擴展**：`flex: 1` 只在需要填滿的模式加（如 fullscreen），不放在 base rule
  - ❌ `.chord-88-keys { flex: 1 }` → 一般模式鋼琴被推到底部
  - ✅ `.chord-display-area.fullscreen .chord-88-keys { flex: 5 }`
- **`display` 屬性**：base rule 設 `display: none` 的元素，fullscreen rule 必須完整覆蓋所有樣式，不可修改 base rule

### 規則 3：scrollIntoView 禁用 (Scroll Containment)

- 播放中自動捲動**禁止**使用 `el.scrollIntoView()`（會連帶捲動所有祖先，包括頁面 body）
- 必須用 `getBoundingClientRect()` + 手動設定 `container.scrollTop` 實現區域內捲動
- Smart View 邏輯：
  - 播放中 → `chordDisplayOverview` 設 `overflow-y: auto` + `max-height`，區域內捲動
  - 暫停時 → 移除限制，頁面自由捲動

### 規則 4：display 屬性單一來源 (Single Source of Truth for display)

- `bigChordBox.style.display` 在 5+ 處被設定，互相覆蓋
- **原則**：每個 UI 元素的 `display` 必須有明確的優先順序：
  1. Tab 切換時設定初始狀態
  2. 資料載入完成時根據 tab 決定
  3. `update` 函式中根據 `_isFullscreen()` 條件決定
- **禁止**在 chord loading 的 error/success path 中硬編碼 `display: none`，除非確認所有 tab 都需要隱藏

### 規則 5：transform 不影響鄰居 (Transform Isolation)

- `transform: scale()` 的預設 `transform-origin` 是 `center`，會向所有方向擴展
- 若元素有與鄰居對齊的邊界（如 color bar），必須設定 `transform-origin` 固定該邊：
  - 頂部色條對齊 → `transform-origin: top center`
  - 底部鍵盤對齊 → `transform-origin: bottom center`

### 規則 6：Cache-Busting 版本號 (Version Bump)

- 每次修改 `player.js` 或 `style.css`，**必須**同時更新 `player.html` 中的 `?v=` 參數
- 否則瀏覽器會使用快取版本，導致「明明改了但沒效果」的幽靈 bug
- 建議：CSS 和 JS 使用相同版號，同步遞增

### 規則 7：變數宣告位置 (Variable Declaration Scope)

- `let` 變數有 **Temporal Dead Zone (TDZ)**：在宣告前使用會拋出 `ReferenceError`
- 所有共用 state 變數（`sectionData`, `chordData`, `hasChords` 等）**必須在檔案頂部 state 區塊統一宣告**
- **禁止**在函式之間穿插宣告 state 變數

### 規則 8：鍵盤容器高度 (Keys88 Ribbon Sizing)

- `.keys88-ribbon` 的 `flex: 0 0 Npx` 必須能容納：段落標記 (20px) + 色條 (4px) + 放大的 active chord badge (30px) + jianpu (15px) + padding
- 目前設定：`flex: 0 0 120px`，**不可低於 100px**
- Active chord 使用 `transform-origin: top center` 確保向下擴展、色條對齊

### 規則 9：W:\ 與 Git 雙向同步 (Dual Sync)

- 修改 W:\（生產）→ 立刻同步到 git repo
- 修改 git repo → 立刻同步到 W:\
- **兩邊必須是同一份檔案**，不可各自維護不同版本
- `cp` 整份檔案比 `Edit` 兩邊更安全

---

## 15. 版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
| 1.0 | 2025-03-25 | 初版：Phase 1-3 功能測試 |
| 2.0 | 2026-03-28 | 和弦引擎升級 BTC Transformer，新增分級測試框架 |
| 2.1 | 2026-04-03 | 新增第 11 節：AI 協作開發品管鐵律 (AI QA Protocol) |
| 3.0 | 2026-04-03 | 全面更新：補齊 AI/Benchmark/Auto Worker API 測試；更新 UI 測試與效能基準 |
| 3.1 | 2026-04-04 | 新增第 12 節：提案 AI 指法生成與驗證雙軌架構 (Generator-Evaluator Architecture) |
| 3.2 | 2026-04-04 | 新增第 13 節：邊緣運算與雙引擎批次處理架構 (Super Worker Edge Architecture) |
| 3.3 | 2026-04-04 | 修復沉浸模式 UI：toolbar grid 佈局、縮放捲動抖動、scrollbar 隱藏、zoom/close 按鈕重疊、select option 深色主題 |
| 3.4 | 2026-04-05 | 新增第 14 節：前端 UI 架構鐵律（9 條規則），源自 AI 鋼琴老師開發的 10+ 次 UI 迴歸修復經驗 |
| 3.5 | 2026-04-05 | 新增 K-09~K-11：瀑布流白/黑鍵色階、琴鍵高亮清晰度、底部發光效果測試項目 |
