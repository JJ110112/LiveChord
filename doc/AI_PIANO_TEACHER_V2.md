# Phase 11: AI 鋼琴教師 V2 — 質感與靈魂提升計畫

> 版本: 2.0 | 日期: 2026-04-05
> 狀態: **Phase 11 全部實作完成** (10 項, 6 新建 + 5 改寫)
> 前置: Phase 10 已完成（伴奏生成 + 指法 + 瀑布流 + A-B repeat）

---

## 設計哲學

Phase 10 建立了 AI 鋼琴教師的骨架——能生成伴奏、推導指法、瀑布流教學。
Phase 11 要注入**學術理論、樂理理論、鋼琴彈奏技巧**，讓 AI 教師從「能用」進化到「有靈魂」。

核心原則：
- 每個模組都要有 **Generator（生成）** 和 **Evaluator（評測）** 雙引擎對抗
- 所有演算法必須有**樂理論文依據**，不是拍腦袋的 magic number
- Viterbi 是貫穿全系統的脊椎，先重構它

---

## 第零層：Viterbi 統一重構（P0 地基工程）

### 現狀問題

Viterbi 目前散落在三處，各自為政：

| 位置 | 用途 | 問題 |
|------|------|------|
| `ai/hmm.py:98` ViterbiDecoder | 和弦路徑解碼 | Emission 只靠 Laplace smoothing，缺樂理先驗 |
| `ai/fingering_model.py:48` generate() | 指法序列 | Transition cost 粗糙，5 個 magic number |
| `ai/accompaniment_generator.py` viterbi_fingering() | 伴奏指法 | 複製貼上 fingering_model 的邏輯 |

### 重構方案

建立統一的 `ai/viterbi_engine.py`：

```python
class ViterbiEngine:
    """通用 Viterbi 框架，支援 log-space + beam pruning"""

    def __init__(self, states, transition_fn, emission_fn, beam_width=None):
        """
        states:        可能的狀態列表
        transition_fn: (prev_state, curr_state, context) -> log_prob
        emission_fn:   (state, observation, context) -> log_prob
        beam_width:    top-K 剪枝（None = 不剪枝）
        """

    def decode(self, observations, context=None) -> List[state]:
        """標準 Viterbi 解碼"""

    def decode_nbest(self, observations, n=5, context=None) -> List[List[state]]:
        """N-best 路徑（供 QA Battle 選擇）"""
```

三個場景各自只需提供 `transition_fn` 和 `emission_fn`：

| 場景 | transition_fn | emission_fn |
|------|--------------|-------------|
| 和弦解碼 | Markov P(chord_j \| chord_i) | P(melody_note \| chord) |
| 指法生成 | 手指轉移成本 | 音高→手指適配度 |
| 伴奏指法 | 同上（注入左/右手 context） | 同上 |

### 學術依據
- Rabiner, L.R. (1989). "A Tutorial on Hidden Markov Models and Selected Applications in Speech Recognition." *Proceedings of the IEEE*, 77(2).
- 統一框架避免三份 Viterbi 各有 bug、各有 edge case 漏洞

---

## 第一項：AI 旋律偵測升級

### 現狀
- `ai/melody_extractor.py`: librosa pYIN + HPSS + 高通濾波
- 頻率範圍 C4-C6，0.1 秒最短音符門檻
- 無 onset 對齊，無 vibrato 處理，無多聲部

### 改進內容

#### 1.1 Onset Detection 節拍對齊
```
現行: 純靠 pYIN voiced_flag 切換來切割音符邊界
問題: 快速經過音 (passing tone) 會被合併，裝飾音 (ornament) 被忽略
改進: 加入 librosa.onset.onset_detect() 作為輔助切割點
```
- 結合 `onset_strength` 和 `pYIN` 雙重證據
- onset 發生但 pitch 未變 → 同音反覆（re-articulation）
- pitch 變但無 onset → 滑音（glissando / portamento）

#### 1.2 Vibrato 穩態量化
```
現行: int(round(midi_float)) 直接四捨五入
問題: vibrato ±50 cent 會在兩個半音間跳動，產生雜訊音符
改進: 滑動窗口中位數濾波 (median filter, window=5 frames)
```
- 偵測 vibrato 特徵（週期性 pitch 震盪 5-7Hz）
- vibrato 區段取中位數作為穩態音高

#### 1.3 頻率範圍自適應
```
現行: 硬編碼 C4-C6
問題: 男聲旋律 (tenor) 在 C3-C5，器樂獨奏可到 C7
改進: 先做全頻段粗掃，找到能量最集中的兩個八度，再精掃
```

#### 1.4 置信度輸出
```
現行: 只回傳 note list
改進: 每個音符附帶 confidence (voiced_prob 的平均值)
      低置信度區段標記為 "uncertain"，供下游模組決策
```

### 學術依據
- de Cheveigné, A. & Kawahara, H. (2002). "YIN, a fundamental frequency estimator for speech and music." *JASA*, 111(4).
- Mauch, M. & Dixon, S. (2014). "pYIN: A Fundamental Frequency Estimator Using Probabilistic Threshold Distributions." *ICASSP*.
- Böck, S. & Widmer, G. (2013). "Maximum Filter Vibrato Suppression for Onset Detection." *DAFx*.

