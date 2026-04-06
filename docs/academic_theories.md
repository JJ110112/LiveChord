# LiveChord 學術理論與實作對照表

本文件整理 LiveChord 專案中所運用的學術理論、對應的實作程式、輸入輸出及用途。

---

## 1. 統計與機器學習模型

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **Markov Chain**（Bigram / Trigram） | `backend/ai/markov.py` | 和弦度數序列（訓練集） | Top-K 下一個和弦預測 + 機率 | 即時和弦建議、和弦進行生成 | Shannon (1948) |
| **Hidden Markov Model (HMM)** | `backend/ai/hmm.py` | 旋律 MIDI 序列 + Emission / Transition 矩陣 | 最佳和弦路徑 + log-probability | 由旋律反推和弦進行 | Rabiner (1989) |
| **Viterbi Algorithm**（log-space + beam pruning） | `backend/ai/viterbi_engine.py` | 觀測序列 + 狀態空間 + 轉移/發射函數 | 最優狀態路徑（1-best / N-best） | HMM 解碼、指法最佳化的通用引擎 | Viterbi (1967), Rabiner (1989) |
| **Word2Vec (Skip-gram) + SVD + PPMI** | `backend/ai/chord2vec.py` | 和弦度數序列 | 32 維和弦嵌入向量（L2 正規化） | 和弦相似度計算、語義空間表示 | Mikolov et al. (2013), Levy & Goldberg (2014) |
| **Perplexity / Prediction Accuracy** | `backend/ai/evaluate.py` | Markov predictor + 測試序列 | Perplexity、Top-K 準確率 | 模型品質評估 | Jelinek (1977) |

---

## 2. 音訊分析與訊號處理

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **pYIN**（Probabilistic YIN） | `backend/ai/melody_extractor.py` | 音訊檔案（WAV/MP3） | 旋律音符序列 [{start, end, midi, confidence}] | 從混音中提取主旋律音高 | Mauch & Dixon (2014), de Cheveigné & Kawahara (2002) |
| **Onset Detection** | `backend/ai/melody_extractor.py` | 音訊波形 | 起音時間點集合 | 偵測音符攻擊點，輔助旋律切分 | Böck & Widmer (2013) |
| **HPSS**（Harmonic-Percussive Source Separation） | `backend/ai/melody_extractor.py` | 音訊波形 | 分離後的諧波/打擊成分 | 前處理，提升旋律提取精度 | Fitzgerald (2010) |
| **Vibrato Filtering**（Median Filter） | `backend/ai/melody_extractor.py` | pYIN 原始 F0 序列 | 平滑後的 F0 序列 | 抑制顫音造成的音高抖動 | — |
| **Demucs**（深度神經網路音源分離） | `backend/ai/stem_separator.py` | 完整混音音訊 | 4 軌分離音檔（bass/vocals/drums/other） | 從混音中拆出個別樂器軌 | Défossez et al. (2019) |
| **Basic Pitch**（Spotify 多音高轉錄） | `backend/ai/audio_to_midi_transcriber.py` | 分離後的音軌音訊 | MIDI 檔案 | 音訊轉 MIDI（保留原始節奏與力度） | Bittner et al. (2022) |

---

## 3. 音樂理論與和聲學

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **和弦音程表**（Chord Intervals） | `backend/chord_table.py` | 和弦符號（如 "Cm7"） | 音高類別陣列（pitch classes） | 和弦名稱→組成音的基礎查表 | 基礎樂理 |
| **調性偵測**（加權根音頻率） | `backend/ai/preprocess.py` | 和弦序列 | 調性（semitone） | 自動推斷歌曲調性 | Krumhansl (1990) |
| **Jazz 和聲規則**（延伸音、三全音代理、ii-V-I、次屬和弦） | `backend/ai/jazz_rules.py` | 和弦度數 + 品質 | 爵士化後的和弦品質 | 分級延伸（L1: 7th → L2: 9th → L3: 13th） | Levine (1995), Barrie Nettles (Berklee) |
| **Reharmonization**（6-pass 爵士重配和聲） | `backend/ai/reharmonizer.py` | 和弦進行 + 調性 + 旋律 | 爵士化和弦 + 變更記錄 + QA 報告 | 自動將流行和弦升級為爵士語彙 | Levine (1995) |
| **Roman Numeral Analysis + Pattern Matching** | `backend/ai/pattern_extractor.py` | 和弦序列 + 調性 | 偵測到的進行模式（ii-V-I、turnaround 等） | 辨識經典和弦進行模式 | Aldwell & Schachter (2010) |

