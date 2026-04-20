# Shadow Mode V2 壓測 — PC Claude 任務單

這份檔案是 **NUC Claude ↔ PC Claude 的跨機溝通通道**：
- 上半部（「任務」）由 NUC Claude 寫下，給 PC Claude 執行
- 下半部（「PC Claude 回報區」）由 PC Claude 填入結果後 commit + push，NUC Claude pull 下來就看得到

格式：每次新任務 append 到最後，不覆蓋歷史紀錄。

---

## 任務 #1 — Shadow Mode 首輪壓測（2026-04-20）

### Context — NUC 側已完成

1. Phase 2 Shadow Mode 已部署並重啟（commit `af103c1`，含前置 `9a8a286` / `3d4de5f`）
2. `ENABLE_NN_MELODY=False`：V1 仍是用戶看到的結果；V2 在背景 subprocess 跑
3. Shadow 資料路徑（PC 可透過 `V:\` 讀寫，因 NUC 掛載為 `V:`）：
   - Log（JSONL）: `V:\data\shadow_v2.log`
   - V2 輸出: `V:\data\melodies_v2\<hash>.json`
   - V1 輸出: `V:\data\melodies\<hash>.json`
4. 測試前 log 僅 1 筆（E2E 人工測試，hash=`shadowtest123abc`）
5. NUC Python 3.11 venv 在 `V:\venv_ai\`（PC 不用動）

### 你的任務

批次上傳 **10-20 首不同類型歌曲**到 NUC 8800（LAN Personal，無需登入），觸發完整 upload → BTC → melody → shadow pipeline，觀察 V1/V2 比較資料累積。

### 具體步驟

1. **基線**：先讀 `V:\data\shadow_v2.log` 目前行數（應該是 1）
2. **選歌**：從 `Y:\`（NAS 根目錄）挑 10-20 首 `.flac`，建議混搭：
   - 3-4 首流行人聲（ABBA、Air Supply、10cc 類）
   - 2-3 首抒情（鋼琴/吉他伴奏）
   - 2-3 首節奏感強（舞曲/搖滾）
   - 1-2 首純器樂（可選）
   - 1 首超短（<2min）
   - 1 首超長（>5min）
3. **上傳**：對 `http://192.168.50.6:8800` 逐首 POST `/api/process/upload`
   - 端點接受 multipart/form-data，field 名稱叫 `file`
   - 如果這個 IP 不通，改試 `http://localhost:8800`（看 PC 是不是跟 NUC 同網段）；再不行改打 `https://livechord.org` 並用 `qatest` / `qatest1234` 登入
   - 上傳完拿 `job_id`，poll `GET /api/process/status/<job_id>` 直到 `status=done`
   - 記錄每首歌的 `result_hash`（用來對照 melodies / melodies_v2）
4. **等 shadow 收尾**：每首 V1 完成後 shadow 會再跑 5-10 秒 subprocess，每首之間 `sleep 30-60s` 再上傳下一首，避免塞爆 NUC
5. **驗證**：
   - `V:\data\shadow_v2.log` 行數應長到 `baseline + N`
   - `V:\data\melodies_v2\` 應有 N 個新檔案（比對 hash）
   - 讀所有新增 log entry，統計：
     * total / ok / error / timeout 數量
     * V1 平均耗時 vs V2 平均耗時（speedup 倍率）
     * V1 notes 平均 vs V2 notes 平均
     * `status != ok` 的項目，列出原因
6. **產出報告**：在本檔案「PC Claude 回報區」下 append markdown summary + 建議下一步（如要不要切 V2 為 primary），commit + push。NUC Claude 會 pull 下來接續

### 注意事項

- **不要重啟 NUC 服務**（用戶沒要求，且會打斷測試）
- **不要改 `ENABLE_NN_MELODY`**（仍維持 False，shadow-only）
- **不要改 `process_queue.py` 或 `melody_shadow.py`**（就是要測目前的 shadow 實作）
- **不要直接動 `V:\data\` 下的檔案**（那是 NUC production state；只讀 shadow_v2.log + melodies_v2 就好）
- 上傳失敗就換歌不要硬撐
- 每首歌成功的關鍵是 BTC 和弦偵測要通過；如果 NUC GPU 忙，BTC 可能要 20-60 秒

### 上下文文件

- [doc/AI_MIGRATION_REPORT.md](AI_MIGRATION_REPORT.md) — 三階段整體紀錄
- [../ai_migration_plan.md](../ai_migration_plan.md) — 原始計畫
- [../CLAUDE.md](../CLAUDE.md) — 專案規則（「Beta Testing Active」那段要注意）

分支: `feature/beta-productization`

---

## PC Claude 回報區

<!-- PC Claude：將測試結果 append 到這下面，遵守以下格式 -->

### 回報 #1 — (待填：日期 / 時間)

**執行狀態**: ☐ 進行中 ☐ 完成 ☐ 失敗

**環境**:
- PC → NUC 連線方式:
- 使用端點:
- 登入與否:

**上傳清單**:

| # | 歌名 | hash | V1 time | V1 notes | V2 time | V2 notes | shadow status |
|---|---|---|---|---|---|---|---|
| 1 |   |   |   |   |   |   |   |

**統計**:
- Total uploads attempted:
- Successfully reached `status=done`:
- shadow_v2.log 新增行數:
- ok / error / timeout 分佈:
- V1 平均耗時: _s
- V2 平均耗時: _s
- 平均 speedup: _×
- V1 平均 notes:
- V2 平均 notes:

**異常 / 觀察**:

**建議**:
