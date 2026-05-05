# NUC 伺服器與 PC 雙環境 AI 旋律擷取升級計畫

本計畫旨在指導開發環境 (PC: RTX 5080) 與正式營運環境 (NUC: 16 Core + AI Boost NPU + Arc 140T) 進行平滑的 AI 加速升級與轉移，透過導入神經網路音高追蹤模型 (Neural Pitch Tracker) 完全取代效能低落的 CPU-bound pYIN，達成 10~50 倍以上的即時運算效能跳躍。

## User Review Required

> [!WARNING]
> **無中斷平滑轉移**: 由於系統處於 Beta 階段且已有真實用戶，所以升級過程絕大部分會在背景 (Shadow Mode) 執行，不影響線上運作。請確認是否滿意這種雙軌並行機制的提議？
> 
> **硬體依賴分流**: 轉移至 NUC 後，NUC 將專屬安裝 `onnxruntime-openvino`，而 PC 使用 CUDA，雙方將維持這份跨平台相容性。

## 環境自動偵測機制 (Cross-Platform Hardware Auto-Detection)

為解決 PC 與 NUC 的架構差異，我們會在核心配置中加入自動偵測：
- **開發機 (PC)**: 偵測路徑 `c:\users\hitea`，自動切換載入 `CUDAExecutionProvider` 匹配您的 RTX 5080 開發。
- **邊緣伺服器 (NUC)**: 偵測路徑 `c:\livechord`，自動尋找 Intel 加速套件並掛載 `OpenVINOExecutionProvider` 給 NPU 或 GPU 處理。

## Proposed Changes

### Configuration & Hardware Detect

#### [MODIFY] config.py
- 新增 `get_env_mode()` 函式以路徑判斷目前實體主機 (PC or NUC)。
- 新增 Feature Flag `ENABLE_NN_MELODY = False`，預設暫時關閉直接給用戶新輸出，保障 Beta 穩定。

### Dependency Management

#### [NEW] requirements_nuc.txt
- 專門定義給 NUC 伺服器的套件：包含 `onnxruntime-openvino`、對應的 openvino 編譯器套件。
#### [NEW] requirements_pc.txt
- 保留原本 CUDA 環境 `onnxruntime-gpu`。

### AI Module (核心萃取器替換)

#### [NEW] backend/ai/melody_extractor_v2.py
- 全新 V2 模組，載入神經網路基準的 Pitch Tracker (預計整合您已裝的 basic-pitch 或更精純的 RMVPE/CREPE 萃取層)。
- 具備硬體自適應推論 (Hardware-Adaptive Inference)：
  * 如偵測到 PC -> 使用 CUDA
  * 如偵測到 NUC -> 使用 OpenVINO
  * 如載入加速失敗 -> gracefully fallback 返回給舊版 pYIN (確保系統不當機)

#### [MODIFY] backend/auto_worker.py (或其他主要背景任務負責檔)
- 導入 **「影子測試 (Shadow Testing)」**。
- 用戶上傳歌曲時，維持呼叫舊的 `librosa.pyin` 回傳結果。
- 在背景觸發一條平行的 V2 線程，默默以神經網路做運算並將耗時、硬體狀態與結果記錄到 Logs 中，方便您檢視。

---

## Open Questions

> [!IMPORTANT]
> 1. 您希望新的 NN 擷取演算法直接以現有的 basic-pitch 架構為骨幹做逐幀擷取？還是考慮引入針對人聲與旋律特化的 RMVPE 模型 (需下載幾十MB的模型檔)？
> 2. NUC 電腦端的 Python 環境是否已經安裝並準備好使用，隨時可以直接切換過去下達 `git pull`？

## Verification Plan

### Automated / Backend Verification
- 在 NUC 端運行背景批次時，檢視終端機 `Execution Provider` 確認 ONNX 成功抓取 Intel NPU 架構。
- 檢查 V1 與 V2 單首歌曲的花費時間差距。

### Manual Verification
- NUC 本機測試：直接在 NUC 打開這份文件讓 Antigravity 接手，執行 `python -m backend.ai.melody_extractor_v2 test_audio.wav` 核對硬體驅動狀態。