---

## 4. 伴奏生成與演奏模擬

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **Voice Leading 最佳化**（最小移動、共同音保留、平行五八度迴避） | `backend/ai/accompaniment_generator.py` | 前後和弦的 voicing | 平滑連接的 voicing 序列 | 確保和弦轉位符合聲學規則 | Piston (1987) |
| **Style Pattern Dictionary**（7 種伴奏型態） | `backend/ai/accompaniment_generator.py` | 和弦 + BPM + 風格 | 左右手 MIDI 事件 + 踏板 | Block / Arpeggio / Rhythm / Alberti / Shell / Walking / Stride | Drotos (Pop Piano Book) |
| **Melody Collision Avoidance** | `backend/ai/accompaniment_generator.py` | 伴奏音 + 旋律音 | 調整後的伴奏（下移八度或移除） | 避免伴奏與旋律撞音（≤4 半音） | — |
| **LH/RH Hand Collision Filter** | `backend/ai/accompaniment_generator.py` | 左右手事件列表 | 過濾後的 LH（下移八度或移除） | 避免左手最高音 >= 右手最低音 | — |
| **RH Section-Aware Gap-Fill** | `backend/ai/accompaniment_generator.py` | 和弦 + 旋律 + 段落類型 | RH 事件（填空 / 琶音 / block chord） | RH 閃避人聲，在空白處用和弦 3rd/5th/7th 補 fill | — |
| **Viterbi Fingering Optimization** | `backend/ai/fingering_model.py` | MIDI 音高序列 + 手別 + BPM | 最佳指法序列 [1-5] | 產生人體工學可行的指法 | Parncutt et al. (1997), Al Kasimi et al. (2007) |
| **Ergonomic Fingering Evaluation**（三階段評估） | `backend/ai/fingering_evaluator.py` | 事件列表 [{time, pitch, finger}] | 人體工學分數 + 警告 | 驗證指法的舒適度與可行性 | Parncutt (1997), Jacobs (2001) |
| **Sustain Pedal Advisory** | `backend/ai/pedal_advisor.py` | 和弦 + 段落 + BPM + 旋律 | 踏板事件 [{start, end, depth}] | 產生 legato / rhythmic / half-pedal 踏板建議 | Banowetz (1985), Schnabel (1942) |
| **MIDI Pitch Sanitization**（Snap-to-nearest） | `backend/ai/midi_sanitizer.py` | 原始轉錄 MIDI + 和弦時間軸 | 修正後的 MIDI | 將音高量化到和弦音，修正轉錄錯誤 | — |

---

## 5. 動態表情與樂句塑形

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **Velocity Curve Modeling**（正弦曲線 + 黃金比例） | `backend/ai/dynamics_engine.py` | 事件序列 + 段落 + BPM | 帶力度的事件 [{velocity, articulation}] | 模擬真實演奏的力度起伏 | Todd (1992), Palmer (1997) |
| **Articulation Generation**（legato / staccato / portato） | `backend/ai/dynamics_engine.py` | 事件序列 + BPM | 調整後的音符時值 | 模擬不同觸鍵風格 | Repp (1999) |
| **Humanization**（Timing 微偏移 + Velocity 抖動） | `backend/ai/dynamics_engine.py` | 事件序列 + BPM + amount | timing ±5~30ms 偏移 + velocity ±5 抖動 | 強拍提前 (anticipation)、消除機械感 | Repp (1999), Dixon (2001), Goebl (2001) |
| **Piano Sustain Envelope**（音域感知延音） | `frontend/js/player.js` (PianoSynth) | MIDI pitch + duration | 低音 1.2s / 中音 0.8s / 高音 0.5s release | 模擬真實鋼琴弦的自然衰減 | — |
| **Section Energy Mapping** | `backend/ai/section_context.py` | 時間 + 段落列表 | 能量等級、密度乘數、踏板風格 | 段落感知的參數調變 | — |

