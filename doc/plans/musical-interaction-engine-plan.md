# Musical Interaction Engine（MIE）— 技術架構與 MVP 規劃

日期：2026-09-06　狀態：規劃（Phase 0 尚未開始）　Beads：`LiveChord-3nex`（epic）

> 一句話：LiveChord 即時觀察使用者的 MIDI 演奏（音、和弦、節奏、力度、留白），
> 依演算法 + 機率 + 音樂約束，指揮 15 個 MIDI Channel 上的硬體樂器與 REAPER VST 彼此互動。
> 不是 Auto Accompaniment，不是 MIDI Router，是「受音樂約束的不可預測性」。

本文件先回答：架構、資料結構、演算法引擎、互動圖、回授防護、MIDI routing、UI/UX、MVP 分期。
**不含完整程式**；每個 Phase 結束都停下來給使用者驗收（本 repo 慣例，見 CLAUDE.md「Phase gates」）。

---

## 0. 現況盤點（決定架構的事實）

| 事實 | 影響 |
|---|---|
| repo 沒有任何即時 MIDI **輸出**/排程程式碼；只有 `editor.js` 用 Web MIDI 讀琴鍵偵測和弦、`backend/ai/*` 用 `mido` 做離線 `.mid` 處理 | MIE 是全新子系統，不用遷就既有 player 架構 |
| `mido 1.3.3` 已安裝，`python-rtmidi` **未安裝** | Phase 0 需 `pip install python-rtmidi`（Windows 有 wheel，不需 MSVC） |
| LiveChord 後端跑在 NUC（`192.168.50.6:8800`），mioXL 接在演奏用 PC | **引擎不能跑在 NUC**（LAN 往返 + uvicorn thread pool 不是 1 ms 等級的排程器）。引擎是 PC 上的獨立行程 |
| `chord_names` / 調性推估 / `scale-lab` 音階目錄都是純 Python 或純 JS 資料 | 和弦辨識、音階鎖定可直接 import，不必重寫 |
| Player 已有 chord JSON 時間軸（chord / key / bpm / beats / downbeats / sections） | 播歌時 MIE 可以拿到「絕對可靠」的和弦與拍點上下文，比從演奏推估準得多 |

### 0.1 引擎放哪裡：三個選項

| 選項 | 延遲 / 抖動 | 優點 | 缺點 | 結論 |
|---|---|---|---|---|
| A. 瀏覽器 Web MIDI（player 內） | 5–20 ms 抖動；分頁不在前景時 timer 被節流到 1 s | 零安裝、UI 同一頁 | 不可靠的排程、分頁關掉就沒了、無法 PANIC 保底 | 只當 Phase 0 的「探針」，不做正式引擎 |
| **B. PC 上獨立 Python 行程（mido + python-rtmidi）** | rtmidi callback thread 約 1 ms；自寫排程 thread 用 `perf_counter` | 完全掌控 I/O 與排程、可重用 backend 的 Python 音樂模組、行程獨立於瀏覽器 | 需要 `pip install python-rtmidi`、要自寫 WebSocket 給 UI | **採用** |
| C. REAPER JSFX / Lua | 音訊等級精準 | 最準 | 邏輯全在 REAPER 內，跟 LiveChord 脫節；Lua 沒有好用的機率/狀態機生態 | 留給 Phase 4（VST 端的 CC 自動化、Scene 觸發） |

選 B。行程名稱 `livechord-mie`，入口 `backend/mie/__main__.py`，用 `start_mie.bat` 啟動（PC 專用，不進 V:\ runtime deploy surface，理由同 `tools/` 研究腳本）。

### 0.2 職責劃分

