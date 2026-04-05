"""
AI Sustain Pedal Advisor: 踏板建議生成器
Phase 11d

根據和弦進行與段落上下文，自動生成延音踏板建議。
支援三種踏板模式: Legato / Rhythmic / Half-pedal。

學術依據:
  - Banowetz (1985). "The Pianist's Guide to Pedaling." Indiana University Press.
  - Schnabel (1942). Pedal technique in Classical/Romantic repertoire.

踏板事件格式:
  {"start": float (秒), "end": float (秒), "type": "sustain", "depth": 0.0-1.0}
  depth: 1.0 = 全踩, 0.5 = 半踏板
"""

import math
from typing import List, Dict, Any, Optional


# ──────────────────────────────────────────
# 踏板類型常數
# ──────────────────────────────────────────

PEDAL_STYLES = {
    "legato":   {"desc": "release-depress at chord changes", "default_depth": 1.0},
    "rhythmic": {"desc": "beat-aligned pedaling",            "default_depth": 1.0},
    "half":     {"desc": "partial sustain for clarity",      "default_depth": 0.5},
}

# 段落結尾漸放延長倍率
SECTION_END_SUSTAIN_MULTIPLIER = 1.5
# 快速音群門檻 (每秒音符數)
FAST_PASSAGE_THRESHOLD = 6.0
# 最小踏板持續時間 (秒)
MIN_PEDAL_DURATION = 0.1


# ──────────────────────────────────────────
# 輔助工具
# ──────────────────────────────────────────

def _beat_duration(bpm: float) -> float:
    """一拍的秒數"""
    return 60.0 / max(bpm, 1)


def _chord_start_sec(chord: Dict, bpm: float) -> float:
    """取得和弦的起始秒數 (支援 'time' 或 'start' 欄位)"""
    if "start" in chord:
        return float(chord["start"])
    if "time" in chord:
        return float(chord["time"])
    # 如果有 beat 欄位，轉換成秒
    if "beat" in chord:
        return float(chord["beat"]) * _beat_duration(bpm)
    return 0.0


def _chord_end_sec(chord: Dict, bpm: float, default_beats: float = 2.0) -> float:
    """取得和弦的結束秒數"""
    start = _chord_start_sec(chord, bpm)
    if "end" in chord:
        return float(chord["end"])
    if "duration" in chord:
        return start + float(chord["duration"])
    if "beats" in chord:
        return start + float(chord["beats"]) * _beat_duration(bpm)
    return start + default_beats * _beat_duration(bpm)


def _chord_label(chord: Dict) -> str:
    """取得和弦標記"""
    return chord.get("chord", chord.get("label", "N"))


def _bass_note(chord: Dict) -> Optional[str]:
    """取得低音 (若有 slash chord 則取斜線後面)"""
    label = _chord_label(chord)
    if "/" in label:
        return label.split("/")[-1]
    # 取根音
    root = ""
    for ch in label:
        if ch in "ABCDEFG":
            root = ch
        elif ch in "#b" and root:
            root += ch
        else:
            break
    return root if root else None


def _is_fast_passage(melody: List[Dict], start: float, end: float) -> bool:
    """判斷此區間是否為快速音群"""
    if not melody or end <= start:
        return False
    notes_in_range = [n for n in melody
                      if start <= n.get("time", n.get("start", 0)) < end]
    duration = end - start
    if duration <= 0:
        return False
    density = len(notes_in_range) / duration
    return density >= FAST_PASSAGE_THRESHOLD


def _is_section_ending(chord: Dict, chords: List[Dict], idx: int) -> bool:
    """判斷是否為段落結尾 (最後一個和弦，或有 section_end 標記)"""
    if chord.get("section_end", False):
        return True
    if idx == len(chords) - 1:
        return True
    return False


# ──────────────────────────────────────────
# 踏板生成: Legato 模式
# ──────────────────────────────────────────

