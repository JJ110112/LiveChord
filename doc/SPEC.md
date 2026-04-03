# LiveChord 規格書

> 版本: 2.0 | 日期: 2026-04-03

## 1. 產品概述

**LiveChord** 是一個私人用途的即時音樂和弦＋簡譜＋旋律顯示網站，從本地 NAS 讀取 FLAC 音樂檔，播放時即時顯示和弦與簡譜，並整合 AI 和弦偵測、旋律萃取、段落分析、重配和等智慧功能。

### 1.1 目標使用者
- 私人使用（單人）

### 1.2 伺服器
- ASUS Mini PC NUC 15 Pro+ (U9-285H / 32GB / 1TB / WIN11)
- 音樂 NAS 掛載（多磁碟支援，約 78,907 首 FLAC）
- MIDI 庫 (X:\)
- GPU: NVIDIA RTX 5080（AI 推理加速）

---

## 2. 系統架構

```
┌─────────────────────────────────────────────────────────┐
│  Browser (任意裝置)                                       │
│  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌─────┐ ┌─────┐  │
│  │ Dashboard │ │ 播放頁   │ │ 編輯頁 │ │Admin│ │Bench│  │
│  │ 搜尋/最愛 │ │ 和弦+旋律│ │ 時間軸 │ │ 管理│ │ 評測│  │
│  └──────────┘ └──────────┘ └────────┘ └─────┘ └─────┘  │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / REST
┌───────────────────────▼─────────────────────────────────┐
│  Python Backend (FastAPI)                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │Music API │ │Chord API │ │ User API │ │Benchmark  │  │
│  │(掃描/串流)│ │(CRUD/偵測)│ │(最愛/最近)│ │(評測系統) │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
│  ┌──────────────────────────────────────────────────┐   │
│  │ AI / ML 層                                        │   │
│  │ BTC Transformer │ HMM/Viterbi │ Markov │ Jazzify │   │
│  │ Chord2Vec │ Groove Dict │ Section Detect          │   │
│  │ Melody Extractor │ Pattern Extractor              │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Auto Worker (背景排程: 掃描 + 偵測)                │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │ JSON File Storage                                 │   │
│  │ data/library_cache.json  data/settings.json       │   │
│  │ data/favorites.json      data/recent.json         │   │
│  │ data/chords/{hash}.json  data/chord_index.json    │   │
│  │ data/models/             data/melodies/           │   │
│  └──────────────────────────────────────────────────┘   │
└───────────────────────┬─────────────────────────────────┘
                        │ File System
┌───────────────────────▼─────────────────────────────────┐
│  音樂庫 (多磁碟, 78,907+ FLAC)                           │
│  Genre / Artist / Album / Track.flac + cover.jpg         │
├─────────────────────────────────────────────────────────┤
│  X:\ MIDI 庫 (MIDI 和弦匯入來源)                         │
└─────────────────────────────────────────────────────────┘
```

---

## 3. NAS 音樂庫結構

```
音樂庫 (多磁碟支援, @0/@1/@2 虛擬根目錄):
├── Christmas/
├── Classics/
├── Electronic Dance Music/
├── Jam/
├── Jazz/
├── Other/          (Audiophile, Folk, Latin, Soundtracks...)
├── POP/            (C-POP, J-POP, K-POP, E-POP...)
├── Relax/
└── Sleep/

每個 Album 資料夾:
  Artist - Album Name/
  ├── Track1.flac
  ├── Track2.flac
  └── cover.jpg
```

---

## 4. 功能需求

### 4.1 Dashboard（首頁）