```
┌──────────────────────── PC（演奏機）────────────────────────┐
│  livechord-mie (Python 行程)                                  │
│   ├ MIDI In  ← mioXL「MIE In」USB port  （使用者演奏 + UC4）    │
│   ├ MIDI Out → mioXL「MIE Out」USB port （CH1–15）             │
│   ├ 分析 / 狀態 / 演算法 / 機率 / 突變 / 約束 / 安全 / 排程        │
│   └ WebSocket :8810  ← 面板 UI、player 上下文同步                 │
│                                                              │
│  瀏覽器：/mie 面板（由 NUC 8800 或本機 8803 提供靜態頁）           │
│           連 ws://localhost:8810                              │
│  REAPER：CH1 VST；Phase 4 再加 OSC / ReaScript                  │
└──────────────────────────────────────────────────────────────┘
          │ USB
┌─────────▼──────────── iConnectivity mioXL ───────────────────┐
│  只做「實體 port ↔ 虛擬 port」與 channel filter                  │
│  規則 1：所有琴鍵 / UC4 的輸出 → MIE In（保留各自 channel）        │
│  規則 2：MIE Out → 各硬體 DIN / USB port（依 channel 過濾）        │
│  規則 3：MIE Out 絕對不得 route 回 MIE In（硬體層防迴圈）           │
│  規則 4：硬體樂器的 MIDI Thru / Soft Thru 全部關閉                 │
└──────────────────────────────────────────────────────────────┘
```

- **LiveChord（MIE 行程）負責**：一切音樂判斷、機率、狀態、安全、UI、Scene。
- **mioXL 負責**：實體派發、channel 過濾、硬體層迴圈阻斷。引擎只看到「一個 In、一個 Out、16 個 channel」，程式碼不知道任何 DIN/USB port 細節。
- **REAPER 負責**：CH1 音色與音訊處理；Phase 4 把 Scene / CC 自動化交給 ReaScript。

---

## 1. 時序與時鐘

MIE 需要三種時間：

| 時鐘 | 來源 | 用途 |
|---|---|---|
| 壁鐘 `t_wall` | `time.perf_counter()` | 所有排程、延遲、TTL |
| 音樂時鐘 `beat/bar` | 依優先序：① LiveChord player 播歌時的 chord JSON 拍點（WebSocket 推送 `playhead`）② mioXL 轉來的 MIDI Clock（Fantom 當 master）③ 演奏 IOI 叢集推估 ④ 手動 tap / 固定 BPM | Echo 的 1/4 拍、Silence 的「小節」記憶、和弦強拍鎖定 |
| 和聲時鐘 `chord/key` | ① player 時間軸 ② 由按住的音即時辨識（移植 `editor.js detectChordFromMIDI` → Python，或直接用 `backend/chord_names`）③ 12 音級直方圖 Krumhansl 調性推估 | 所有生成音的約束 |

**執行緒模型**（沒有 asyncio 在 MIDI 路徑上，避免 event loop 抖動）：

```
rtmidi callback thread ──► in_queue ──► Engine thread（單一，序列化所有狀態變更）
                                              │
                                              ├──► Scheduler thread（min-heap by t_due，1 ms 輪詢，送 MIDI Out）
                                              └──► UI thread（WebSocket，10 Hz 狀態快照 + 事件流）
```

Engine thread 是唯一能改 `MusicalState` 與 `InteractionGraph` 的執行緒；UI 的參數變更也丟進 `in_queue` 當控制訊息處理。這是為了不用鎖，也讓「回放測試」可以用同一條 pipeline 重播 log。

---

## 2. 資料結構

### 2.1 事件信封 `MieEvent`

所有進入 pipeline 的東西都是 `MieEvent`，不管來自琴鍵、生成、UC4 或 UI。

```python
@dataclass(slots=True)
class MieEvent:
    event_id: int            # 單調遞增，不用 uuid（每秒可能上千個）
    kind: Literal["note_on", "note_off", "cc", "pc", "control"]
    t_wall: float            # perf_counter 秒
    ch: int                  # 1–16，目前所在 channel
    note: int | None
    vel: int | None
    cc: int | None; val: int | None
    dur_hint: float | None   # note_on 時未知；note_off 到達後回填給分析器
    # ---- 血統 ----
    origin: Literal["HUMAN", "GENERATIVE", "CONTROL"]
    root_id: int             # 最初的人類事件 id（整條互動鏈共用）
    parent_id: int | None
    source_ch: int           # 最初的人類來源 channel
    hop: int                 # HUMAN=0；每經過一條 edge +1
    ttl_wall: float          # 超過此時間仍未送出 → 丟棄
    lane: str                # "human" | "echo" | "answer" | "pad" | ...（給人看 + 給約束用）
    # ---- 音樂上下文快照（產生時刻的 state 摘要，供 replay / debug）----
    ctx: ContextSnapshot | None
```

