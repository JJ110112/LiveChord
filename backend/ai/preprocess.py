"""
和弦預處理：解析 JSON → 移調至 C 大調 → 輸出級數序列
用於 Markov Chain 和弦預測模型的訓練資料準備
"""

import json
import os
import re
from pathlib import Path
from collections import Counter

# 半音對照
NOTE_TO_SEMI = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
                "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
                "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}

SEMI_TO_NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 級數名稱（相對於大調主音）
DEGREE_NAMES = ["I", "bII", "II", "bIII", "III", "IV", "#IV", "V", "bVI", "VI", "bVII", "VII"]


def parse_chord_name(chord_str):
    """解析和弦名 → (根音半音, 品質字串)
    例: 'Ebm7' → (3, 'm7'), 'F#' → (6, ''), 'N' → (None, None)
    """
    if not chord_str or chord_str in ("N", "X", "NC", ""):
        return None, None

    m = re.match(r"^([A-G][b#]?)(.*?)(?:/[A-G][b#]?)?$", chord_str)
    if not m:
        return None, None

    root_name = m.group(1)
    quality = m.group(2)

    if root_name not in NOTE_TO_SEMI:
        return None, None

    return NOTE_TO_SEMI[root_name], quality


def transpose_chord(chord_str, semitones):
    """將和弦移調 semitones 個半音
    例: transpose_chord('Eb', -3) → 'C'
    """
    m = re.match(r"^([A-G][b#]?)(.*?)(/([A-G][b#]?))?$", chord_str)
    if not m:
        return chord_str

    root = m.group(1)
    quality = m.group(2)
    bass = m.group(4)

    if root not in NOTE_TO_SEMI:
        return chord_str

    new_root = SEMI_TO_NOTE[(NOTE_TO_SEMI[root] + semitones) % 12]
    result = new_root + quality

    if bass and bass in NOTE_TO_SEMI:
        new_bass = SEMI_TO_NOTE[(NOTE_TO_SEMI[bass] + semitones) % 12]
        result += "/" + new_bass

    return result


def detect_key_from_chords(chords):
    """從和弦序列推測調性（取最常出現的根音）"""
    roots = []
    for c in chords:
        semi, _ = parse_chord_name(c)
        if semi is not None:
            roots.append(semi)

    if not roots:
        return 0  # default C

    # 用加權計算：第一個和最後一個和弦權重高
    weighted = Counter()
    for i, r in enumerate(roots):
        w = 1
        if i == 0 or i == len(roots) - 1:
            w = 3
        elif i < 4 or i > len(roots) - 4:
            w = 2
        weighted[r] += w

    return weighted.most_common(1)[0][0]


def chord_to_degree(chord_str, key_semi):
    """將和弦轉為相對於 key 的級數表示
    例: key=G(7), chord='D' → 'V', chord='Am' → 'IIm'
    """
    semi, quality = parse_chord_name(chord_str)
    if semi is None:
        return None

    degree_idx = (semi - key_semi) % 12
    degree = DEGREE_NAMES[degree_idx]

    # 簡化品質標記
    q = quality
    if q in ("min",):
        q = "m"

    return degree + q


def load_all_chord_sequences(chords_dir):
    """載入所有和弦 JSON，回傳 [(song_info, [chord_names])]"""
    songs = []
    chords_path = Path(chords_dir)

    if not chords_path.is_dir():
        return songs

    # Sharded layout: <chords_path>/<bucket>/<hash>.json. Recurse 1 level deep.
    json_files = sorted(chords_path.glob("*/*.json"))
    if not json_files:
        # Backward fallback for legacy flat layout (testing only)
        json_files = sorted(chords_path.glob("*.json"))
    for f in json_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            chords = data.get("chords", [])
            if not chords:
                continue

            chord_names = [c["chord"] for c in chords if c.get("chord")]
            if len(chord_names) < 4:  # 太短的跳過
                continue

            songs.append({
                "path": data.get("path", f.stem),
                "key": data.get("key", ""),
                "source": data.get("source", ""),
                "chords": chord_names,
            })
        except Exception:
            continue

    return songs


VOCAB_LIMIT = 100  # 只保留前 N 常見的級數，罕見的降級為簡化版

# 品質降級對照：罕見品質 → 基礎三和弦品質
_SIMPLIFY_QUALITY = {
    "maj7": "", "maj9": "", "maj11": "", "maj13": "", "6": "", "add9": "",
    "m7": "m", "m9": "m", "m11": "m", "m6": "m", "madd9": "m",
    "7": "", "9": "", "13": "", "7b9": "", "7#9": "", "7#11": "", "7b13": "",
    "m7b5": "dim", "mM7": "m",
    "sus2": "", "sus4": "",
}


