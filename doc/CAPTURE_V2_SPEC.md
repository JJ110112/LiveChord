# ChordCurator V2 — 影片錄製 + 離線分析規格書

> 版本: 2.0 | 日期: 2026-03-29
> 解決: 即時 OCR 擷取的時間軸誤差問題

---

## 1. 問題分析

### 現有方案的根本缺陷

即時擷取（V1）在 30ms 迴圈中同時做截圖 + 像素追蹤 + OCR + 時間計算，
導致多個不可控的時間誤差來源：

| 誤差來源 | 影響 | 程度 |
|----------|------|------|
| pyautogui.click() 延遲 | 播放起點不精確 | ~0.5-1.5s |
| OCR 處理時間不均勻 | 格子邊界判斷延遲 | ~0.1-0.3s |
| 格子邊界座標偏移 | GIMP 參照圖 vs 實際截圖 | ~7px → ~0.06s |
| 滯後 (hysteresis) 設定 | 觸發點不在格子起始位置 | ~0.2s |
| perf_counter 累計漂移 | 長歌曲時間軸偏移 | ~0.5s/3min |
| Producer-Consumer 延遲 | Buffer 排隊等待 | ~0.05-0.2s |

**累計誤差: 1-3 秒，且不可預測。**

### 新方案的核心思路

**把「擷取」和「分析」完全分離。**

```
V1 (即時): 截圖 → 分析 → 記錄  (同時進行，互相干擾)
V2 (錄影): 錄影 → 結束 → 逐幀分析  (先錄後分析，零干擾)
```

---

## 2. 新方案流程

```
┌─────────────────────────────────────────────────────────┐
│                     Phase 1: 錄影                        │
│                                                         │
│  1. 選擇歌曲 + 等級                                      │
│  2. 選擇 Chordify 畫面區域（含時間 + 和弦網格）            │
│  3. 按「Start Recording」                                │
│  4. 預錄 2 秒（記錄空白基準幀）                            │
│  5. 自動點擊 Chordify Play                               │
│  6. 錄影整個播放過程（螢幕錄影，~30fps）                    │
│  7. 偵測播放結束 → 停止錄影                               │
│  8. 存為 .mp4 或 .avi                                    │
│                                                         │
│  產出: {song}.recording.avi                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                  Phase 2: 離線分析                        │
│                                                         │
│  1. 載入 .recording.avi                                  │
│  2. 逐幀分析:                                            │
│     a. 每幀偵測高亮方塊位置 (cx, cy)                      │
│     b. 用 beat_boundaries 判斷在第幾格                    │
│     c. 格子變化 → 記錄 frame_number → 精確時間            │
│        時間 = frame_number / fps                         │
│     d. 不需要 OCR（和弦名稱從 .chords.txt 取）            │
│  3. 對齊 .chords.txt 的節拍序列                          │
│  4. 產出 .lab（精確時間 + 正確和弦）                       │
│                                                         │
│  產出: {song}.lab                                        │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 技術細節

### 3.1 螢幕錄影

```python
import cv2
import mss
import numpy as np

def record_screen(region, output_path, fps=30):
    """錄製螢幕指定區域"""
    x, y, w, h = region
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))

    with mss.mss() as sct:
        monitor = {"left": x, "top": y, "width": w, "height": h}
        while recording:
            frame = sct.grab(monitor)
            img = np.array(frame)[:, :, :3]  # BGRA → BGR
            out.write(img)
            # 控制幀率
            time.sleep(1.0 / fps)

    out.release()
```

**錄影區域**: 包含時間顯示 + 和弦網格（一次錄整個 Chordify 播放區域）

**幀率**: 30fps → 每幀 33ms → 時間精度 ±33ms（遠優於 V1 的 ±1-3s）

**檔案大小估算**:
- 區域 ~1830×1200 px, 30fps, 4min
- 壓縮後 ~50-100MB/首

### 3.2 預錄 2 秒

錄影開始後等 2 秒再點擊 Play，這 2 秒用來：
1. 記錄「靜止畫面」的基準（方塊還沒動）
2. 確認錄影正常運作
3. 之後分析時，偵測到**第一次方塊移動的幀** = Play 的精確時間

```python
# 預錄期間方塊不動 → 記錄基準位置
baseline_center = None  # 靜止時的方塊位置

