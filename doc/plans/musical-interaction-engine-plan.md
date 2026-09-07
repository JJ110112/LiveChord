# Musical Interaction Engine（MIE）— 技術架構與 MVP 規劃

日期：2026-09-06（Phase 0 驗證 2026-09-07，Phase 1 完成 2026-09-07）　狀態：**Phase 0、Phase 1 皆通過**（演奏驗收 + 使用者程式碼審核 APPROVED，51 個無硬體測試），Phase 2 待使用者核准開工　Beads：`LiveChord-3nex`（epic）

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
│   ├ MIDI In  ← mioXL HST Port 2（Fantom port 1 的副本）           │
│   ├ MIDI In  ← Faderfox UC4（直接 USB 接 PC，獨立 port）           │
│   ├ MIDI Out → mioXL HST Port 3（CH2–15 硬體，與 Fantom 直通並行）  │
│   ├ MIDI Out → loopMIDI「LiveChord_MIE_to_REAPER」（CH1 VST）      │
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
│  現況保留：Fantom port 1 In → DIN2–8（CH9–15 各琴自濾）不動        │
│  規則 1：Fantom port 1 In 另外「複製」一份 → HST Port 2（保留 ch）  │
│  規則 2：HST Port 3 → merge 到 DIN1（Fantom INT zone）+ DIN2–8     │
│  規則 3：HST Port 3 絕對不得 route 回 HST Port 2（硬體層防迴圈）    │
│  規則 4：DIN2–8 各琴的 Out 一律不 route 到 HST Port 2；Fantom Thru 關│
│  規則 5：HST Port 1 保留給 REAPER，MIE 不碰（避免 WinMM 獨占衝突）   │
└──────────────────────────────────────────────────────────────┘
```

**引擎是「並聯」不是「串聯」**：Fantom → DIN2–8 的直通路徑保持原樣，人類演奏零額外延遲；引擎只拿一份副本來分析，再從獨立的 MIE Out 把生成事件「合流」到各 DIN（mioXL 負責 merge）。引擎當掉或關掉，整套琴照常運作。

**HUMAN 的判定靠 port，不靠 channel**：Fantom 是唯一主控鍵盤，INT zone 在 Fantom 內發聲、EXT zone 8–16 直接以對應 channel 送出。所以人類演奏可能出現在 CH2–15 任一個 channel，由「來自 HST Port 2」判定為 HUMAN，事件的 `ch` 記為 `human_ch`（使用者此刻透過哪台樂器在彈）。UC4 走自己的 USB port，來自該 port 的一切都是 `CONTROL`，channel 不再重要（CH16 仍保留不用，作為未來 mioXL 內控制訊號的備援）。

- **LiveChord（MIE 行程）負責**：一切音樂判斷、機率、狀態、安全、UI、Scene。
- **mioXL 負責**：實體派發、channel 過濾、硬體層迴圈阻斷。引擎只看到「一個 In、一個 Out、16 個 channel」，程式碼不知道任何 DIN/USB port 細節。
- **REAPER 負責**：CH1 音色與音訊處理；Phase 4 把 Scene / CC 自動化交給 ReaScript。

---

## 1. 時序與時鐘

MIE 需要三種時間：

| 時鐘 | 來源 | 用途 |
|---|---|---|
| 壁鐘 `t_wall` | `time.perf_counter()` | 所有排程、延遲、TTL |
| 音樂時鐘 `beat/bar` | 依優先序：① LiveChord player 播歌時的 chord JSON 拍點（WebSocket 推送 `playhead`）② mioXL 轉來的 MIDI Clock（Fantom 當 master）③ 演奏 IOI 叢集推估 ④ Scene 預設 BPM，UI / UC4 encoder 可即時改 | Echo 的 1/4 拍、Silence 的「小節」記憶、和弦強拍鎖定 |
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
    human_chs: set[int]          # 最近 2 s 內有人類 note_on 的 channel（Fantom 當前作用中的 EXT zone）
    register: Literal["low","mid","high"]  # 依最近 8 個音的加權中位數，門檻 <48 / 48–72 / >72
    direction: int               # -1/0/+1，最近 4 個音的斜率
    silence_s: float             # 距最後一個人類「聲音結束」的秒數；held 或 sustained 非空時為 0
    sustained: dict[int, HeldNote]  # 已放開但延音踏板還撐著的音
    sustain: dict[int, bool]        # 每個 human channel 的 CC64 狀態
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
  3. 找出邊         ─ HUMAN 事件走 src=0 的邊（不論它在哪個 ch）；GENERATIVE 走 src=事件所在 ch；過濾 enabled / accepts / hop ≤ max_hop / cooldown
  4. 抑制門         ─ p_eff = edge.prob × scene.prob_scale × restraint(human_energy)（§6）；rng < p_eff 才繼續
  5. 演算法         ─ algo(event, state, edge, rng) → list[Proposal]（純函式，可單元測試）
  6. 突變           ─ 對 Proposal 套 edge.mutations（§5），chaos 值決定額外隨機突變的量
  7. 音樂約束       ─ constraint="chord" → 貼齊和弦音；"scale" → 貼齊調內音；再 clamp 到 instrument.note_range
  8. 安全           ─ §7：voice 預算、rate limit、與人類 held 音撞音、hop/TTL、dur 上限；**目標 ch ∈ human_chs 時丟棄**（那台琴此刻是人在彈，生成音會跟人類音在同一音色上疊）
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
| 11 | **Sustain**（2026-09-07 新增） | tick | Silence 的鏡像：**人類的聲音還在響**（手指按著或延音踏板撐著）時，每 `every_bars_min`–`every_bars_max` 小節加一個和弦音，落在下一拍；超過 `voices` 就放掉最舊的；人類聲音停了則 `release_beats` 後淡出 | chord | 使用者需求：「只要手指沒離開鍵盤、pad 還在發聲，AI 就該繼續回應」。機率門只決定濃淡，被拒絕的窗口在 `retry_beats` 後重試，節奏由間隔決定 |

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
| 5 | 限流：每 ch 音符 **任一秒的送出時間軸視窗內最多 20 個**（`SendWindowLimiter`，與到達順序無關；一個和弦手勢必須整組放行）；CC 仍用 token bucket（只走牆鐘）、每 ch CC 30/s、全域生成 `max_gen_notes_per_s`（scene 設定，預設 12） | 超限 → 丟棄並在 UI 顯示「限流」計數 |
| 6 | Voice 預算：`instrument.max_voices`、Fantom 群組總量、`human_energy` 縮減 | 滿了 → 先偷最舊的生成音（送其 note_off）再發新音，永不超發 |
| 7 | Stuck-note 看門狗：`active_gen` 中任何音超過 `max_dur`（預設 8 s；`sustain_ok` lane 30 s）→ 強制 note_off | 每 250 ms 掃一次 |
| 8 | note_off 配對保證：Scheduler 只接受 `(on, off)` 對；行程結束 / 例外 / KeyboardInterrupt 一律先跑 PANIC | `try/finally` |
| 9 | CC 護欄：每個 CC 有 `[min,max]` 與 slew 限制；Volume(7) 絕不由演算法降到 0 以下 `floor` | 預設 floor 40 |
| 10 | **PANIC**：對兩個 Out port（HST Port 3 + loopMIDI）各自的 CH1–16 送 CC120 + CC123 + CC64=0，再對 `active_gen` 每音補送明確 note_off（vel 0；SWAM 等不吃 CC123 的 VST 靠這個），然後 engine 進入 `BYPASS` 直到人工解除 | 觸發：UI 按鈕、鍵盤 `Esc×2`、UC4 指定按鈕、WebSocket 斷線 5 s |

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

- UC4 直接 USB 接 PC（現況已如此）；引擎啟動時另開一個 In port，依裝置名稱含 `UC4` 自動選取。來自該 port 的一切事件都是 `CONTROL`，不進音樂分析、不經 mioXL。
- `data/mie/control_map.json`：`{"uc4:cc:20": "scene.select", "uc4:cc:21": "global.prob_scale", "uc4:cc:22": "global.chaos", "uc4:cc:23": "global.bpm", "uc4:cc:30": "inst.11.enabled", "uc4:cc:127": "panic"}`（key 前綴是 port 別名，不是 channel）。
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

### 9.1 AI 介入的「方法」與「程度」要能在 UI 調（使用者需求，2026-09-07）

使用者要求：面板上要能直接決定 **AI 用什麼方式介入、介入到什麼程度**，不必改 JSON。分兩層：

| 層級 | 「方法」 | 「程度」 |
|---|---|---|
| 全域 | 模式（OFF/BYPASS/SAFE/AMBIENT/INTERACTIVE/GENERATIVE/CHAOS）、Scene | `prob_scale`、`chaos`、`restraint` 與 `restraint_curve`、`max_gen_notes_per_s` |
| 每條邊 | 演算法（follow/echo/shadow/silence/sustain…）、來源 → 目標樂器、`constraint`（chord/scale/free）、`collision`、突變 | `prob`、延遲（拍或 ms）、`repeats`、`every_bars_*`、`voices`、`hold_*`、力度縮放、`transpose`/`octave` |
| 每台樂器 | 角色 `role`、啟用 | `max_voices`、音域、力度縮放 |

**Phase 1 已經有的**：模式、Scene 切換、`prob_scale`/`chaos`/`restraint` 三個滑桿、每條邊的啟用與 `prob`、每台樂器的啟用。
**Phase 2 要補的**：邊的新增／刪除與演算法切換、上表其餘參數的即時編輯、15×15 矩陣檢視、Scene 從 UI 存檔、以及幾個「介入風格」預設（例如伴奏型 / 對話型 / 氛圍型），讓使用者一鍵換掉整組邊而不必逐條調。

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
- 前置（使用者手動）：Auracle 內把 Fantom port 1 In 複製到 HST Port 2、HST Port 3 merge 到 DIN1–8；安裝 loopMIDI 並建立 `LiveChord_MIE_to_REAPER`，REAPER 新增該 port 為 input。
- `pip install python-rtmidi`；`backend/mie/probe.py` 列出所有 port、依名稱選 `HST Port 2`（In）/ `HST Port 3`（Out）/ `UC4`（In）/ loopMIDI（Out），把收到的 note 原樣延遲 250 ms 轉到 CH11，UC4 任一按鈕觸發 PANIC。
- 量測：callback→送出的延遲分布、抖動；驗證 mioXL 規則 4（MIE Out 沒有回到 MIE In）；驗證 Fantom Thru 已關、DIN2–8 沒有回流（開 BYPASS 看有沒有自我回音）；驗證引擎關掉時 Fantom → DIN2–8 直通完全不受影響。
- **驗收**：T0 回音測試 0 筆重複；延遲 p95 < 5 ms。
- BYPASS 下逐台彈 Nord / Wavestate / Iridium / MODX / Event 61，引擎 log 必須完全沒有來自 DIN2–8 的事件（只看得到 Fantom 副本）。
- 確認 Windows 上實際的 port 名稱（mioXL 韌體版本不同，可能叫 `HST 2` 或 `mioXL Port 2`），寫進 `data/mie/ports.json`。

#### Phase 0 結果（2026-09-07，ProArt 16，使用者在琴前逐項驗證）

**環境**：Python 3.12.10（`%LOCALAPPDATA%\Programs\Python\Python312`，winget 安裝）、`python-rtmidi`、`mido`、`pytest`；`test_mie_probe.py` 15 passed。`probe_mie.bat` 已改為自行尋找 CPython（py launcher → 使用者安裝 → PATH），避免 Microsoft Store 的 `python.exe` 別名把探針擋掉。

**實際 port 名稱**（Windows / rtmidi，數字是裝置索引，`ports.json` 的子字串已能匹配）：

| 角色 | Windows port | Auracle 對應 |
|---|---|---|
| MIE In | `HST 2 14` | USB DAW 欄 `HST 2` |
| MIE Out | `HST 3 16` | USB DAW 欄 `HST 3` |
| UC4 | `Faderfox UC4 1` | 直接 USB 接 ProArt |
| REAPER | `LiveChord_MIE_to_REAPER 23` | loopMIDI（2026-09-07 新裝、新建） |

Auracle 名詞對照：**DIN MIDI** = 實體 DIN 孔（`Fantom 8` = DIN 1）、**USB Host** = mioXL 自己的 USB 主機孔、**USB DAW** = 接 ProArt 的那條 USB 線，Windows 看到的 `DIN 1–8 / HST 1–7 / Control` 就是 USB DAW 欄的名字。

**Auracle 路由（本次新增，直通路徑未動）**：
- Input `Fantom 8`（DIN 1 In）→ DIN 2–7（原有直通）**+ USB DAW `HST 2`**（新增，Fantom 副本給引擎）。
- Input USB DAW `HST 3`（引擎輸出）→ DIN MIDI `Fantom 8` + `DIN 2`–`DIN 8`；USB DAW 欄全不亮（規則 4）。USB Host 欄的 `HST 3` 已取消。
- **Output DIN MIDI `Fantom 8` 的 Filter：CH 9–16 全部擋掉（All Filters），CH 1–8 放行**（2026-09-07 Phase 1 試奏時發現：引擎送到 CH10 的回音會同時進 Fantom 的 DIN IN，Fantom scene 的 part 10 是鼓組，聽起來像隨機 hi-hat / tom；Fantom 主音量歸零即消失。濾波器要放在 Fantom 8 這個實體輸出上，放在 HST 3 的 Input 總濾波器會連 DIN 2–8 一起擋掉，放在 USB DAW `DIN 1` 則濾錯 port）。這就是規則 2「HST 3 只把 CH2–8 merge 給 Fantom」的實作。
- 原本 Fantom 8 只送 DIN 2–7、未送 DIN 8；規格寫 DIN2–8，以使用者現況為準。

**T0 量測**：

| 項目 | 結果 | 門檻 |
|---|---|---|
| 規則 4 軟體檢查（HST 3 送 CH11 短音，聽 HST 2 1.5 s） | 0 回流 | 0 |
| BYPASS 逐台彈 Nord / Wavestate / Iridium / MODX / Event 61（120 s，不碰 Fantom） | HST 2 與 DIN 2–8 皆 0 事件 | 0 |
| BYPASS 彈 Fantom（多輪，累計 >2000 事件） | `loops=0`、`T0 OK` | 0 |
| 250 ms 回音（120 s，in=130 / out=113） | jitter p50 0.00 / p95 **0.55** / max 0.81 ms | p95 < 5 ms |
| `--delay 0` 直通（callback → send） | p50 0.11–0.15 / p95 0.16–0.21 ms | 記錄 |
| 純軟體排程精度（`Condition.wait` + spin，250 ms × 60） | p95 0.26 / max 0.93 ms | 記錄 |
| UC4 任一按鈕（ch16 CC20=127）→ PANIC | 立刻 `PANIC sent to HST/REAPER`，按住的音補 1 個 note_off，之後輸入只記錄不送；`r` 後 `resumed`、回音恢復 | 生效 |
| 引擎關掉時 Fantom zone 8–15 → DIN 2–7 直通 | 各琴照常發聲 | 不受影響 |

耳朵確認（使用者）：用 Fantom **INT zone（ch1）** 彈，該 channel 不經直通到任何琴，Iridium 響即為引擎回音。250 ms 有延遲跟響、放開即停；PANIC 時 Iridium 立刻靜音；`--delay 0` 幾乎同時響。

**接線事實補充（Phase 1 要處理）**：
- 其他琴（Nord Grand 2 / Wavestate mk II / Iridium / MODX M6 / Ketron Event 61 / PSR-SX900 / microArranger）的 MIDI OUT **目前沒有接** mioXL，DIN2–8 回流在實體上不可能發生。使用者決定加購 7 條 5-pin DIN 線把各琴 OUT 接到 mioXL DIN 2–8 IN（設定不用大改）；接上後 Auracle 的 DIN 2–8 Input **不得**點亮 USB DAW `HST 2`，且要重跑一次 BYPASS 逐台測試。七台琴都有 USB-MIDI（Ketron Event 61 待使用者確認），也可改插 mioXL 的 USB Host 孔。
- Fantom 的 INT zone 也會送到 MIDI OUT（觀測到 ch1–5），不只 EXT zone 的 CH9–15；且某些 scene 下**同一個鍵同時送多個 channel**（例如 ch2+ch13、ch4+ch13、ch5+ch14、ch5+ch9+ch15，timestamp 差 < 1 ms）。HUMAN 判定與 `human_chs` 要把「一次按鍵、多 channel」視為同一事件，不能各自觸發演算法。
- Fantom 切 scene 時在 **ch16** 送 CC0/CC32 + program change；規格說 CH16 保留不用，引擎收到 ch16 的 CC/PC 應忽略，PANIC 仍涵蓋 CH1–16。
- Fantom 另有一條 USB 直接接 ProArt（Windows 顯示 `FANTOM-6 7 8`）。Fantom zone 1 EXT 就是經這條 USB 直接彈 REAPER 的 VST（SWAM、Pianoteq），REAPER 沒開 `HST 1` Input，不會疊音。引擎不開這個 port；人類 CH1 直達 REAPER、引擎的 CH1 生成音走 loopMIDI，兩者一致。
- UC4 同時被 REAPER 與探針開啟沒有衝突。

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

#### Phase 1 實作狀態（2026-09-07，ProArt 16；等使用者驗收）

**啟動**：`start_mie.bat [--mode SAFE|INTERACTIVE] [--scene 01] [--no-ui]`（或 `python -m backend.mie`），面板 `http://127.0.0.1:8810/mie`（引擎自己用 stdlib 提供靜態頁與 WebSocket，不需 NUC；NUC 的 `/mie` 路由也能提供同一頁，頁面固定連 `ws://127.0.0.1:8810/ws`）。終端鍵：`p` PANIC、`r` resume、`b` BYPASS、`s` stats、`m <MODE>`、`q`。任何退出路徑先 PANIC。無硬體驗證：引擎在 ProArt 上以 `--mode BYPASS` 跑 40 s，四個 port 全開、面板連線、退出時 PANIC 兩個 Out port。

