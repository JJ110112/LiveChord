"""
AI 伴奏評測模組 (Accompaniment Evaluator) — Phase 11c #4

評測維度:
  4.1 旋律碰撞率 (Melody Collision Score)
  4.2 Voice Leading Smoothness
  4.3 音域平衡 (Register Balance)
  4.4 節奏密度合理性
  4.5 和聲功能完整度

學術依據:
  - Huron (2001). "Tone and Voice." Music Perception, 19(1).
  - Parncutt (1989). Harmony: A Psychoacoustical Approach. (Roughness model)
  - Piston (1987). Harmony, 5th ed. (Voice leading rules)
"""

import math
from typing import List, Dict, Any, Optional
from collections import Counter


# ==============================================================================
# 壹、常數
# ==============================================================================

LH_LOW, LH_HIGH = 36, 59   # C2-B3
RH_LOW, RH_HIGH = 60, 84   # C4-C6

# 和弦最少需要的組成音 (root=0 可省, 3rd 不可省, 5th 可省, 7th 視情況)
ESSENTIAL_INTERVALS = {
    "":     {0, 4},       # root, 3rd (major)
    "m":    {0, 3},       # root, b3
    "7":    {4, 10},      # 3rd, b7 (root 可由 bass 提供)
    "m7":   {3, 10},      # b3, b7
    "maj7": {4, 11},      # 3rd, 7th
    "dim":  {0, 3, 6},
    "aug":  {0, 4, 8},
}


# ==============================================================================
# 貳、評測函數
# ==============================================================================

def melody_collision_score(
    accompaniment: List[Dict],
    melody: List[Dict],
) -> Dict[str, Any]:
    """
    4.1 旋律碰撞率。

    伴奏音與旋律音在同一時間點、同一音高 (或相距 ≤1 半音) → collision。

    Returns:
        {
            "score": 0-100 (越高越好, 代表無碰撞),
            "collision_count": int,
            "total_accompaniment_notes": int,
            "collision_ratio": float,
            "severe_collisions": [{time, acc_midi, melody_midi, distance}]
        }
    """
    if not accompaniment or not melody:
        return {"score": 100, "collision_count": 0,
                "total_accompaniment_notes": len(accompaniment),
                "collision_ratio": 0.0, "severe_collisions": []}

    # 建立旋律的時間→pitch 查找表
    melody_at_time = {}
    for m in melody:
        start = m.get("start", m.get("time", 0))
        end = m.get("end", start + 0.5)
        midi = m["midi"]
        # 量化到 0.05s 精度
        t = start
        while t < end:
            t_key = round(t, 2)
            melody_at_time[t_key] = midi
            t += 0.05

    collisions = 0
    severe = []

    for acc in accompaniment:
        acc_time = acc.get("time", 0)
        acc_midi = acc.get("pitch", acc.get("midi", 0))
        acc_dur = acc.get("duration", 0.5)

        # 檢查伴奏音存續期間是否與旋律碰撞
        t = acc_time
        found_collision = False
        while t < acc_time + acc_dur and not found_collision:
            t_key = round(t, 2)
            mel_midi = melody_at_time.get(t_key)
            if mel_midi is not None:
                distance = abs(acc_midi - mel_midi)
                # 同八度內同音 = 碰撞 (半音差 ≤1 且非八度重疊)
                if distance <= 1:
                    collisions += 1
                    found_collision = True
                    if distance <= 1:  # 嚴重碰撞 (非八度)
                        severe.append({
                            "time": round(acc_time, 3),
                            "acc_midi": acc_midi,
                            "melody_midi": mel_midi,
                            "distance": distance,
                        })
            t += 0.05

    total = len(accompaniment)
    ratio = collisions / total if total > 0 else 0.0

    # 分數: 碰撞率 < 5% = 滿分, > 20% = 0 分
    score = max(0, min(100, int(100 - ratio * 500)))

    return {
        "score": score,
        "collision_count": collisions,
        "total_accompaniment_notes": total,
        "collision_ratio": round(ratio, 3),
        "severe_collisions": severe[:10],  # 最多顯示 10 個
    }


