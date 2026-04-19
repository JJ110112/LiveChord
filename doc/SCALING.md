# LiveChord 擴展路線圖（Scaling Roadmap）

> 版本: 1.0 | 日期: 2026-04-20
> 觸發情境: Beta 回饋正向、使用量成長、開始出現「撐不住」的訊號時，查這份表決定下一步投資方向

這份文件是**長期規劃**，不是要立刻做。[doc/PRODUCTIZATION.md](PRODUCTIZATION.md) 負責「Beta 能跑起來」，本篇負責「Beta 成功之後」。

---

## 0. 觸發門檻（什麼時候該動）

各維度各自有觸發條件，不要一起做：

| 維度 | 目前狀態 | 觸發點（看到就開始動） |
|---|---|---|
| 個人/公眾資料隔離 | 全共用（見 CLAUDE.md dual-instance 段） | Beta 月活 >50 或法務/隱私問題浮現 |
| 和弦擷取 GPU | NUC CPU, 每首 30–60s | 佇列積壓常態 >10 首 或 週尖峰 >200 首分析 |
| 雲端部署 | NUC + Cloudflare Tunnel | 住家停電/斷網開始影響可用性；或需要異地備援 |
| DB 擴展 | SQLite + WAL（單機） | 寫入延遲 >100ms 常態化；或需要多地讀取 |
| 本地化 (i18n) | 介面僅繁中 | 非華語使用者回饋多、或要上架海外 |

---

## 1. 個人版（8800）與公眾版（8801）分割

