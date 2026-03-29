# LiveChord 工具集

> Chordify 和弦擷取 → LiveChord 播放系統

---

## 快速開始（推薦流程）

```
步驟 1: 輸入和弦序列
  雙擊 chordify_gui.pyw
  → 輸入歌名 + 等級
  → 📝 Edit Ref → 新建（輸入總拍數）
  → 看著 Chordify 畫面，在 Grid Editor 打字輸入和弦
  → 💾 Save → .chords.txt

步驟 2: 錄影 + 分析（取得精確時間軸）
  → Chordify 切換到 Chord diagrams 模式
  → 🔴 Record（預錄 2 秒 + 自動播放 + 錄影至結束）
  → ▶ Analyze（逐幀偵測格線 + OCR 時間校正）
  → 自動產出 .lab + 匯入 LiveChord 播放系統

完成！LiveChord 播放此歌時即時顯示和弦。
```

---

## 工具一覽

| 工具 | 用途 | 啟動方式 |
|------|------|----------|
| **chordify_gui.pyw** | GUI 主工具（錄影+分析+編輯） | 雙擊（無 terminal） |
| chordify_gui.py | 同上（有 terminal 輸出） | `python chordify_gui.py` |
| chordify_ocr.py | 截圖逐格 OCR（備用） | `python chordify_ocr.py <png>` |
| capture_debug.py | 擷取診斷工具 | `python capture_debug.py` |

---

## GUI 面板說明

```
┌─ ChordCurator Studio ─────────────────────────────────────┐
│ ♫ ChordCurator Studio                                      │
├────────────────────────────────────────────────────────────┤
│ ACTIVE COMPOSITION                                         │
│ [Dancing Queen          ] [瀏覽] Level [Lv1]               │
├────────────────────────────────────────────────────────────┤
│ EXTRACTION CONFIGURATION                                   │
│ ▶|| Play/Pause     [Select] [Test]                         │
│ 00:00 Current Time  [Select] [Test]                        │
│ 03:53 Total Length   [Select] [Test]                       │
│ ♫ Chord Scroll       [Select] [Test]                       │
├────────────────────────────────────────────────────────────┤
│ RECORDING & ANALYSIS                                       │
│ [🔴 Record] [▶ Analyze] [⏹ Stop] ☑Auto ☑Top              │
├────────────────────────────────────────────────────────────┤
│ STATUS: ✓ 完成 (121 和弦)                                  │
├────────────────────────────────────────────────────────────┤
│ CHORD OVERVIEW SCREENSHOTS                                 │
│ [📷 Capture] [🗑 Remove] [✅ Stitch+OCR] [📝 Edit Ref]    │
├────────────────────────────────────────────────────────────┤
│ EXTRACTION LOG                                             │
│ 0:01.000  A  📋                                            │
│ 0:03.367  D/A  📋                                          │
└────────────────────────────────────────────────────────────┘
```

---

## V2 錄影 + 離線分析

### 原理

Chordify Chord diagrams 模式：格子水平捲動，方塊固定。
錄影整個播放過程，離線逐幀偵測格線經過方塊的像素變化。

```
錄影中：
  [休止][A  ][   ][   ][D/A][   ][   ][A  ] → 捲動
        ↑ 方塊固定在這裡

分析時：
  像素變化脈衝 = 格線經過 = 新的一拍
  脈衝次數對應 .chords.txt 的拍數序列
  OCR 讀取錄影中的時間顯示做校正
```

### 時間精度

| 方法 | 精度 |
|------|------|
| V1 即時擷取 | ±1-3 秒 |
| **V2 錄影分析** | **±0.5 秒** |

### 流程

```
🔴 Record
  → 預錄 2 秒
  → 自動點擊 Play
  → 30fps 錄影（含時間 + 和弦區域）
  → OCR 偵測歌曲結束 → 自動停止
  → 存為 .recording.avi

▶ Analyze
  → Phase 1: 找播放起點（方塊首次移動）
  → Phase 2: 逐幀偵測像素變化脈衝
     - 脈衝 = 格線經過 = 新的一拍
     - OCR 每 30 幀讀時間做校正
     - 脈衝次數對應 .chords.txt
  → Phase 3: 時間校正 + 匯入 LiveChord
     - 第一個和弦對齊 Chordify 00:01
     - 寫入 data/chords/{hash}.json
```

