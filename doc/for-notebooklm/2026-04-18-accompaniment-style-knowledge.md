# LiveChord 伴奏風格知識文件

**建立日期**：2026-04-18
**狀態**：v1 — 初版
**用途**：給 NotebookLM 當 source，後續 AI coding agent 以此為領域 ground truth，改寫 LiveChord 的 AI 伴奏引擎，讓輸出從「死板的固定 pattern」升級為「有動感的風格化 pattern」。

---

## 0. 本文件是什麼、怎麼讀

LiveChord 是一個 piano 教學 / play-along 網站，使用者上傳音樂或貼 YouTube 連結 → 系統偵測和弦 → 生成一份「AI 伴奏」讓使用者可以聽範例演奏、或跟著彈。

目前 AI 伴奏的問題是：**節奏死板、velocity 平、每次遇到同一個和弦聽起來都一樣**。這份文件不是 code review，是 **音樂領域知識彙整**，把流行 / 抒情 / 爵士 / R&B / 拉丁 等風格中「為什麼好聽」的規則寫成可程式化的描述，讓 AI 拿去改 code 時，心裡有一張「鋼琴伴奏怎樣才不死」的地圖。

讀者有兩種：
1. **音樂家 / 使用者**：透過 NotebookLM 的問答、podcast、心智圖功能，理解每種伴奏風格的特徵與適用時機。
2. **AI coding agent**：把第 3、4、5、6、7 節當實作規格，生成新的伴奏 pattern 程式碼；第 8 節是自測清單。

**文件自成一體**：所有 schema、術語、MIDI 音名、欄位定義都在本文件內，不 reference 任何 repo 檔案或外部連結（NotebookLM 只看上傳的內容）。

---

## 1. 基礎術語與資料格式

### 1.1 MIDI 音高

MIDI pitch 是 0–127 整數，中央 C（C4）= 60。每半音 +1。同八度內：

| 音名 | MIDI (第 4 八度) |
|---|---|
| C | 60 |
| C# / Db | 61 |
| D | 62 |
| D# / Eb | 63 |
| E | 64 |
| F | 65 |
| F# / Gb | 66 |
| G | 67 |
| G# / Ab | 68 |
| A | 69 |
| A# / Bb | 70 |
| B | 71 |

其他八度 ±12：C3 = 48, C5 = 72, C2 = 36。

鋼琴常用範圍：左手 C2–C4（36–60），右手 C4–C6（60–84）。

### 1.2 MIDI Velocity

Velocity 是 0–127 的整數，代表力度。常用區間：

| 表情 | Velocity |
|---|---|
| ppp（極弱）| 20–30 |
| pp（很弱）| 35–45 |
| p（弱）| 50–60 |
| mp（中弱）| 60–70 |
| mf（中強）| 70–85 |
| f（強）| 85–100 |
| ff（很強）| 100–115 |
| fff（極強）| 115–127 |

**一個死板的引擎常犯的錯**：整首歌 velocity 都在 80–85，完全沒拉開。好的伴奏至少會用到 60–100 的範圍。

### 1.3 Beat、Subdivision、Time Signature

本文件假設 4/4 拍（最常見）。一個 bar（小節）= 4 拍。節奏細分：

- Quarter note（四分音符）：一拍 = 1.0 beat
- Eighth note（八分音符）：半拍 = 0.5 beat
- Sixteenth note（十六分音符）：四分之一拍 = 0.25 beat
- Triplet（三連音）：一拍三等分 = 0.333 beat

**強弱**（4/4 拍）：第 1 拍最強（downbeat），第 3 拍次強，第 2、4 拍弱（backbeat，但搖滾/流行常反過來強調 2、4）。

### 1.4 和弦輸入格式

AI 伴奏引擎收到的和弦 JSON 是：

```json
[
  {"time": 0.00, "end": 2.00, "chord": "C"},
  {"time": 2.00, "end": 4.00, "chord": "Am"},
  {"time": 4.00, "end": 6.00, "chord": "F"},
  {"time": 6.00, "end": 8.00, "chord": "G"}
]
```

欄位：
- `time`（float，秒）：和弦開始絕對時間
- `end`（float，秒）：和弦結束絕對時間
- `chord`（字串）：和弦符號，root + quality（C, Am, Dm7, G7, Fmaj7, C/E）

**注意**：輸入**沒有**風格、bpm、情緒、section 類型欄位。引擎必須自行推測 tempo、套用風格、決定強弱。

### 1.5 伴奏輸出格式（accData schema）

引擎產生的伴奏是一份 JSON：

```json
{
  "left_hand":  [ /* MIDI event objects */ ],
  "right_hand": [ /* MIDI event objects */ ],
  "style": "Pop Ballad",
  "section_type": "verse"
}
```

每個 MIDI event 物件：

```json
{
  "time":     0.00,     // 秒，絕對時間點（onset）
  "duration": 0.45,     // 秒，音長（decay time）
  "pitch":    60,       // MIDI pitch (0-127)
  "velocity": 85,       // MIDI velocity (0-127)
  "hand":     "left",   // "left" 或 "right"
  "finger":   1,        // 指法 1-5（1=拇指, 5=小指）
  "chord_tone": true    // 是否和弦內音（區分和聲音與裝飾音）
}
```

- `left_hand` 與 `right_hand` 是時間排序的 flat array（不是 per-beat 分組，是 per-onset）。
- 一個和弦可以拆成多個 event（例如 arpeggio 會有 4 個分散的 event）。
- `duration` 是該音該持續多久，不等於「到下一個音之間的時間」。

