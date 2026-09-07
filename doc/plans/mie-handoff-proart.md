# MIE 交接 — ProArt 16 端 AI Agent 工作提示

日期：2026-09-07　來源：RTX 5080 PC 端 session　分支：`feature/mie`　Beads epic：`LiveChord-3nex`

> 把這份文件整段貼給 ProArt 16 上的 coding agent 當第一則訊息即可。它是自足的，
> 但完整規格在 [musical-interaction-engine-plan.md](musical-interaction-engine-plan.md)，開工前先讀完。

---

## 你是誰、在哪裡

你在 **ProArt 16 筆電（RTX 5070）** 上工作。這台是演奏機：iConnectivity mioXL、Faderfox UC4、REAPER 都接在這裡。
Musical Interaction Engine（MIE）**只能**在這台跑，永遠不在 NUC（`192.168.50.6:8800`）上跑，也不 sync 到 `V:\`。

RTX 5080 PC 的 session 已經完成規劃與 Phase 0 探針程式碼，並停在 Phase Gate。你的工作是**接手 Phase 0 硬體驗證，通過後進 Phase 1**。

## 第一步：環境

```bash
git fetch origin
git checkout feature/mie
pip install python-rtmidi          # mido 1.3.3 已是既有依賴；rtmidi 是 MIDI I/O backend
python -m pytest backend/tests/test_mie_probe.py -q   # 應 15 passed，不需硬體
```

所有 MIE 工作都 commit 在 `feature/mie`，不要 merge 進 master（master 上另一條線在改 LH/RH 旋律擷取）。
每個 Phase 結束都 push。

## 已完成（不要重做）

| 檔案 | 內容 |
|---|---|
| `doc/plans/musical-interaction-engine-plan.md` | 完整規格：架構、資料結構、10 個演算法、互動圖、10 層 MIDI 安全、UI、分期。§13 是使用者已確認的接線決定 |
| `backend/mie/probe.py` | Phase 0 探針：`list` / `run [--bypass] [--delay ms] [--target ch] [--log-all]` / `panic` |
| `data/mie/ports.json` | port 子字串（`HST 2` / `HST 3` / `UC4` / `LiveChord_MIE_to_REAPER`）、echo 參數、PANIC 觸發規則 |
| `probe_mie.bat` | 啟動器 |
| `backend/tests/test_mie_probe.py` | 15 個無硬體測試，含假 port 的 echo / PANIC / loop 偵測流程 |

## 接線事實（使用者已確認，不要再問）

- Fantom 8 = mioXL port 1（In + Out），**唯一主控鍵盤**；zone 8–16 為 EXT，直接以 CH9–15 送出。
- mioXL 現況：port 1 In → DIN2–8，CH9–15 各琴自濾自己的 channel。**這條直通路徑保持不動**，引擎是並聯。
- `HST Port 1` 保留給 REAPER；`HST Port 2` = MIE In（port 1 的副本）；`HST Port 3` = MIE Out（Auracle 內 merge 到 DIN1–8）。
- UC4 直接 USB 接 PC，引擎獨立開 port 讀它，全部視為 CONTROL。
- CH1 → REAPER 走本機 loopMIDI `LiveChord_MIE_to_REAPER`，不繞 mioXL。
- CH16 空閒，保留。
- HUMAN 判定靠 **port**（來自 HST Port 2）不靠 channel；`human_ch` 隨 Fantom 當前 zone 變動。
- PANIC 對兩個 Out port 各自 CH1–16 送 CC120 + CC123 + CC64=0，再對發聲池逐音補 note_off vel 0。

## 你的第一個任務：Phase 0 硬體驗證（T0）

使用者會自己做 Auracle 路由、loopMIDI 建立、Fantom MIDI Thru 關閉。你負責跑探針並判讀。

1. `probe_mie.bat list` → 對照真實名稱修 `data/mie/ports.json`（mioXL 韌體不同，可能叫 `mioXL HST 2` 或 `mioXL Port 2`；子字串比對，大小寫不分）。
2. `probe_mie.bat bypass` → 使用者逐台彈 Nord / Wavestate / Iridium / MODX / Event 61。**畫面只能出現 Fantom 副本的事件**；任何來自 DIN2–8 的回流都是 routing 錯誤，要指出是哪個 channel。
3. `probe_mie.bat` → 彈幾個音，CH11 Iridium 應在 250 ms 後跟著響、放開就停。結束時看：
   - `T0 OK`（loops=0）— 否則列出 `!!! LOOP` 行，對照 mioXL 規則 3/4 與 Fantom Thru。
   - jitter `p95 < 5 ms`。若不達標，先確認是 WinMM 的問題還是 `_scheduler` 的 spin 門檻（`0.0015`），不要直接調大 delay 掩蓋。
   - `probe_mie.bat run --delay 0` 量原始直通延遲，記錄 p50 / p95。
4. UC4 任一按鈕 → PANIC 生效（Iridium 立刻靜音、終端印 `PANIC sent to HST` 與 `REAPER`），`r` 恢復。
5. 引擎關掉時，Fantom → DIN2–8 直通完全不受影響（並聯驗證）。

**通過標準**全部達成後，把終端輸出摘要寫進 `doc/plans/musical-interaction-engine-plan.md` §11 Phase 0 下方（含實際 port 名稱、jitter 數字、日期），commit 到 `feature/mie`，**然後停下來給使用者驗收**。不要自行進 Phase 1。

## Phase 1（使用者驗收 Phase 0 後才開始）

範圍見規格 §11 Phase 1，摘要：

- `backend/mie/`：`events.py`（`MieEvent` 信封，§2.1）、`state.py`（`MusicalState`，§2.2 + `human_chs`）、`graph.py`（`Edge` / `InteractionGraph`，§2.4）、`algos/{follow,echo,shadow,silence}.py`（§4，純函式介面 `run(ev, st, edge, rng) -> list[Proposal]`）、`probability.py`（§3 步驟 4 + §6 restraint）、`constraint.py`（和弦音 / 調內音 snap，§4 末段；音階表從 `frontend/js/scale-lab.js` 的 31 種移植到 `backend/mie/scales.py`）、`safety.py`（§7 第 1–9 層）、`scheduler.py`（從 probe 的 `_scheduler` 抽出）、`io_rtmidi.py`（從 probe 的 port 開關抽出）、`ws_server.py`（`:8810`，10 Hz 快照）、`__main__.py`。
- 設定：`data/mie/instruments.json`（§2.3 樣板）、一個 scene（§2.5）、`data/mie/control_map.json`（先只綁 PANIC + ON/OFF + `global.prob_scale`）。
- UI：`frontend/mie.html` + `js/mie.js` + `css/mie.css`，Phase 1 只做頂列 + 能量儀表 + 事件流；scene 用 JSON 編輯。遵守 `doc/UX_CONVENTION.md`。
- 測試：`backend/tests/test_mie_*.py`，FakeClock + FakeMidiOut，不需硬體。必做 T1 / T3 / T4 / T6 與安全（A→B→A 鏈在 `max_hop` 停止、每鏈 ≤ 24 事件、看門狗、PANIC 後 `active_gen` 為空）。
- 驗收：使用者在 SAFE 與 INTERACTIVE 各彈 10 分鐘，無卡音、無迴圈、PANIC 一鍵有效。

## 硬性規則（來自 repo CLAUDE.md 與規格）

- **Phase gate 強制**：每個 Phase 結束停下來報告，使用者核准才進下一 Phase。
- **不改 mioXL 直通路徑**、不在 NUC 跑、不 sync `backend/mie/` 到 `V:\`（它不是 runtime deploy surface）。
- MIDI 路徑上**不用 asyncio**；Engine thread 是唯一改 state 的執行緒。
- 任何未來需要 numpy / ML 的工作（例如 motif 相似度）丟子行程，daemon thread 仍持 GIL。
- 演算法與突變是純函式；**約束永遠在突變之後**；安全層是唯一否決點。
- 任何情況下行程結束都先 PANIC（`try/finally`）。
- 新增 i18n key 要 bump `DICT_VERSION`；改 JS/CSS 要 bump `?v=`。
- Beads：`bd update LiveChord-3nex` 記錄進度；Phase 1 開子 issue。若這台沒裝 `bd`，改在 commit message 標 `(LiveChord-3nex)`。
- 不確定就問使用者，不要猜；「Feature works」只有在硬體上真的驗過才能說。
