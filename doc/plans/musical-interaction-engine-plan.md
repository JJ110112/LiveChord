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

**⑫ 樂句回音第一次實奏（2026-09-07 19:09 / 19:10 兩趟）與四個修正**

使用者回報：「有重複，但是聲音感覺一樣大」。查 log，兩趟彈的都是同一句 G3 C4 D4 E4：

| | 19:09 | 19:10 |
|---|---|---|
| 回來的內容 | 三趟都完整，內部間隔 0 / .461 / .852 / 1.230 與原句**逐毫秒相同** | **第 2 趟少了 n55 與 n60** |
| 力度 | 60 / 44 / 31（每趟 ×0.72） | 55 / — / 28 |
| 丟棄 | 0 | **2，`reason: voices`** |
| 迴圈 | 0 | 0 |

節奏與音高是對的，「一樣大」是四件事湊出來的，全部修掉：

1. **聲部預算把第 2 趟的頭吃掉。** `instruments.json` 給 MODX M6 的 `max_voices` 是 4，那是佔位值——MODX 是 128 音複音的合成器。第一個音按了 1.87 秒，回音照著響 1.87 秒，跨進下一趟，通道音數用盡，預算就從下一趟的**開頭**砍。**缺字換一扇門又回來了。** 兩邊都改：`max_voices` 改 12，且演算法自己保證「一趟結束後下一趟才開始」（`dur ≤ period − offset`）。
2. **回音會躲開演奏者還在響的音。** 撞音迴避預設把第一個音從 n55 挪到 n57——山谷回的變成「你好媽」。`COLLISION_DEFAULT["phrase"] = "none"`：**回音就是要回同一個音高。**
3. **還按著的音沒有長度。** 收樂句的當下手指還沒離開，`rec.dur` 是 `None`，退回 `dur_min` 讓每個音變成 160 ms 的點——長音回來變成敲擊。改用「已經按了多久」。
4. **一趟是全有全無。** 原本用整句**最大**力度判斷要不要再回一趟，於是最後一趟放行後，句中較輕的音各自掉到 `min_vel` 以下——又是半句。改用**最小**力度判斷：尾巴在樂句之間結束，不在樂句中間。

同時把衰減調陡（`vel_scale` 0.72 → 0.6）並加上 `dur_decay` 0.75：**回音變遠不只變小聲，也變短**——在力度曲線平坦的音色上，耳朵聽到的其實是變短。把 19:10 那一句原封餵回修好的引擎：

| | 回來的內容 |
|---|---|
| 第 1 次 | +1.85 n55 v46 1390ms　+2.22 n60 v41 1110ms　+2.62 n62 v33 810ms　+3.03 n64 v29 510ms |
| 第 2 次 | +3.67 n55 v27 1040ms　+4.04 n60 v24 830ms　+4.44 n62 v20 610ms　+4.85 n64 v17 380ms |

音高與節奏不動，力度 46 → 27 → 停，長度 1390 → 1040 ms，丟棄 0。面板多一個「時值衰減」旋鈕（`mie.js?v=6`）。新增 4 個回歸測試（不跨趟殘響、變短、不回半句、聲部預算不再截斷），MIE 測試共 91 個。

**還沒做、可能仍需要的**：若 MODX 那個音色的力度根本不對應音量，力度衰減再陡也聽不出來。真正保險的做法是每一趟送一次 expression（CC11），與音色無關；但那要動引擎的 CC 排程與 PANIC 復原（不能讓當機把琴留在小聲狀態），所以先問過使用者再做。

**⑬ 第二次樂句實奏（2026-09-07 19:18 兩句 + 19:20 環境和弦 45 秒）**

前一輪四個修正全部生效，**兩段都沒有掉音、沒有迴圈**：

| | 19:18（兩句，各 4 音） | 19:20（環境和弦，70 音 45 秒） |
|---|---|---|
| phrase 觸發 | 2 次 | 6 次 |
| 每次回來的內容 | 兩次都完整，內部間隔 0 / .365 / .759 / 1.185 對上人類的 0 / .364 / .759 / 1.185 | 六次都完整（含 5–6 音的琶音與和弦） |
| 丟棄 | 0 | 0（前一輪是 2 筆 `voices`） |
| 迴圈 | 0 | 0 |
| jitter p95 / max | 0.80 / 1.29 ms | 0.93 / 2.60 ms |

但使用者仍覺得衰減不明顯，log 說出原因：**第一趟就已經是 0.6 了**。`vel_scale ** k` 從 k=1 起算，所以人類彈 v54，第一趟回來只有 v32，第二趟 v19，第三趟就低於 `min_vel` 停掉——整條尾巴擠在一個很窄的小聲帶裡，聽起來當然「一樣大」。

**拆成兩個數字**（`echo` 也有同樣的問題，但那邊「原音」是人類自己彈的那個音，情況不同，暫不動）：

| 參數 | 管什麼 | 何時作用 |
|---|---|---|
| `vel_scale` | 這條邊整體回多大聲（邊本來就有的增益） | 只作用一次 |
| `decay`（新） | 尾巴掉多快 | 第 2 趟起，每趟一次 |

scene 改成 `vel_scale 0.85`、`decay 0.55`、`dur_decay 0.7`、`repeats 4`、`min_vel 12`。把 log 裡三段真實樂句原封餵回去：

| 樂句 | 人類力度 | 回來 |
|---|---|---|
| 上行 55 60 62 64 | 54 53 46 40 | 第 1 趟 v46 900ms → 第 2 趟 v25 630ms → 停 |
| 下行 64 62 60 55 | 57 67 74 64 | v48 900ms → v27 630ms → v15 440ms → 停 |
| 琶音 41 53 56 60 63 | 40 42 48 45 52 | v34 900ms → v19 630ms → 停 |

動態範圍從 32→19 拉開成 48→27→15，音高與節奏一樣不動。面板的旋鈕改名為「每趟衰減」（`mie.js?v=7`）。MIE 測試 92 個。

**19:20 那段觀察到、還沒決定的一件事**：彈環境和弦時，同時按下的 3–5 個音（間隔 2–13 ms）會被當成一個「樂句」，於是 MODX 把整個和弦重複回來。目前聽起來合理，但 `min_notes` 數的是音數而不是**起音次數**，所以「一個五音和弦」和「五個音的旋律」對它是一樣的。若之後要區分，應該改成數起音群（同一個 gesture window 內算一次）。等使用者聽過再決定。

**⑭ 第三次實奏（2026-09-07 20:28，39 秒 43 音）：樂句只回答了一次**

`decay` 拆開之後衰減本身是對的——log 裡唯一那次回應是 v34 → v19（比值 0.559，設定 0.55）、時值 1504 → 1053 ms（比值 0.70），丟棄 0、迴圈 0、jitter p95 1.25 ms。但**使用者彈了八個清楚的樂句，引擎只回了一次**，而且 log 裡連一筆 phrase 的 `skip` 都沒有——它根本沒走到擲骰那一步。

原因在 log 裡看得很清楚：**引擎鎖到 179.9 BPM，一拍 = 0.33 秒，而使用者的音距也正好是 0.33 秒。** `phrase_gap_beats: 1.0` 於是變成「每一個音都結束一個樂句」，每次收到的只有一兩個音，`min_notes: 3` 一律退回，什麼都沒發生。八個樂句之間真正的空白是 2.7–7.8 秒。

**固定拍數當樂句結束的門檻是錯的。** 門檻必須同時相對於**這位演奏者當下的音距**：真正的斷句是他自己音距的好幾倍。改成兩者取大：

```
gap = max(phrase_gap_beats × beat_s,  phrase_gap_iois × 演奏者近期 IOI 中位數)
```

`phrase_gap_iois` 預設 2.2。用 20:28 的節奏重現（180 BPM 釘住、音距 0.36 秒、三個樂句各隔 3 秒）：**修正前 0 次回應，修正後 3 次全中**，這是回歸測試 `test_phrase_end_is_relative_to_how_fast_the_player_plays`。

**⑮ 衰減是線性還是對數？（使用者提問）**

是**等比**，也就是每一趟固定掉幾個 dB——正是耳朵要的對數行為，不是線性遞減。`decay` 是乘法而非減法：若某台琴的音量對應為 `振幅 ∝ (力度/127)^γ`，則每趟的振幅比固定是 `decay^γ`，換算成 dB 是**固定的一格**，與絕對音量無關。若當初寫成「每趟減 20 力度」才會是線性，尾巴的最後幾趟會塌得很突兀。

`decay` 對應的每趟 dB（γ=1 表示力度線性對應振幅，γ=2 是 GM 建議曲線，多數合成器介於兩者之間）：

| `decay` | γ=1 | γ=2 |
|---|---|---|
| 0.80 | −1.9 dB | −3.9 dB |
| 0.70 | −3.1 dB | −6.2 dB |
| 0.62 | −4.2 dB | −8.3 dB |
| **0.55（目前）** | **−5.2 dB** | **−10.4 dB** |

真實山谷回音大約每趟 −6 dB。所以在力度曲線接近平方的音色上，0.55 其實偏陡（只撐得住兩三趟），面板上把「每趟衰減」調到 0.65–0.7 會更像回音。**唯一不對數的地方是 `min_vel` 的截斷**：尾巴不是淡到聽不見，而是低於門檻就停——這是刻意的，v4 的音在硬體上是「喀」一聲而不是回音。

**⑯ 全域音量（使用者提問：能不能用 Fantom zone 1 的音量推桿控制 MIE？）**

在這之前要平衡引擎與自己的演奏，得走過七台琴一台一台調。新增 `global.master_gain`（0–1），在提議進入約束層之前乘上去，面板最上方多一條「音量」滑桿。**選擇縮放力度而不是送 CC7**：音量 CC 會把持久狀態寫進別人的合成器、PANIC 還得記得復原，而且 CC7 常常正是使用者手上在調的那一支。缺點誠實寫出來——力度在很多音色上同時牽動音色而不只是音量。

硬體推桿：`global.master_cc`（預設 7）與 `master_ch`（0 = 任何人類通道）。收到指定的 CC 就更新 `master_gain`。**只是聆聽**——同一個 CC 經由 mioXL 直通送到那個 zone 真正在彈的對象，這條路徑完全沒有被碰過（硬規則）。CH16 是 Fantom 自己的 bank/program 流量，一律不聽。

**還要確認的一件事**：Fantom zone 1 的音量推桿實際送的是哪一個 CC、哪一個通道，這在 log 裡看不到——引擎原本把 CC64 以外的控制訊息全部丟棄，連記都沒記。現在會記（每個 (ch, cc) 節流過，推桿掃一次不會洗版），事件流上是灰色的 `cc_in`。**請使用者推一次 zone 1 音量再存一份 log**，就能確定要不要改 `master_cc`。

**⑰ 20:44 那趟：全域音量推桿成功、但整段沒有任何聲音**

使用者推了最大／最小／中間，回報「沒聽到其他樂器聲音」。log 分成兩件事看：

**推桿本身是成功的。** 28 筆 `cc_in` 全是 **CC7 on CH1**（Fantom zone 1 的音量推桿就是這一支），`master_gain` 跟著走完 0.535 → 0 → 1.0 → 0.44，數值與 `val/127` 完全對得上。既然通道確定了，scene 改成 `master_ch: 1`——**釘住通道**，免得別台琴的音量推桿悄悄接管引擎的總音量。