---

## 2. 為什麼目前伴奏聽起來死板（5 大死因）

這是現狀診斷，用來提醒 AI 改的時候要正面解決這些點，不要只換表面 pattern。

### 死因 1：onset 時間硬量化在 0.0 / 0.25 / 0.5 / 0.75

目前每個 pattern 都是 `(time_frac, pitches, velocity_ratio)` 的 tuple list，`time_frac` 只用 0.0、0.25、0.5、0.75 四個值（quarter 細分）。

後果：
- 沒有 swing（67/33 的長短八分感）
- 沒有 triplet（三連音）
- 沒有 16th 切分（0.125, 0.375, 0.625, 0.875）
- 沒有 push/pull（提前或延後的微量 offset）

### 死因 2：velocity 在 pattern 內是常數，沒有 phrase arc

每個 pitch 對應一個固定的 `velocity_ratio`，例如 walking bass 每個音都是 0.85×base。後果：
- 沒有重拍/弱拍對比（downbeat accent）
- 沒有句首強、句尾弱的 phrase arc
- 沒有 crescendo/decrescendo

### 死因 3：Humanize 是 post-hoc，不是 style-aware

現行 humanize 是在 pattern 決定完畢後，整段加 ±5ms timing jitter + ±5 velocity jitter。這雖然比完全機械強，但：
- 所有風格共用同一個 humanize 強度（ballad 跟 rock 抖動一樣，不合理）
- jitter 是隨機的，沒有節奏意義（不會把 backbeat 往後拉、不會把 pickup 往前拉）

### 死因 4：Section 密度已計算但未使用

引擎會分析 section（intro、verse、chorus、bridge、outro）並算出 `density_mult`，但這個乘數沒有被 pattern 消費。後果：
- verse 跟 chorus 的密度一樣
- intro 沒辦法做「漸入」
- outro 沒辦法做「稀疏收尾」

### 死因 5：右手永遠只有 Block 或 Gap-fill

右手目前兩種模式：
- **Block**：在 0.0 把整個和弦一起彈下去
- **Gap-fill**：只在左手空檔補單音

後果：
- 沒有 RH arpeggio（例如 Neo-Soul 的 16th 切分）
- 沒有 RH ostinato（重複動機）
- 沒有 RH 切分 stabs（爵士 comping 的核心）

---

## 3. 讓 MIDI 伴奏動感的 6 大機制

這 6 個機制正交（獨立可組合），每個都要處理才能避免死板。

### 機制 1：Velocity Contour（強弱曲線）

**規則**：在一個 4/4 小節裡，velocity 不應該是常數，應該依拍位套用強弱模板。

基本強弱模板（流行抒情）：
- Beat 1（downbeat）: +10 ~ +15
- Beat 2: −5 ~ 0
- Beat 3: +5 ~ +8
- Beat 4: −3 ~ 0
- 所有 offbeat（反拍）: −8 ~ −12

基本強弱模板（搖滾、流行副歌，強調 backbeat）：
- Beat 1: +5
- Beat 2: +10（backbeat）
- Beat 3: 0
- Beat 4: +12（backbeat）

**Phrase arc**：一個 4-bar phrase 通常 velocity 從第 1 bar 漸強到第 3 bar，第 4 bar 稍弱做 breath。在每個 event 的 base velocity 上額外加 0~+6 的 arc offset。

**AI 實作**：引入 `velocity_curve(beat_pos, section, style)` 函數，回傳一個 offset，加在 base velocity 上。pattern 不再儲存絕對 velocity_ratio，只存「角色」（例如 accent、normal、ghost），curve 函數根據角色 + 拍位決定最終 velocity。

### 機制 2：Beat-Specific Micro-timing

**規則**：不同拍位的 onset 應有不同方向的微位移，不是隨機 jitter。

建議模板：
- Downbeat（Beat 1）：−5 ~ 0 ms（稍微提前，有推進感）
- Beat 3：−3 ~ 0 ms
- Weak beats（Beat 2, 4）：0 ~ +5 ms（稍微延後，有放鬆感）
- Offbeat 8th（反拍）：+2 ~ +10 ms（swing feel）
- 16th 切分音：+5 ~ +15 ms（R&B 的 laid-back）
- 切分預備音（push）：−10 ~ −20 ms（跨小節 push）

**AI 實作**：`timing_offset(beat_pos, role, style)` 函數。ballad 整體幅度小（±3ms），jazz 可達 ±20ms。絕對不要隨機對所有 event 同強度抖。

### 機制 3：Syncopation（切分）

**規則**：不要所有 onset 都在正拍。切分是把原本該在正拍的音挪到半拍或四分之一拍前 / 後。

常見切分 pattern（4/4 一拍內細分為 1-e-&-a 即 0.0-0.25-0.5-0.75）：
- **Anticipation（預備）**：Beat 3 的音提前到 Beat 2.75（& of 2），讓 Beat 3 空一拍的張力。
- **Push（推）**：小節末一個音提前 0.25 beat 到下一小節的準備位。
- **Charleston**：Beat 1 + Beat 2.5（& of 2），形成爵士經典切分。
- **Son Clave**：1, 1.5, 2.5, 4, 4.5（拉丁、bossa nova 的根骨架）。

切分密度依風格：
- Ballad：低（0–1 切分/bar）
- Jazz Swing：中（2–3 切分/bar）
- Neo-Soul, R&B：高（4+ 切分/bar）