**檔案**（全部在 `backend/mie/`）：`events.py`（§2.1 信封 + Proposal）、`state.py`（§2.2 + `human_chs`、EMA 能量、IOI 推估 BPM）、`harmony.py`（held 音和弦辨識 = editor.js `CHORD_MAP`；Krumhansl 調性）、`scales.py`（31 種音階，從 `scale-lab.js` 移植）、`graph.py`（Instrument / Edge / Scene / 模式表 / InteractionGraph）、`probability.py`（§6 restraint、群組輪盤）、`mutation.py`（§5）、`constraint.py`（snap、撞音迴避、late binding）、`safety.py`（§7 第 1–9 層）、`scheduler.py`（heap + (on,off) 配對 + 送出前 re-snap）、`engine.py`（管線 + 模式 + PANIC + UC4 動作）、`io_rtmidi.py`、`ws_server.py`（stdlib HTTP + RFC6455）、`__main__.py`、`fakes.py`（FakeClock / FakeMidiOut）、`algos/{follow,echo,shadow,silence}.py`。設定：`data/mie/instruments.json`（七台琴、CH14/15 arranger 預設關）、`data/mie/scenes/01_safe_echo.json`、`data/mie/control_map.json`（CC20 = PANIC，實測過）。UI：`frontend/mie.html` + `js/mie.js` + `css/mie.css`（頂列 / 能量儀表 / 全域滑桿 / 樂器開關 / 邊清單含 prob 與啟用 / 統計 / 事件流；`Esc Esc` = PANIC）。測試：`backend/tests/test_mie_engine.py`（T1 / T3 / T4 / T6、A→B→A 停在 max_hop、鏈 ≤ 24、fan-out 禁止、看門狗、PANIC 兩 port CC120/123/64 + 補 note_off、human_ch 不當目標、疊 zone 去重、自我回音、SAFE 模式白名單與 0.3 上限）+ 原本 15 個探針測試，共 29 passed。

