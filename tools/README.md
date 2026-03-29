# LiveChord 工具集

> Ground Truth 擷取與和弦辨識工具

---

## 快速開始

### 最佳流程（推薦）

```
步驟 1: 截圖 OCR（取得準確和弦名稱）
  雙擊 chordify_gui.pyw
  → 框選一列 → 設定拍數（如 4小節×4拍=16拍）
  → 📷 框選擷取（多頁截圖）
  → ✅ 拼接儲存+OCR
  → 產出 .chords.txt（準確和弦序列）+ .png（拼接截圖）

步驟 2: 即時擷取（取得精確時間軸）
  → ▶ 開始擷取（自動載入 .chords.txt 為參照）
  → 程式自動播放 + 追蹤方塊移動 + 綁定時間
  → 產出 .lab（準確和弦 + 精確時間）
```

---

## 工具一覽

| 工具 | 用途 | 啟動方式 |
|------|------|----------|
| **chordify_gui.pyw** | GUI 擷取終端（主力工具） | 雙擊（無 terminal） |
| chordify_gui.py | 同上（有 terminal 輸出） | `python chordify_gui.py` |
| chordify_capture.py | 命令列擷取 | `python chordify_capture.py` |
| chordify_ocr.py | 截圖逐格 OCR | `python chordify_ocr.py <png>` |
| chordify_screenshot.py | 單張截圖分析 | `python chordify_screenshot.py <png>` |
| capture_debug.py | 擷取診斷工具 | `python capture_debug.py` |

---

## GUI 擷取工具 (chordify_gui)

### 面板說明

```
┌─ Chordify Ground Truth 擷取工具 ─────────────────────────────┐
│                                                               │
│ 歌曲: [Dancing Queen    ] [瀏覽...] 等級: [Lv1 ▼]            │
│                                                               │
│ ┌─ 擷取區域 ──────────────────────────────────────────────┐   │
│ │ 播放按鈕 ▶/||:  x=825,y=539...  [框選] [測試]          │   │
│ │ 目前時間 00:00:  x=941,y=593...  [框選] [測試]          │   │
│ │ 總長度 03:53:    x=2453,y=592... [框選] [測試]          │   │
│ │ 和弦網格區域:    x=734,y=913...  [框選] [測試]          │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                               │
│ [▶ 開始擷取] [⏹ 停止] ☑ 自動覆蓋                             │
│                                                               │
│ ┌─ 樂譜截圖（多頁拼接）──────────────────────────────────┐   │
│ │ [⬜ 框選一列] 拍:[4▼] 小節:[4▼] 4小節×4拍=16拍 115px   │   │
│ │ [📷 框選擷取] [🗑 移除最後一張] [✅ 拼接儲存+OCR]       │   │
│ │ ┌────┐ ┌────┐ ┌────┐                                   │   │
│ │ │ #1 │ │ #2 │ │ #3 │  ← 縮圖預覽                       │   │
│ │ └────┘ └────┘ └────┘                                   │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                               │
│ ✓ 完成 (123 和弦)                                             │
│ 和弦: 123/123 | 時間: 3:45.435                                │
│                                                               │
│ ┌─ 擷取紀錄 ──────────────────────────────────────────────┐   │
│ │ 0:00.000  A       📋                                    │   │
│ │ 3:42.989  D/A     📋                                    │   │
│ │ 5:46.123  A       📋                                    │   │
│ └─────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

### 首次設定

1. 開啟 Chordify 歌曲頁面（Chord overview 模式）
2. 雙擊 `chordify_gui.pyw`
3. 框選 4 個區域（座標會記住，之後不需要重選）：
   - 播放按鈕（▶/|| 圓形按鈕）
   - 目前時間（00:00 數字）
   - 總長度（右上角 03:53）
   - 和弦網格（高亮方塊移動的區域）
4. 框選一列 → 設定拍號（每小節幾拍 × 每列幾小節）

### 擷取模式

#### 📋 參照模式（推薦，100% 準確）

先截圖 OCR 產生 `.chords.txt`，再播放時只綁定時間：

| 步驟 | 操作 | 產出 |
|------|------|------|
| 截圖 | 📷 框選擷取（多頁） | 截圖清單 |
| OCR | ✅ 拼接儲存+OCR | .chords.txt + .png |
| 擷取 | ▶ 開始擷取 | .lab（自動載入參照） |

Log 顯示 `📋` 表示和弦來自參照序列（0ms，100% 準確）。

#### 🔍 即時 OCR 模式（無截圖時的 fallback）

直接按 ▶ 開始擷取，即時 OCR 辨識和弦：

Log 顯示 `🔍` 表示和弦來自即時 OCR（~200ms，~85% 準確）。

---

## 架構：Producer-Consumer Buffer

```
Producer (30ms loop):                     截圖不被 OCR 阻塞
  screenshot → pixel tracking (~3ms)
  box moved? → queue.put(img, time, jump)
                    ↓
              Buffer (Queue 30)
                    ↓