**沒有聲音是另一件事，而且和音量無關**：`gen_sched: 0`、八條邊的 `edge_fires` 全部是 0、42 個人類音、`panics: 2`。第一筆是 **`panic reason="ws_lost"` at t=8.2**，模式在 t=9.1 從 INTERACTIVE 翻成 BYPASS，然後**整段沒有再回來**。使用者從 t=17.9 才開始彈——**全程對著一台已經停住的引擎演奏了 40 秒**。

起因是我自己造成的：我請他重新整理面板去拿 `?v=8`。刷新頁面就是關掉 WebSocket，而規格 §7-10 的規則是「面板消失 5 秒就 PANIC」（沒有面板＝操作者失去急停鍵）。規則本身是對的，但這個代價太隱形，三處都要修：

1. **等待時間拉長**：`ws_grace_s` 預設 12 秒（可在 scene 調）。硬重新整理會超過 5 秒。UC4 的 PANIC 鍵完全不需要面板，所以多等幾秒的風險很小。
2. **面板要用喊的**：灰色的 BYPASS 藥丸不夠。頂端多一條閃爍的紅色橫幅「引擎已停止 — PANIC 之後不會自己恢復（面板重新整理過也會觸發）。按 RESUME 才會再發聲」，附一個 RESUME 按鈕。
3. **log 要看得到面板進出**：`ws_server` 現在在連上／斷線時各寫一筆 `ui` 事件。**之前 log 完全沒有這條線索**——引擎知道自己為什麼停了，但那份 log 沒辦法說出面板是什麼時候不見的。診斷不到的東西就會重演。

配合這次還要記住的一條操作紀律：**改完前端請使用者重新整理之後，要提醒他確認引擎沒有掉進 BYPASS**——或者乾脆先按 RESUME 再彈。

**⑱ 20:53 那趟：三個修正同時驗證，音量推桿露出一個時間點的問題**

這趟是乾淨的：INTERACTIVE 全程、丟棄 0、迴圈 0、生成 109 音、jitter p95 0.87 ms / max 1.91 ms。三件事都得到印證：

| 驗證項 | log 證據 |
|---|---|
| **面板刷新不再殺掉 session** | 新的 `ui` 事件顯示面板在 t=5.9 與 t=21.7 各重連一次，兩次都在 12 秒寬限內，**沒有 `ws_lost`**。整段只有一筆 `exit` PANIC |
| **樂句斷句跟著演奏者** | `phrase_modx` 觸發 **3 次**（上一趟同樣長度只有 1 次），其中兩次各回 16 音——八音樂句 × 兩趟，完整 |
| **zone 1 推桿** | 45 筆 `cc_in` 全是 CC7 on CH1，`master_gain` 更新 81 次，掃過 0.0–1.0 |

但也看到一個真問題：**音量是在「排程時」讀的，不是「送出時」。** t=64.19 觸發的樂句在推桿還推滿時就把 16 個音的力度算完排進佇列，使用者在 t=66 把推桿拉到底，那些音**仍然照原力度陸續送出去**——推桿對已經排好的幾秒鐘完全無效。同一趟還有一次相反的情形：t=59.02 的觸發在 gain≈0.008 時發生，16 個音全部被靜音，`edge_fires` 記了一次卻一個音都沒出來，**而且 log 裡沒有任何一行說明為什麼**。

兩邊都修：

- **改在送出的那一刻讀音量**（`_emit`），推桿因此對「還沒響的一切」立即生效。**已經在響的音維持原力度**——這是「用力度做音量」本來就有的限制，誠實寫在程式碼註解裡。
- **被靜音的音要算數**：`stats.muted` 計數、面板統計多一格、`gen` 事件多帶 `gain` 欄位並回報**實際送出的力度**而不是演算法要的力度。上一輪剛學到「診斷不到的東西就會重演」，這裡不再犯。
- 靜音的音不會留下懸空的 note_off：沒有響過的音不需要釋放（`NotePair.muted`），回歸測試同時檢查 `active_gen` 沒有殘留。

**⑲ 外部審查的四項指控，逐條驗證（2026-09-07，對 20:53 那份 log）**

使用者拿到一份對 log 的分析，四項指控。**兩項成立（其中一項的理由是錯的）、一項是誤判、一項在 log 裡根本無法判斷。** 逐條記下來，因為「查證的方法」比結論更值得留著。

**① follow 的 resnap 把五度改掉了 —— 結論成立，但推論是錯的。**

分析說「彈 55，follow 該生成 60，卻被改成 62」。**算術就錯了**：55 + 7 = 62，60 才是被和弦吸附後的中途值，**最後送出的 62 正是那個正確的五度**。這一條如果照著它改，會把對的東西改壞。

但底下確實有問題，只是機制不同。查 `late_bind` 的撞音迴避：它比對的是**音級**（`held_pcs = {h % 12 for h in held}`），不是實際音高。20:53 那段使用者手上按著 C2 / C3 / C4，於是「C 這個音級」整個被視為擋路；`constraint: "chord"` 又只准 C 和 G，兩者一交集就**空了**，逃生路徑於是退到音階，答出 D（2 度）與 A（6 度）。log 裡的 `60→62`、`55→57`、`72→74` 全是這一條。

修法是把逃生順序改對：**先換八度、再換音級**。同一個和弦音的其他八度優先，改音級是最後手段而不是第一手段。回歸測試直接用 log 裡那個排列（手按 36/48/60，彈 55 與 67），修正前答出 `n62`、修正後只答和弦音。

**② 機率衰減過快，後半段完全不回應 —— 誤判。**

分析看到 t=53–59 一個生成音都沒有，歸因於密度上升把機率壓垮。**實際上那段的 `master_gain` 是 0.000**——正是使用者當時在測試音量推桿。那六秒有 17 次邊觸發、7 次 skip（正常機率）、**排程 0 音**，因為當時的音量是在排程時套用的，靜音的音被無聲丟掉、log 裡沒有任何一行說明。

這正是上一輪已經修掉的東西（音量改在送出時讀、新增 `stats.muted`）。20:59 的 log 直接證實修好了：`muted: 49`、`gen` 事件帶著當下的 `gain`、力度與 gain 對得上（gain 0.6 時 v22 / v8）。**機率沒有問題。**

**③ Master Gain 劇烈變動 —— 使用者確認是他自己在測推桿。**

順帶更正一個細節：分析猜是 UC4 的抖動，log 說是 **CC7 on CH1**，也就是 Fantom zone 1 的音量推桿。至於「建議加上 smoothing 以免爆音」——**這裡不需要，而且會讓推桿變鈍**。力度是在 note_on 當下決定、之後不再改變，所以音量變化只影響「還沒開始響的音」，不存在對持續音做增益掃描的 zipper noise。真要平滑，該平滑的是 CC7 直通到那個 zone 真正在彈的對象，而那條路徑我們不碰。

**④ Shadow 的 8 秒長音沒有跟著離鍵放掉 —— log 無法判斷，實測不成立。**

分析引用了兩筆 `off why="release"`（t=47.97 與 50.118）當作「樂手 47.97 放手、引擎拖到 50.118」。但**那兩筆都是引擎自己的釋放**，`human` 事件當時只記 note_on、不記 note_off——**log 根本沒有樂手放手的時間**，這個指控無從查起。實測（按住三和弦 2 秒後放開）shadow 的釋放延遲是 **0.000 秒**；`dur_ms: 8000` 是安全上限而不是實際長度。

不過這一條暴露了真正的問題：**診斷不到就等於沒有**。現在 `human_off` 事件會記錄通道、音高與按了多久，下一份 log 就能直接回答這個問題。

**⑤ 查證途中另外發現的：一個音被放開兩次。**

`_force_off` 為了保險自己送一個 note_off，而排程器也會送它剛提前的那一筆——同一個音出去兩次。多餘的 note_off 不是無害的：同通道另一個聲部若剛好按下同一個音高，會被這一筆掐斷。改成 `release(..., mark_sent=True)` 標記該對已釋放，**保留立即送出的保證、去掉重複的那一筆**。回歸測試在修正前確實抓到兩筆。

MIE 測試 99 個。

**⑳ 「配音很奇怪」的真正來源：引擎在跟自己撞音（21:08 take，549 個人類音 / 1099 個生成音）**

又一份外部分析，三項指控。這次先量再說——`human_off` 事件（上一輪才加的）讓「同時響的音」第一次可以完整重建。

**先更正三個事實錯誤：**

- 「彈 C，五度算出 G (MIDI 64)，被 resnap 壓回和弦音 C (MIDI 76)」——**MIDI 64 是 E、76 也是 E**，那是同一個音級升八度，不是被壓回 C。而且這正是上一輪的修正在運作：撞音時**換八度、不換音級**。實測 `follow` 這一趟落在和弦外的比例是 **1.5%**（134 個音裡 2 個），這條指控已經不成立。
- 「`iridium_to_wavestate` 讓聲響混濁，建議停用」——它是**所有發聲邊裡最乾淨的一條**：13 個音、2 對撞音（15.4%）。停掉它不會改善任何事。
- 「Echo 尾巴殘留舊和弦造成刺耳」——按尾巴年齡分組量測：延遲 0 秒 38.6%、1 秒 42.5%、2 秒 33.3%、3 秒 34.5%、5 秒 7.7%。**撞音比例並不隨尾巴變老而上升**，所以「舊和弦殘響」不是主因。

**量出來的真正主因**：把「同時響、相差半音或小九度」逐對數出來，撞音的**對象**是這樣分布的：

| 邊 | 撞音對數 | 對上樂手 | 同一台琴 | **別台琴** |
|---|---|---|---|---|
| echo_wavestate | 331 | 35 | 27 | **269** |
| phrase_modx | 197 | 23 | 23 | **151** |
| follow_iridium_5th | 64 | 0 | 41 | 23 |
| shadow_iridium | 61 | 12 | 41 | 8 |

**80% 的刺耳來自引擎的不同聲部彼此相撞，而不是樂手彈的東西。** 樂手自己同時彈出半音的只有 36 對（549 個音）。五條邊各自都合理，五台琴同時響時沒有人在看總和——**房間聽到的是一個聲音，MIDI 通道是聲部預算的概念，不是聲學上的概念。**

**修法**：`late_bind` 新增 minor 2nd / minor 9th 迴避，`avoid_semitone` = `ensemble`（預設）/ `instrument` / `off`。三個設計要點：

1. **絕不質疑樂手彈的東西。** 只迴避引擎自己正在響的音；樂手的 Am(maj7) 裡 G# 與 A 的半音是他的意圖，shadow 照樣要疊上去（有回歸測試守著）。
2. **先換八度、再換音級**，與上一輪同一條原則。
3. **`constraint: "free"` 的邊只准換八度。** echo 與 phrase 存在的意義就是重複那個音——**會換音高的回音就不是回音**。
4. 音高來源改用排程器的 `sounding_notes()` 而不是 `active_gen`：後者靠「已送出」通知在引擎執行緒填入，**同一瞬間排程的兩個音在那裡看不見彼此**，而這正是 follow 兩個聲部相差半音的情形。

**把 21:08 那一趟的 549 個人類音原封重放進引擎量測**：

