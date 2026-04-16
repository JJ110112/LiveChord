# LiveChord 產品化規劃書

> 版本: 1.0 | 日期: 2026-04-16
> 目標: Beta tester 能夠進行測試並回饋問題

---

## 1. 現狀盤點

| 項目 | 現況 |
|------|------|
| 和弦資料庫 | ~54,000 首（BTC + MIDI），598 MB JSON |
| 音樂庫 | ~78,907 首 FLAC（私人 NAS） |
| 後端 | FastAPI, 9 個 router, 50+ API endpoints |
| 前端 | Vanilla JS, 7 個頁面（dashboard/player/editor/admin/...） |
| 帳號系統 | SQLite + invite code + token auth, admin 角色 |
| AI 功能 | BTC 和弦偵測, Markov 預測, Jazzify, Chord2Vec, 旋律萃取, 段落分析 |
| 部署 | NUC (Win11, RTX 5080), 單機 LAN 使用 |

### 1.1 產品化缺口

目前系統為**單人私有架構**，要讓 beta tester 使用，需解決：

1. **音樂來源** — tester 沒有你的 NAS，無法播放 FLAC
2. **和弦匹配** — tester 的歌可能不在 54k 資料庫裡
3. **新歌處理** — 需要一個流程讓 tester 產生新歌的和弦
4. **網路存取** — 目前只有 LAN，beta 需要外網或 VPN
5. **回饋機制** — tester 需要一個方式回報問題與評價準確度

---

## 2. 架構決策：音訊在哪裡處理？

### 2.1 版權分析

| 方案 | 版權風險 | 說明 |
|------|---------|------|
| Server 儲存/串流使用者音樂 | **高** | 構成重製，即使刪除也有爭議 |
| Server 經 yt-dlp 串流 → 擷取 → 刪除 | **中** | 灰色地帶，私人 beta 風險可控 |
| Client 本地處理，只上傳和弦 JSON | **無** | 和弦進行不受著作權保護 |
| Server 只提供和弦資料庫查詢 | **無** | 純文字資料，無音訊 |

### 2.2 決策

採用**三層混合架構**，按階段漸進：

```
Layer 1: Chord Database (查詢既有和弦)     ← Phase 1 (Beta MVP)
Layer 2: Server-side Processing (新歌擷取)  ← Phase 2 (Beta Extended)
Layer 3: Local Client App (本地擷取)        ← Phase 3 (Production)
```

---

## 3. Phase 1 — Chord Database Web Service（Beta MVP）

> 目標：tester 可以搜尋 54k 歌曲庫、瀏覽和弦譜、給予評價回饋
> 預估工期：2 週

### 3.1 功能範圍

| 功能 | 說明 | 狀態 |
|------|------|------|
| 註冊/登入 | 現有 invite code 機制 | ✅ 已有 |
| 歌曲搜尋 | 依歌名/歌手/專輯搜尋 chord index | ✅ 已有 |
| 和弦譜瀏覽 | 顯示 chord chart + beat timing + key | ✅ 已有 |
| 樂器檢視 | 鋼琴/吉他/烏克麗麗指法圖 | ✅ 已有 |
| 移調/Capo | 即時移調 | ✅ 已有 |
| AI 功能 | Jazzify, 和弦預測, 段落分析 | ✅ 已有 |
| **Tester 音訊播放** | tester 自備 MP3/FLAC，瀏覽器本地播放 | ✅ 已完成 |
| **和弦準確度評價** | 按歌評分 + 留言 | ✅ 已完成 |
| **問題回報** | Bug report / feature request 表單 | ✅ 已完成 |
| **使用統計** | 追蹤 tester 的使用行為（匿名） | ✅ 已完成 |

### 3.2 需新增的功能

#### 3.2.1 本地音檔播放（Client-side Audio）

Tester 無法存取 NAS，改為在瀏覽器本地載入音檔：

```
Player 頁面新增:
  [📂 選擇本地音檔] 按鈕
      ↓
  <input type="file" accept="audio/*">
      ↓
  URL.createObjectURL(file) → HTML5 Audio 播放
      ↓
  和弦同步顯示（使用 chord JSON 的 time 欄位）
```

- 音檔**不上傳 server**，全程在瀏覽器播放
- 需要 tester 自行將歌名與資料庫比對（或輸入 hash）
- 進階：根據檔名自動模糊搜尋匹配