---

## 6. 結構分析

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **BPM-Adaptive Windowing + Jaccard Similarity** | `backend/ai/section_detect.py` | 和弦序列 + 調性 | 段落列表 [{start, end, type}]（intro/verse/chorus/bridge/outro） | 自動偵測歌曲段落結構 | Paulus et al. (2010) |
| **Groove Pattern Dictionary**（4/8-chord loops） | `backend/ai/groove_dict.py` | 和弦訓練目錄 | 常見節奏型態頻率統計 | 統計訓練資料中的節奏模式分布 | — |

---

## 7. 品質評估（QA）

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **Melody Evaluation**（5 維度：音域 / 音程 / 樂句弧 / 節奏多樣性 / DTW 相似度） | `backend/ai/melody_evaluator.py` | 旋律事件列表 | 綜合分數 + 各維度分數 | 評估旋律品質 | Narmour (1990), Müller (2007) |
| **Dynamic Time Warping (DTW)** | `backend/ai/melody_evaluator.py` | 兩段旋律（pitch / rhythm / full） | 正規化距離 0–1 | 旋律相似度比較 | Müller (2007) |
| **Accompaniment Evaluation**（5 維度：撞音 / Voice Leading / 音域平衡 / 密度 / 和聲完整性） | `backend/ai/accompaniment_evaluator.py` | 左右手事件 + 旋律 + 和弦 | 綜合分數 + 各維度分數 | 評估伴奏品質 | Piston (1987), Huron (2001) |
| **Pitch-Class Overlap**（Jaccard Index） | `backend/ai/evaluate.py` | 兩個和弦 | 重疊比率 0–1 | 和弦相似度量化 | — |
| **Musician QA**（可演奏性評估） | `backend/ai/musician_qa.py` | 和弦進行 + BPM | 可演奏分數 + 警告 | 驗證是否在人的技術範圍內 | — |
| **Producer QA**（混音品質） | `backend/ai/producer_qa.py` | 原始 / 爵士化和弦 | 混音分數 + 警告 | 偵測頻率遮蔽與濁度 | Parncutt (1989) |
| **Battle QA**（多代理聚合） | `backend/ai/battle_qa.py` | 所有音樂元素 | 總裁決 (pass/warn/fail) + 建議 | 整合所有 QA 模組的最終判定 | — |

---

## 8. 前處理與資料管線

| 理論 / 演算法 | 實作檔案 | 輸入 | 輸出 | 用途 | 學術來源 |
|---|---|---|---|---|---|
| **Key Detection**（加權根音頻率） | `backend/ai/preprocess.py` | 和弦序列 | 調性 semitone | 自動調性推斷 | Krumhansl (1990) |
| **Chord Transposition** | `backend/ai/preprocess.py` | 和弦 + 半音數 | 轉調後和弦 | 資料增強、正規化到統一調性 | 基礎樂理 |
| **Degree Conversion**（絕對→相對） | `backend/ai/preprocess.py` | 和弦 + 調性 | Roman numeral 度數 | 將和弦轉為與調性無關的度數表示 | 基礎樂理 |

---

## 參考文獻索引