| `avoid_semitone` | 生成音數 | 引擎內部半音／小九度重疊 |
|---|---|---|
| `off`（修正前） | 665 | **158 對** |
| `instrument` | 665 | 114 對 |
| **`ensemble`（新預設）** | 664 | **44 對（−72%）** |

生成音數幾乎沒變（665 → 664）——**聲部沒有變安靜，只是不再互相摩擦**。`instrument` 只降 28%，再次印證撞音主要跨樂器。

**還沒做、需要使用者決定的**：分析建議把 `phrase_modx` 的 `constraint` 改成 `"chord"`，說這樣「重播時會隨當前和弦自動轉調」。**這是誤解**——`chord` 是把每個音各自吸附到最近的和弦音，旋律會被拆掉，不是轉調。真正對應那個想法的是**整句依和弦根音移調**（Am → Dm 就整句 +5，音程關係完整保留）。這是新功能也是音樂取捨，等使用者決定再做。

**㉑ 樂句依和弦根音整句移調（使用者決策，2026-09-07）＋ echo 預設收斂**

使用者核准了上一則提出的方案：**整句平行移調，而不是逐音吸附到和弦音**。Am → Dm 就整句 +5，音程曲線與節奏 100% 保留，9th / 11th / 13th 這些色彩音移調後仍是對應的色彩音，不會被吸附閹割。

實作四個要點：

1. **移調發生在送出的那一刻**，不是擷取的時候——擷取時還不知道第 2、3 趟會落在哪個和弦上。樂句擷取時把當下的和弦根音（`capture_root`）與這一趟的編號（`pass_id`）釘在每個音上，一路帶到 `NotePair`，送出前才算差值。
2. **走最短路徑**（不超過三全音）：`(now - capture) % 12`，大於 6 就減 12。Am → Dm 是 +5 不是 −7，回應留在原本的音域。
3. **一趟只決定一次。** 差值由該趟最先到達送出路徑的那個音算出來、其餘音沿用（`_phrase_shift` 快取，上限 64 筆）。**和弦如果落在一趟的中間，這一趟不會被劈成兩半**——那正是這個功能唯一要保護的東西，有單元測試直接守著（同一 `pass_id` 換過和弦仍回傳 5，下一趟才變 3）。
4. **與 `avoid_semitone` 疊在一起**：整句移調後若仍與別的聲部撞半音，上一輪的 late-bind 八度位移是最後一道防線；而 phrase 是 `constraint: "free"`，只准換八度不准換音級，所以旋律identity 不會被那道防線破壞。

`follow_chord` 預設關閉、scene 的 `phrase_modx` 開啟；面板多一個「跟和弦移調」開關（`mie.js?v=11`）。實測：同一句 69 / 72 / 76 在 Am 下原音回來，Dm 進來之後那一趟回 **74 / 77 / 81**——整句 +5，內部音程 [3, 4] 一模一樣。

**echo 預設同時收斂**（使用者指示）：`repeats` 6 → **3**、`dur_decay` 0.85 → **0.7**，並且把 phrase 早先學到的那一課補回 echo：**`vel_scale` 是這條邊的增益（只作用一次）、新的 `decay` 是尾巴掉多快（第 2 趟起）**。原本 `vel_scale ** k` 讓第一次回音就已經降到 0.78，整條尾巴擠在窄帶裡。現在是 `vel_scale 0.85` + `decay 0.5`：第一次回音清楚、之後每趟掉一半、三趟結束。`decay` 未設定時回落到 `vel_scale`，舊 scene 行為不變。

MIE 測試 106 個。

**㉒ 21:30 take：新設定上線、`avoid_semitone` 實測歸零、樂句重疊的 bug**

這趟乾淨：INTERACTIVE 全程、生成 258 音、丟棄 0、靜音 0、迴圈 0、jitter p95 1.03 ms / max 1.79 ms，面板重連三次都沒觸發 `ws_lost`。echo 的新參數（`repeats 3` / `decay 0.5` / `dur_decay 0.7`）與 phrase 的 `follow_chord` 都在 log 標頭裡確認生效。

**`avoid_semitone` 用同一份演奏 A/B 重放**：撞音 **29 對 → 0 對**（生成音數 270 → 269）。上一趟量到 −72%，這一趟直接歸零。

**量測方法的更正**：我先前直接數 log 的 `sched` 事件算撞音（21:08 那份的 331 / 197 / 64…）——**那是 late-bind 之前的音高**，`sched` 記的是排程當下的值、`gen` 才是實際送出的。所以那些絕對數字偏高，只有「同一份輸入 A/B 重放」的比較是可信的。以後量撞音一律用重放，不要數 `sched`。

**resnap 42 次裡 39 次是純八度位移**（±12 / +24），三次改了音級，全部來自 `constraint` 不是 `free` 的邊——`follow` / `shadow` / `sustain` 本來就允許換音級。phrase 與 echo 的**送出音級直方圖與排程完全相同**：`{0:19, 1:4, 2:14, 3:2, 4:17, 5:20, 7:10, 8:6, 9:11, 10:2, 11:2}` 兩邊一模一樣，`keep_pc` 確實守住了回音的identity。

**和弦移調一次都沒有觸發**——12 個樂句全部在「擷取時的和弦 == 回來時的和弦」下發生，所以位移是 0。**這不是 bug，是還沒被試到**：要聽出效果，必須在彈完一句之後**換一個和弦**再等它回來。已加上 `phrase_shift` 事件（記錄位移半音數與前後根音），下一份 log 就能分辨「不需要移」與「壞掉了」——這正是這個專案一再學到的同一課。

**找到一個真的 bug：樂句的每一趟疊在同一個瞬間。** t=35.305 那次排程的 offset 是 `[0, .006, .014, .022, .031]` **出現兩次**，音高與時值完全相同——兩趟同時發聲。

原因在「不掉第一個字」那個修正裡：`shift = max(0, -start)` 是**每一趟各自**計算的。當偵測樂句結束的成本超過 `delay`（自從斷句門檻改成跟著演奏者的音距之後，慢速演奏必然如此），每一趟的 `start` 都是負的、都被夾到 0，於是全部疊在一起。

改成**整個回應只往後推一次**：`head = max(0, delay - late)`，`start = head + (k-1) * period`。第一趟仍然立刻開口、趟與趟之間的間隔完整保留。回歸測試用和弦形狀的手勢加上大的 `phrase_gap_beats` 重現，修正前兩趟同時、修正後分得開。

MIE 測試 107 個。

**㉓ 21:40 take：和弦移調在硬體上跑出來了，並露出一個音樂上的取捨**

使用者這趟只開 phrase 一條邊（其餘七條 `edge_fires` 全是 0），乾淨地把功能單獨試出來。整趟：88 個人類音、106 個生成音、丟棄 0、靜音 0、迴圈 0、jitter p95 0.86 ms / max 1.19 ms。12 次觸發裡**有一次移調**，log 完整記下了整個過程：

```
t=87.61  你彈 n57 A          和弦 = A
t=87.85  你彈 n61 C#
t=88.10  你彈 n62 D
t=89.01  樂句觸發 → 排兩趟：+0.00/+0.24/+0.48 與 +0.89/+1.12/+1.37
t=89.01  第一趟送出 A  C#  D      （和弦還是 A，位移 0）
t=89.40  你改按 Dm7
t=89.90  ***** 整句移調 +5（A -> D）*****
t=89.90  第二趟送出 D  F#  G
```

**三件事同時得到驗證**：位移就是設計裡的 A→D +5；**音程完全保留**（A→C# 是 +4、C#→D 是 +1；D→F# 是 +4、F#→G 是 +1，動機形狀一模一樣）；而且**整趟一起移動**，三個音沒有一個掉隊——「一趟只決定一次」在硬體上守住了。上一則修掉的「兩趟疊在同一瞬間」也不再出現，兩趟的排程間隔清楚分開。

**但這一次也照出平行移調的固有取捨**：原句 A–C#–D 的 C# 是 A 和弦的**大三度**，整句 +5 之後 F# 就落在 **Dm** 上——大三度疊在小和弦上，而且使用者當時手上正按著 F。log 逐音對照：

| MODX 送出 | 你按著 | |
|---|---|---|
| n62 D | D D A C D F | |
| n66 **F#** | D D A C D F | **與你的 F 差半音** |
| n67 G | D D A C D F | |

`avoid_semitone` 沒有攔它，而且是刻意的：那條規則只避開**引擎自己**正在響的音，「絕不質疑樂手彈的東西」。但這裡的情況與當初訂規則時想的不同——當初想的是「shadow 要能照樣疊上樂手自己彈的半音」，這裡卻是**引擎自己生出一個新音級去磨樂手按著的音**。兩者該不該同一條規則處理，是還沒回答的問題。

**四個可能的方向（等使用者決定，未實作）**：

| | 作法 | 保留什麼 | 失去什麼 |
|---|---|---|---|
| A | 維持純平行（現況） | 動機音程 100% | 調式（大三度會落到小和弦上） |
| B | 依音階級數移調（A→D, C#→F, D→G） | 音階級數與旋律輪廓 | 精確音程（+4+1 變成 +3+2） |
| C | 平行移調後，**只有**與樂手按著的音相撞的那幾個音吸附進和弦／音階 | 絕大部分動機 | 少數音的精確音程 |
| D | 只在和弦性質相同時移調（Am→Dm 移，A→Dm 不移） | 調式正確 | 大部分的移調機會 |

以使用者一路的優先順序（動機完整性）來看，C 是最小的改動；但這是音樂取捨不是 bug，等指示。

MIE 測試 107 個（本次無程式碼變更）。

**㉔ 使用者選 B：依音階級數移調（Diatonic Transposition）**

上一則的四個選項，使用者選 **B**，並且明確否掉 C：「C 這種平行移調後只吸附撞音音符的做法，會讓同一個 Phrase 裡出現部分音平行、部分音強制跳折的情況，旋律線會產生突兀的斷層感」。理由記在這裡，因為它是這個引擎往後所有「移調 / 改寫」決策的判準：

> 人耳辨識一個動機靠的是**起伏方向與相對級數**，不是絕對的半音音程。把大三度順修成小三度，聽覺上仍是「同一個樂句的變體」；但把大三度硬疊在小和弦上，是音型錯位。

（我當時已經先把 C 做出來了。使用者的判斷是對的：C 會在一句裡混用兩種邏輯，B 是整句一致的。C 的程式碼整個移除，不留旗標。）

**實作**：`constraint.diatonic_map(note, src_root, src_quality, dst_root, dst_quality)`。

1. **和弦決定音階**（`_CHORD_SCALES`）：大三和弦 / maj7 / 6 → Ionian；屬七 / 9 / 11 → Mixolydian；小三和弦 / m7 / m9 / m6 → Dorian；mMaj7 → 旋律小音階；m7b5 → Locrian ♮2；dim → 半全音階；`5` 這種只有根音與五度的和弦不表態，用大調。**大三度變小三度是「和弦換了、音階跟著換」的自然結果，不是特例。**
2. **級數對級數**：把音讀成來源音階的第幾級（`idx`），在目標音階取同一級。
3. **音階外的音保留變化音**（`alt`）：C 大調的 #4 移到 G 大調仍是 #4，色彩帶得過去而不是被抹平。
4. **錨點走最短路徑**（不超過三全音）：C → G 是**往下四度**而不是往上五度，回應留在原本的音域。
5. 一趟只決定一次的凍結邏輯照舊，改成凍結「目標和弦（根音, 性質）」而不是一個半音數；`phrase_shift` 事件同時記下前後的根音與性質。