**執行緒**：rtmidi callback → `in_queue` → Engine thread（唯一改 state）；Scheduler thread 只送 MIDI，送出後把「已送出」通知丟回 `in_queue`，由 Engine thread 做 `active_gen` 記帳與第 10 步回饋；UI thread 只讀快照。MIDI 路徑沒有 asyncio。

**與規格的差異 / 決定（Phase 0 接線事實導致）**：
- **撞音迴避改在送出那一刻做**（`late_bind`），不在排程時做：延遲音真正要避的是「發聲當下」人按住的音。每條邊可設 `collision`：`octave`（同音 ±1 八度，follow / echo 預設）、`unison`、`none`（shadow 與 silence 預設，因為它們的目的就是疊奏）。替代音順序：和弦音 → 調內音 → 丟棄。
- **一鍵多 channel 去重**：同一個 note 在 5 ms 內從另一個 channel 再到 → 只更新 `human_chs`，不重複觸發演算法、不算進密度。
- **ch16 的 CC / PC 忽略**（Fantom 切 scene 用），PANIC 仍涵蓋 CH1–16。
- Silence 的和弦音配置從人類按住的最高音以上開始（`above_held`），避免跟人類同音區疊。
- `human_energy` 的力度項乘上 `min(1, density)`，靜止時能量會真的回到 0（否則 restraint 永遠不會回到 1）。
- Density / Velocity(CC) / Answer / Mirror / Register / 矩陣 UI / MIDI Learn / player playhead 徽章依規格留 Phase 2；`playhead` WebSocket 訊息引擎端已接（chord / key / bpm），player 端尚未送。
- 沒裝 `websockets` 套件、也不用 FastAPI：面板由引擎行程自己用 stdlib 提供，避免在演奏機上多跑一個 uvicorn。

**首次試奏發現與修正（2026-09-07 深夜，SAFE / INTERACTIVE）**：

| 現象 | 原因 | 修正 |
|---|---|---|
| Wavestate 有隨機打擊聲 | MIE Out merge 進 Fantom DIN IN，引擎的 CH10 打到 Fantom scene 的鼓組 part | Auracle：Output DIN MIDI `Fantom 8` 濾掉 CH9–16（見上方接線事實） |
| Echo 很短促、突兀 | `echo.py` 把時值 cap 在 delay×0.9（半拍延遲 → 293 ms），且力度連乘後只剩 v4 | 時值改為跟隨人類音長（`dur_min_beats` / `dur_max_beats`），新增 `min_vel`（低於它就不送這一次重複） |
| Echo 在按住的和弦上跑到非和弦音（A3 → B3） | 撞音迴避預設 `octave`，把重複音推開 | `echo` 的預設 collision 改為 `none`（重複人類正在按的音本來就是 echo 的意義） |
| Silence pad 只出一個音 | `above_held` 把音域下限推高，上限沒跟著放寬，配置只塞得下一個音 | 音域不足時退回設定的 low/high，保證聲部數；scene 的 pad high 48–84 |
| 卡音 | Shadow 只靠 `active_gen` 找要放開的音；短音時人類 note_off 會早於 scheduler 的「已送出」通知到引擎，找不到就放不掉，撐到 8 s 安全上限 | 新增 `Scheduler.release_by_src()`，直接用「這個 shadow 綁的人類音」在 heap 上放開，不依賴 `active_gen` |

| 按著和弦不放卻觸發 silence pad | `silence_s` 依規格 §2.2 是「距最後一個 note_on 的秒數」，按住不放＝沒有新 note_on＝被當成留白 | **留白重新定義為「人類的聲音都停了」**：`held` 或延音踏板撐著的 `sustained` 只要非空，`silence_s` 就是 0；引擎現在也處理 CC64（踏板放開才開始算留白） |
| 按和弦時有時完全沒有 echo、有時只回其中兩個音 | 機率是**每個音各擲一次**（SAFE 模式 p ≈ 0.13，五音和弦有一半機率全部落空） | 新增和弦分組：`chord_window_ms`（預設 45 ms）內按下的音，同一條邊共用一次擲骰，和弦要嘛整組回應、要嘛不回應 |

