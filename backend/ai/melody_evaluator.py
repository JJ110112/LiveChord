"""
AI 旋律評測模組 (Melody Evaluator) — Phase 11c #2

獨立評估旋律品質的五個維度:
  2.1 音域合理性 (Tessitura Score)
  2.2 跳進/級進比例 (Intervallic Profile)
  2.3 樂句弧線 (Phrase Arc Score)
  2.4 節奏多樣性 (Rhythmic Variety)
  2.5 旋律相似度比對 (Melody Similarity via DTW)

學術依據:
  - Narmour (1990). The Analysis and Cognition of Basic Melodic Structures.
  - Temperley (2007). Music and Probability.
  - Müller (2007). Information Retrieval for Music and Motion. (DTW)
"""

import math
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter


# ==============================================================================
# 壹、常數
# ==============================================================================

# 舒適音域 (MIDI range)
TESSITURA = {
    "soprano":  (60, 79),   # C4 - G5
    "alto":     (55, 74),   # G3 - D5
    "tenor":    (48, 69),   # C3 - A4
    "bass":     (40, 62),   # E2 - D4
    "piano_rh": (55, 84),   # G3 - C6  (右手旋律常見範圍)
    "piano_lh": (36, 60),   # C2 - C4  (左手旋律常見範圍)
    "default":  (48, 84),   # C3 - C6  (通用)
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


# ==============================================================================
# 貳、評測函數
# ==============================================================================

def tessitura_score(events: List[Dict], voice: str = "default") -> Dict[str, Any]:
    """
    2.1 音域合理性評分。

    計算旋律的有效音域 (5th-95th percentile)，
    對比舒適音域，超出比例 → penalty。

    Returns:
        {
            "score": 0-100,
            "range_low": int (MIDI),
            "range_high": int (MIDI),
            "comfort_low": int,
            "comfort_high": int,
            "out_of_range_ratio": float,
        }
    """
    if not events:
        return {"score": 0, "range_low": 0, "range_high": 0,
                "comfort_low": 0, "comfort_high": 0, "out_of_range_ratio": 1.0}

    midis = [e["midi"] for e in events]
    low, high = TESSITURA.get(voice, TESSITURA["default"])

    p5 = int(np.percentile(midis, 5))
    p95 = int(np.percentile(midis, 95))

    out_count = sum(1 for m in midis if m < low or m > high)
    out_ratio = out_count / len(midis)

    # 分數: 100 - penalty
    score = max(0, 100 - int(out_ratio * 150))

    return {
        "score": score,
        "range_low": p5,
        "range_high": p95,
        "range_low_note": midi_to_name(p5),
        "range_high_note": midi_to_name(p95),
        "comfort_low": low,
        "comfort_high": high,
        "out_of_range_ratio": round(out_ratio, 3),
    }


def intervallic_profile(events: List[Dict]) -> Dict[str, Any]:
    """
    2.2 跳進/級進比例分析。

    好的旋律約 70% 級進 (stepwise, ≤2 半音), 30% 跳進。
    大跳 (>P5=7 半音) 後是否有反向級進修正 (Gestalt 原則)。

    Returns:
        {
            "score": 0-100,
            "stepwise_ratio": float,
            "skip_ratio": float,
            "leap_ratio": float,
            "large_leap_resolved": float,  # 大跳後反向解決的比例
            "interval_histogram": dict,
        }
    """
    if len(events) < 2:
        return {"score": 50, "stepwise_ratio": 0, "skip_ratio": 0,
                "leap_ratio": 0, "large_leap_resolved": 0, "interval_histogram": {}}

    midis = [e["midi"] for e in events]
    intervals = [midis[i+1] - midis[i] for i in range(len(midis) - 1)]
    abs_intervals = [abs(iv) for iv in intervals]
    n = len(intervals)

    stepwise = sum(1 for iv in abs_intervals if iv <= 2)
    skips = sum(1 for iv in abs_intervals if 3 <= iv <= 7)
    leaps = sum(1 for iv in abs_intervals if iv > 7)

    stepwise_ratio = stepwise / n
    skip_ratio = skips / n
    leap_ratio = leaps / n

    # 大跳解決率: 大跳後下一個 interval 是否反向且 ≤ 4 半音
    large_leaps = 0
    resolved = 0
    for i in range(len(intervals) - 1):
        if abs(intervals[i]) > 7:
            large_leaps += 1
            next_iv = intervals[i + 1]
            # 反向且步進
            if (intervals[i] > 0 and next_iv < 0 and abs(next_iv) <= 4) or \
               (intervals[i] < 0 and next_iv > 0 and abs(next_iv) <= 4):
                resolved += 1

    large_leap_resolved = resolved / large_leaps if large_leaps > 0 else 1.0

    # Interval histogram
    hist = Counter(abs_intervals)
    histogram = {str(k): v for k, v in sorted(hist.items())}

    # 評分: 理想 stepwise ~70%, 大跳解決率 > 70%
    score = 100
    score -= abs(stepwise_ratio - 0.7) * 80
    score -= (1 - large_leap_resolved) * 30
    score -= leap_ratio * 40  # 太多大跳扣分
    score = max(0, min(100, int(score)))

    return {
        "score": score,
        "stepwise_ratio": round(stepwise_ratio, 3),
        "skip_ratio": round(skip_ratio, 3),
        "leap_ratio": round(leap_ratio, 3),
        "large_leap_resolved": round(large_leap_resolved, 3),
        "interval_histogram": histogram,
    }


def phrase_arc_score(events: List[Dict], silence_threshold: float = 0.3) -> Dict[str, Any]:
    """
    2.3 樂句弧線評分。

    偵測樂句邊界 (靜音 > threshold)，
    每個樂句的音高輪廓是否呈 arch shape (上行→高點→下行)。

    References:
      Narmour (1990) Implication-Realization model:
      旋律傾向回到起點附近，高點在黃金比例 (~0.618) 處。

    Returns:
        {
            "score": 0-100,
            "num_phrases": int,
            "phrase_details": [{
                "start": float, "end": float, "num_notes": int,
                "peak_position": float, "arch_score": float
            }]
        }
    """
    if len(events) < 3:
        return {"score": 50, "num_phrases": 0, "phrase_details": []}

    # 切割樂句 (靜音 OR 超過最大長度自動切割)
    MAX_PHRASE_NOTES = 16  # 典型樂句 4-8 小節 ≈ 8-16 個音符
    phrases = []
    current_phrase = [events[0]]
    for i in range(1, len(events)):
        gap = events[i]["start"] - events[i-1]["end"]
        phrase_too_long = len(current_phrase) >= MAX_PHRASE_NOTES
        if gap >= silence_threshold or phrase_too_long:
            if len(current_phrase) >= 3:
                phrases.append(current_phrase)
            current_phrase = []
        current_phrase.append(events[i])
    if len(current_phrase) >= 3:
        phrases.append(current_phrase)

    if not phrases:
        return {"score": 50, "num_phrases": 0, "phrase_details": []}

    phrase_details = []
    total_arch_score = 0.0

    for phrase in phrases:
        midis = [e["midi"] for e in phrase]
        n = len(midis)

        # 找高點位置 (相對 0~1)
        peak_idx = np.argmax(midis)
        peak_pos = peak_idx / (n - 1) if n > 1 else 0.5

        # Arch score: 高點接近黃金比例 + 結尾回到起點附近
        # 理想高點: 0.618
        peak_deviation = abs(peak_pos - 0.618)
        peak_score = max(0, 1.0 - peak_deviation * 2)

        # 結尾是否回到起點附近 (±5 半音)
        return_deviation = abs(midis[-1] - midis[0])
        return_score = max(0, 1.0 - return_deviation / 12)

        # 上升-下降趨勢
        first_half = midis[:n//2+1]
        second_half = midis[n//2:]
        ascending = sum(1 for i in range(len(first_half)-1) if first_half[i+1] >= first_half[i])
        descending = sum(1 for i in range(len(second_half)-1) if second_half[i+1] <= second_half[i])
        trend_score = 0.0
        if len(first_half) > 1 and len(second_half) > 1:
            asc_ratio = ascending / (len(first_half) - 1)
            desc_ratio = descending / (len(second_half) - 1)
            trend_score = (asc_ratio + desc_ratio) / 2

        arch = (peak_score * 0.4 + return_score * 0.3 + trend_score * 0.3) * 100
        total_arch_score += arch

        phrase_details.append({
            "start": phrase[0]["start"],
            "end": phrase[-1]["end"],
            "num_notes": n,
            "peak_position": round(peak_pos, 3),
            "arch_score": round(arch, 1),
        })

    avg_score = total_arch_score / len(phrases)

    return {
        "score": int(min(100, max(0, avg_score))),
        "num_phrases": len(phrases),
        "phrase_details": phrase_details,
    }


def rhythmic_variety(events: List[Dict]) -> Dict[str, Any]:
    """
    2.4 節奏多樣性評分。

    統計 IOI (Inter-Onset Interval) 的 entropy。
    太單調 (全是八分音符) → 低分
    太混亂 (無週期性) → 低分
    最佳: 2-4 種主要節奏型態

    Returns:
        {
            "score": 0-100,
            "ioi_entropy": float,
            "num_distinct_durations": int,
            "dominant_durations": list,
        }
    """
    if len(events) < 3:
        return {"score": 50, "ioi_entropy": 0, "num_distinct_durations": 0,
                "dominant_durations": []}

    # 計算 IOI (onset 間距)
    iois = [events[i+1]["start"] - events[i]["start"]
            for i in range(len(events) - 1)]
    iois = [max(0.01, ioi) for ioi in iois]  # 避免零值

    # 量化 IOI 到最近的 0.05s (避免浮點雜訊)
    quantized = [round(ioi / 0.05) * 0.05 for ioi in iois]

    # 統計分布
    counter = Counter(quantized)
    total = sum(counter.values())
    probs = [c / total for c in counter.values()]

    # Shannon entropy
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)

    # 主要時值 (出現超過 10% 的)
    dominant = sorted(
        [(dur, cnt / total) for dur, cnt in counter.items() if cnt / total > 0.1],
        key=lambda x: -x[1]
    )

    num_distinct = len(counter)

    # 評分邏輯:
    # - entropy 太低 (< 1.0): 太單調
    # - entropy 太高 (> 3.5): 太混亂
    # - 最佳 2-4 種主要時值
    score = 100
    if entropy < 1.0:
        score -= int((1.0 - entropy) * 40)
    elif entropy > 3.5:
        score -= int((entropy - 3.5) * 20)

    if num_distinct == 1:
        score -= 20  # 完全單一節奏
    elif num_distinct > 8:
        score -= 15  # 過度碎裂

    score = max(0, min(100, score))

    return {
        "score": score,
        "ioi_entropy": round(entropy, 3),
        "num_distinct_durations": num_distinct,
        "dominant_durations": [
            {"duration": round(d, 3), "ratio": round(r, 3)} for d, r in dominant
        ],
    }


def melody_similarity_dtw(
    events_a: List[Dict],
    events_b: List[Dict],
    mode: str = "pitch",
) -> Dict[str, Any]:
    """
    2.5 旋律相似度比對 (DTW)。

    mode:
      "pitch":  Pitch-class DTW (忽略八度差異)
      "rhythm": Rhythm DTW (忽略音高, 只比 IOI)
      "full":   Full MIDI DTW (考慮音高+八度)

    Returns:
        {
            "distance": float,
            "normalized_distance": float,  # 0~1, 越低越相似
            "similarity": float,           # 0~1, 越高越相似
            "mode": str,
        }
    """
    if not events_a or not events_b:
        return {"distance": float("inf"), "normalized_distance": 1.0,
                "similarity": 0.0, "mode": mode}

    if mode == "pitch":
        seq_a = [e["midi"] % 12 for e in events_a]
        seq_b = [e["midi"] % 12 for e in events_b]
        max_cost = 6  # 最大 pitch-class 距離 (tritone)
    elif mode == "rhythm":
        seq_a = [events_a[i+1]["start"] - events_a[i]["start"]
                 for i in range(len(events_a) - 1)]
        seq_b = [events_b[i+1]["start"] - events_b[i]["start"]
                 for i in range(len(events_b) - 1)]
        if not seq_a or not seq_b:
            return {"distance": float("inf"), "normalized_distance": 1.0,
                    "similarity": 0.0, "mode": mode}
        max_cost = max(max(seq_a), max(seq_b)) if seq_a and seq_b else 1.0
    else:  # full
        seq_a = [e["midi"] for e in events_a]
        seq_b = [e["midi"] for e in events_b]
        max_cost = 24  # 兩個八度

    # DTW computation
    n, m = len(seq_a), len(seq_b)
    dtw = np.full((n + 1, m + 1), float("inf"))
    dtw[0][0] = 0.0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if mode == "pitch":
                d = min(abs(seq_a[i-1] - seq_b[j-1]),
                        12 - abs(seq_a[i-1] - seq_b[j-1]))
            elif mode == "rhythm":
                d = abs(seq_a[i-1] - seq_b[j-1])
            else:
                d = abs(seq_a[i-1] - seq_b[j-1])
            dtw[i][j] = d + min(dtw[i-1][j], dtw[i][j-1], dtw[i-1][j-1])

    distance = float(dtw[n][m])
    path_len = n + m  # approximate path length
    norm_dist = min(1.0, distance / (path_len * max_cost)) if path_len > 0 and max_cost > 0 else 1.0

    return {
        "distance": round(distance, 3),
        "normalized_distance": round(norm_dist, 3),
        "similarity": round(1.0 - norm_dist, 3),
        "mode": mode,
    }


# ==============================================================================
# 參、綜合評測
# ==============================================================================

def evaluate_melody(
    events: List[Dict],
    voice: str = "default",
    reference: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    綜合旋律評測。

    Args:
        events: 旋律事件列表 [{start, end, note, midi, confidence?}, ...]
        voice: 聲部類型 (soprano/alto/tenor/bass/piano_rh/piano_lh/default)
        reference: (可選) 參照旋律，用於相似度比對

    Returns:
        {
            "overall_score": 0-100,
            "tessitura": {...},
            "intervals": {...},
            "phrase_arc": {...},
            "rhythm": {...},
            "similarity": {...} | None,
            "confidence_stats": {...} | None,
        }
    """
    result = {
        "tessitura": tessitura_score(events, voice),
        "intervals": intervallic_profile(events),
        "phrase_arc": phrase_arc_score(events),
        "rhythm": rhythmic_variety(events),
        "similarity": None,
        "confidence_stats": None,
    }

    # 相似度比對
    if reference:
        result["similarity"] = {
            "pitch": melody_similarity_dtw(events, reference, mode="pitch"),
            "rhythm": melody_similarity_dtw(events, reference, mode="rhythm"),
        }

    # 置信度統計 (如果 events 有 confidence 欄位)
    confs = [e.get("confidence") for e in events if e.get("confidence") is not None]
    if confs:
        result["confidence_stats"] = {
            "mean": round(float(np.mean(confs)), 3),
            "min": round(float(np.min(confs)), 3),
            "max": round(float(np.max(confs)), 3),
            "low_confidence_ratio": round(
                sum(1 for c in confs if c < 0.5) / len(confs), 3
            ),
        }

    # 綜合分數 (加權平均)
    weights = {
        "tessitura": 0.2,
        "intervals": 0.3,
        "phrase_arc": 0.3,
        "rhythm": 0.2,
    }
    overall = sum(result[k]["score"] * w for k, w in weights.items())

    # 低置信度懲罰
    if result["confidence_stats"] and result["confidence_stats"]["low_confidence_ratio"] > 0.3:
        penalty = result["confidence_stats"]["low_confidence_ratio"] * 15
        overall = max(0, overall - penalty)

    result["overall_score"] = int(min(100, max(0, overall)))

    return result


# ==============================================================================
# 肆、測試
# ==============================================================================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import json

    # 模擬一段 C 大調旋律 (Mary Had a Little Lamb)
    test_melody = [
        {"start": 0.0,  "end": 0.5,  "note": "E4", "midi": 64, "confidence": 0.9},
        {"start": 0.5,  "end": 1.0,  "note": "D4", "midi": 62, "confidence": 0.85},
        {"start": 1.0,  "end": 1.5,  "note": "C4", "midi": 60, "confidence": 0.88},
        {"start": 1.5,  "end": 2.0,  "note": "D4", "midi": 62, "confidence": 0.87},
        {"start": 2.0,  "end": 2.5,  "note": "E4", "midi": 64, "confidence": 0.91},
        {"start": 2.5,  "end": 3.0,  "note": "E4", "midi": 64, "confidence": 0.89},
        {"start": 3.0,  "end": 4.0,  "note": "E4", "midi": 64, "confidence": 0.92},
        # 靜音 0.5s → 新樂句
        {"start": 4.5,  "end": 5.0,  "note": "D4", "midi": 62, "confidence": 0.86},
        {"start": 5.0,  "end": 5.5,  "note": "D4", "midi": 62, "confidence": 0.84},
        {"start": 5.5,  "end": 6.5,  "note": "D4", "midi": 62, "confidence": 0.88},
        {"start": 7.0,  "end": 7.5,  "note": "E4", "midi": 64, "confidence": 0.90},
        {"start": 7.5,  "end": 8.0,  "note": "G4", "midi": 67, "confidence": 0.87},
        {"start": 8.0,  "end": 9.0,  "note": "G4", "midi": 67, "confidence": 0.91},
    ]

    print("=" * 60)
    print("Melody Evaluator 旋律評測測試")
    print("=" * 60)

    result = evaluate_melody(test_melody, voice="piano_rh")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"\n綜合分數: {result['overall_score']}/100")

    # DTW 測試
    print("\n--- DTW 相似度測試 ---")
    reference = test_melody[:7]  # 只比前半段
    sim = melody_similarity_dtw(test_melody, reference, mode="pitch")
    print(f"Pitch similarity: {sim['similarity']:.3f}")