| ID | 功能 | 說明 |
|----|------|------|
| H-01 | 資料夾瀏覽 | 樹狀結構瀏覽多磁碟的 Genre → Artist → Album → Track |
| H-02 | 搜尋 | 即時搜尋歌名、演出者、專輯名稱（從快取索引） |
| H-03 | 最愛清單 | 橫向卡片顯示已收藏歌曲，拖拉慣性滾動 |
| H-04 | 專輯封面 | 顯示 cover.jpg 作為縮圖 |
| H-05 | 最近播放 | 橫向卡片顯示最近播放歌曲（最多 50 首），拖拉慣性滾動 |
| H-06 | 和弦譜狀態 | 標示該歌曲是否已有和弦譜 |
| H-07 | 難度星級 | 依和弦數量顯示 1-4 星難度 (★~★★★★) |
| H-08 | 和弦覆蓋率 | 顯示全庫和弦偵測覆蓋率統計 |
| H-09 | 拖拉慣性滾動 | 最近播放/最愛區塊支援滑鼠拖拉 + 動量滾動 |
| H-10 | 播放器內搜尋 | 播放頁 header 內建搜尋框，可直接切歌 |

### 4.2 播放頁

#### 基礎播放

| ID | 功能 | 說明 |
|----|------|------|
| P-01 | FLAC 串流播放 | 後端串流 FLAC → 前端 HTML5 Audio（HTTP Range 支援）|
| P-02 | 播放控制 | 播放/暫停、進度條拖拉、音量控制、上一首/下一首 |
| P-07 | 收藏 | 一鍵加入/移除最愛（愛心圖示切換）|
| P-10 | 專輯封面 | 播放頁顯示專輯封面 |
| P-11 | 歌曲資訊 | FLAC metadata（標題、演出者、專輯、時長）|
| P-23 | 循環模式 | 三模式切換：關閉 / 單曲循環 / 最愛循環 |
| P-24 | 播放速度 | 0.5x / 0.75x / 1x / 1.25x / 1.5x / 2x，點擊切換 |

#### 三分頁顯示模式

| ID | 功能 | 說明 |
|----|------|------|
| P-12 | 三分頁模式 | Overview / Diagrams / 88-Key Piano 分頁切換 |
| P-03 | Overview 和弦即時顯示 | 橫向和弦方塊時間軸，自動高亮當前和弦並捲動置中 |
| P-27 | Diagrams 時間軸 ribbon | 水平 ribbon，和弦按時間定位，中心線標示當前播放位置 |
| P-13 | 88 鍵鋼琴視覺化 | Canvas 渲染全 88 鍵（A0-C8），含八度標記、C4 中央 C 指示線 |
| P-25 | 縮放控制 | 50%~300% 共 13 段，每分頁獨立記憶，全螢幕另存 |
| P-26 | 全螢幕模式 | 瀏覽器全螢幕 + mini 播放控制列（播放/暫停/前後曲/速度/標題跑馬燈）|

#### 和弦與簡譜

| ID | 功能 | 說明 |
|----|------|------|
| P-04 | 簡譜顯示 | C-relative 簡譜（1-7），上標升降記號 |
| P-05 | 和弦圖 | 吉他 / 烏克麗麗 / 鋼琴鍵盤 三種顯示 |
| P-06 | 樂器模式切換 | Piano / Guitar / Ukulele（Overview & Diagrams 分頁）|
| P-08 | 移調 | Transpose ±11 半音，即時更新和弦與簡譜 |
| P-09 | Capo 設定 | Capo 0-7，僅吉他/烏克麗麗模式顯示 |
| P-22 | 和弦來源標記 | MIDI / BTC badge 標示和弦資料來源 |
| P-17 | 段落標記 | AI 自動偵測段落（前奏/主歌/導歌/副歌/橋段/尾奏/間奏），色彩標記 |

#### 88 鍵鋼琴模式

| ID | 功能 | 說明 |
|----|------|------|
| P-14 | 手部模式切換 | 雙手 / 左手（和弦）/ 右手（旋律）三模式 |
| P-15 | Voice Leading | 自動聲部導向，最小化和弦切換時音符距離 |
| P-16 | 旋律即時標示 | 綠色菱形標記當前旋律音 + 簡譜顯示 |
| — | Sustain 視覺化 | 前一和弦音符灰色漸出（~0.5s）|
| — | Ribbon 軌道 | 鍵盤上方水平和弦方塊列，含和弦名/簡譜/旋律簡譜，點擊跳轉 |

#### AI 互動功能