### 機制 4：Voicing Variation（聲位變化）

**規則**：同一個 C 和弦不該每次都是 C–E–G 緊密排列。要依脈絡變 inversion、voice leading、音域。

策略：
- **Inversion（轉位）**：C → E/C, G/C, C/E（根在第三音、第五音、或 bass 換）。和弦進行時，選擇讓上三音移動最少的 voicing，稱為 **smooth voice leading**。
- **Spread voicing（寬距）**：bass + gap + upper structure。例：C4–G4–C5–E5 而不是 C4–E4–G4。
- **Drop 2 voicing（降 2 聲位）**：爵士常用，把第二高的音降 8 度。Cmaj7 原為 C–E–G–B，drop 2 變 E–G–B–C。
- **第 9/11/13 音**：Cmaj7 升級 Cmaj9 加入 D。增加色彩但不破壞和聲功能。

### 機制 5：Density Variation by Section

**規則**：intro / verse / chorus / bridge / outro 的音符密度應明顯拉開。

建議 density 乘數（相對 baseline = 1.0）：
- Intro: 0.4–0.7（稀疏，建立氣氛）
- Verse: 0.7–1.0（穩定陪襯）
- Pre-chorus: 1.0–1.2（漸強）
- Chorus: 1.2–1.6（最密集，推向高潮）
- Bridge: 0.6–1.0（反差）
- Outro: 0.3–0.6（淡出）

實作做法：
- **稀化**：每個 onset 有機率被 drop（density 0.5 → drop 50% 的 fill events，保留 downbeat）
- **加密**：在空檔加入 ghost note、passing tone、16th subdivision

### 機制 6：Humanize（保留，但細化）

既有的 random jitter 改成 style-aware：

| 風格 | Timing jitter (ms) | Velocity jitter |
|---|---|---|
| Ballad | ±2 | ±3 |
| Pop | ±4 | ±4 |
| Rock | ±3 | ±6 |
| Jazz Swing | ±8 | ±5 |
| Neo-Soul | ±10 | ±6 |
| Ambient | ±15 | ±4 |

**重點**：jitter 是最後一層微調，不能取代機制 1–5。如果前 5 個機制沒做好，再多 jitter 也只是把死板變成「抖動的死板」。

---

## 4. 伴奏風格字典

8 個風格，每個給：骨架、LH 規則、RH 規則、velocity 曲線、適用時機、tempo 範圍。

### 4.1 Pop Ballad（流行抒情）

**骨架**：4/4，慢板 60–80 BPM。
**LH**：根音 + 五度，每拍一個音，重拍強。例 C 和弦 = C2 (Beat 1) + G2 (Beat 3)。
**RH**：整個和弦 block，在 Beat 1 彈一次，音長到下一個和弦（long sustain），或 Beat 1 + Beat 3。
**Velocity**：Beat 1 = 85, Beat 3 = 75, 其他 = 65。
**適用**：抒情慢歌、verse、情感鋪陳、獨奏段。
**典型例子**：John Legend《All of Me》verse、Adele《Someone Like You》開頭。

### 4.2 Piano Driving 8ths（八分推進）

**骨架**：4/4, 80–120 BPM。
**LH**：根音八分音符重複 8 個（或根—五度—根—五度交替）。
**RH**：和弦整塊每拍一次（四分音符 block），或每半拍一次（八分音符 block）。
**Velocity**：Beat 1 +10, Beat 3 +5, offbeat −5。
**適用**：流行副歌、pop rock verse→chorus 推進、情緒升溫段。
**典型例子**：Journey《Don't Stop Believin'》、Coldplay《Clocks》的衍生 pattern。

### 4.3 Arpeggio Flow（分解和弦）

**骨架**：4/4, 70–100 BPM，每拍 2 或 4 音。
**LH**：根音每小節一次（或 1 + 3 拍各一次）。
**RH**：和弦分解為 1–3–5–3（4 音循環）或 1–3–5–8–5–3（6 音波浪）。以 16 分音符流動。
**Velocity**：第一音強、之後遞減；下一組重新強。
**適用**：抒情、冥想、新世紀、folk ballad、kirk fanclub-style verse。
**典型例子**：Pachelbel《Canon in D》、Coldplay《The Scientist》RH arpeggio。

### 4.4 Alberti Bass

**骨架**：4/4, 60–110 BPM, 16th 流動。
**LH**：低—高—中—高 循環（例 C 和弦 = C3–G3–E3–G3），每個音 16th 長度。
**RH**：melody 或 long sustain chord。
**Velocity**：低音稍強（C3 = 75），中高音弱（60–65）。
**適用**：古典風、moderate ballad、復古流行、教學曲。
**典型例子**：Mozart Piano Sonata K.545、Taylor Swift 部分古典編曲。

### 4.5 Swing Jazz Comping（爵士切分和聲）

**骨架**：4/4 swing（第 2 個 8th 延後到 triplet 位置，長短比約 67:33）。
**LH**：Shell voicing（根 + 3 + 7，或根 + 7），不搶拍，常在 Beat 1 或 Beat 3 切入，偶爾 push 到前半拍。
**RH**：Upper structure 三四音和弦（extensions: 9, 13），切分 stab。Charleston pattern 常見（Beat 1 + Beat 2.5）。
**Velocity**：stab 強（90+），hold 音弱（65–70），依 comping 邏輯與 soloist 對話。
**適用**：Jazz standards、bebop backing、爵士三重奏 piano。
**典型例子**：Bill Evans、Red Garland comping style。

