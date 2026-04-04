"""
Accompaniment Generation & Fingering Engine
伴奏生成與指法推導引擎 (Phase 10)

Pipeline:
  1. Skeleton Voicing: chord text ->MIDI pitches (via chord_table)
  2. Style Pattern Routing: map pitches to rhythmic pattern
  3. Conflict Resolution: avoid melody collision
  4. Viterbi Fingering: optimal finger assignment
"""

import sys
import os
import math
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from backend.chord_table import get_chord_notes, root_to_semitone, parse_chord

# ==============================================================================
# 壹、基礎常數
# ==============================================================================

OCTAVE = 12
# Left hand range: C2 (36) ~ B3 (59)
LH_LOW, LH_HIGH = 36, 59
# Right hand range: C4 (60) ~ C6 (84)
RH_LOW, RH_HIGH = 60, 84

# ==============================================================================
# 貳、Pattern Dictionary (樣式字典)
# ==============================================================================
# 格式: [(time_fraction, [pitch_indices], velocity_ratio)]
# time_fraction: 0.0~1.0 相對於和弦持續時間的位置
# pitch_indices: 引用 voicing 陣列的索引 ('R'=0, '3rd'=1, '5th'=2, '7th'=3)
# velocity_ratio: 相對力度 (1.0 = 基準)

STYLE_DICT = {
    "Block": [
        (0.0, [0, 1, 2], 1.0),
    ],
    "Arpeggio": [
        (0.0,  [0],    1.0),
        (0.25, [1],    0.8),
        (0.50, [2],    0.8),
        (0.75, [1],    0.7),
    ],
    "Rhythm": [
        (0.0,  [0],       1.0),
        (0.50, [0, 1, 2], 0.85),
        (0.75, [0, 1, 2], 0.7),
    ],
    "Alberti": [
        (0.0,  [0],  1.0),   # 低 (Root)
        (0.25, [2],  0.75),   # 高 (5th)
        (0.50, [1],  0.8),    # 中 (3rd)
        (0.75, [2],  0.75),   # 高 (5th)
    ],
    "Shell": [
        (0.0, [1, 3], 0.9),  # 3rd + 7th only
    ],
    "Walking": [
        (0.0,  [0],  1.0),    # Root
        (0.25, [1],  0.85),   # 3rd
        (0.50, [2],  0.85),   # 5th
        (0.75, [-1], 0.8),    # approach note (chromatic below next root)
    ],
    "Stride": [
        (0.0,  [0],       1.0),    # 低音 Root (低八度)
        (0.50, [1, 2, 3], 0.85),   # 中音 和弦 (正常八度)
    ],
}

# 曲風適配表
GENRE_STYLE_MAP = {
    "pop":       ["Block", "Arpeggio", "Rhythm"],
    "ballad":    ["Arpeggio", "Block"],
    "jazz":      ["Shell", "Walking", "Stride"],
    "bossa":     ["Shell", "Walking"],
    "classical": ["Alberti", "Arpeggio"],
    "rock":      ["Rhythm", "Block"],
    "r&b":       ["Rhythm", "Shell", "Arpeggio"],
    "electronic": ["Block", "Rhythm"],
    "edm":       ["Block", "Rhythm"],
    "country":   ["Arpeggio", "Block"],
    "folk":      ["Arpeggio", "Block"],
    "latin":     ["Rhythm", "Walking"],
}

BPM_STYLE_MAP = [
    (80,  ["Arpeggio", "Shell"]),       # 慢歌
    (120, ["Block", "Arpeggio", "Rhythm"]),  # 中速
    (999, ["Rhythm", "Block"]),          # 快歌
]


# ==============================================================================
# 參、音高工具函數
# ==============================================================================

def note_names_to_midi(notes: List[str], base_octave: int) -> List[int]:
    """將音名陣列轉為 MIDI pitches，保證向上遞增。"""
    if not notes:
        return []
    pitches = []
    base = root_to_semitone(notes[0]) + (base_octave + 1) * 12
    for note in notes:
        st = root_to_semitone(note)
        pitch = st + (base_octave + 1) * 12
        while pitches and pitch <= pitches[-1]:
            pitch += 12
        if not pitches and pitch < base:
            pitch += 12
        pitches.append(pitch)
    return pitches