| ID | 功能 | 說明 |
|----|------|------|
| P-18 | Jazzify 重配和 | OFF → L1 → L2 → L3 四段切換，顯示變更數量 |
| P-19 | AI 和弦建議 | Markov chain 預測下一和弦，Toast 顯示 Top 5 候選 + 機率 |
| P-20 | 自動和弦偵測 | MIDI 搜尋 → BTC Transformer → Viterbi fallback 三階段 |
| P-21 | MIDI 搜尋匯入 | 自動比對 X:\ MIDI 庫檔名，key 校驗 + 不符時 fallback BTC |

### 4.3 和弦譜編輯

| ID | 功能 | 說明 |
|----|------|------|
| E-01 | 時間軸編輯器 | /editor 頁面，在時間軸上標記和弦時間點 |
| E-02 | 播放同步預覽 | 邊播邊標記，即時預覽效果 |
| E-03 | ChordPro 匯入 | 支援匯入 ChordPro 格式（未來擴充）|

### 4.4 AI 功能

| ID | 功能 | 說明 |
|----|------|------|
| AI-01 | BTC 和弦偵測 | Bi-directional Transformer, 170 大詞彙和弦，CQT 特徵，mode filter 後處理 |
| AI-02 | Viterbi fallback | BTC 失敗時自動啟動旋律萃取 + HMM Viterbi 解碼推導和弦 |
| AI-03 | 段落偵測 v3 | BPM 感知動態窗口、指紋比對、Jaccard 合併、ii-V 橋段偵測、A/B/C 結構 |
| AI-04 | 旋律萃取 | pYIN F0 + HPSS 人聲分離 + 高通濾波，輸出 MIDI note 序列 |
| AI-05 | Markov 和弦預測 | Bigram/Trigram，relative degree 表示，預測下一和弦 |
| AI-06 | Chord2Vec | 32-dim PPMI SVD 共現矩陣，純 numpy，語意相似度 |
| AI-07 | Groove Dictionary | 4/8 小節 pattern 頻率統計，duration 量化，Neo-Soul 偵測 |
| AI-08 | Jazzify 重配和 | L1 加延伸音 / L2 ii-V-I+9th / L3 tritone sub+secondary dominant+13th |
| AI-09 | Pattern 辨識 | 辨識 ii-V-I、turnaround、modal interchange 等音樂理論模式 |
| AI-10 | 批次 GPU 處理 | RTX 5080, ThreadPool+GPU semaphore, ~2-3 tracks/s, 黑名單曲風過濾 |
| AI-11 | 自動工作排程 | 背景增量掃描（30min 間隔）+ 自動偵測佇列（20 首/週期）|

### 4.5 Benchmark 評測系統

| ID | 功能 | 說明 |
|----|------|------|
| B-01 | 測試歌曲管理 | Lv1-Lv5 分級測試歌曲 |
| B-02 | Ground truth 管理 | 標準和弦標注存取與驗證 |
| B-03 | 偵測對比評分 | Root accuracy + Full accuracy + Key accuracy，時間重疊加權 |
| B-04 | 全域統計 | 跨所有等級的聚合評分 |

### 4.6 Admin 管理頁

| ID | 功能 | 說明 |
|----|------|------|
| A-01 | 音樂庫路徑設定 | 多磁碟路徑管理 |
| A-02 | 掃描控制 | 觸發全量/增量掃描，查看進度 |
| A-03 | 自動工作排程 | 啟動/停止/手動觸發，調整設定（間隔/每週期數/跳過曲風）|
| A-04 | 覆蓋率統計 | 全庫和弦偵測覆蓋率 + 批次狀態 |

### 4.7 鋼琴教學模式（規劃中）

#### 4.7.1 彈奏模式 (Play Styles) — 88 鍵模式下可切換