---

## 第二項：AI 旋律評測（新模組）

### 目標
建立 `ai/melody_evaluator.py`，獨立評估旋律品質。

### 評測維度

#### 2.1 音域合理性 (Tessitura Score)
```
- 統計旋律的有效音域（5th~95th percentile）
- 對比人聲/樂器的舒適音域
- 超出舒適音域的比例 → penalty
- 分數: 0-100
```

#### 2.2 跳進/級進比例 (Intervallic Profile)
```
學術理論: 好的旋律約 70% 級進（stepwise, ≤2 半音）、30% 跳進
- 統計 interval histogram
- 與理論分布做 KL-divergence
- 大跳（>P5）後是否有反向級進修正（Gestalt 原則）
```

#### 2.3 樂句弧線 (Phrase Arc Score)
```
- 偵測樂句邊界（靜音 > 0.3s 或 section boundary）
- 每個樂句內的音高輪廓是否呈 arch shape（上行→高點→下行）
- 參考 Narmour (1990) Implication-Realization 模型
```

#### 2.4 節奏多樣性 (Rhythmic Variety)
```
- 統計 IOI (Inter-Onset Interval) 的 entropy
- 太單調（全是八分音符）→ 低分
- 太混亂（無週期性）→ 低分
- 最佳: 2-4 種主要節奏型態
```

#### 2.5 旋律相似度比對 (Melody Similarity)
```
- DTW (Dynamic Time Warping) 比對兩段旋律
- 用途: 評估 AI 生成旋律 vs 原曲旋律的偏差
- Pitch-class DTW（忽略八度差異）+ Rhythm DTW（忽略音高）
```

### 學術依據
- Narmour, E. (1990). *The Analysis and Cognition of Basic Melodic Structures.* University of Chicago Press.
- Müller, M. (2007). *Information Retrieval for Music and Motion.* Springer. (DTW)
- Temperley, D. (2007). *Music and Probability.* MIT Press.

---

## 第三項：AI 伴奏生成深化

### 現狀
- `ai/accompaniment_generator.py`: 8 風格 × 12 曲風，L1/L2/L3 三級
- STYLE_DICT 是靜態 pattern，velocity_ratio 是常數
- Walking bass 只用 chord tone，無 chromatic approach

### 改進內容

#### 3.1 Tension-Release 動態控制
```
現行: L1/L2/L3 整首曲子一個等級
改進: 按 section 調整 tension level
  - Verse: 低密度 (Arpeggio/Shell)
  - Pre-chorus: 漸增
  - Chorus: 高密度 (Block/Rhythm + 延伸音)
  - Bridge: 轉折（Shell / 意外和弦）
  - Outro: 漸收
需要: 串接 section_detect.py 的結果
```

#### 3.2 Bass Line 理論強化
```
Walking Bass 改進:
  - Chromatic approach note: 下一個根音的 ±1 半音
  - Diatonic passing tone: 調內音階的經過音
  - Enclosure: 上下包圍目標音 (jazz 經典手法)
  - 節拍權重: 強拍 = chord tone, 弱拍 = passing/approach

Stride Bass 改進:
  - 低音根音 + 高音和弦交替（Fats Waller 風格）
  - 加入 tenth (10度) 跳躍選項
```

#### 3.3 Voice Leading 最佳化
```
現行: 基本的最近音原則
改進: 加入以下學術規則
  - 避免平行五度/八度 (parallel 5ths/8ves)
  - 導音 (leading tone) 必須解決到主音
  - 七度音必須下行解決
  - 共同音保持 (common tone retention)
  - 外聲部反向進行 (contrary motion in outer voices)
```

#### 3.4 節奏密度自適應
```
BPM-aware 密度:
  - 慢歌 (<80 BPM): 允許 16 分音符 arpeggio
  - 中速 (80-120): 8 分音符為主
  - 快歌 (>120): 4 分音符 block 為主，避免手忙腳亂
Section-aware 密度:
  - Verse 密度 = 基準 × 0.7
  - Chorus 密度 = 基準 × 1.2
  - Bridge 密度 = 基準 × 0.5（留白）
```

### 學術依據
- Piston, W. (1987). *Harmony* (5th ed.). W.W. Norton. (Voice leading rules)
- Levine, M. (1995). *The Jazz Theory Book.* Sher Music. (Walking bass, enclosure)
- Aldwell, E. & Schachter, C. (2010). *Harmony and Voice Leading* (4th ed.). Cengage.

---

## 第四項：AI 伴奏評測（新模組）

### 目標
建立 `ai/accompaniment_evaluator.py`，全面評估伴奏品質。

### 評測維度

#### 4.1 旋律碰撞率 (Melody Collision Score)
```
定義: 伴奏音與旋律音在同一時間點、同一音高（或相距 ≤1 半音）
計算: collision_count / total_accompaniment_notes
理想值: < 5%
嚴重碰撞: 同一八度內的半音碰撞（如旋律 E4，伴奏 F4）
```