#### 3.2.2 和弦評價系統

```
API:
  POST /api/feedback/rating    — 歌曲評分 (1-5 星)
  POST /api/feedback/comment   — 文字留言
  GET  /api/feedback/list      — Admin 檢視所有回饋

Storage:
  data/feedback.db (SQLite)
  - song_hash TEXT
  - username TEXT
  - rating INTEGER (1-5)
  - comment TEXT
  - timestamp TEXT
  - page TEXT (player/editor/general)
```

#### 3.2.3 問題回報

```
API:
  POST /api/feedback/bug       — Bug report
  GET  /api/feedback/bugs      — Admin 檢視

Fields:
  - category: "accuracy" | "ui" | "performance" | "feature_request" | "other"
  - description: TEXT
  - screenshot: BLOB (optional, base64)
  - browser_info: TEXT (auto-captured)
  - page_url: TEXT
```

#### 3.2.4 使用統計（匿名）

```
API:
  POST /api/analytics/event    — 記錄事件

Events:
  - page_view (page, duration)
  - song_play (song_hash, play_duration, completed)
  - feature_use (feature_name: jazzify/transpose/capo/...)
  - search (query, results_count)

Storage:
  data/analytics.db (SQLite)
```

### 3.3 外網存取方案

| 方案 | 優點 | 缺點 | 建議 |
|------|------|------|------|
| Cloudflare Tunnel | 免費、HTTPS、零設定 NAT | 需安裝 cloudflared | ✅ **推薦** |
| Tailscale | P2P VPN、低延遲 | Tester 需安裝 client | 備選 |
| Port forwarding | 最簡單 | 安全風險、無 HTTPS | ❌ 不建議 |
| 租 VPS 反向代理 | 穩定 | 月費、需維護 | 可考慮 |

**推薦方案：Cloudflare Tunnel**

```bash
# NUC 上安裝 cloudflared
cloudflared tunnel create livechord
cloudflared tunnel route dns livechord livechord.yourdomain.com

# config.yml
tunnel: <tunnel-id>
credentials-file: ~/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: livechord.yourdomain.com
    service: http://localhost:8800
  - service: http_status:404
```

### 3.4 安全強化（面向外網）

Phase 1 上外網前必須完成：

- [x] **Rate limiting** — 登入/註冊 API 加 rate limit（防暴力破解）— 10 req/5min per IP
- [x] **密碼強度** — 要求最少 8 字元
- [ ] **HTTPS only** — Cloudflare Tunnel 自動提供
- [x] **Invite code 管理** — Admin 可生成/撤銷多組 invite code
- [x] **Token 過期** — 加入 token expiry（30 天）
- [x] **Input sanitization** — 所有用戶輸入做 `html.escape()` XSS 過濾
- [x] **Admin 頁面 IP 限制** — beta mode 下只允許 LAN IP 存取 /admin

---

## 4. Phase 2 — Server-side Processing（Beta Extended）✅ 已實作

> 目標：tester 可以處理資料庫沒有的新歌
> 實作日期：2026-04-16
> 狀態：核心功能已完成，YouTube 需確認 yt-dlp PATH

### 4.1 新歌處理流程

```
Tester 操作流程:

  方式 A: 上傳音檔
  ┌──────────────────────────────────────────────┐
  │ [📤 上傳音檔] → Server 接收                    │
  │       ↓                                       │
  │ BTC 和弦擷取 (GPU, ~30s/首)                    │
  │       ↓                                       │
  │ 儲存 chord JSON → 刪除音檔                     │
  │       ↓                                       │
  │ 回傳和弦資料 → Player 顯示                     │
  └──────────────────────────────────────────────┘

  方式 B: YouTube URL
  ┌──────────────────────────────────────────────┐
  │ [🔗 貼上 YouTube URL]                          │
  │       ↓                                       │
  │ yt-dlp 擷取音訊串流 → 暫存 /tmp               │
  │       ↓                                       │
  │ BTC 和弦擷取 (GPU, ~30s/首)                    │
  │       ↓                                       │
  │ 儲存 chord JSON → 刪除暫存音訊                 │
  │       ↓                                       │
  │ 回傳和弦資料 → Player 顯示                     │
  └──────────────────────────────────────────────┘
```

### 4.2 新增 API