# 第一次移動 = 播放開始
play_start_frame = first_frame_where(center != baseline_center)
play_start_time = play_start_frame / fps  # 精確到 33ms
```

### 3.3 逐幀分析（離線）

```python
def analyze_recording(video_path, beat_bounds, chords_txt):
    """逐幀分析錄影，產出精確時間軸"""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)

    current_grid = -1
    ref_idx = 0
    results = []

    frame_num = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # 偵測高亮方塊位置（純像素，~1ms）
        center = find_dark_block_center(frame)

        if center:
            grid = cx_to_grid(center[0], beat_bounds)

            if grid != current_grid:
                # 格子變化 → 記錄時間
                time_sec = frame_num / fps
                chord = chords_txt[ref_idx] if ref_idx < len(chords_txt) else ""
                ref_idx += 1

                if chord:  # 非空拍
                    results.append((time_sec, chord))

                current_grid = grid

        frame_num += 1

    cap.release()
    return results
```

**關鍵優勢**：
- `frame_num / fps` = 精確到 1 幀的時間（33ms）
- 不受 OCR 延遲影響（離線分析，不趕時間）
- 不需要 perf_counter（用幀數計算，零漂移）
- 可以反覆分析同一段錄影，調整參數

### 3.4 時間精度比較

| 方案 | 時間精度 | 誤差來源 |
|------|---------|---------|
| V1 即時擷取 | ±1-3 秒 | OCR 延遲 + perf_counter 漂移 + 格子偏移 |
| V2 錄影分析 | ±33 ms | 僅受幀率限制（30fps → 33ms/frame） |
| V2 @ 60fps | ±17 ms | 更高幀率可更精確 |

---

## 4. GUI 改動

### 4.1 新增控制

```
┌─ Recording ────────────────────────────────────────────┐
│ [🔴 Start Recording]  [⏹ Stop]  FPS:[30▼]             │
│ Status: Recording... 2:45 / 3:53  [████████░░] 73%    │
└────────────────────────────────────────────────────────┘

┌─ Analysis ─────────────────────────────────────────────┐
│ [▶ Analyze Recording]  [📝 Edit Ref]                   │
│ Status: Analyzing frame 4500/7200...  [██████░░░] 62%  │
└────────────────────────────────────────────────────────┘
```

### 4.2 工作流程（使用者操作）

```
1. 輸入歌名 + 等級
2. 確認擷取區域已設定（沿用 V1 的 config）
3. 確認 .chords.txt 已準備好（Stitch+OCR → Edit Ref 修正）
4. 按 🔴 Start Recording
   → 預錄 2 秒
   → 自動點擊 Play
   → 錄影中...（顯示進度）
   → 偵測結束 → 自動停止
5. 按 ▶ Analyze Recording
   → 逐幀分析（離線，~30 秒完成 4 分鐘歌曲）
   → 自動對齊 .chords.txt
   → 產出 .lab
6. 完成！
```

---

## 5. 檔案結構

```
data/test_songs/Lv1/
├── Dancing Queen.flac              ← 測試音檔
├── Dancing Queen.png               ← Chordify 截圖（OCR 基準）
├── Dancing Queen.chords.txt        ← OCR 和弦序列（手動校正後）
├── Dancing Queen.recording.avi     ← 螢幕錄影（新增）
├── Dancing Queen.lab               ← Ground Truth（最終產出）
├── Dancing Queen.capture.txt       ← V1 擷取紀錄（保留相容）
└── Dancing Queen.capture-screenshots.png  ← V1 自動截圖
```

---

## 6. 相依套件

```
已有: mss, numpy, PIL, easyocr, pyautogui, pynput, cv2
新增: 無（cv2 已安裝）
```

---

## 7. 實作優先順序

| Phase | 工作 | 預估 |
|-------|------|------|
| 1 | 螢幕錄影功能（record_screen） | 30min |
| 2 | 離線逐幀分析（analyze_recording） | 30min |
| 3 | GUI 整合（Recording + Analysis 面板） | 30min |
| 4 | 測試驗證（Dancing Queen） | 15min |
| 5 | 比對 V1 vs V2 結果 | 15min |

**總計: ~2 小時**

---

## 8. 風險評估

| 風險 | 影響 | 因應 |
|------|------|------|
| 錄影檔太大 | 磁碟空間 | 用 XVID 壓縮，分析完可刪除 |
| 30fps 不夠快 | 極快歌曲漏幀 | 可調高到 60fps |
| 錄影區域包含其他視窗 | 分析失敗 | 使用者需確保無遮擋 |
| 分析時間太長 | 使用者等待 | 顯示進度條，可背景處理 |

---

## 9. 與 V1 的共存

V2 不取代 V1，而是新增一個更精確的模式：

```
V1 (即時): 快速取得大致時間軸（適合初步測試）
V2 (錄影): 精確到 33ms 的時間軸（適合 Ground Truth 建立）
```

GUI 上可用 Tab 或 Radio 切換模式。