#### 4.2 Voice Leading Smoothness
```
計算所有相鄰和弦之間的聲部移動距離:
  smoothness = 1 / (1 + avg_voice_movement_in_semitones)
理想值: 平均移動 ≤ 3 半音
平行五八度偵測: 扣分
```

#### 4.3 音域平衡 (Register Balance)
```
- 左手音域: C2-B3 (36-59) — 超出 → muddy 或 thin
- 右手音域: C4-C6 (60-84) — 超出 → 與旋律打架
- 左右手間距: 最佳 8-15 半音，太近 → 糊，太遠 → 空洞
- 和弦密度: 低音區不超過 3 音（避免 mud）
```

#### 4.4 節奏密度合理性
```
- 計算每小節的 note density (notes per beat)
- 與 BPM 對照: 高 BPM + 高密度 = 不合理
- Section 對照: verse 密度應 < chorus 密度
- Entropy: 節奏型態不宜太單調也不宜太混亂
```

#### 4.5 和聲功能完整度
```
- 檢查和弦音是否都被彈到（至少 root + 3rd）
- 7th chord 的 7th 是否出現
- 省略音規則: root 可省（有 bass），5th 可省，3rd 不可省
```

### 學術依據
- Huron, D. (2001). "Tone and Voice: A Derivation of the Rules of Voice-Leading from Perceptual Principles." *Music Perception*, 19(1).
- Parncutt, R. (1989). *Harmony: A Psychoacoustical Approach.* Springer. (Roughness model for collision)

---

## 第五項：AI 指法生成精進

### 現狀
- `ai/fingering_model.py`: Viterbi + transition cost（5 規則）
- 缺黑白鍵差異、手指長度、速度因子

### 改進內容

#### 5.1 黑白鍵幾何模型
```
黑鍵特性:
  - 比白鍵短 ~40%，窄 ~50%
  - 位置偏後（離身體更遠）
  - 拇指 (1) 按黑鍵成本高（短且粗）
  - 4/5 指按黑鍵相對容易（手指自然彎曲到黑鍵位置）

成本調整:
  black_key_cost(finger):
    finger 1: +3.0（拇指按黑鍵困難）
    finger 2: +0.5
    finger 3: +0.0（最自然）
    finger 4: +0.0
    finger 5: +1.0（小指力量不足）
```

#### 5.2 手指長度比例模型
```
手指相對長度 (標準化):
  1 (拇指): 0.72  — 最短，但最強壯
  2 (食指): 1.00  — 基準
  3 (中指): 1.05  — 最長
  4 (無名指): 0.95 — 受限於肌腱連結
  5 (小指): 0.78  — 最弱

跨距能力 (相鄰手指間最大舒適跨距，半音數):
  1-2: 8  (拇指下穿後的跨距)
  2-3: 4
  3-4: 3  (最受限，4指肌腱與3共用)
  4-5: 4

超出舒適跨距 → exponential penalty
```

#### 5.3 速度感知 (Tempo-Aware Cost)
```
BPM 對指法的影響:
  - 快速段 (>140 BPM 的 16 分音符): 
    × 同指反覆 penalty × 3
    × 跨距 penalty × 2
    × 拇指下穿 penalty × 1.5
  - 慢速段 (<70 BPM):
    × 跨距限制放寬 20%
    × 允許更大的手位移動

tempo_factor = 1.0 + max(0, (bpm - 100) / 100)
adjusted_cost = base_cost * tempo_factor
```

#### 5.4 慣用指法模式庫 (Fingering Idiom Library)
```
常見模式的預設指法（覆蓋 Viterbi 結果如果 cost 更低）:
  - 音階上行 (C→D→E→F→G): RH 1-2-3-1-2 (拇指下穿)
  - 音階下行 (G→F→E→D→C): RH 3-2-1-3-2 (指過拇指)
  - 琶音上行 (C→E→G→C): RH 1-2-3-5
  - 八度跳躍: 1-5 (RH) 或 5-1 (LH)
  - 三度進行: 1-3, 2-4, 1-3, 2-4 交替
```

### 學術依據
- Parncutt, R. et al. (1997). "An Ergonomic Model of Keyboard Fingering for Five-Finger Passages." *Music Perception*, 14(4).
- Hart, M., Bosch, R. &"; E. (2000). "An ergonomic model for piano fingering." *Computer Music Journal*.
- Nakamura, E. et al. (2020). "Statistical Learning and Estimation of Piano Fingering." *Information Sciences*.

---

## 第六項：AI 指法評測強化

### 現狀
- `ai/fingering_evaluator.py`: 垂直約束（跨距/手指順序）+ 水平約束（同指跳躍/伸展）
- 缺疲勞模型和速度感知

### 改進內容