```
POST /api/process/upload
  - multipart/form-data: audio file (max 50MB)
  - Response: { job_id, status: "queued" }

POST /api/process/youtube
  - Body: { url: "https://youtube.com/watch?v=..." }
  - Response: { job_id, status: "queued" }

GET /api/process/status/{job_id}
  - Response: { status: "queued"|"processing"|"done"|"error", progress: 0-100 }

GET /api/process/result/{job_id}
  - Response: chord JSON (same format as existing)
```

### 4.3 處理隊列

```python
# 新增 process_queue.py
# - 使用 asyncio.Queue 管理任務
# - 同時只跑 1 個 BTC job（GPU 資源限制）
# - 每個 job 有 5 分鐘 timeout
# - 音檔處理完畢後立即刪除
# - 每個 tester 每日限額 10 首（防濫用）
```

### 4.4 隱私與法律

- TOS 頁面（首次登入時顯示，需同意）：
  - 「上傳之音檔僅用於和弦分析，處理完畢後立即刪除」
  - 「本服務僅供個人音樂學習用途」
  - 「產生之和弦資料可能用於改善服務品質」
- 上傳音檔保留時間：**最多 10 分鐘**，處理完立即刪除
- 保留 audit log：who uploaded what (hash only), when, result

---

## 5. Phase 3 — Desktop App（Production）

> 目標：正式版，本地擷取，徹底解決版權問題
> 預估工期：6-8 週

### 5.1 技術選型

| 框架 | 優點 | 缺點 | 結論 |
|------|------|------|------|
| **Tauri** | 輕量 (~5MB)、Rust 安全、可 spawn Python | 生態較新 | ✅ **推薦** |
| Electron | 成熟生態、大量範例 | 肥大 (~150MB)、記憶體重 | 備選 |
| PyInstaller | 純 Python、最簡單 | 無原生 UI、打包大 | ❌ |
| Flutter | 跨平台含 mobile | 需學 Dart、Python 整合差 | ❌ |

### 5.2 Tauri App 架構

```
LiveChord Desktop
├── tauri-app/
│   ├── src-tauri/          # Rust backend (Tauri)
│   │   ├── src/main.rs     # App entry, spawn Python sidecar
│   │   └── sidecar/        # Bundled Python + BTC model
│   └── src/                # Frontend (現有 HTML/JS/CSS)
│       ├── index.html
│       ├── player.html
│       └── js/, css/
├── backend/                # Python backend (as sidecar)
│   ├── main.py             # FastAPI, listen localhost:8800
│   └── ...
└── models/                 # AI models (BTC, DEMUCS, etc.)
    └── btc_model.pt        # ~200MB, 首次啟動下載
```

### 5.3 功能差異（Web vs Desktop）

| 功能 | Web (Phase 1-2) | Desktop (Phase 3) |
|------|-----------------|-------------------|
| 和弦資料庫查詢 | ✅ | ✅ (連線 server) |
| 本地音檔播放 | ✅ (browser file) | ✅ (原生檔案存取) |
| 和弦擷取 | Server-side | **本地 GPU/CPU** |
| 旋律萃取 | Server-side | **本地** |
| AI 功能 | Server-side | **本地 + Server** |
| 離線使用 | ❌ | ✅ |
| 安裝門檻 | 零 | 需安裝 (~300MB) |

### 5.4 社群和弦回傳

Desktop 使用者本地擷取的和弦，可選擇回傳 server 壯大資料庫：

```
本地擷取和弦 → 彈出對話框:
  "是否將此和弦資料分享到 LiveChord 社群資料庫？"
  [分享] [不分享] [永遠不再問]

分享內容 (不含音訊):
  - 歌名 / 歌手 / 專輯 (metadata)
  - chord JSON (time, chord, confidence)
  - audio fingerprint (Chromaprint hash, 用於比對)
```

---

## 6. Beta Test 計畫

### 6.1 招募

| 項目 | 內容 |
|------|------|
| 人數 | 5-10 人（第一批） |
| 對象 | 音樂人、鋼琴/吉他老師、編曲者 |
| 管道 | 個人邀請、音樂社群推薦 |
| 條件 | 有基本和弦知識、願意花時間回饋 |

### 6.2 Invite Code 管理

升級現有 invite code 機制：

