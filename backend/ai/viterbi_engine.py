"""
Viterbi Engine — 統一 Viterbi 解碼框架
Phase 11a, P0 地基工程

三個場景共用同一套 log-space Viterbi + beam pruning 演算法，
差異只在 transition_fn 和 emission_fn 的注入。

場景:
  1. 和弦路徑解碼 (HMM chord decoding)
  2. 指法序列生成 (fingering generation)
  3. 伴奏指法推導 (accompaniment fingering)

學術依據:
  Rabiner, L.R. (1989). "A Tutorial on Hidden Markov Models and Selected
  Applications in Speech Recognition." Proceedings of the IEEE, 77(2).
"""

import math
from typing import (
    Any, Callable, Dict, Generic, List, Optional, Sequence, Tuple, TypeVar,
)

S = TypeVar("S")   # state type
O = TypeVar("O")   # observation type


# ==============================================================================
# 壹、通用 Viterbi 引擎
# ==============================================================================

class ViterbiEngine(Generic[S, O]):
    """
    通用 Viterbi 解碼器（log-space + beam pruning）。

    使用者只需提供:
      - states:         所有可能狀態
      - transition_fn:  (prev_state, curr_state, context) -> log_prob
      - emission_fn:    (state, observation, context) -> log_prob
      - initial_fn:     (state, first_obs, context) -> log_prob  (可選)

    context 是可選的字典，讓使用者在不同場景注入額外資訊
    （例如 BPM、手別、黑白鍵等）。
    """

    def __init__(
        self,
        states: Sequence[S],
        transition_fn: Callable[[S, S, Optional[Dict]], float],
        emission_fn:   Callable[[S, O, Optional[Dict]], float],
        initial_fn:    Optional[Callable[[S, O, Optional[Dict]], float]] = None,
        beam_width:    Optional[int] = None,
    ):
        self.states = list(states)
        self.transition_fn = transition_fn
        self.emission_fn = emission_fn
        self.initial_fn = initial_fn
        self.beam_width = beam_width

    # ------------------------------------------------------------------
    # 核心: 1-best 解碼
    # ------------------------------------------------------------------
    def decode(
        self,
        observations: Sequence[O],
        context: Optional[Dict] = None,
    ) -> Tuple[List[S], float]:
        """
        標準 Viterbi 解碼。

        Returns:
            (best_path, path_score)
            path_score 是 log-probability (最大化目標) 或
            negative cost (最小化場景，由 fn 自行取負號)。
        """
        if not observations or not self.states:
            return [], float("-inf")

        T = len(observations)
        K = self.beam_width or len(self.states)

        # dp[t] = {state: (score, prev_state)}
        dp: List[Dict[S, Tuple[float, Optional[S]]]] = [{} for _ in range(T)]

        # ---- t=0 初始化 ----
        obs0 = observations[0]
        scores = []
        for s in self.states:
            if self.initial_fn is not None:
                sc = self.initial_fn(s, obs0, context)
            else:
                # 均勻初始 + emission
                sc = -math.log(len(self.states)) + self.emission_fn(s, obs0, context)
            dp[0][s] = (sc, None)
            scores.append((sc, s))

        # beam prune t=0
        dp[0] = self._prune(dp[0], scores, K)

        # ---- t=1..T-1 遞推 ----
        for t in range(1, T):
            obs = observations[t]
            candidates = []

            for s in self.states:
                emit = self.emission_fn(s, obs, context)
                best_sc = float("-inf")
                best_prev: Optional[S] = None

                for prev_s, (prev_sc, _) in dp[t - 1].items():
                    trans = self.transition_fn(prev_s, s, context)
                    total = prev_sc + trans + emit
                    if total > best_sc:
                        best_sc = total
                        best_prev = prev_s

                if best_prev is not None:
                    dp[t][s] = (best_sc, best_prev)
                    candidates.append((best_sc, s))

            dp[t] = self._prune(dp[t], candidates, K)

        # ---- 回溯 ----
        if not dp[T - 1]:
            return [], float("-inf")

        best_state = max(dp[T - 1], key=lambda s: dp[T - 1][s][0])
        best_score = dp[T - 1][best_state][0]

        path = [best_state]
        for t in range(T - 1, 0, -1):
            _, prev = dp[t][path[-1]]
            if prev is None:
                break
            path.append(prev)
        path.reverse()

        return path, best_score

    # ------------------------------------------------------------------
    # N-best 解碼（供 QA Battle 選擇最佳路徑）
    # ------------------------------------------------------------------
    def decode_nbest(
        self,
        observations: Sequence[O],
        n: int = 5,
        context: Optional[Dict] = None,
    ) -> List[Tuple[List[S], float]]:
        """
        N-best 路徑解碼。

        使用 list-Viterbi 策略：保留 top-N 路徑到每個時步。
        """
        if not observations or not self.states:
            return []

        T = len(observations)
        K = max(self.beam_width or len(self.states), n * 2)

        # dp[t] = {state: [(score, prev_state, path_id), ...]}  最多保留 n 條
        dp: List[Dict[S, List[Tuple[float, Optional[S], int]]]] = [
            {} for _ in range(T)
        ]

        # ---- t=0 ----
        obs0 = observations[0]
        for s in self.states:
            if self.initial_fn is not None:
                sc = self.initial_fn(s, obs0, context)
            else:
                sc = -math.log(len(self.states)) + self.emission_fn(s, obs0, context)
            dp[0][s] = [(sc, None, 0)]

        # ---- t=1..T-1 ----
        for t in range(1, T):
            obs = observations[t]
            step: Dict[S, List[Tuple[float, Optional[S], int]]] = {}

            for s in self.states:
                emit = self.emission_fn(s, obs, context)
                all_paths: List[Tuple[float, Optional[S], int]] = []

                for prev_s, entries in dp[t - 1].items():
                    trans = self.transition_fn(prev_s, s, context)
                    for prev_sc, _, pid in entries:
                        all_paths.append((prev_sc + trans + emit, prev_s, pid))

                if all_paths:
                    all_paths.sort(reverse=True)
                    step[s] = all_paths[:n]

            # beam prune: keep top-K states by their best score
            if len(step) > K:
                ranked = sorted(
                    step.items(), key=lambda kv: kv[1][0][0], reverse=True
                )
                step = dict(ranked[:K])

            dp[t] = step

        # ---- 回溯 N-best ----
        # Collect all final (state, path_entry) and pick top-n
        finals: List[Tuple[float, S, int]] = []
        for s, entries in dp[T - 1].items():
            for sc, _, pid in entries:
                finals.append((sc, s, pid))
        finals.sort(reverse=True)

        results: List[Tuple[List[S], float]] = []
        seen_paths: set = set()

        for score, end_state, _ in finals[:n * 3]:
            path = self._backtrace_nbest(dp, T, end_state, score)
            path_key = tuple(path)
            if path_key not in seen_paths:
                seen_paths.add(path_key)
                results.append((path, score))
                if len(results) >= n:
                    break

        return results

    # ------------------------------------------------------------------
    # 內部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _prune(
        dp_step: Dict[S, Tuple[float, Optional[S]]],
        candidates: List[Tuple[float, S]],
        K: int,
    ) -> Dict[S, Tuple[float, Optional[S]]]:
        if len(candidates) <= K:
            return dp_step
        candidates.sort(reverse=True)
        keep = {s for _, s in candidates[:K]}
        return {s: v for s, v in dp_step.items() if s in keep}

    @staticmethod
    def _backtrace_nbest(dp, T, end_state, target_score):
        """Simple greedy backtrace for n-best (approximate)."""
        path = [end_state]
        curr_state = end_state
        for t in range(T - 1, 0, -1):
            entries = dp[t].get(curr_state, [])
            if not entries:
                break
            # Pick the entry closest to the target accumulated score
            best_prev = entries[0][1]
            if best_prev is None:
                break
            path.append(best_prev)
            curr_state = best_prev
        path.reverse()
        return path