#### 6.1 疲勞累積模型 (Fatigue Accumulation)
```
原理: 同一手指連續使用會累積疲勞，特別是弱指 (4, 5)
模型:
  fatigue[finger] += usage_cost(finger) / recovery_time_since_last_use
  
  usage_cost:
    finger 1: 0.3 (最耐用)
    finger 2: 0.5
    finger 3: 0.5
    finger 4: 1.0 (最容易疲勞)
    finger 5: 0.8
    
  recovery_rate: fatigue 以指數衰減 (half-life = 2 beats)
  
警告閾值: fatigue > 5.0 → "此段落手指疲勞風險高"
```

#### 6.2 BPM-Aware 評分
```
同樣的指法在不同速度下難度差異巨大:
  difficulty_multiplier = 1.0 + max(0, (bpm - 100)) / 80

跨距容忍度隨速度降低:
  max_comfortable_span(bpm):
    bpm < 80:  15 半音
    bpm 80-120: 13 半音
    bpm > 120:  10 半音
    bpm > 160:  8 半音
```

#### 6.3 拇指下穿品質評估
```
拇指下穿 (thumb under) 是鋼琴技巧的核心:
  - 合法下穿: 1 指從 3 或 4 指下方穿過
  - 非法下穿: 1 指從 5 指下穿（手腕角度不合理）
  - 品質評分: 下穿前後的音程是否平滑（理想: 2-4 半音）
  - 快速段下穿: 額外 penalty（下穿需要時間）
```

### 學術依據
- Jacobs, J.P. (2001). "Refinements to the Ergonomic Model for Keyboard Fingering of Parncutt et al." *Music Perception*.
- Al Kasimi, A., Nichols, E., & Raphael, C. (2007). "A Simple Algorithm for Automatic Generation of Polyphonic Piano Fingerings." *ISMIR*.

---

## 第七項：段落結構串連（Section-Aware Intelligence）

### 現狀
- `ai/section_detect.py` 已存在
- 但與伴奏/指法/力度模組**完全沒有串接**

### 改進內容

#### 7.1 Section Context 注入
```
所有生成/評測模組都接收 section_context:
  {
    "type": "verse" | "chorus" | "bridge" | "intro" | "outro",
    "position_in_song": 0.0 ~ 1.0,  (曲子進度)
    "position_in_section": 0.0 ~ 1.0, (段落內進度)
    "energy_level": 0.0 ~ 1.0  (由段落類型決定)
  }
```

#### 7.2 段落→參數對照表
```
| Section   | 伴奏密度 | 力度基準 | 指法難度上限 | 踏板風格    |
|-----------|---------|---------|------------|-----------|
| Intro     | 0.5     | mp      | L1         | 長踏板     |
| Verse     | 0.7     | mp-mf   | L2         | 樂句踏板   |
| Pre-chorus| 0.9     | mf      | L2         | 漸收      |
| Chorus    | 1.0     | f       | L3         | 節奏踏板   |
| Bridge    | 0.6     | mp      | L1-L2      | 自由      |
| Outro     | 0.4→0.1 | mf→pp   | L1         | 長踏板→無  |
```

---

## 第八項：AI 踏板建議（新模組）

### 目標
建立 `ai/pedal_advisor.py`。

### 踏板類型

#### 8.1 Sustain Pedal（延音踏板）
```
三種踏板法:
  1. Legato Pedaling（連結踏板法）:
     - 在和弦轉換瞬間「先放後踩」(release-depress)
     - 適用: 抒情段、和弦持續段
     
  2. Rhythmic Pedaling（節奏踏板法）:
     - 配合節拍踩放
     - 每拍開頭踩，拍尾放
     - 適用: 有明確節奏感的段落
     
  3. Half Pedaling（半踏板）:
     - 踏板只踩一半，部分延音
     - 用於需要些許共鳴但不要太糊的段落
```

#### 8.2 踏板時機生成規則
```
- 和弦變換點: 必須換踏板（避免不協和音堆積）
- 和聲節奏: 和弦持續 > 2拍 → 建議踩踏板
- Bass note 變化: bass 音變化時必須換踏板
- 快速經過段: 不踩踏板（保持清晰度）
- Section 結尾: 長踏板 + 漸放（fade out 效果）
```

#### 8.3 踏板評測
```
- 不協和音累積偵測: 踏板期間的 pitch-class 數量 > 5 → 警告
- 踏板切換頻率: 太頻繁 (< 0.5拍) → 機械感 / 太稀疏 (> 4拍) → 糊
- 與和弦邊界對齊率: 踏板切換應與和弦切換同步率 > 80%
```

### 學術依據
- Banowetz, J. (1985). *The Pianist's Guide to Pedaling.* Indiana University Press.
- Schnabel, K.U. (1954). *Modern Technique of the Pedal.* Mills Music.

---

## 第九項：AI 力度與表情（新模組）

### 目標
建立 `ai/dynamics_engine.py`。

### 改進內容

#### 9.1 Phrase Shaping（樂句塑形）
```
每個樂句的力度曲線:
  - 預設 arch shape: 漸強 → 高點 → 漸弱
  - 高點位置: 黃金比例 (~0.618 處)
  - 力度範圍: mp (64) → f (96) → mp (64) [MIDI velocity]
  
公式:
  velocity(t) = base + amplitude * sin(π * t / phrase_length)
  
  其中 base 和 amplitude 由 section 類型決定
```