### 2.2 `MusicalState`（短期記憶）

```python
class MusicalState:
    # 即時
    chord: ChordInfo | None      # name, root_pc, quality, tones(pc set), since_t
    key: KeyInfo                 # tonic_pc, mode, confidence, source ("player"|"inferred"|"manual")
    bpm: float; beat_phase: float; bar_pos: int; beats_per_bar: int; clock_source: str
    held: dict[int, HeldNote]    # 使用者目前按住的音（note→vel,t_on,ch）
    register: Literal["low","mid","high"]  # 依最近 8 個音的加權中位數，門檻 <48 / 48–72 / >72
    direction: int               # -1/0/+1，最近 4 個音的斜率
    silence_s: float             # 距最後一個人類 note_on 的秒數
    # 能量（EMA，見 §6）
    density: float               # notes/sec，τ=1.5 s
    vel_mean: float; vel_var: float   # τ=2 s
    human_energy: float          # 0–1
    # 環形緩衝（依「拍」而非「秒」切，所以要有 bpm）
    recent_notes: RingBuffer[NoteRec]   # 最近 8 小節
    recent_intervals: RingBuffer[int]
    recent_ioi: RingBuffer[float]
    motif_index: MotifIndex      # 最近 8 小節內重複出現的 3–6 音片段（Phase 3）
    # 生成側
    active_gen: dict[(ch,note), GenNote]  # 目前發聲中的生成音（stuck-note 看門狗用）
    per_ch_voices: Counter       # 每 channel 生成 voice 數
```

`ContextSnapshot` 是上面前 6 行的凍結副本，跟著每個事件走。

### 2.3 樂器角色 `instruments.json`

```json
{
  "1":  {"name": "REAPER VST",        "role": "texture",  "group": "vst",     "enabled": true, "max_voices": 6,  "vel_scale": 0.9, "note_range": [36, 96], "sustain_ok": true},
  "2":  {"name": "Fantom Piano",      "role": "piano",    "group": "fantom",  "enabled": true, "max_voices": 8},
  "3":  {"name": "Fantom Pad",        "role": "pad",      "group": "fantom",  "enabled": true, "max_voices": 4, "sustain_ok": true},
  "4":  {"name": "Fantom Strings",    "role": "strings",  "group": "fantom"},
  "5":  {"name": "Fantom Synth",      "role": "synth",    "group": "fantom"},
  "6":  {"name": "Fantom Bass",       "role": "bass",     "group": "fantom",  "note_range": [28, 55], "max_voices": 1},
  "7":  {"name": "Fantom Lead",       "role": "lead",     "group": "fantom",  "max_voices": 1},
  "8":  {"name": "Fantom FX",         "role": "fx",       "group": "fantom"},
  "9":  {"name": "Nord Grand 2",      "role": "piano",    "group": "hw"},
  "10": {"name": "Wavestate mk II",   "role": "sequence", "group": "hw",      "sustain_ok": true},
  "11": {"name": "Iridium",           "role": "exp_synth","group": "hw"},
  "12": {"name": "MODX M6",           "role": "synth",    "group": "hw"},
  "13": {"name": "Event 61",          "role": "keyboard", "group": "hw"},
  "14": {"name": "PSR-SX900",         "role": "arranger", "group": "hw",      "enabled": false},
  "15": {"name": "microArranger",     "role": "arranger", "group": "hw",      "enabled": false}
}
```

`group: "fantom"` 讓 CH2–8 共享一個「Fantom 總 voice 預算」與同一個 PANIC 時序；`role` 是演算法選目標時的語意（Register 演算法找 `role=bass`，Silence 找 `pad`/`texture`），不寫死 channel。`note_range` 是最後一道 clamp。

### 2.4 互動圖 `InteractionGraph`

有向圖，節點 = channel（0 = HUMAN 虛擬節點），邊 = 一條互動規則：

