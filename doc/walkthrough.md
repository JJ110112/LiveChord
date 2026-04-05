# Phase 10: AI 鋼琴教學模式 — 實作紀錄

> Branch: `feature/piano-teaching`
> 開始日期: 2026-04-03

---

## Step 1: 強化伴奏生成引擎 (完成)

### 檔案: `backend/ai/accompaniment_generator.py`

#### 1.1 Pattern Dictionary — 7 種伴奏型態

| 型態 | 節拍結構 | 說明 |
|------|----------|------|
| Block | 拍頭同時按 R-3-5 | 柱狀和弦 |
| Arpeggio | R, 5th, 10th, 5th (各 1/4 拍) | 經典分解琶音 |
| Rhythm | R, R-3-5, R-3-5 (0, 0.5, 0.75) | 切分節奏 |
| Alberti | R, 5th, 3rd, 5th | 古典 低-高-中-高 |
| Shell | 3rd+7th 同時 | Jazz 精簡和弦 |
| Walking | R, 3rd, 5th, approach (半音趨近) | 逐拍行走低音 |
| Stride | R(低八度), chord(中音) | 低音跳躍 |

#### 1.2 Level 分級

| Level | 左手 Voicing | 右手 | Velocity |
|-------|-------------|------|----------|
| L1 | 根音 only | 純旋律 | LH=55, RH=90 |
| L2 | Shell (3rd+7th) | 旋律 + 大三度和聲 | LH=65, RH=90 |
| L3 | 完整和弦 | 旋律 + 大三度和聲 | LH=70, RH=90 |

#### 1.3 核心演算法

- **Voice Leading**: 每個和弦的 voicing 與前一和弦比較，嘗試 -12/0/+12 偏移，選擇最靠近前一和弦中心的組合
- **旋律防撞**: 掃描 ±duration 內旋律 MIDI，距離 < 4 半音則伴奏降八度
- **Walking Bass approach**: 索引 -1 表示下一和弦根音的半音下方
- **Chord Tone 標記**: 右手旋律音比對和弦 pitch class，標記 chord_tone=true/false
- **Viterbi 指法**: 左手鏡像邏輯（delta_p, delta_f 反轉），穿指成本 5.0，非法跨指 50.0

#### 1.4 曲風適配

```
suggest_style(genre, bpm) -> top 3 styles

Pop,100bpm  -> [Block, Arpeggio, Rhythm]
Jazz,140bpm -> [Shell, Walking, Rhythm]
Classical,70bpm -> [Arpeggio, Alberti, Shell]
```

#### 1.5 測試結果

```
Accompaniment Generation (ii-V-I progression: Cmaj7->Am7->Dm7->G7):
  Arpeggio   L1: LH= 4 events, RH=11 events
  Shell      L1: LH= 8 events, RH=11 events
  Arpeggio   L2: LH=16 events, RH=17 events  (L2 右手多 6 events = 三度和聲)
  Walking    L2: LH=15 events, RH=17 events
  Walking    L3: LH=15 events, RH=17 events

Viterbi Fingering:
  RH C scale up:   [60..72] -> [1,2,3,1,2,3,4,4] (穿指正確)
  RH C scale down: [72..60] -> [4,4,3,2,1,3,2,1]
  LH scale up:     [48..60] -> [1,1,2,2,3,4,5,5] (左手鏡像)
```

---

## Step 2: API 端點 + 快取層 (完成)

### 檔案: `backend/ai_api.py`

#### 2.1 新增端點

| 端點 | 方法 | 說明 |
|------|------|------|
| `/api/ai/accompaniment?path=&style=&level=` | GET | 生成伴奏（含快取） |
| `/api/ai/suggest-style?path=` | GET | 曲風+BPM 建議 top 3 styles |

#### 2.2 資料流

```
Request -> 檢查快取 data/accompaniments/{hash}_{style}_{level}.json
  -> 命中: 直接回傳
  -> 未命中:
     1. 載入和弦 data/chords/{hash}.json
     2. 載入旋律 data/melodies/{hash}.json (如有)
     3. 從 library_cache.json 取 genre + 估算 BPM (中位和弦長度)
     4. 呼叫 generate_accompaniment()
     5. 寫入快取
     6. 回傳結果
```

#### 2.3 設計決策

- 使用 `def`（非 `async def`）— 避免 file I/O 阻塞 event loop (已知 pitfall)
- BPM 估算：取和弦持續時間中位數，`60 / median_dur`
- 快取 key：`{md5(path)[:12]}_{style}_{level}.json`

---

## Step 3: 前端瀑布流 + 教學 UI (完成)

### 檔案: `frontend/js/player.js`, `frontend/player.html`, `frontend/css/style.css`

#### 3.1 Canvas 瀑布流
- 88 鍵 Canvas 上方瀑布流，lookAhead = 4 秒
- 左手青藍 / 右手橘紅分色，接近琴鍵時 glow effect
- 指法數字 (1-5) 標示在長條中央
- Beat Grid 小節線 + 拍號標記
- 左手手型背景區塊 (Position Hand Block)

#### 3.2 教學控制列
- Style 下拉 (Block/Flow/Rhythm/Walking/Stride)
- Level 切換 (L1 基礎 / L2 進階 / L3 大師)
- AI 推薦按鈕 → `/api/ai/suggest-style`
- 左手/右手切換 (三態: 雙手 → 左手 → 右手)
- Chord Tone 分色 (♪ 按鈕)

#### 3.3 A-B Repeat 區段循環
- 三段式狀態機: idle → a_set → active → idle
- 第一次點擊設 A 點 (按鈕 `A-⏸` 青藍), 第二次設 B 點 (按鈕 `A-B ✓` 綠色), 第三次取消
- tickSync 中偵測 `currentTime >= B` 時自動 seek 回 A
- 進度條: 綠色半透明區塊 + A/B 邊界線
- 瀑布流: A/B 虛線水平標記 + 範圍外區域變暗
- 切歌自動清除狀態

## Step 4: 進階互動 (待實作)