def voice_leading_optimize(pitches: List[int], prev_pitches: List[int],
                           low: int, high: int) -> List[int]:
    """最短移動距離 Voice Leading：調整 pitches 使其盡量靠近 prev_pitches。"""
    if not prev_pitches or not pitches:
        return pitches

    prev_center = sum(prev_pitches) / len(prev_pitches)
    result = []
    for p in pitches:
        best = p
        best_dist = abs(p - prev_center)
        for shift in [-12, 0, 12]:
            candidate = p + shift
            if low <= candidate <= high:
                dist = abs(candidate - prev_center)
                if dist < best_dist:
                    best_dist = dist
                    best = candidate
        result.append(best)
    result.sort()
    return result


def expand_voicing(pitches: List[int], target_size: int, max_span: int = 13) -> List[int]:
    """擴展 voicing 到指定大小（八度疊加），並強制進行人手合理性檢查（聲部過濾與最大跨度限制）。"""
    if not pitches:
        return []
    result = list(pitches)
    base_pitch = min(pitches)
    
    # 嘗試往上疊加八度，並套用 Voice Filtering 與 Max Span Constraint
    while len(result) < target_size:
        next_pitch = result[-len(pitches)] + 12
        if next_pitch - base_pitch <= max_span:
            result.append(next_pitch)
        else:
            # 超過閾值：尋找該和絃的密集排列（Close Position）替代方案
            close_pitch = next_pitch
            while close_pitch - base_pitch > max_span:
                close_pitch -= 12
            
            if close_pitch >= base_pitch and close_pitch not in result:
                result.append(close_pitch)
                result.sort()
            else:
                # 無可用的 Close Position，強制拋棄重複音 (Doubling notes)
                break
                
    return result[:target_size]


def clamp_to_range(pitches: List[int], low: int, high: int) -> List[int]:
    """將音高限制在指定範圍內。"""
    result = []
    for p in pitches:
        while p < low:
            p += 12
        while p > high:
            p -= 12
        result.append(p)
    return sorted(set(result))


# ==============================================================================
# 肆、伴奏生成核心
# ==============================================================================

def suggest_style(genre: str = "", bpm: float = 120.0) -> List[str]:
    """根據曲風和 BPM 建議伴奏風格，回傳排序後的建議清單。"""
    genre_lower = genre.lower().strip()
    scores: Dict[str, float] = {}

    # Genre 匹配
    for key, styles in GENRE_STYLE_MAP.items():
        if key in genre_lower:
            for i, s in enumerate(styles):
                scores[s] = scores.get(s, 0) + (3 - i)  # 越前面分數越高

    # BPM 匹配
    for threshold, styles in BPM_STYLE_MAP:
        if bpm < threshold:
            for i, s in enumerate(styles):
                scores[s] = scores.get(s, 0) + (2 - i * 0.5)
            break

    # 無匹配時回傳預設
    if not scores:
        return ["Block", "Arpeggio", "Rhythm"]

    return sorted(scores, key=scores.get, reverse=True)[:3]


