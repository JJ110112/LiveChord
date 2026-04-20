# Phase 4 — Hybrid Melody Architecture

**Status**: 規劃 + POC 驗證完成
**Prerequisite**: [AI_MIGRATION_REPORT.md](AI_MIGRATION_REPORT.md) 的 Phase 1-2.5 已落地
**Last updated**: 2026-04-21

## 1. Why hybrid

Phase 2.5 overlap 分析證實：**單一演算法無法同時處理 LiveChord 的三種歌曲來源**：
- 經典老歌有 MIDI（但 basic-pitch 單跑會被和聲污染）
- 熱門新歌無 MIDI（只能靠 NN 模型，但 V2 有 85% extras）
- 異質 MV 含對話（單純音訊對齊會被劇情段破壞）

Phase 4 採用 **3-tier 路由**：依歌曲特性選演算法，各取所長。

## 2. 3-Tier 路由決策樹

```
用戶點一首歌（或上傳）
         │
         ▼
  ┌─────────────────────────────────────┐
  │ 步驟 1：查 midi_catalog.json         │
  │   → 有 MIDI 對齊成果？               │
  └─────────────────────────────────────┘
         │
    ┌────┴────┐
    │ yes     │ no
    ▼         ▼
  TIER 1     ┌─────────────────────────────────────┐
  讀 JSON     │ 步驟 2：查 data/melodies_v4/        │
  0 計算      │   → PC 已批次算過 V4？              │
             └─────────────────────────────────────┘
                 │
            ┌────┴────┐
            │ yes     │ no
            ▼         ▼
          TIER 2     TIER 3
          讀 JSON     即時 V1 pYIN
          0 計算       + 排入 PC 批次佇列
                     （下次播放就能升級到 V4）
```

### Tier 1 — MIDI-Audio Alignment（品質天花板）

**適用**：經典老歌（LiveChord 策展 MIDI catalog）
**演算法**：pretty_midi synth → chroma CQT → DTW → warp apply → LiveChord JSON
**品質**：**錄音室級**（人工轉譜的 MIDI 當 ground truth）
**耗時**：~5 秒/首（CPU）、PC 批次 1 秒/首（GPU）
**POC 驗證**：見下方 §5

### Tier 2 — Demucs + RMVPE (V4) Precomputed（規模化優質）

**適用**：熱門新歌無 MIDI、但 PC 已跑過批次
**演算法**：demucs vocals stem → RMVPE F0 tracking → LiveChord JSON
**品質**：優良（vocal-isolated + vocal-specialized pitch tracker）
**PC 批次耗時**：估 ~8 秒/首（RTX 5080），78K 首 ≈ 175 小時（一週閒置時間掛機）
**落地前提**：需先採用 RMVPE model（replaces basic-pitch V2）

### Tier 3 — 即時 V1 pYIN Fallback（新上傳即時回應）

**適用**：首次上傳、Tier 1/2 都沒有
**演算法**：現行 V1 pYIN（`backend/ai/melody_extractor.py`）
**品質**：堪用（目前 beta 用戶看到的就是這個）
**耗時**：~50 秒/首（CPU，NUC）
**行為**：
- 立即回 V1 JSON 給前端（用戶體驗不變）
- 同時 push hash 進 PC 批次佇列（下次打開同首歌已有 V4）

### 額外層：MV 5.5s DTW Sync（即時輕量）

**適用**：已有 chord/melody JSON 的歌曲，用戶點的 YouTube 是異質 MV（含對話、現場演唱）
**演算法**：MP3 chroma（audio-only reference）↔ MV audio chroma → DTW 映射 JSON 時間戳
**耗時**：~5.5 秒（NUC 即時）
**場景範例**：用戶點「電視劇主題曲」→ 背景有角色對話 → 我們把音樂版的 chord 時間軸 warp 到 MV 版上

## 3. PC/NUC 工廠—門市分工

### 3.1 職責劃分

| 角色 | 機器 | GPU | 職責 |
|---|---|---|---|
| **ML 工廠** | PC | RTX 5080 | Demucs 批次分離、RMVPE 批次推論、MIDI catalog 對齊批次 |
| **前台門市** | NUC | 無（Intel iGPU + NPU for light） | FastAPI 服務、SQLite、靜態 JSON 讀取、即時 MV 5.5s DTW |

### 3.2 資料流

```
PC (off-peak 批次)                      NUC (實時服務)
  │                                       │
  ├─ Demucs ────────►  V:\data\hybrid_melody\<hash>\vocals.wav
  │                                       │ ← NUC 不生成這些
  ├─ RMVPE ────────►  V:\data\melodies_v4\<hash>.json
  │                                       │
  ├─ MIDI align ──►  V:\data\midi_aligned\<hash>.json
  │                    + midi_catalog.json（進 git）
  │                                       │
                                          ├─ GET /api/ai/melody?hash=X
                                          │    ↓
                                          │  查 melodies_v4/ → 有？回。
                                          │  查 midi_aligned/ → 有？回。
                                          │  都沒 → V1 pYIN + push 進 PC queue
                                          │
                                          └─ 用戶點 YouTube MV
                                               → 下載 → 跟 V4/MIDI JSON 做 5.5s DTW
                                               → 回 warped JSON
```