#### 9.2 Accent Pattern（重音模式）
```
基於拍號的自然重音:
  4/4: 強-弱-中強-弱 (velocity ratio: 1.0, 0.7, 0.85, 0.7)
  3/4: 強-弱-弱 (1.0, 0.7, 0.7)
  6/8: 強-弱-弱-中強-弱-弱 (1.0, 0.7, 0.7, 0.85, 0.7, 0.7)

額外重音:
  - 切分音 (syncopation): 提前重音 +10% velocity
  - 旋律高點: +15% velocity
  - 和弦變換第一拍: +10% velocity
```

#### 9.3 Articulation 建議
```
- Legato: 音符間無間隙，note_off 與下一個 note_on 重疊
  適用: 慢速旋律、抒情段
  
- Staccato: 音符縮短至標記時值的 50%
  適用: 活潑段落、節奏型伴奏
  
- Portato: 音符之間有極小間隙 (~20ms)
  適用: 中速旋律段

判斷依據:
  - BPM > 140 + 重複音型 → staccato 傾向
  - BPM < 80 + 級進旋律 → legato 傾向
  - Section = bridge → portato 傾向
```

### 學術依據
- Todd, N.P.M. (1992). "The dynamics of dynamics: A model of musical expression." *JASA*, 91(6).
- Palmer, C. (1997). "Music performance." *Annual Review of Psychology*, 48.

---

## 模組依賴關係圖

```
                    ┌─────────────────────┐
                    │  section_detect.py   │
                    │  (段落結構偵測)        │
                    └──────────┬──────────┘
                               │ section_context
                    ┌──────────▼──────────┐
                    │  viterbi_engine.py   │  ← P0 地基
                    │  (統一 Viterbi 框架)  │
                    └──┬───┬───┬───┬──────┘
                       │   │   │   │
         ┌─────────────┘   │   │   └──────────────┐
         ▼                 ▼   ▼                   ▼
┌────────────────┐ ┌──────────────┐ ┌──────────────────────┐
│ melody_extractor│ │ accomp_gen   │ │ fingering_model       │
│ (旋律偵測)      │ │ (伴奏生成)    │ │ (指法生成)             │
│ [改進 #1]      │ │ [改進 #3]    │ │ [改進 #5]             │
└───────┬────────┘ └──────┬───────┘ └──────────┬───────────┘
        │                 │                     │
        ▼                 ▼                     ▼
┌────────────────┐ ┌──────────────┐ ┌──────────────────────┐
│melody_evaluator │ │ accomp_eval  │ │ fingering_evaluator   │
│ (旋律評測)      │ │ (伴奏評測)    │ │ (指法評測)             │
│ [新建 #2]      │ │ [新建 #4]    │ │ [強化 #6]             │
└────────────────┘ └──────────────┘ └──────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  pedal_advisor.py    │
                    │  (踏板建議) [新建 #8]  │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │  dynamics_engine.py  │
                    │  (力度表情) [新建 #9]  │
                    └─────────────────────┘
```

---

## 實作進度

> **全部完成** — 2026-04-05 單輪對話實作

```
Phase 11a — 地基 ✅
  └─ #0 Viterbi 統一重構            → ai/viterbi_engine.py (新建)
     └─ 遷移 hmm.py / fingering_model.py / accompaniment_generator.py

Phase 11b — 生成引擎強化 ✅
  ├─ #1 旋律偵測升級                 → ai/melody_extractor.py (改寫)
  ├─ #3 伴奏生成深化                 → ai/accompaniment_generator.py (強化)
  └─ #5 指法生成精進                 → (內建於 viterbi_engine.py)

Phase 11c — 評測引擎建立 ✅
  ├─ #2 旋律評測 (新)                → ai/melody_evaluator.py (新建)
  ├─ #4 伴奏評測 (新)                → ai/accompaniment_evaluator.py (新建)
  └─ #6 指法評測強化                 → ai/fingering_evaluator.py (改寫)

Phase 11d — 靈魂注入 ✅
  ├─ #7 段落結構串連                 → ai/section_context.py (新建)
  ├─ #8 踏板建議 (新)                → ai/pedal_advisor.py (新建)
  └─ #9 力度表情 (新)                → ai/dynamics_engine.py (新建)
```

### 實作成果摘要