**21:40 那一句的前後對照**（A 上彈 A–C#–D，Dm 進來後回來）：

| | 回來的內容 | |
|---|---|---|
| 平行移調（舊） | D **F#** G | F# 是小和弦上的大三度，與使用者按著的 F 差半音 |
| **順階移調（新）** | **D F G** | 級數不變（根音 / 三音 / 四音），三度隨和聲轉小 |

同一句在 **Am → Dm**（性質相同）時兩者輸出完全一樣（D F A）——**B 不是在所有情況都改變行為，只在和弦性質變了的時候才分岔**，這正是它該有的樣子。

把 21:40 整趟重放：`follow_chord` 關 → 引擎內部撞音 1 對；開 → **0 對**，生成音數兩邊都是 288。

MIE 測試 110 個。

**㉕ 22:06 take：順階移調在 ii–V 上的表現**

整趟乾淨：INTERACTIVE 全程、排程 100 / 送出 100、丟棄 0、靜音 0、迴圈 0、**離開時的 PANIC 顯示 0 個音還在響**（沒有卡音）、jitter p95 0.63 ms / max 1.19 ms、面板重連三次無 `ws_lost`、無任何 error 事件。引擎內部撞音 **0 對**，與演奏者之間 1 對（100 個生成音）。

這趟只開 phrase 一條邊，而且剛好彈出一個 **ii–V**，把順階移調最能說明問題的情況試出來了：

```
t=57.03-58.00  你彈 G  B  D  E        和弦 = Em7
t=59.16        樂句擷取，排兩趟
t=59.16-60.13  第一趟送出 G  B  D  E   （和弦還是 Em7，不動）
t=60.72-60.75  你改按 A  C#  E  G      和弦 = A7
t=60.79        ***** 順階移調 Em7 -> A7 *****
t=60.79-61.76  第二趟送出 C#  E  G  A
```

用級數讀就看得出它做對了什麼：

| 原句（Em7 · E Dorian） | G | B | D | E |
|---|---|---|---|---|
| 級數 | ♭3 | 5 | ♭7 | 1 |
| **回來（A7 · A Mixolydian）** | **C#** | **E** | **G** | **A** |
| 級數 | 3 | 5 | ♭7 | 1 |

級數逐一對應，而且 **C# 與 G 正好是 A7 的三音與七音**——屬七和弦的那個三全音。平行移調在這裡會給出 C D F G（全部 +5），完全踩不到新和弦的導音。**順階移調把樂句的導音送到新和弦的導音上，這正是 ii–V 該有的聲部進行。**

（`m7` → Dorian、`7` → Mixolydian 這兩條對應是 `_CHORD_SCALES` 表裡的預設值，這次是它們第一次在真實演奏上被走到。）

**仍然開著的一個問題**：t=61.63 那次擷取到的是 `A G A C# E` ——你同時按下的和弦本身。`min_notes` 數的是**音數**而不是**起音次數**，所以「一個五音和弦」與「五個音的旋律」對它是同一回事。目前聽起來合理，要區分的話應改成數起音群（同一個 gesture window 內算一次）。等使用者決定。

**㉖ 全部邊一起跑（22:14 take）：那個「不同音階的持續長音」是什麼**

使用者第一次把八條邊全開，485 個人類音、349 個生成音、丟棄 0、靜音 0、迴圈 0、jitter p95 0.65 ms。回報：**演奏中不時出現一個不同音階的持續長音，干擾彈奏**。

按音長排序馬上看到嫌犯：

| 邊 | 音數 | 中位音長 | 最長 |
|---|---|---|---|
| **silence_texture** | 6 | **24000 ms** | **24000 ms** |
| shadow_iridium | 58 | 8000 | 8000 |
| sustain_strings | 22 | 2766 | 5217 |
| phrase_modx | 144 | 334 | 1016 |
| echo_wavestate | 136 | 189 | 652 |

`silence_texture` 送到 CH1（REAPER 的 texture），`hold_s: 24`、`after_s: 4.0`、`silence_mode: "attack"`。**它照設定做對了事**：重放時最長的一次從 t=26.48 響到 41.94（15.5 秒），而那 15.5 秒裡**使用者一個新的起音都沒有**——attack 模式的定義就是「4 秒沒有新按鍵就進來，直到你再按下一個音才走」。長時間按住和弦（這趟有 86 筆踏板事件）正好落在這個定義裡。

這條邊與使用者稍早親自關掉的 `silence_pad` 是同一類東西（當時的理由是「聲音太大而且死板」，而且他用 SP-404 mk II 鋪環境音）。**所以 scene 裡把 `silence_texture` 也停用**，面板上一鍵可以再打開。

**查證途中找到兩個真的 bug（都已修，與上面那條設定問題無關）**：

1. **時間驅動的聲部有一段時間是釋放不掉的。** silence 的進場對齊小節線，所以「排程」與「發聲」之間有一秒多的窗口。使用者若在那個窗口裡彈了音，`on_human_note` 會在 `active_gen` 還是空的時候把 `fired` 清掉、找不到任何音可以釋放——**接著才響起來的那些音就再也不會被釋放，整整撐滿 24 秒橫跨每一次和弦變化**。新增 `Scheduler.release_lane()`：釋放一個聲部時，連還在佇列裡沒開始的音一起帶走。回歸測試用「在對齊窗口內彈一個音」重現，修正前那些音永遠不放。
2. **逃生時會落在同一台琴已經在響的音上。** 22:14 那趟 t=43.96，一個要閃避的 F 逃到了自己聲部已經在彈的 G#，於是同一個通道、同一個音高送出**兩次 note_on**。一個 MIDI 通道無法同時持有同一個音——第一個 note_off 會把兩個都殺掉，還白白吃掉一個聲部。`_unharsh` 現在收下「這台琴正在響的音」（`taken`）並且避開它們；不只是避開半音，連同音也避開。

MIE 測試 112 個。

**若之後想把 texture 這類聲部找回來**，正確的做法不是延長或縮短 `hold_s`，而是讓它**跟著和聲走**——進場時記下當時的和弦，和弦一離開就淡出，而不是只等使用者的下一個起音。這需要新的 lane 狀態，等使用者要的時候再做。

**㉗ texture 跟著和聲走（使用者指示）**

`silence` 新增 `follow_chord`（預設開）：聲部進場時記下當時的和聲，之後每個 tick 檢查自己是否還合得上；合不上就照 `release_beats` 淡出，不必等使用者的下一個起音。scene 的 `silence_texture` 重新啟用並打開這個開關。

**兩個實作陷阱，都是差點做出一個無效功能的那種：**

1. **不能拿 `st.chord` 當依據。** 它只在 note_on 時重算，而且刻意「和聲脈絡比放鍵活得久」。用它寫出來的規則**永遠不會觸發**——因為和弦真的變了的那一刻，一定伴隨一個起音，而起音早就依舊規則把聲部叫走了。改成**當場辨識現在還在響的音**（手指 + 踏板）。抬手指會移動和聲卻不產生任何 note_on，那正是這條規則存在的理由。
2. **改名不是離開的理由。** 只要 pad 的音仍在新和弦裡就繼續留著，否則每經過一個和弦聲部就會閃斷一次。判準是「**還合不合**」，不是「名字一不一樣」。C 與 E 在 Am 底下照樣留著；到了 Dm 才走。

離開之後聲部會在下一次靜默重新進場、依當下的和聲重新配置——**先走再回來**，而不是原地換音。

**把 22:14 那趟重放 A/B：兩邊完全一樣**（CH1 texture 8 個音、最長 15.5 秒、中位 10.3 秒）。這不是功能沒生效，而是**那一趟的和聲根本沒有移動過**：使用者踩著延音踏板長按（86 筆踏板事件），放開手指時音仍由踏板延續，「正在響的音」因此完全沒變，pad 合得上的和弦一直都在。**規則會在踏板抬起、或無踏板放鍵時作用。**

所以 22:14 那個長音的成因仍然是設定本身：`after_s 4.0` + attack 模式 + `hold_s 24`，遇上「15.5 秒沒有任何新起音」的踏板長句。若重新啟用之後仍覺得干擾，該調的是 `hold_s`（讓它換氣）或 `after_s`，而不是這條新規則。

MIE 測試 115 個。

**㉘ `hold_s` 預設改短（使用者指示）**

程式預設 20 → **8 秒**，scene 的 `silence_pad` 20 → 8、`silence_texture` 24 → 8。pad 是換氣不是持續音；`follow_chord` 只在和聲移動時把它帶走，而 22:14 那種踏板長句裡和聲根本沒動，所以還需要一個時間上限。

**22:14 重放的前後對照，以及一個要注意的副作用**：

| | 音數 | 最長 | 中位 | 總發聲時間 |
|---|---|---|---|---|
| `hold_s` 24 | 8 | 15.5 s | 10.3 s | 70.7 s |
| **`hold_s` 8** | 10 | **8.0 s** | **8.0 s** | **67.1 s** |

單一長音確實被切掉了（15.5 → 8.0 秒），**但整體佔用時間幾乎沒變**（70.7 → 67.1 秒）——因為聲部在 `after_s: 4.0` 之後就會重新進場。在一段完全沒有新起音的長句裡，現在會變成「響 8 秒、停 4 秒」的循環。好處是每次重新進場都會依當下的和聲重新配置；壞處是它變成有脈動的。

**要讓它整體更稀疏，該調的是 `after_s`（等更久才進來）或 `prob`，不是 `hold_s`。** 面板上都可以直接改。

**㉙ `after_s` 預設也拉長（使用者指示）**

先量再定，因為 `after_s` 在兩種 `silence_mode` 下的意義不同：`sound` 是「完全沒有聲音」（連踏板都算），`attack` 是「沒有新按鍵」。同一個數字在兩邊的鬆緊差很多，所以不該一視同仁。

拿 22:14 那趟（160 秒）掃 `silence_texture`（attack 模式）：

| `after_s` | 進場次數 | 音數 | 總發聲 | 佔演奏時間 |
|---|---|---|---|---|
| 4.0（原本） | 5 | 10 | 67.1 s | **42 %** |
| 6.0 | 3 | 4 | 31.4 s | 20 % |
| **8.0（新）** | 4 | 6 | 28.4 s | **18 %** |
| 12.0 | 2 | 2 | 10.0 s | 6 % |

**明顯的斷點在 4 → 6**，之後就平緩了。取 **8.0**：佔用時間降到原本的四成多一點，而且離「踏板長句」的門檻有足夠餘裕。

- 程式預設 `after_s` 2.0 → **4.0**
- `silence_pad`（`sound` 模式）2.0 → **4.0**——它要的是真正的靜默，4 秒已經是很長的等待
- `silence_texture`（`attack` 模式）4.0 → **8.0**

**這才是決定聲部「有多常出現」的旋鈕；`hold_s` 只管單一個音有多長。** 兩者一起看：`hold_s 8` 把最長音從 15.5 秒切到 8 秒，`after_s 8` 把總佔用從 42 % 降到 18 %。

