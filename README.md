# LiveChord

即時音樂和弦＋簡譜顯示網站 — 從本地 NAS 讀取 FLAC 音樂，播放時即時顯示和弦與簡譜。

## 功能

- **音樂庫瀏覽** — 樹狀結構瀏覽 NAS 中的 Genre / Artist / Album / Track
- **搜尋** — 即時搜尋歌名、演出者、專輯（需先掃描建立索引）
- **FLAC 串流播放** — 支援 HTTP Range，瀏覽器原生播放
- **即時和弦顯示** — 播放時高亮當前和弦，大字顯示正在演奏的和弦
- **簡譜 / 吉他 / 烏克麗麗** — 三種顯示模式切換，170+ 吉他和弦圖、100+ 烏克麗麗和弦圖
- **移調 + Capo** — 即時移調，Capo 設定自動調整和弦
- **AI 和弦偵測** — 基於 librosa chroma 分析，自動偵測音訊中的和弦與調性
- **自動播放偵測** — 播放無和弦譜的歌曲時，自動觸發偵測
- **和弦時間軸編輯器** — 拖曳、縮放、新增、刪除和弦，支援 ChordPro 匯入
- **LiveChord Core** — 類似 Roon Core 的背景自動掃描與和弦分析
- **最愛 / 最近播放** — 收藏歌曲，記錄播放歷史
- **Windows 開機自動啟動** — 安裝為 Startup 服務

## 系統需求

- **Python 3.9+**
- **瀏覽器**: Chrome / Edge / Firefox（Safari 不支援 FLAC）
- **NAS**: 掛載為 Windows 磁碟代號（預設 Z:\）

## 安裝

```bash
cd LiveChord/backend
pip install -r requirements.txt
```

## 使用

```bash
# 啟動伺服器
start.bat

# 開啟瀏覽器
# http://localhost:8800

# 掃描音樂庫（另開 CMD 視窗）
scan.bat

# 管理頁面（自動掃描、批次和弦偵測）
# http://localhost:8800/admin
```

### 批次檔

| 檔案 | 用途 |
|------|------|
| `start.bat` | 啟動伺服器 |
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
| `/admin` | 管理頁 — Core 狀態、自動化設定、批次操作 |

## 技術架構

```
Browser ──HTTP──▶ FastAPI (Python)
                    ├── music_api.py    音樂庫 API
                    ├── chord_api.py    和弦 API + 批次偵測
                    ├── user_api.py     最愛 / 最近播放
                    ├── chord_detect.py AI 和弦偵測 (librosa)
                    ├── chord_table.py  和弦→簡譜轉換
                    ├── chord_diagrams.py 吉他/烏克麗麗指法
                    └── auto_worker.py  背景自動掃描+偵測
                           │
                    data/  │  JSON 檔案儲存
                    Z:\    │  NAS 音樂庫 (FLAC)
```

## 授權

私人使用