| ID | 模式 | 左手 | 右手 | 適合曲風 | 難度 |
|----|------|------|------|----------|------|
| PS-01 | 基礎理解型 (Chord+Melody) | 和弦 (紅) | 旋律 (綠) | 全曲風 | ★ |
| PS-02 | Block Chord | 根音/八度 | 旋律+和聲 | Pop/Ballad | ★★ |
| PS-03 | 分解伴奏 (Arpeggio) | 分解和弦動畫 (1-5-3-5) | 旋律 | 抒情/J-pop | ★★ |
| PS-04 | 節奏型 (Pop Rhythm) | 低音拍點 | 切分和弦+旋律 | Pop/Rock | ★★★ |
| PS-05 | 旋律強化 (Melody Emphasis) | 簡化和弦/根音 | 放大旋律+樂句線 | 練唱/教學 | ★ |
| PS-06 | 和弦音訓練 (Chord Tone) | 和弦 | 標示 chord tone (綠) vs passing tone (灰) | 即興基礎 | ★★★ |
| PS-07 | 雙手平衡 (Hand Balance) | 和弦 (弱 30%) | 旋律 (強 70%), 含音量條 | 全曲風 | ★★ |

#### 4.7.2 伴奏型態分類 (Accompaniment Patterns)

| 類型 | 左手 | 右手 | 代表曲風 |
|------|------|------|----------|
| Block | 根音 | 柱狀和弦 | Pop 基礎 |
| Arpeggio | 分解和弦 (1-5-3-5) | 旋律 | 抒情/動漫/J-pop |
| Alberti Bass | 低-高-中-高 | 旋律 | 古典 |
| Rhythm | 低音拍點 | 切分和弦 | Pop/Rock/Jazz Pop |
| Shell Voicing | 3rd + 7th | 延伸音 (9, 11, 13) | Jazz |
| Walking Bass | 每拍不同音（連續行走）| 和弦/即興 | Jazz/Bossa Nova |
| Stride | 低音↔中音跳躍 | 和弦 | Ragtime/Old Jazz |

#### 4.7.3 曲風適配建議 (Genre-Specific Recommendations)

程式根據 track metadata (genre, BPM, key) + 音樂內容分析（和弦密度、音型、低音行為）自動建議彈奏模式：

| 條件 | 建議模式 |
|------|----------|
| Pop/Ballad | Block, Arpeggio, Rhythm |
| Jazz | Shell Voicing, Walking Bass |
| Classical | Alberti, Polyphony |
| BPM < 80（慢歌）| Arpeggio, Melody Emphasis |
| BPM > 120（快歌）| Rhythm, Block |
| 和弦密度高（同時多音）| Block Chord |
| 分散音型 | Arpeggio |
| 低音逐拍變化 | Walking Bass |

#### 4.7.4 難度階梯 (Scaffolding)

| Level | 左手 | 右手 | 說明 |
|-------|------|------|------|
| L1 初階 | 根音 | 旋律 | 建立基本音感 |
| L2 中階 | Shell Voicing (3rd+7th) | 旋律 | 訓練和弦色彩感 |
| L3 進階 | 節奏律動 (Groove) | 旋律+二聲部和聲 | 完整演奏能力 |

#### 4.7.5 情境感知功能 (Context-Aware)

- **Voice Leading 導引線**：和弦切換時顯示手指移動路徑（哪些手指不動、哪些移半音）
- **Chord Tone 標記**：旋律音標示為 chord tone (綠) 或 passing tone (灰)
- **段落級模式切換**：Verse/Chorus 自動適配不同伴奏型態
- **節拍視覺化**：Beat Markers 顯示重拍/弱拍位置

---

## 5. API 設計

### 5.1 音樂庫 API

```
GET  /api/browse?path=             瀏覽目錄（多磁碟 @0/@1/@2 虛擬根）
GET  /api/search?q=                搜尋歌曲（標題/演出者/專輯/檔名）
GET  /api/track/info?path=         單曲 metadata（FLAC tag）
GET  /api/track/stream?path=       串流音訊（HTTP Range 206）
GET  /api/track/cover?path=        專輯封面（外部 jpg 或 FLAC 內嵌）
POST /api/library/scan             觸發掃描（全量或增量）
GET  /api/library/scan/status      掃描進度（計數/新增/更新/刪除）
GET  /api/library/stats            庫統計（總曲數、索引狀態、掃描時間）
GET  /api/settings                 取得設定（音樂根路徑清單）
POST /api/settings                 更新設定
```

### 5.2 和弦 API