以上都有回歸測試（`test_echo_length_follows_the_human_note_not_the_delay`、`test_echo_keeps_its_pitch_over_a_held_chord`、`test_silence_pad_keeps_all_voices_when_the_human_plays_high`、`test_shadow_releases_even_if_the_sent_notice_arrives_late`、`test_holding_a_chord_is_not_silence_even_with_the_pedal_down`、`test_a_chord_gets_one_probability_roll_not_one_per_note`），共 35 passed。

**第二輪試奏（環境音樂彈法：按住和弦數秒再換和弦）發現與修正**：

| 現象 | 原因 | 修正 |
|---|---|---|
| 回應非常少（約 1/5 的和弦才有反應） | ① 五音和弦被算成五次密度事件，慢速鋪陳的彈法被判定為「彈得很忙」，restraint 壓到 0.5；② 改成和弦共用一次擲骰後，每個和弦只有一次機會，但邊的機率仍是照「每音一次」的舊語意設定 | ① 45 ms 內的音只算一次密度與一次 IOI（`GESTURE_WINDOW_S`）；② 邊的 `prob` 語意正式改為「這個手勢被回應的機率」，scene 對應上調（shadow 0.7→0.8、echo 0.5→0.75、follow 0.4→0.55） |
| Iridium 常常吃到整組和弦、還被丟棄 | `shadow: top` 判斷的是「當下按住的最高音」，由低往高彈時每個音都曾是最高音，五個音全送出去 | 同一手勢內後到的音取代先到的（`_regroup_shadow`），一個和弦只留一個影子；scene 的 shadow 延遲 30→60 ms，讓和弦先落定 |
| 和弦回音常常缺幾個音 | 限流器在「排程當下」計費，一個和弦的十個回音雖然分散在半秒內送出，卻被當成瞬間爆量；per-channel burst 8 也蓋不住一個手勢 | 限流改用「實際發聲時間」計費；`NOTE_BURST` 8→12（持續速率 20/s 不變）；scene `max_gen_notes_per_s` 12→24；Wavestate 10 聲部、Iridium 8 聲部 |

以環境彈法模擬 24 個和弦（每個按住 5 秒、間隔 2 秒）：INTERACTIVE 從 15% 提升到 92% 的和弦有回應，且**安全層丟棄為 0**（安全層不再默默修剪音樂，剩下的疏密純粹由機率決定）；SAFE 因 0.3 上限維持稀疏的 46%。

**規格修訂**：§2.2 的 `silence_s` 定義改為「距最後一個人類聲音結束的秒數（`held` 與踏板 `sustained` 皆空之後才起算）」，`MusicalState` 新增 `sustained` 與 `sustain`（每個 human channel 的 CC64 狀態）。

**第三輪試奏：持續按壓（pad）彈法（2026-09-07 深夜）**

使用者指出：「我按下和弦時馬上有回應，但手還沒離開、pad 音還在發聲，之後就都沒回應了。」

原因是設計缺口不是 bug：除了 Silence（tick 驅動）之外，所有演算法都由 note_on 觸發，所以「按住不放」＝沒有新事件＝引擎不說話；而 Silence 又刻意在還有聲音時不作用。兩者中間沒有東西。

**新增 Sustain 演算法**（`backend/mie/algos/sustain.py`，規格 §4-11），經使用者選定：每 1–2 小節回應一次。設計重點：
- 觸發條件是 `st.sounding` 非空（`held` ∪ 踏板 `sustained`），與 Silence 互補、互不重疊。
- 每次挑一個「和弦內、lane 還沒蓋到的音色」，優先挑人類沒在彈的音級（加色而非單純疊厚）；`voices` 滿了就先放掉最舊的。
- 落點對齊下一拍（`align`），時值 `hold_beats` 預設 8 拍，所以聲部會互相重疊、緩慢演化。
- 人類的聲音真的停了 → `release_beats` 後整條 lane 淡出，且不會自己再開口。
- **機率門只決定濃淡，不決定節奏**：被拒絕的窗口在 `retry_beats`（預設 1 拍）後重試，否則 p≈0.6 會讓「每 1–2 小節」變成「每十幾秒」。人類彈得忙時 restraint 下降、連續重試都失敗，lane 自然安靜下來，這才是預期的音樂控制。
- 撞音迴避對 sustain 預設 `none`（pad 聲部本來就該跟著和弦疊）。

模擬（92 BPM，一小節 2.6 秒，按住和弦 12 秒 ×4）：INTERACTIVE 每 48 秒 11 個聲部、同一和弦內平均間隔約 5 秒；SAFE 約 6–7 秒（0.3 上限使然）；安全層丟棄 0。

**尚未實作、使用者提過的更大構想**：彈法／織度辨識（持續按壓 / 琶音 / 旋律 / 打和弦，以及左手低音 + 右手旋律的分手判斷），讓不同的邊只在特定彈法下作用。現有 buffer（`recent_notes` 256 筆、`recent_ioi`、`recent_intervals`、`register`、`direction`）足以支撐，但分類器與 `when: {texture: ...}` 邊條件都還沒做，列為 Phase 2 候選。

**程式碼審核修正（2026-09-07，使用者審核）**

| 等級 | 發現 | 修正 |
|---|---|---|
| 高 | `set_instrument` / `set_edge` 由 WebSocket 執行緒直接改 `Instrument` / `Edge`，而 Engine thread 與 Scheduler thread（`_before_on`）同時在讀 | 這正是規格 §1 早就寫的「UI 的參數變更也丟進 `in_queue` 當控制訊息處理」，實作時漏了。新增 `Engine.submit(fn, *args)`：WS 與 console 執行緒一律把呼叫排進佇列，由 Engine thread 執行；壞掉的 UI 訊息只會記一筆 `error` 事件，不會殺掉引擎執行緒 |
| 高（延伸） | PANIC 必須立即生效、不能排隊，但它會 `clear()` 發聲池，而其他執行緒正在迭代同一個 dict；且多執行緒同時對 rtmidi output port 寫入 | 所有 `active_gen` 的迭代改成先 `list()` 快照（CPython 下這是原子操作）；`MidiIO.send` 加互斥鎖，序列化送出的位元組。PANIC 因此可以安全地從任何執行緒直接呼叫 |
| 中 | `mutation._weighted` 在 `weights` 全為 0 時 `tot=0`，輪盤邏輯失效 | `tot <= 0` 或長度不符時退回 `rng.choice`；空 `choices` 直接拋錯 |
| 中 | `constraint.snap` 的 `best` 在找不到音時為 `None`，靠尾端三元運算保護，維護時易踩雷 | 拆成 `best_key` / `best_note` 兩個變數、加上 `lo > hi` 的早退與註解，回傳 `None` 的路徑一目了然 |
| 低 | `_shadow_group` 與 `_roll_cache` 以 `edge.id` 為鍵，切換 Scene 後不會清掉 | `load_scene()` 一併 `clear()` 兩者 |

**第二輪程式碼審核（2026-09-07）**

| 等級 | 發現 | 處置 |
|---|---|---|
| 高 | `MusicalState.sounding` 的 `{**self.sustained, **self.held}` 合併可能在其他執行緒讀取時出錯 | **部分成立**：這個合併在 CPython 下是 C 層原子操作、不會丟 `RuntimeError`（`dict.update` 全程持有 GIL）。但確實有真正的跨執行緒讀者——UI 執行緒每 100 ms 呼叫 `to_dict()`。已改為顯式快照（`sounding` 與 `to_dict` 都先 `dict()` / `list()`），意圖清楚，也讓程式在未來的 free-threaded build 上仍然正確 |
| 高 | `TokenBucket.take()` 接受未來的 `t_send`，會把桶子的時鐘推到未來，**預支還沒到的額度**；之後的即時請求因而不受限 | **完全成立，這輪最重要的發現**。音符限流改用 `SendWindowLimiter`：沿著「送出時間軸」計數，任何一秒視窗內最多 `rate` 個音，與到達順序無關。`TokenBucket` 只留給 CC（只用牆鐘）。`NOTE_BURST` 這個補丁式的參數因此取消，視窗本身就是允許量 |
| 中 | `snap()` 在 `d == 0` 時 `cands` 為 `(note, note)`，重複評估 | 成立，改為 `(note,) if d == 0 else (note + d, note - d)` |
| 中 | `_ema` 的 `1.0 - math.exp(-dt/tau)` 在 `dt << tau` 時損失精度 | 成立，改用 `-math.expm1(-dt/tau)`。實測 `dt=1e-9` 時舊寫法直接歸零，新寫法正確 |