```python
@dataclass
class Edge:
    src: int; dst: int                    # 0=human, 1–15
    algo: str                             # "follow"|"echo"|"answer"|"mirror"|"shadow"|"density"|"velocity"|"register"|"silence"
    prob: float                           # 0–1，會再乘 restraint（§6）
    delay_beats: float = 0.0; delay_ms: float = 0.0
    transpose: int = 0; octave: int = 0
    vel_scale: float = 1.0; vel_offset: int = 0
    dur_scale: float = 1.0
    mutations: list[Mutation] = []        # §5
    constraint: Literal["chord","scale","free"] = "scale"
    max_hop: int = 2                      # 此邊接受的最大入邊 hop（generative 進來的門檻）
    accepts: set[str] = {"HUMAN","GENERATIVE"}
    cooldown_ms: float = 0                # 觸發後冷卻
    enabled: bool = True
```

**機率矩陣**就是 `edges` 依 `(src,dst)` 的檢視；UI 的 15×15 格子每格可以有多條邊（不同 algo）。

### 2.5 Scene

```json
{
  "id": "01", "name": "Piano Ambient", "mode": "AMBIENT",
  "global": {"prob_scale": 0.6, "chaos": 0.1, "restraint": 0.8, "max_hop": 2, "max_gen_notes_per_s": 12},
  "edges": [
    {"src": 0, "dst": 11, "algo": "shadow", "prob": 0.7, "shadow": "top",  "constraint": "chord", "delay_ms": 30},
    {"src": 0, "dst": 3,  "algo": "density","prob": 1.0, "rule": "low→on, high→off"},
    {"src": 0, "dst": 10, "algo": "silence","prob": 0.2, "after_s": 2.0, "lane": "pad"},
    {"src": 11,"dst": 10, "algo": "echo",   "prob": 0.4, "delay_beats": 0.5, "transpose": 7, "max_hop": 1}
  ],
  "midi_select": {"pc": 1, "cc": {"num": 20, "val": 1}}
}
```

Scene 存在 `data/mie/scenes/*.json`，切換是「先 fade 舊 scene 的持續音（pad lane 送 note_off 排程 + CC7 漸降），再載新邊」，不硬切。

---

## 3. 處理管線（每個事件走一次）

```
MieEvent 進 Engine thread
  1. 自我回音過濾   ─ 若 (ch,note,kind) 在「最近 8 ms 送出」的集合裡 → 丟（硬體 Thru 沒關乾淨時的保底）
  2. 分析器更新     ─ HUMAN 事件更新 MusicalState；GENERATIVE 只更新 active_gen / per_ch_voices（不汙染人類統計）
  3. 找出邊         ─ graph.out_edges(src=事件所在 ch 或 0)；過濾 enabled / accepts / hop ≤ max_hop / cooldown
  4. 抑制門         ─ p_eff = edge.prob × scene.prob_scale × restraint(human_energy)（§6）；rng < p_eff 才繼續
  5. 演算法         ─ algo(event, state, edge, rng) → list[Proposal]（純函式，可單元測試）
  6. 突變           ─ 對 Proposal 套 edge.mutations（§5），chaos 值決定額外隨機突變的量
  7. 音樂約束       ─ constraint="chord" → 貼齊和弦音；"scale" → 貼齊調內音；再 clamp 到 instrument.note_range
  8. 安全           ─ §7：voice 預算、rate limit、與人類 held 音撞音、hop/TTL、dur 上限
  9. 排程           ─ 轉成 (note_on, note_off) 配對推進 Scheduler；note_off 永遠與 note_on 一起排，永不分離
 10. 回饋           ─ 生成事件在「實際送出」那一刻，以 origin=GENERATIVE、hop+1 重新進入步驟 1（這就是 Cross-Interaction）
```

步驟 5–7 是純函式；步驟 8 是唯一能否決的地方；步驟 10 是唯一會製造迴圈的地方，所以 §7 全部針對它。

---

## 4. 演算法引擎

介面統一：`def run(ev: MieEvent, st: MusicalState, edge: Edge, rng: Random) -> list[Proposal]`，`Proposal = (t_offset, ch, note, vel, dur, lane)`。時間類演算法（Silence、Density）另外實作 `tick(st, edge, rng, now)`，由 Engine 每 50 ms 呼叫一次。

