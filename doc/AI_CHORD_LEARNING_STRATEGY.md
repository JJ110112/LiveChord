# LiveChord: AI Chord Learning Strategy
**人工智慧和絃學習與預測策略白皮書**

此文件為 LiveChord 專案後續訓練 AI 模型處理、學習與預測音樂和弦（Chord Progression）之深度技術藍圖與策略。

---

## 壹、核心挑戰與目標 (Core Challenges & Goals)
1. **調性歸一化 (Key Invariance)**：同樣的進行（I - IV - V）在 C 大調是 `C-F-G`，在 E 大調是 `E-A-B`。AI 不應分別死記，必須學會在「相對音程」上思考。
2. **多尺度學習 (Multi-Scale Learning)**：
    *   **微觀 (Micro-Level)**：相鄰兩三個和弦的合理轉換（如 ii -> V -> I 或 Triton 取代）。
    *   **宏觀 (Macro-Level)**：整首歌的曲式結構（如 主歌 -> 導歌 -> 副歌 的情緒張力漸進）。
3. **律動與時值 (Timing & Duration)**：和弦不是只有「順序」，還有「停留時間」。正確的發聲時機，是決定曲風（Jazz vs Pop）的關鍵。

---

## 貳、特徵工程與資料表示法 (Data Representation)

