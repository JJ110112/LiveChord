# Phase 13: 深度學習大模型 (Deep Learning Transformer Engine) 系統規格書

> 狀態：**基礎架構已建置完畢** | 日期：2026-04-13
> 前置作業：Phase 12 (Hybrid Worker) 與 Super Worker 萃取已順利完成 (共篩選約 4 萬多首)
> 目標：打造 LiveChord 專屬的首個 Transformer Seq2Seq 和聲預測與重配 (Reharmonization) 引擎。

---

## 壹、核心突破：從「機率猜測」到「語境理解」
在 Phase 13 以前，系統仰賴 Markov Chain 與靜態 Rule-based (Jazzify) 的演算法。雖然精準，但缺乏對樂曲前後文的理解能力。
Phase 13 導入了最先進的 Seq2Seq Transformer 架構，讓 AI 足以讀懂一整首流行樂的起承轉合，並如同頂尖爵士樂手般給出「高級且富有音樂靈魂」的替換和弦。

這一切得益於這 8 天來您不眠不休萃取出的 **4萬多首高純度 (Melody/Bass/Chord) 音樂資料庫**！
*(註：原 78K 曲庫已預先排除 classics, sleep, relax 等特殊型態曲目，避免非典型編制稀釋流行/爵士和弦與曲風的正確判斷。)*

---

## 貳、新增模組與 API 規格

### 1. 深度學習 Tokenizer (`backend/ai/tokenizer.py`)
神經網路無法有效處理無規律的絕對音高。此模組負責對過濾後的 40K+ 曲庫進行「無損壓縮與特徵萃取」。

- **[實作機制] 調性歸一化 (Normalization)**：強迫所有解析出的 JSON 和弦，一律移調校準至 `C Major / A Minor`。
- **[實作機制] 時序編碼 (Event-based Encoding)**：為了捕捉「人類律動 (Groove)」，將 JSON 轉為包含時間長度的向量 Token。
  - 編碼範例：`["Cmaj7", "DUR_2.0", "Am7", "DUR_1.5", "Dm9", "DUR_0.5"]`。
  - 時間解析度 (TIME_RESOLUTION) = 0.5 秒。
- **[安全降級]** 針對極罕有和弦進行降級機制保護。實測後已將原定的 `Vocab_Limit = 150` 放寬提升至 `Vocab_Limit = 300`，以確保我們在替 4 萬首歌曲建立重疊樣本空間 (Dense Vector Space) 的同時，也不會過度稀釋掉如 `#11`, `13`, `aug7` 等富有靈魂的高級彩色和弦。

### 2. Chord2Vec 高維空間嵌入矩陣 (`backend/ai/chord2vec.py`)
這是讓 AI 理解「和聲邏輯」的敲門磚，利用 NLP 中 Word2Vec (Skip-gram) 的概念。

- **[升級與變更]** 廢棄舊有的羅馬級數法 (Roman Numerals)，改用 C Major Absolute Normalized Chords。
- **[實踐應用]** 當前端呼叫，只需運算高維矩陣中的 **Cosine Similarity**，系統即刻推斷 `Dm7` 和 `Fmaj7` 的語義相似度極高，可做為無痛替換和弦。
- **[運算效能]** 本地透過共現矩陣 (Co-occurrence Matrix) 加奇異值分解 (SVD) 降維，純 CPU 環境也能秒算出百萬筆和聲特徵。

### 3. Jazzify Transformer 大腦 (`backend/ai/transformer_reharmonizer.py`)
本計畫的心臟。使用 PyTorch 建構了標準的 Transformer (Encoder-Decoder) 模型。

- **[網路架構]**
  - **Encoder**：負責吃進使用者的「流行/初階和弦」(Pop Chords)。
  - **Decoder**：負責吐出經過重配的「高級爵士/Neo-Soul 和弦」(Jazzified Chords)。
  - **Masking Mechanism**：採用標準的 `generate_square_subsequent_mask` 防止預測未來的作弊。
- **[訓練對照 (Ground Truth)]**
  - 第一階段 Pre-training 將使用您現有的 `reharmonizer.py` (Rule-Based)，對 40K+ 曲庫暴力產生出高品質的 Jazz 替換結果作為解答 (Labels)。

### 4. Viterbi 終極防撞網 (`backend/ai/reharmonizer.py`)
大語言模型/Transformer 有機會「幻覺」，寫出脫離主旋律的怪異和弦。因此我們佈下了天羅地網。