### 4.6 Walking Bass（爵士行進 bass）

**骨架**：4/4, 80–180 BPM，每拍一個 LH 音。
**LH**：走 chord tone + passing tone + approach tone。每小節從和弦 root 開始，第 4 拍走 chromatic approach（半音逼近）到下一個和弦 root。
  - 例：C → F，LH 走 C–E–G–A/Ab（Ab 是 F 的半音下方逼近音）→ F
**RH**：偶爾 shell voicing 切分 stabs，不每拍都彈。
**Velocity**：LH 均勻 75–80，但第 1 拍 + 3 拍 +5 accent；RH stabs 90+。
**適用**：Jazz swing、trio、blues、cocktail piano。
**典型例子**：Ray Brown bass lines、Oscar Peterson trio。

### 4.7 Bossa Nova

**骨架**：4/4 或 2/4，80–140 BPM。Clave-based 切分。
**LH**：根音 + 五度兩拍交替（Beat 1 = root, Beat 3 = 5th），或加入 syncopation（Beat 3.5 提前）。
**RH**：partido-alto pattern — 和弦切分：
  - Beat 1（短）
  - & of 2（強）
  - Beat 4（強）
  - 有時 & of 4（push 到下一小節）
**Velocity**：LH 穩定 65–75，RH 切分音 80–90，但整體輕柔，不過 100。
**適用**：Bossa、巴西流行、輕爵士、café music。
**典型例子**：《The Girl from Ipanema》、João Gilberto 彈唱。

### 4.8 Neo-Soul 16ths

**骨架**：4/4, 70–95 BPM, 16th 切分。
**LH**：根音 + 根音 slide（從下方半音往上）+ octave jump。切分密集。
**RH**：複雜 voicing（9, 11, 13, sus, add 音），16th ghost note 交織。經常用 drop-2 或 stacked 4ths voicing。
**Velocity**：ghost note 30–50，accent 90+，對比極大。
**適用**：Neo-soul、modern R&B、lo-fi hip-hop piano。
**典型例子**：D'Angelo《Brown Sugar》、Robert Glasper trio。

---

## 5. 風格選擇與適用時機決策樹

AI 選 style 時的優先序：

1. **有使用者指定 → 用指定**。
2. **Tempo 推測**（從 chord duration 平均推 bpm，或從 chord changes per bar 推）：
   - <70 BPM → Ballad / Arpeggio / Ambient
   - 70–95 → Ballad / Alberti / Neo-Soul / Bossa
   - 95–130 → Pop Driving 8ths / Swing Comping / Walking
   - >130 → Walking / Swing 快板
3. **Chord quality 檢查**：
   - 有 maj7/m7/dom7/9/13 → 偏向 Jazz / Neo-Soul
   - 三和弦為主（C, Am, F, G）→ Pop / Rock / Ballad
   - 有減七、增和弦 → Jazz
4. **Section 調整**：
   - Intro / Outro → 降一級密度（Pop Driving → Pop Ballad）
   - Chorus → 升一級密度（Ballad → Pop Driving）
   - Bridge → 換 style 做反差

---

## 6. C 大調具體 MIDI 範例

所有範例使用 C 大調、4 個 2 秒和弦的進行 ``C | Am | F | G``，共 8 秒，4/4 拍 = 每拍 0.5 秒（120 BPM）。

### 6.1 Pop Ballad（§4.1）

LH：
```json
[
  {"time": 0.0, "duration": 0.9, "pitch": 36, "velocity": 85, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 1.0, "duration": 0.9, "pitch": 43, "velocity": 75, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 2.0, "duration": 0.9, "pitch": 33, "velocity": 85, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 3.0, "duration": 0.9, "pitch": 40, "velocity": 75, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 4.0, "duration": 0.9, "pitch": 29, "velocity": 85, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 5.0, "duration": 0.9, "pitch": 36, "velocity": 75, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 6.0, "duration": 0.9, "pitch": 31, "velocity": 85, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 7.0, "duration": 0.9, "pitch": 38, "velocity": 75, "hand": "left", "finger": 2, "chord_tone": true}
]
```

RH（C, Am, F, G 各彈一次 block）：
```json
[
  {"time": 0.0,  "duration": 1.9, "pitch": 60, "velocity": 80, "hand": "right", "finger": 1, "chord_tone": true},
  {"time": 0.0,  "duration": 1.9, "pitch": 64, "velocity": 75, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 0.0,  "duration": 1.9, "pitch": 67, "velocity": 78, "hand": "right", "finger": 5, "chord_tone": true},
  {"time": 2.0,  "duration": 1.9, "pitch": 57, "velocity": 80, "hand": "right", "finger": 1, "chord_tone": true},
  {"time": 2.0,  "duration": 1.9, "pitch": 60, "velocity": 75, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 2.0,  "duration": 1.9, "pitch": 64, "velocity": 78, "hand": "right", "finger": 5, "chord_tone": true},
  {"time": 4.0,  "duration": 1.9, "pitch": 57, "velocity": 80, "hand": "right", "finger": 1, "chord_tone": true},
  {"time": 4.0,  "duration": 1.9, "pitch": 60, "velocity": 75, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 4.0,  "duration": 1.9, "pitch": 65, "velocity": 78, "hand": "right", "finger": 5, "chord_tone": true},
  {"time": 6.0,  "duration": 1.9, "pitch": 59, "velocity": 82, "hand": "right", "finger": 1, "chord_tone": true},
  {"time": 6.0,  "duration": 1.9, "pitch": 62, "velocity": 77, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 6.0,  "duration": 1.9, "pitch": 67, "velocity": 80, "hand": "right", "finger": 5, "chord_tone": true}
]
```
（注意：Voice leading 已優化 — Am 用 A–C–E, F 用 A–C–F，上三音只動最少）

