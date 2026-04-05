"""
Evaluator AI: 指法序列物理合理性驗證 (Ergonomics Critic)
Phase 11c #6 強化版

改進:
  6.1 疲勞累積模型 (Fatigue Accumulation)
  6.2 BPM-Aware 評分
  6.3 拇指下穿品質評估

學術依據:
  - Parncutt et al. (1997). "An Ergonomic Model of Keyboard Fingering." Music Perception.
  - Jacobs (2001). "Refinements to the Ergonomic Model." Music Perception.
  - Al Kasimi, Nichols & Raphael (2007). "Automatic Generation of Polyphonic Piano Fingerings." ISMIR.
"""

import math
from typing import List, Dict, Any

from .viterbi_engine import is_black_key, FINGER_LENGTH, MAX_COMFORTABLE_SPAN


class FingeringEvaluator:
    """
    Evaluator AI: 驗證指法序列的物理合理性 (Ergonomics Critic)
    Phase 11c: + 疲勞模型 + BPM 感知 + 拇指下穿品質
    """
    def __init__(self, hand="right", bpm=100):
        self.hand = hand
        self.bpm = bpm

    def evaluate_events(self, events: List[Dict]) -> Dict[str, Any]:
        """
        輸入完整的 events 列表 (每個元素需有 'time', 'pitch', 'finger')。
        三重約束: Pass 1 垂直, Pass 2 水平, Pass 3 疲勞/BPM/拇指品質。
        """
        if not events:
            return {"score": 0, "valid": False, "reason": "Empty events"}

        sorted_events = sorted(events, key=lambda e: (e['time'], e['pitch']))

        time_groups: Dict[float, List[Dict]] = {}
        for e in sorted_events:
            t = e.get('time', 0.0)
            if t not in time_groups:
                time_groups[t] = []
            time_groups[t].append(e)

        cost = 0.0
        warnings = []
        sorted_times = sorted(time_groups.keys())

        # ---------------------------------------------------------
        # Pass 1: Vertical Constraints (垂直約束)
        # ---------------------------------------------------------
        for t in sorted_times:
            group = time_groups[t]
            if len(group) <= 1:
                continue

            group_sorted = sorted(group, key=lambda x: x['pitch'])
            min_pitch = group_sorted[0]['pitch']
            max_pitch = group_sorted[-1]['pitch']
            fingers_played = [e.get('finger', 1) for e in group_sorted]

            # BPM-aware 跨距容忍度
            max_span = self._max_comfortable_span_bpm()
            span = max_pitch - min_pitch
            if span > max_span:
                cost += 100.0
                warnings.append(f"時間 {t}: 致命跨距 (跨 {span} 半音, 超出 {max_span} 限制)")

            # 手指順序錯位
            for i in range(len(fingers_played) - 1):
                f1, f2 = fingers_played[i], fingers_played[i+1]
                p1, p2 = group_sorted[i]['pitch'], group_sorted[i+1]['pitch']
                if p1 != p2:
                    if self.hand == "right" and f1 > f2:
                        cost += 80.0
                        warnings.append(f"時間 {t}: 右手手指錯位 (指 {f1} 按 {p1}, 指 {f2} 按 {p2})")
                    elif self.hand == "left" and f1 < f2:
                        cost += 80.0
                        warnings.append(f"時間 {t}: 左手手指錯位 (指 {f1} 按 {p1}, 指 {f2} 按 {p2})")

        # ---------------------------------------------------------
        # Pass 2: Horizontal Constraints (水平約束)
        # ---------------------------------------------------------
        thumb_unders = []  # 收集拇指下穿事件供 Pass 3 評估

        for i in range(1, len(sorted_times)):
            t_prev = sorted_times[i-1]
            t_curr = sorted_times[i]
            delta_time = t_curr - t_prev

            group_prev = time_groups[t_prev]
            group_curr = time_groups[t_curr]

            # 同指跳躍
            for f in range(1, 6):
                notes_prev_f = [e for e in group_prev if e.get('finger', 1) == f]
                notes_curr_f = [e for e in group_curr if e.get('finger', 1) == f]
                if not notes_prev_f or not notes_curr_f:
                    continue

                p_prev = notes_prev_f[0]['pitch']
                p_curr = notes_curr_f[0]['pitch']
                delta_p = p_curr - p_prev

                if delta_p != 0:
                    slide_cost = abs(delta_p) * 3.0
                    if f == 1:
                        slide_cost *= 0.5
                    # BPM penalty
                    tempo_mult = 1.0 + max(0, (self.bpm - 100)) / 80
                    time_penalty = 1.0
                    if delta_time < 0.2:
                        time_penalty = 6.0 * tempo_mult
                    elif delta_time < 0.4:
                        time_penalty = 2.5 * tempo_mult

                    applied_cost = slide_cost * time_penalty
                    cost += applied_cost
                    if applied_cost > 15.0:
                        warnings.append(f"時間 {t_curr}: 同指不合理跳躍 (指{f}, 移{delta_p}半音)")

            # 穿指/交叉檢測
            p_prev_rep = max(e['pitch'] for e in group_prev) if self.hand == "right" \
                else min(e['pitch'] for e in group_prev)
            f_prev_rep = [e.get('finger', 1) for e in group_prev if e['pitch'] == p_prev_rep][0]

            p_curr_rep = min(e['pitch'] for e in group_curr) if self.hand == "right" \
                else max(e['pitch'] for e in group_curr)
            f_curr_rep = [e.get('finger', 1) for e in group_curr if e['pitch'] == p_curr_rep][0]

            delta_p_rep = p_curr_rep - p_prev_rep
            fp_diff = f_curr_rep - f_prev_rep
            if self.hand == "left":
                fp_diff = -fp_diff

            if fp_diff < 0:
                if delta_p_rep > 0 and f_curr_rep == 1:
                    # 合法拇指下穿
                    cost += 2.0
                    thumb_unders.append({
                        "time": t_curr,
                        "from_finger": f_prev_rep,
                        "interval": delta_p_rep,
                        "delta_time": delta_time,
                    })
                elif delta_p_rep < 0 and f_prev_rep == 1:
                    cost += 2.0  # 合法跨指
                else:
                    if abs(delta_p_rep) > 2:
                        punish = abs(delta_p_rep) * 5.0
                        cost += punish
                        if punish > 10.0:
                            warnings.append(f"時間 {t_curr}: 不合理交叉 (移 {delta_p_rep}半音)")

        # ---------------------------------------------------------
        # Pass 3: Fatigue + BPM + Thumb-Under Quality
        # ---------------------------------------------------------

        # 6.1 疲勞累積模型
        fatigue_cost, fatigue_warns = self._evaluate_fatigue(sorted_events)
        cost += fatigue_cost
        warnings.extend(fatigue_warns)

        # 6.3 拇指下穿品質
        for tu in thumb_unders:
            quality_cost, quality_warn = self._evaluate_thumb_under(tu)
            cost += quality_cost
            if quality_warn:
                warnings.append(quality_warn)

        # 6.2 黑鍵-手指適性
        for e in sorted_events:
            p = e.get("pitch", 60)
            f = e.get("finger", 3)
            if is_black_key(p):
                if f == 1:
                    cost += 2.0  # 拇指按黑鍵 penalty
                elif f == 5:
                    cost += 1.0  # 小指按黑鍵 minor penalty

        # ---------------------------------------------------------
        # 最終評分
        # ---------------------------------------------------------
        max_cost = max(10, len(events) * 30)
        normalized_score = 100.0 - (cost / max_cost * 100.0)
        normalized_score = max(0.0, min(100.0, normalized_score))

        has_fatal_error = False
        fatal_keywords = ["致命跨距", "手指錯位", "不合理交叉", "不合理跳躍"]
        for w in warnings:
            if any(k in w for k in fatal_keywords):
                has_fatal_error = True
                break

        is_valid = (normalized_score > 60.0) and not has_fatal_error
        unique_warnings = list(dict.fromkeys(warnings))

        return {
            "score": round(normalized_score, 1),
            "valid": is_valid,
            "cost": round(cost, 1),
            "bpm": self.bpm,
            "warnings": unique_warnings,
        }

    # ==================================================================
    # 6.1 疲勞累積模型
    # ==================================================================
    def _evaluate_fatigue(self, events: List[Dict]):
        """
        追蹤每根手指的疲勞累積。
        弱指 (4, 5) 連續使用會產生更高的疲勞。
        """
        USAGE_COST = {1: 0.3, 2: 0.5, 3: 0.5, 4: 1.0, 5: 0.8}
        FATIGUE_HALF_LIFE = 2.0  # beats

        beat_dur = 60.0 / self.bpm
        fatigue = {f: 0.0 for f in range(1, 6)}
        last_use_time = {f: -999.0 for f in range(1, 6)}

        total_fatigue_cost = 0.0
        warnings = []

        for e in events:
            t = e.get("time", 0.0)
            f = e.get("finger", 3)
            if f < 1 or f > 5:
                continue

            # 衰減
            for finger in range(1, 6):
                elapsed_beats = (t - last_use_time[finger]) / beat_dur
                if elapsed_beats > 0:
                    decay = 0.5 ** (elapsed_beats / FATIGUE_HALF_LIFE)
                    fatigue[finger] *= decay

            # 累加疲勞
            fatigue[f] += USAGE_COST.get(f, 0.5)
            last_use_time[f] = t

            # 檢查警告
            if fatigue[f] > 5.0:
                total_fatigue_cost += (fatigue[f] - 5.0) * 2.0
                if fatigue[f] > 7.0:
                    warnings.append(
                        f"時間 {round(t, 2)}: 指 {f} 疲勞過高 ({round(fatigue[f], 1)})"
                    )

        return total_fatigue_cost, warnings

    # ==================================================================
    # 6.2 BPM-Aware 跨距
    # ==================================================================
    def _max_comfortable_span_bpm(self) -> int:
        """BPM 越高，跨距容忍度越低。"""
        if self.bpm < 80:
            return 15
        elif self.bpm <= 120:
            return 13
        elif self.bpm <= 160:
            return 10
        else:
            return 8

    # ==================================================================
    # 6.3 拇指下穿品質
    # ==================================================================
    def _evaluate_thumb_under(self, tu: Dict):
        """
        評估拇指下穿品質:
        - 合法: 從 3 或 4 指下穿
        - 非法: 從 5 指下穿
        - 品質: 下穿前後音程 2-4 半音最佳
        - 快速段: 額外 penalty
        """
        cost = 0.0
        warn = None

        from_finger = tu["from_finger"]
        interval = abs(tu["interval"])
        delta_time = tu["delta_time"]

        # 從 5 指下穿不合理
        if from_finger == 5:
            cost += 10.0
            warn = f"時間 {tu['time']}: 拇指從5指下穿 (不合理)"

        # 下穿音程太大
        if interval > 6:
            cost += (interval - 6) * 2.0
            if not warn:
                warn = f"時間 {tu['time']}: 拇指下穿跨距過大 ({interval} 半音)"

        # 快速段下穿
        beat_dur = 60.0 / self.bpm
        if delta_time < beat_dur * 0.5:
            cost += 3.0  # 快速下穿有困難

        return cost, warn

    # ==================================================================
    # 向後相容 API
    # ==================================================================
    def evaluate(self, pitches: List[int], fingers: List[int]) -> Dict[str, Any]:
        if not pitches or not fingers or len(pitches) != len(fingers):
            return {"score": 0, "valid": False, "reason": "Length mismatch or empty"}
        events = [{"time": float(i), "pitch": pitches[i], "finger": fingers[i]}
                  for i in range(len(pitches))]
        return self.evaluate_events(events)