def _generate_legato(chords: List[Dict], melody: Optional[List[Dict]],
                     bpm: float) -> List[Dict[str, Any]]:
    """
    Legato 踏板: 在和弦變換處 release-depress。
    規則:
      - 和弦變換 → 必須換踏板 (避免不協和堆積)
      - 低音變換 → 必須換踏板
      - 快速音群 → 不踩踏板
      - 段落結尾 → 延長踏板 + 漸放
    """
    events = []
    beat_dur = _beat_duration(bpm)

    for i, chord in enumerate(chords):
        start = _chord_start_sec(chord, bpm)
        end = _chord_end_sec(chord, bpm)
        duration_beats = (end - start) / beat_dur if beat_dur > 0 else 0

        # 快速音群: 跳過踏板
        if melody and _is_fast_passage(melody, start, end):
            continue

        # 和弦持續不到 2 拍: 不建議延音踏板 (除非段落結尾)
        is_ending = _is_section_ending(chord, chords, i)
        if duration_beats < 2.0 and not is_ending:
            continue

        depth = 1.0

        # 段落結尾: 延長 + 漸放 (depth 從 1.0 → 0.3)
        if is_ending:
            end = start + (end - start) * SECTION_END_SUSTAIN_MULTIPLIER
            depth = 1.0  # 全踩, 評估時 gradual release 另外處理

        # Legato 精髓: 踏板稍微提前放開再踩下 (模擬 syncopated pedaling)
        # 在和弦起始點前 ~30ms release, 起始點踩下
        pedal_start = max(0.0, start - 0.03)
        pedal_end = end

        if pedal_end - pedal_start >= MIN_PEDAL_DURATION:
            events.append({
                "start": round(pedal_start, 4),
                "end": round(pedal_end, 4),
                "type": "sustain",
                "depth": depth,
            })

    return events


# ──────────────────────────────────────────
# 踏板生成: Rhythmic 模式
# ──────────────────────────────────────────

def _generate_rhythmic(chords: List[Dict], melody: Optional[List[Dict]],
                       bpm: float) -> List[Dict[str, Any]]:
    """
    Rhythmic 踏板: 以節拍對齊。
    每一拍踩放一次，和弦變換時必定換踏板。
    """
    events = []
    beat_dur = _beat_duration(bpm)

    for i, chord in enumerate(chords):
        start = _chord_start_sec(chord, bpm)
        end = _chord_end_sec(chord, bpm)

        # 快速音群: 跳過
        if melody and _is_fast_passage(melody, start, end):
            continue

        # 以拍為單位切割踏板
        t = start
        while t < end - MIN_PEDAL_DURATION:
            pedal_end = min(t + beat_dur, end)
            if pedal_end - t >= MIN_PEDAL_DURATION:
                events.append({
                    "start": round(t, 4),
                    "end": round(pedal_end, 4),
                    "type": "sustain",
                    "depth": 1.0,
                })
            t += beat_dur

    return events


# ──────────────────────────────────────────
# 踏板生成: Half-pedal 模式
# ──────────────────────────────────────────

def _generate_half_pedal(chords: List[Dict], melody: Optional[List[Dict]],
                         bpm: float) -> List[Dict[str, Any]]:
    """
    Half-pedal: 部分延音，增加清晰度。
    depth=0.5，適合快速和弦進行或需要透明度的段落。
    """
    events = []
    beat_dur = _beat_duration(bpm)

    for i, chord in enumerate(chords):
        start = _chord_start_sec(chord, bpm)
        end = _chord_end_sec(chord, bpm)

        # 快速音群: 跳過
        if melody and _is_fast_passage(melody, start, end):
            continue

        depth = 0.5
        is_ending = _is_section_ending(chord, chords, i)

        # 段落結尾用全踏板
        if is_ending:
            depth = 1.0
            end = start + (end - start) * SECTION_END_SUSTAIN_MULTIPLIER

        if end - start >= MIN_PEDAL_DURATION:
            events.append({
                "start": round(start, 4),
                "end": round(end, 4),
                "type": "sustain",
                "depth": depth,
            })

    return events