### 6.2 Piano Driving 8ths（§4.2）

LH 八分音符根音重複（C 和弦下 C2 敲 4 次）：
```json
[
  {"time": 0.0,  "duration": 0.45, "pitch": 36, "velocity": 88, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 0.5,  "duration": 0.45, "pitch": 36, "velocity": 72, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 1.0,  "duration": 0.45, "pitch": 36, "velocity": 78, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 1.5,  "duration": 0.45, "pitch": 36, "velocity": 70, "hand": "left", "finger": 5, "chord_tone": true}
]
```
（重複到其他和弦：Am → A1=33, F → F1=29, G → G1=31。Pattern 相同。）

RH 每拍一個 block chord：
```json
[
  {"time": 0.0,  "duration": 0.45, "pitch": 60, "velocity": 85, "hand": "right"},
  {"time": 0.0,  "duration": 0.45, "pitch": 64, "velocity": 80, "hand": "right"},
  {"time": 0.0,  "duration": 0.45, "pitch": 67, "velocity": 82, "hand": "right"},
  {"time": 0.5,  "duration": 0.45, "pitch": 60, "velocity": 72, "hand": "right"},
  {"time": 0.5,  "duration": 0.45, "pitch": 64, "velocity": 68, "hand": "right"},
  {"time": 0.5,  "duration": 0.45, "pitch": 67, "velocity": 70, "hand": "right"},
  {"time": 1.0,  "duration": 0.45, "pitch": 60, "velocity": 80, "hand": "right"},
  {"time": 1.0,  "duration": 0.45, "pitch": 64, "velocity": 75, "hand": "right"},
  {"time": 1.0,  "duration": 0.45, "pitch": 67, "velocity": 77, "hand": "right"},
  {"time": 1.5,  "duration": 0.45, "pitch": 60, "velocity": 70, "hand": "right"},
  {"time": 1.5,  "duration": 0.45, "pitch": 64, "velocity": 66, "hand": "right"},
  {"time": 1.5,  "duration": 0.45, "pitch": 67, "velocity": 68, "hand": "right"}
]
```

### 6.3 Arpeggio Flow 1–3–5–3（§4.3）

C 和弦 2 秒，四拍 16 個 16 分音符，RH 跑 C–E–G–E 循環 4 次：
```json
[
  {"time": 0.000, "duration": 0.12, "pitch": 60, "velocity": 82, "hand": "right", "finger": 1, "chord_tone": true},
  {"time": 0.125, "duration": 0.12, "pitch": 64, "velocity": 70, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 0.250, "duration": 0.12, "pitch": 67, "velocity": 75, "hand": "right", "finger": 5, "chord_tone": true},
  {"time": 0.375, "duration": 0.12, "pitch": 64, "velocity": 68, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 0.500, "duration": 0.12, "pitch": 60, "velocity": 78, "hand": "right", "finger": 1, "chord_tone": true},
  {"time": 0.625, "duration": 0.12, "pitch": 64, "velocity": 66, "hand": "right", "finger": 3, "chord_tone": true},
  {"time": 0.750, "duration": 0.12, "pitch": 67, "velocity": 72, "hand": "right", "finger": 5, "chord_tone": true},
  {"time": 0.875, "duration": 0.12, "pitch": 64, "velocity": 66, "hand": "right", "finger": 3, "chord_tone": true}
]
```
（每個 4 音 group：第 1 音 82, 第 2 音 70, 第 3 音 75, 第 4 音 68 — 有清楚 velocity 曲線；下一個 group 整體略降）

LH：每 2 秒彈一個根音 long sustain：
```json
[
  {"time": 0.0, "duration": 1.95, "pitch": 36, "velocity": 70, "hand": "left", "finger": 5, "chord_tone": true}
]
```

### 6.4 Alberti Bass（§4.4）

C 和弦 16th Alberti（C3–G3–E3–G3 重複，每音 0.125 秒）：
```json
[
  {"time": 0.000, "duration": 0.12, "pitch": 48, "velocity": 78, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 0.125, "duration": 0.12, "pitch": 55, "velocity": 62, "hand": "left", "finger": 1, "chord_tone": true},
  {"time": 0.250, "duration": 0.12, "pitch": 52, "velocity": 68, "hand": "left", "finger": 3, "chord_tone": true},
  {"time": 0.375, "duration": 0.12, "pitch": 55, "velocity": 62, "hand": "left", "finger": 1, "chord_tone": true},
  {"time": 0.500, "duration": 0.12, "pitch": 48, "velocity": 75, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 0.625, "duration": 0.12, "pitch": 55, "velocity": 60, "hand": "left", "finger": 1, "chord_tone": true},
  {"time": 0.750, "duration": 0.12, "pitch": 52, "velocity": 66, "hand": "left", "finger": 3, "chord_tone": true},
  {"time": 0.875, "duration": 0.12, "pitch": 55, "velocity": 60, "hand": "left", "finger": 1, "chord_tone": true}
]
```

### 6.5 Swing Jazz Comping（§4.5）— Charleston Pattern