```
GET  /api/chord/info/{name}        和弦資訊（組成音、簡譜）
GET  /api/chord/diagram/{inst}/{name}  和弦圖（guitar/ukulele）
GET  /api/chords?path=             取得某首歌的和弦譜
POST /api/chords?path=             儲存和弦譜
POST /api/chords/detect?path=      BTC 偵測單曲（含 Viterbi fallback）
GET  /api/chords/midi-search?path= 搜尋匹配 MIDI 檔
POST /api/chords/midi-import       MIDI 和弦匯入（含 key 校驗）
POST /api/chords/midi-upload       上傳 MIDI 檔匯入
POST /api/chords/batch-midi-import 批次 MIDI 自動匯入
POST /api/chords/batch-detect      啟動批次 BTC 偵測
GET  /api/chords/batch-detect/status  批次偵測進度
GET  /api/chords/tracks            所有曲目和弦狀態清單
GET  /api/chords/stats             覆蓋率統計
```

### 5.3 使用者資料 API

```
GET    /api/favorites              取得最愛清單（含和弦摘要）
POST   /api/favorites              新增最愛
DELETE /api/favorites?path=        移除最愛
GET    /api/recent                 最近播放（最多 50 筆）
POST   /api/recent                 新增最近播放（自動去重）
```

### 5.4 AI API

```
GET  /api/ai/suggest?chords=&key=&top_k=   Markov 預測下一和弦
GET  /api/ai/generate?key=&length=&seed=   生成和弦進行
GET  /api/ai/similar?chord=&top_k=         Chord2Vec 相似和弦
GET  /api/ai/groove?context=&top_k=        Groove Dictionary 常見 pattern
POST /api/ai/jazzify                       Jazzify 重配和（L1/L2/L3）
GET  /api/ai/melody?path=                  旋律萃取（pYIN → MIDI）
GET  /api/ai/emission?chord=               HMM emission matrix
POST /api/ai/viterbi                       Viterbi 解碼（旋律→和弦）
GET  /api/ai/sections?path=                段落偵測
GET  /api/ai/patterns?chords=&key=         音樂理論 pattern 辨識
POST /api/ai/retrain                       重新訓練所有 AI 模型
GET  /api/ai/stats                         模型統計
GET  /api/ai/evaluate                      模型評估（perplexity, accuracy）
```

### 5.5 Auto Worker API

```
GET  /api/auto/status              工作排程狀態
GET  /api/auto/log?limit=50        活動日誌
GET  /api/auto/settings            排程設定
POST /api/auto/settings            更新排程設定
POST /api/auto/start               啟動排程
POST /api/auto/stop                停止排程
POST /api/auto/trigger             手動觸發一次週期
```

### 5.6 Benchmark API

```
GET  /api/benchmark/songs                    測試歌曲清單（Lv1-Lv5）
GET  /api/benchmark/ground-truth/{lv}/{song} 載入 ground truth
POST /api/benchmark/ground-truth             儲存 ground truth
POST /api/benchmark/detect/{lv}/{song}       執行偵測
GET  /api/benchmark/detection/{lv}/{song}    取得偵測結果
GET  /api/benchmark/score/{lv}/{song}        單曲評分
GET  /api/benchmark/score-all                全域聚合評分
```

---

## 6. 資料格式

### 6.1 音樂庫快取 (data/library_cache.json)

```json
{
  "scan_time": "2026-04-01T10:00:00",
  "total_tracks": 78907,
  "tracks": [
    {
      "path": "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
      "title": "Beerbohm",
      "artist": "Bob James",
      "album": "Jazz Hands",
      "genre": "Jazz",
      "duration": 285.3,
      "has_chords": true,
      "mtime": 1711234567.0
    }
  ]
}
```

### 6.2 和弦譜 (data/chords/{song_hash}.json)

```json
{
  "path": "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
  "key": "Eb",
  "capo": 0,
  "source": "btc",
  "chords": [
    {"time": 0.0, "end": 4.5, "chord": "Eb"},
    {"time": 4.5, "end": 8.2, "chord": "Gm"},
    {"time": 8.2, "end": 12.0, "chord": "Cm7"}
  ]
}
```