# ==============================================================================
# 貳、成本模式適配器 (Cost-Mode Adapter)
# ==============================================================================
# 指法場景用「最小化 cost」而非「最大化 log-prob」。
# 此適配器將 cost function 轉換為 negative log-prob，使 ViterbiEngine
# 的 maximize 語義自動變成 minimize cost。

class CostModeAdapter:
    """
    將 cost function 包裝成 ViterbiEngine 所需的 log-prob function。

    cost_fn(prev_state, curr_state, context) -> cost (越低越好)
    => 轉換為 -cost (越高越好，符合 Viterbi maximize 語義)
    """

    @staticmethod
    def wrap_transition(cost_fn):
        """transition cost -> negative log-prob"""
        def wrapped(prev_s, curr_s, ctx):
            return -cost_fn(prev_s, curr_s, ctx)
        return wrapped

    @staticmethod
    def wrap_emission(cost_fn):
        """emission cost -> negative log-prob"""
        def wrapped(s, obs, ctx):
            return -cost_fn(s, obs, ctx)
        return wrapped

    @staticmethod
    def zero_emission(s, obs, ctx):
        """不用 emission（全權交給 transition cost）時的零函數"""
        return 0.0

    @staticmethod
    def zero_initial(s, obs, ctx):
        """所有初始狀態等機率"""
        return 0.0


