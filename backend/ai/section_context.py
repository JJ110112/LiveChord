"""
Section Context Bridge — Phase 11d #7

將 section_detect.py 的段落偵測結果轉換為
所有下游模組 (伴奏/指法/踏板/力度) 共用的 section_context。

用法:
  sections = detect_sections(chords)
  for chord in chords:
      ctx = get_section_context(chord["time"], sections, total_duration)
      result = generate_accompaniment(chords, melody, section_type=ctx["type"])
"""

from typing import List, Dict, Any, Optional


# ==============================================================================
# 壹、Section Context 結構
# ==============================================================================

# 段落類型 → 能量等級
ENERGY_LEVELS = {
    "intro":        0.4,
    "verse":        0.6,
    "pre_chorus":   0.8,
    "chorus":       1.0,
    "bridge":       0.5,
    "outro":        0.3,
    "instrumental": 0.5,
    # 簡單歌模式
    "A":            0.6,
    "A'":           0.7,
    "B":            0.8,
    "C":            0.9,
    "default":      0.6,
}

# 段落類型 → 建議伴奏密度乘數
DENSITY_MULTIPLIER = {
    "intro":        0.5,
    "verse":        0.7,
    "pre_chorus":   0.9,
    "chorus":       1.0,
    "bridge":       0.6,
    "outro":        0.4,
    "instrumental": 0.7,
    "A":            0.7,
    "A'":           0.75,
    "B":            0.85,
    "C":            1.0,
    "default":      0.8,
}

# 段落類型 → 建議踏板風格
PEDAL_STYLE = {
    "intro":        "legato",
    "verse":        "legato",
    "pre_chorus":   "half",
    "chorus":       "rhythmic",
    "bridge":       "legato",
    "outro":        "legato",
    "instrumental": "half",
    "default":      "legato",
}

# 段落類型 → 建議最大指法難度
MAX_FINGERING_LEVEL = {
    "intro":        "L1",
    "verse":        "L2",
    "pre_chorus":   "L2",
    "chorus":       "L3",
    "bridge":       "L1",
    "outro":        "L1",
    "instrumental": "L2",
    "default":      "L2",
}


# ==============================================================================
# 貳、核心函數
# ==============================================================================

def get_section_context(
    time: float,
    sections: List[Dict],
    total_duration: float = 0.0,
) -> Dict[str, Any]:
    """
    根據時間點取得 section context。

    Args:
        time: 當前時間點 (秒)
        sections: section_detect 的輸出 [{start, end, type, label}, ...]
        total_duration: 曲子總長度

    Returns:
        {
            "type": "verse",                  # 段落類型
            "label": "主歌",                   # 段落標籤
            "position_in_song": 0.35,         # 曲子進度 0~1
            "position_in_section": 0.5,       # 段落內進度 0~1
            "energy_level": 0.6,              # 能量等級 0~1
            "density_multiplier": 0.7,        # 密度乘數
            "pedal_style": "legato",          # 建議踏板風格
            "max_level": "L2",                # 建議最大指法難度
        }
    """
    if not sections:
        return _default_context(time, total_duration)

    # 找到包含此時間點的段落
    for sec in sections:
        sec_start = sec.get("start", sec.get("time", 0))
        sec_end = sec.get("end", sec_start + 10)

        if sec_start <= time < sec_end:
            sec_type = _normalize_section_type(sec.get("type", sec.get("label", "default")))
            sec_dur = sec_end - sec_start

            pos_in_section = (time - sec_start) / sec_dur if sec_dur > 0 else 0.5
            pos_in_song = time / total_duration if total_duration > 0 else 0.5

            return {
                "type": sec_type,
                "label": sec.get("label", sec_type),
                "position_in_song": round(pos_in_song, 3),
                "position_in_section": round(pos_in_section, 3),
                "energy_level": ENERGY_LEVELS.get(sec_type, 0.6),
                "density_multiplier": DENSITY_MULTIPLIER.get(sec_type, 0.8),
                "pedal_style": PEDAL_STYLE.get(sec_type, "legato"),
                "max_level": MAX_FINGERING_LEVEL.get(sec_type, "L2"),
            }

    return _default_context(time, total_duration)


