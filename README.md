# LiveChord

即時音樂和弦顯示網站 — 從 NAS 讀取 FLAC 音樂，播放時即時顯示和弦、簡譜與鍵盤指法。

## 功能

- **音樂庫瀏覽** — 樹狀結構瀏覽 NAS 中的 Genre / Artist / Album / Track
- **搜尋** — 即時搜尋歌名、演出者、專輯
- **FLAC 串流播放** — 支援 HTTP Range，瀏覽器原生播放
- **即時和弦顯示** — 播放時高亮當前和弦，支援 Overview 與 Diagrams 兩種視圖
- **鋼琴 / 吉他 / 烏克麗麗** — 三種顯示模式：鋼琴鍵盤指法、吉他和弦圖、烏克麗麗和弦圖
- **簡譜** — 和弦以簡譜符號標示（1=C）
- **移調 + Capo** — 即時移調，Capo 設定（吉他/烏克麗麗模式）
- **和弦來源** — Chordify 擷取（最佳）> MIDI 匯入（優良）> BTC AI 偵測（堪用）
- **MIDI 匯入** — 從 MIDI 檔案自動擷取和弦與時間軸，單首或批次匯入
- **和弦時間軸編輯器** — 拖曳、縮放、新增、刪除和弦
- **LiveChord Core** — 背景自動掃描音樂庫 + 自動和弦偵測
- **最愛 / 最近播放** — 收藏歌曲，記錄播放歷史
- **設定持久化** — 音量、顯示模式、視圖自動記憶

## 系統需求

- **Python 3.9+**
- **瀏覽器**: Chrome / Edge / Firefox（Safari 不支援 FLAC）
- **NAS**: 掛載為 Windows 磁碟代號（預設 Y:\ = 音樂, X:\ = MIDI）

## 安裝

```bash
cd LiveChord/backend
pip install -r requirements.txt
```

## 使用

```bash
# 啟動伺服器（NAS 模式，預設 Y:\）
start.bat

# 啟動伺服器（本機資料夾模式）
start_local.bat

# 開啟瀏覽器
# http://localhost:8800

# 管理頁面（自動掃描、批次和弦偵測、MIDI 匯入）
# http://localhost:8800/admin
```

### 批次檔

| 檔案 | 用途 |
|------|------|
| `start.bat` | 啟動伺服器（NAS 模式） |
| `start_local.bat` | 啟動伺服器（本機資料夾模式） |
| `restart.bat` | 重啟伺服器（自動關閉舊程序） |
| `scan.bat` | 命令列掃描音樂庫 |
| `install-service.bat` | 安裝為 Windows 開機自動啟動 |
| `uninstall-service.bat` | 移除開機自動啟動 |

## 頁面

| 路徑 | 說明 |
|------|------|
| `/` | 首頁 — 瀏覽、搜尋、最愛、最近播放 |
| `/player?path=...` | 播放頁 — 音訊播放 + 即時和弦顯示 |
| `/editor?path=...` | 編輯頁 — 和弦時間軸編輯器 |
| `/admin` | 管理頁 — Core 狀態、自動化設定、MIDI 管理、批次操作 |

## 技術架構

```
Browser ──HTTP──▶ FastAPI (Python, port 8800)
                    ├── music_api.py      音樂庫 API（瀏覽/搜尋/串流）
                    ├── chord_api.py      和弦 API（CRUD/偵測/MIDI 匯入）
                    ├── user_api.py       最愛 / 最近播放
                    ├── auto_worker.py    背景自動掃描 + 偵測
                    ├── config.py         路徑設定（Y:\ / X:\）
                    ├── chord_detect.py   BTC Transformer 和弦偵測
                    ├── chord_table.py    和弦→簡譜轉換
                    └── chord_diagrams.py 吉他/烏克麗麗指法圖

Frontend (vanilla JS)
  ├── player.js       播放頁邏輯 + 即時同步
  ├── chord-render.js 鋼琴鍵盤/和弦圖渲染
  ├── api.js          API 呼叫封裝
  └── app.js          首頁邏輯

tools/
  ├── chordify_gui.py   Chordify 擷取工具（V2 錄影+離線分析）
  ├── midi_to_lab.py    MIDI → 和弦時間軸轉換
  └── chordify_ocr.py   Chordify 截圖 OCR
```

## 和弦資料來源

| 來源 | 準確度 | 方式 |
|------|--------|------|
| Chordify | ~100% | tools/chordify_gui.py 擷取 |
| MIDI | ~92% | X:\ MIDI 檔案自動匯入 |
| BTC | ~41% | AI 音訊分析（fallback） |

## 授權

私人使用
