# LiveChord 規格書

> 版本: 1.0 | 日期: 2026-03-25

## 1. 產品概述

**LiveChord** 是一個私人用途的即時音樂和弦＋簡譜顯示網站，從本地 NAS（Z:\）讀取 FLAC 音樂檔，播放時即時顯示和弦與簡譜。

### 1.1 目標使用者
- 私人使用（單人）

### 1.2 伺服器
- ASUS Mini PC NUC 15 Pro+ (U9-285H / 32GB / 1TB / WIN11)
- 音樂 NAS 掛載於 Z:\（約 78,907 首 FLAC）

---

## 2. 系統架構

```
┌─────────────────────────────────────────────────┐
│  Browser (任意裝置)                               │
│  ┌──────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ 首頁     │  │ 播放頁       │  │ 編輯頁    │  │
│  │ 搜尋/最愛│  │ 音訊+和弦同步│  │ 和弦譜編輯│  │
│  └──────────┘  └──────────────┘  └───────────┘  │
└───────────────────┬─────────────────────────────┘
                    │ HTTP / WebSocket
┌───────────────────▼─────────────────────────────┐
│  Python Backend (FastAPI)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │Music API │ │Chord API │ │ SheetMusicChord  │ │
│  │(掃描/串流)│ │(CRUD)    │ │ 模組 (複用)      │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
│  ┌──────────────────────────────────────────┐   │
│  │ JSON File Storage                         │   │
│  │ data/favorites.json                       │   │
│  │ data/chords/{song_hash}.json              │   │
│  │ data/library_cache.json                   │   │
│  └──────────────────────────────────────────┘   │
└───────────────────┬─────────────────────────────┘
                    │ File System
┌───────────────────▼─────────────────────────────┐
│  Z:\ NAS (78,907 FLAC tracks)                   │
│  Genre / Artist / Album / Track.flac + cover.jpg │
└─────────────────────────────────────────────────┘
```

---

## 3. NAS 音樂庫結構