```sql
-- 新增 invite_codes 表
CREATE TABLE invite_codes (
    code TEXT PRIMARY KEY,
    created_by TEXT,          -- admin username
    created_at TEXT,
    used_by TEXT DEFAULT NULL,
    used_at TEXT DEFAULT NULL,
    expires_at TEXT,          -- 過期時間
    max_uses INTEGER DEFAULT 1,
    use_count INTEGER DEFAULT 0
);
```

Admin 可以：
- 生成一次性 invite code
- 設定過期時間
- 查看使用狀態
- 撤銷未使用的 code

### 6.3 回饋收集流程

```
Tester 日常使用
    ↓
每首歌播放完 → 彈出評價 (可跳過)
    ↓
遇到問題 → 右下角 [🐛 回報] 按鈕
    ↓
每週 → 自動發 email 摘要給 admin
    ↓
Admin 在 /admin 頁面查看:
  - 回饋總覽 dashboard
  - 按嚴重度排序的 bug list
  - 和弦準確度統計（哪些歌/類型最差）
```

### 6.4 Beta 階段 KPI

| 指標 | 目標 | 量測方式 |
|------|------|---------|
| Tester 留存率 | >60% 每週活躍 | analytics 事件 |
| 平均和弦評分 | ≥3.5 / 5.0 | feedback rating |
| Bug report 回應時間 | <48 小時 | feedback timestamp |
| 新歌處理成功率 | >95% | process job status |
| 頁面載入時間 | <3 秒 | analytics timing |

---

## 7. 部署架構演進

### 7.1 Phase 1 — 現有 NUC + Cloudflare Tunnel

```
Internet
    ↓ HTTPS
Cloudflare Tunnel (livechord.yourdomain.com)
    ↓
NUC (192.168.50.6:8800)
├── FastAPI backend
├── data/chords/ (54k JSON)
├── data/users.db
└── data/feedback.db (新增)

注意: 音樂 FLAC 不經過外網，tester 自備音檔在瀏覽器播放
```

### 7.2 Phase 2 — NUC + GPU Processing

```
Internet
    ↓ HTTPS
Cloudflare Tunnel
    ↓
NUC (RTX 5080)
├── FastAPI backend
├── Process Queue (新增)
│   ├── Upload handler → /tmp/
│   ├── yt-dlp worker → /tmp/
│   └── BTC detector (GPU)
├── data/chords/
└── Cleanup cron (刪除 /tmp 暫存)
```

### 7.3 Phase 3 — Hybrid (Server + Desktop)

```
                    Internet
                       ↓
              LiveChord Cloud (輕量)
              ├── Chord DB API
              ├── User accounts
              ├── Feedback system
              └── Community chord pool
                  ↑           ↑
           ┌──────┘           └──────┐
      Desktop App A              Desktop App B
      (Tauri + Python)           (Tauri + Python)
      ├── 本地 BTC 擷取          ├── 本地 BTC 擷取
      ├── 本地音檔播放           ├── 本地音檔播放
      └── 和弦回傳 ↑            └── 和弦回傳 ↑
```

---

## 8. 實作優先順序

### Phase 1 — Sprint 計畫

```
Week 1:
  ├── Day 1-2: 本地音檔播放功能 (browser file input + audio sync)
  ├── Day 3:   Feedback API + DB (rating, comment, bug report)
  ├── Day 4:   Feedback UI (player 頁面 + 回報按鈕)
  └── Day 5:   使用統計 (analytics events)

Week 2:
  ├── Day 1:   安全強化 (rate limit, password policy, token expiry)
  ├── Day 2:   Invite code 管理 (multi-code, expiry)
  ├── Day 3:   Cloudflare Tunnel 設定 + HTTPS 驗證
  ├── Day 4:   Admin feedback dashboard
  └── Day 5:   E2E 測試 + 修 bug + 準備 beta 文件
```

### Phase 2 — Sprint 計畫 ✅ 已完成