Consumer (OCR thread):                    按需處理
  queue.get() → 📋 ref or 🔍 OCR → record

Time OCR (every 2s):                      獨立校時
  screenshot time → OCR → update base
```

| 線程 | 頻率 | 工作 | 延遲 |
|------|------|------|------|
| Producer | ~30Hz | 截圖 + 像素追蹤 | ~13ms |
| Consumer (📋) | ~30Hz | 查表綁定 | ~0ms |
| Consumer (🔍) | ~5Hz | OCR 辨識 | ~200ms |
| Time OCR | 0.5Hz | 校時 | ~200ms |

---

## 設定檔

`tools/capture_config.json`（所有歌曲共用）：

```json
{
  "play_btn_region": [825, 539, 108, 117],
  "time_region": [941, 593, 101, 53],
  "duration_region": [2453, 592, 92, 53],
  "chord_region": [734, 913, 1829, 1110],
  "row_height": 112,
  "row_width": 1835,
  "beats_per_bar": 4,
  "bars_per_row": 4,
  "beats_per_row": 16,
  "last_song": "Dancing Queen",
  "last_level": "Lv1"
}
```

瀏覽器視窗位置固定時，換歌只需改歌名。

---

## OCR 修正表

EasyOCR 常見誤讀和修正（`_OCR_FIX`）：

| 誤讀 | 正確 | 原因 |
|------|------|------|
| DA, DIA, DJA | D/A | `/` 讀成 `I/J` |
| DFA, D/F | D/F# | `#` 消失 |
| Fim, Frm, Fum | F#m | `#` 讀成 `i/r/u` |
| C37, Ci7, Cz7 | C#7 | `#` 讀成 `3/i/z` |
| B7/D3, B7/Di | B7/D# | `#` 讀成 `3/i` |
| E/Gi, E/Gu | E/G# | `#` 讀成 `i/u` |
| Bmi/E, Bme/E | Bm7/E | `7` 讀成 `i/e` |

---

## 產出檔案

每首歌最多產生 4 個檔案：

| 檔案 | 格式 | 來源 | 用途 |
|------|------|------|------|
| `歌名.lab` | JSON | 即時擷取 | Ground truth（和弦+時間） |
| `歌名.chords.txt` | TSV | 擷取 or OCR | 和弦序列 + 時間（對照用） |
| `歌名.png` | PNG | 截圖拼接 | Chordify 樂譜截圖 |
| `歌名.flac` | FLAC | NAS 複製 | 測試音檔 |

### .lab 格式

```json
{
  "song": "Dancing Queen",
  "level": "Lv1",
  "key": "A",
  "source": "Chordify (GUI capture)",
  "entries": [
    {"time": 0.0, "end": 3.469, "chord": "A"},
    {"time": 3.469, "end": 5.465, "chord": "D/A"},
    ...
  ]
}
```

### .chords.txt 格式

```
0.000	A
3.427	D/A
5.499	A
...
```

---

## 診斷工具 (capture_debug.py)

用於排查擷取問題：

```
python capture_debug.py
選擇 (1-5/a):
  1. 區域擷取截圖      ← 確認框選正確
  2. 播放按鈕偵測 (10s) ← 確認 ▶/|| 辨識
  3. 時間 OCR (7.5s)    ← 確認時間讀取成功率
  4. 和弦偵測 (6s)      ← 確認高亮方塊追蹤
  5. 完整擷取模擬 (30s) ← 模擬完整擷取流程
```

截圖存在 `tools/debug_output/`。

---

## 已知限制

| 限制 | 說明 | 因應 |
|------|------|------|
| OCR `#` 辨識差 | EasyOCR 常把 `#` 讀成 `i/3/z` | _OCR_FIX 修正表 |
| 極快裝飾和弦 | <0.2s 的和弦可能被 Producer 跳過 | 漏拍補償 + ref 序列 |
| 螢幕解析度變化 | 區域座標失效 | 重新框選 |
| Chordify 改版 | UI 佈局變化 | 重新框選 |

---

## 版本歷史

| 日期 | 變更 |
|------|------|
| 2026-03-28 | 初版：螢幕擷取 + OCR |
| 2026-03-29 | Producer-Consumer buffer 架構 |
| 2026-03-29 | 逐格 OCR + 格線偵測 |
| 2026-03-29 | 📋 參照模式（.chords.txt → .lab）|
| 2026-03-29 | 修正連鎖漏拍 + Race Condition + Deadlock |