**㉚ Phase 2 第五項：彈法／織度辨識（`texture.py`）**

使用者從 Phase 1 就提過的構想：讓引擎知道人**怎麼彈**，而不只是彈了什麼——持續按壓 / 琶音 / 旋律 / 打和弦，以及左手低音與右手旋律的分手。做出來之後，邊可以加 `texture: [...]`（或 `when: {texture: [...]}`）條件，只在特定彈法下作用。

`MusicalState` 新增 `texture` / `texture_conf` / `lh` / `rh`，note_on 時更新、tick 時也重新讀一次（織度是時間的函數：按住的和弦要等擊發停下來才變成「持續」）。面板頂列多一個「彈法」藥丸。

**這一項的價值全在門檻定得對不對，所以每一條規則都是先量再定，用的是使用者自己十一趟演奏的 log。** 過程中推翻了兩個想當然耳的作法：

1. **音程大小分不出琶音與旋律。** 第一版用「平均音程 ≤ 2.6 半音 = 旋律」，結果 92 % 的時間都被判成琶音。實測手內音程中位數在 5–7 半音之間，**琶音與旋律都一樣**，這個特徵根本沒有分辨力。
2. **跨手測音程更糟。** 鋼琴演奏左手低音與右手旋律交替，相鄰起音的音程是 12–24 半音，於是所有有伴奏的演奏都變成琶音。改成**每個音只跟同一半邊鍵盤上的前一個音比**。

最後定案的三個判準，都有量測支持：

| 判準 | 依據 | 實測 |
|---|---|---|
| **和弦**：一個「擊發」內同時到達的音佔比 ≥ 0.45 | 真實起音間隔是**雙峰**的——20 ms 以下一個尖峰、150–400 ms 一個寬帶，中間幾乎沒有東西 | 和弦密集的 22:06 是 0.80；自由彈的 22:14 是 0.33 |
| **持續**：還在響但每秒起音 < 1.2 | 按住不動就是持續，不必等使用者放手 | — |
| **琶音 vs 旋律**：落在和弦音上的比例 ≥ 0.75 **且** 同方向連續 ≥ 2.0 | 琶音是把和弦攤開來走，旋律會轉向、也會離開和弦音 | 樂句測試 0.83–1.00 / 2.0–3.0；自由彈 0.54 / 1.38 |

分手用**音程上最大的空隙**切，而不是固定在中央 C：低音在旋律底下會在中間留一個洞，洞在哪裡由音樂決定。而且下半部必須真的低（≤ 60）才算兩隻手，否則右手的寬voicing 會被誤判。

**用四趟有完整放鍵紀錄的演奏驗證**（更早的 log 沒有 `human_off`，織度無從判讀，不能用）：

| 演奏 | 判讀 | 分手 |
|---|---|---|
| 21:30 刻意彈樂句 + 和弦 | 持續 40 % · 和弦 34 % · 旋律 12 % · 琶音 9 % | 85 % |
| 21:40 樂句 + 換和弦 | 持續 28 % · 和弦 24 % · 琶音 21 % · 旋律 10 % | 79 % |
| 22:06 ii-V，大量和弦 | **和弦 51 %** · 持續 38 % · 琶音 6 % | 95 % |
| 22:14 全部混合，自由彈 | **旋律 43 %** · 和弦 21 % · 靜 25 % | 46 % |

和使用者當時實際在做的事情對得上：ii-V 那趟是和弦、全部混合那趟是自由的旋律演奏。

**還有一個小陷阱**：只有一個音在響時不算任何織度。第一版把它判成「持續」，於是一條設定成只在 `sustained` 作用的邊，會在旋律的**第一個音**就開口。現在資訊不足時維持前一次的判讀。

MIE 測試 121 個。**未上機驗證**（明天與使用者一起）。

**㉛ Phase 2 第六項（上半）：高階旋鈕與 Scene 存檔**

**Scene 存檔**（`graph.save_scene`，面板「儲存」鈕）。在這之前，面板上調的每一個參數都只活在記憶體裡——一整晚用耳朵找出來的衰減與音域，下次重啟就沒了，這對「只能靠聽」的工作是很差的對待。原子寫入（`.tmp` + `os.replace`），留空覆寫、輸入編號則另存新檔。序列化的是 `scene_snapshot()` 產生的**副本**而不是活的 `Edge` 物件——寫檔在引擎執行緒之外進行，而使用者還在轉旋鈕。

**厚度（DENSITY）**，`globals.density`，0.5 = 「就照邊上寫的」，所以沒設定的 scene 行為不變。它**不是第二個 `prob_scale`**：那個決定一條線多**常**開口，這個決定開口時彈多**厚**——pad 的聲部數、回音的趟數。

實作時撞到兩個東西：

1. **`st.density` 這個名字已經被用掉了**，那是「人彈得多密」的 EMA，restraint 曲線讀的就是它。直接覆蓋會**安靜地弄壞 restraint 而看起來像正常運作**。改名 `st.density_knob`，並補一個測試守著兩者不再混淆。
2. **只調 `repeats` 沒有用**。實測 22:14 那趟，厚度 0.5 / 0.75 / 1.0 生成音數都是 **651**——因為 `repeats` 是上限，衰減早在到達上限前就結束了。真正決定尾巴長短的是「一個回音可以多小聲才停」，所以旋鈕也要動 `min_vel`（`tail_floor`）。補上之後才有真正的範圍：

| 厚度 | 生成音數（同一段演奏，485 個人類音） |
|---|---|
| 0.00 | 382 |
| 0.25 | 488 |
| **0.50（等同原設定）** | **587** |
| 0.75 | 693 |
| 1.00 | 903 |

**張力（TENSION）** 早就存在（`globals.tension` → `function.extension_pcs`，只開放具名的 9/11/13 級數，不會開出隨機半音），這次只是接上面板滑桿。實測它的**作用範圍目前很窄**——只有 sustain 這條線會走到延伸音，落在和弦外的比例 76 %（張力 0）→ 90 %（張力 1），而且基準本來就高，因為 sustain 刻意避開自己已經覆蓋的音級。**要讓張力真正成為一個全域旋鈕，得讓 follow / silence 也吃延伸音**，那是另一個決定，等使用者。

**啟動時同步**：scene 存的 `density` 原本要等使用者碰滑桿才生效——scene 說一套、引擎彈另一套，是最糟的那種設定。`_sync_knobs()` 在建構與 `load_scene` 時各推一次。

MIE 測試 127 個。矩陣檢視與「介入風格預設」尚未做。**未上機驗證。**

**㉜ A / LIVE / B 三段預設（借自 Bad Mood，使用者指定先做）**

使用者讀完 Chase Bliss **Bad Mood** 手冊後選定的第一項。那台效果器把兩組儲存設定與一個 LIVE 位置放在同一個三段撥桿上，**價值不在「能存」而在「中間那一格」**：中間握著你剛才正在做的事，所以可以 A / LIVE / B 三個版本聽同一個當下。沒有它的話，比較兩組設定得「改、彈、改回來、再彈」，等彈到第二次耳朵已經忘了第一次。

**兩個設計判斷，都是這個功能好不好用的關鍵：**

1. **切換時不能把音樂切斷。** `load_scene` 會釋放所有正在響的音、清掉 lane 狀態——每切一次就剁斷一次，那就不能邊彈邊比較了。所以 preset **就地套用**（走既有的 `set_global` / `set_edge`），聲部、樂句緩衝與正在響的音原封不動。回歸測試按住一個和弦、切到 A，斷言 `active_gen` 完全沒變。
2. **preset 只改「怎麼彈」，不改「有什麼」。** 邊的存在與接線（`src` / `dst` / `algo`）不屬於 preset——它是同一套器材的另一種彈法。套用時跳過結構欄位，也跳過 scene 裡已經不存在的邊。

**LIVE 的保護**：離開 LIVE 時先把當下狀態暫存起來，切回來時原樣還原。在 A 或 B 上動任何旋鈕會標記成「已修改」（面板上是一個小圓點），存檔才會採納。

preset 隨 scene 檔一起存（`presets: {A: {...}, B: {...}}`），所以跟其他設定一樣能撐過重啟。面板右上角是 `A | LIVE | B` 加上 `＋A` `＋B`；空的槽位顯示成淡色而不是隱藏——**你應該看得出 A 是空的，再決定要不要放東西進去**。

**查證途中修掉一個潛在 bug**：`set_edge` 用 `hasattr(e, key)` 判斷要寫欄位還是寫 params，而 `lane` 是由 params 算出來的**唯讀 property**，於是寫它會直接丟 `AttributeError`。preset 套用時會把 `to_dict()` 的每一個鍵寫回去，正好踩到。改成只認 `_EDGE_FIELDS` 裡宣告過的欄位。這條路徑一直都在（面板送 `edge.X.lane` 也會炸），只是沒人走過。

在瀏覽器裡用真實引擎快照驗證：A 已存（正常顯示）、LIVE 亮起、B 空（淡色）、八條邊照常渲染、無錯誤。MIE 測試 133 個。**未上機驗證。**

**㉝ 17:13 take（2026-09-08，528 個人類音）：新功能全數上線，但移調抓到一個真問題**

整趟乾淨：INTERACTIVE 全程、生成 805 音、丟棄 0、靜音 0、迴圈 0、無 error、jitter p95 1.71 ms / max 2.59 ms。

昨天調的兩個數字看得到效果：**`silence_texture` 整趟只進場 1 次**（前一趟同樣長度是 3–6 次），那個「不同音階的持續長音」應該已經不擋路。新的 `lane_off` 事件記到 22 次聲部離開；`resnap` 111 次裡 **100 次是純八度位移**，只有 11 次改了音級（都來自 constraint 不是 free 的邊，符合設計）。織度分類器也在跑：旋律 89 / 靜 54 / 和弦 24 / 琶音 19 / 持續 11，分手偵測 46 %——與自由演奏的樣子相符。

**但五次順階移調裡有三次是假的：**

```
t= 52.15  Em7 -> Em(3)      同一個根音，只是判讀退化成雙音
t= 64.52  Dm7 -> Dm(3)      同上
t= 81.27  Dm(3) -> C        真的換了
t= 91.39  Am  -> D5         目標是空五度
t= 98.53  Am  -> A5         同一個根音，Dorian -> Ionian：小三度被改成大三度
```

追下去看和弦判讀的時間軸，原因很清楚——**判讀會在手落鍵的那十幾毫秒之間閃動**：

```
t= 90.18  和弦 = D5      （彈 n62）
t= 90.19  和弦 = Dm      （彈 n65，10 毫秒後第三音才到）
```

移調的目標是在**某一個瞬間**取樣的，而那個瞬間可能正好落在和弦還沒按滿的空檔。`5` 這種空五度對三度沒有任何主張，而音階表只能猜（表裡把 `5` 對應到 Ionian），於是 **Am → A5 會把一句小調樂句重新拼寫成大調**。

**修法：要重新拼寫一句樂句，目標和弦至少要有三個音級。** 沒有第三音就沒有調式可言，那就照原樣回來。擷取端套用同一條規則——在空五度上擷取的樂句根本不標記「跟和弦走」，因為它沒有可供轉換的來源調式。兩個回歸測試都直接用 log 裡那一刻建構（A5 → 不移調；Dm → 移調）。

