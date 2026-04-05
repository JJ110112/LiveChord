"""
AI Dynamics & Articulation Engine: 力度曲線與觸鍵建議生成器
Phase 11d

根據樂句結構、拍號、BPM 與段落類型，自動生成 velocity 曲線及 articulation 建議。

學術依據:
  - Todd (1992). "The dynamics of dynamics: A model of musical expression."
    Journal of the Acoustical Society of America.
  - Palmer (1997). "Music performance." Annual Review of Psychology.
  - Repp (1999). "Control of expressive and metronomic timing in pianists."
    Journal of Motor Behavior.

力度曲線公式:
  velocity(t) = base + amplitude * sin(π * t / phrase_length)
  峰值位於黃金比例 ~0.618 處 (透過非對稱 sin 修正)

Articulation 類型:
  - legato:   音符重疊 (overlap ~20ms)
  - staccato: 音符縮短至 50% duration
  - portato:  音符間微小間隙 (~20ms gap)
"""

import math
from typing import List, Dict, Any, Optional, Tuple


# ──────────────────────────────────────────
# 段落能量等級
# ──────────────────────────────────────────

SECTION_ENERGY = {
    "intro":  0.5,
    "verse":  0.7,
    "chorus": 1.0,
    "bridge": 0.6,
    "outro":  0.4,
}

# ──────────────────────────────────────────
# 重音模式 (按拍號)
# ──────────────────────────────────────────

ACCENT_PATTERNS = {
    "4/4": [1.0, 0.7, 0.85, 0.7],
    "3/4": [1.0, 0.7, 0.7],
    "2/4": [1.0, 0.7],
    "6/8": [1.0, 0.7, 0.7, 0.85, 0.7, 0.7],
}

# ──────────────────────────────────────────
# 力度參數
# ──────────────────────────────────────────

# velocity 範圍 (MIDI 0-127)
VELOCITY_MIN = 30
VELOCITY_MAX = 127

# 黃金比例
GOLDEN_RATIO = 0.618

# Articulation 門檻
STACCATO_BPM_THRESHOLD = 140
LEGATO_BPM_THRESHOLD = 80


# ──────────────────────────────────────────
# 輔助工具
# ──────────────────────────────────────────

def _beat_duration(bpm: float) -> float:
    """一拍的秒數"""
    return 60.0 / max(bpm, 1)


def _parse_time_sig(time_sig: str) -> Tuple[int, int]:
    """解析拍號字串 '4/4' → (4, 4)"""
    parts = time_sig.split("/")
    if len(parts) == 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 4, 4


def _get_section_energy(section_type: str) -> float:
    """取得段落能量等級 (0.0 ~ 1.0)"""
    return SECTION_ENERGY.get(section_type.lower(), 0.7)


def _event_time(event: Dict) -> float:
    """取得事件時間"""
    return float(event.get("time", event.get("start", 0.0)))


def _event_duration(event: Dict) -> float:
    """取得事件持續時間"""
    return float(event.get("duration", 0.5))


def _event_pitch(event: Dict) -> int:
    """取得事件音高"""
    return int(event.get("pitch", event.get("note", 60)))


# ──────────────────────────────────────────
# 樂句分割
# ──────────────────────────────────────────

def _detect_phrases(events: List[Dict], bpm: float) -> List[List[int]]:
    """
    簡易樂句偵測: 以時間間隙 > 1拍 或 大跳 > 7半音 為斷句點。
    回傳每個樂句的事件索引列表。
    """
    if not events:
        return []

    beat_dur = _beat_duration(bpm)
    gap_threshold = beat_dur * 1.0
    interval_threshold = 7  # 半音

    phrases: List[List[int]] = [[0]]

    for i in range(1, len(events)):
        prev_end = _event_time(events[i - 1]) + _event_duration(events[i - 1])
        curr_start = _event_time(events[i])
        gap = curr_start - prev_end

        pitch_jump = abs(_event_pitch(events[i]) - _event_pitch(events[i - 1]))

        if gap > gap_threshold or pitch_jump > interval_threshold:
            phrases.append([i])
        else:
            phrases[-1].append(i)

    return phrases


# ──────────────────────────────────────────
# 力度曲線: 樂句拱形 (Phrase Arch)
# ──────────────────────────────────────────

