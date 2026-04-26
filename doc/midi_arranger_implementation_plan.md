# MIDI 深度學習與多軌編曲提取計畫

本計畫旨在針對 `U:\MIDI-Library` 中的 7.8 萬首 MIDI 音樂庫，進行深度的節拍分析、多軌 AI 編曲學習與高音質渲染。為了確保不影響 LiveChord 現有系統（Personal / Beta），所有實驗將在獨立的腳本與資料夾中進行，並最終產出 `.mid` 與 `.wav` 供使用者評測。

## User Review Required

> [!WARNING]
> 本計畫將大量使用 CPU 與 GPU 資源（高達 70% 佔用率）。建議在 NUC 或 PC 處於離峰時段時執行大批次的平行處理任務，以免影響線上 Beta 使用者的體驗。

## Open Questions

> [!IMPORTANT]
> 1. **Timbre Transfer 模型選擇**：音色特徵提取部分，是否有偏好的開源模型（例如 Google Magenta 的 DDSP）？還是先以純符號層級 (Symbolic) 的 MIDI Channel 轉換為主？
> 2. **Keyscape 渲染自動化**：Keyscape 屬於 VST 插件，若要在背景自動渲染，通常需要一個命令列 VST Host（例如 `MrsWatson`、`RenderMan` 或是透過 Python 控制 DAW）。目前是否有現成的命令列工具可供呼叫？還是先以 `FluidSynth` (FluidR3_GM.sf2) 作為主力實作？
> 3. **輸出目錄配置**：產出的 `.mid` 與 `.wav` 檔案預計存放在哪裡？建議可以建立一個專屬的 `U:\MIDI-Experiments-Output` 或是放在專案內的 `data/experiments/` 下。

## Proposed Changes

我們將新增一系列獨立的腳本（放置於 `scripts/` 下或獨立的 `experiments/` 目錄），完全不修改現有 `LiveChord` 的核心 API 與排程器。

### Phase 1: 平行處理 (Parallel Processing) - `beat_this` 加速

將原本單線程或低利用率的 `beat_this` 推論改寫為多進程平行架構。

#### [NEW] `scripts/run_beat_this_parallel.py`
- 使用 `concurrent.futures.ProcessPoolExecutor` 開啟 4-8 個獨立的 Worker。
- 負責遍歷 `U:\MIDI-Library`。
- 每一個 Worker 載入 `beat_this` 模型並鎖定部分 GPU 資源（若 VRAM 允許，或依靠 PyTorch 自動調度）。
- 將分析出的節拍與小節線資料寫入獨立的 JSON 檔案（例如 `.beat.json`），確保冪等性 (Idempotent) 以利中斷後接續處理。

#### [NEW] `run_beat_parallel.bat`
- Windows 批次檔，方便快速啟動上述平行處理腳本。

---

### Phase 2: 同步啟動多軌 AI 編曲學習 (Multi-track Learning)

針對已成功辨識的 1,700 首 MIDI 進行特徵提取與 Transformer 模型訓練。

#### [NEW] `backend/ai/neural_arranger.py`
- 建立 Transformer 架構的編曲模型，目標是學習編寫副旋律 (Sub-melodies)。
- 輸入：主旋律、和弦進行、節拍位置。
- 輸出：Bass, Ocarina (陶笛), Sax (薩克斯風), Flute (長笛) 等配器的 MIDI 音符。

#### [NEW] `scripts/extract_timbre_features.py`
- 負責處理音色特徵提取 (Timbre Transfer)。
- 將模型生成的符號層級資料，套用各樂器的演奏表情與力度變化 (Velocity/Expression curve)，並組合產出最終的評測用 `.mid` 檔。

---

### Phase 3: 高品質音源即時渲染 (High-Res Rendering)

將產出的編曲 MIDI 或現有的 1,700 首 MIDI 直接渲染成 `.wav`。

#### [NEW] `scripts/render_midi_to_wav.py`
- 實作 `FluidSynth` 的 Python 包裝器，利用 `FluidR3_GM.sf2` (或指定 SoundFont) 進行快速背景渲染。
- 預留 `Keyscape` 的 VST 命令列呼叫介面。
- 支援批次處理資料夾內的 `.mid` 檔案並轉換成對應的 `.wav` 檔案。

#### [NEW] `run_rendering.bat`
- 啟動背景渲染任務的入口。

## Verification Plan

### Automated Tests
- 針對 `run_beat_this_parallel.py` 進行小批次 (e.g., 10 首) 測試，監控 GPU VRAM 佔用率與處理時間，確認效能提升且不 OOM (Out Of Memory)。
- 驗證 `neural_arranger.py` 的訓練流程能夠正常讀取 MIDI 並完成至少 1 Epoch 的訓練。
- 測試 `FluidSynth` 渲染出單首 `.wav` 的正確性。

### Manual Verification
- 請使用者親自試聽產出的編曲 `.mid` 與渲染後的 `.wav` 檔案，評估副旋律的音樂性與樂器表情 (Bass, Sax 等)。
- 確認上述執行期間，原有的 NUC 個人版 (8800) 與 Beta 版 (8801) 皆能正常運作且不被 Blocking。