```
Z:\
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

### 4.1 首頁（音樂庫瀏覽）

| ID | 功能 | 說明 |
|----|------|------|
| H-01 | 資料夾瀏覽 | 樹狀結構瀏覽 Z:\ 的 Genre → Artist → Album → Track |
| H-02 | 搜尋 | 即時搜尋歌名、演出者、專輯名稱（從快取索引） |
| H-03 | 最愛清單 | 顯示已收藏歌曲，可快速播放 |
| H-04 | 專輯封面 | 顯示 cover.jpg 作為縮圖 |
| H-05 | 最近播放 | 記錄最近播放的歌曲（最多 50 首） |
| H-06 | 和弦譜狀態 | 標示該歌曲是否已有和弦譜 |

### 4.2 播放頁

| ID | 功能 | 說明 |
|----|------|------|
| P-01 | FLAC 串流播放 | 後端串流 FLAC → 前端 HTML5 Audio 播放 |
| P-02 | 播放控制 | 播放/暫停、進度條、音量、上一首/下一首 |
| P-03 | 和弦即時顯示 | 依播放時間高亮當前和弦 |
| P-04 | 簡譜顯示 | 和弦下方顯示簡譜組成音（複用 chord_table.py） |
| P-05 | 吉他和弦圖 | 切換顯示吉他/烏克麗麗和弦圖（複用 chord_diagrams.py） |
| P-06 | 模式切換 | 簡譜 / 吉他 / 烏克麗麗 三模式 |
| P-07 | 收藏 | 一鍵加入/移除最愛 |
| P-08 | 移調 | Transpose 升降半音，即時更新和弦與簡譜 |
| P-09 | Capo 設定 | 設定 Capo 位置，自動調整和弦 |
| P-10 | 專輯封面 | 播放頁顯示專輯封面 |
| P-11 | 歌曲資訊 | 讀取 FLAC metadata（標題、演出者、專輯、時長） |

### 4.3 和弦譜編輯（Phase 5，規格先列出）

| ID | 功能 | 說明 |
|----|------|------|
| E-01 | 時間軸編輯器 | 在時間軸上標記和弦出現的時間點 |
| E-02 | 播放同步預覽 | 邊播邊標記，即時預覽效果 |
| E-03 | ChordPro 匯入 | 支援匯入 ChordPro 格式檔案 |
| E-04 | 自動偵測 | 呼叫音訊和弦辨識（未來擴充） |

---

## 5. API 設計

### 5.1 音樂庫 API

```
GET  /api/browse?path=             瀏覽目錄（回傳子目錄與檔案清單）
GET  /api/search?q=                搜尋歌曲（從快取索引搜尋）
GET  /api/track/info?path=         取得單曲 metadata（FLAC tag）
GET  /api/track/stream?path=       串流音訊（HTTP Range 支援）
GET  /api/track/cover?path=        取得專輯封面
GET  /api/library/scan             觸發全庫掃描建立索引
GET  /api/library/stats            取得庫統計資訊
```

### 5.2 和弦 API

```
GET  /api/chord/info/{name}        和弦資訊（組成音、簡譜）
GET  /api/chord/diagram/{inst}/{name}  和弦圖（吉他/烏克麗麗）
GET  /api/chords?path=             取得某首歌的和弦譜
POST /api/chords?path=             儲存和弦譜
```

### 5.3 使用者資料 API

```
GET  /api/favorites                取得最愛清單
POST /api/favorites                新增最愛
DELETE /api/favorites?path=        移除最愛
GET  /api/recent                   取得最近播放
```

---

## 6. 資料格式

### 6.1 音樂庫快取 (data/library_cache.json)

```json
{
  "scan_time": "2026-03-25T10:00:00",
  "total_tracks": 78907,
  "tracks": [
    {
      "path": "Z:/Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
      "title": "Beerbohm",
      "artist": "Bob James",
      "album": "Jazz Hands",
      "genre": "Jazz",
      "duration": 285.3,
      "has_chords": false
    }
  ]
}
```

### 6.2 和弦譜 (data/chords/{song_hash}.json)

```json
{
  "path": "Z:/Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
  "key": "Eb",
  "capo": 0,
  "chords": [
    {"time": 0.0, "end": 4.5, "chord": "Eb"},
    {"time": 4.5, "end": 8.2, "chord": "Gm"},
    {"time": 8.2, "end": 12.0, "chord": "Cm7"}
  ]
}
```

### 6.3 最愛 (data/favorites.json)

```json
{
  "favorites": [
    {
      "path": "Z:/Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
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
      "path": "Z:/Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac",
      "played_at": "2026-03-25T10:30:00"
    }
  ],
  "max_items": 50
}
```

---

## 7. 技術選型

| 層級 | 技術 | 說明 |
|------|------|------|
| 前端 | HTML5 + Vanilla JS + CSS | 輕量、無框架依賴 |
| 音訊播放 | HTML5 Audio (FLAC) | 現代瀏覽器原生支援 |
| 後端 | Python + FastAPI | 非同步、高效能 |
| 音訊串流 | FastAPI StreamingResponse | HTTP Range 支援 |
| FLAC metadata | mutagen | 讀取 FLAC tag |
| 和弦邏輯 | chord_table.py (複用) | 和弦 → 簡譜轉換 |
| 和弦圖 | chord_diagrams.py (複用) | 吉他/烏克麗麗指法 |
| 資料儲存 | JSON 檔案 | 簡單、無需資料庫 |
| 部署 | 直接執行 / Docker (optional) | NUC 上 Windows 11 |

### 7.1 Python 依賴

```
fastapi>=0.115
uvicorn[standard]>=0.32
mutagen>=1.47
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
│   ├── main.py              # FastAPI app 進入點
│   ├── music_api.py         # 音樂庫 API
│   ├── chord_api.py         # 和弦 API
│   ├── user_api.py          # 最愛/最近播放 API
│   ├── chord_table.py       # 複用（從 SheetMusicChord）
│   ├── chord_diagrams.py    # 複用（從 SheetMusicChord）
│   └── requirements.txt
├── frontend/
│   ├── index.html           # 首頁
│   ├── player.html          # 播放頁
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js           # 首頁邏輯
│       ├── player.js        # 播放器邏輯
│       ├── chord-render.js  # 和弦/簡譜渲染
│       └── api.js           # API 呼叫封裝
├── data/
│   ├── favorites.json
│   ├── recent.json
│   ├── library_cache.json
│   └── chords/              # 和弦譜檔案
└── start.bat                # Windows 一鍵啟動
```

---

## 9. Phase 規劃

| Phase | 範圍 | 功能 ID |
|-------|------|---------|
| **1** | 基礎框架 + 音樂瀏覽 + 播放 + 靜態和弦顯示 | H-01~H-06, P-01~P-02, P-07, P-10, P-11 |
| **2** | 即時和弦同步（時間軸 JSON + 播放器同步） | P-03~P-06 |
| **3** | 移調 + Capo | P-08, P-09 |
| **4** | 音訊和弦自動偵測 | E-04 |
| **5** | 和弦譜時間軸編輯器 | E-01~E-03 |
| **6** | 部署優化（Docker、HTTPS） | - |

---

## 10. 非功能需求

| 項目 | 需求 |
|------|------|
| 回應時間 | 搜尋 < 200ms（快取索引後） |
| 串流延遲 | 音訊播放啟動 < 1 秒 |
| 並行存取 | 支援 1-3 人同時使用（私人用途） |
| 瀏覽器支援 | Chrome、Edge、Firefox（現代瀏覽器） |
| 中文介面 | 全繁體中文 UI |