```
2026-04-16 實作完成:
  ├── ✅ Upload API + 處理隊列 (process_queue.py + process_api.py)
  ├── ✅ yt-dlp 整合 (YouTube URL → chord) — 需確認 NUC PATH
  ├── ✅ Job status polling UI (frontend/process.html)
  ├── ✅ TOS 頁面 + 同意機制 (frontend/tos.html + auth_api)
  ├── ✅ 每日額度限制 (10 首/天, in-memory)
  ├── ✅ 音檔自動清理 (即時刪除 + 5分鐘掃描) + audit log (data/audit.db)
  ├── ✅ Player ?hash= mode (分析結果直接進播放器)
  ├── ✅ XSS sanitization + admin IP restriction + beta mode gating
  ├── ✅ IndexedDB 上傳即播放 (blob 跨頁傳遞, auto-play)
  ├── ✅ YouTube IFrame 嵌入同步 (currentTime 驅動和弦)
  ├── ✅ YouTube 自動搜尋 (資料庫歌曲 → yt-dlp ytsearch → 嵌入)
  ├── ✅ Beta 首頁重設計 (大上傳區 + YouTube + 分析記錄)
  ├── ✅ NAS 隱私保護 (非 admin 看不到 NAS 路徑, browse 403)
  ├── ✅ 封面圖片 (上傳: mutagen 擷取, YouTube: 縮圖)
  ├── ✅ 200MB 上傳限制 (FLAC 友善)
  └── ✅ 分析記錄持久化 (audit DB + my-history endpoint)

待完成:
  ├── 壓力測試（多人同時上傳）
  ├── 錯誤處理強化
  └── Beta tester 回饋修正
```

---

## 9. 風險與緩解

| 風險 | 影響 | 緩解措施 |
|------|------|---------|
| NUC 當機 / 斷電 | 服務中斷 | UPS + auto-restart service |
| GPU 記憶體不足 | 和弦擷取失敗 | 限制同時 job 數 = 1 |
| yt-dlp 被 YouTube 封鎖 | URL 擷取失效 | 改用上傳音檔方式 |
| Tester 上傳有害檔案 | 安全風險 | 驗證 MIME type + 檔案大小限制 |
| 和弦準確度不佳被吐槽 | 負面回饋 | 優先修正 tester 回報的歌曲 |
| 外網暴露後被攻擊 | 資安事件 | Cloudflare WAF + rate limit |
| 版權投訴 | 法律風險 | Phase 2 限 beta、TOS 免責 |

---

## 10. 成功標準

**Phase 1 上線標準：**
- [x] 外網可透過 HTTPS 存取 — https://livechord.org (Cloudflare Tunnel, 2026-04-16)
- [ ] 5 位 tester 完成註冊
- [x] 搜尋 + 瀏覽和弦功能正常
- [x] 本地音檔播放 + 和弦同步正常
- [x] Feedback 系統可收集評價與 bug

**Phase 2 上線標準：**
- [x] 上傳音檔 → 和弦擷取 → 回傳 < 2 分鐘 (實測 ~1 秒，靜音檔)
- [ ] YouTube URL → 和弦擷取成功率 > 90% (待確認 yt-dlp PATH)
- [x] 音檔處理後 10 分鐘內自動刪除 (即時刪除 + 5 分鐘掃描)
- [x] 每日額度限制正常運作 (10 首/天)

**Beta 結束標準：**
- [ ] 收到 50+ 歌曲評價
- [ ] 和弦平均評分 ≥ 3.5/5.0
- [ ] 所有 Critical bug 已修復
- [ ] 10+ tester 活躍使用超過 2 週

---

## 附錄 A: 與現有文件的關係

| 文件 | 角色 |
|------|------|
| `doc/SPEC.md` | 功能規格（what it does） |
| `doc/PRODUCT_STRATEGY.md` | 商業戰略分析（why & business model） |
| `doc/PRODUCTIZATION.md` | **本文件 — 產品化實作藍圖（how to ship）** |
| `doc/QA.md` | 品質保證流程 |

## 附錄 B: 相關技術備忘

### Cloudflare Tunnel 安裝 (Windows)

```powershell
# 下載 cloudflared
winget install Cloudflare.cloudflared

# 登入 (開瀏覽器授權)
cloudflared tunnel login

# 建立 tunnel
cloudflared tunnel create livechord

# 設定 DNS
cloudflared tunnel route dns livechord livechord.yourdomain.com

# 啟動 (或註冊為 Windows Service)
cloudflared tunnel run livechord
```

### Tauri + Python Sidecar 參考

```toml
# tauri.conf.json — externalBin 設定
{
  "bundle": {
    "externalBin": ["sidecar/livechord-backend"]
  }
}
```

```rust
// main.rs — 啟動 Python backend
use tauri::api::process::Command;

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            Command::new_sidecar("livechord-backend")
                .expect("failed to create sidecar")
                .spawn()
                .expect("failed to spawn sidecar");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```