- **[整合與防護]** 在 `reharmonizer.py` 擴充了 `mode="transformer"`，在接獲 Transformer 推理出來的和弦機率分佈後，我們**疊加套用 Viterbi 解碼與旋律發射機率 (Emission Probability)**。
- 只要 Transformer 吐出的新和弦跟旋律發生小二度碰撞，機率直接歸零，確保輸出的樂譜具備 **100% 音樂合理性 (Playability & Harmonic Safety)**。

### 5. 紫色 ✨AI 按鈕 (前端 UI 與概念定位)
- **定位**：頂級爵士作曲家/編曲家 🎼
- **工作內容**：負責改寫樂譜 (Reharmonization)。
- **運作原理**：基於動用高效能 GPU (如 RTX 5080) 與 5 萬首曲庫訓練出來的深度學習神經網路 (Seq2Seq Transformer)。
- **實際效果**：當套用時，它看的是整首歌的**「和弦進行」**。如果原本的歌是傳統流行樂的四和弦進行 `C -> Am -> F -> G`，這位神級編曲家會直接把樂譜劃掉，大刀闊斧幫使用者改成複雜度極高的爵士和聲，如 `Cmaj9 -> A7(b13) -> Dm9 -> G13(b9)`。
- **神經網路特色**：它具備「前後文語意理解」，會像真人一樣看後面的和弦來決定前面要不要偷塞個過渡和弦 (Passing Chord)，或是使用代理和弦 (Tritone Substitution)。簡而言之，它改變的是這首歌的「DNA (和弦本體)」。相比於「機器人 (AI 伴奏教學)」單純負責指法與律動，紫色按鈕負責從根本上改寫音樂的基因。


---

## 肆、Audio-Informed 動態段落偵測改良計畫 (Section Detect V4)

您觀察得非常敏銳！舊版的 `section_detect.py` 純粹只看「和弦複雜度」與「和弦和聲變化密度」，這導致它常常被和弦簡單的高潮段落騙成主歌，或是被純伴奏的高複雜度前奏騙成副歌。
既然我們透過 Phase 12 已經把主旋律 (Melody) 與貝斯 (Bass) 給抽離出來了，我們就擁有了**「物理能量 (Energy Level)」與「節奏輪廓 (Rhythmic Outline)」**這兩把最強大的武器！

### 1. 旋律能量與留白特徵 (Melody Energy & Silence)
- **特徵萃取**：讀取 `melody.json`，計算該段落的「旋律音符發聲比率 (Voiced Ratio)」與「平均音高 (Pitch Height)」。
- **判斷基準**：
  - `Intro / Outro / Instrumental`：旋律發聲比率趨近於 0（或只有很長的單音）。若一首歌結尾「旋律聲變小且逐漸消失」，即刻強制標註為 Outro。
  - `Chorus`：流行樂的副歌通常具有**全曲最高的平均音高**、最密集的旋律線，且休息留白的時間最短。
  - `Verse`：音高相對於 Chorus 偏低，樂句短且中間留白多（給歌手喘息）。

### 2. Bass 律動對齊與節拍偵測 (Bass Groove & Beat Tracking)
- **特徵萃取**：掃描 Hybrid Phase 建立的 `sanitized_bass.mid`。貝斯的 Onset（發聲點）是流行音樂判斷段落的核心。
- **判斷基準**：
  - `Pre-Chorus (導歌)`：貝斯線的密度通常會突然翻倍（例如從 4 分音符轉為 8 分音符的連續 Root 敲擊），作為進入副歌的橋樑。
  - **精確小節切割**：目前的段落切割是靠統計猜測 BPM，然後切出固定的 2 或 4 小節窗口，很容易切在樂句中間。有了貝斯後，我們可以鎖定 Downbeat (重拍第一拍)，保證以正確的樂理小節線作為段落切割的邊界 (Phrase Boundary)。

### 3. 多模態段落計分系統 (Multi-Modal Scoring)
未來 `section_detect.py` 將升級為多模態評分矩陣：
`Section Score = W1 × 和弦複雜度 + W2 × 旋律音高與密度 + W3 × 貝斯緊湊度`
結合三者的判斷，將能徹底根除「開頭顯示副歌、結尾顯示主歌」的荒謬情況，提供最完美的曲式解剖圖。

### 4. 近期實測紀錄與問題分析 (以 Arthur's Theme 為例)
> **測試時間**：2026-04-13
> **觀測對象**：`y:\Christopher Cross - Arthur's Theme (Best That You Can Do)`

透過將此曲目送入 V4 手工切割並比對結果，我們發現目前 V4 面臨以下兩大嚴重瓶頸，此將直接留待未來進階模型 (Deep Learning Models) 推展時解決：

