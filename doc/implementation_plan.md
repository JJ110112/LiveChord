# Phase 10: AI 鋼琴教學模式 — 實作藍圖

> 對應規格書: SPEC.md v2.0 §4.7
> Branch: `feature/piano-teaching`

---

## 架構總覽

```
Jazzify (OFF/L1-L3)          彈奏難度 (L1-L3) + 伴奏風格 (Style)
     |                              |
     v                              v
基礎和弦譜 --> 最終和弦譜 --> Accompaniment Engine --> Viterbi Fingering --> Canvas 瀑布流
                                     ^
                                     |
                               pYIN 旋律資料
```

兩軸獨立：Jazzify 改寫和弦記號，Accompaniment Engine 將和弦展開為物理按鍵。

---

## Step 1: 強化伴奏生成引擎 ✅ 完成

**檔案**: `backend/ai/accompaniment_generator.py`

- 7 種 Pattern Dictionary (Block/Arpeggio/Rhythm/Alberti/Shell/Walking/Stride)
- 3 Level 分級 (L1 根音 / L2 Shell Voicing / L3 完整)
- 右手生成 (旋律 + L2/L3 三度和聲 + chord tone 標記)
- Voice Leading 跨和弦最短距離
- 旋律防撞 (< 4 半音自動降八度)
- Walking Bass approach note (半音趨近)
- Viterbi 指法 (左手鏡像, 穿指成本模型)
- 曲風適配 suggest_style(genre, bpm) → top 3

---

## Step 2: API 端點 + 快取層 ✅ 完成

**檔案**: `backend/ai_api.py`

- `GET /api/ai/accompaniment?path=&style=&level=` — 伴奏生成 (含快取)
- `GET /api/ai/suggest-style?path=` — 曲風+BPM 建議
- 快取: `data/accompaniments/{hash}_{style}_{level}.json`
- 使用 `def` (非 `async def`) 避免 file I/O 阻塞
- BPM 估算: 和弦持續時間中位數

---

## Step 3: 前端瀑布流 + 教學 UI ✅ 完成

**檔案**: `frontend/js/player.js`, `frontend/player.html`, `frontend/css/style.css`

### 3.1 Canvas 瀑布流
- 88 鍵 Canvas 上方新增瀑布流區域 (佔 60% 高度，專注模式)
- 音符長條由上往下掉落，速度與 playbackRate 同步
- 左手 = 青藍色 (rgba(0,255,255)), 右手 = 橘紅色 (rgba(255,165,0))
- 長條長度 = duration, 寬度 = 琴鍵寬度
- 接近琴鍵時顯示指法數字 (1-5)
- 落鍵觸發光暈 glow effect
- requestAnimationFrame 只渲染可見範圍

### 3.2 教學控制列
- Style 下拉選單 (7 種)
- Level 切換 (L1/L2/L3)
- 「AI 推薦」按鈕 → 呼叫 /api/ai/suggest-style
- A-B Repeat 區段循環 (三段式: 設 A → 設 B → 取消，進度條+瀑布流視覺標記)
- 左手/右手/原聲 靜音切換

### 3.3 資料載入
- 播放時併發 fetch `/api/ai/accompaniment`
- 將 left_hand/right_hand events 存為全域陣列
- Jazzify 切換 → 重新 fetch (和弦已更新)
- Level/Style 切換 → 重新 fetch (和弦不變)

### 3.4 Chord Tone 分色
- 右手旋律比對當前和弦 pitch class
- chord tone → 綠色, passing tone → 灰色

---

## Step 4: 進階互動 — 待實作 (漸進式)

- Voice Leading 導引線: Canvas 虛線連接和弦切換時的相同/相近音
- ~~Beat Markers: 根據 BPM 畫水平小節線~~ ✅ (已在瀑布流中實作)
- 段落級自動切換: section_detect → Verse=Arpeggio, Chorus=Rhythm
- WebMIDI: `navigator.requestMIDIAccess()` 接收琴鍵, 比對正確性

---

## 驗證方式

1. **Step 1**: `python backend/ai/accompaniment_generator.py` → All tests passed ✅
2. **Step 2**: `curl /api/ai/accompaniment?path=...&style=Arpeggio&level=L2` → JSON 回傳 ✅
3. **Step 3**: 選一首已有和弦+旋律的歌，播放後確認瀑布流正確落下 ✅
4. **Step 4**: Playwright QA 新增教學模式 test cases