**兩件觀察，沒有動：**
- **A / LIVE / B 這趟沒有被用到**（log 裡 0 筆 `preset` 事件）——可能是面板還沒重新整理到 `?v=15`。
- `lane_off` 的 22 筆 `why` 全是空字串：`sustain` 這條線的釋放沒有帶原因（只有 `silence` 會寫 `left`）。純粹是紀錄上的缺口，不影響行為。

MIE 測試 135 個。

**㉞ 17:21 take：A/LIVE/B 與 scene 存檔在硬體上跑通，滑桿露出一個新的失敗模式**

414 個人類音、生成 771 音、丟棄 0、靜音 0、迴圈 0、無 error、jitter p95 1.39 ms、INTERACTIVE 全程。

**新功能全部驗證通過：**

- `preset save A` 於 t=136.3 觸發，`saved path=test1.json` 於 t=148.7 —— 使用者按了「儲存」並輸入編號，所以是**另存新檔**而不是覆寫。檔案裡確認有 `presets.A`（八條邊全在），live 的四筆調整也寫進去了。
- **七次順階移調全部是真的**，沒有任何一個目標是 `(3)` 或 `5`：Em7→G、Am→Dm、Dm7→Fm、Bb→Ab、**Bb→Bbm**（同根音大轉小，正當的重新拼寫）、Am7→C6 ×2。**上一則的空五度防線在硬體上生效。**
- `silence_texture` 一樣只進場 1 次。

**但滑桿造成了一個新的失敗模式，而且已經讓這一趟損失了四分鐘：**

```
t=102.56  set edge.sustain_strings.high = 55      （原本是 88）
```

`sustain_strings` 的音域原本是 `low 55 / high 88`（兩個半八度）。使用者把 `high` 從 88 一路拖到 55，**正好落在 `low` 上**，音域塌成一個音。之後那條線送出 **17 個音，全部是 n55**——同一個音重複到收工。

這是我昨天把數字框改成滑桿直接造成的：**數字框要你刻意打進去，滑桿一掃就到了，而且沒有任何回饋告訴你剛剛做了什麼。** 而它現在還被存進 `test1.json` 與 preset A 裡。

**修法：音域是一對，不是兩個獨立的數字。** 兩端互推，並且維持至少 12 個半音的間距——那是一條線要把和弦攤開來所需的最小空間。實測：把 `high` 從 88 拖到 55，`low` 會跟著從 55 降到 43（間距 12）；把 `low` 拖到 80，`high` 會被推到 92。一般的移動不會干擾另一端。

（若之後真的想要單音 drone，就得放寬這條規則——那是一個音樂決定，等使用者提。）

**要提醒使用者手動改回來的值**：`test1.json` 與其中的 preset A 目前都存著 `sustain_strings.high = 55`，建議改回 88。

**㉟ 18:09 take：音域修好了，但 preset 沒有寫進檔案**

454 個人類音、生成 533 音、丟棄 0、靜音 0、迴圈 0、無 error、INTERACTIVE 全程、jitter p95 1.95 ms / max 3.04 ms（目前最高，仍在容忍範圍內）。

**音域塌陷確定修好**。同一條 `sustain_strings`：

| | 送出音數 | 音高 | 跨度 |
|---|---|---|---|
| 17:21（塌陷時） | 17 | **全部是 n55** | 0 |
| **18:09（改回 88）** | 15 | 64 / 65 / 67 / 71 / 79 / 81 / 83 / 84 | **20 半音** |

樂句觸發 7 次、移調 **0 次**——查了每一次的當下和弦（Am ×5、Csus2、C(3)），**擷取與回來時是同一個和弦，本來就不該移調**，其中 `C(3)` 還是個雙音，依上一則的規則根本不會被標記跟隨。行為正確。

**一件要提醒使用者的事**：log 裡有 `preset save A`（t=47.4），但**沒有 `saved` 事件**——這一趟沒有按「儲存」，所以 preset A 只活在記憶體裡，重啟就沒了。scene 也是從 `01` 開的而不是昨天存的 `test1`。**「＋A」只放進當下的引擎，「儲存」才落地。** 這個區別在面板上不夠明顯，之後值得在按鈕上標示。

**順手補完第三次提到的紀錄缺口**：`lane_off` 的 26 筆原因欄仍是空的，因為只有 `silence` 會寫 `left`。`sustain` 現在也會寫（`human_stopped` / `voice_budget`），並加一個測試斷言**沒有任何一次釋放是無法歸因的**——查不出原因的釋放就是查不出的 bug。

MIE 測試 136 個。

**㊱ 防呆與「退回」機制（回答使用者的提問），以及 Bad Mood 第 2 項：時間軸旋鈕**

**使用者問：UX 有沒有防呆或 reset to default？** 17:21 那次音域塌陷是最好的例子——一次拖曳讓一條線四分鐘只彈一個音，畫面上沒有任何跡象，然後還被存進檔案。分三層回答：

| 層 | 做法 | 擋住什麼 |
|---|---|---|
| 限制輸入 | 音域是一對，兩端互推、至少差 12 半音（已做） | **那一個**意外 |
| **復原** | 每個 setter 記下被覆寫的值，40 層深，Ctrl+Z 或按鈕退回 | **下一個**意外，不論它是什麼 |
| **退回檔案** | 整份或單一條邊退回 scene 檔 | 一連串調整之後不知道從哪裡走偏 |

「預設值」在這裡指的是**使用者自己上次刻意存下來的狀態**，也就是 scene 檔，而不是程式碼裡寫死的值——那才是他會想回去的地方。退回是**權威式**而不是合併：檔案裡沒有的全域設定會被移除，否則「退回」就名不副實。

preset 切換與退回會一次寫入上百個設定，**刻意不記進復原堆疊**——否則使用者最後一個真正的動作會被埋掉，按復原看起來就像壞掉。

另外把使用者連續兩次踩到的事寫進介面：**「＋A」只放進執行中的引擎，「儲存」才落地。** 儲存鈕未落地時帶黃色圓點，滑過去會說還有幾項沒寫。

**Bad Mood 第 2 項：時間軸旋鈕（`global.time`）**

那台效果器的 CLOCK「controls everything ... tone, length, and quality, all in one」，而且**走音樂性的階梯**——取樣率減半，loop 與效果時間同時減半。MIE 的對應是一顆旋鈕拉長或壓縮**全場所有的等待**：回音的延遲與時值下限、樂句的斷句門檻、shadow 的最長持續、pad 的 `after_s` / `hold_s` / `release_beats` / `retry_beats`、sustain 的 `every_bars_*` 與 `hold_beats`。全部改走 `t_beats()` / `t_secs()` 兩個共用輔助函式。

**階梯是重點，不是裝飾**：延遲減半是音樂，乘以 1.07 不是。步階為 ¼ ⅓ ½ ⅔ 1 1½ 2 3 4，用**比值的對數距離**取最近（½→1 與 1→2 是同一步，這才是耳朵聽到的關係）。Bad Mood 的 `SMOOTH` 開關對應 `time_steps: false`，關掉就是連續調整。滑桿本身連續，但顯示的是引擎**實際落到**的那一格（拖到 1.3 會顯示 1.50）。

`time` 沒設定時整條路徑回傳 1.0，**所有既有 scene 行為完全不變**，有測試守著。

用 18:09 那趟（454 個人類音）重放，只動這顆旋鈕：回音的間隔中位數 **0.5× → 0.17 秒、1.0× → 0.34 秒、2.0× → 0.39 秒**（2× 時尾巴會蓋到下一個音，中位數因此低於線性兩倍；單元測試在隔離條件下確認剛好是兩倍）。

MIE 測試 145 個。**未上機驗證。**

**㊲ 18:34 take：時間旋鈕在硬體上跑通，並抓到「沒有三度的和弦」這一類**

831 個人類音、生成 1002 音、丟棄 0、靜音 0、迴圈 0、無 error、INTERACTIVE 全程、jitter p95 1.80 ms。

**使用者自己在同一趟裡做了 A/B/C，只動時間旋鈕**（`set global.time` 三次：2.05 → 1 → 0.45，量化成 2.0 / 1.0 / 0.5，**階梯在硬體上生效**）：

| | 人類音 | 回音延遲中位 | 回音時值中位 | sustain 進場 |
|---|---|---|---|---|
| **2.0×（拉長）** | 382 | **1304 ms** | 393 ms | 5 次 |
| **0.5×（壓縮）** | 449 | **278 ms** | 122 ms | 47 次 |

延遲比 4.7 倍（理論 4 倍，差額來自兩段的實際拍速不同）。sustain 的進場次數 5 → 47，因為 `every_bars_*` 也跟著縮放——**這正是「一顆旋鈕動全部」該有的樣子**。`lane_off` 的原因欄現在全部是 `human_stopped`，上一則補的紀錄缺口確認生效。

**但三次移調裡有兩次仍然不該發生：**

```
Dm    -> E7      真的換了
Asus2 -> Am      sus 和弦沒有三度
Eaug  -> Am      增和弦那一列的音階是壞的
```

1. **sus 和弦不能決定調式。** `sus2` / `sus4` / `5` 都不說第三音是什麼，音階表只能猜——`sus2` 被對應到 Ionian，於是一句樂句會先被讀成大調再被拼寫成小調。這與空五度是同一個道理，只是多了一個音，所以上一輪的「至少三個音級」擋不住它。改成**必須明確指出三度**（`states_a_third`），三個音級的條件仍然保留（它同時擋住落鍵中途的判讀）。
2. **增和弦那一列根本不是音階。** 我原本寫 `(0,2,4,6,8,10,10)`——全音音階只有六個音，硬湊成七度就重複了一格，第 6 與第 7 度會對到同一個音。改成 Lydian augmented `(0,2,4,6,8,9,11)`，是真正的七音音階而且含有 0-4-8。新增一個測試把**整張表**掃過：七度、不重複、遞增、不出八度。

MIE 測試 147 個。

**㊳ Bad Mood 第 5 項：FREEZE（凍結）**

那台效果器長按 bypass 就把當下的聲音無限延續下去，你在上面繼續彈（Soup 變成 pad、Flip 變成重複的和弦）。MIE 的對應是：**按住引擎此刻正在響的東西**，讓演奏者在一張引擎鋪出來的床上繼續彈，而不是自己按住一個和弦然後放棄它。

**這是刻意覆蓋音長，也就是刻意覆蓋「防止音卡住」的機制**——換句話說，它正是最有可能把這個專案幾週來一直在避免的卡音送回來的功能。因此三條規則：

1. **凍結的音仍然歸看門狗管**，只是繩子放長，不是豁免；
2. **繩子是有限的**（`freeze_max_s`，預設 120 秒），走開忘了解凍它會自己結束；
3. **PANIC 位階更高**，跟所有東西一樣。

`Scheduler.hold()` 把還在響的音的釋放時間往後推、標記 `frozen`，`_bring_off_forward` 對凍結的音一律略過——**聲部自己的釋放邏輯不能推翻演奏者的「按住」**（shadow 跟著放鍵、pad 跟著和聲離開，都不行）。只有解凍與 PANIC 走 `_force_off`，而它會先 `thaw()`。

**做這個功能時翻出三個潛藏問題：**