### 3.3 同步協議

**PC 批次完成** → **NUC 即時可用** 之間的同步管道：

**Option A — SMB（現狀）**
- PC 寫 `V:\data\*`（= NUC 的 `c:\LiveChord\data\*` 經 SMB 映射）
- NUC uvicorn 讀本機檔
- 優點：零額外基礎設施
- 缺點：PC 關機 = NUC 看不到新資料（但 off-peak 反正是 PC 開機時寫、NUC 持續讀）

**Option B — Firestore / Object Storage**（長期）
- PC 寫雲端 bucket
- NUC 讀雲端 bucket（或本地 cache）
- 優點：PC/NUC 解耦、多 NUC 部署可共用資料
- 缺點：成本、複雜度；Phase 5+ 再議

**本階段採 Option A**。

### 3.4 PC 批次觸發協議

PC 需要知道「什麼歌該跑 V4」。幾種來源：

1. **初始掃描**：PC 端掃 `V:\data\chords\*.json`，跑所有 hash 沒在 `melodies_v4/` 裡的
2. **新上傳動態加隊**：NUC 上傳流程寫一行到 `V:\data\v4_queue.txt`（hash + timestamp）
3. **人工 priority flag**：admin 在 UI 可勾選「優先跑這首 V4」

PC 端有個 daemon：
- 每 5 分鐘掃 `chords/` 與 `melodies_v4/` 的差集
- 合併 `v4_queue.txt`（priority 置前）
- 依序跑 demucs + RMVPE
- 完成後寫 `melodies_v4/<hash>.json` + 刪 queue 行

## 4. MIDI Catalog 規格

### 4.1 目標規模

**Phase 4 Week 1**：Curate 30-50 首最受歡迎經典老歌的 MIDI
**Phase 4 Month 1**：擴到 200-300 首
**長期**：500-1000 首（鋼琴教學場景夠用）

### 4.2 Catalog schema

`data/midi_catalog.json`（進 git）：

```json
{
  "songs": [
    {
      "id": "mj_remember_the_time",
      "title": "Remember The Time",
      "artist": "Michael Jackson",
      "midi_file": "E-POP/Michael Jackson - Remember The Time.mid",
      "target_songs": [
        {
          "audio_hash": "6f846c3a48f7",
          "audio_path": "Z:/POP/E-POP/Michael Jackson/.../Remember the Time.flac",
          "alignment_confidence": "high",
          "warp_mean_s": 2.4
        }
      ],
      "melody_track_index": 0,
      "bass_track_indices": [1],
      "rh_harmony_track_indices": [2, 3],
      "pad_track_indices": [4, 5],
      "license": "user_provided",
      "curator_note": "clean MIDI, explicit MELODY label"
    }
  ]
}
```

### 4.3 Curation workflow

每首 MIDI 進 catalog 前：

1. **載入驗證**：`pretty_midi.PrettyMIDI(path)` 不噴例外
2. **Duration sanity**：30s < duration < 900s
3. **跑一次 alignment**：用 `data/tmp/midi_align_multitrack.py --batch` 對應 flac
4. **人工審核** alignment summary：
   - `warp_mean_s ≤ 5` 且 `alignment_confidence == "high"`
   - melody track 人工確認是主旋律
5. **填 catalog entry**：指定 track indices、記錄 warp_mean
6. **git commit** catalog + 觸發 PC 批次跑 `midi_aligned/<hash>.json`

POC 證實這 workflow 可行（見 §5）。

## 5. POC 結果摘要

### 5.1 Tier 1（MIDI 對齊）驗證

對 7 首 E-POP MIDI + Z:\ FLAC 配對 batch 跑：

| 歌曲 | Confidence | warp_mean | 用途 |
|---|---|---:|---|
| MJ - Remember The Time | ✓ high | 2.4s | 可入庫 |
| Europe - The Final Countdown | ✓ high | 0.9s | 可入庫 |
| John Lennon - Imagine | ~ medium | 3.2s | 可入庫（需人工覆核）|
| Paul McCartney - Ebony & Ivory | ~ medium | 4.4s | 可入庫（需人工覆核）|
| Eagles - Hotel California (LIVE) | ✗ low | 55.9s | **拒絕**（結構差異）|
| Eagles - Tequila Sunrise (LIVE) | ✗ low | 22.7s | 拒絕 |
| Guru Josh - Infinity | ✗ low | 94.8s | 拒絕 |
| MJ - You Are Not Alone | ❌ MIDI corrupt | — | 拒絕 |

**可用率 4/8 = 50%**。以策展流程看這是健康比例（每 2 首取 1 首是正常的策展效率）。