# ──────────────────────────────────────────
# 主要 API
# ──────────────────────────────────────────

def generate_pedal_suggestions(
    chords: List[Dict],
    melody: Optional[List[Dict]] = None,
    bpm: float = 120,
    style: str = "legato",
) -> List[Dict[str, Any]]:
    """
    根據和弦進行、旋律、BPM 及風格生成踏板建議。

    Args:
        chords: 和弦列表，每個元素需有 time/start + duration/end/beats + chord/label
        melody: (選用) 旋律音符列表 [{time, pitch, duration}, ...]
        bpm:    速度 (預設 120)
        style:  踏板風格 "legato" | "rhythmic" | "half"

    Returns:
        踏板事件列表 [{"start", "end", "type", "depth"}, ...]
    """
    if not chords:
        return []

    style = style.lower()
    if style not in PEDAL_STYLES:
        style = "legato"

    generators = {
        "legato":   _generate_legato,
        "rhythmic": _generate_rhythmic,
        "half":     _generate_half_pedal,
    }

    events = generators[style](chords, melody, bpm)

    # 後處理: 合併過近的踏板事件、移除重疊
    events = _postprocess(events)
    return events


def _postprocess(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """後處理: 按時間排序，移除重疊"""
    if not events:
        return events

    events.sort(key=lambda e: e["start"])

    cleaned = [events[0]]
    for ev in events[1:]:
        prev = cleaned[-1]
        # 如果和前一個重疊，截斷前一個
        if ev["start"] < prev["end"]:
            prev["end"] = round(ev["start"], 4)
            if prev["end"] - prev["start"] < MIN_PEDAL_DURATION:
                cleaned.pop()
        cleaned.append(ev)

    return cleaned


# ──────────────────────────────────────────
# 踏板品質評估
# ──────────────────────────────────────────

def evaluate_pedal(
    pedal_events: List[Dict[str, Any]],
    chords: List[Dict],
    bpm: float = 120,
) -> Dict[str, Any]:
    """
    評估踏板品質。

    評分維度:
      1. dissonance_score: 不協和堆積風險 (是否在和弦變換處換踏板)
      2. frequency_score:  換踏板頻率是否適當
      3. alignment_score:  踏板邊界是否對齊和弦邊界

    Returns:
        {"total_score": float, "dissonance_score": float,
         "frequency_score": float, "alignment_score": float,
         "details": str}
    """
    if not pedal_events or not chords:
        return {
            "total_score": 0.0,
            "dissonance_score": 0.0,
            "frequency_score": 0.0,
            "alignment_score": 0.0,
            "details": "No pedal events or chords to evaluate",
        }

    # ── 1. 不協和堆積評估 ──
    # 檢查和弦變換點是否有踏板中斷
    chord_boundaries = []
    for i in range(1, len(chords)):
        prev_label = _chord_label(chords[i - 1])
        curr_label = _chord_label(chords[i])
        if prev_label != curr_label:
            boundary_time = _chord_start_sec(chords[i], bpm)
            chord_boundaries.append(boundary_time)

    # 也檢查低音變換
    for i in range(1, len(chords)):
        prev_bass = _bass_note(chords[i - 1])
        curr_bass = _bass_note(chords[i])
        if prev_bass != curr_bass:
            boundary_time = _chord_start_sec(chords[i], bpm)
            if boundary_time not in chord_boundaries:
                chord_boundaries.append(boundary_time)

    if chord_boundaries:
        tolerance = 0.05  # 50ms 容差
        covered = 0
        for bt in chord_boundaries:
            # 檢查是否有踏板在此邊界附近結束或開始
            has_change = any(
                abs(ev["end"] - bt) < tolerance or abs(ev["start"] - bt) < tolerance
                for ev in pedal_events
            )
            if has_change:
                covered += 1
        dissonance_score = covered / len(chord_boundaries) * 100.0
    else:
        dissonance_score = 100.0  # 沒有和弦變換，不扣分

    # ── 2. 換踏板頻率評估 ──
    # 理想頻率: 約每 1-3 秒換一次
    if len(pedal_events) >= 2:
        total_span = pedal_events[-1]["end"] - pedal_events[0]["start"]
        if total_span > 0:
            avg_interval = total_span / len(pedal_events)
            # 理想區間 0.5~3.0 秒
            if 0.5 <= avg_interval <= 3.0:
                frequency_score = 100.0
            elif avg_interval < 0.5:
                frequency_score = max(0.0, 100.0 - (0.5 - avg_interval) * 200)
            else:
                frequency_score = max(0.0, 100.0 - (avg_interval - 3.0) * 30)
        else:
            frequency_score = 50.0
    else:
        frequency_score = 50.0  # 太少踏板事件

    # ── 3. 和弦邊界對齊評估 ──
    chord_starts = [_chord_start_sec(c, bpm) for c in chords]
    if pedal_events and chord_starts:
        tolerance = 0.08  # 80ms
        aligned = 0
        for ev in pedal_events:
            if any(abs(ev["start"] - cs) < tolerance for cs in chord_starts):
                aligned += 1
        alignment_score = (aligned / len(pedal_events)) * 100.0
    else:
        alignment_score = 50.0

    # ── 加權總分 ──
    total = (
        dissonance_score * 0.4
        + frequency_score * 0.3
        + alignment_score * 0.3
    )

    return {
        "total_score": round(total, 1),
        "dissonance_score": round(dissonance_score, 1),
        "frequency_score": round(frequency_score, 1),
        "alignment_score": round(alignment_score, 1),
        "details": (
            f"Chord boundaries: {len(chord_boundaries)}, "
            f"Pedal events: {len(pedal_events)}, "
            f"Style evaluation complete"
        ),
    }


# ──────────────────────────────────────────
# 測試
# ──────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("AI Sustain Pedal Advisor — Phase 11d Test")
    print("=" * 60)

    # 模擬和弦進行: C → Am → F → G (各 2 拍, BPM=120)
    test_chords = [
        {"time": 0.0, "beats": 2, "chord": "C"},
        {"time": 1.0, "beats": 2, "chord": "Am"},
        {"time": 2.0, "beats": 2, "chord": "F"},
        {"time": 3.0, "beats": 2, "chord": "G"},
    ]

    # 模擬旋律
    test_melody = [
        {"time": 0.0, "pitch": 60, "duration": 0.5},
        {"time": 0.5, "pitch": 64, "duration": 0.5},
        {"time": 1.0, "pitch": 67, "duration": 0.5},
        {"time": 1.5, "pitch": 72, "duration": 0.5},
        {"time": 2.0, "pitch": 65, "duration": 0.5},
        {"time": 2.5, "pitch": 69, "duration": 0.5},
        {"time": 3.0, "pitch": 67, "duration": 0.5},
        {"time": 3.5, "pitch": 71, "duration": 0.5},
    ]

    bpm = 120

    for style in ["legato", "rhythmic", "half"]:
        print(f"\n--- Style: {style} ---")
        events = generate_pedal_suggestions(
            test_chords, melody=test_melody, bpm=bpm, style=style
        )
        for ev in events:
            print(f"  {ev['start']:.3f}s → {ev['end']:.3f}s  "
                  f"depth={ev['depth']:.1f}  type={ev['type']}")

        scores = evaluate_pedal(events, test_chords, bpm=bpm)
        print(f"  Score: {scores['total_score']:.1f} "
              f"(dissonance={scores['dissonance_score']:.1f}, "
              f"frequency={scores['frequency_score']:.1f}, "
              f"alignment={scores['alignment_score']:.1f})")

    print("\n✓ Pedal advisor test complete.")