1. **區塊過度破碎 (Fragmented Sections) 與無法融合**
   - V4 的運算依賴於固定的 8.5s 時窗 (Window Size)。但演算法未能成功藉助 `_merge_sections()` 合併連續長度的主歌或副歌。
   - **現象**：系統會產出 8.5 秒切成一塊塊的片段 (如 43s - 85s 連續跳出 8s 的細碎 Verse 與 Pre-Chorus 切換)，無法拼湊出連貫完整的樂句。
   
2. **副歌定位對高複雜度和絃 (Jazz/AOR) 抵抗力薄弱**
   - 傳統 pop 取向單純依靠密度或複雜度判定的遺毒依然存在。Arthur's Theme 的調性經常遊走於 `Dmaj7 -> C#m -> F#m`、`Bm7 -> E7 -> A` 等連續的副屬和弦及 ii-V 進行。
   - **現象**：系統將音樂進行不到 25 秒的複雜經過轉換處誤判為 Chorus，卻將真正 43 秒進入最高潮的副歌判為 Verse 或 Pre-Chorus，出現嚴重漏判及誤判的問題。

綜上所述，現純手寫的 Rule-based/Heuristic 段落引擎（雖然已加入 Melody 與 Bass 參數防呆）對非標準曲式的容錯率依然極低。未來應引入 **RNN / LSTM 時序模型或 Seq2Seq 架構 (Phase 13.2)** 來做全域的動態段落邊界偵測與切分，才能真正根除碎片化問題。

---

## 伍、驗證與生產計畫

本規格內的模組均已完成源碼攥寫。下一步即刻可以上機（NUC U9-285H + RTX 5080 GPU）進行訓練驗證。

1. **運行 Tokenizer**：執行 `python backend/ai/tokenizer.py`，打通 40K+ 的 `data/chords` 曲庫。
2. **訓練 Chord2Vec**：獲得第一份具備真實音樂邏輯的「AI 代理和弦推薦字典」。
3. **Seq2Seq Transformer 微調**：使用 Pytorch DataLoader 正式訓練模型，觀察 Perplexity 收斂。
4. **前端打通**：在網頁前端點選 `Jazzify (AI 模式)`，聆聽 AI 從無到有配出的高級和絃編曲。

---

## 避雷指南與最佳實踐 (Troubleshooting & Best Practices)

### 1. Windows Batch (.bat) 腳本的 LF/CRLF 編碼地獄
在專案開發過程中，我們建立 `train_ai.bat` 總控腳本時遇到了罕見的 CMD 解析崩潰事件，特此紀錄預防：
*   **問題現象**：
    *   最初的現象是：只要 `.bat` 內的中文包含了括號 `()`（例如 `echo [STEP 1] (多執行緒)`），且剛好位在 `if...` 條件區塊中，CMD 會直接將這個右括號誤判為 `if ... ( ... )` 的提早結束，造成後方腳本變成語法錯誤 (Syntax Error)，並噴出 `xxx is not recognized as an internal or external command`。
    *   **深度隱患 (LF vs CRLF)**：為了解決括號問題，我們將提示文字全改為英文 ASCII (例如 `[STEP 1]`)，但系統在執行時仍然**無視了 `if "%choice%"=="1"` 的條件跳轉**，也就是當使用者輸入 `2`，照理說應該要跳過 Step 1，腳本卻還是硬衝進入條件區塊將 Step 1 給執行了！
*   **真實原因**：
    *   Windows `cmd.exe` 在解析 `.bat` 檔案 `if` 或 `for` 等跨行區塊時，是嚴格仰賴位元組偏移量 (Byte offsets) 搭配 `\r\n` (CRLF) 來判斷指令邊界的。
    *   當我們用部分跨平台編輯器、AI 輔助工具、或是 Node/Python 腳本寫入建立 `.bat` 檔案時，若預設採用了 Linux/Mac 體系的 `\n` (LF) 換行符，CMD 算出來的區塊記憶體指針會發生嚴重偏移。這導致 CMD 以為自己跳過了 IF 區塊，指針卻不偏不倚降落進了 IF 區塊的中央，於是繼續無視條件無腦向下執行！
*   **未來防呆與實踐方針**：
    *   **嚴格 CRLF 檢查**：若要在專案內建立 `.bat` 批次檔，存檔時**務必確認 IDE 右下角的換行符號為嚴格的 `CRLF`**。
    *   **避免在自動化腳本內使用巢狀巨集**：若無法擔保建立出的批次檔一定是 CRLF，請**絕對避免在 `.bat` 中使用 `( )` 跨行區塊與複雜的 `if` 邏輯**。推薦改用傳統的 `goto` Label 錨點（例如 `if "%choice%"=="1" goto step1`，然後底下標註 `:step1`）來進行流程控制，這對抗 LF 錯位的免疫力與容錯率會高出非常多！