# ==============================================================================
# 參、和弦解碼場景 (Chord Decoding Adapter)
# ==============================================================================

def build_chord_decoder(transition_probs, emission_matrix, states, beam_width=5):
    """
    從 Markov 轉移機率 + EmissionMatrix 建立和弦解碼用的 ViterbiEngine。

    Args:
        transition_probs: dict[str, dict[str, float]] — P(next|current)
        emission_matrix: EmissionMatrix instance (from hmm.py)
        states: list[str] — 所有和弦 degree 字串
        beam_width: top-K 剪枝寬度

    Returns:
        ViterbiEngine[str, int] — state=chord_degree, observation=midi
    """
    def _trans_fn(prev_s, curr_s, ctx):
        prob = transition_probs.get(prev_s, {}).get(curr_s, 1e-6)
        return math.log(prob)

    def _emit_fn(s, midi, ctx):
        return emission_matrix.log_probability(s, midi)

    return ViterbiEngine(
        states=states,
        transition_fn=_trans_fn,
        emission_fn=_emit_fn,
        beam_width=beam_width,
    )


# ==============================================================================
# 肆、指法生成場景 (Fingering Adapter)
# ==============================================================================

# 手指 1-5 作為狀態
FINGER_STATES = [1, 2, 3, 4, 5]

# 黑鍵 pitch class set (C#, D#, F#, G#, A#)
_BLACK_KEY_PC = {1, 3, 6, 8, 10}


def is_black_key(midi: int) -> bool:
    return (midi % 12) in _BLACK_KEY_PC


# -- 手指長度比例 (Parncutt et al. 1997) --
FINGER_LENGTH = {1: 0.72, 2: 1.00, 3: 1.05, 4: 0.95, 5: 0.78}

# -- 相鄰手指最大舒適跨距 (半音數) --
MAX_COMFORTABLE_SPAN = {
    (1, 2): 8, (2, 3): 4, (3, 4): 3, (4, 5): 4,
    (2, 1): 8, (3, 2): 4, (4, 3): 3, (5, 4): 4,
    # 非相鄰跨距
    (1, 3): 10, (3, 1): 10,
    (1, 4): 12, (4, 1): 12,
    (1, 5): 13, (5, 1): 13,
    (2, 4): 6, (4, 2): 6,
    (2, 5): 8, (5, 2): 8,
    (3, 5): 6, (5, 3): 6,
}