> `source` 欄位：`"btc"` | `"midi"` | `"chordify"` | `"manual"`

### 6.3 最愛 (data/favorites.json)

```json
{
  "favorites": [
    {
      "path": "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
      "added_at": "2026-03-25T10:30:00"
    }
  ]
}
```

### 6.4 最近播放 (data/recent.json)

```json
{
  "recent": [
    {
      "path": "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
      "played_at": "2026-03-25T10:30:00"
    }
  ],
  "max_items": 50
}
```

### 6.5 和弦索引 (data/chord_index.json)

```json
{
  "a1b2c3d4e5f6": {
    "unique_chords": 8,
    "key": "C",
    "list": ["C", "Am", "F", "G", "Dm", "Em", "Bdim", "E7"]
  }
}
```

### 6.6 設定 (data/settings.json)

```json
{
  "music_roots": ["Y:\\Music", "Z:\\Backup"],
  "midi_root": "X:\\MIDI",
  "auto": {
    "scan_interval_min": 30,
    "max_per_cycle": 20,
    "skip_genres": ["Classical", "Sleep", "EDM"]
  }
}
```

### 6.7 旋律快取 (data/melodies/{hash}.json)

```json
[
  {"start": 1.2, "end": 2.5, "midi": 67},
  {"start": 2.5, "end": 3.1, "midi": 65}
]
```

---

## 7. 技術選型

| 層級 | 技術 | 說明 |
|------|------|------|
| 前端 | HTML5 + Vanilla JS + CSS | 輕量、無框架依賴 |
| 音訊播放 | HTML5 Audio (FLAC) | 現代瀏覽器原生支援 |
| Canvas 渲染 | HTML5 Canvas API | 88 鍵鋼琴視覺化、和弦圖 |
| 後端 | Python + FastAPI | 非同步、高效能 |
| 音訊串流 | FastAPI StreamingResponse | HTTP Range 支援 |
| FLAC metadata | mutagen | 讀取 FLAC tag |
| 和弦邏輯 | chord_table.py | 和弦 → 簡譜轉換 |
| 和弦圖 | chord_diagrams.py | 吉他/烏克麗麗指法 |
| AI 和弦偵測 | PyTorch + BTC Transformer | ISMIR 2019 預訓練模型, 170 和弦 |
| 音訊分析 | librosa | CQT、pYIN F0、HPSS |
| AI 模型 | numpy / scipy | Markov、Chord2Vec、HMM、SVD |
| QA 測試 | Playwright | 端對端前端自動化測試 |
| 資料儲存 | JSON 檔案 | 簡單、無需資料庫 |
| 部署 | 直接執行 / Windows Service | NUC 上 Windows 11 |

### 7.1 Python 依賴

```
fastapi>=0.115
uvicorn[standard]>=0.32
mutagen>=1.47
torch>=2.0
librosa>=0.10
numpy>=1.24
scipy>=1.10
```

---

## 8. 目錄結構