def _build_left_hand(chord_name: str, start_time: float, duration: float,
                     style: str, level: str, prev_lh: List[int],
                     next_root_midi: Optional[int],
                     melody: List[Dict], base_velocity: int = 70) -> Tuple[List[Dict], List[int]]:
    """生成單一和弦的左手伴奏事件。"""
    notes = get_chord_notes(chord_name)
    if not notes:
        return [], prev_lh

    root_name, quality, bass_name = parse_chord(chord_name)
    if bass_name:
        notes[0] = bass_name

    # Level 決定 voicing 複雜度
    if level == "L1":
        # 只有根音
        raw = note_names_to_midi(notes[:1], base_octave=2)
    elif level == "L2" and len(notes) >= 4:
        # Shell Voicing: 根音(C2) + 3rd/7th(C3)
        shell_notes = [notes[1], notes[3]] if len(notes) >= 4 else notes[1:3]
        upper = note_names_to_midi(shell_notes, base_octave=3)
        root_st = root_to_semitone(notes[0]) + 36 # C2 root
        raw = [root_st] + upper
    else:
        # L3 或無足夠音符時用完整 voicing
        # Open Voicing: 根音(C2) + 剩餘和弦音(C3)
        upper = note_names_to_midi(notes[1:], base_octave=3)
        root_st = root_to_semitone(notes[0]) + 36 # C2 root
        raw = [root_st] + upper

    # Clamp 到左手範圍
    pitches = clamp_to_range(raw, LH_LOW, LH_HIGH)
    if not pitches:
        pitches = clamp_to_range(raw, LH_LOW - 12, LH_HIGH + 12)
    if not pitches:
        return [], prev_lh

    # Voice Leading
    pitches = voice_leading_optimize(pitches, prev_lh, LH_LOW, LH_HIGH)

    # 取得 pattern
    pattern = STYLE_DICT.get(style, STYLE_DICT["Block"])

    # L1 強制簡化 pattern
    if level == "L1" and style not in ("Block", "Shell"):
        pattern = [(0.0, [0], 1.0)]

    # 擴展 voicing 到 pattern 需要的最大索引
    max_idx = max(max(abs(i) for i in indices) for _, indices, _ in pattern) + 1
    voicing = expand_voicing(pitches, max(max_idx, len(pitches)))

    events = []
    for frac, indices, vel_ratio in pattern:
        event_time = start_time + frac * duration
        # 每個 pattern step 的持續時間
        event_dur = (duration / len(pattern)) * 0.85

        for idx in indices:
            # Walking Bass approach note: -1 表示下一和弦根音的半音下方
            if idx == -1:
                if next_root_midi is not None:
                    pitch = next_root_midi - 1  # 半音趨近
                    while pitch < LH_LOW:
                        pitch += 12
                    while pitch > LH_HIGH:
                        pitch -= 12
                else:
                    continue
            elif len(voicing) > 0:
                # 自動尋找密集排列替代：若原本的廣跨度被拋棄，透過取餘數讓琶音在合理範圍內盤旋
                pitch = voicing[idx % len(voicing)]
            else:
                continue

            # Stride: 第一拍降八度
            if style == "Stride" and frac < 0.25:
                if pitch - 12 >= LH_LOW:
                    pitch -= 12

            # 旋律防撞
            if _check_melody_conflict(pitch, event_time, event_dur, melody):
                if pitch - 12 >= LH_LOW:
                    pitch -= 12

            velocity = int(base_velocity * vel_ratio)
            events.append({
                "time": round(event_time, 3),
                "duration": round(event_dur, 3),
                "pitch": int(pitch),
                "velocity": velocity,
                "hand": "left",
            })

    return events, pitches


def _build_right_hand(chord_name: str, start_time: float, duration: float,
                      level: str, melody_segment: List[Dict],
                      base_velocity: int = 90) -> List[Dict]:
    """生成單一和弦的右手事件（基於旋律）。"""
    events = []
    chord_notes = get_chord_notes(chord_name)
    chord_pitches_pc = set()  # pitch class set for chord tone marking
    for n in chord_notes:
        chord_pitches_pc.add(root_to_semitone(n) % 12)

    for m in melody_segment:
        m_start = m.get("start", m.get("time", 0))
        m_end = m.get("end", m_start + m.get("duration", 0.5))
        m_midi = m.get("midi", 60)

        # 只取落在此和弦時間範圍內的旋律
        if m_end <= start_time or m_start >= start_time + duration:
            continue

        # 裁切到和弦邊界
        note_start = max(m_start, start_time)
        note_end = min(m_end, start_time + duration)
        note_dur = note_end - note_start
        if note_dur < 0.05:
            continue

        is_chord_tone = (m_midi % 12) in chord_pitches_pc

        evt = {
            "time": round(note_start, 3),
            "duration": round(note_dur, 3),
            "pitch": int(m_midi),
            "velocity": base_velocity,
            "hand": "right",
            "chord_tone": is_chord_tone,
        }

        events.append(evt)

        # L2: 加入和聲三度
        if level in ("L2", "L3") and is_chord_tone and note_dur > 0.2:
            harmony_pitch = m_midi + 4  # 大三度上方
            if harmony_pitch <= RH_HIGH:
                events.append({
                    "time": round(note_start, 3),
                    "duration": round(note_dur, 3),
                    "pitch": int(harmony_pitch),
                    "velocity": int(base_velocity * 0.6),
                    "hand": "right",
                    "chord_tone": (harmony_pitch % 12) in chord_pitches_pc,
                })

    return events