1. **排程器死鎖。** `self.lock` 是普通的 `Lock` 而不是 `RLock`，`hold()` 在持鎖狀態下呼叫 `_push()`（也要拿同一把鎖）——**排程器執行緒與它後面的一切全部停住**。測試直接跑到逾時。改成持鎖找到 pair、放開後再 push。
2. **惰性刪除只處理了一個方向。** 原本的設計是「釋放提前時推一筆新的，舊的那筆在 pop 時因為 `off_sent` 被跳過」。但凍結是把釋放**往後**推，舊的那一筆會**先**觸發、而且完全不算過期——於是凍結的音仍然在原本的長度被切斷。加上 `item.t < p.t_off` 的檢查，兩個方向才都成立。
3. **`_apply_offs` 的保險路徑會踩過凍結。** 正常釋放被凍結擋下後回傳 0，程式就退回 `_force_off`——而它會先解凍再殺掉。加上 `is_frozen()` 檢查。

面板加一顆「凍結／解凍」鈕（凍結時亮藍），並綁 **空白鍵**——雙手通常都在琴鍵上，唯一值得伸手的控制必須是筆電上最好按的那個鍵。UC4 也有兩個動作可綁：`freeze`（切換）與 `freeze.hold`（按住才凍結）。

六個測試：按住有效、解凍放開、**自己會結束**、**PANIC 蓋過凍結**、可以只凍一條線、**只凍已經聽得見的音**（凍結佇列裡還沒出去的音等於按住一個還沒被聽見、而且送出前還會被重新吸附的音）。MIE 測試 153 個。**未上機驗證。**

**㊴ 每條邊一個 XY pad：把效果器的兩層結構搬進面板**

使用者的觀察：「使用者在面對這麼多參數選項時一定會迷航……一個效果有兩層，第一層用一個滑桿或 XY pad 就可以修改參數，想要細部修改再點擊展開。」

面板原本是**反過來的**：第一層完全沒有控制（只有開關與名字），要改任何東西都得展開，然後面對十條滑桿——八條邊讀起來就是八十個控制。Bad Mood 的解法很乾脆：每個聲道只有 TIME 與 MODIFY 兩顆旋鈕，意義隨模式改變，其餘全部藏在 Hidden Options 後面。

現在每條邊的第一列帶一個 XY pad，**橫向是它的時間感、直向是它的份量**，一次拖曳同時改兩個：

| 演算法 | X | Y |
|---|---|---|
| echo / phrase | 間隔 `delay_beats` | 尾巴 `decay` |
| shadow | 延遲 `delay_ms` | 持續 `max_hold_s` |
| silence | 等待 `after_s` | 持續 `hold_s` |
| sustain | 間隔 `every_bars_min` | 聲部 `voices` |
| follow | 音程 `interval` | 機率 `prob` |

其餘參數原封不動留在 ▾ 後面。Y 軸**向上為多**（跟推桿一致），拖曳以約 30 Hz 送出，放開時再送一次確保最後的位置有落地。快照更新時 pad 會跟著移動，但**正在拖的那一個不會被覆蓋**。

一個小地方值得記：`Math.round(x / 0.05) * 0.05` 會給出 `0.8500000000000001`，而那個值會原封不動寫進 scene 檔。改成按步階本身的精度四捨五入。

瀏覽器實測：八條邊全部產生 pad，圓點位置與各自的值相符（echo 的 1.0 拍落在 0.25–8 軸上的 9.7 %、decay 0.5 落在 0.1–0.95 軸上的 47 %、follow 的音程 7 落在 −24..24 的 64.6 %）；拖到 75 % / 90 % 送出 `delay_beats=6` 與 `decay=0.85`，標籤與圓點同步。

**㊵ 「按下退回的確定，popup 不會消失」——三個問題疊在一起**

使用者的截圖右半邊是關鍵：事件流裡幾百行一模一樣的 `{"on":true,"notes":0,"lane":"*","cap":120}`，全部同一個時間戳。對話框沒有壞，是**頁面被自己的事件洪水淹住**。

**① 空白鍵的自動重複。** 按住空白鍵，作業系統會以重複速率持續送 keydown，每一次都讀到還沒更新的按鈕狀態、再送一次凍結。加上 `e.repeat` 判斷後，一次按壓加十二次重複只送出 **1 則訊息**（原本 13 則）。

**② 凍結「沒有東西可按住」時仍然latch。** 什麼都沒在響時按凍結，引擎仍然設定 `_frozen = True`；下一個 tick 又因為「凍結但無聲不是一個狀態」把它清掉——於是每一次按壓都是一次全新的 on，洪水就是這樣來的。現在**沒有東西可按住就什麼都不做**，重複按也不是事件（已在按住時再按一次同樣是 no-op）。

**③ `confirm()` 會阻塞頁面的 JavaScript**，而被阻塞期間引擎的訊息會在後面排隊——對話框看起來卡住，是因為頁面沒辦法重繪。改成**兩段式按鈕**（按一次變成「再按一次確認」，四秒後自己取消），同樣問清楚，但不會停掉面板。

**順帶修掉一個沉默的脆弱點**：鍵盤處理器用 `e.target.matches(...)` 判斷焦點是否在輸入框，但 keydown 的 target 不一定是 Element——落在 document 上時 `document.matches` 不存在，**整個處理器會拋例外然後靜靜死掉**。改成先檢查 `instanceof Element`。

兩個回歸測試：凍結沒有東西可按住時不 latch、也不會把事件流灌爆；重複按凍結／解凍不會產生第二次事件。MIE 測試 155 個。

**㊶ 19:27 take：又一次洪水，這次找到真正的根源（是我造成的）**

log 的形狀就是證據：`set` **24,777 筆**、`revert` **117 筆全部在 t=53.6、彼此相差 1–3 毫秒**、`freeze` **331 筆全部在 t=56.96**。24,372 筆 set 落在某次 revert 之後的半秒內——**那些 set 是 revert 自己寫回兩百多個參數的紀錄**，不是使用者調的。所以真正要解釋的只有一件事：一次按壓為什麼會變成 117 次。

**根源：我把 freeze / undo / revert / keydown 四個監聽器寫進了 `renderPreset()` 裡面，而它每收到一次快照就執行一次。** 每個快照多掛四個監聽器，到 t=53 已經累積了一百多個，於是**按一次按鈕就送出一百多則訊息**。上一則我修的 `e.repeat` 與「凍結沒有東西可按住不 latch」都是對的，但它們只是症狀——真正的病在這裡。

（順帶一提，那次編輯還把 `renderPreset` 的函式主體推到監聽器後面，而且我用 heredoc 寫入時 `e.target` 與 `document.matches` 被 shell 當成指令替換吃掉了，註解裡留下兩個空洞。都一併修好。）

修法是把四個綁定移回只執行一次的 controls 區塊，並在那裡留下一行給未來的自己：**底下每一個綁定都只在載入時執行一次；把任何一個放進 render 路徑，都會每張快照再掛一份。** 實測：先讓十五次狀態更新進來（過去足以複製十五份監聽器），再按一次 revert 的兩段確認、按一次凍結、按一次空白鍵——送出 **revert 1 則、freeze 2 則**（兩個不同的動作），不再是 15 與 30。

**這一趟本身其他部分是健康的**：255 個人類音、261 個生成音、迴圈 0、靜音 0、jitter p95 1.3 ms。而且新東西確實被用到了：

- **XY pad 用在五條邊上**，每條的兩個參數次數完全相等（43/43、38/38、33/33、28/28、22/22）——那正是 pad 的簽名，一次拖曳同時送兩個參數。
- **凍結真的按住過一次**（`notes: 2, cap: 120`），其餘 330 筆是監聽器複製造成的空按。
- preset A 已存、scene 另存為 `test2.json`。

**11 個掉音，全部 `constraint`，10 個來自 phrase。** 追下去：使用者把 `phrase_modx.octave` 拖到 **−3**，音落在 MIDI 9–24，遠低於 MODX 設定的 36–96，於是在排程前就被丟掉。**引擎做對了**（音超出樂器範圍），但**畫面上完全沒有跡象**——那一列看起來跟安靜的線一模一樣。這與音域塌陷是同一類問題，所以每條邊現在會回報自己的掉音數，在列上顯示成橘色的 `⚠N`，滑過去說明多半是音域或八度把音推出樂器範圍。

MIE 測試 156 個。

**㊷ 19:40 take：洪水確定止住，但它留下的傷還在 scene 檔裡**

**監聽器修好了**：`set` 從 24,777 降到 **31 筆**、`freeze` 從 331 降到 **1 筆**、`revert` 0 筆。一次按壓就是一次動作。238 個人類音、迴圈 0、靜音 0。

但這一趟有三個異常，追下去**全部同源**：t=18.4 使用者載入了 `test2`——**那是在洪水那一趟存下來的 scene**，於是把當時被亂點出來的值一起帶了回來。

| 現象 | 存在 test2 裡的值 | 應該改回 |
|---|---|---|
| **echo 這條線整趟 0 次**（連 skip 都沒有） | `echo_wavestate.vel_scale = 0.0` | **0.85** |
| 16 個掉音，音落在 MIDI 4–23 | `phrase_modx.octave = -3` | **0** |
| 5 個掉音 | `follow_iridium_5th.interval = -21` | **7** |

`vel_scale = 0` 讓 echo 的第一個回音力度就算成 0，低於 `min_vel`，演算法直接回傳空的提議——**連「觸發」都不算，所以 log 上既沒有 fires 也沒有 skip**，看起來就像一條安靜的線。

這是第三次同一類問題了（音域塌陷、八度 −3、現在是力度 0）：**一個設定讓聲部發不出聲音，而畫面上跟「暫時沒話講」長得一模一樣。** 所以引擎現在會回報「為什麼這條啟用中的線發不出聲音」，面板把那一列標成橘框並顯示「靜音」，滑過去說明原因。目前偵測兩種：力度× 是 0、音域上下限反了。

（掉音的 ⚠N 標記上一則才加，這一趟正好第一次派上用場——16 個 phrase 掉音會直接顯示在那一列上。）

MIE 測試 157 個。

**㊸ 19:46 take：「凍結之後還有聲音持續跑出來」**

**先說好消息**：三個被 test2 帶壞的值都改回來了（echo `vel_scale` 0.85、phrase `octave` 0、follow `interval` 7），效果立刻反映在 log 上——**echo 從 0 次回到 91 次觸發、掉音從 21 個回到 0 個**。整趟迴圈 0、靜音 0、jitter p95 0.95 ms，另存為 `test01.json`。XY pad 用在三條邊上（36/36、13/13、12/12）。

**使用者的問題查證如下**（第一次凍結前後的逐筆事件）：

```
t=157.29   引擎送出 CH10 n96          （echo）
t=157.32   ***** 凍結，按住 1 個音 *****
t=158.30   引擎送出 CH10 n64          ← 凍結之後
t=160.06   引擎送出 CH10 n88          ← 凍結之後
t=161.43   解凍
```

那兩個音**不是新彈出來的回應，是凍結之前就排在佇列裡的回音尾巴**，只是還沒輪到它們出去。我當初刻意讓凍結「只按住聽得見的音」（凍結一個還沒響的音等於按住一個沒人聽過、而且送出前還會被重新吸附的音），但**沒有想到另外一半**：佇列裡那些正在路上的音，會在凍結之後一兩拍陸續落在那張床上——而那正是凍結想要停住的東西。