目前只有 **設定檔** 分流（`settings_personal/beta/shared.json`），其餘 DB/模型/JSON 共用。短期已知副作用見 [CLAUDE.md — What's SHARED](../CLAUDE.md#whats-shared-across-the-two-instances-important-for-beta-contributions)。

### 1.1 分割選項

| 選項 | 工作量 | 好處 | 代價 |
|---|---|---|---|
| **A. DB 分檔，模型共用** | 低（1-2 週） | 個人模型不被 beta 低品質校正污染；隱私邊界清晰 | 同一 NUC 兩份 auth/feedback/audit DB；模型訓練需明確挑語料 |
| **B. 公眾版整包搬上雲** | 中（2-4 週） | 個人不被公眾流量影響；雲端 GPU 等彈性資源隨時可接 | 新的 infra（DB、物件儲存、CI/CD）；運維成本 |
| **C. Multi-tenant 單 codebase** | 高（2-3 月） | 為未來 SaaS 鋪路；每個使用者獨立 namespace | 需要全面改 schema（加 `user_id` 欄），現有 file-path 命名要重構 |

### 1.2 建議推進順序

1. **短期（觸發點到達時）**：走 A。加 `origin_mode` 欄位到 `process_audit` / `human_feedback`，讓 chord2vec 重訓可選擇 `WHERE origin_mode='personal'`。改動小、可逆
2. **中期**：走 B 的子集 — 把 `data/accompaniments`（~15GB）、`data/uploads`（beta 音檔）搬到物件儲存（R2/S3），NUC 只留小型 DB + 前端靜態檔
3. **長期**：如果真的商業化才考慮 C。Vibe-coding 一人專案走 multi-tenant 風險高於收益

### 1.3 絕對不要做的事

- **把 beta user 的校正直接進 personal 模型**，不論訓練流程多方便 — 品質風險無法量化
- **把 8800 和 8801 設計成同時寫同一份模型 weights**（現況是 auto_worker 只在 personal 跑，硬 gate，別放寬）

---

## 2. 和弦擷取效率 — CPU → GPU

### 2.1 現況

- BTC chord detection: CPU `ProcessPoolExecutor`, `LIVECHORD_BTC_WORKERS=2` per instance → NUC 總共 4 workers
- 單首 30–60s（取決於歌曲長度與 BTC pass 數）
- 旋律擷取 (Basic Pitch): CPU
- chord2vec retrain: subprocess, 數小時

### 2.2 加速路徑

| 路徑 | 成本 | 單首擷取時間 | 適用規模 |
|---|---|---|---|
| NUC 加 eGPU (Thunderbolt) | 一次 NT\$15k–25k | 5–10s | 週尖峰 <500 首 |
| 換桌機 + RTX 4060 | 一次 NT\$25k–35k | 3–5s | 週尖峰 <3000 首 |
| 雲 GPU on-demand (L4/A10G) | 隨用 NT\$15–30/hr | 2–5s | 尖峰彈性 |
| 模型推論服務 (Modal/Replicate) | 按 call NT\$0.3–1 | 2–5s | 不想管 infra |

### 2.3 Pytorch 開關

BTC 目前 `torch.device("cpu")` hardcode（見 `backend/ai/`）。GPU 化的改動：

```python
DEVICE = os.environ.get("LIVECHORD_DEVICE", "cpu")
model = BTC_model.eval().to(DEVICE)
audio_tensor = audio_tensor.to(DEVICE)
```

加環境變數切換，本地 CPU 不受影響、雲端 GPU 打開即可。批次推論（multiple songs in one forward pass）可把 GPU 使用率從 ~20% 拉到 ~80%，值得做。

### 2.4 Basic Pitch 旋律擷取

目前 `_loadMelody` 阻塞 ~60s（CLAUDE.md 有提）。同樣 GPU-ify 或改用 Crepe-tiny（速度快 5 倍、精度略低）。兩個方案都可以，**要人類耳朵 A/B 後決定**。

### 2.5 不要走的路

- **NPU/INT8 量化**：CLAUDE.md 已經說 `project_nuc_hw_accel` 因「不值得 + cloud CUDA 遷移路徑」捨棄，繼續維持
- **自建 GPU 訓練 cluster**：一人專案規模，用雲 GPU 按需租更划算

---

## 3. 部署平台

### 3.1 各平台 fit 分析

| 平台 | 月費 (小流量) | 適合做什麼 | 不適合做什麼 |
|---|---|---|---|
| **目前 NUC + Cloudflare Tunnel** | ~0（電費） | Personal + Beta <100 MAU | 住家斷網、GPU 擴充、地理冗餘 |
| **Fly.io / Railway** | \$5–30 | FastAPI app, 小 volume, 自動擴展 | 大檔案儲存（貴） |
| **Hetzner VPS + Cloudflare R2** | \$10–40 | 自控後端 + 便宜物件儲存 | 自己 setup nginx/systemd/backup |
| **AWS/GCP** | \$50+ | 真商業化、多區域、GPU instance | 運維複雜度高 |
| **Modal** | 按 call | 只把 AI 推論外包，app 留在別處 | 做整個 app |
| **Tauri desktop 打包** | 0 | 完全 local、零 infra、繞過版權風險 | 需要雲端同步 |

### 3.2 建議混合架構（觸發點到達時）

```
┌──────────────────┐
│ 使用者瀏覽器     │
└────────┬─────────┘
         │
    Cloudflare (CDN + Tunnel)
         │
    ┌────┴──────────────────────────┐
    │                               │
┌───▼───────────┐          ┌────────▼─────────┐
│ Hetzner VPS   │          │ Modal / Replicate│
│ FastAPI       │─call─────│ BTC GPU 推論     │
│ Postgres      │          │ (按首收費)       │
│ (auth/audit)  │          └──────────────────┘
└───┬───────────┘
    │
┌───▼───────────┐
│ Cloudflare R2 │  (音檔、cover、chord JSON)
└───────────────┘
```

NUC 退回純 personal 用，公眾流量完全獨立。Beta → 雲端的遷移路徑清楚。

### 3.3 Tauri 桌面版（PRODUCTIZATION Phase 3）

[doc/PRODUCTIZATION.md](PRODUCTIZATION.md) 已列 Phase 3 為 Tauri 桌面版。這路線的意義：
- **法律上最安全** — 使用者的音檔永遠不離開本機
- 雲端只負責同步 chord JSON / 使用者帳號
- 對大眾發行最穩

但 Tauri 需要 Rust + 打包流程 + 跨平台測試，**人力成本是雲端方案的 3-5 倍**。除非版權風險具體化，否則先走雲端混合。

---

## 4. 資料庫擴展

### 4.1 現況盤點

| DB / 檔案 | 體積 (2026-04) | 預估成長率 | 瓶頸點 |
|---|---|---|---|
| `auth.db` | <1 MB | 慢 | — |
| `feedback.db` | <1 MB | 中 | — |
| `audit.db` | ~25 KB | 隨 beta 使用量 | 掃 `process_audit` 做 search；~1M 筆後需 index + TTL |
| `data/chords/*.json` | 598 MB / 54k 首 | 慢（庫歌+beta 新分析） | 檔案數量，非單檔大小 |
| `chord_index.json` | ~14 MB | 慢 | 啟動時 load 到記憶體 |
| `library_cache.json` | ~10 MB | 慢 | 同上 |

### 4.2 遷移路徑（依觸發順序）

1. **先加 `process_audit` TTL**（最便宜）：每日排程清掉 >90 天且 status=error 的紀錄
2. **SQLite → Postgres**（中等工作量）：
   - 用 `alembic` 做 schema 版本
   - `auth.db` / `feedback.db` / `audit.db` 合併成單一 Postgres DB
   - 本地開發仍可用 SQLite（SQLAlchemy 抽層）
3. **Chord JSON → 物件儲存 (R2/S3)**：
   - 現在走檔案系統 → 換 S3-compatible client
   - `chord_index.json` 改用 Postgres 表（`songs` table with FTS index）
4. **Search 升級**：目前 `/api/search` 是記憶體 scan `library_cache`。換 Postgres FTS 或 Meilisearch
   - 現況 ~50k 曲可以接受；~500k 就必須換

### 4.3 不要做的事

- **不要太早遷 Postgres**。SQLite WAL 足夠撐到 hundreds of MAU，提早搬只增運維成本
- **不要把音檔存 DB**。二進位 blob 永遠走物件儲存

---

## 5. 國際化 (i18n) — **至少要英文版**

### 5.1 現況

- 介面文字 100% 繁體中文（hardcoded）
- 歌名/藝人/專輯元資料：中/英/日/韓混雜（YouTube 資料本身多語，沒問題）
- 錯誤訊息 / toast / label — 都是中文

### 5.2 觸發條件

- 非華語使用者回饋 ≥5 人
- 考慮上架海外市場（Product Hunt, 國際社群）
- 有人提 GitHub issue 用英文

### 5.3 實作路徑

| 階段 | 工作量 | 涵蓋範圍 |
|---|---|---|
| 1. 靜態字串抽取 | 中（3-5 天） | 所有 HTML + JS toast/label → `i18n/zh-TW.json` + `en.json` |
| 2. 語系切換 UI | 小（1 天） | 頁首下拉，persist `localStorage.livechord_lang` |
| 3. 後端錯誤訊息 | 小（1-2 天） | `HTTPException(detail=...)` 改用 key，前端查表翻譯 |
| 4. 歌名/藝人英譯（選配） | 大（AI 翻 + 人工校對） | 不做也沒關係，音樂資料多語本來就正常 |

### 5.4 推薦工具

- **Frontend**: `i18next` 或自寫簡易 `t("key")` 函式（vanilla JS 無框架，i18next 略重，自寫更搭調）
- **後端**: FastAPI 不需要，錯誤訊息回 key，前端翻
- **翻譯**: Claude API 做第一版 → 母語者校對。**不要用 Google Translate 機譯的結果直接上線**，音樂術語（chord, key, bar, measure）容易錯

### 5.5 字串抽取原則

- **不要用自動 regex 工具**：中文夾英文的語境（「請輸入 YouTube URL」）regex 抽不乾淨
- **漸進式**：先抽最高曝光頁面（homepage + player 主要按鈕），次要頁面（admin、editor）最後
- **保留 key 語意化**：`search.placeholder.noHistory` 比 `str_042` 好維護

---

## 6. 其他 scaling 議題

### 6.1 濫用防護

- **Rate limit**: 目前 auth endpoint 有（`_RATE_MAX_AUTH`），其他 endpoint 沒有。至少 `/api/process/*` 應加 per-user quota（每日 10 次已有，但缺 per-minute 的 burst limit）
- **CAPTCHA**: 註冊開放後，bot 註冊會是問題。Cloudflare Turnstile 免費且不吵
- **yt-dlp 被 YouTube 限速**: 現況 subprocess 直跑，量大會被 IP 封。考慮 Modal 等「帶住宅 IP 的託管服務」

### 6.2 媒體儲存策略

- **短期（<6 個月）**：beta 使用者上傳的音檔不刪，方便重複分析 — 但這會線性成長
- **中期**：上傳音檔分析完就刪，只保留 chord JSON + cover。相依的 `audioDBLoad(hashMode)` 前端 IndexedDB 已做「一次性傳遞」設計，backend 本來就不該長期持有音檔
- **長期**：如上雲，音檔走 R2 + 7 天 lifecycle rule 自動刪除

### 6.3 模型版本管理

- 目前 retrain 直接覆寫 `data/models/*`。沒有 rollback 機制
- 加上：retrain 先寫到 `data/models/candidate/`，人工 A/B 比對後再 `promote` 成正式版。舊版備份到 `data/models/archive/<yyyy-mm-dd>/`
- CLAUDE.md 已有「tiered backup」機制，models 在 tier2，但沒有熱備援（切回舊版需人工介入）

### 6.4 監控

- 目前只有 rotating file log（fb81322 commit）
- 觸發門檻：週尖峰 >100 使用者時加 Sentry（免費 tier 就夠）
- Grafana / Prometheus 是過度工程，除非量級到 1k+ DAU

### 6.5 CDN / 快取

- 靜態資源：Cloudflare 已 CDN
- API 回應：目前無 HTTP cache hints，`/api/chords/by-hash` 其實永不變動，可以加 `Cache-Control: public, max-age=31536000, immutable`
- 這是**便宜大收益**的優化，加一行就降 ~30% 後端負載

### 6.6 成本量級估算

| 規模 | 月費估算 (USD) | 拆解 |
|---|---|---|
| 現況 (NUC only, <20 MAU) | \$0 + 電費 | — |
| 100 MAU, 雲遷後 | \$30–60 | VPS 20 + R2 5 + Modal 10–30 |
| 500 MAU | \$100–200 | +Postgres 管理版、GPU 時數多 |
| 2000 MAU | \$400–800 | 多區域部署、專職監控 |

> 規模指標：MAU = Monthly Active User，每月至少一次和弦分析

---

## 7. 決策流程

當使用量上升，照這個順序檢查：

1. **是哪個維度先滿？** 看 §0 的觸發門檻。常見先後：GPU擴充 → 媒體儲存 → DB → i18n
2. **有沒有 10 倍擋板？** 可以只做當下痛點的 2-3 倍擴展，別一次做 10 倍 — 否則工作量爆表且未必命中
3. **能不能逆？** 先做可逆的（加 env var 切換、加一欄 column），不可逆的（schema 大改、真雲端遷移）放後面
4. **寫下「為什麼」**：每個擴展決定寫進 CLAUDE.md 對應段落，未來自己才看得懂動機

---

## Reference

- 現況架構與 dual-instance 隔離: [CLAUDE.md](../CLAUDE.md)
- Beta rollout 規劃: [PRODUCTIZATION.md](PRODUCTIZATION.md)
- 商業/法律分析: [PRODUCT_STRATEGY.md](PRODUCT_STRATEGY.md)
- 作戰故事 (過去踩過的坑): [QA_BATTLE_STORY.md](QA_BATTLE_STORY.md)
