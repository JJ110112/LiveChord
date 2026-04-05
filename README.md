# LiveChord

即時音樂和弦顯示網站 — 從 NAS 讀取 FLAC 音樂，播放時即時顯示和弦、簡譜與鍵盤指法。

## 功能

### 播放與顯示
- **FLAC 串流播放** — 支援 HTTP Range，PC 與平板瀏覽器皆可播放
- **即時和弦顯示** — 兩種視圖：Overview（全覽）與 Diagrams（滾動時間軸）
- **鋼琴 / 吉他 / 烏克麗麗** — 三種指法顯示模式
- **鋼琴鍵盤** — 2 個八度、根音優先排列、圓點標示按鍵（指法請參考 [AI鋼琴教師視覺符號說明書](AI-Piano-Teacher-Guide.md)）
- **簡譜** — 和弦以簡譜符號標示（1=C）
- **移調 + Capo** — 即時移調，Capo 設定（吉他/烏克麗麗模式）
- **播放速度** — 0.5x ~ 2x 可調，練習慢速播放
- **循環模式** — 單曲循環 / 最愛播放清單循環
- **和弦區全螢幕** — 專注練習模式，含迷你播放控制
- **頁面全螢幕** — 隱藏瀏覽器網址列（平板練習用）
- **和弦區縮放** — +/- 按鈕調整和弦顯示大小

### 和弦管理
- **MIDI 匯入** — Player 直接選檔上傳，或從 X:\ 自動匹配
- **AI 偵測** — BTC Transformer 自動偵測（fallback）
- **批次匯入** — Admin 頁面一鍵批次 MIDI 匯入
- **和弦編輯器** — 拖曳、縮放、新增、刪除和弦
- **來源保護** — Chordify > MIDI > BTC，高品質不被覆蓋

### 音樂庫
- **瀏覽** — 樹狀結構瀏覽 NAS 音樂庫
- **搜尋** — 即時搜尋歌名、演出者、專輯
- **最愛 / 最近播放** — 收藏歌曲，記錄播放歷史
- **LiveChord Core** — 背景自動掃描 + 自動和弦偵測

### 持久化
- 音量、播放速度、顯示模式、視圖（Overview/Diagrams）、縮放比例、循環模式自動記憶

## 系統需求

- **Python 3.9+**（已測試 3.11, 3.14）
- **瀏覽器**: Chrome / Edge / Firefox（Safari 不支援 FLAC）
- **NAS**: 掛載為 Windows 磁碟代號（預設 Y:\ = 音樂, X:\ = MIDI）

## 安裝

```bash
cd LiveChord/backend
pip install -r requirements.txt
```

## 部署

### 開發機（本機）
```bash
start.bat                    # NAS 模式（Y:\）
start_local.bat              # 本機資料夾模式
```

### NAS 部署（W:\）
將 backend/、frontend/、tools/、data/、start.bat 複製到 W:\，雙擊 start.bat 啟動。

### 平板使用
1. 連上同一區網
2. 瀏覽器開啟 `http://NUC_IP:8800`
3. 點頁面右上角 ⛶ 進入全螢幕

### 批次檔

| 檔案 | 用途 |
|------|------|
| `start.bat` | 啟動伺服器（透過 run.py） |
| `start_local.bat` | 啟動伺服器（本機資料夾模式） |
| `restart.bat` | 重啟伺服器 |
| `scan.bat` | 命令列掃描音樂庫 |
| `install-service.bat` | Windows 開機自動啟動 |
| `uninstall-service.bat` | 移除開機自動啟動 |

## 頁面

| 路徑 | 說明 |
|------|------|
| `/` | 首頁 — 瀏覽、搜尋、最愛、最近播放 |
| `/player?path=...` | 播放頁 — 即時和弦 + 播放控制 |
| `/editor?path=...` | 編輯頁 — 和弦時間軸編輯器 |
| `/admin` | 管理頁 — Core 狀態、和弦管理、MIDI 匯入 |

## 技術架構

```
PC / 平板 ──HTTP──▶ FastAPI (port 8800)
                      │
Backend               │  Frontend (vanilla JS)
├── run.py            │  ├── player.html/js    播放 + 即時和弦
├── main.py           │  ├── chord-render.js   鋼琴/吉他/烏克麗麗渲染
├── music_api.py      │  ├── api.js            API 呼叫封裝
├── chord_api.py      │  ├── app.js            首頁邏輯
├── user_api.py       │  ├── admin.html        管理頁
├── auto_worker.py    │  ├── editor.html/js    和弦編輯器
├── config.py         │  └── manifest.json     PWA 設定
├── chord_detect.py   │
├── chord_table.py    │  data/
├── chord_diagrams.py │  ├── chords/*.json     和弦資料（per song）
└── btc/              │  ├── library_cache.json 音樂庫索引
                      │  ├── settings.json     系統設定
tools/                │  ├── favorites.json    最愛
├── chordify_gui.py   │  └── recent.json       最近播放
├── midi_to_lab.py    │
└── chordify_ocr.py   │  路徑設定
                      │  Y:\ = 音樂根目錄
                      │  X:\ = MIDI 根目錄
                      │  W:\ = 部署目錄
```

## 和弦資料來源

| 來源 | 準確度 | 方式 |
|------|--------|------|
| Chordify | ~100% | tools/chordify_gui.py 擷取 |
| MIDI | ~92% | Player 上傳 / X:\ 自動匹配 / Admin 批次匯入 |
| BTC | ~41% | AI 音訊分析（fallback） |

## 授權

私人使用