### 5.2 Playable RH 密度（B1 放寬後）

| 歌曲 | melody | RH rich (/s) | **playable_RH (/s)** |
|---|---:|---:|---:|
| MJ Remember Time | 197 (0.82) | 946 (3.93) | **257 (1.07)** |
| Europe Final Countdown | 512 (1.63) | 1697 (5.40) | **747 (2.39)** ⭐ |
| Imagine | 191 (1.04) | 389 (2.12) | **213 (1.16)** |
| Ebony & Ivory | 98 (0.42) | 2272 (9.73) | **340 (1.50)** |

密度天花板由 source MIDI 決定（MJ/Imagine 本身 harmony 稀疏）。Europe 命中 2.39/s 目標 ⭐。

### 5.3 工程修復驗證

- ✅ **Bass 排除**：Europe 從 DISTORTION → 正確選 SYNBRASS 1（主 synth）
- ✅ **DTW NaN 防護**（epsilon + Gaussian smoothing）：Ebony & Ivory 和 Guru Josh 從 NaN fail → 能跑
- ✅ **結構差異拒絕**（warp_mean > 10s）：LIVE 版自動標 low confidence

## 6. 實作階段規劃

### Phase 4.0（本階段完成項）

- [x] POC 多首驗證（§5）
- [x] `simplify_to_playable_rh()` 函式（`data/tmp/midi_align_multitrack.py`）
- [x] Pad heuristic 修正（range > 36 semitones）
- [x] Bass 排除 melody picker
- [x] DTW robustness（NaN + smoothing + structural rejection）
- [x] 本文件

### Phase 4.1（下一步、需人力）

- [ ] `data/midi_catalog.json` schema 固化 + 工具腳本
- [ ] **策展 10 首** 入庫（MJ Remember / Europe / Imagine / Ebony 已有 POC 數據，直接可入）
- [ ] PC 端 batch worker：讀 catalog → 跑 alignment → 寫 `data/midi_aligned/<hash>.json`
- [ ] Admin UI「策展 MIDI」頁面：上傳 MIDI + 指定 target audio + 預覽 alignment

### Phase 4.2

- [ ] `backend/ai/melody_shadow_v3.py`：把 `midi_aligned/` 當最高優先讀取路徑
- [ ] 修改 `process_queue._melody_worker_loop`：查 `midi_aligned/` → 查 `melodies_v4/` → fall through V1
- [ ] `ai_api._get_melody` 同樣路由
- [ ] Admin dashboard 顯示「V1 / V4 / MIDI-aligned」佔比

### Phase 4.3

- [ ] PC 端 RMVPE 整合（replaces V2 basic-pitch for Tier 2）
- [ ] PC 批次 demucs + RMVPE 跑 78K 首
- [ ] NUC 讀 `melodies_v4/` 優先於 V1

### Phase 4.4（MV DTW sync）

- [ ] `backend/ai/mv_sync.py` — 5.5s 即時 DTW
- [ ] hook 進 YouTube 播放流程

## 7. 風險與緩解

| 風險 | 緩解 |
|---|---|
| **MIDI 授權**：商業 MIDI library 要錢；社群 MIDI 品質不一 | 策展型採購或用戶 upload；catalog 標記 license 來源 |
| **Curation 人力瓶頸**：每首需人工聽 alignment | 建 Admin UI 簡化流程；先 10-50 首重點歌曲 |
| **PC 掛機時間**：78K 首一週跑不完？ | 按熱門度 priority 佇列；熱門 top 1K 優先 |
| **Tier 切換邊界爭議**：用戶看不出差異 | 前端角落顯示品質徽章（和 Phase 2.5 的 chord 評分 LED 類似） |
| **catalog 膨脹**：大 JSON 進 git 不便 | catalog 超過 500 首時切 SQLite |

## 8. 相關檔案

- [AI_MIGRATION_REPORT.md](AI_MIGRATION_REPORT.md) — Phase 1-2.5 資料
- [shadow_stress_test_prompt.md](shadow_stress_test_prompt.md) — 跨機 agent 協作紀錄
- `c:/LiveChord/data/tmp/midi_align_multitrack.py` — Phase 4.0 POC 腳本（git-ignored）
- `c:/LiveChord/data/tmp/midi_aligned/*.json` — POC 7 首輸出
- `c:/LiveChord/E-POP/*.mid` — 測試 MIDI source（15 首）

## 9. Open Questions

1. **MIDI 取得策略**：用戶自帶 / 內建策展 library / 混合？
2. **Curation 權責**：admin 全包 or 開放 user 貢獻？
3. **RMVPE 取代時機**：Phase 4.3 要不要直接跳過 V2 basic-pitch？
4. **PC batch daemon 怎麼啟動**：開機自動還是手動？
5. **Catalog 版本化**：每次更新要 bump version 嗎？NUC 怎麼偵測 catalog 變動？