回歸測試：`test_rate_limit_is_measured_on_the_send_timeline`、`test_rate_limit_ignores_the_order_notes_are_admitted_in`、`test_sounding_is_a_snapshot`、`test_ema_keeps_precision_for_tiny_time_steps`。環境彈法模擬重跑：SAFE 22/24、INTERACTIVE 24/24 個和弦有回應，安全層丟棄仍為 0。

全部有回歸測試（`test_ui_parameter_changes_run_on_the_engine_thread`、`test_a_bad_ui_command_does_not_kill_the_engine`、`test_loading_a_scene_clears_the_per_edge_caches`、`test_snap_returns_none_when_nothing_in_range_fits`、`test_mutation_survives_all_zero_weights`），共 47 passed。

**Phase 1 驗收狀態**：使用者在 SAFE 與 INTERACTIVE 各彈 10 分鐘，無卡音、`loops` 維持 0、三種 PANIC 皆有效；`silence_pad` 依使用者要求關閉（SP-404 MK II 已鋪環境音）。等使用者完成程式碼審核後才算通過。

**驗收方式（規格 §11 Phase 1）**：使用者用 `start_mie.bat --mode SAFE` 彈 10 分鐘、再 `--mode INTERACTIVE` 彈 10 分鐘，觀察無卡音、無迴圈（面板 loops = 0）、UC4 / 面板 / `Esc Esc` PANIC 一鍵有效。

### Phase 2 — 互動與控制

> **使用者驗收（2026-09-07）**：Phase 1 通過。演奏驗收 SAFE 與 INTERACTIVE 各 10 分鐘、無卡音、`loops` 0、三種 PANIC 皆有效；兩輪程式碼審核結論 APPROVED。審核者附上 Phase 2 的架構方向，整理如下。

#### Phase 2 的音樂性目標：避免「學術上精準、音樂上難聽」

使用者指出 Phase 1 的生成雖然安全，但音高選擇仍是「最近的合法音」，缺少音樂邏輯。Phase 2 要補三件事：

| 原則 | 現況 | 要做的 |
|---|---|---|
| **功能和聲優先於音名相似度** | 引擎只知道和弦音與調內音（`constraint.py`），不知道 T / S / D 功能 | 新 `function.py`：把和弦分類為主 / 下屬 / 屬功能與代換群；Answer、Register、Silence、Sustain 選目標音時先問功能，而不是只問「哪個音在和弦裡」。例：C 大調的主功能區可以給 `Am7` / `Em7` 的色彩音，而不是只為了湊音去拿異調音 |
| **聲部導向（Voice Leading）** | `snap()` 找的是「離提議音最近」的合法音，不看同一 lane 上一個音在哪 | 改成「離同 lane 前一個音最近」，並讓三音與七音（Guide Tones）正確解決；避免平行五度與無意義大跳。這是最直接能把機械感壓下去的一項 |
| **和聲節奏（Harmonic Rhythm）** | 只有 Sustain 有 `align`，其餘 lane 想送就送 | 和弦相關的生成一律鎖強拍；弱拍的極端離調音只在 tension 參數很高時才允許。離調音必須被解釋成 b9 / #11 / b13 或副屬，不能是隨機結果 |

#### 雙軌與前瞻（Dual-Buffer / Look-Ahead）

- **歷史軌**：Phase 1 已有（`recent_notes` 256 筆、`recent_ioi`、`recent_intervals`、`pc_hist`）。
- **預測軌**：提前 1–2 拍推測和聲走向，預先排程。**要先講清楚它買到的是什麼**：MIE 的延遲已經是 0.2 ms 等級（它是 MIDI 反應式引擎，不做音訊分析、不必等樂句結束），所以前瞻買到的**不是延遲，而是樂句感**——讓生成音跟人「一起到」而不是「跟在後面」。這值得做，但別用「Zero Latency」當理由去做錯的東西。
- **Top-K 候選**：引擎已有加權輪盤邊群組（`group_id`，一次擲骰選一條），缺的是把候選與信心值送到 UI 顯示，以及讓使用者看得到「為什麼是這個」。

#### 高階可控參數（對應 §9.1）

樂手不打字，用旋鈕改變生成邏輯。三個高階旋鈕映射到現有低階參數：

| 旋鈕 | 映射 |
|---|---|
| **Tension**（0 = 純三和弦 → 100 = Alt / 離調） | `constraint`（chord → scale → free）、允許的延伸音集合、`chaos`、離調音是否需要副屬解釋 |
| **Density**（每小節換幾次） | `every_bars_*`、`prob_scale`、`repeats`、Density 演算法的門檻 |
| **Style Vector**（Pop → Neo-Soul → Bebop → Impressionist） | 一整組邊的預設值 + 音階選擇 + 突變權重；等同「一鍵換整組邊」的介入風格預設 |

#### 明確劃清界線（避免把別的子系統搬進 MIE）

使用者的架構筆記裡有一段是「即時音訊聆聽（Chroma / Pitch）→ 和弦推估 → 重和聲」。**這不屬於 MIE**：
- MIE 是 MIDI 互動引擎，輸入是琴鍵，不做音訊分析（§0.1 選項 B 的理由）。
- 音訊和弦辨識是既有的 `backend/chord_detect.py`（BTC，離線、process pool）。
- 旋律重和聲是既有的 `backend/ai/reharmonizer.py`（Jazzify L1/L2/L3 + transformer）。
- MIE 需要和聲上下文時，走 §10 的優先序：player 時間軸 → 按住的音辨識 → Krumhansl 推估。

要做的是**讓 MIE 用上這些既有結果**（例如把 `reharmonizer` 的功能和聲表移植進 `function.py`），而不是在 MIE 內重寫一套音訊管線。

#### Phase 2 進度

**① 聲部導向（完成 2026-09-07）** — `backend/mie/voicing.py`

Phase 1 用 `constraint.snap()` 選音：離「演算法提議的音」最近的合法音，完全不看同一 lane 前一個音在哪，所以一條線可以在兩個和弦之間跳一個八度以上，每次進入都像不相干的新事件。這就是使用者說的機械感主因。

`lead()` 的成本函數（單位是半音）：跟前一個音的距離（權重 1.0）＋ 跟演算法提議音的距離（權重 0.5），級進（1–2 半音）給獎勵，超過五度的跳進逐半音加罰，導音解決（7 音或 4 音向下級進到和弦音）給獎勵，與其他發聲聲部形成平行五度或八度重罰。**刻意不罰「不動」**：保留共同音是聲部導向的正確做法。

三種模式，每條邊可設 `voice_lead`：

| 模式 | 意義 | 預設用在 |
|---|---|---|
| `off` | Phase 1 行為：離提議音最近 | echo（必須保留原音高）、shadow（跟隨特定聲部）、silence（一次送整組配置，沒有單一線條）、follow |
| `octave` | **保留演算法選的音級，只決定音域** | sustain |
| `free` | 連音級都可以換 | 需要時手動指定 |

實測（環境彈法，5 個和弦各按住 10 秒，同一 seed）：

| 模式 | 平均移動 | 最大跳進 | 用到的音級數 |
|---|---|---|---|
| `off` | 10.4 半音 | 19 | 6 |
| `octave` | 5.3 半音 | 8 | 6 |
| `free` | 0.7 半音 | 2 | **3** |

`free` 把線壓成幾乎不動的單音——因為它把 Sustain 精心挑的音色選擇整個覆蓋掉。所以 sustain 預設是 `octave`：**音色由演算法決定、音域由聲部導向決定**，色彩完整保留而跳進砍半。這個取捨值得記住，之後做 Answer / Mirror 時同樣適用。