| # | 項目 | 檔案 | 類型 | 核心能力 |
|---|------|------|------|---------|
| 0 | Viterbi 統一重構 | `viterbi_engine.py` | 新建 | 通用 log-space Viterbi + beam pruning，CostModeAdapter |
| 1 | 旋律偵測升級 | `melody_extractor.py` | 改寫 | onset detection + vibrato 中位數濾波 + 自適應頻率 + confidence |
| 2 | 旋律評測 | `melody_evaluator.py` | 新建 | 音域/跳進比/樂句弧/節奏多樣性/DTW 相似度 (5 維度) |
| 3 | 伴奏生成深化 | `accompaniment_generator.py` | 強化 | section-aware 密度力度 + voice leading 平行五八度避免 + walking bass approach |
| 4 | 伴奏評測 | `accompaniment_evaluator.py` | 新建 | 碰撞率/smoothness/音域平衡/密度/和聲完整度 (5 維度) |
| 5 | 指法生成精進 | `viterbi_engine.py` | 內建 | 黑白鍵幾何成本 + 手指長度比例 + BPM-aware + 舒適跨距表 |
| 6 | 指法評測強化 | `fingering_evaluator.py` | 改寫 | 疲勞累積模型 + BPM 跨距容忍度 + 拇指下穿品質 |
| 7 | 段落結構串連 | `section_context.py` | 新建 | section→能量/密度/踏板/指法難度 統一 bridge |
| 8 | 踏板建議 | `pedal_advisor.py` | 新建 | legato/rhythmic/half 三種踏板法 + 評測 |
| 9 | 力度表情 | `dynamics_engine.py` | 新建 | phrase shaping (黃金比例) + accent pattern + articulation |

### 測試結果

| 測試項目 | 結果 |
|---------|------|
| Viterbi 指法 (RH C 大調上行) | `1-2-3-1-2-3-4-5` ✅ 教科書標準 |
| Viterbi 指法 (LH C 大調上行) | `5-4-3-5-4-3-2-1` ✅ 左手鏡像正確 |
| Viterbi 指法 (Db 黑鍵音階) | `2-3-1-2-3-4-1-2` ✅ 拇指避開黑鍵 |
| 旋律評測 (Mary Had a Little Lamb) | 80/100 ✅ |
| 伴奏評測 (C→Am 碰撞偵測) | 偵測到 2 處碰撞 ✅ |
| 指法評測 (4 指連續使用) | 7.6/100 ✅ 疲勞偵測 |
| 指法評測 (BPM=180 跨距) | 33.3/100 ✅ 超出 8 半音限制 |
| Section-aware 力度 (verse/chorus/bridge) | chorus>verse>bridge ✅ |
| 踏板建議 (legato/rhythmic/half) | 85-100 分 ✅ |
| 力度表情 (arch shape + accent) | 80-85 分 ✅ |
| 全模組整合匯入 | 11 模組全部成功 ✅ |

---

## 驗收標準

每個模組完成後，必須通過以下測試:

1. **單元測試**: 覆蓋 edge case（空輸入、單音、極端 BPM）✅
2. **樂理正確性**: 至少 3 首不同風格的歌曲驗證（pop / jazz / classical）— 待真實音檔測試
3. **QA Battle**: Generator 和 Evaluator 互相對抗，分數收斂 — 框架就緒
4. **Playwright UI 測試**: 瀑布流顯示正確（指法、踏板、力度標記）✅ 已驗證
5. **效能**: 單首歌的完整 pipeline < 3 秒（不含 BTC 和弦偵測）— 待基準測試

---

## Phase 11 前端整合 (2026-04-05 完成)

### API 端點 (20 個，+5 新)

| 端點 | 功能 |
|------|------|
| `GET /api/ai/evaluate-melody` | 旋律品質評分 (5 維度) |
| `GET /api/ai/evaluate-accompaniment` | 伴奏品質評分 (5 維度) |
| `GET /api/ai/pedal?style=legato` | 踏板建議 + 評分 |
| `GET /api/ai/dynamics` | 力度表情 + 評分 |
| `GET /api/ai/section-context` | 段落結構 + AI 參數時間軸 |
| `GET /api/ai/accompaniment` | 強化: +section_type +nocache +pedal +dynamics |
| `GET /api/ai/qa-battle` | QA Battle: 7 維度綜合品質對抗評測 |

### 瀑布流視覺強化

| 功能 | 說明 |
|------|------|
| **Velocity 光暈** | 二次方曲線 + 資料感知 normalize：弱音暗褐無光 → 強音爆亮光暈 (28px + 雙層疊加) |
| **踏板視覺化** | 綠色漸層區域 + 切換標記線 (虛線=半踏板) |
| **Articulation 標記** | staccato 圓點 / legato 弧線 |
| **AI 教師 HUD** | 右下角即時教學提示（呼吸綠點 + 上下文感知）：拇指穿越、大跳警告、換和弦預備、力度表情、踏板狀態、黑鍵群提醒 |
| **🔄 強制重新生成** | 清除快取按鈕，重新生成含踏板/力度的伴奏資料 |

### 實機測試截圖驗證

- Christopher Cross - Arthur's Theme: 踏板綠區 ✅ 力度光暈 ✅ AI 教師提示 ✅
- FIFTY FIFTY - Cupid: 瀑布流基本渲染 ✅ legato 弧線 ✅

---

## P1 驗證結果 (2026-04-05 完成)

### 端到端 Pipeline 測試 (3 首 × 7 模組)

