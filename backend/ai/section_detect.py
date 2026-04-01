"""
段落偵測器 (Section Detector)
利用和弦切換密度、Chord2Vec 張力變化、重複模式
自動標記：Intro / Verse / Pre-Chorus / Chorus / Bridge / Outro

不需要音檔，純粹從和弦時間軸分析。
"""

import math
from collections import Counter
from .preprocess import chord_to_degree, NOTE_TO_SEMI, parse_chord_name


# 段落類型定義
SECTION_TYPES = {
    "intro": {"emoji": "🎬", "label": "前奏", "color": "#888"},
    "verse": {"emoji": "🎤", "label": "主歌", "color": "#4caf50"},
    "pre_chorus": {"emoji": "⬆️", "label": "導歌", "color": "#ff9800"},
    "chorus": {"emoji": "🎵", "label": "副歌", "color": "#e94560"},
    "bridge": {"emoji": "🌉", "label": "橋段", "color": "#9c27b0"},
    "outro": {"emoji": "🔚", "label": "尾奏", "color": "#607d8b"},
    "instrumental": {"emoji": "🎸", "label": "間奏", "color": "#2196f3"},
}


def _chord_complexity(chord_str):
    """和弦複雜度分數（延伸音越多越高）"""
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
    if "#" in quality or "b" in quality[1:] if len(quality) > 1 else False: score += 1
    return score


def _change_density(chords, window_sec=8.0):
    """計算每個時間窗口的和弦切換密度"""
    if not chords:
        return []

    total_dur = chords[-1].get("end", chords[-1]["time"] + 2)
    n_windows = max(1, int(math.ceil(total_dur / window_sec)))
    densities = [0] * n_windows

    prev_chord = None
    for c in chords:
        t = c["time"]
        w = min(int(t / window_sec), n_windows - 1)
        if c["chord"] != prev_chord:
            densities[w] += 1
            prev_chord = c["chord"]

    return densities