def voice_leading_smoothness(
    events: List[Dict],
    chords: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    4.2 Voice Leading Smoothness。

    只在和弦轉換邊界計算聲部移動距離（忽略同和弦內的琶音跳躍）。
    如果無和弦資料，fallback 到每 2 秒取一個快照。

    Returns:
        {
            "score": 0-100,
            "avg_movement": float (半音),
            "max_movement": float,
            "parallel_fifths": int,
            "parallel_octaves": int,
        }
    """
    if len(events) < 2:
        return {"score": 100, "avg_movement": 0, "max_movement": 0,
                "parallel_fifths": 0, "parallel_octaves": 0}

    # 確定和弦邊界時間點
    boundary_times = set()
    if chords and len(chords) > 1:
        for c in chords:
            boundary_times.add(round(c.get("time", 0), 2))
    else:
        # Fallback: 每 2 秒一個快照
        all_times = [e.get("time", 0) for e in events]
        if all_times:
            t = min(all_times)
            t_max = max(all_times)
            while t <= t_max:
                boundary_times.add(round(t, 2))
                t += 2.0

    if len(boundary_times) < 2:
        return {"score": 100, "avg_movement": 0, "max_movement": 0,
                "parallel_fifths": 0, "parallel_octaves": 0}

    # 在每個邊界時間點取一個 pitch snapshot（該和弦區段的所有不同 pitch）
    sorted_boundaries = sorted(boundary_times)
    snapshots = []
    for i, bt in enumerate(sorted_boundaries):
        next_bt = sorted_boundaries[i + 1] if i + 1 < len(sorted_boundaries) else bt + 10
        pitches = sorted(set(
            e.get("pitch", e.get("midi", 60))
            for e in events
            if bt <= e.get("time", 0) < next_bt
        ))
        if pitches:
            snapshots.append(pitches)

    if len(snapshots) < 2:
        return {"score": 100, "avg_movement": 0, "max_movement": 0,
                "parallel_fifths": 0, "parallel_octaves": 0}

    movements = []
    parallel_5 = 0
    parallel_8 = 0

    for i in range(len(snapshots) - 1):
        curr = snapshots[i]
        nxt = snapshots[i + 1]
        n = min(len(curr), len(nxt))
        if n == 0:
            continue

        for j in range(n):
            mv = abs(nxt[j] - curr[j])
            movements.append(mv)

        if n >= 2:
            for j in range(n - 1):
                for k in range(j + 1, n):
                    iv_c = abs(curr[k] - curr[j]) % 12
                    iv_n = abs(nxt[k] - nxt[j]) % 12
                    mv_j = nxt[j] - curr[j]
                    mv_k = nxt[k] - curr[k]
                    if mv_j * mv_k > 0:
                        if iv_c == 7 and iv_n == 7:
                            parallel_5 += 1
                        if iv_c == 0 and iv_n == 0 and mv_j != 0:
                            parallel_8 += 1

    avg_mv = sum(movements) / len(movements) if movements else 0
    max_mv = max(movements) if movements else 0

    # 分數: avg ≤ 4 半音 = 滿分
    score = 100
    if avg_mv > 4:
        score -= int((avg_mv - 4) * 10)
    # 平行五八度在流行/搖滾中常見，只輕微扣分
    score -= min(parallel_5, 10) * 2
    score -= min(parallel_8, 5) * 3
    score = max(0, min(100, score))

    return {
        "score": score,
        "avg_movement": round(avg_mv, 2),
        "max_movement": max_mv,
        "parallel_fifths": parallel_5,
        "parallel_octaves": parallel_8,
    }


def register_balance(
    left_events: List[Dict],
    right_events: List[Dict],
) -> Dict[str, Any]:
    """
    4.3 音域平衡評分。

    - 左手: C2-B3 (36-59), 超出 → muddy 或 thin
    - 右手: C4-C6 (60-84), 超出 → 與旋律打架
    - 左右手間距: 最佳 8-15 半音
    - 低音區和弦密度不超過 3 音 (避免 mud)

    Returns:
        {
            "score": 0-100,
            "lh_out_of_range": float,
            "rh_out_of_range": float,
            "avg_hand_gap": float,
            "low_register_density_violations": int,
        }
    """
    lh_pitches = [e.get("pitch", e.get("midi", 48)) for e in left_events]
    rh_pitches = [e.get("pitch", e.get("midi", 72)) for e in right_events]

    # 左手超出比例
    lh_out = sum(1 for p in lh_pitches if p < LH_LOW or p > LH_HIGH)
    lh_out_ratio = lh_out / len(lh_pitches) if lh_pitches else 0

    # 右手超出比例
    rh_out = sum(1 for p in rh_pitches if p < RH_LOW or p > RH_HIGH)
    rh_out_ratio = rh_out / len(rh_pitches) if rh_pitches else 0

    # 左右手間距
    hand_gaps = []
    lh_by_time = {}
    for e in left_events:
        t = round(e.get("time", 0), 3)
        pitch = e.get("pitch", e.get("midi", 48))
        lh_by_time.setdefault(t, []).append(pitch)

    for e in right_events:
        t = round(e.get("time", 0), 3)
        rh_pitch = e.get("pitch", e.get("midi", 72))
        lh_at_t = lh_by_time.get(t)
        if lh_at_t:
            lh_top = max(lh_at_t)
            gap = rh_pitch - lh_top
            hand_gaps.append(gap)

    avg_gap = sum(hand_gaps) / len(hand_gaps) if hand_gaps else 12

    # 低音區密度: pitch < 55 的同時刻音符數
    low_density_violations = 0
    lh_time_groups = {}
    for e in left_events:
        t = round(e.get("time", 0), 3)
        p = e.get("pitch", e.get("midi", 48))
        if p < 55:
            lh_time_groups.setdefault(t, []).append(p)
    for t, pitches in lh_time_groups.items():
        if len(pitches) > 3:
            low_density_violations += 1

    # 評分
    score = 100
    score -= int(lh_out_ratio * 60)
    score -= int(rh_out_ratio * 60)
    if avg_gap < 8:
        score -= int((8 - avg_gap) * 5)
    elif avg_gap > 20:
        score -= int((avg_gap - 20) * 3)
    score -= low_density_violations * 5
    score = max(0, min(100, score))

    return {
        "score": score,
        "lh_out_of_range": round(lh_out_ratio, 3),
        "rh_out_of_range": round(rh_out_ratio, 3),
        "avg_hand_gap": round(avg_gap, 1),
        "low_register_density_violations": low_density_violations,
    }


def rhythm_density_score(
    events: List[Dict],
    bpm: int = 120,
) -> Dict[str, Any]:
    """
    4.4 節奏密度合理性。

    計算每拍的 note density, 與 BPM 對照。

    Returns:
        {
            "score": 0-100,
            "avg_notes_per_beat": float,
            "max_notes_per_beat": int,
            "bpm": int,
            "too_dense": bool,
        }
    """
    if not events:
        return {"score": 50, "avg_notes_per_beat": 0,
                "max_notes_per_beat": 0, "bpm": bpm, "too_dense": False}

    beat_dur = 60.0 / bpm
    times = [e.get("time", 0) for e in events]
    if not times:
        return {"score": 50, "avg_notes_per_beat": 0,
                "max_notes_per_beat": 0, "bpm": bpm, "too_dense": False}

    min_t = min(times)
    max_t = max(times)
    total_beats = max(1, (max_t - min_t) / beat_dur)

    # 每拍音符數
    beat_counts = Counter()
    for t in times:
        beat_idx = int((t - min_t) / beat_dur)
        beat_counts[beat_idx] += 1

    avg_npb = len(events) / total_beats
    max_npb = max(beat_counts.values()) if beat_counts else 0

    # BPM 越高, 容許的密度越低
    max_acceptable = 8 if bpm < 80 else (6 if bpm < 120 else (4 if bpm < 160 else 3))
    too_dense = max_npb > max_acceptable

    score = 100
    if too_dense:
        score -= int((max_npb - max_acceptable) * 10)
    if avg_npb < 0.5:
        score -= 20  # 太稀疏
    score = max(0, min(100, score))

    return {
        "score": score,
        "avg_notes_per_beat": round(avg_npb, 2),
        "max_notes_per_beat": max_npb,
        "bpm": bpm,
        "too_dense": too_dense,
    }


def harmonic_completeness(
    events: List[Dict],
    chord_name: str = "",
) -> Dict[str, Any]:
    """
    4.5 和聲功能完整度。

    檢查伴奏是否彈出了和弦的必要組成音。

    Returns:
        {
            "score": 0-100,
            "pitch_classes_played": list,
            "missing_essential": list,
            "chord_name": str,
        }
    """
    if not events or not chord_name:
        return {"score": 50, "pitch_classes_played": [],
                "missing_essential": [], "chord_name": chord_name}

    # 取所有 pitch class
    pcs = set()
    for e in events:
        p = e.get("pitch", e.get("midi", 60))
        pcs.add(p % 12)

    # 解析和弦根音
    from .preprocess import parse_chord_name
    root_semi, quality = parse_chord_name(chord_name)
    if root_semi is None:
        return {"score": 50, "pitch_classes_played": sorted(pcs),
                "missing_essential": [], "chord_name": chord_name}

    essential = ESSENTIAL_INTERVALS.get(quality, ESSENTIAL_INTERVALS.get("", {0, 4}))
    expected_pcs = {(root_semi + iv) % 12 for iv in essential}

    missing = expected_pcs - pcs
    completeness = 1.0 - len(missing) / len(expected_pcs) if expected_pcs else 1.0

    score = int(completeness * 100)

    return {
        "score": score,
        "pitch_classes_played": sorted(pcs),
        "missing_essential": sorted(missing),
        "chord_name": chord_name,
    }


# ==============================================================================
# 參、綜合評測
# ==============================================================================

def evaluate_accompaniment(
    left_hand: List[Dict],
    right_hand: List[Dict],
    melody: Optional[List[Dict]] = None,
    bpm: int = 120,
    chords: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    綜合伴奏評測。

    Args:
        left_hand: [{time, pitch, duration, finger?, velocity?}, ...]
        right_hand: same format
        melody: [{start, end, midi}, ...] 用於碰撞檢測
        bpm: 曲速
        chords: [{time, end, chord}, ...] 用於和聲完整度

    Returns:
        {
            "overall_score": 0-100,
            "collision": {...},
            "voice_leading": {...},
            "register": {...},
            "density": {...},
            "harmony": {...},
        }
    """
    all_events = left_hand + right_hand

    result = {}

    # 4.1 碰撞
    if melody:
        result["collision"] = melody_collision_score(all_events, melody)
    else:
        result["collision"] = {"score": 100, "collision_count": 0,
                               "total_accompaniment_notes": len(all_events),
                               "collision_ratio": 0.0, "severe_collisions": []}

    # 4.2 Voice leading
    result["voice_leading"] = voice_leading_smoothness(all_events, chords=chords)

    # 4.3 音域平衡
    result["register"] = register_balance(left_hand, right_hand)

    # 4.4 節奏密度
    result["density"] = rhythm_density_score(all_events, bpm)

    # 4.5 和聲完整度 (取第一個和弦做代表)
    if chords and chords[0].get("chord"):
        first_chord = chords[0]["chord"]
        # 取第一個和弦時間段的 events
        t_start = chords[0].get("time", 0)
        t_end = chords[0].get("end", t_start + 2)
        chord_events = [e for e in all_events
                        if t_start <= e.get("time", 0) < t_end]
        result["harmony"] = harmonic_completeness(chord_events, first_chord)
    else:
        result["harmony"] = {"score": 50, "pitch_classes_played": [],
                             "missing_essential": [], "chord_name": ""}

    # 綜合分數
    weights = {
        "collision": 0.25,
        "voice_leading": 0.25,
        "register": 0.2,
        "density": 0.15,
        "harmony": 0.15,
    }
    overall = sum(result[k]["score"] * w for k, w in weights.items())
    result["overall_score"] = int(min(100, max(0, overall)))

    return result


# ==============================================================================
# 肆、測試
# ==============================================================================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import json

    # 模擬伴奏
    lh = [
        {"time": 0.0, "pitch": 48, "duration": 1.0, "finger": 5},  # C3
        {"time": 0.5, "pitch": 52, "duration": 0.5, "finger": 3},  # E3
        {"time": 1.0, "pitch": 55, "duration": 0.5, "finger": 1},  # G3
        {"time": 2.0, "pitch": 45, "duration": 1.0, "finger": 5},  # A2
        {"time": 2.5, "pitch": 48, "duration": 0.5, "finger": 3},  # C3
        {"time": 3.0, "pitch": 52, "duration": 0.5, "finger": 1},  # E3
    ]
    rh = [
        {"time": 0.0, "pitch": 67, "duration": 1.0, "finger": 3},  # G4
        {"time": 1.0, "pitch": 64, "duration": 1.0, "finger": 1},  # E4
        {"time": 2.0, "pitch": 69, "duration": 1.0, "finger": 3},  # A4
        {"time": 3.0, "pitch": 67, "duration": 1.0, "finger": 2},  # G4
    ]
    melody = [
        {"start": 0.0, "end": 1.0, "midi": 72},  # C5
        {"start": 1.0, "end": 2.0, "midi": 71},  # B4
        {"start": 2.0, "end": 3.0, "midi": 69},  # A4
        {"start": 3.0, "end": 4.0, "midi": 67},  # G4
    ]
    chords = [
        {"time": 0.0, "end": 2.0, "chord": "C"},
        {"time": 2.0, "end": 4.0, "chord": "Am"},
    ]

    print("=" * 60)
    print("Accompaniment Evaluator 伴奏評測測試")
    print("=" * 60)

    result = evaluate_accompaniment(lh, rh, melody, bpm=120, chords=chords)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n綜合分數: {result['overall_score']}/100")
