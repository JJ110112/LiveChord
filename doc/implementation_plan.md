# Implementation Plan: Phase 13.3 - Seq2Seq Transformer 微調與訓練

> **目標**：從 Rule-based （硬邏輯）正式跨入 Deep Learning 時代。我們將訓練第一代的 Transformer Encoder-Decoder 架構，讓它學會把「基本的流行和弦進行」翻譯成「華麗的爵士和弦」。此模型將作為 `Jazzify` 引擎的下世代大腦。

## 執行細節與架構設計

### 1. 建立神經重配訓練器 (`backend/ai/transformer_trainer.py`)
為補足已有的模型骨架 `transformer_reharmonizer.py`，我們需要一支完整的 PyTorch 訓練迴圈。
- **神經蒸餾預處理 (Knowledge Distillation from Rule-Based Engine)**：
  - 依照規格書指示，第一階段 Pre-training 將採用現有的 `reharmonizer.py (Level=3)` 當作我們完美的 Teacher。
  - 將自 `transformer_dataset.json` 從 W 槽讀出後，我會撰寫轉換函式：`[Tokens] -> 構建虛擬 Timeline -> reharmonizer.py -> [Target Tokens]`。
  - 藉由這 40,000 首經過 Rule-based 高壓重配的曲目，做為解答標籤 (Ground Truth) 餵給模型。
- **PopJazzDataset 資料集封裝**：
  - 把 Source (原曲 Token) 和 Target (Teacher 重配 Token) 切割為訓練集 (90%) 與驗證集 (10%)。
  - 套用 PAD (補齊長度)、SOS (開始標籤)、EOS (結束標籤)。序列最大長度將設為 `MAX_LEN=128` 或 `256`。
- **訓練與驗證 (Training Loop)**：
  - 選用 `CrossEntropyLoss` (忽略 PAD index) 作為損失函數。
  - `AdamW` Optimizer 搭配 Learning Rate Scheduler (Warmup)。
  - 每過一個 Epoch 即會輸出最新模型的 `Perplexity (困惑度)`，驗證神經網路是否順利收斂。
  - 最佳權重落地儲存：`W:\data\models\transformer_jazzify.pth`。

### 2. 生產環境部署與 `reharmonizer.py` 整合
- 訓練完成後，我們將在現有的 `backend/ai/reharmonizer.py` 中進行接軌。
- 當使用者在 UI 選擇 `mode="transformer"`，後端將不再走過去那些 `if/else` 的硬規則，而是套用 `transformer_reharmonizer.py` 的 `greedy_decode` 將和弦生出來。
- **Viterbi 終極防護網**：如規格書所述，AI 雖然充滿靈性但也可能產生幻覺 (Hallucination)。所以當 Transformer 推論出包含小二度碰撞的奇怪和弦時，原有的 Viterbi Emission 仍會介入，將和弦歸回安全的旋律線上！

## User Review Required
> [!IMPORTANT]
> 1. **訓練效能估計**：讓 `reharmonizer.py` 即時去生出 40K 首標籤解答會極其耗時，為了效率我會在腳本加入一個**「離線標記 (Offline Labelling) 快取」機制**。亦即先利用 32 執行緒預先產生並儲存標註檔 (`W:\data\models\teacher_labels.json`)，再來做 Epoch 訓練。請問您同意這項加速設計嗎？
> 2. **運算設備**：腳本遇到 NUC 與 RTX 5080 時會自動鎖定 GPU (`cuda`) 進行張量運算。這部分需要等模型寫完真正上機測試才知道速度，目前我都先封裝在標準的 PyTorch Device 動態綁定中。

## 驗證計畫
- **訓練損失驗證**：看能否在 5 個 Epoch 內觀察到訓練 Loss 急速降溫以及 Perplexity < 5.0 甚至更低。
- **替換實測**：透過自訂輸入（例如經典的 `C - Am - F - G`），直接測試加載 `.pth` 權重後的 Transformer 是否自動填入了類似 `Cmaj9 - A7b9 - Dm9 - G13` 之類的華麗翻譯。

---

## 階段完工總結 (Walkthrough)

> 本階段所有源碼已順利落實於 backend 內。

1. **神經訓練師上線 (`transformer_trainer.py` 撰寫完畢)**：
   - 採用 `PopJazzDataset` 的 DataLoader 封裝，順利將 `Source (Pop Sequence)` 與 `Target (Jazzified Sequence)` 配對。
   - `offline_teacher_labeling` 已掛接 32 執行緒的 `ThreadPoolExecutor`，這讓這隻神經模型將在訓練前，能從 LiveChord 的傳統 `reharmonizer.py (Level=3)` 身上，以飛快的效率榨取 4 萬首爵士和弦當作標籤解答 (Knowledge Distillation)！
   - 完美銜接 PyTorch 的 `CrossEntropyLoss` 與 `AdamW`，將確保權重最終落回 `transformer_jazzify.pth` 大腦。

2. **引擎整合接軌 (`reharmonizer.py` inference 完成實作)**：
   - 先前遺留的 `mode="transformer"` 現在已經不再是紙上談兵！
   - 當模型上線後，系統將自動套用 `tokenize_song (標準化 C大調)` -> `greedy_decode (Seq2Seq 推論生成)` -> `逆向反推算 (DUR 解析與反移調)`。
   - 在還原成 LiveChord 時軸字典後，原本的 **Viterbi Emission 旋律避撞層** 將會發揮最堅強的安全護盾作用，防止 AI 幻覺亂彈跟旋律打架的離譜錯音。

若您已備妥機器算力，只需讓您的 NUC (含 RTX 5080) 跑起 `python transformer_trainer.py`，它就會開始將這 40,000 首流行樂反覆翻盤、千錘百鍊，成為 LiveChord 傲視群雄的次世代人工大腦！🚀