| 歌曲 | 風格 | Melody | Accomp | Pedal | Dynamics | Playability | Overall | Verdict |
|------|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| ABBA - Dancing Queen | Pop | 64 | 69 | 99 | 77 | 70 | **72** | PASS |
| Nat King Cole - Autumn Leaves | Jazz | 66 | 63 | 99 | 73 | 70 | **68** | PASS |
| Queen - Bohemian Rhapsody | Rock | 64 | 55 | 80 | 77 | 70 | **62** | PASS |

**Pipeline 效能**: 每首歌 < 0.15 秒（不含 BTC 和弦偵測），遠低於 3 秒目標。

### QA Battle 統合架構

`ai/battle_qa.py` — 一鍵跑全部 evaluator 並產出 verdict：

```
GET /api/ai/qa-battle?path=...&style=Arpeggio&level=L2

{
  "verdict": "pass",           // pass / warn / fail
  "overall_score": 72,
  "scores": {
    "melody":        { "score": 64, "details": {...} },
    "accompaniment": { "score": 69, "details": {...} },
    "fingering":     { "score": 55, "left_hand": 29.5, "right_hand": 80.8 },
    "pedal":         { "score": 99 },
    "dynamics":      { "score": 77 },
    "playability":   { "score": 70, "warnings": [...] },
    "mix":           { "score": 100 }
  },
  "suggestions": [
    "旋律碰撞率偏高，建議調整伴奏音域避開旋律",
    "左手指法有不合理動作，建議啟用安全降級或降低難度"
  ]
}
```

### 評測參數校正

| 問題 | 修正 |
|------|------|
| Voice Leading 琶音誤判 | 只在和弦轉換邊界計算，不算同和弦內琶音跳躍 |
| 旋律碰撞假陽性 | 移除八度重疊判定，只計 ≤1 半音真碰撞 |
| 樂句弧線全曲一段 | 加入 MAX_PHRASE_NOTES=16 自動切割 |
| 平行五八度過度扣分 | 降低 penalty（pop/rock 常見手法）|

---

## P2 生產力工具 (2026-04-05 完成)

### 批次檔一覽 (W:\ 目錄下雙擊執行)

| 批次檔 | 用途 | 使用時機 | 預估時間 |
|--------|------|---------|---------|
| `start.bat` | 啟動 LiveChord 伺服器 | 每次開機後執行 | 即時 |
| `Run-SuperWorker.bat` | BTC 和弦 + pYIN 旋律雙引擎掃描 | NAS 新增音樂後，需要批次偵測和弦+旋律 | 依剩餘量 |
| `run_accompaniment_factory.bat` | 標準伴奏生成 (L1+L2 × 2 風格) | Super Worker 完成後，為所有歌生成伴奏+踏板+力度 | ~1hr/6000首 |
| `run_accompaniment_full.bat` | 完整伴奏 (L1~L3 × 5 風格 = 15 檔/首) | 想要所有風格×難度的完整組合時 | 較久 |
| `run_accompaniment_fast.bat` | 高速伴奏 (不含踏板/力度) | 只需要基本伴奏指法，不需要教學輔助資料時 | 最快 |
| `run_retrain_models.bat` | 重新訓練 AI 模型 | Super Worker 跑完一大批後，更新 Markov/Chord2Vec/Emission | ~2 分鐘 |
| `run_qa_test.bat` | QA 品質測試 (3 首基準歌曲) | 修改 AI 模組後，快速驗證品質未退化 | ~3 秒 |

### 建議執行順序

```
1. start.bat                      ← 開機啟動伺服器
2. Run-SuperWorker.bat             ← NAS 有新歌時跑（可背景）
3. run_retrain_models.bat          ← Super Worker 跑完一批後更新模型
4. run_accompaniment_factory.bat   ← 為已有和弦+旋律的歌生成伴奏
5. run_qa_test.bat                 ← 隨時可跑，驗證系統品質
```

### Super Worker 加速 (Phase 11)

| 項目 | 之前 | 之後 |
|------|------|------|
| adaptive_range | ON (雙重 pYIN) | OFF (fast_mode) |
| onset_detect | ON | OFF (fast_mode) |
| print 輸出 | 每首 3 行 | 靜音 |
| **速度** | 0.08 首/秒 | **~0.13 首/秒 (+60%)** |

---

## 生產驗證結果 (2026-04-05 NUC 實機)

### QA Battle Benchmark (NUC 15 Pro+ U9-285H)

```
Pop: Dancing Queen        PASS 72/100  (0.15s)
Jazz: Autumn Leaves       PASS 74/100  (0.03s)
Rock: Bohemian Rhapsody   PASS 67/100  (0.10s)
```

逐項分數:

| 歌曲 | melody | accomp | fingering | pedal | dynamics | playability | overall |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Dancing Queen | 64 | 69 | 55 | 99 | 77 | 70 | **72** |
| Autumn Leaves | 66 | 63 | 56 | 99 | 73 | 88 | **74** |
| Bohemian Rhapsody | 64 | 55 | 37 | 80 | 77 | 86 | **67** |

AI 建議:
- Dancing Queen: 旋律碰撞率 15%，左手指法需安全降級
- Autumn Leaves: 旋律碰撞率 37%，左手指法需安全降級
- Bohemian Rhapsody: 樂句弧線不明顯，旋律碰撞率 22%