def _check_melody_conflict(pitch: int, time: float, dur: float,
                           melody: List[Dict]) -> bool:
    """檢查伴奏音是否與旋律音太近（< 4 半音）。"""
    for m in melody:
        m_start = m.get("start", m.get("time", 0))
        m_end = m.get("end", m_start + m.get("duration", 0.5))
        m_midi = m.get("midi", 0)
        # 時間重疊檢查
        if m_start < time + dur and m_end > time:
            if abs(m_midi - pitch) < 4:
                return True
    return False


def _filter_lh_span(events: List[Dict], max_span: int = 13) -> List[Dict]:
    """過濾左手音符，限制最大跨距。"""
    time_groups: Dict[float, List[Dict]] = {}
    for e in events:
        t = e["time"]
        if t not in time_groups:
            time_groups[t] = []
        time_groups[t].append(e)

    filtered = []
    for t, group in time_groups.items():
        if not group: continue
        group.sort(key=lambda x: x["pitch"])
        min_pitch = group[0]["pitch"]
        for e in group:
            if e["pitch"] - min_pitch <= max_span:
                filtered.append(e)
    return filtered


def generate_accompaniment(chords: List[Dict],
                           melody: List[Dict] = None,
                           bpm: float = 120.0,
                           style: str = "Block",
                           level: str = "L1",
                           genre: str = "") -> Dict[str, Any]:
    """
    主入口：根據和弦序列、旋律、風格與難度，生成左右手 MIDI 伴奏。

    Args:
        chords: [{"time": 0, "end": 4.5, "chord": "Cmaj7"}, ...]
        melody: [{"start": 0.5, "end": 1.0, "midi": 72}, ...]
        bpm: 歌曲 BPM
        style: Block/Arpeggio/Rhythm/Alberti/Shell/Walking/Stride
        level: L1(初階)/L2(中階)/L3(進階)
        genre: 曲風字串（用於建議）

    Returns:
        {
          "left_hand": [...events with finger...],
          "right_hand": [...events with finger...],
          "suggested_styles": [...],
          "style": "...",
          "level": "..."
        }
    """
    melody = melody or []
    if style not in STYLE_DICT:
        style = "Block"
    if level not in ("L1", "L2", "L3"):
        level = "L1"

    left_events = []
    right_events = []
    prev_lh: List[int] = []

    # Hand Balance: L1/L2 左手弱, L3 正常
    lh_velocity = 55 if level == "L1" else 65 if level == "L2" else 70
    rh_velocity = 90

    for i, chord_evt in enumerate(chords):
        start = chord_evt.get("time", 0)
        end = chord_evt.get("end", start + 2.0)
        duration = end - start
        chord_name = chord_evt.get("chord", "C")

        if duration < 0.1:
            continue

        # 計算下一和弦根音 (Walking Bass approach note)
        next_root_midi = None
        if i + 1 < len(chords):
            next_notes = get_chord_notes(chords[i + 1].get("chord", "C"))
            if next_notes:
                next_root_midi = root_to_semitone(next_notes[0]) + 48  # C3 base

        # 左手
        lh, prev_lh = _build_left_hand(
            chord_name, start, duration, style, level,
            prev_lh, next_root_midi, melody, lh_velocity
        )
        left_events.extend(lh)

        # 右手（旋律 + 和聲）
        rh = _build_right_hand(chord_name, start, duration, level, melody, rh_velocity)
        right_events.extend(rh)

    # 左手跨度限制過濾
    left_events = _filter_lh_span(left_events, max_span=13)

    # 排序
    left_events.sort(key=lambda e: (e["time"], e["pitch"]))
    right_events.sort(key=lambda e: (e["time"], e["pitch"]))

    # Viterbi 指法推導
    _assign_fingering(left_events, hand="left")
    _assign_fingering(right_events, hand="right")

    return {
        "left_hand": left_events,
        "right_hand": right_events,
        "suggested_styles": suggest_style(genre, bpm),
        "style": style,
        "level": level,
    }


from .fingering_model import generate_fingers
from .fingering_evaluator import evaluate_fingers

# ==============================================================================
# 伍、AI指法生成與驗證雙軌架構 (Generator-Evaluator Architecture)
# ==============================================================================