def evaluate_fingers(pitches: List[int], fingers: List[int], hand: str = "right",
                     bpm: int = 100) -> Dict:
    return FingeringEvaluator(hand=hand, bpm=bpm).evaluate(pitches, fingers)


def evaluate_events(events: List[Dict], hand: str = "right", bpm: int = 100) -> Dict:
    return FingeringEvaluator(hand=hand, bpm=bpm).evaluate_events(events)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import pprint

    print("=== Fingering Evaluator (Phase 11c) ===\n")

    evaluator_rh = FingeringEvaluator(hand="right", bpm=120)

    print("--- 1. Valid Arpeggio (RH) ---")
    ev1 = [
        {"time": 0.0, "pitch": 60, "finger": 1},
        {"time": 0.5, "pitch": 64, "finger": 2},
        {"time": 1.0, "pitch": 67, "finger": 3},
        {"time": 1.5, "pitch": 72, "finger": 5},
    ]
    pprint.pprint(evaluator_rh.evaluate_events(ev1))

    print("\n--- 2. Rapid 4th finger stress ---")
    ev2 = [{"time": i * 0.15, "pitch": 65 + (i % 3), "finger": 4}
           for i in range(10)]
    pprint.pprint(evaluator_rh.evaluate_events(ev2))

    print("\n--- 3. Thumb on black key ---")
    ev3 = [
        {"time": 0.0, "pitch": 61, "finger": 1},  # C#4 with thumb
        {"time": 0.5, "pitch": 63, "finger": 2},  # D#4
    ]
    pprint.pprint(evaluator_rh.evaluate_events(ev3))

    print("\n--- 4. Fast BPM=180 ---")
    evaluator_fast = FingeringEvaluator(hand="right", bpm=180)
    ev4 = [
        {"time": 0.0, "pitch": 60, "finger": 1},
        {"time": 0.0, "pitch": 64, "finger": 2},
        {"time": 0.0, "pitch": 67, "finger": 3},
        {"time": 0.0, "pitch": 71, "finger": 4},
        {"time": 0.0, "pitch": 72, "finger": 5},  # span = 12
    ]
    pprint.pprint(evaluator_fast.evaluate_events(ev4))