def _phrase_velocity(
    events: List[Dict],
    phrase_indices: List[int],
    section_energy: float,
) -> None:
    """
    為一個樂句內的事件調整 velocity (原地修改)。
    以原始 velocity 為基底，疊加拱形曲線調變 (±20%)。
    峰值偏向黃金比例位置 (非對稱 sin)。
    """
    n = len(phrase_indices)
    if n == 0:
        return

    # 拱形調變幅度: ±20% of original velocity
    modulation_depth = 0.20 * section_energy

    p = math.log(0.5) / math.log(GOLDEN_RATIO) if GOLDEN_RATIO > 0 else 1.0

    for rank, idx in enumerate(phrase_indices):
        if n == 1:
            t_norm = GOLDEN_RATIO
        else:
            t_norm = rank / (n - 1)

        u = t_norm ** p if t_norm > 0 else 0.0
        # shape: 0 ~ 1, 代表樂句拱形位置
        shape = math.sin(math.pi * u)

        # 以原始 velocity 為基底，套用拱形調變
        original_vel = events[idx].get("velocity", 80)
        # 調變範圍: original * (1 - depth) ~ original * (1 + depth)
        # shape=0 → 減弱, shape=1 → 增強
        factor = 1.0 + modulation_depth * (2.0 * shape - 1.0)
        vel = original_vel * factor
        vel = max(VELOCITY_MIN, min(VELOCITY_MAX, round(vel)))
        events[idx]["velocity"] = int(vel)


# ──────────────────────────────────────────
# 重音模式套用
# ──────────────────────────────────────────

def _apply_accents(
    events: List[Dict],
    bpm: float,
    time_sig: str,
) -> None:
    """
    根據拍號的重音模式微調 velocity (原地修改)。
    """
    pattern = ACCENT_PATTERNS.get(time_sig, ACCENT_PATTERNS["4/4"])
    beats_per_bar, _ = _parse_time_sig(time_sig)
    beat_dur = _beat_duration(bpm)

    for ev in events:
        t = _event_time(ev)
        # 計算此事件在小節內的拍數位置
        beat_in_bar = (t / beat_dur) % beats_per_bar
        beat_idx = int(round(beat_in_bar)) % len(pattern)
        accent = pattern[beat_idx]

        base_vel = ev.get("velocity", 80)
        # 重音調整: ±15%
        adjusted = base_vel * (0.85 + 0.15 * accent)
        ev["velocity"] = int(max(VELOCITY_MIN, min(VELOCITY_MAX, round(adjusted))))


# ──────────────────────────────────────────
# Articulation 決策
# ──────────────────────────────────────────

def _is_stepwise(events: List[Dict], indices: List[int]) -> bool:
    """判斷樂句是否為級進旋律 (多數音程 ≤ 2 半音)"""
    if len(indices) < 2:
        return True
    stepwise_count = 0
    for i in range(1, len(indices)):
        interval = abs(_event_pitch(events[indices[i]]) -
                       _event_pitch(events[indices[i - 1]]))
        if interval <= 2:
            stepwise_count += 1
    return stepwise_count / (len(indices) - 1) >= 0.6


def _is_repeated_pattern(events: List[Dict], indices: List[int]) -> bool:
    """判斷是否為重複音型 (連續相同音高)"""
    if len(indices) < 3:
        return False
    repeated = 0
    for i in range(1, len(indices)):
        if _event_pitch(events[indices[i]]) == _event_pitch(events[indices[i - 1]]):
            repeated += 1
    return repeated / (len(indices) - 1) >= 0.5


def _decide_articulation(
    events: List[Dict],
    phrase_indices: List[int],
    bpm: float,
) -> str:
    """
    決定樂句的 articulation 類型。
    規則:
      - BPM > 140 + 重複音型 → staccato
      - BPM < 80 + 級進旋律 → legato
      - 其他 → portato
    """
    if bpm > STACCATO_BPM_THRESHOLD and _is_repeated_pattern(events, phrase_indices):
        return "staccato"
    if bpm < LEGATO_BPM_THRESHOLD and _is_stepwise(events, phrase_indices):
        return "legato"
    return "portato"


def _apply_articulation(
    events: List[Dict],
    phrase_indices: List[int],
    articulation: str,
) -> None:
    """
    套用 articulation 到事件 (原地修改)。
    - legato:   增加 20ms overlap (不改 duration, 標記即可)
    - staccato: duration 縮短為 50%
    - portato:  duration 縮短約 20ms gap
    """
    for idx in phrase_indices:
        ev = events[idx]
        ev["articulation"] = articulation

        original_dur = _event_duration(ev)

        if articulation == "staccato":
            ev["duration"] = round(original_dur * 0.5, 4)
        elif articulation == "portato":
            gap = 0.02  # 20ms
            ev["duration"] = round(max(0.05, original_dur - gap), 4)
        elif articulation == "legato":
            # legato: 微小 overlap, 稍微延長
            ev["duration"] = round(original_dur + 0.02, 4)