| # | 演算法 | 觸發 | 核心規則（MVP 版） | 約束預設 | 備註 |
|---|---|---|---|---|---|
| 1 | Follow | HUMAN note_on | 目標音 = 人類音 + `interval`（預設 +7），同 vel×scale，dur = 人類上一音的 dur（首音用 1 拍） | chord | 例：C5 → Iridium G5 |
| 2 | Echo | note_on | 原音延遲 `delay_beats`/`delay_ms`，vel 衰減 `vel_scale^k`，可設 `repeats`（每次 hop+1） | free（保留原音） | repeats>1 時每次都得再過機率門 |
| 3 | Answer | 片語結束（note_on 後 ≥ 0.6 拍無新音，或 3–8 音累積） | 取片語 → 逆行 / 倒影 / 節奏保留＋音高改為和弦音走向 tonic；延遲到「下個強拍」 | chord 強拍 / scale 弱拍 | 例：C D E → G F E。Phase 2 |
| 4 | Mirror | note_on | reverse（片語級）/ inversion（以 chord root 為軸）/ octave displacement | scale | Phase 2 |
| 5 | Shadow | note_on / note_off | 只轉送 `top` / `bottom` / `root`（root 從 st.chord 拿）；人類放開 → 影子也放開（跟隨 note_off，不是固定 dur） | free | 給 MODX Bass / Lead 用；`max_voices=1` |
| 6 | Density | tick | `density` 落在 `[lo,hi)` 區間 → 對應 lane ON/OFF（含 hysteresis 0.3 notes/s，避免抖動） | — | ON = 送 pad 音（和弦音、長音）；OFF = 排程 note_off |
| 7 | Velocity | note_on | `vel_mean` → CC（Expression 11 / Mod 1 / Filter 74 / Vol 7），slew-rate 限制每 20 ms 最多變 4 | — | 這是 CC 路徑，走 §7 的 CC 限流 |
| 8 | Register | note_on | 依 `st.register` 選 role（low→bass, mid→piano/strings, high→lead/texture）再套 Follow/Shadow | chord | 目標由 role 解析，不寫死 ch |
| 9 | Silence | tick | `silence_s` 越過 `after_s` 門檻一次觸發（edge-triggered，不重複）；人類再彈 → 該 lane 依 `release_beats` 淡出 | chord | 分層：1 s pad / 2 s Wavestate / 4 s texture 就是三條邊 |
| 10 | Probability | 所有 | 不是獨立演算法，是步驟 4；多目標互斥用「加權輪盤」邊群組（`group_id` 相同的邊只擲一次、依權重選一條） | — | 例：70/20/10 → 三條邊同 group |

**和弦音鎖定實作**：`snap(note, allowed_pcs, prefer="nearest"|"up"|"down")`，距離相同時偏向「遠離人類 held 音」的方向（避免同度撞音）。`allowed_pcs` 來源：chord → `st.chord.tones`；scale → `scale-lab` 的 intervals 表（移植 31 種到 `backend/mie/scales.py`）依 `st.key`。

---

## 5. 突變 `Mutation`

```python
Mutation = (
  {"type": "octave",   "choices": [-1, 0, +1], "weights": [1, 2, 1]}
| {"type": "interval", "choices": [3, 5, 7]}
| {"type": "chordify", "shape": "octave"|"fifth"|"triad"|"seventh", "spread_ms": 12}   # 單音→多音；只允許 hop==0 的來源（§7-4）
| {"type": "rhythm",   "pattern": "x---x-x-", "grid_beats": 0.25}                   # 一音→多次觸發，每次 vel×0.85
| {"type": "dur",      "scale": [0.5, 1, 2]}
| {"type": "vel",      "jitter": 12}
)
```

`chaos ∈ [0,1]` 的效果：每個 Proposal 額外以機率 `chaos×0.5` 隨機套一個突變、以 `chaos×0.3` 換到同 role 的另一台樂器、以 `chaos×0.2` 加 0–1 拍隨機延遲。但 **約束步驟永遠在突變之後**，所以 chaos 再高也在調內/和弦內。

---

## 6. Human Musical Priority — 動態抑制

```
density_n = clamp(density / 8 notes/s, 0, 1)
vel_n     = clamp((vel_mean - 40) / 80, 0, 1)
human_energy = EMA(0.6·density_n + 0.4·vel_n, τ_attack=0.3 s, τ_release=2.5 s)   # 快起慢落，像 compressor

restraint(e) = (1 - e) ** scene.restraint_curve      # curve 1 = 線性；2 = 更保守；0.5 = 更積極
p_eff = edge.prob × scene.prob_scale × restraint(human_energy)
```