def _find_repeating_patterns(degrees, min_len=4, max_len=16):
    """找出重複出現的和弦模式（用於識別 Chorus）"""
    patterns = Counter()
    for length in range(min_len, min(max_len + 1, len(degrees) // 2 + 1)):
        for i in range(len(degrees) - length + 1):
            pat = tuple(degrees[i:i + length])
            patterns[pat] += 1

    # 只保留出現 >= 2 次的
    return {pat: cnt for pat, cnt in patterns.items() if cnt >= 2}


def detect_sections(chords, key="C"):
    """主入口：偵測段落

    Args:
        chords: [{time, end, chord}, ...]
        key: 調性

    Returns:
        {
            "sections": [
                {"type": "verse", "start": 0.0, "end": 30.0, "chords_count": 12, ...},
                ...
            ],
            "analysis": { density, complexity, patterns }
        }
    """
    if not chords or len(chords) < 4:
        return {"sections": [], "analysis": {}}

    key_semi = NOTE_TO_SEMI.get(key.rstrip("m"), 0)
    total_dur = chords[-1].get("end", chords[-1]["time"] + 4)

    # Step 1: 計算每個窗口的特徵
    WINDOW = 8.0  # 8 秒 ≈ 2-4 小節
    n_windows = max(1, int(math.ceil(total_dur / WINDOW)))

    # 每窗口的和弦數、複雜度、不同和弦數
    w_changes = [0] * n_windows
    w_complexity = [0.0] * n_windows
    w_unique = [set() for _ in range(n_windows)]
    w_chords = [[] for _ in range(n_windows)]

    prev = None
    for c in chords:
        w = min(int(c["time"] / WINDOW), n_windows - 1)
        if c["chord"] != prev:
            w_changes[w] += 1
            prev = c["chord"]
        w_complexity[w] += _chord_complexity(c["chord"])
        deg = chord_to_degree(c["chord"], key_semi)
        if deg:
            w_unique[w].add(deg)
            w_chords[w].append(deg)

    # 平均複雜度
    for i in range(n_windows):
        if w_changes[i] > 0:
            w_complexity[i] /= w_changes[i]

    # Step 2: 計算全曲統計
    avg_density = sum(w_changes) / n_windows if n_windows else 0
    avg_complexity = sum(w_complexity) / n_windows if n_windows else 0

    # Step 3: 找重複模式（Chorus 候選）
    all_degrees = []
    for c in chords:
        deg = chord_to_degree(c["chord"], key_semi)
        if deg:
            all_degrees.append(deg)

    # 去重
    deduped = [all_degrees[0]] if all_degrees else []
    for i in range(1, len(all_degrees)):
        if all_degrees[i] != all_degrees[i - 1]:
            deduped.append(all_degrees[i])

    repeating = _find_repeating_patterns(deduped, min_len=3, max_len=8)

    # 找最常重複的模式 = 可能的 Chorus
    chorus_pattern = None
    if repeating:
        chorus_pattern = max(repeating, key=lambda p: repeating[p] * len(p))

    # Step 4: 分類每個窗口
    sections = []
    for i in range(n_windows):
        start = i * WINDOW
        end = min((i + 1) * WINDOW, total_dur)

        density = w_changes[i]
        complexity = w_complexity[i]
        unique_count = len(w_unique[i])

        # 判斷段落類型
        if i == 0 and density <= 2:
            stype = "intro"
        elif i >= n_windows - 1 and density <= 2:
            stype = "outro"
        elif chorus_pattern and tuple(w_chords[i][:len(chorus_pattern)]) == chorus_pattern[:len(w_chords[i])]:
            stype = "chorus"
        elif density > avg_density * 1.3 or complexity > avg_complexity * 1.5:
            stype = "chorus"  # 高密度或高複雜度 = 副歌
        elif density > avg_density * 0.8 and complexity > avg_complexity:
            stype = "pre_chorus"
        elif unique_count <= 2 and density <= avg_density * 0.5:
            stype = "instrumental"
        else:
            stype = "verse"

        sections.append({
            "type": stype,
            "start": round(start, 1),
            "end": round(end, 1),
            "density": density,
            "complexity": round(complexity, 1),
            "unique_chords": unique_count,
        })

    # Step 5: 合併相鄰同類型段落
    merged = [sections[0]] if sections else []
    for s in sections[1:]:
        if s["type"] == merged[-1]["type"]:
            merged[-1]["end"] = s["end"]
            merged[-1]["density"] += s["density"]
            merged[-1]["complexity"] = round(
                (merged[-1]["complexity"] + s["complexity"]) / 2, 1)
            merged[-1]["unique_chords"] = max(
                merged[-1]["unique_chords"], s["unique_chords"])
        else:
            merged.append(s)

    # 加入 emoji 和顏色
    for s in merged:
        info = SECTION_TYPES.get(s["type"], SECTION_TYPES["verse"])
        s["emoji"] = info["emoji"]
        s["label"] = info["label"]
        s["color"] = info["color"]

    return {
        "sections": merged,
        "analysis": {
            "total_duration": round(total_dur, 1),
            "avg_density": round(avg_density, 1),
            "avg_complexity": round(avg_complexity, 1),
            "chorus_pattern": list(chorus_pattern) if chorus_pattern else None,
            "chorus_repeats": repeating.get(chorus_pattern, 0) if chorus_pattern else 0,
        },
    }


# ---- CLI ----
if __name__ == "__main__":
    import sys, json
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, "..")

    # 測試：用實際和弦資料
    from pathlib import Path
    chords_dir = Path("W:/data/chords") if Path("W:/data/chords").is_dir() else Path("../data/chords")

    # 找一首測試歌
    test_files = sorted(chords_dir.glob("*.json"))[:3]
    for f in test_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if len(data.get("chords", [])) < 10:
            continue
        name = data.get("path", f.stem)
        key = data.get("key", "C")
        result = detect_sections(data["chords"], key)

        print(f"\n=== {name} (Key: {key}) ===")
        print(f"Duration: {result['analysis']['total_duration']}s")
        print(f"Avg density: {result['analysis']['avg_density']}, complexity: {result['analysis']['avg_complexity']}")
        if result["analysis"]["chorus_pattern"]:
            print(f"Chorus pattern: {' → '.join(result['analysis']['chorus_pattern'])} (x{result['analysis']['chorus_repeats']})")

        for s in result["sections"]:
            dur = s["end"] - s["start"]
            print(f"  {s['emoji']} {s['start']:5.0f}-{s['end']:5.0f}s ({dur:4.0f}s) {s['type']:12s} density={s['density']} complexity={s['complexity']}")