# -- 黑鍵手指適性成本 (拇指按黑鍵最困難) --
BLACK_KEY_FINGER_COST = {1: 3.0, 2: 0.5, 3: 0.0, 4: 0.0, 5: 1.0}


def fingering_transition_cost(f_prev: int, f_curr: int, ctx: Optional[Dict]) -> float:
    """
    手指轉移成本函數 (Parncutt-inspired ergonomic model)。

    ctx 預期包含:
      - "delta_p":   音高差 (半音, int)
      - "hand":      "left" | "right"
      - "curr_midi": 當前音符的 MIDI number
      - "prev_midi": 前一個音符的 MIDI number
      - "bpm":       (可選) 當前 BPM
    """
    ctx = ctx or {}
    delta_p = ctx.get("delta_p", 0)
    hand = ctx.get("hand", "right")
    curr_midi = ctx.get("curr_midi", 60)
    bpm = ctx.get("bpm", 100)

    # -- 同音 --
    if delta_p == 0:
        return 0.0 if f_prev == f_curr else 1.0

    cost = 0.0

    # -- 同指跳躍：極度懲罰 --
    if f_prev == f_curr:
        return 10.0 + abs(delta_p)

    # -- 方向邏輯 --
    # 右手自然方向: 音往上(dp>0) ↔ 指編號往大(fp_diff>0)
    # 左手自然方向: 音往上(dp>0) ↔ 指編號往小(fp_diff<0)
    #   因為左手 5(小指)=最低音位, 1(拇指)=最高音位
    fp_diff = f_curr - f_prev

    if hand == "right":
        same_direction = (delta_p > 0 and fp_diff > 0) or (delta_p < 0 and fp_diff < 0)
        opposite_direction = (delta_p > 0 and fp_diff < 0) or (delta_p < 0 and fp_diff > 0)
    else:
        # 左手: 自然方向相反
        same_direction = (delta_p > 0 and fp_diff < 0) or (delta_p < 0 and fp_diff > 0)
        opposite_direction = (delta_p > 0 and fp_diff > 0) or (delta_p < 0 and fp_diff < 0)

    if same_direction:
        # -- 正常順向移動 --
        span_key = (min(f_prev, f_curr), max(f_prev, f_curr))
        max_span = MAX_COMFORTABLE_SPAN.get(span_key, 8)
        if abs(delta_p) > max_span:
            overshoot = abs(delta_p) - max_span
            cost = overshoot ** 1.5
        else:
            expected = abs(fp_diff) * 2.5
            cost = abs(abs(delta_p) - expected) * 0.4

    elif opposite_direction:
        # -- 逆向: 穿指/交叉 --
        # 對右手: 音上+指小 → thumb under 要 f_curr==1
        # 對左手: 音上+指大 → thumb under 要 f_curr==1 (拇指是高音端)
        thumb_under = (delta_p > 0 and f_curr == 1) if hand == "right" else (delta_p > 0 and f_curr == 1)
        # 右手: 音下+從拇指跨出  左手: 音下+從拇指跨出
        cross_over = (delta_p < 0 and f_prev == 1) if hand == "right" else (delta_p < 0 and f_prev == 1)

        if thumb_under:
            cost = 2.5
            if f_prev == 5:
                cost = 15.0
        elif cross_over:
            cost = 2.5
        else:
            cost = 20.0
    else:
        cost = 5.0

    # -- 黑鍵成本 --
    if is_black_key(curr_midi):
        cost += BLACK_KEY_FINGER_COST.get(f_curr, 0.0)

    # -- 速度因子 (BPM-aware) --
    tempo_factor = 1.0 + max(0.0, (bpm - 100)) / 100.0
    cost *= tempo_factor

    return cost


def fingering_emission_cost(finger: int, midi: int, ctx: Optional[Dict]) -> float:
    """指法 emission cost: 特定手指按特定音的基礎適配度。"""
    cost = 0.0
    # 黑鍵手指偏好
    if is_black_key(midi):
        cost += BLACK_KEY_FINGER_COST.get(finger, 0.0) * 0.3
    return cost