C 和弦 2 秒，4/4 120 BPM。Charleston = Beat 1 + & of 2（Beat 2.5 = 1.25 秒位置）。

RH shell stabs（C7 shell = E4 + Bb3，但 C maj 用 E + B）：
```json
[
  {"time": 0.0,  "duration": 0.35, "pitch": 64, "velocity": 90, "hand": "right", "finger": 2, "chord_tone": true},
  {"time": 0.0,  "duration": 0.35, "pitch": 71, "velocity": 85, "hand": "right", "finger": 5, "chord_tone": true},
  {"time": 1.25, "duration": 0.30, "pitch": 64, "velocity": 78, "hand": "right", "finger": 2, "chord_tone": true},
  {"time": 1.25, "duration": 0.30, "pitch": 71, "velocity": 75, "hand": "right", "finger": 5, "chord_tone": true}
]
```
（注意：只兩次 stab，之間留白讓 soloist 呼吸）

LH shell（root + 7th）：
```json
[
  {"time": 0.02, "duration": 1.8, "pitch": 48, "velocity": 70, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 0.02, "duration": 1.8, "pitch": 59, "velocity": 65, "hand": "left", "finger": 1, "chord_tone": true}
]
```
（LH onset 0.02 秒 — 稍後於 RH，形成 spread feel）

### 6.6 Walking Bass（§4.6）

C → Am → F → G 每小節 4 拍走音。每拍一音：
```json
[
  {"time": 0.0, "duration": 0.48, "pitch": 36, "velocity": 80, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 0.5, "duration": 0.48, "pitch": 40, "velocity": 72, "hand": "left", "finger": 3, "chord_tone": false},
  {"time": 1.0, "duration": 0.48, "pitch": 43, "velocity": 78, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 1.5, "duration": 0.48, "pitch": 44, "velocity": 72, "hand": "left", "finger": 1, "chord_tone": false},

  {"time": 2.0, "duration": 0.48, "pitch": 45, "velocity": 80, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 2.5, "duration": 0.48, "pitch": 48, "velocity": 72, "hand": "left", "finger": 3, "chord_tone": true},
  {"time": 3.0, "duration": 0.48, "pitch": 52, "velocity": 78, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 3.5, "duration": 0.48, "pitch": 53, "velocity": 72, "hand": "left", "finger": 1, "chord_tone": false},

  {"time": 4.0, "duration": 0.48, "pitch": 53, "velocity": 80, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 4.5, "duration": 0.48, "pitch": 57, "velocity": 72, "hand": "left", "finger": 3, "chord_tone": true},
  {"time": 5.0, "duration": 0.48, "pitch": 60, "velocity": 78, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 5.5, "duration": 0.48, "pitch": 55, "velocity": 72, "hand": "left", "finger": 1, "chord_tone": false},

  {"time": 6.0, "duration": 0.48, "pitch": 55, "velocity": 80, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 6.5, "duration": 0.48, "pitch": 59, "velocity": 72, "hand": "left", "finger": 3, "chord_tone": true},
  {"time": 7.0, "duration": 0.48, "pitch": 62, "velocity": 78, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 7.5, "duration": 0.48, "pitch": 48, "velocity": 72, "hand": "left", "finger": 1, "chord_tone": false}
]
```
（`chord_tone: false` 的音是 passing note 或 chromatic approach。例如 Beat 4 走到下一個 chord root 的半音下方：C→Am 走 C–E–G–**G#**→A。第 4 小節走 **G**→**B**→**D**→**C** 最後音是下一小節 C 的 pickup。）

### 6.7 Bossa Nova（§4.7）— Partido Alto

LH 根音 + 五度，兩拍一組（每小節兩組）：
```json
[
  {"time": 0.0, "duration": 0.95, "pitch": 36, "velocity": 72, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 1.0, "duration": 0.95, "pitch": 43, "velocity": 68, "hand": "left", "finger": 1, "chord_tone": true}
]
```

RH partido-alto 切分（Beat 1 短 + & of 2 + Beat 4 + & of 4 跨小節 push）：
```json
[
  {"time": 0.0,  "duration": 0.20, "pitch": 60, "velocity": 75, "hand": "right"},
  {"time": 0.0,  "duration": 0.20, "pitch": 64, "velocity": 72, "hand": "right"},
  {"time": 0.0,  "duration": 0.20, "pitch": 67, "velocity": 74, "hand": "right"},
  {"time": 0.75, "duration": 0.40, "pitch": 60, "velocity": 82, "hand": "right"},
  {"time": 0.75, "duration": 0.40, "pitch": 64, "velocity": 78, "hand": "right"},
  {"time": 0.75, "duration": 0.40, "pitch": 67, "velocity": 80, "hand": "right"},
  {"time": 1.50, "duration": 0.35, "pitch": 60, "velocity": 85, "hand": "right"},
  {"time": 1.50, "duration": 0.35, "pitch": 64, "velocity": 80, "hand": "right"},
  {"time": 1.50, "duration": 0.35, "pitch": 67, "velocity": 82, "hand": "right"},
  {"time": 1.875,"duration": 0.15, "pitch": 57, "velocity": 72, "hand": "right"},
  {"time": 1.875,"duration": 0.15, "pitch": 60, "velocity": 68, "hand": "right"},
  {"time": 1.875,"duration": 0.15, "pitch": 64, "velocity": 70, "hand": "right"}
]
```
（注意：最後 0.875 = &-of-4 是 push，預先彈下一小節 Am 和弦的聲位）