# ──────────────────────────────────────────
# 主要 API
# ──────────────────────────────────────────

def generate_dynamics(
    events: List[Dict],
    bpm: float = 120,
    time_sig: str = "4/4",
    section_type: str = "verse",
) -> List[Dict[str, Any]]:
    """
    為音符事件列表生成力度曲線和 articulation 建議。

    Args:
        events:       音符列表 [{time, pitch, duration}, ...]
        bpm:          速度 (預設 120)
        time_sig:     拍號 (預設 "4/4")
        section_type: 段落類型 "intro"|"verse"|"chorus"|"bridge"|"outro"

    Returns:
        events (原地修改), 每個事件增加 'velocity' 和 'articulation' 欄位
    """
    if not events:
        return events

    # 確保依時間排序
    events.sort(key=lambda e: _event_time(e))

    section_energy = _get_section_energy(section_type)

    # 步驟 1: 偵測樂句
    phrases = _detect_phrases(events, bpm)

    # 步驟 2: 為每個樂句生成拱形 velocity
    for phrase_indices in phrases:
        _phrase_velocity(events, phrase_indices, section_energy)

    # 步驟 3: 套用拍號重音模式
    _apply_accents(events, bpm, time_sig)

    # 步驟 4: 決定並套用 articulation
    for phrase_indices in phrases:
        art = _decide_articulation(events, phrase_indices, bpm)
        _apply_articulation(events, phrase_indices, art)

    return events


# ──────────────────────────────────────────
# 力度品質評估
# ──────────────────────────────────────────