# -- 起始偏好: 右手偏好拇指(1)開始, 左手偏好小指(5)開始 --
_INITIAL_COST = {
    "right": {1: 0.0, 2: 1.0, 3: 2.0, 4: 3.0, 5: 3.0},
    "left":  {1: 3.0, 2: 3.0, 3: 2.0, 4: 1.0, 5: 0.0},
}


def build_fingering_engine(hand: str = "right", bpm: int = 100, beam_width: Optional[int] = None):
    """
    建立指法生成用的 ViterbiEngine。

    States: [1, 2, 3, 4, 5] (手指編號)
    Observations: MIDI note numbers

    使用 CostModeAdapter 將 cost 轉為 Viterbi maximize 語義。
    """
    def _trans_fn(f_prev, f_curr, ctx):
        return -fingering_transition_cost(f_prev, f_curr, ctx)

    def _emit_fn(finger, midi, ctx):
        return -fingering_emission_cost(finger, midi, ctx)

    def _init_fn(finger, midi, ctx):
        h = (ctx or {}).get("hand", hand)
        init_cost = _INITIAL_COST.get(h, _INITIAL_COST["right"]).get(finger, 2.0)
        return -(init_cost + fingering_emission_cost(finger, midi, ctx))

    return ViterbiEngine(
        states=FINGER_STATES,
        transition_fn=_trans_fn,
        emission_fn=_emit_fn,
        initial_fn=_init_fn,
        beam_width=beam_width,
    )


def generate_fingering(
    pitches: List[int],
    hand: str = "right",
    bpm: int = 100,
) -> List[int]:
    """
    生成最優指法序列（統一 ViterbiEngine 版）。

    這是給外部呼叫的便利函數，取代原本的:
      - fingering_model.generate_fingers()
      - accompaniment_generator.viterbi_fingering()

    Args:
        pitches: MIDI note 序列 [60, 64, 67, ...]
        hand: "left" | "right"
        bpm: 曲速 (影響快速段的成本權重)

    Returns:
        finger 序列 [1, 3, 5, ...]
    """
    if not pitches:
        return []
    if len(pitches) == 1:
        return [3]  # 中指起始（最自然）

    engine = build_fingering_engine(hand=hand, bpm=bpm)

    # 建立帶 context 的觀測序列
    # ViterbiEngine 的 transition_fn 需要 delta_p 等資訊，
    # 但標準 Viterbi 的 transition 只看 (prev_state, curr_state)，
    # 我們透過 context 傳遞每步的 observation-dependent 資訊。
    # 這需要特殊處理: 在每步更新 context。

    # 為此，我們直接用 low-level decode 而非通用 decode，
    # 因為指法場景的 transition 依賴 observation (delta_p)。
    return _fingering_decode_with_context(engine, pitches, hand, bpm)


