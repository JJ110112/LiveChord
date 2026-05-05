# Phase 2: Multi-track REMI 與 AI 編曲架構升級計畫

這個計畫旨在根據商用級編曲 AI 架構建議，全面升級 Phase 2 的訓練環境與資料表徵。我們將從原本的雛型，升級為 **Multi-track REMI + Prefix Role Conditioning + Chord Masking**，確保模型生成 100% 和諧的編曲。

## Proposed Changes

### Tokenization & Data Pipeline

#### `remi_tokenizer.py`
建立專為「編曲 AI」打造的 Multi-track REMI 詞表與轉換工具。
- **功能 1：資料清洗與正規化 (Data Cleaning)**
  - 強制將 MIDI 量化 (Quantize) 到 1/16 grid。
  - 將所有歌曲 Key normalization 到 C Major / A Minor，極大化降低學習難度。
- **功能 2：詞表建構 (Vocabulary Design)**
  - **結構 Token**: `[BAR]`, `[POS_0]` ~ `[POS_15]`
  - **和弦 Token**: `[CH_C:maj]`, `[CH_G:7]` 等。
  - **音符 Token**: `[PITCH_21]` ~ `[PITCH_108]`, `[DUR_1]` ~ `[DUR_64]`
  - **軌道 Token**: `[TRK_MELODY]`, `[TRK_BASS]`, `[TRK_SAX]`
  - **律動 Token**: `[BEAT_STRONG]`, `[BEAT_WEAK]`
- **功能 3：Prefix Conditioning 組合**
  - 將輸入資料封裝為：`[CHORD_SEQ] [BEAT_SEQ] [TRK_MELODY] ... [TARGET_ROLE=BASS] <GENERATE> [PITCH...]`

---

### Neural Model & Training Skeleton

#### `neural_arranger.py`
全面升級模型骨架以支援 RTX 5080 的硬體優勢與解碼約束。
- **模型架構升級 (Decoder-only Transformer)**
  - 改用 Decoder-only 結構以完美契合 Prefix Conditioning。
  - 參數配置：`d_model=768`, `n_heads=12`, `layers=12`, `seq_len=1024`。
  - 啟用 PyTorch AMP (`fp16=True`) 加速訓練與降低 VRAM 佔用。
- **解碼約束引擎 (Constrained Decoding Engine)**
  - 實作 `apply_chord_mask(logits, current_chord)` 函式。
  - 在 Inference 的 sampling 迴圈中，根據當前時間點的 `[CH_xxx]` 狀態，強迫將非和弦內音（或非過渡音）的 Logits 設為 `-inf`，從物理層面保證 100% 不彈錯音。

## Verification Plan

### Automated Tests
1. **Token 化驗證**：拿一首已經有 `beat.json` 的 `.mid`，透過 `remi_tokenizer.py` 輸出為 Token 序列，肉眼檢查 `[TRK_MELODY]` 與 `[TRK_BASS]` 是否正確交織，以及 Key 是否正確轉到 C大調。
2. **模型 Forward 驗證**：產生假 batch 資料丟入升級後的 `neural_arranger.py`，確認 VRAM 佔用與 FP16 計算圖是否正常運作。
3. **Masking 驗證**：手動輸入 `current_chord="C:maj"`，檢查輸出的 `logits` 中，`C#` 與 `D#` 是否已被成功 mask 掉（數值為 `-inf`）。