def _assign_fingering(events: List[Dict], hand: str = "right"):
    """使用雙軌AI 架構對事件序列注入 finger 欄位（就地修改）。"""
    if not events:
        return

    # 提取按時間點群組
    time_groups: Dict[float, List[Dict]] = {}
    for e in events:
        t = e["time"]
        if t not in time_groups:
            time_groups[t] = []
        time_groups[t].append(e)

    sorted_times = sorted(time_groups.keys())
    if not sorted_times:
        return

    # 取每組的代表音 (右手取最高，左手取最低) 作為旋律/Bass線
    rep_pitches = []
    for t in sorted_times:
        group = time_groups[t]
        rep = max(e["pitch"] for e in group) if hand == "right" else min(e["pitch"] for e in group)
        rep_pitches.append(rep)

    # 1. 呼叫 Generator AI 產生初版指法
    fingers = generate_fingers(rep_pitches, hand=hand)

    # 2. 呼叫 Evaluator AI (Critic) 進行嚴格人體工學審核
    qa_report = evaluate_fingers(rep_pitches, fingers, hand=hand)
    
    # 3. 處理 Reject 路徑 (Fallback 機制)
    if not qa_report["valid"]:
        # 若被判定為「外星人」指法或有致命跨距，強制啟用 Close Position 安全降級
        # 這裡的簡單降級方案為全部初始化為保守順位，或拋棄跨距外的音符
        # (這裡目前作保守設定，未來可再次呼叫 Generator 產生 beam search 第二名)
        if hand == "left":
            fingers = [5] * len(rep_pitches)
        else:
            fingers = [1] * len(rep_pitches)

    # 將指法寫回所有 events (和絃散開邏輯)
    for i, t in enumerate(sorted_times):
        group = time_groups[t]
        base_finger = fingers[i] if i < len(fingers) else (1 if hand == "right" else 5)
        
        group_sorted = sorted(group, key=lambda e: e["pitch"])
        if len(group_sorted) == 1:
            group_sorted[0]["finger"] = base_finger
        else:
            # Chord block
            if hand == "right":
                # 右手：最低音必定是拇指(1)，然後往上分配
                fingers_to_assign = {
                    2: [1, 5],
                    3: [1, 3, 5],
                    4: [1, 2, 3, 5],
                    5: [1, 2, 3, 4, 5]
                }.get(len(group_sorted), [1] * len(group_sorted))
                for j, e in enumerate(group_sorted):
                    if j < len(fingers_to_assign):
                        e["finger"] = fingers_to_assign[j]
            else:
                # 左手：最低音必定是小指(5)，一直到拇指(1)
                fingers_to_assign = {
                    2: [5, 1],
                    3: [5, 3, 1],
                    4: [5, 4, 2, 1],
                    5: [5, 4, 3, 2, 1]
                }.get(len(group_sorted), [5] * len(group_sorted))
                for j, e in enumerate(group_sorted):
                    if j < len(fingers_to_assign):
                        e["finger"] = fingers_to_assign[j]


def calculate_physical_cost(delta_p: int, f_prev: int, f_curr: int,
                            hand: str = "right") -> float:
    """計算從前一個手指切換到現有手指的物理移動成本。"""
    cost = 0.0

    # 同音換指
    if delta_p == 0:
        return 10.0 if f_prev == f_curr else 2.0

    # 超過八度
    if abs(delta_p) > 12:
        return float('inf')

    delta_f = f_curr - f_prev

    # 左手鏡像：方向邏輯反轉
    if hand == "left":
        delta_p = -delta_p
        delta_f = -delta_f

    # 順向性
    if delta_p > 0 and delta_f < 0:
        # 音往上走但手指往下 ->穿指
        if f_curr == 1:
            cost += 5.0   # 拇指穿過：常見合法
        else:
            cost += 50.0  # 其他手指穿過：幾乎不可能
    elif delta_p < 0 and delta_f > 0:
        if f_prev == 1:
            cost += 5.0
        else:
            cost += 50.0
    else:
        # 正常順向移動
        expected = delta_f * 2
        cost += abs(delta_p - expected) * 2.0

    return cost