def _simplify_degree(degree):
    """將罕見級數降級為基礎版本
    例: bIIImaj7 → bIII, VIm7b5 → VIdim
    """
    import re
    m = re.match(r"^(bVII|#IV|bVI|bIII|bII|VII|VI|IV|III|II|V|I)(.*)", degree)
    if not m:
        return degree
    root_deg = m.group(1)
    quality = m.group(2)
    simplified = _SIMPLIFY_QUALITY.get(quality, quality)
    return root_deg + simplified


def build_training_data(chords_dir, vocab_limit=VOCAB_LIMIT):
    """建立訓練資料：所有歌曲移調到 C 大調，輸出級數序列

    Phase 1: 收集所有級數統計
    Phase 2: 過濾罕見級數（降級為基礎三和弦）
    Phase 3: 輸出清理後的序列

    Returns:
        degree_sequences: [[degree_str, ...], ...]  每首歌一個 list
        transposed_sequences: [[chord_str, ...], ...]  移調後的絕對和弦
        stats: dict 統計資訊
    """
    songs = load_all_chord_sequences(chords_dir)

    # Phase 1: 收集所有級數
    raw_sequences = []
    raw_transposed = []

    for song in songs:
        chord_names = song["chords"]
        key_semi = detect_key_from_chords(chord_names)
        shift = -key_semi
        transposed = [transpose_chord(c, shift) for c in chord_names]

        degrees = []
        for c in chord_names:
            d = chord_to_degree(c, key_semi)
            if d:
                degrees.append(d)

        if len(degrees) < 4:
            continue

        # 去除連續重複
        deduped_degrees = [degrees[0]]
        deduped_transposed = [transposed[0]]
        for i in range(1, len(degrees)):
            if degrees[i] != degrees[i - 1]:
                deduped_degrees.append(degrees[i])
                deduped_transposed.append(transposed[i])

        raw_sequences.append(deduped_degrees)
        raw_transposed.append(deduped_transposed)

    # Phase 2: 建立詞彙表，過濾罕見級數
    all_degrees = Counter()
    for seq in raw_sequences:
        all_degrees.update(seq)

    # Top N 為核心詞彙
    core_vocab = {d for d, _ in all_degrees.most_common(vocab_limit)}
    filtered_count = 0

    # Phase 3: 正規化序列
    degree_sequences = []
    transposed_sequences = []
    total_chords = 0
    unique_degrees = Counter()
    unique_chords = Counter()

    for seq, trans in zip(raw_sequences, raw_transposed):
        cleaned = []
        cleaned_trans = []
        for d, t in zip(seq, trans):
            if d in core_vocab:
                cleaned.append(d)
                cleaned_trans.append(t)
            else:
                # 降級為基礎版本
                simplified = _simplify_degree(d)
                if simplified in core_vocab:
                    cleaned.append(simplified)
                    cleaned_trans.append(t)
                    filtered_count += 1
                else:
                    # 極度罕見：取級數根部
                    import re
                    m = re.match(r"^(bVII|#IV|bVI|bIII|bII|VII|VI|IV|III|II|V|I)", d)
                    root = m.group(1) if m else d
                    cleaned.append(root)
                    cleaned_trans.append(t)
                    filtered_count += 1

        if len(cleaned) < 4:
            continue

        # 再次去除因降級產生的連續重複
        final_deg = [cleaned[0]]
        final_trans = [cleaned_trans[0]]
        for i in range(1, len(cleaned)):
            if cleaned[i] != cleaned[i - 1]:
                final_deg.append(cleaned[i])
                final_trans.append(cleaned_trans[i])

        degree_sequences.append(final_deg)
        transposed_sequences.append(final_trans)
        total_chords += len(final_deg)

        for d in final_deg:
            unique_degrees[d] += 1
        for c in final_trans:
            unique_chords[c] += 1

    stats = {
        "total_songs": len(degree_sequences),
        "total_chord_changes": total_chords,
        "unique_degrees": len(unique_degrees),
        "unique_chords": len(unique_chords),
        "vocab_limit": vocab_limit,
        "filtered_rare_chords": filtered_count,
        "top_degrees": unique_degrees.most_common(20),
        "top_chords": unique_chords.most_common(20),
    }

    return degree_sequences, transposed_sequences, stats


# ---- CLI 測試 ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = Path(__file__).parent.parent.parent / "data" / "chords"
    print(f"Chords dir: {data_dir}")

    degrees, transposed, stats = build_training_data(data_dir)

    print(f"\n=== 訓練資料統計 ===")
    print(f"歌曲數: {stats['total_songs']}")
    print(f"和弦變化總數: {stats['total_chord_changes']}")
    print(f"獨特級數: {stats['unique_degrees']}")
    print(f"獨特和弦: {stats['unique_chords']}")

    print(f"\n=== Top 20 級數 ===")
    for d, cnt in stats["top_degrees"]:
        print(f"  {d:10s} {cnt:5d}")

    print(f"\n=== 範例：第一首歌級數序列 ===")
    if degrees:
        print(f"  {' → '.join(degrees[0][:20])}...")