def evaluate_dynamics(events: List[Dict]) -> Dict[str, Any]:
    """
    評估力度與觸鍵品質。

    評分維度:
      1. phrase_shape_score: 樂句是否有拱形曲線
      2. accent_score:       重音是否合理
      3. variety_score:      力度變化豐富度

    Returns:
        {"total_score": float, "phrase_shape_score": float,
         "accent_score": float, "variety_score": float,
         "details": str}
    """
    if not events:
        return {
            "total_score": 0.0,
            "phrase_shape_score": 0.0,
            "accent_score": 0.0,
            "variety_score": 0.0,
            "details": "No events to evaluate",
        }

    velocities = [ev.get("velocity", 80) for ev in events]

    # ── 1. 樂句拱形評估 ──
    # 簡易: 檢查 velocity 是否有上升-下降趨勢
    n = len(velocities)
    if n >= 3:
        peak_pos = velocities.index(max(velocities))
        # 峰值應在前 1/3 ~ 後 1/3 之間
        peak_ratio = peak_pos / (n - 1) if n > 1 else 0.5
        if 0.3 <= peak_ratio <= 0.8:
            phrase_shape_score = 90.0
        elif 0.2 <= peak_ratio <= 0.9:
            phrase_shape_score = 70.0
        else:
            phrase_shape_score = 40.0

        # 加分: 有明顯的上升和下降
        first_third = velocities[:n // 3] if n >= 3 else velocities[:1]
        last_third = velocities[-(n // 3):] if n >= 3 else velocities[-1:]
        mid = velocities[n // 3: 2 * n // 3] if n >= 3 else velocities

        if mid and first_third and last_third:
            avg_mid = sum(mid) / len(mid)
            avg_edges = (sum(first_third) + sum(last_third)) / (len(first_third) + len(last_third))
            if avg_mid > avg_edges:
                phrase_shape_score = min(100.0, phrase_shape_score + 10.0)
    else:
        phrase_shape_score = 50.0

    # ── 2. 重音合理性評估 ──
    # 檢查 velocity 的起伏是否有規律
    if n >= 4:
        diffs = [velocities[i] - velocities[i - 1] for i in range(1, n)]
        sign_changes = sum(
            1 for i in range(1, len(diffs))
            if diffs[i] * diffs[i - 1] < 0
        )
        # 適度的起伏 (不全平也不全亂跳)
        change_ratio = sign_changes / max(len(diffs) - 1, 1)
        if 0.3 <= change_ratio <= 0.7:
            accent_score = 90.0
        elif 0.2 <= change_ratio <= 0.8:
            accent_score = 70.0
        else:
            accent_score = 50.0
    else:
        accent_score = 60.0

    # ── 3. 力度變化豐富度 ──
    if n >= 2:
        vel_range = max(velocities) - min(velocities)
        std_dev = (sum((v - sum(velocities) / n) ** 2 for v in velocities) / n) ** 0.5

        # 理想: range 在 20~60, std_dev 在 8~25
        if 20 <= vel_range <= 60:
            range_score = 100.0
        elif 10 <= vel_range <= 80:
            range_score = 70.0
        else:
            range_score = 40.0

        if 8 <= std_dev <= 25:
            std_score = 100.0
        elif 4 <= std_dev <= 35:
            std_score = 70.0
        else:
            std_score = 40.0

        variety_score = (range_score + std_score) / 2.0
    else:
        variety_score = 30.0

    # ── 加權總分 ──
    total = (
        phrase_shape_score * 0.4
        + accent_score * 0.3
        + variety_score * 0.3
    )

    return {
        "total_score": round(total, 1),
        "phrase_shape_score": round(phrase_shape_score, 1),
        "accent_score": round(accent_score, 1),
        "variety_score": round(variety_score, 1),
        "details": (
            f"Events: {n}, "
            f"Velocity range: {min(velocities)}-{max(velocities)}, "
            f"Articulation types: {set(ev.get('articulation', 'none') for ev in events)}"
        ),
    }


# ──────────────────────────────────────────
# 測試
# ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("AI Dynamics & Articulation Engine — Phase 11d Test")
    print("=" * 60)

    # 模擬 8 小節旋律 (C major scale ascending + descending)
    test_events = [
        {"time": 0.0,  "pitch": 60, "duration": 0.5},
        {"time": 0.5,  "pitch": 62, "duration": 0.5},
        {"time": 1.0,  "pitch": 64, "duration": 0.5},
        {"time": 1.5,  "pitch": 65, "duration": 0.5},
        {"time": 2.0,  "pitch": 67, "duration": 0.5},
        {"time": 2.5,  "pitch": 69, "duration": 0.5},
        {"time": 3.0,  "pitch": 71, "duration": 0.5},
        {"time": 3.5,  "pitch": 72, "duration": 0.5},
        {"time": 4.0,  "pitch": 71, "duration": 0.5},
        {"time": 4.5,  "pitch": 69, "duration": 0.5},
        {"time": 5.0,  "pitch": 67, "duration": 0.5},
        {"time": 5.5,  "pitch": 65, "duration": 0.5},
        {"time": 6.0,  "pitch": 64, "duration": 0.5},
        {"time": 6.5,  "pitch": 62, "duration": 0.5},
        {"time": 7.0,  "pitch": 60, "duration": 1.0},
    ]

    for section in ["intro", "verse", "chorus", "bridge", "outro"]:
        # 深拷貝以避免重複修改
        import copy
        ev = copy.deepcopy(test_events)

        print(f"\n--- Section: {section} (energy={SECTION_ENERGY[section]}) ---")
        result = generate_dynamics(ev, bpm=120, time_sig="4/4", section_type=section)

        for e in result:
            art = e.get("articulation", "?")
            vel = e.get("velocity", "?")
            print(f"  t={e['time']:.1f}  pitch={e['pitch']:3d}  "
                  f"vel={vel:>3}  art={art:<9}  dur={e['duration']:.3f}")

        scores = evaluate_dynamics(result)
        print(f"  Score: {scores['total_score']:.1f} "
              f"(phrase={scores['phrase_shape_score']:.1f}, "
              f"accent={scores['accent_score']:.1f}, "
              f"variety={scores['variety_score']:.1f})")

    # 測試不同 BPM 的 articulation 決策
    print("\n--- Articulation BPM test ---")
    for bpm in [60, 120, 160]:
        ev = copy.deepcopy(test_events)
        result = generate_dynamics(ev, bpm=bpm, time_sig="4/4", section_type="verse")
        arts = set(e.get("articulation", "?") for e in result)
        print(f"  BPM={bpm:3d} → articulations: {arts}")

    # 測試 3/4 拍
    print("\n--- 3/4 time signature ---")
    ev_34 = copy.deepcopy(test_events[:6])
    result = generate_dynamics(ev_34, bpm=100, time_sig="3/4", section_type="verse")
    for e in result:
        print(f"  t={e['time']:.1f}  vel={e.get('velocity', '?'):>3}  "
              f"art={e.get('articulation', '?')}")

    print("\n✓ Dynamics engine test complete.")