引擎新增 `_lane_hist`（每條 lane 的前一個音、上一個音、時間），送出時更新；平行五度偵測只看 2 秒內動過的其他 lane。

**② 試彈回饋與第二輪審核修正（2026-09-07）**

| 現象 / 發現 | 原因 | 修正 |
|---|---|---|
| Echo「出現一下就消失」，使用者要的是山谷回音那樣來回幾次再衰減 | `repeats: 2` 加上 `min_vel: 24`，輕彈時兩次就低於門檻 | `repeats` 改成**上限而非目標**，由衰減決定尾巴長短：彈 v100 有 6 次回音、v25 只有 3 次，跟真實回音一樣「彈得重傳得遠」。新增 `dur_decay`（每次回音更短）與 `spacing_growth`（可拉開間距） |
| 回音尾巴撞爆聲部預算（丟棄 60–103 個音） | ① 按住和弦時每次回音跟著長達 5 秒，六次全疊在一起；② **聲部預算把「未來才會響的音」當成「現在同時發聲」**（`pending_on` 數的是所有已排程的 note_on），跟限流器先前那個 bug 同源 | ① 同音回音必須**接續而非重疊**：MIDI 同一 channel 無法同時發兩個相同音高，前一次的 note_off 會把後一次切掉；真實山谷回音本來也是分明的。`max_overlap` 預設 1.0，scene 的 delay 改成 1 拍讓每次回音完整響完。② 新增 `Scheduler.sounding_at(ch, t)`：只算「那一刻真的會同時發聲」的音，每個 pair 在 heap 裡只有一個 off 項目，不會重複計數 |
| `_lane_hist` 不會過期，休止很久後的新音仍被很久以前的音牽引 | 沒有清理機制 | `tick()` 每次過期一次，門檻 16 拍（下限 8 秒） |
| 平行音程比對的 2 秒視窗在極慢板可能來不及 | 寫死秒數 | 兩個視窗都改成**以拍為單位**：平行偵測 4 拍（下限 2 秒）、lane 歷史 16 拍（下限 8 秒），自動跟著速度走 |

環境彈法模擬（12 個和弦，每個按住 6 秒）：SAFE 回音 120 音、INTERACTIVE 196 音，**安全層丟棄 0、殘留發聲 0**。

**③ 功能和聲（完成 2026-09-07）** — `backend/mie/function.py`，新增 `constraint: "function"`

Phase 1 只有兩種選音方式，而且都不是音樂家的做法：

- `constraint: "chord"` 只用字面上的和弦音。按住一個三和弦就只有三個音級，pad 線很快沒色彩可用，變成單音持續。
- `constraint: "scale"` 整個調內都合法。每個音都「合規」，但線會走到模糊功能的音上——正是審核者警告的「學術上精準、音樂上難聽」。

新的 `constraint: "function"` 介於兩者之間：**和弦本身的音，加上調內同功能和弦借給它的音**。C 大調的 C 和弦屬主功能，同組還有 Em7 與 Am7，所以色彩集是 C D E G A B——伴奏者真正會伸手去拿的音——而會把和聲拉向下屬的 F 被排除在外。G7 屬功能組是 G7 與 Bm7b5，色彩集 D F G A B，刻意不含 C 與 E（屬和弦要解決到它們，不是坐在上面）。

**T/S/D 的分類沒有重寫**，直接 import `backend/ai/jazz_rules.py`——離線 reharmonizer 已經在用的那份，只依賴標準庫，所以兩個子系統不會各自漂移。這比移植更好：單一真相來源。

實測（按住一個 C 三和弦 60 秒，同一 seed）：

| constraint | 用到的音級 | 結果 |
|---|---|---|
| `chord` | 3 種：C E G | 單音持續，沒有色彩 |
| **`function`** | **5 種：C D G A B** | 加上 9 音、6 音、7 音，功能清楚 |
| `scale` | 4 種：D F A B | 含 F，主功能被模糊掉 |

**架構上學到的一點**：一開始只改 `allowed_pcs()` 完全沒有效果，因為 `sustain._candidates()` 自己寫死了「用和弦音」當調色盤，後端的約束只能「收窄」提議、不能「放寬」。演算法必須向 constraint 要調色盤，設定才有意義。Silence 刻意維持用和弦音——pad 負責把和弦講清楚，sustain 線負責上色。

**④ 排程器釋放路徑優化（審核建議，完成 2026-09-07）**

審核指出 `release()` / `release_by_src()` / `release_lane()` 對 `self.heap` 做線性掃描再 `heapify()`，事件多時會吃 CPU。先量測再動手：

| 堆積大小 | 修改前（命中） | 修改後 | 沒命中 |
|---|---|---|---|
| 100 | 16.9 µs | **1.1 µs** | 0.5 µs |
| 400 | 69.6 µs | **2.3 µs** | 0.5 µs |
| 2 000 | 349 µs | **8.3 µs** | 0.5 µs |
| 8 000 | 1 475 µs | **31 µs** | 0.5 µs |

實際環境彈法時堆積峰值只有 75 項（約 13 µs），所以今天還不是問題；但 8 000 項時單次呼叫 1.5 ms **超過排程器自己的自旋門檻 `SPIN_S`，而且是持鎖進行**，會直接變成 jitter。而且修改前「完全沒命中」跟命中一樣貴，`release_by_src` 每個人類 note_off 都會呼叫一次，多半是空跑。

兩項修改：

1. **每個 channel 一份 live pair 索引**。刻意用 channel 當鍵——`note` 會在送出前被 late binding 改掉，用它當鍵會失效，這是個陷阱。
2. **惰性刪除取代 heapify**：要讓音提早停，就推一個新的 off 項目，舊的在 pop 時因為 `off_sent` 已為真而略過；還沒開始的音直接標記 `dropped`，`pump` 本來就會跳過。整條路徑不再有 `heapify`。

`release_lane()` 沒有任何呼叫者，一併刪除。真執行緒實跑 12 秒（每 1.3 秒一個五音和弦）：jitter p95 0.71 ms、max 0.88 ms、丟棄 0、PANIC 後殘留 0。

**⑤ 和聲節奏鎖強拍 + 離調音必須可解釋（完成 2026-09-07）**

**對齊提升成引擎層級的邊屬性**。原本只有 Sustain 自己算 `align`，其餘 lane 想送就送。現在 `align` 是 `Edge` 的欄位，接受 `none` / `half` / `beat` / `bar` 或直接給拍數（`0.25` = 十六分音符），由引擎在排程時統一量化。預設值分兩類：

| 類型 | 預設 | 理由 |
|---|---|---|
| 時間驅動（silence / sustain） | `bar` / `beat` | 它們是自己決定開口的，理應照著音樂進來 |
| 事件驅動（follow / echo / shadow） | `none` | 它們在回應人類的演奏，該跟著人的時間感，量化反而破壞回應的即時性 |

**前提是拍點網格要對得上真實音樂**。自由演奏時 `clock_source` 是 `scene` 或 `ioi`，`beat_origin_t` 只是「引擎啟動的時刻」，量化到那個網格是任意的、甚至相位是錯的。所以新增：**沒有外部時鐘時，把「休息超過一小節之後的第一個音」當成強拍**，用演奏者自己建立的脈動當網格。有 player 時間軸或 MIDI Clock 時則以外部時鐘為準，引擎不再自行改錨點。

**離調音必須叫得出名字**。新增 `tension`（scene 全域，可被單一條邊覆寫），開放的是**有名字的音級**而不是任意半音：

| tension | 開放的音 |
|---|---|
| < 0.35 | 無，只有和弦音與功能色彩 |
| ≥ 0.35 | 調內的 9 / 11 / 13 |
| ≥ 0.70 | 所有自然延伸音（含調外） |
| ≥ 0.85 | 變化音 b9 / #9 / #11 / b13 |

實測（按住 C 三和弦 50 秒，sustain 線用到的音）：

| tension | 用到的音 |
|---|---|
| 0.0 | C(1) D(9) G(5) A(13) B(7) |
| 0.5 | D(9) F(11) A(13) B(7) |
| 0.9 | C#(b9) D(9) D#(#9) F(11) F#(#11) G#(b13) A(13) B(7) |

