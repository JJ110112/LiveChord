"""
段落偵測器 v2 (Section Detector)
修正：BPM 動態窗口、簡單歌曲 A/B 模式、重複模式比對、段落平滑化

策略：
1. 偵測 BPM → 動態計算窗口大小（4 或 8 小節）
2. 計算和弦複雜度 → 簡單歌（≤4 種和弦）用 A/A'/B 標記
3. 重複模式比對 → 相同和弦進行 = 相同段落
4. 密度+複雜度分析 → 流行歌用 前奏/主歌/副歌/橋段/尾奏
5. 平滑化 → 避免頻繁切換
"""

import math
from collections import Counter
from .preprocess import chord_to_degree, NOTE_TO_SEMI, parse_chord_name


# 段落類型定義
SECTION_TYPES = {
    # 流行歌模式
    "intro": {"emoji": "🎬", "label": "前奏", "color": "#888"},
    "verse": {"emoji": "🎤", "label": "主歌", "color": "#4caf50"},
    "pre_chorus": {"emoji": "⬆️", "label": "導歌", "color": "#ff9800"},
    "chorus": {"emoji": "🎵", "label": "副歌", "color": "#e94560"},
    "bridge": {"emoji": "🌉", "label": "橋段", "color": "#9c27b0"},
    "outro": {"emoji": "🔚", "label": "尾奏", "color": "#607d8b"},
    "instrumental": {"emoji": "🎸", "label": "間奏", "color": "#2196f3"},
    # 簡單歌模式（A/B 結構）
    "A": {"emoji": "🅰️", "label": "A段", "color": "#4caf50"},
    "A'": {"emoji": "🅰️", "label": "A'段", "color": "#66bb6a"},
    "B": {"emoji": "🅱️", "label": "B段", "color": "#e94560"},
    "C": {"emoji": "©️", "label": "C段", "color": "#ff9800"},
}