除了機率，還有兩個硬規則：
- **撞音迴避**：Proposal 的音若等於任何 `st.held`（同音級、±1 八度）→ 改 snap 到下一個和弦音；找不到就丟。
- **voice 預算**：`human_energy > 0.7` 時每 channel 生成 voice 上限降為 1，Fantom 群組總上限降為 3。

效果：使用者爆發演奏時生成率趨近 0；放手後 2–3 秒內生成事件像潮水湧入；Silence 邊接手。

---

## 7. MIDI 安全（每一層都獨立生效，任一層失效其他層仍擋得住）

| 層 | 機制 | 參數 |
|---|---|---|
| 0 | mioXL 硬體 routing：MIE Out 永不回到 MIE In；樂器 Thru 全關 | 設定時人工驗證（Phase 0 測試 T0） |
| 1 | 自我回音過濾：最近送出的 `(ch,note,kind,t)` 環形集合 | 8 ms 窗 |
| 2 | `hop ≤ scene.max_hop`（預設 2，CHAOS 最多 3）；`ttl_wall` 過期即丟 | TTL = 排程時刻 + 4 拍 |
| 3 | 同一 `root_id` 的鏈總事件數上限 | 24 |
| 4 | GENERATIVE 來源禁止 fan-out > 1 的突變（chordify / rhythm）與 Answer；只允許單音對單音 | 硬編碼，不可由 scene 覆寫 |
| 5 | Token bucket 限流：每 ch 音符 20/s（burst 8）、每 ch CC 30/s、全域生成 `max_gen_notes_per_s`（scene 設定，預設 12） | 超限 → 丟棄並在 UI 顯示「限流」計數 |
| 6 | Voice 預算：`instrument.max_voices`、Fantom 群組總量、`human_energy` 縮減 | 滿了 → 先偷最舊的生成音（送其 note_off）再發新音，永不超發 |
| 7 | Stuck-note 看門狗：`active_gen` 中任何音超過 `max_dur`（預設 8 s；`sustain_ok` lane 30 s）→ 強制 note_off | 每 250 ms 掃一次 |
| 8 | note_off 配對保證：Scheduler 只接受 `(on, off)` 對；行程結束 / 例外 / KeyboardInterrupt 一律先跑 PANIC | `try/finally` |
| 9 | CC 護欄：每個 CC 有 `[min,max]` 與 slew 限制；Volume(7) 絕不由演算法降到 0 以下 `floor` | 預設 floor 40 |
| 10 | **PANIC**：對 CH1–16 送 CC120 + CC123 + 對 `active_gen` 每音明確 note_off + CC64=0，然後 engine 進入 `BYPASS` 直到人工解除 | 觸發：UI 按鈕、鍵盤 `Esc×2`、UC4 指定按鈕、WebSocket 斷線 5 s |

**運作模式**（§ 使用者需求 OFF/BYPASS/SAFE/INTERACTIVE/CHAOS 對應）：

| 模式 | 意義 |
|---|---|
| OFF | 行程不開 MIDI Out |
| BYPASS | 開 port，只分析、只顯示 state，不送任何生成事件（含 PANIC 後的狀態） |
| SAFE | `prob_scale ≤ 0.3`、`max_hop=1`、chaos=0、只允許 Shadow / Echo / Silence(pad) |
| AMBIENT | pad/texture lane 優先，dur 拉長 ×2，vel 上限 70 |
| INTERACTIVE | 完整邊集合，`max_hop=2` |
| GENERATIVE | + 片語記憶（motif_index）、Answer/Mirror、隨機突變 |
| CHAOS | `max_hop=3`、chaos 由 UC4 encoder 控制、允許 role 內隨機換樂器；§7 全部仍生效 |

---

## 8. 控制面：UC4 與 MIDI 呼叫

- UC4 走 mioXL 進 MIE In，用獨立 channel（建議 CH16，目前沒用）；引擎依 `(ch, cc/pc)` 判定為 `CONTROL` 事件，不進音樂分析。
- `data/mie/control_map.json`：`{"cc:16:20": "scene.select", "cc:16:21": "global.prob_scale", "cc:16:22": "global.chaos", "cc:16:30": "inst.11.enabled", "cc:16:127": "panic"}`。
- UI 有「MIDI Learn」：點參數 → 轉 UC4 → 綁定。
- Scene 也接受 Program Change（`midi_select.pc`）。