| 簡稱 | 完整引用 |
|---|---|
| Rabiner (1989) | Rabiner, L. R. "A Tutorial on Hidden Markov Models and Selected Applications in Speech Recognition." *Proc. IEEE*, 1989. |
| Viterbi (1967) | Viterbi, A. "Error Bounds for Convolutional Codes and an Asymptotically Optimum Decoding Algorithm." *IEEE Trans. IT*, 1967. |
| Mikolov et al. (2013) | Mikolov, T. et al. "Efficient Estimation of Word Representations in Vector Space." *ICLR Workshop*, 2013. |
| Levy & Goldberg (2014) | Levy, O. & Goldberg, Y. "Neural Word Embedding as Implicit Matrix Factorization." *NeurIPS*, 2014. |
| Mauch & Dixon (2014) | Mauch, M. & Dixon, S. "pYIN: A Fundamental Frequency Estimator Using Probabilistic Threshold Distributions." *ICASSP*, 2014. |
| de Cheveigné & Kawahara (2002) | de Cheveigné, A. & Kawahara, H. "YIN, a fundamental frequency estimator for speech and music." *JASA*, 2002. |
| Böck & Widmer (2013) | Böck, S. & Widmer, G. "Maximum Filter Vibrato Suppression for Onset Detection." *DAFx*, 2013. |
| Défossez et al. (2019) | Défossez, A. et al. "Music Source Separation in the Waveform Domain." *arXiv:1911.13254*, 2019. |
| Bittner et al. (2022) | Bittner, R. et al. "A Lightweight Instrument-Agnostic Model for Polyphonic Note Transcription and Multipitch Estimation." *ICASSP*, 2022. |
| Piston (1987) | Piston, W. *Harmony*. 5th ed. W.W. Norton, 1987. |
| Krumhansl (1990) | Krumhansl, C. L. *Cognitive Foundations of Musical Pitch*. Oxford University Press, 1990. |
| Levine (1995) | Levine, M. *The Jazz Theory Book*. Sher Music, 1995. |
| Todd (1992) | Todd, N. P. M. "The Dynamics of Dynamics: A Model of Musical Expression." *JASA*, 1992. |
| Palmer (1997) | Palmer, C. "Music Performance." *Annual Review of Psychology*, 1997. |
| Repp (1999) | Repp, B. H. "Effects of Auditory Feedback Deprivation on Expressive Piano Performance." *Music Perception*, 1999. |
| Narmour (1990) | Narmour, E. *The Analysis and Cognition of Basic Melodic Structures*. University of Chicago Press, 1990. |
| Müller (2007) | Müller, M. *Information Retrieval for Music and Motion*. Springer, 2007. |
| Huron (2001) | Huron, D. "Tone and Voice: A Derivation of the Rules of Voice-Leading from Perceptual Principles." *Music Perception*, 2001. |
| Parncutt et al. (1997) | Parncutt, R. et al. "An Ergonomic Model of Keyboard Fingering for Five-Finger Passages." *Music Perception*, 1997. |
| Al Kasimi et al. (2007) | Al Kasimi, A. et al. "A Simple Algorithm for Automatic Generation of Polyphonic Piano Fingerings." *ISMIR*, 2007. |
| Banowetz (1985) | Banowetz, J. *The Pianist's Guide to Pedaling*. Indiana University Press, 1985. |
| Paulus et al. (2010) | Paulus, J. et al. "State of the Art Report: Audio-Based Music Structure Analysis." *ISMIR*, 2010. |
| Parncutt (1989) | Parncutt, R. *Harmony: A Psychoacoustical Approach*. Springer, 1989. |
| Drotos | Drotos, M. *The Pop Piano Book*. Shelly Music, 1998. |
| Shannon (1948) | Shannon, C. E. "A Mathematical Theory of Communication." *Bell System Technical Journal*, 1948. |
| Jelinek (1977) | Jelinek, F. "Perplexity — A Measure of the Difficulty of Speech Recognition Tasks." *JASA*, 1977. |
| Aldwell & Schachter (2010) | Aldwell, E. & Schachter, C. *Harmony and Voice Leading*. 4th ed. Cengage, 2010. |
| Fitzgerald (2010) | Fitzgerald, D. "Harmonic/Percussive Separation Using Median Filtering." *DAFx*, 2010. |
| Jacobs (2001) | Jacobs, J. P. "Refinements to the Ergonomic Model of Keyboard Fingering." *Music Perception*, 2001. |
| Schnabel (1942) | Schnabel, A. *Music and the Line of Most Resistance*. Princeton University Press, 1942. |
| Dixon (2001) | Dixon, S. "Automatic Extraction of Tempo and Beat from Expressive Performances." *JNMR*, 2001. |
| Goebl (2001) | Goebl, W. "Melody Lead in Piano Performance: Expressive Device or Artifact?" *JASA*, 2001. |
