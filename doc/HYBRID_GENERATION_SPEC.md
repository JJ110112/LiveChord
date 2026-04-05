# Hybrid AI Accompaniment Generation Spec
> 版本: 1.0 (2026-04-05)

## 1. 架構概述 (Architecture Overview)

Hybrid AI 伴奏生成系統（Audio-Informed Generation）旨在結合「Audio-to-MIDI 轉錄」的動態律動（Groove）與「Rule-based 生成」的絕對和聲準確度。
傳統單憑和弦生成的伴奏（如琶音）缺乏樂曲原始的呼吸感與律動；而單純的音源轉錄（如 Samplab）雖然律動精確，但常帶有雜音、錯音或受泛音干擾。

本架構取兩者之長：
1. **Stem Extraction**: 先抽取出原始音訊的 Bass 與 Melody 骨架。
2. **Transcription**: 將骨架轉為保有原始 Onset/Duration 的粗糙 MIDI。
3. **LiveChord Sanitization**: 以 `chord_detect.py` 分析出的黃金和弦為準則，過濾並強制校正粗糙 MIDI 的 Pitch。
4. **Hybrid Assembly**: 以校正後的 Bass 為基礎，避開 Melody 音域，生成其餘的伴奏聲部。

## 2. 系統流程 (Pipeline)

### 2.1 源音軌分離 (Source Separation)
- **輸入**: 歌曲音檔 (`song.mp3` 或 `song.wav`)
- **模組**: `backend/ai/stem_separator.py`
- **技術**: `demucs` 
- **輸出**: 獨立的四軌音訊 (`drums.wav`, `bass.wav`, `vocals.wav`, `other.wav`)
  - *為節省空間，目前流程僅保留 `bass.wav` 與 `vocals.wav` 進入下一階段。*

### 2.2 粗糙轉錄 (Raw Transcription)
- **輸入**: `bass.wav` 與 `vocals.wav`
- **模組**: `backend/ai/audio_to_midi_transcriber.py`
- **技術**: `basic-pitch` (Spotify)
- **輸出**: `raw_bass.mid`, `raw_melody.mid`
  - 這些檔案包含人類演奏的精確起始時間 (Onset)、長度 (Duration)、力度起伏 (Velocity curve)，但音高 (Pitch) 可能有 10%~30% 的錯誤率。

### 2.3 和弦淨化過濾器 (Sanitization Layer)
- **輸入**: `raw_bass.mid`, `raw_melody.mid`, `chords.json` (BTC 產出)
- **模組**: `backend/ai/midi_sanitizer.py`
- **邏輯**:
  1. 遍歷所有的 MIDI 音符。
  2. 根據音符的 onset timestamp，反查該時間點的 Chord。
  3. **Bass 淨化規則**: 如果擷取到的 bass pitch 不是和弦根音 (Root) 或和弦內音 (Chord Tones)，且不屬於合理的經過音 (Passing Tone)，則強制將其 Pitch 吸附 (Snap) 到最近的和弦內音。
  4. **Melody 淨化規則**: 主要用於去雜訊。將零碎的、置信度過低的「鬼音 (Ghost notes)」剔除。
- **輸出**: `sanitized_bass.mid`, `sanitized_melody.mid`

### 2.4 混合伴奏生成 (Hybrid Generator)
- **輸入**: `sanitized_bass.mid`, `sanitized_melody.mid`, `chords.json`
- **模組**: `backend/ai/accompaniment_generator.py` (新增 Hybrid 模式)
- **邏輯**:
  1. **Bass 繼承**: 直接沿用 `sanitized_bass.mid` 的律動與音符作為伴奏的最低聲部。
  2. **Melody 避讓**: 若目前旋律頻繁活動於 C4-C5，伴奏和弦 (Chords / Arpeggio) 強制配置於 C3-B3 區間，避免頻率碰撞。
  3. **節奏同步**: 生成的中高頻伴奏音符（如和弦打擊），將參考 Bass 的 Onset 時間，確保合奏的「重拍鎖定 (Groove Lock)」。
- **輸出**: 最終伴奏 MIDI

## 3. 目錄結構與依賴

```text
backend/
├── ai/
│   ├── stem_separator.py         # 負責 Demucs 調用
│   ├── audio_to_midi_transcriber.py  # 負責 Basic Pitch 調用
│   ├── midi_sanitizer.py         # 核心校正演算法
│   └── accompaniment_generator.py# 修改以支援 Hybrid 參數
```

**新增依賴套件**:
- `demucs`
- `basic-pitch`

## 4. 效能與資源管理
- **Batch 限制**: 由於 Demucs 分離較為耗時（CPU 平均需要數十秒），預設不將此流程加入全局掃描的 `Run-SuperWorker.bat`。
- **觸發機制**: 設定為 "On-demand"（使用者按鈕要求）或單獨的慢速 Batch Job (`run_hybrid_extraction_worker.py`)。