每一個都是可命名的音級，沒有一個是「剛好合法」的隨機半音。這也是 Phase 2 第五項 Tension 旋鈕的底層，旋鈕之後只要接到這個值。

順帶把所有 tick 演算法的簽名統一成帶 `tension` 參數，取代原本 `try/except TypeError` 的寫法——後者會連演算法內部真正的 TypeError 一起吞掉。

**⑥ 例外不再無聲（審核建議，完成 2026-09-07）**

審核指出 `_run_command` 只呼叫 `self._ui("error", ...)`，沒有 UI 連著時 stack trace 就消失了。順著查下去發現三層問題，由輕到重：

| 層級 | 原本 | 現在 |
|---|---|---|
| UI 指令失敗 | 只推一筆 UI 事件 | `logging` 輸出完整 traceback，UI 事件照舊 |
| `_before_on`（排程器執行緒） | **`except Exception: return True`，完全靜默**。這條在送音的關鍵路徑上，出錯等於徹底隱形 | 照舊 fail open（音在排程時已經約束過，送出去比丟掉安全），但一定記錄 |
| PANIC 送出失敗 | `except: pass` | 繼續送其餘訊息與另一個 port，但記錄——PANIC 時 port 掛掉正是最需要知道的事 |

**更嚴重的是測試順手抓到的兩件事**：

1. **引擎執行緒本身沒有任何例外防護**。一個壞掉的 instrument 設定會讓執行緒直接死掉，而且是無聲的：port 還開著、面板還在更新快照，但引擎已經不處理任何事件。現在 `drain` / `loop` / `tick` 都有防護加記錄。
2. **一條壞掉的邊會連累同一事件的其他邊**。例外會中斷 `_fire_edges` 的迴圈，後面的邊全部跳過。現在每條邊各自隔離，壞掉的那條靜音並記名，其餘照常演奏——這跟 §7「每一層安全獨立生效」是同一個原則。

熱路徑防洪：同一個位置的錯誤第一次與每第 100 次輸出 traceback，其餘只累加計數，UI 事件流則每次都收得到。實測輸出：

```
ERROR mie.engine: mie: edge[shadow_iridium] failed (1 time(s))
Traceback (most recent call last):
  ...
ValueError: too many values to unpack (expected 2)
```

`start_mie.bat` 新增 `--log-file` 與 `--verbose`；預設輸出到 stderr，所以終端本來就看得到。`graph.list_scenes()` 讀到壞掉的 scene 檔會警告而不是靜默跳過，`function.py` 找不到 `jazz_rules` 時會警告並退回內建的 T/S/D 表。

**⑦ 實奏 log 分析與修正（2026-09-07 深夜，兩輪：按壓和弦 / 低音+旋律+和弦）**

新的 session log 讓整段演奏可以事後分析。1265 行、140 個人類音、390 個生成音、安全層丟棄 0、迴圈 0、jitter p95 0.79 ms。分析找出三個缺陷：

| 缺陷 | 證據 | 修正 |
|---|---|---|
| **引擎從頭到尾沒跟上速度** | 99 個快照 `clock` 全是 `scene`、BPM 卡在 92；演奏者 IOI 中位 0.429 s（約 140 BPM），所以 echo 的一拍延遲等於他的 **1.52 拍**，生成音相對他拍點的相位分布幾乎是平的 | `_estimate_bpm` 原本要求單一 4-BPM 區間吃下 60% 的 IOI，真實演奏混著四分／八分／附點，門檻**永遠達不到**。改成對候選脈動評分：每個間隔是不是該脈動的常見音值，簡單比例（1、1/2、2）權重高於附點比例（1.5、3/4），避免「三分之二速」的讀法勝出；已鎖定後要明顯更好（+12%）才換，免得樂句中途飄移 |
| **silence lane 的觸發被一次擲骰吃掉** | `silence_texture` 在 15.5 s 靜默中只有 1 筆 skip、0 次觸發，而有 25 個快照完全沒聲音 | `fired` 旗標在機率門**之前**就立起來，擲輸就永久退場。加上 `on_skip`（跟 Sustain 同一個修法），`retry_beats` 後重試 |
| **邊的音域在送出時被丟掉** | CH4 實測音域 76–91，但那條邊設的上限是 88 | 聲部導向在**樂器**音域裡挑，不看邊的 low/high。`late_bind` 增加 `note_range`，取兩者交集 |

**已知限制（不假裝精準）**：第一輪的資料本身沒有明確脈動——68 BPM 得分 0.64，112/114/116/118 全是 0.62，實質平手，因為那是自由速度的和弦鋪陳。與其加一條只對這兩筆樣本有效的判別規則，面板改成**顯示脈動推估值與信心百分比**（BPM 欄旁邊，滑鼠移上去有說明），讓演奏者自己判斷。第二輪（有明確律動）鎖在 131 BPM，等於他一拍的 1.09 倍，可用。

**調校觀察，交給使用者決定**：回音佔生成音的 80%（390 中的 311 個都在 CH10），人機比 1:2.8。這不是 bug 是品味，所以做成可調（見下）。

**⑧ 邊參數編輯器（使用者要求「保留彈性」，完成 2026-09-07）**

面板每一條邊多了展開鈕，裡面是這條邊自己的參數，改了立刻生效（透過 §1 的 `submit` 佇列進引擎執行緒）：

| 演算法 | 可調 |
|---|---|
| 共通 | 機率、力度倍率、移調、八度、`constraint`、`align`、`voice_lead`、`collision` |
| echo | 回音次數、間隔拍數、衰減、最小力度、時值衰減、重疊 |
| follow | 音程 |
| shadow | 延遲、最長持續 |
| silence / sustain | 等待秒數、間隔小節、聲部數、力度、持續、釋放拍數、音域上下限 |

實測 WebSocket 來回：整數、浮點、dataclass 欄位（`constraint`）、`params` 欄位（`align`）四種都正確寫入並回報。這是 §9.1「介入的方法與程度」的第一批，Phase 2 第五項的矩陣檢視與 Scene 存檔仍待做。

**⑨ 第二次實奏驗證（2026-09-07 18:27，88 秒，463 個人類音）**

三個修正的前後對照，全部用同一種量測方式：

| 指標 | 修正前（18:09） | 修正後（18:27） |
|---|---|---|
| `clock` 來源 | `scene` 99/99 快照 | **`ioi` 99/111**，t≈17 s 鎖定 |
| 採用的 BPM | 92（與演奏無關） | 177.4，整首只換過 2 個值、標準差 12 |
| 引擎一拍 ÷ 演奏者間隔 | 1.52（既非拍上也非細分） | **1.16** |
| 生成音相對演奏者拍點的相位 | `{正拍 86 … 反拍 70}` 幾乎平坦 | **`{238, 188, 159, 79, 74, 50, 35, 48}`** 由拍點衰減 |
| 人機比 | 1:2.87 | 1:1.88 |

黏著規則也如預期生效：逐音的原始推估在 106–179 之間游移，但採用值穩在 177.4，因為要明顯更好（+12%）才換。脈動判讀本身是對的——演奏者的間隔直方圖集中在 0.3 s（127 次），177 BPM 的一拍 0.339 s，得分 0.730 遠高於 103 BPM 的 0.530。

安全層：丟棄 15/887（`rate_ch` 10、`rate_global` 5）、迴圈 0、jitter p95 1.07 ms max 2.78 ms。

**⑩ silence 的「空間」定義可選（本次發現）**

`silence_texture` 在 88 秒裡只進來一次。不是 bug：演奏者 111 個快照裡有 69 個踩著延音踏板，而 §2.2 的留白定義是「人類的聲音都停了」（正是 2026-09-07 稍早依使用者要求改的）。踏板踩滿全曲的人，用這個定義永遠不算留白。

兩種定義都對，取決於意圖，所以做成每條邊可選：

| `silence_mode` | 意義 | 適合 |
|---|---|---|
| `sound`（預設） | 人類的聲音全停，含踏板 | pad 不該疊在按住的和弦上 |
| `attack` | `after_s` 內沒有新的按鍵 | 踏板延音的演奏，texture 仍要能進來 |