```
LiveChord/
├── doc/
│   ├── SPEC.md              # 本規格書
│   ├── QA.md                # 品管文件
│   └── SheetMusicChord/     # 參考用原始專案
├── backend/
│   ├── main.py              # FastAPI app 進入點 + 路由
│   ├── config.py            # 全域設定
│   ├── music_api.py         # 音樂庫 API（瀏覽/搜尋/串流）
│   ├── chord_api.py         # 和弦 API（CRUD/偵測/MIDI匯入）
│   ├── user_api.py          # 最愛/最近播放 API
│   ├── ai_api.py            # AI API（預測/Jazzify/段落/旋律）
│   ├── benchmark_api.py     # Benchmark 評測 API
│   ├── chord_detect.py      # BTC Transformer 和弦偵測
│   ├── chord_cache.py       # 和弦索引快取層
│   ├── chord_table.py       # 和弦 → 簡譜轉換
│   ├── chord_diagrams.py    # 吉他/烏克麗麗指法圖
│   ├── auto_worker.py       # 背景自動掃描+偵測排程
│   ├── batch_btc_worker.py  # GPU 批次偵測工作器
│   ├── train_models.py      # AI 模型訓練腳本
│   ├── run.py               # 啟動腳本
│   ├── ai/                  # AI 子模組
│   │   ├── markov.py        # Markov chain 和弦預測
│   │   ├── chord2vec.py     # Chord2Vec 相似度
│   │   ├── groove_dict.py   # Groove Dictionary
│   │   ├── hmm.py           # HMM emission/transition
│   │   ├── melody_extractor.py  # pYIN 旋律萃取
│   │   ├── section_detect.py    # 段落偵測 v3
│   │   ├── reharmonizer.py  # Jazzify 重配和
│   │   ├── jazz_rules.py    # Jazz 規則庫
│   │   ├── pattern_extractor.py # 音樂理論 pattern 辨識
│   │   ├── preprocess.py    # 資料前處理
│   │   └── evaluate.py      # 模型評估
│   └── requirements.txt
├── frontend/
│   ├── index.html           # Dashboard 首頁
│   ├── player.html          # 播放頁
│   ├── editor.html          # 和弦譜編輯頁
│   ├── admin.html           # 管理後台
│   ├── benchmark.html       # Benchmark 評測頁
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js           # 首頁邏輯
│       ├── player.js        # 播放器邏輯（含 88 鍵 Canvas）
│       ├── chord-render.js  # 和弦/簡譜/和弦圖渲染
│       ├── editor.js        # 編輯器邏輯
│       └── api.js           # API 呼叫封裝
├── data/
│   ├── library_cache.json   # 音樂庫快取
│   ├── chord_index.json     # 和弦索引
│   ├── settings.json        # 系統設定
│   ├── favorites.json       # 最愛
│   ├── recent.json          # 最近播放
│   ├── chords/              # 和弦譜 JSON 檔案
│   ├── melodies/            # 旋律快取
│   ├── models/              # AI 模型（markov.json 等）
│   └── test_songs/          # Benchmark 測試歌曲（Lv1-Lv5）
├── tests/                   # Playwright QA 測試
└── start.bat                # Windows 一鍵啟動
```

---

## 9. Phase 規劃

| Phase | 範圍 | 狀態 |
|-------|------|------|
| **1** | 基礎框架 + 音樂瀏覽 + 播放 + 靜態和弦顯示 | ✅ 已完成 |
| **2** | 即時和弦同步（時間軸 JSON + 播放器同步）| ✅ 已完成 |
| **3** | 移調 + Capo | ✅ 已完成 |
| **4** | AI 和弦偵測（BTC + Viterbi + MIDI 匯入）| ✅ 已完成 |
| **5** | 和弦譜時間軸編輯器 | 🔧 基礎完成 |
| **6** | Dashboard 重設計 + 搜尋 + 難度 + Admin | ✅ 已完成 |
| **7** | AI 進階（Jazzify, Chord2Vec, Section Detect, Pattern）| ✅ 已完成 |
| **8** | Benchmark 評測系統 + Playwright QA | ✅ 已完成 |
| **9** | 88 鍵鋼琴模式 + Voice Leading + 旋律標示 | ✅ 已完成 |
| **10** | 鋼琴教學模式（彈奏模式/伴奏型態/曲風適配/難度階梯）| 📋 規劃中 |

---

## 10. 非功能需求

| 項目 | 需求 |
|------|------|
| 回應時間 | 搜尋 < 200ms（快取索引後）|
| 串流延遲 | 音訊播放啟動 < 1 秒 |
| BTC 推理 | 單曲偵測 30s~1min（GPU）；批次 ~2-3 tracks/s |
| 並行存取 | 支援 1-3 人同時使用（私人用途）|
| 瀏覽器支援 | Chrome、Edge、Firefox（現代瀏覽器）|
| 中文介面 | 全繁體中文 UI |
| 背景排程 | Auto Worker 不阻塞 UI（低優先權執行緒）|
| GPU 隔離 | ProcessPoolExecutor subprocess 隔離 GIL/GPU 記憶體 |
| 資料安全 | JSON 檔案儲存，無外部資料庫依賴 |