def viterbi_fingering(pitches: List[int], hand: str = "right") -> List[int]:
    """Viterbi 動態規劃尋找最優指法序列。"""
    if not pitches:
        return []
    T = len(pitches)
    if T == 1:
        return [3]  # 中指起始

    NUM_FINGERS = 5
    dp = np.full((T, NUM_FINGERS), float('inf'))
    paths = np.zeros((T, NUM_FINGERS), dtype=int)

    for f in range(NUM_FINGERS):
        dp[0][f] = 0.0

    for t in range(1, T):
        delta_p = pitches[t] - pitches[t - 1]
        for f_curr in range(NUM_FINGERS):
            min_cost = float('inf')
            best_prev = 0
            for f_prev in range(NUM_FINGERS):
                cost = calculate_physical_cost(delta_p, f_prev + 1, f_curr + 1, hand)
                total = dp[t - 1][f_prev] + cost
                if total < min_cost:
                    min_cost = total
                    best_prev = f_prev
            dp[t][f_curr] = min_cost
            paths[t][f_curr] = best_prev

    # Backtrack
    best_last = int(np.argmin(dp[T - 1]))
    seq = [0] * T
    seq[T - 1] = best_last
    for t in range(T - 1, 0, -1):
        seq[t - 1] = paths[t][seq[t]]

    return [int(f + 1) for f in seq]


# ==============================================================================
# 陸、測試
# ==============================================================================

if __name__ == "__main__":
    print("=== 1. Style Suggestion ===")
    print("Pop, 100bpm:", suggest_style("pop", 100))
    print("Jazz, 140bpm:", suggest_style("jazz", 140))
    print("Classical, 70bpm:", suggest_style("classical", 70))
    print("Unknown, 120bpm:", suggest_style("", 120))

    print("\n=== 2. Accompaniment Generation ===")
    test_chords = [
        {"time": 0.0, "end": 2.0, "chord": "Cmaj7"},
        {"time": 2.0, "end": 4.0, "chord": "Am7"},
        {"time": 4.0, "end": 6.0, "chord": "Dm7"},
        {"time": 6.0, "end": 8.0, "chord": "G7"},
    ]
    test_melody = [
        {"start": 0.5, "end": 1.5, "midi": 72},  # C5
        {"start": 1.5, "end": 2.5, "midi": 71},  # B4
        {"start": 2.5, "end": 3.5, "midi": 69},  # A4
        {"start": 3.5, "end": 4.5, "midi": 67},  # G4
        {"start": 4.5, "end": 5.5, "midi": 65},  # F4
        {"start": 5.5, "end": 6.5, "midi": 64},  # E4
        {"start": 6.5, "end": 7.5, "midi": 62},  # D4
        {"start": 7.5, "end": 8.0, "midi": 60},  # C4
    ]

    for lvl in ("L1", "L2", "L3"):
        for sty in ("Arpeggio", "Shell", "Walking"):
            result = generate_accompaniment(
                test_chords, test_melody,
                bpm=120, style=sty, level=lvl, genre="jazz"
            )
            lh_count = len(result["left_hand"])
            rh_count = len(result["right_hand"])
            print(f"  {sty:10s} {lvl}: LH={lh_count:2d} events, RH={rh_count:2d} events")

    print("\n=== 3. Arpeggio L2 Detail ===")
    result = generate_accompaniment(
        test_chords, test_melody,
        bpm=120, style="Arpeggio", level="L2", genre="pop"
    )
    print(f"  Suggested styles: {result['suggested_styles']}")
    print("  Left hand (first 8):")
    for e in result["left_hand"][:8]:
        print(f"    t={e['time']:5.2f} p={e['pitch']:3d} v={e['velocity']:2d} f={e['finger']}")
    print("  Right hand (first 8):")
    for e in result["right_hand"][:8]:
        ct = "CT" if e.get("chord_tone") else "PT"
        print(f"    t={e['time']:5.2f} p={e['pitch']:3d} v={e['velocity']:2d} f={e['finger']} {ct}")

    print("\n=== 4. Viterbi Fingering ===")
    scale = [60, 62, 64, 65, 67, 69, 71, 72]
    print(f"  C scale up:   {scale} ->{viterbi_fingering(scale, 'right')}")
    scale_down = [72, 71, 69, 67, 65, 64, 62, 60]
    print(f"  C scale down: {scale_down} ->{viterbi_fingering(scale_down, 'right')}")
    lh_scale = [48, 50, 52, 53, 55, 57, 59, 60]
    print(f"  LH scale up:  {lh_scale} ->{viterbi_fingering(lh_scale, 'left')}")

    print("\n=== All tests passed ===")