---

## 9. UI / UX

新頁 `frontend/mie.html` + `frontend/js/mie.js` + `frontend/css/mie.css`，由現有靜態 mount 提供，連 `ws://localhost:8810`。遵守 `doc/UX_CONVENTION.md`（主題 token、Type C 浮動面板慣例、toast 不吃 pointer）。

版面（桌面單頁，演奏時看一眼就懂；不需要滑鼠操作是 UC4 的事）：

```
┌ 頂列 ─ MODE [INTERACTIVE] SCENE [02 Jazz Interaction] KEY C  CHORD Cmaj7  92 BPM ●clock:player  [PANIC]
├ 左 1/3 ─ 能量儀表：DENSITY 42%  ENERGY 68%  RESTRAINT 0.32  SILENCE 0.0 s
│           ACTIVE: CH2 Fantom ● CH9 Nord ● CH11 Iridium ◐
├ 中 1/3 ─ 互動流：節點圓圈（HUMAN + 15 ch），最近 2 s 有事件的邊會亮起並帶「機率 / 實際觸發次數」
│           Nord → Iridium 63%  ▮▮▮
│           Iridium → Wavestate 41%  ▮
├ 右 1/3 ─ 邊編輯：點一條邊或矩陣格 → prob / delay / transpose / vel / dur / mutations / constraint
│           矩陣檢視切換（15×15，格內顯示最高機率 + 邊數）
└ 底列 ─ 事件流（最近 30 筆，顏色分 HUMAN / GEN hop1 / GEN hop2 / 丟棄原因）
```

WebSocket 訊息：`state`（10 Hz 快照）、`event`（每筆生成/丟棄，含 `drop_reason`）、`edge_fire`（邊觸發，UI 亮線）、`set`（UI → 引擎參數）、`scene`（載入/儲存）。

Player 端只加一個小徽章 `#mieBadge`（連線中 / 模式），點了開 `/mie`；player 播歌時透過同一個 WebSocket 推 `playhead {t, chord, key, bpm, beat, bar, section}` 給引擎當音樂時鐘。

---

## 10. 音樂上下文來源優先序

| 情境 | chord/key | beat/bar |
|---|---|---|
| Player 正在播 chord JSON | 時間軸（最可靠） | 時間軸 `beats[]/downbeats[]` |
| 自由演奏、Fantom 送 MIDI Clock | 由 held 音辨識，2 音以下用 Krumhansl 推估 key，chord=None → 退到 scale 約束 | MIDI Clock |
| 自由演奏、無 clock | 同上 | IOI 叢集（最近 16 個 IOI，取 60–180 BPM 內最密的峰）；信心低於門檻 → 用 scene 固定 BPM |

Test 6（Cmaj7→Am7→Fmaj7→G7 生成音必須跟著和弦）在自由演奏下依賴「按住的音」辨識，延遲 ≈ 一個 rtmidi callback（<5 ms）；chord 變更那一瞬間已排程但未送出的 Proposal 會在 Scheduler 送出前 **再 snap 一次**（late-binding constraint），避免 Echo 延遲 1/4 拍後送出上個和弦的音。

---

## 11. 分期與驗收（每期結束停下來給使用者驗收）

### Phase 0 — 探針（半天）
- `pip install python-rtmidi`；`backend/mie/probe.py` 列出 port、選 mioXL In/Out、把收到的 note 原樣延遲 250 ms 轉到 CH11。
- 量測：callback→送出的延遲分布、抖動；驗證 mioXL 規則 3（MIE Out 沒有回到 MIE In）；驗證各樂器 Thru 已關（開 BYPASS 看有沒有自我回音）。
- **驗收**：T0 回音測試 0 筆重複；延遲 p95 < 5 ms。
- 產出需要使用者回答的事實：使用者自己演奏的 MIDI 到底在哪個 channel（Fantom CH2？Nord CH9？兩台都會彈？）、mioXL 給 PC 的 port 名稱、UC4 的 port/channel、有無 MIDI Clock。