### 6.8 Neo-Soul 16ths（§4.8）

C 和弦 2 秒，extension 到 Cmaj9（加 D5）。LH root slide：
```json
[
  {"time": 0.0,   "duration": 0.20, "pitch": 35, "velocity": 50, "hand": "left", "finger": 5, "chord_tone": false},
  {"time": 0.05,  "duration": 0.40, "pitch": 36, "velocity": 85, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 0.875, "duration": 0.15, "pitch": 48, "velocity": 78, "hand": "left", "finger": 2, "chord_tone": true},
  {"time": 1.25,  "duration": 0.30, "pitch": 36, "velocity": 72, "hand": "left", "finger": 5, "chord_tone": true},
  {"time": 1.75,  "duration": 0.20, "pitch": 43, "velocity": 68, "hand": "left", "finger": 2, "chord_tone": true}
]
```
（0.0 是 B2=35 作為 slide-up 的 grace note，0.05 主音 C2；後面 0.875、1.25、1.75 是切分 subdivisions）

RH Cmaj9 voicing（E–G–B–D，stacked 3rds）加 ghost 16ths：
```json
[
  {"time": 0.125, "duration": 0.10, "pitch": 64, "velocity": 40, "hand": "right"},
  {"time": 0.125, "duration": 0.10, "pitch": 67, "velocity": 38, "hand": "right"},
  {"time": 0.250, "duration": 0.20, "pitch": 64, "velocity": 88, "hand": "right"},
  {"time": 0.250, "duration": 0.20, "pitch": 67, "velocity": 85, "hand": "right"},
  {"time": 0.250, "duration": 0.20, "pitch": 71, "velocity": 88, "hand": "right"},
  {"time": 0.250, "duration": 0.20, "pitch": 74, "velocity": 82, "hand": "right"},
  {"time": 0.625, "duration": 0.10, "pitch": 64, "velocity": 45, "hand": "right"},
  {"time": 0.750, "duration": 0.35, "pitch": 64, "velocity": 80, "hand": "right"},
  {"time": 0.750, "duration": 0.35, "pitch": 67, "velocity": 75, "hand": "right"},
  {"time": 0.750, "duration": 0.35, "pitch": 71, "velocity": 80, "hand": "right"},
  {"time": 1.375, "duration": 0.10, "pitch": 67, "velocity": 42, "hand": "right"},
  {"time": 1.500, "duration": 0.30, "pitch": 64, "velocity": 85, "hand": "right"},
  {"time": 1.500, "duration": 0.30, "pitch": 67, "velocity": 80, "hand": "right"},
  {"time": 1.500, "duration": 0.30, "pitch": 71, "velocity": 85, "hand": "right"},
  {"time": 1.500, "duration": 0.30, "pitch": 74, "velocity": 78, "hand": "right"}
]
```
（ghost notes velocity 40, stab velocity 85+, 對比接近 1:2）

---

## 7. 給 AI Coding 的實作建議

這一節是給 coding agent 的 concrete 重構方向。

### 7.1 改 STYLE_DICT 的 tuple 格式

現有：`(time_frac, [pitch_indices], velocity_ratio)`
建議升級為物件：

```python
{
    "onset": 0.5,            # 拍位（0–4 in 4/4）
    "pitches": [0, 2, 4],    # chord tone indices (0=root, 2=3rd, 4=5th, 6=7th, 8=9th)
    "role": "accent",        # accent / normal / ghost / passing
    "articulation": "stab",  # stab / hold / staccato / legato
    "timing_push": 0,        # ms 相對於 onset 的偏移（可為負）
    "duration_beats": 0.25,  # 持續拍數
}
```

velocity 不再由 pattern 決定，改由 `velocity_curve(role, beat_pos, section, style)` 函數決定。

### 7.2 引入 MicroTiming 矩陣

每個風格定義一個 16th 格子的 timing offset 表：

```python
SWING_JAZZ_TIMING = {
    0.00: -5,   # downbeat 提前 5ms
    0.25: +15,  # & of 1 延後 15ms（swing）
    0.50: -3,   # beat 2
    0.75: +15,  # & of 2 延後
    # ...
}
```

ballad 風格的表所有值都在 ±3ms 之內；jazz 可到 ±20ms。

### 7.3 實際消費 density_mult

在 pattern 生成後、humanize 之前，對每個 event 套：

```python
def apply_density(events, density_mult):
    if density_mult < 1.0:
        # 稀化：按 role 保留度 drop 部分 events
        events = [e for e in events
                  if e["role"] == "accent"                    # accent 必留
                  or random.random() < density_mult]          # 其他機率保留
    elif density_mult > 1.0:
        # 加密：在空檔插 ghost 16ths
        events = interleave_ghosts(events, density_mult)
    return events
```

### 7.4 Voice Leading 取代固定 voicing

當從和弦 A 轉到和弦 B，RH voicing 應選「與 A 的上三音移動總半音數最少」的 B inversion。

虛擬碼：
```python
def choose_voicing(chord_b, previous_voicing_a):
    candidates = all_inversions(chord_b)
    return min(candidates, key=lambda v: voice_distance(v, previous_voicing_a))
```

### 7.5 Phrase Arc Velocity

每 4 小節（phrase）對整體 velocity 加一層 arc：

```python
def phrase_arc_offset(bar_in_phrase):
    # 4-bar phrase: bars 0,1,2,3 → offsets 0, +2, +4, -1
    return [0, 2, 4, -1][bar_in_phrase % 4]
```

