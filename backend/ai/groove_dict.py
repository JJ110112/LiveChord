"""
Groove Dictionary — 風格和弦字典
從和弦資料中萃取常見的和弦循環模式 (patterns)
包含時值資訊 (temporal encoding)
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from .preprocess import build_training_data, chord_to_degree, NOTE_TO_SEMI


# 量化時值等級
def _quantize_duration(seconds):
    """將持續時間量化為 beat 等級"""
    if seconds < 0.5:
        return "8th"     # 八分音符
    elif seconds < 1.2:
        return "1beat"   # 一拍
    elif seconds < 2.5:
        return "2beat"   # 兩拍
    elif seconds < 4.5:
        return "4beat"   # 一小節
    elif seconds < 9:
        return "8beat"   # 兩小節
    else:
        return "long"    # 更長


def _chord_voicing_spread(chord_str):
    """計算和弦的 voicing spread（最高音-最低音的半音數）
    用於 Neo-Soul Cluster Voicing 偵測
    """
    from .jazz_rules import parse_root_quality
    root_semi, quality = parse_root_quality(chord_str)
    if root_semi is None:
        return 0
    INTERVALS = {
        "": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10],
        "m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11],
        "9": [0, 4, 7, 10, 14], "m9": [0, 3, 7, 10, 14],
        "maj9": [0, 4, 7, 11, 14], "13": [0, 4, 7, 10, 14, 21],
    }
    ivs = INTERVALS.get(quality, INTERVALS.get("", [0, 4, 7]))
    return max(ivs) - min(ivs) if ivs else 0


class GrooveDictionary:
    """風格和弦循環字典"""

    def __init__(self):
        # pattern → count: 常見的 4 和弦循環
        self.patterns_4 = Counter()
        # pattern → count: 常見的 8 和弦循環
        self.patterns_8 = Counter()
        # degree → 常見時值分佈
        self.duration_dist = defaultdict(Counter)
        # event tokens: (degree, duration_class) 序列
        self.event_sequences = []
        # voicing spread 統計：degree → [spread_values]
        self.voicing_spread = defaultdict(list)
        # 統計
        self.total_songs = 0

    def build(self, chords_dir):
        """從和弦資料建立字典"""
        chords_path = Path(chords_dir)
        if not chords_path.is_dir():
            return {}

        degree_sequences, _, stats = build_training_data(chords_dir)
        self.total_songs = stats["total_songs"]

        # 也需要原始時值資料
        for f in sorted(chords_path.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                chords = data.get("chords", [])
                key = data.get("key", "C")
                if len(chords) < 4:
                    continue

                key_semi = NOTE_TO_SEMI.get(key.rstrip("m"), 0)

                # 建立 event sequence (degree + duration)
                events = []
                degrees = []
                for c in chords:
                    deg = chord_to_degree(c["chord"], key_semi)
                    if not deg:
                        continue
                    duration = c.get("end", c["time"] + 2) - c["time"]
                    dur_class = _quantize_duration(duration)

                    # 時值分佈統計
                    self.duration_dist[deg][dur_class] += 1

                    # Voicing Spread 統計
                    spread = _chord_voicing_spread(c["chord"])
                    if spread > 0:
                        self.voicing_spread[deg].append(spread)

                    events.append((deg, dur_class))
                    degrees.append(deg)

                self.event_sequences.append(events)

                # 去除連續重複
                deduped = [degrees[0]]
                for i in range(1, len(degrees)):
                    if degrees[i] != degrees[i - 1]:
                        deduped.append(degrees[i])

                # 萃取 4-chord patterns
                for i in range(len(deduped) - 3):
                    pat = tuple(deduped[i:i + 4])
                    self.patterns_4[pat] += 1

                # 萃取 8-chord patterns
                for i in range(len(deduped) - 7):
                    pat = tuple(deduped[i:i + 8])
                    self.patterns_8[pat] += 1

            except Exception:
                continue

        return self.get_stats()

    def top_patterns(self, length=4, top_k=20):
        """回傳最常見的和弦循環"""
        source = self.patterns_4 if length == 4 else self.patterns_8
        return [
            {"pattern": list(pat), "count": cnt}
            for pat, cnt in source.most_common(top_k)
        ]

    def typical_duration(self, degree, top_k=3):
        """回傳某級數最常見的時值"""
        if degree not in self.duration_dist:
            return []
        return [
            {"duration": dur, "count": cnt}
            for dur, cnt in self.duration_dist[degree].most_common(top_k)
        ]

    def voicing_spread_stats(self, degree):
        """回傳某級數的 voicing spread 統計（為 Neo-Soul Cluster Voicing 偵測用）"""
        spreads = self.voicing_spread.get(degree, [])
        if not spreads:
            return {"degree": degree, "count": 0}
        import numpy as np
        arr = np.array(spreads)
        return {
            "degree": degree,
            "count": len(spreads),
            "mean_spread": round(float(arr.mean()), 1),
            "min_spread": int(arr.min()),
            "max_spread": int(arr.max()),
            "cluster_ratio": round(float((arr <= 10).sum() / len(arr)), 2),  # spread<=10 = cluster voicing
        }

    def suggest_pattern(self, context, top_k=5):
        """根據前幾個和弦，從字典中找匹配的循環模式"""
        if len(context) < 2:
            return self.top_patterns(4, top_k)

        ctx_tuple = tuple(context[-3:])  # 最多取最後 3 個
        results = []

        for pat, cnt in self.patterns_4.most_common(200):
            # 檢查 pattern 是否以 context 結尾開始
            for start in range(len(pat) - len(ctx_tuple) + 1):
                if pat[start:start + len(ctx_tuple)] == ctx_tuple:
                    remaining = list(pat[start + len(ctx_tuple):])
                    if remaining:
                        results.append({
                            "pattern": list(pat),
                            "next": remaining,
                            "count": cnt,
                        })
                    break
            if len(results) >= top_k:
                break

        return results

    def get_stats(self):
        return {
            "total_songs": self.total_songs,
            "unique_4_patterns": len(self.patterns_4),
            "unique_8_patterns": len(self.patterns_8),
            "duration_degrees": len(self.duration_dist),
            "event_sequences": len(self.event_sequences),
            "voicing_spread_degrees": len(self.voicing_spread),
        }


# ---- Singleton ----
_dict = None


def get_groove_dict(chords_dir=None):
    global _dict
    if _dict is None:
        if chords_dir is None:
            chords_dir = Path(__file__).parent.parent.parent / "data" / "chords"
        _dict = GrooveDictionary()
        _dict.build(str(chords_dir))
    return _dict


# ---- CLI 測試 ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = "W:/data/chords"
    print(f"Building Groove Dictionary from: {data_dir}")

    gd = GrooveDictionary()
    stats = gd.build(data_dir)
    print(f"Stats: {stats}")

    print("\n=== Top 15 四和弦循環 ===")
    for p in gd.top_patterns(4, 15):
        print(f"  {' → '.join(p['pattern']):40s} ({p['count']}x)")

    print("\n=== Top 10 八和弦循環 ===")
    for p in gd.top_patterns(8, 10):
        print(f"  {' → '.join(p['pattern']):60s} ({p['count']}x)")

    print("\n=== 時值分佈（常見級數）===")
    for deg in ["I", "IV", "V", "V7", "IIm7", "VIm"]:
        durs = gd.typical_duration(deg)
        dur_str = ", ".join(f"{d['duration']}({d['count']})" for d in durs)
        print(f"  {deg:8s}: {dur_str}")

    print("\n=== Pattern 推薦：I → IV 之後？===")
    for p in gd.suggest_pattern(["I", "IV"], 5):
        print(f"  {' → '.join(p['pattern'])} → next: {p['next']} ({p['count']}x)")