def build_section_timeline(
    sections: List[Dict],
    chords: List[Dict],
    total_duration: float = 0.0,
) -> List[Dict]:
    """
    為每個和弦事件附加 section_context。

    Returns:
        [{**chord, "section_context": {...}}, ...]
    """
    if not total_duration and chords:
        last = chords[-1]
        total_duration = last.get("end", last.get("time", 0) + 2)

    result = []
    for chord in chords:
        t = chord.get("time", 0)
        ctx = get_section_context(t, sections, total_duration)
        enriched = {**chord, "section_context": ctx}
        result.append(enriched)

    return result


# ==============================================================================
# 參、內部工具
# ==============================================================================

def _normalize_section_type(raw: str) -> str:
    """將各種段落標記統一為標準類型。"""
    raw_lower = raw.lower().strip()

    # 直接匹配
    known_types = [
        "intro", "verse", "pre_chorus", "chorus", "bridge",
        "outro", "instrumental",
    ]
    for t in known_types:
        if t in raw_lower:
            return t

    # 簡單歌模式
    if raw_lower.startswith("a'") or raw_lower.startswith("a'"):
        return "A'"
    if raw_lower.startswith("a"):
        return "A"
    if raw_lower.startswith("b"):
        return "B"
    if raw_lower.startswith("c"):
        return "C"

    # 中文匹配
    cn_map = {
        "主歌": "verse", "副歌": "chorus", "前奏": "intro",
        "尾奏": "outro", "橋段": "bridge", "導歌": "pre_chorus",
        "間奏": "instrumental",
    }
    for cn, en in cn_map.items():
        if cn in raw:
            return en

    return "default"


def _default_context(time: float, total_duration: float) -> Dict[str, Any]:
    """無段落資訊時的預設 context。"""
    pos = time / total_duration if total_duration > 0 else 0.5
    return {
        "type": "default",
        "label": "default",
        "position_in_song": round(pos, 3),
        "position_in_section": 0.5,
        "energy_level": 0.6,
        "density_multiplier": 0.8,
        "pedal_style": "legato",
        "max_level": "L2",
    }


# ==============================================================================
# 肆、測試
# ==============================================================================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import json

    # 模擬段落偵測結果
    sections = [
        {"start": 0, "end": 8, "type": "intro", "label": "前奏"},
        {"start": 8, "end": 32, "type": "verse", "label": "主歌"},
        {"start": 32, "end": 48, "type": "chorus", "label": "副歌"},
        {"start": 48, "end": 56, "type": "bridge", "label": "橋段"},
        {"start": 56, "end": 72, "type": "chorus", "label": "副歌"},
        {"start": 72, "end": 80, "type": "outro", "label": "尾奏"},
    ]

    print("=" * 60)
    print("Section Context Bridge Test")
    print("=" * 60)

    for t in [2, 10, 35, 50, 60, 75]:
        ctx = get_section_context(t, sections, total_duration=80)
        print(f"  t={t:3d}s: {ctx['type']:12s} energy={ctx['energy_level']:.1f} "
              f"density={ctx['density_multiplier']:.1f} pedal={ctx['pedal_style']:8s} "
              f"pos_song={ctx['position_in_song']:.2f}")

    # 和弦 timeline
    print("\n--- Chord Timeline ---")
    chords = [
        {"time": 5, "end": 9, "chord": "C"},
        {"time": 20, "end": 24, "chord": "Am"},
        {"time": 40, "end": 44, "chord": "F"},
        {"time": 52, "end": 56, "chord": "G7"},
    ]
    timeline = build_section_timeline(sections, chords, 80)
    for c in timeline:
        ctx = c["section_context"]
        print(f"  {c['chord']:4s} @ t={c['time']:2d}s → {ctx['type']:12s} "
              f"(energy={ctx['energy_level']:.1f}, level={ctx['max_level']})")