### 7.6 風格 + section 的二維查表

不要只用 style 決定 pattern，應依 (style, section) 查。例：
- (Pop Ballad, verse) → density 0.9, RH=block-long
- (Pop Ballad, chorus) → density 1.3, RH=block-each-beat
- (Pop Ballad, bridge) → density 0.7, RH=arpeggio-flow

---

## 8. AI 改完後的自測清單

Coding agent 改完，請按以下清單驗證。每項**聽得出差異**才算 pass，不是看 code 有沒有寫到。

### 8.1 同和弦不死板
- [ ] 同一個 C 和弦在 verse 出現兩次，timing 和 velocity 有明顯差異（不是完全複製貼上）
- [ ] 連續 4 次 C 和弦，4 次的 onset 時間抖動分布均勻，不是固定 ±5ms

### 8.2 Section 有對比
- [ ] Intro 的音符密度 < verse < chorus（用 event 數 / 秒 統計）
- [ ] Chorus 的 velocity 平均比 verse 高 8–15

### 8.3 切分真的有切
- [ ] Jazz/Bossa 風格的 RH event 有 > 30% 落在非正拍（0.25/0.75/...）
- [ ] Charleston pattern 可辨識 — 有 Beat 1 + Beat 2.5 的 stab 組合

### 8.4 Swing 比例正確
- [ ] Swing jazz 風格，相鄰 8 分音符的長短比接近 67:33（triplet feel），不是 50:50

### 8.5 Voice leading 順
- [ ] RH 連續三個和弦 C → Am → F，上聲部每次移動最多 2 個半音，不會 octave jump

### 8.6 Phrase arc 可聞
- [ ] 第 3 小節的 velocity 平均比第 1 小節高（phrase crescendo）
- [ ] 第 4 小節最後半拍的音 velocity 略降（breath）

### 8.7 Density 乘數真的用了
- [ ] 把 density 從 0.5 切到 1.5，輸出 event 數確實倍增，不是相同

### 8.8 Humanize 合理而非混亂
- [ ] Ballad 風格 timing 抖動 ≤ ±4ms；jazz 風格 timing 抖動可達 ±20ms
- [ ] 沒有 event 被抖到跨越下一個 onset（不會兩個音撞在一起）

---

## 9. 延伸方向（v2 預留）

以下不在 v1 範圍，但將來可加：

- **Fills / Break**：每 4 或 8 小節在 phrase 末尾加一個 fill（drum 世界的術語，piano 可用小 run、arpeggio 下行、或 dominant 7 停頓）
- **Dynamics map per song**：整首歌的 velocity 包絡 — intro 弱、chorus 最強、outro 淡出
- **Section 轉場**：section 交界處加一個 pickup 音、一個 suspension、或一個 silence bar
- **Listener-aware 演算**：若前幾小節是 RH 空白（使用者在獨奏）→ 伴奏應更稀；若 RH 密集 → 伴奏可跟進
- **Genre classification**：從 chord progression pattern（例 ii-V-I 爵士、I-V-vi-IV 流行、12-bar blues）自動推 genre，再選 style
- **Micro-articulation**：staccato / legato / tenuto，目前只有 duration 區分，未來可加 attack curve

---

## 附錄 A：和弦符號解讀對照

| 符號 | 組成音（以 C 為 root）| MIDI (closed C4) |
|---|---|---|
| C | C E G | 60 64 67 |
| Cm | C Eb G | 60 63 67 |
| C7 | C E G Bb | 60 64 67 70 |
| Cmaj7 | C E G B | 60 64 67 71 |
| Cm7 | C Eb G Bb | 60 63 67 70 |
| Cdim | C Eb Gb | 60 63 66 |
| Cdim7 | C Eb Gb Bbb(=A) | 60 63 66 69 |
| Caug | C E G# | 60 64 68 |
| Csus4 | C F G | 60 65 67 |
| Cadd9 | C E G D | 60 64 67 62 |
| Cmaj9 | C E G B D | 60 64 67 71 74 |
| C6 | C E G A | 60 64 67 69 |
| C/E | E C G | 52 60 67 |
| C/G | G C E | 55 60 64 |

其他 root 的和弦用相同結構，只要把 root 的 MIDI 值平移：
- D: +2 半音（D = C + 2 = 62）
- E: +4, F: +5, G: +7, A: +9, B: +11

## 附錄 B：常見流行和弦進行（C 大調）

| 名稱 | 進行 | 感覺 |
|---|---|---|
| I-V-vi-IV | C-G-Am-F | 最普遍流行，anthemic |
| vi-IV-I-V | Am-F-C-G | 抒情、sentimental |
| I-vi-IV-V | C-Am-F-G | 50s doo-wop、復古 |
| ii-V-I | Dm7-G7-Cmaj7 | 爵士、jazz standards 骨架 |
| I-IV-I-V | C-F-C-G | 鄉村、簡單民謠 |
| 12-bar blues | C-C-C-C-F-F-C-C-G-F-C-G | blues、early rock |
| Canon | C-G-Am-Em-F-C-F-G | Pachelbel、經典 |

測試新伴奏風格時，建議各進行至少測一次，確保 LH bass 走音、RH voicing 切換都合理。

---

**文件結束。**

總字數：本檔設計為 NotebookLM 一份 source 使用，可直接餵 NotebookLM 生成 podcast、心智圖、FAQ；也可直接丟給 Claude Code / Cursor 等 coding agent 當實作 spec。