### Phase 1 — MVP 核心（使用者 §16）
- `backend/mie/`：`events.py`、`state.py`、`graph.py`、`algos/{follow,echo,shadow,silence}.py`、`probability.py`、`constraint.py`、`safety.py`、`scheduler.py`、`io_rtmidi.py`、`ws_server.py`、`__main__.py`。
- 設定檔：`instruments.json`、一個 scene、`control_map.json`（先只綁 PANIC + ON/OFF + prob_scale）。
- UI：`mie.html` 頂列 + 能量儀表 + 事件流（互動流與矩陣編輯留 Phase 2；Phase 1 用 JSON 編輯 scene）。
- 測試（pytest，`backend/tests/test_mie_*.py`，用 FakeClock + FakeMidiOut，不需硬體）：
  - T1 C5 → 某次執行 CH11 得到 G5（固定 rng seed）；
  - T3 密集演奏 → 生成事件數下降 ≥ 80%；
  - T4 靜默 2 s → pad lane 收到和弦音 note_on；再彈 → 收到 note_off；
  - T6 和弦切換 → 已排程 Echo 在送出前重新 snap；
  - 安全：人工構造 A→B→A 邊集合，斷言鏈在 `max_hop` 停止且總事件 ≤ 24；stuck-note 看門狗；PANIC 後 `active_gen` 為空。
- **驗收**：使用者在 SAFE 與 INTERACTIVE 各彈 10 分鐘，無卡音、無迴圈、PANIC 一鍵有效。

### Phase 2 — 互動與控制
- Answer、Mirror、Density、Velocity(CC)、Register；輪盤邊群組；Scene 切換淡出；UC4 MIDI Learn；矩陣 UI + 互動流動畫；player `playhead` 同步。
- 測試：T2（C D E → 另一 ch 有 3 音回答，落在強拍）、T5（持續 Cmaj7 → 60 s 內活躍樂器數單調遞增）。

### Phase 3 — Generative / Chaos
- `motif_index` 片語記憶與再現、chaos 突變、role 內隨機換樂器、GENERATIVE 事件的多 hop 鏈（`max_hop=3`）。
- 事件 log 回放工具（`tools/mie_replay.py`）：把一段演奏的 HUMAN 事件錄下來，離線用不同 scene / seed 重跑，比對生成統計 — 這是調機率矩陣不用一直彈琴的方法。

### Phase 4 — REAPER
- CH1 之外：OSC 或 ReaScript 接收 scene 切換、把 `human_energy` 對應到 REAPER track automation；SWAM/VST 的 expression 曲線由引擎 CC 驅動。

---

## 12. 明確不做 / 風險

- **不在 NUC 跑引擎**、不經 LAN 送即時 MIDI。
- **不用 Web MIDI 當正式輸出**（分頁節流、無 PANIC 保底）。
- Arranger（CH14/15）預設 `enabled:false`：arranger 收到 note 會啟動自動伴奏，跟「人類優先」衝突；Phase 2 再決定要不要只送 chord-recognition 區的音。
- 風險：硬體 Local Control / Thru 沒關 → 第一層防線靠 mioXL 與 §7-1；Phase 0 必須人工驗證。
- 風險：Fantom 七個 zone 的 MIDI Rx channel 需在 Fantom 端設好，否則 CH2–8 全打到同一 zone。
- Python GIL：引擎是純事件驅動、每事件工作量微秒級，不會像 chord2vec 那樣長迴圈；但 **任何** 需要 numpy/ML 的未來功能（例如 motif 相似度用 DTW）必須丟到子行程，同 CLAUDE.md 既有規則。

---

## 13. 需要使用者確認的問題（Phase 0 前）

1. 使用者本人演奏時，MIDI 從哪台琴、哪個 channel 進來？（決定 `src=0` 的判定規則：目前假設「CH2 與 CH9 的輸入 = HUMAN」，其他 channel 的輸入視為樂器回傳並丟棄）
2. mioXL 對 PC 露出的 USB port 名稱與數量（要獨立一對給 MIE，還是共用現有的 DAW port？）
3. UC4 走哪個 port/channel？可否指定 CH16？
4. 有沒有 MIDI Clock master（Fantom？REAPER？），還是以 LiveChord player 播歌為主要情境？
5. PANIC 要不要同時送到 REAPER（CH1 的 VST 通常吃 CC123，但某些 SWAM 不吃）？