scene 的 `silence_texture` 改用 `attack`，`silence_pad` 維持 `sound`（目前停用）。面板的邊參數區對 silence 演算法會多出這個下拉選單。

**⑪ 樂句回音 Phrase Echo（2026-09-07，使用者提出）**

使用者問：「目前 echo 是單音還是一段音符？例如在山中喊『你好嗎』會重複這句話然後衰減（像簡短的 looper）」——並指出「這個很重要，表示 MIE 回應具有音樂性」。

**檢查 18:45 那份 log 的結果：是逐音的。** 494 個人類音只有 142 個（29%）被回音，因為 `echo` 每一個音各擲一次骰；抽樣的一句 21 個音回來時少了 9 個。**節奏本身是準的**（回來的音彼此間隔與原句完全一致、整體晚一拍），壞的只是「有幾個字沒回來」。換句話說，山谷回的是「你＿嗎」。

新增 `backend/mie/algos/phrase.py`，作法與 echo 相反：

| | echo（逐音） | phrase（整句） |
|---|---|---|
| 擲骰時機 | 每個 note_on | 每一句一次 |
| 觸發 | 立刻 | 等 `phrase_gap_beats` 沒有新音＝句子結束 |
| 回什麼 | 這一個音 | 整句，內部節奏原封不動 |
| 對齊 | 可量化到拍 | **`align` 預設 `none`** |

最後一列是重點：**把樂句逐音量化，等於把這個演算法唯一存在的理由抹掉**，所以 `ALIGN_DEFAULT["phrase"] = "none"`。

實作上踩到一個坑值得記：偵測「句子結束」本身要花 `phrase_gap_beats`，所以開口的那一刻已經遲了，第一個音的 `t_offset` 算出來是負的，被我原本的 `if offset < 0: continue` 丟掉——**回音掉了第一個字，正是這個演算法要避免的事**。改成把整趟往後平移（`shift = max(0, -start)`）：晚一點可以，缺字不行。

實測（三音手勢 72 / 74 / 71，內部間隔 0.30 與 0.45 秒）：

| | 回來的內容 | 內部間隔 |
|---|---|---|
| 第 1 次 | +1.45 n72 v58　+1.75 n74 v58　+2.20 n71 v58 | 0.30 / 0.45 |
| 第 2 次 | +2.81 n72 v41　+3.11 n74 v41　+3.56 n71 v41 | 0.30 / 0.45 |
| 第 3 次 | +4.21 n72 v30　+4.51 n74 v30　+4.96 n71 v30 | 0.30 / 0.45 |

三趟都是完整的「你好嗎」，節奏不變、力度遞減、衰減到 `min_vel` 以下自動停。scene 把它放在 MODX M6（CH12，先前 enabled 但沒有任何邊用到），`prob 0.8`、`repeats 3`、`vel_scale 0.72`、`constraint free`（回音要像原句，不該被和弦重新吸附）。面板的邊參數區多出重複次數 / 句尾間隔 / 延遲 / 衰減 / 最少最多音數 / 最小力度七個旋鈕（`mie.js?v=5`）。

新增 9 個回歸測試，其中三個守住這次的教訓：整句回來、內部節奏不變、任何 `delay_beats` 下都不掉第一個音。MIE 測試共 88 個。**尚未上機演奏驗證。**

#### Phase 2 工項（原本規劃 + 上述新增）

- Answer、Mirror、Density、Velocity(CC)、Register；輪盤邊群組；Scene 切換淡出；UC4 MIDI Learn；矩陣 UI + 互動流動畫；player `playhead` 同步。
- **邊編輯器與介入風格預設**（§9.1）：在面板上新增／刪除邊、切換演算法、調整上表所有參數、Scene 存檔。
- **彈法／織度辨識**：持續按壓 / 琶音 / 旋律 / 打和弦，加上左手低音與右手旋律的分手判斷；邊可加 `when: {texture: [...]}` 條件，只在特定彈法下作用。現有 buffer（`recent_notes` / `recent_ioi` / `recent_intervals` / `register` / `direction`）足以支撐，缺分類器與條件語法。
- ~~**功能和聲 `function.py`**~~ **完成**，見上（直接 import `jazz_rules`，比移植更好）。
- ~~**聲部導向**：`snap()` 改成參考同 lane 前一個音；Guide Tones 解決；平行五度與大跳的懲罰。~~ **完成**，見上。
- ~~**和聲節奏鎖強拍**~~ **完成**，見上。
- ~~**樂句回音**：整句回應而非逐音，保留內部節奏~~ **完成**，見上（⑪）。
- **前瞻軌**：提前 1–2 拍推測和聲走向並預排；買的是樂句感，不是延遲。
- **Top-K 透明度**：把候選與信心值送進 WebSocket，UI 顯示「為什麼是這一條」。
- **高階旋鈕**：Tension / Density / Style 三個參數映射到低階設定（見上表）。
- 測試：T2（C D E → 另一 ch 有 3 音回答，落在強拍）、T5（持續 Cmaj7 → 60 s 內活躍樂器數單調遞增）；新增聲部導向與功能和聲的單元測試（固定 seed，斷言移動距離與解決方向）。

**建議順序**（由「最能立刻改善聽感」到「最花工」）：~~① 聲部導向~~（完成） ~~② 功能和聲~~（完成） ~~③ 和聲節奏鎖強拍~~（完成） ~~④ 樂句回音~~（完成） ⑤ 彈法辨識 ⑥ 高階旋鈕與邊編輯器 ⑦ 前瞻軌 ⑧ 其餘演算法（Answer / Mirror / Density / Velocity / Register）。

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
- 風險：Fantom INT zone 的 MIDI Rx channel 需在 Fantom 端設好（zone N 收 CH N），否則引擎送到 DIN1 的 CH2–8 全打到同一 zone；Fantom 的 MIDI Thru 必須關，否則 DIN1 In 收到的生成音會從 DIN1 Out 再回到 mioXL。
- 風險：使用者切換 Fantom EXT zone 時 `human_chs` 會跟著變，前一個 zone 上已排程的生成音仍會送出；這是預期行為（人剛離開那台琴），但 Shadow 這種「跟隨 note_off」的 lane 要在 zone 切換時明確收掉。
- Python GIL：引擎是純事件驅動、每事件工作量微秒級，不會像 chord2vec 那樣長迴圈；但 **任何** 需要 numpy/ML 的未來功能（例如 motif 相似度用 DTW）必須丟到子行程，同 CLAUDE.md 既有規則。

---

## 13. 使用者已確認的決定（2026-09-06，Phase 0 可啟動）

| 項目 | 決定 |
|---|---|
| 接線現況 | Fantom 8 = mioXL port 1（In+Out），唯一主控鍵盤；zone 8–16 EXT；port 1 In → DIN2–8，CH9–15 各琴自濾；此路徑不動 |
| mioXL USB port | `HST Port 1` 保留 REAPER；`HST Port 2` = MIE In（Fantom port 1 副本）；`HST Port 3` = MIE Out（Auracle 內 merge 到 DIN1–8）。絕不與 REAPER 共用 port |
| UC4 | 已直接 USB 接 PC；引擎獨立開一個 In port，依名稱選取；全部視為 CONTROL |
| MIDI Clock | ① LiveChord player 播歌時間軸 ② Fantom 經 port 1 送 clock ③ IOI 推估 ④ Scene 預設 BPM，UI / UC4 可即時改 |
| CH1 → REAPER | 本機 loopMIDI `LiveChord_MIE_to_REAPER`，不繞 mioXL；CH2–15 才走 HST Port 3 |
| DIN2–8 回流 | 各琴 Out 一律不 route 到 HST Port 2；Fantom MIDI Thru 關閉；Phase 0 用 BYPASS 逐台驗證 |
| PANIC | 兩個 Out port 各自 CH1–16：CC120 + CC123 + CC64=0；`active_gen` 每音補 note_off vel 0；引擎進 BYPASS |
| CH16 | 保留不用（未來若控制訊號改走 mioXL 時使用） |