### Accompaniment Factory 批次生產 (NUC 實機)

```
run_accompaniment_factory.bat 執行結果:
  Songs:     6,346
  Generated: 25,382 files (L1+L2 × Arpeggio+Block = 4 files/song)
  Errors:    0
  Time:      592s (9.9 min)
  Speed:     10.7 songs/sec, 42.9 files/sec
```

每個生成的檔案包含:
- 左手伴奏 events (含指法)
- 右手伴奏 events (含指法)
- 踏板建議 (legato/rhythmic by section)
- 力度表情 (velocity + articulation)
- 段落類型 (section_type)

### 系統效能摘要

| 操作 | 速度 | 環境 |
|------|------|------|
| QA Battle (單首) | 0.03~0.15s | NUC U9-285H |
| 伴奏生成 (單首 4 檔) | 0.09s | NUC U9-285H |
| 伴奏批次 (6,346 首) | 9.9 min | NUC U9-285H, 12 threads |
| Super Worker (和弦+旋律) | ~0.13 首/秒 | NUC U9-285H + RTX 5080 |
| 完整 Pipeline (無 BTC) | < 0.15s | 遠低於 3s 目標 |

---

## P3 教學體驗 (2026-04-05 完成)

### 鋼琴鍵指法顯示 (Hoffman Academy 風格)

在 88 鍵鋼琴的琴鍵底部顯示帶顏色圓圈 + 手指編號：

| 狀態 | 視覺 | 說明 |
|------|------|------|
| **正在彈奏** | 實心彩色圓圈 + 白邊 + 白字 | 藍=左手, 橙=右手 |
| **即將到來 (1 秒 lookahead)** | 脈動半透明圓圈 + 虛線邊框 | 提前預備手位 |

- 工具列 ✋ 按鈕可開關（預設開啟，localStorage 記憶）
- 黑鍵/白鍵自動調整圓圈位置和大小

### 踏板指示燈

鍵盤底部 bevel 區域即時顯示踏板狀態：

| 狀態 | 顯示 |
|------|------|
| Sustain 全踩 | 綠色亮條 + `PEDAL` 標籤 |
| Half-pedal | 淡綠色條 + `½ PED` 標籤 |
| 未踩 | 正常鍵盤外觀 |

### 指法安全降級修正

Phase 10 的指法 Evaluator 過度嚴格，會把整首歌的指法全面覆蓋為 RH=1 / LH=5。
Phase 11 修正：移除全面降級，只修正致命跨距 (>13 半音)，保留 Parncutt 模型的正確指法。

### 實機截圖驗證

- Arthur's Theme: 左手 ⑤③ + 右手 ④⑤ 正確顯示在琴鍵上 ✅
- 即將到來的指法脈動閃爍 ✅
- 踏板延音中提示 ✅
- AI 教師 HUD 準備換和弦提示 ✅

---

## 完整實作總結 (2026-04-05)

### 數據

| 指標 | 數量 |
|------|------|
| 新增 AI 模組 | 8 個 |
| 修改既有檔案 | 10+ 個 |
| 新增程式碼 | ~5,000 行 |
| API 端點 | 21 個 (含 6 新增) |
| 批次檔 | 6 個 |
| 伴奏已生成 | 25,382 檔 (6,346 首 × 4) |
| 生成錯誤 | 0 |
| QA 測試歌曲 | 3 首全 PASS |
| Pipeline 效能 | < 0.15s / 首 |

### 今日 Git Commits

| Commit | 內容 |
|--------|------|
| `16df2f9` | Rewind 按鈕 + Phase 11 計畫文件 |
| `2ca6d28` | Phase 11 全部 10 項 AI 模組實作 |
| `613072b` | API 5 端點 + 瀑布流踏板/力度/articulation |
| `76a2d3d` | 強制重新生成按鈕 (🔄) |
| `d3b5861` | AI 教師 HUD (右下角即時教學提示) |
| `a23bb7d` | Velocity 光暈 (亮度漸層) |
| `6acdfbd` | AI 教師 HUD 移至右下角 |
| `056a8c3` | Velocity 光暈大幅強化 (二次方+資料感知) |
| `43d17cc` | 文件更新: 前端整合結果 |
| `e27b6d4` | P1: QA Battle + 評測校正 |
| `87ed0ff` | 文件更新: P1 驗證結果 |
| `a098463` | P2: Super Worker fast_mode + 批次工廠升級 |
| `c3138bf` | 文件更新: 批次檔使用指南 |
| `ce91560` | QA 測試改用獨立 Python 腳本 |
| `4746df1` | 靜音 librosa n_fft 警告 |
| `9efdc39` | GPU semaphore 2→6 |
| `441874b` | 修正批次檔路徑 |
| `97ec682` | P3: 鍵盤指法圓圈 + 踏板指示燈 |
| `043a9cd` | 修正指法全面降級問題 |
| `67007f7` | 指法 lookahead (提前 1 秒顯示) |