def _estimate_bpm(chords):
    """從和弦時值推估 BPM"""
    if len(chords) < 4:
        return 120
    durations = [c.get("end", c["time"] + 2) - c["time"] for c in chords]
    durations = [d for d in durations if 0.2 < d < 10]
    if not durations:
        return 120
    median = sorted(durations)[len(durations) // 2]
    # 用多種假設取最合理的 BPM（80-160 範圍）
    candidates = [
        60.0 / median * 1,  # 中位數 = 1 拍
        60.0 / median * 2,  # 中位數 = 2 拍
        60.0 / median * 4,  # 中位數 = 4 拍（1 小節）
    ]
    # 選最接近 100-120 的
    best = min(candidates, key=lambda b: abs(b - 110))
    return max(60, min(200, round(best)))


def _get_unique_count(chords):
    """不重複和弦數"""
    return len(set(c["chord"] for c in chords if c.get("chord") and c["chord"] != "N"))


def _chord_sequence_hash(chords, key_semi):
    """把一段和弦轉為級數 tuple 作為 fingerprint"""
    degrees = []
    for c in chords:
        d = chord_to_degree(c["chord"], key_semi)
        if d:
            degrees.append(d)
    # 去連續重複
    deduped = [degrees[0]] if degrees else []
    for i in range(1, len(degrees)):
        if degrees[i] != degrees[i - 1]:
            deduped.append(degrees[i])
    return tuple(deduped)


def _is_simple_song(chords):
    """判斷是否為簡單歌曲（兒歌/民謠/簡單 Pop）"""
    uc = _get_unique_count(chords)
    total_dur = chords[-1].get("end", chords[-1]["time"] + 4) if chords else 0
    # 不重複和弦 ≤ 5 種，或總長 < 2 分鐘
    return uc <= 5 or total_dur < 120


def detect_sections(chords, key="C"):
    """主入口：偵測段落

    Returns:
        {
            "sections": [{type, label, emoji, color, start, end, ...}],
            "analysis": {bpm, mode, ...}
        }
    """
    if not chords or len(chords) < 4:
        return {"sections": [], "analysis": {}}

    key_semi = NOTE_TO_SEMI.get(key.rstrip("m"), 0)
    total_dur = chords[-1].get("end", chords[-1]["time"] + 4)
    bpm = _estimate_bpm(chords)
    is_simple = _is_simple_song(chords)

    # 動態窗口：基於 BPM，4 小節為一個段落單位
    beat_sec = 60.0 / bpm
    bars_per_section = 8 if not is_simple else 4
    window = beat_sec * 4 * bars_per_section  # 4 拍 × N 小節
    window = max(4, min(32, window))  # 4-32 秒

    n_windows = max(1, int(math.ceil(total_dur / window)))

    # 切割窗口，收集每個窗口的和弦
    window_chords = [[] for _ in range(n_windows)]
    for c in chords:
        w = min(int(c["time"] / window), n_windows - 1)
        window_chords[w].append(c)

    # 計算每個窗口的 fingerprint
    fingerprints = []
    for wc in window_chords:
        if wc:
            fp = _chord_sequence_hash(wc, key_semi)
            fingerprints.append(fp)
        else:
            fingerprints.append(())

    if is_simple:
        sections = _detect_simple(fingerprints, window, total_dur)
        mode = "simple (A/B)"
    else:
        sections = _detect_pop(fingerprints, window_chords, window, total_dur, key_semi)
        mode = "pop (verse/chorus)"

    # 合併相鄰同類型段落
    merged = _merge_sections(sections)

    # 加入 emoji/label/color
    for s in merged:
        info = SECTION_TYPES.get(s["type"], SECTION_TYPES.get("A"))
        s["emoji"] = info["emoji"]
        s["label"] = info["label"]
        s["color"] = info["color"]

    return {
        "sections": merged,
        "analysis": {
            "total_duration": round(total_dur, 1),
            "bpm": bpm,
            "window_sec": round(window, 1),
            "mode": mode,
            "unique_chords": _get_unique_count(chords),
        },
    }


def _detect_simple(fingerprints, window, total_dur):
    """簡單歌曲：用重複模式匹配 A/A'/B"""
    if not fingerprints:
        return []

    # 第一個非空 fingerprint = A 段
    a_pattern = None
    for fp in fingerprints:
        if fp:
            a_pattern = fp
            break
    if not a_pattern:
        return [{"type": "A", "start": 0, "end": total_dur}]

    sections = []
    for i, fp in enumerate(fingerprints):
        start = i * window
        end = min((i + 1) * window, total_dur)

        if not fp:
            stype = "A"
        elif fp == a_pattern:
            stype = "A"
        elif _similarity(fp, a_pattern) > 0.6:
            stype = "A'"  # 變形
        else:
            stype = "B"

        sections.append({"type": stype, "start": round(start, 1), "end": round(end, 1)})

    return sections


def _detect_pop(fingerprints, window_chords, window, total_dur, key_semi):
    """流行歌曲：密度+複雜度+重複模式"""
    n = len(fingerprints)
    if n == 0:
        return []

    # 計算每個窗口的密度和複雜度
    densities = []
    complexities = []
    for wc in window_chords:
        prev = None
        changes = 0
        comp = 0
        for c in wc:
            if c["chord"] != prev:
                changes += 1
                prev = c["chord"]
            comp += _chord_complexity(c["chord"])
        densities.append(changes)
        complexities.append(comp / max(changes, 1))

    avg_density = sum(densities) / max(n, 1)
    avg_complexity = sum(complexities) / max(n, 1)

    # 找最常見的重複模式 = Chorus 候選
    fp_counts = Counter(fp for fp in fingerprints if fp)
    chorus_fp = fp_counts.most_common(1)[0][0] if fp_counts else None
    chorus_count = fp_counts.get(chorus_fp, 0) if chorus_fp else 0

    sections = []
    for i in range(n):
        start = i * window
        end = min((i + 1) * window, total_dur)
        density = densities[i]
        complexity = complexities[i]
        fp = fingerprints[i]

        # 判斷段落類型
        if i == 0 and density <= 2:
            stype = "intro"
        elif i >= n - 1 and density <= 2:
            stype = "outro"
        elif chorus_fp and chorus_count >= 2 and fp == chorus_fp:
            stype = "chorus"
        elif chorus_fp and chorus_count >= 2 and _similarity(fp, chorus_fp) > 0.7:
            stype = "chorus"
        elif density > avg_density * 1.4 and complexity > avg_complexity * 1.3:
            stype = "chorus"
        elif density <= 1 and len(set(c["chord"] for c in window_chords[i])) <= 2:
            stype = "instrumental"
        elif complexity > avg_complexity * 1.2 and density > avg_density * 0.8:
            stype = "pre_chorus"
        else:
            stype = "verse"

        sections.append({
            "type": stype,
            "start": round(start, 1),
            "end": round(end, 1),
        })

    return sections


def _merge_sections(sections):
    """合併相鄰同類型段落"""
    if not sections:
        return []
    merged = [dict(sections[0])]
    for s in sections[1:]:
        if s["type"] == merged[-1]["type"]:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    return merged


def _similarity(fp1, fp2):
    """兩個 fingerprint 的相似度 (Jaccard)"""
    if not fp1 or not fp2:
        return 0
    s1, s2 = set(fp1), set(fp2)
    intersection = s1 & s2
    union = s1 | s2
    return len(intersection) / len(union) if union else 0


def _chord_complexity(chord_str):
    """和弦複雜度分數"""
    _, quality = parse_chord_name(chord_str)
    if quality is None:
        return 0
    score = 0
    if "7" in quality: score += 1
    if "9" in quality: score += 2
    if "11" in quality: score += 3
    if "13" in quality: score += 4
    if "dim" in quality: score += 2
    if "aug" in quality: score += 2
    return score


# ---- CLI 測試 ----
if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")

    from pathlib import Path
    chords_dir = Path("W:/data/chords") if Path("W:/data/chords").is_dir() else Path("../data/chords")

    # 測試小蜜蜂 + 其他歌
    import hashlib
    test_songs = ["小蜜蜂.flac", "Get It On.flac", "Dancing Queen.flac"]

    for song in test_songs:
        h = hashlib.md5(song.encode()).hexdigest()[:12]
        f = chords_dir / f"{h}.json"
        if not f.is_file():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        key = data.get("key", "C")
        result = detect_sections(data["chords"], key)
        name = song.replace(".flac", "")

        print(f"\n=== {name} (Key:{key}, BPM:{result['analysis']['bpm']}, mode:{result['analysis']['mode']}) ===")
        for s in result["sections"]:
            dur = s["end"] - s["start"]
            print(f"  {s['emoji']} {s['start']:5.0f}-{s['end']:5.0f}s ({dur:4.0f}s) {s['label']}")
