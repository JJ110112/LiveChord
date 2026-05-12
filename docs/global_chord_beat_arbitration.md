# LiveChord 全局和弦與節拍仲裁機制

本文整理 2026-05-12 這一輪 POP 目標優化後的 serve-time 演算法。重點是：儲存在 `V:/data/chords` 的原始 JSON 不一定重寫；player 讀譜時會經過一串保守修復層，最後產生使用者看到的和弦卡、BPM、與小圓點節拍。

## Serve Pipeline

player API 目前的順序如下：

1. `bpm_sanity.maybe_apply_structural_bpm_correction_for_serve`
   - 修正明顯 x2、/2、6/8 被當成快 4/4 等結構性 BPM 錯誤。
   - 只在多個 gate 同時成立時套用，避免把真正快歌誤砍半。

2. `bar_phase_corrector.maybe_correct_for_serve`
   - 用和弦邊界與 beat/downbeat regularity 修正相位。
   - 目標不是重新偵測和弦，而是把 downbeat grid 對齊到比較像樂譜小節線的位置。

3. `global_chord_arbiter.maybe_analyze_global_structure_for_serve`
   - 站在整首歌高度觀察：段落、循環、轉調、2 拍/4 拍文法、長尾、清唱或 rubato 區段。
   - 對高信心區段寫入 `display_beats`、`display_bpm`、`global_arbiter_meta`，必要時重組 serve-time chord cards。

4. `chord_noise_filter.maybe_filter_for_serve`
   - 吸收短雜訊和弦、1 拍尾巴、明顯不符合上下文的小碎片。
   - 若卡片已帶有 `global_arbiter`，代表全局仲裁已做過讀譜切分；noise filter 不應再把這些卡片合併回 long card。

5. `chord_tail_extender.maybe_extend_tail_for_serve`
   - 當歌曲後段仍有 beats 但和弦卡提前結束時，延伸或補足尾段。

6. `chord_splitter.maybe_split_for_serve`
   - 依修正後 downbeats 切開超過一小節的和弦卡。
   - 若全局仲裁已給 `split_display_beats`，切割後保留每張卡應顯示的拍數。
   - 同和弦合併只處理 detector 或舊 auto-split 造成的碎片；帶 `global_arbiter` 的 `4+tail` 切分必須保留，避免 `Cm7(4)+Cm7(1)` 又被黏回 `Cm7(5)`。

前端 `player.js` 會優先尊重 `display_bpm` 與 `display_beats`。舊的 per-song BPM multiplier 不應覆蓋全局仲裁結果；若後端已有 `display_bpm`，player 會清掉 stale `bpm_mult_<song>`。

## 全局仲裁的主要知識

### 1. 4/4 卡片文法

POP 目標先以 4/4 為主。常見規則：

- 一張和弦卡預設代表 4 拍。
- 同一和弦跨 8 拍以上時要切卡，避免小圓點過多或折行。
- 約 5-6 拍的同和弦 hold 若落在穩定 4/4 downbeat grid，優先切成 `4+tail`，而不是顯示成一張 5 或 6 點卡；這讓小圓點速度維持一致，也保留後續樂句切割空間。
- 2 拍和弦通常出現在固定文法中，例如 `G(2) D(2) C(4)` 或 `Fm(2) Eb(2) Db(2) Ab(2) ...`。
- `1+3`、`3+1`、`4+1`、`1+4` 通常是 downbeat phase 或 chord boundary jitter，而不是音樂上的真實切分。

### 2. Display Beats 是「讀譜層」訊號

`display_beats` 不等於原始 chord duration。它表示這張卡在 player 應顯示幾個小圓點。這讓系統可在不破壞原始偵測資料的情況下修正讀譜體驗：

- Perfect Duet：原始 BPM/beat grid 容易半拍密度錯誤，但顯示層應是 63 BPM、4 拍卡。
- Lifetime：12/8 慢板 ballad 可能被 beat tracker 記成約 180 BPM 的三連音 subdivision；全局仲裁會把約 6 個 subdivision 的 downbeat 主群視為半小節，推回約 60 BPM 的 dotted-quarter display tempo。
- 獨上西樓：清唱與獨白段沒有穩定鼓點，但仍有 G 大調循環；後段轉 Ab 大調，需要用全曲文法延續。
- Lover / 夜曲：固定 POP loop 比 local chord detector 更可信。

### 3. BPM 校正與 Display BPM 分離

`bpm` 是儲存或偵測層速度；`display_bpm` 是 player 讀譜速度。兩者分離是必要的：

- 有些歌 beat tracker 會抓到 double time。
- 有些慢歌缺鼓，beat_this 可能抓到 half-density 或 transient tempo。
- 有些演唱會/清唱/rubato 歌曲局部速度不穩，但樂譜讀法仍可穩定。

quality gate 與前端顯示都必須使用 `display_bpm` 作為使用者視角評分基準。

### 4. MIDI 不再是最高品質基準

早期因沒有 ground truth，曾把 Chordify MIDI 當 gold reference。現在不再這樣做：

- MIDI 可作為參考來源，但不應優先覆蓋 BTC/beat_this 的偵測結果。
- `backend/chord_batch.py` 已停用 batch MIDI chord import。
- quality gate 仍記錄 `source=midi` 作為 legacy risk，不把它當作通過品質的證明。

## 目前能力邊界

全局仲裁能大幅修復「規則性 POP」：

- 固定 4/4 loop。
- 2 拍/4 拍混合文法。
- 重複副歌、主歌、前奏循環。
- 常見大小調轉調。
- double/half BPM。
- 同和弦 1+3、3+1、4+1、1+4 碎裂。
- 尾段 missing chords。

仍有極限：

- 無鼓、自由速度、a cappella、古典/爵士 rubato 只能在高信心時修。
- Jazz/Relax/Christmas 的和聲密度與拍號變化比 POP 複雜，不能直接套 POP gate。
- 若原始和弦偵測根音錯得太多，全局仲裁只能修切分與讀譜文法，不能憑空知道正確和聲。
- 每首歌的局部特殊編曲仍可能需要新 pattern，但 pattern 必須升級為可泛化規則，不能只補單曲。