---

## 和弦來源優先順序

```
播放頁載入和弦時：
  1. data/chords/{hash}.json 有 source="chordify" → 使用（100% 正確）
  2. data/chords/{hash}.json 有 source="btc" → 使用（~41% 正確）
  3. 無和弦 → 顯示「偵測」按鈕（BTC 自動偵測）
  4. BTC 偵測時如果已有 chordify 來源 → 跳過不覆蓋
```

---

## 檔案結構

### 每首歌的資料夾

```
data/test_songs/Lv1/Dancing Queen/
├── Dancing Queen.flac              ← 測試音檔（from NAS）
├── Dancing Queen.chords.txt        ← 和弦序列（手動輸入/OCR）
├── Dancing Queen.recording.avi     ← 螢幕錄影
├── Dancing Queen.lab               ← Ground Truth（時間+和弦）
├── Dancing Queen.capture.txt       ← 擷取時間紀錄
└── Dancing Queen.png               ← Chord overview 截圖（備用）
```

### LiveChord 播放系統

```
data/chords/{hash}.json
{
  "path": "POP/E-POP/ABBA/ABBA - ABBA Gold/Dancing Queen.flac",
  "source": "chordify",     ← "chordify" 或 "btc"
  "key": "A",
  "capo": 0,
  "chords": [
    {"time": 1.0, "end": 3.367, "chord": "A"},
    {"time": 3.367, "end": 5.7, "chord": "D/A"},
    ...
  ]
}
```

---

## Chord Grid Editor

```
┌─ Chord Grid Editor ──────────────────────────────────┐
│                                    [💾 Save] [Close]  │
│     1    2    3    4    1    2    3    4    ...        │
│  1 ║ ·  │ ·  │ ·  │ ·  ║ A  │ ·  │ ·  │ ·  ║ ...   │
│  2 ║D/A │ ·  │ ·  │ ·  ║ A  │ ·  │ ·  │ ·  ║ ...   │
│  3 ║D/F#│ ·  │A/E │ ·  ║ E  │ ·  │ ·  │ ·  ║ ...   │
│  ...                     ↕ 捲動                       │
└───────────────────────────────────────────────────────┘

- 點擊格子 → 輸入/修改和弦名稱
- 空格 · = 延續上一拍
- 藍色粗線 ║ = 小節分隔
- 新建：指定總拍數，打開空白網格
- 字體大小 16px，視窗 1200×800
```

---

## 設定檔

`tools/capture_config.json`（所有歌曲共用）：

```json
{
  "play_btn_region": [825, 539, 108, 117],
  "time_region": [941, 593, 101, 53],
  "duration_region": [2453, 592, 92, 53],
  "chord_region": [725, 788, 1842, 141],
  "last_song": "Dancing Queen",
  "last_level": "Lv1"
}
```

首次設定 4 個擷取區域後，所有歌曲共用（瀏覽器位置固定時）。

---

## 驗證結果（Dancing Queen）

| 指標 | 結果 |
|------|------|
| 和弦數 | 121/123 (98.4%) |
| 和弦名稱 | 100% 正確 |
| 時間精度 | ±0.5 秒 |
| 第一個和弦 | A = 1.0s（精確） |

---

## 版本歷史

| 日期 | 變更 |
|------|------|
| 2026-03-28 | V1: 即時螢幕擷取 + OCR |
| 2026-03-29 | V2: 錄影 + 離線逐幀分析 |
| 2026-03-29 | Chord diagrams 捲動模式 |
| 2026-03-29 | 像素脈衝偵測 + OCR 時間校正 |
| 2026-03-30 | Grid Editor + 手動輸入和弦 |
| 2026-03-30 | 匯入 LiveChord 播放系統 |
| 2026-03-30 | BTC 偵測保護（不覆蓋 chordify） |