def _fingering_decode_with_context(
    engine: ViterbiEngine,
    pitches: List[int],
    hand: str,
    bpm: int,
) -> List[int]:
    """
    指法專用解碼：transition 依賴當前 observation (delta_p)，
    因此每步動態更新 context。
    """
    T = len(pitches)
    states = engine.states
    K = engine.beam_width or len(states)

    # dp[t] = {finger: (score, prev_finger)}
    dp: List[Dict[int, Tuple[float, Optional[int]]]] = [{} for _ in range(T)]

    # t=0: 初始化
    ctx0 = {"hand": hand, "bpm": bpm, "curr_midi": pitches[0], "delta_p": 0}
    scores = []
    for f in states:
        sc = engine.initial_fn(f, pitches[0], ctx0) if engine.initial_fn else 0.0
        dp[0][f] = (sc, None)
        scores.append((sc, f))

    if K < len(states):
        dp[0] = ViterbiEngine._prune(dp[0], scores, K)

    # t=1..T-1
    for t in range(1, T):
        delta_p = pitches[t] - pitches[t - 1]
        ctx = {
            "hand": hand,
            "bpm": bpm,
            "delta_p": delta_p,
            "curr_midi": pitches[t],
            "prev_midi": pitches[t - 1],
        }
        candidates = []

        for f_curr in states:
            emit = engine.emission_fn(f_curr, pitches[t], ctx)
            best_sc = float("-inf")
            best_prev = None

            for f_prev, (prev_sc, _) in dp[t - 1].items():
                trans = engine.transition_fn(f_prev, f_curr, ctx)
                total = prev_sc + trans + emit
                if total > best_sc:
                    best_sc = total
                    best_prev = f_prev

            if best_prev is not None:
                dp[t][f_curr] = (best_sc, best_prev)
                candidates.append((best_sc, f_curr))

        if K < len(states):
            dp[t] = ViterbiEngine._prune(dp[t], candidates, K)

    # 回溯
    if not dp[T - 1]:
        return [3] * T  # fallback

    best_f = max(dp[T - 1], key=lambda f: dp[T - 1][f][0])
    path = [best_f]
    for t in range(T - 1, 0, -1):
        _, prev = dp[t][path[-1]]
        if prev is None:
            break
        path.append(prev)
    path.reverse()

    return path


# ==============================================================================
# 伍、測試
# ==============================================================================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    def midi_to_name(m):
        return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"

    print("=" * 60)
    print("Viterbi Engine 統一框架測試")
    print("=" * 60)

    # -- Test 1: 指法生成 --
    print("\n--- 1. 指法生成 (右手 C 大調音階上行) ---")
    scale = [60, 62, 64, 65, 67, 69, 71, 72]  # C4→C5
    fingers = generate_fingering(scale, hand="right", bpm=100)
    for m, f in zip(scale, fingers):
        bw = "B" if is_black_key(m) else "W"
        print(f"  {midi_to_name(m):4s} [{bw}] → 指 {f}")

    print("\n--- 2. 指法生成 (右手 C 大調音階下行) ---")
    scale_down = list(reversed(scale))
    fingers_down = generate_fingering(scale_down, hand="right", bpm=100)
    for m, f in zip(scale_down, fingers_down):
        print(f"  {midi_to_name(m):4s} → 指 {f}")

    print("\n--- 3. 指法生成 (左手 C 大調音階上行) ---")
    lh_scale = [48, 50, 52, 53, 55, 57, 59, 60]  # C3→C4
    fingers_lh = generate_fingering(lh_scale, hand="left", bpm=100)
    for m, f in zip(lh_scale, fingers_lh):
        print(f"  {midi_to_name(m):4s} → 指 {f}")

    print("\n--- 4. 指法生成 (右手琶音 C-E-G-C) ---")
    arp = [60, 64, 67, 72]
    fingers_arp = generate_fingering(arp, hand="right", bpm=100)
    for m, f in zip(arp, fingers_arp):
        print(f"  {midi_to_name(m):4s} → 指 {f}")

    print("\n--- 5. 指法生成 (快速段 BPM=160) ---")
    fast = [60, 62, 64, 65, 67, 69, 71, 72]
    fingers_fast = generate_fingering(fast, hand="right", bpm=160)
    print(f"  BPM 100: {generate_fingering(fast, hand='right', bpm=100)}")
    print(f"  BPM 160: {fingers_fast}")

    print("\n--- 6. 黑鍵旋律 (Db major scale) ---")
    db_scale = [61, 63, 65, 66, 68, 70, 72, 73]  # Db4→Db5
    fingers_db = generate_fingering(db_scale, hand="right", bpm=100)
    for m, f in zip(db_scale, fingers_db):
        bw = "B" if is_black_key(m) else "W"
        print(f"  {midi_to_name(m):4s} [{bw}] → 指 {f}")

    print("\n✅ 所有測試完成")