修法：凍結時把**還沒開始的音全部丟掉**（`drop_pending()`）。「按住這一刻」現在是完整的：聽得見的留住、在路上的取消。

**但引擎仍然會回應你之後彈的新音**，而且這是刻意的——凍結一張床就是為了在上面彈，如果連新的回應都沒有，那就只是一個 loop。回歸測試兩條都守著：舊尾巴不再落下、新演奏仍然有回應。若使用者要的是「凍結期間引擎完全安靜」，那是另一個模式（Bad Mood 的 Relay freeze 就是那樣），一句話就能加。

（另一件小事：`sustain_strings` 這趟觸發 0 次但被 skip 30 次——那是機率門檻，它的 `prob` 還停在 0.3。不是 bug。）

MIE 測試 159 個。

**㊹ 凍結期間引擎完全安靜（使用者決定）**

上一則問的兩種凍結，使用者選了**完全安靜**那一種：按住的那張床單獨響著，人在上面彈，引擎不再插話。

實作是兩個閘門：事件驅動的 `_fire_edges` 在凍結時直接返回，時間驅動的 tick 迴圈拿到空的邊清單。**看門狗、釋放、樂句緩衝這些都照常運作**——被關掉的只有「產生新的音」。解凍後立刻恢復。

回歸測試把兩邊都釘住：凍結後彈四個音、再等四秒（久到 pad 會想進來），送出的音數**完全沒有增加**、按住的那組音也一個沒變；解凍後再彈一個音，立刻又有回應。

面板：凍結時主區塊會有一圈藍框，按鈕的提示改成「按住中：引擎不再產生新的音，你在這張床上彈」。**刻意的安靜不能看起來像引擎死掉**——這個專案已經在那件事上摔過一次（`ws_lost` 進 BYPASS，使用者對著停住的引擎彈了 40 秒）。

MIE 測試 159 個。

**㊺ 19:58：引擎卡住、q 出不來、log 是 0 位元組——原因是我留下的行程**

先講清楚責任：**是我造成的。** 我為了在瀏覽器裡驗證面板，早上 09:59 起了一個假的 ws 伺服器佔用 8810，之後每次清理都只殺當下在聽的那個 PID，**那一個一直活到晚上**。

**Windows 的 `SO_REUSEADDR` 語意與 Unix 不同**：它允許第二個 socket 綁上一個**已經在 LISTEN** 的埠，而 `HTTPServer` 預設就開著它。於是使用者重啟引擎時，引擎**成功地**和我的殘留伺服器共用了 8810，作業系統把連線隨機分給其中一個。面板連到的是我那份九小時前的靜態快照，引擎則一個 client 都沒看到——12 秒後 `ws_lost` PANIC 進 BYPASS，於是「沒反應」。

**而 `q` 出不來、log 0 位元組是同一條鏈的下游**：結束流程是 `panic → ui.shutdown() → … → evlog.close()`，而 `httpd.shutdown()` 會等 `serve_forever` 回應；那條執行緒卡住時它就永遠等下去，**於是整段紀錄從來沒有被寫出來**。

三件事都修了：

1. **綁定前先探測。** 埠上已經有人在服務就**大聲拒絕啟動**，並說明是另一個 MIE 或殘留的測試伺服器。實測：佔用中的埠丟出明確錯誤、空的埠正常啟動。
2. **關閉不再會卡死。** `httpd.shutdown()` 丟到另一條執行緒並等 2 秒，逾時就留著它直接離開——**socket 收得漂不漂亮，遠不如把紀錄寫出來重要**。實測 0.5 秒完成。
3. **先關 log 再關 UI。** 結束流程改成 `panic → evlog.close() → ui.shutdown() → io.close()`。就算 UI 真的卡住，那一趟演奏的紀錄也已經在磁碟上了。

**給我自己的紀律**：測試用的伺服器一律用非 8810 的埠，而且用完立刻確認埠是空的。這次的代價是使用者的一整趟演奏。

MIE 測試 159 個。

**㊻ 引擎完全起不來：MIDI 埠列舉卡死（2026-09-08 20:07）**

`.\start_mie.bat` 沒有任何輸出、8810 上沒有東西在聽、瀏覽器拒絕連線。逐段隔離：

```
import mido        0.10s
import rtmidi      0.00s
mido.get_input_names()   ← 永遠不回來
```

**卡在 Windows 的 MIDI 埠列舉**，而引擎所有的 `print` 都排在 `io.open()` 之後，所以畫面上什麼都沒有。當下機器上有**八個 Auracle X 實例**（20:07 前後陸續開的）與一個 MidiView 監看工具，它們把 MIDI 輸入全抓著。這與程式碼無關，但**程式碼不該以「無聲卡死」來面對它**。

**第一版的修法是錯的，值得記下來**：我把列舉丟到一條有 timeout 的執行緒——**沒有用**。那個阻塞的呼叫**握著 GIL 不放**，主執行緒根本輪不到執行，`join(6.0)` 永遠不會回來。**執行緒的 timeout 擋不住不放 GIL 的 C 呼叫，只有另一個行程可以被放棄。**

改成 **subprocess 探測**：用 `python -c` 列舉並帶 6 秒 timeout，逾時就丟出說明——是哪一類程式抓住了埠、依序該試什麼（關掉那些程式 → 重插 USB → 最後才重開機）。`--list-ports` 也走同一條路徑：**引擎起不來時第一個會去敲的就是它，它更不能一起卡住**。

在當下那台仍然卡住的機器上實測：6 秒內回報並離開（exit 1），不再是無限等待。

MIE 測試 159 個。

**㊼ 20:33 take：「phrase_modx 好像沒動作」**

先確認**它有動作**：整趟觸發 3 次、在 CH12 送出 32 個音，內容也是對的（八音的手勢完整回來兩趟、力度遞減）。整趟 166 個人類音、丟棄 0、靜音 0、迴圈 0、jitter p95 1.86 ms。所以問題是「**為什麼這麼少**」，答案有三層：

**① TIME 旋鈕把偵測門檻也一起拉長了——這是我的錯。** 那趟 224 個快照裡有 195 個的 `time_knob` 是 **3.0**，而我把 `phrase_gap_beats` 也接到了 `t_beats()`。**TIME 旋鈕動的其他東西都是「引擎要等多久」**（回音延遲、pad 進場前的靜默）——拉長它們正是這顆旋鈕的用途。**但樂句結束的門檻不是等待，是對「演奏者自己的演奏」的偵測門檻**，乘上去只會讓引擎變聾：3× 之下它要 **1.22 秒**的靜默才認為一句結束。已經把它從 TIME 的作用範圍拿掉，並加測試守著「TIME 仍然拉長延遲，但不動偵測門檻」。

**② 主因其實是演奏本身連續。** 修正後門檻回到 **0.80 秒**（`phrase_gap_iois 2.2 ×` 你的音距中位數 0.364 秒），而整趟只有 **14 次**停頓夠長；扣掉「答過的那一句要等新音才會再算一次」，實際機會大約四次。重放同一段演奏：TIME 3× → 3 次，TIME 1× → 4 次。**所以 TIME 是幫兇，不是主犯。**

| `phrase_gap_iois` | 門檻 | 這趟夠長的停頓次數 |
|---|---|---|
| **2.2（目前）** | 0.80 s | 14 |
| 1.5 | 0.55 s | 17 |
| 1.2 | 0.44 s | 24 |

**③ 送出的力度很輕**：v16–v47，第一趟大多在二三十。人類音大約 70–90，而同時還有 shadow / echo / follow 在響——**很可能是被蓋掉而不是沒出來**。

給使用者的兩個旋鈕：`phrase_gap_iois` 調到 1.5 會讓它明顯更常開口（門檻 0.55 秒）；覺得聽不到就把 phrase 的「力度×」往上帶。兩個都在面板上。

MIE 測試 160 個。

**㊽ `phrase_gap_iois` 預設 2.2 → 1.5（使用者指定）**

改的是**程式預設值**而不是 scene 檔：使用者的四個 scene（01 / test1 / test01 / test2）都沒有寫這個欄位，所以動預設值就一次涵蓋全部，也不必去改他自己存下來的檔案。同時把這顆旋鈕放上面板的 phrase 參數區——**這種要靠耳朵找的門檻，不該只能改 JSON**。

用 20:33 那趟（166 個人類音）重放對照：

| `phrase_gap_iois` | TIME 1× | TIME 3× |
|---|---|---|
| 2.2（舊） | 4 次 / 42 音 | 3 次 / 32 音 |
| **1.5（新）** | **7 次 / 58 音** | **7 次 / 58 音** |
| 1.2 | 6 次 / 54 音 | 7 次 / 62 音 |

觸發次數多了將近八成。1.2 沒有更好（1× 反而少一次），所以 1.5 是對的落點。

順帶一提：1.5 之下 **TIME 旋鈕已經完全不影響觸發次數**（兩邊都是 7 次）——因為門檻由「相對於演奏者音距」那一項主導了，正是上一則修正想要的結果。

MIE 測試 160 個。

**㊾ 21:00 take：`phrase_gap_iois 1.5` 生效，但移調反而一次都沒發生**

**門檻改對了**：整趟 416 秒、877 個人類音（目前最長的一趟），phrase 觸發 **36 次 = 每分鐘 5.2 次**，上一趟是每分鐘 1.1 次——**快了四倍多**。丟棄 0、靜音 0、迴圈 0、jitter p95 1.92 ms。preset A 存了六次、scene 也寫回 test01 六次。

**但 `phrase_shift` 是 0**，而 `follow_chord` 明明是開著的。查下去：36 次擷取裡有 25 次當下是可用的和弦，17 次在兩秒後和弦**確實不一樣了**，卻一次都沒移調——因為那些「不一樣」多半長這樣：

```
Am  -> Am(3)        Dm -> E(3)        Fm7 -> Em(3)
```

**都是雙音。** ㊳ 為了擋掉「落鍵中途的空五度亂改調式」，我加了「目標和弦至少三個音級」的條件；現在同一個瞬間取樣反過來咬人——**樂句回來的那一刻剛好讀到片段，於是每一次真正的和弦變化都被擋掉了**。一個保護擋住了它本來要服務的功能。

根本問題是**「取某一瞬間的判讀」本身就不對**。改成在 `MusicalState` 記一個 `chord_solid`：**最後一次「有講出三度」的判讀**。擷取端與回放端都改讀它。這同時解決兩個方向——落鍵中途的片段既不會造成誤判，也不會擋掉真正的變化——而且保護從「使用時檢查」變成**結構上的保證**：`chord_solid` 依定義就不可能是片段。

**A/B**：實機那趟 36 次觸發 / **0 次移調**；同一段演奏重放，改後 44 次觸發 / **15 次移調**。

順帶清掉一個結構問題：`states_a_third` 原本住在 `constraint.py`，而 `constraint` 反過來 import `state`——state 要用它就會循環。搬到 `harmony.py`，和弦品質本來就是它的職責。

**測試裡也修了一個會說謊的地方**：六個測試直接寫 `eng.st.chord = recognize(...)`，繞過了 `set_chord()`。**那是引擎永遠不可能進入的狀態**，測試不該從那扇門進去。改成一律走 `set_chord()`。

MIE 測試 160 個。

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