在將 78,000 首混合歌單 (`Z:\`) 與 3,000 首 Jam Tracks (`Z:\Jam`) 餵給模型前，必須制定統一的 Token 化策略。

### 1. 和弦詞彙表 (Chord Vocabulary / Tokenization)
我們將採用類似 NLP (自然語言處理) 的思維：將每個和絃視為一個「單字 (Word)」。
*   **Root (根音)**: 12 個半音 (C, C#, D...)。
*   **Quality (和弦性質)**: maj, min, maj7, min7, 7, dim7, m7b5, aug, sus4 等。
*   為減少維度，系統會在前處理時將所有歌曲**強制移調至 C 大調 / A 小調 (C Major / A Minor)**。此舉可將無限變化的組合縮減至約 60-100 個常用核心 Token。

### 2. 時序編碼 (Temporal Encoding)
拋棄單純的 List，改為包含時值特徵的序列結構：
*   **連續 Frame 法**：每 0.5 秒切一個 Frame，C 停留 2 秒表示為 `[C, C, C, C]`。優點是能完美結合深度學習卷積。
*   **Event-based 法 (推薦)**：將指令編碼為 `<Chord_Cmaj7> <Duration_2_Beats>`。此法節省 Token 長度，極度適合 Transformer 學習。

---

## 參、模型架構選型藍圖 (Model Architectures)

依據專案階段與資料量，團隊將採用循序漸進的模型架構：

### 階段一：精準律動與基礎機率 (Markov Chains & Jam Track Dictionary)
*   **適用資料**：3,000 首 Jam Tracks
*   **策略**：建立 Bigram / Trigram 的機率轉移矩陣 (Transition Matrix)。利用 Jam Tracks 產生無雜訊的「風格和絃字典 (Groove Dictionary)」。
*   **優勢**：極度輕量、不需要 GPU 推論、給出的建議 100% 符合樂理常識。可以在第一天就實裝進系統。

### 階段二：和絃嵌入表示法 (Chord2Vec 模型)
*   **適用資料**：78,000 首全資料集
*   **策略**：使用 Word2Vec (Skip-gram) 演算法訓練。
*   **優勢**：模型能學會「相似概念」。例如：發現 `Dm7` 跟 `Fmaj7` 距離很近（因為組成音高度重疊），當需要改變編曲豐富度時，AI 能透過計算 Cosine Similarity 推薦最好的「代理和絃 (Substitute Chords)」。

### 階段三：序列生成與曲風轉換 (Transformer / Seq2Seq)
*   **適用資料**：78,000 首全資料集
*   **策略**：採用類似 GPT (Decoder-only) 或 T5 (Encoder-Decoder) 的自注意力架構。
*   **應用一：Next-Chord Prediction**：給定前 4 節的進行，AI 自動續寫接下來 8 小節。
*   **應用二：Style Transfer (Reharmonization)**：Encoder 讀入 `[C, Am, F, G]` (Pop Style)；Decoder 輸出 `[Cmaj9, A7b13, Fmaj7, G7#9]` (Jazz Style)。此層級將由多智能體的 PM 及 Data RD 協同調試。

---

## 肆、驗證與評測機制 (Evaluation Metrics)

為避免 AI 產出「難聽」的音樂，我們需導入以下評估標準（由 QA Agent 或自動化化腳本把關）：

1.  **困惑度 (Perplexity, PPL)**：衡量模型對和絃序列的預測信心，數值越低代表模型越能掌握該風格的語言。
2.  **音高重疊率 (Pitch-Class Overlap)**：在做和絃替換 (Reharmonization) 時，檢驗原和絃與 AI 產生之新和絃的組成音是否具備適當的共同音 (Common Tones)，避開不合理的極端離調。
3.  **旋律相容性檢測 (Melody Constraint)**：若原曲有主旋律，AI 放上的和弦必須確保旋律音不會撞到和弦的「避用音 (Avoid Notes)」（例如大本位和弦遇到小二度音）。

---

## 伍、未來目標與願景 (Roadmap)
1. **Q1 目標**：實作 `Chord2Vec` 與機率矩陣，讓 LiveChord 具備「智慧和絃建議」提示燈光或按鈕。
2. **Q2 目標**：完成 `Jazzify` 轉換引擎，並發布流行轉特定曲風 (Funk/Jazz/Neo-Soul) 的重配和聲 API。
3. **Q3 目標**：導入外部大語言模型（如 Google Ultra 或 Claude），賦予系統「解讀人類情緒指令」的能力（如：「幫我把這首歌變得更憂鬱、更有都會感」）。

---

## 陸、多智能體協作 Prompts 庫 (Multi-Agent Prompts)

為加速後續模型建置與架構實作，請在 LangSmith / LangGraph 框架中注入以下系統提示詞，啟動 AI 開發團隊：

### 🧑‍💼 1. Product Manager & Music Theorist (音樂理論家)
**職責**：將流行音樂分析為功能和聲，建立訓練與替換的樂理規則基礎。
```text
# Role: AI Chord Learning Product Manager & Music Theory Expert
You are an expert Music Theorist and Product Manager. Your goal is to guide the engineering agents in building an AI chord progression engine.

# Responsibilities:
1. Define the rules for translating absolute chords into normalized tokens (e.g., C -> I, Am -> vi).
2. Evaluate the "Musicality" of the AI's predictions. If the AI suggests a highly unusual chord, explain the theoretical justification or flag it as an anomaly.
3. Design the loss function weightings (e.g., heavily penalize wrong bass notes vs. mildly penalize missing 7th extensions).
```

### 👨‍💻 2. Data & MIR Engineer (資料處理工程師)
**職責**：開發 `Z:\` 與 `Z:\Jam` 的資料萃取管線，清洗資料並建立字典。
```text
# Role: Music Information Retrieval (MIR) & Data Engineer
You are a senior Data Engineer specializing in MIR. We have 78,000+ mixed audio/MIDI tracks and 3,000 Jam tracks.

# Responsibilities:
1. Write Python scripts to batch-process tracks, extracting `(time, end, chord)` segments.
2. Build data normalization pipelines to transpose all extracted sequences into C Major / A Minor.
3. Train the initial Bigram/Trigram sequence probability matrices using the Jam Tracks.
4. Export the resulting "Groove Dictionary" to SQLite or JSON.
```

### 👨‍💻 3. ML/Backend Engineer (機器學習後端工程師)
**職責**：訓練 `Chord2Vec` 或 Seq2Seq 模型，並開放 API。
```text
# Role: Machine Learning & Backend API Engineer
You are a proficient AI/Backend Developer.

# Responsibilities:
1. Using the normalized sequences from the Data Engineer, build a Word2Vec or Transformer model implementation in Python (PyTorch/TensorFlow).
2. Implement next-chord prediction inference endpoints.
3. Expose the prediction and generation logic via FastAPI (`/api/ai/predict_chord`).
4. Ensure inference latency is under 50ms for real-time editor suggestions.
```

### 🕵️ 4. Harmony QA (和聲驗證員)
**職責**：驗證模型輸出的和絃是否正確，避免「音樂上的Bug」。
```text
# Role: Harmony Quality Assurance
You are an uncompromising QA Engineer with a perfect pitch background. You test the outputs of the AI Chord Models.

# Responsibilities:
1. Perform statistical tests on the generated outputs (e.g., checking Perplexity and Pitch-Class Overlap constraints).
2. Write automated tests asserting that common progressions (like ii-V-I) are correctly predicted by the basic models.
3. Block merging any model version that outputs functionally clashing triads given a simple constraint.
```
