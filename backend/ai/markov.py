"""
Markov Chain 和弦預測器
支援 Bigram (前 1 個) 和 Trigram (前 2 個) 預測
"""

import json
from collections import defaultdict, Counter
from pathlib import Path

from .preprocess import build_training_data, SEMI_TO_NOTE, NOTE_TO_SEMI, DEGREE_NAMES


class ChordPredictor:
    """基於 Markov Chain 的和弦預測器"""

    def __init__(self):
        # bigram: P(next | current)
        self.bigram = defaultdict(Counter)
        # trigram: P(next | prev, current)
        self.trigram = defaultdict(Counter)
        # 統計
        self.total_transitions = 0
        self.total_songs = 0

    def train(self, chords_dir):
        """從和弦資料目錄訓練模型"""
        degree_sequences, _, stats = build_training_data(chords_dir)
        self.total_songs = stats["total_songs"]

        for seq in degree_sequences:
            for i in range(len(seq) - 1):
                curr = seq[i]
                nxt = seq[i + 1]

                # Bigram
                self.bigram[curr][nxt] += 1
                self.total_transitions += 1

                # Trigram
                if i > 0:
                    prev = seq[i - 1]
                    self.trigram[(prev, curr)][nxt] += 1

        return stats

    def predict(self, context, top_k=5):
        """預測下一個和弦

        Args:
            context: list of degree strings, e.g. ["I", "IV", "V"]
            top_k: 回傳前 k 個最可能的和弦

        Returns:
            list of (degree, probability) pairs
        """
        if not context:
            # 沒有 context，回傳最常見的起始和弦
            all_starts = Counter()
            for k, v in self.bigram.items():
                all_starts[k] += sum(v.values())
            total = sum(all_starts.values())
            return [(d, c / total) for d, c in all_starts.most_common(top_k)]

        curr = context[-1]

        # 優先用 trigram（如果有前兩個和弦）
        if len(context) >= 2:
            prev = context[-2]
            tri_key = (prev, curr)
            if tri_key in self.trigram:
                total = sum(self.trigram[tri_key].values())
                results = [(d, c / total) for d, c in self.trigram[tri_key].most_common(top_k)]
                if results:
                    return results

        # Fallback: bigram
        if curr in self.bigram:
            total = sum(self.bigram[curr].values())
            return [(d, c / total) for d, c in self.bigram[curr].most_common(top_k)]

        return []

    def degree_to_chord(self, degree, key="C"):
        """將級數轉回絕對和弦名

        Args:
            degree: e.g. "IVm7"
            key: e.g. "G"

        Returns:
            chord name, e.g. "Cm7"
        """
        # 解析級數
        for i, name in enumerate(DEGREE_NAMES):
            if degree.startswith(name) and (
                len(degree) == len(name) or not degree[len(name)].isupper()
            ):
                degree_idx = i
                quality = degree[len(name):]
                break
        else:
            return degree  # 無法解析

        key_semi = NOTE_TO_SEMI.get(key, 0)
        chord_semi = (key_semi + degree_idx) % 12
        return SEMI_TO_NOTE[chord_semi] + quality

    def suggest(self, recent_chords, key="C", top_k=5):
        """對外 API：給定最近幾個絕對和弦 + 調性，回傳建議

        Args:
            recent_chords: ["C", "F", "G"] 最近的和弦（絕對名稱）
            key: "C" 調性
            top_k: 回傳數量

        Returns:
            list of {"chord": "Am", "degree": "VIm", "probability": 0.35}
        """
        key_semi = NOTE_TO_SEMI.get(key, 0)

        # 將輸入和弦轉為級數
        from .preprocess import chord_to_degree
        context = []
        for c in recent_chords[-3:]:  # 最多取最後 3 個
            d = chord_to_degree(c, key_semi)
            if d:
                context.append(d)

        if not context:
            return []

        predictions = self.predict(context, top_k=top_k)

        results = []
        for degree, prob in predictions:
            chord_name = self.degree_to_chord(degree, key)
            results.append({
                "chord": chord_name,
                "degree": degree,
                "probability": round(prob, 3),
            })

        return results

    def generate(self, key="C", length=16, seed=None):
        """生成和弦進行

        Args:
            key: 調性
            length: 和弦數量
            seed: 起始和弦級數 (None = 從 I 開始)

        Returns:
            list of {"chord": "C", "degree": "I"}
        """
        import random

        if seed:
            current = seed
        else:
            current = "I"

        result = [{"chord": self.degree_to_chord(current, key), "degree": current}]
        prev = None

        for _ in range(length - 1):
            # 嘗試 trigram
            predictions = self.predict([prev, current] if prev else [current], top_k=10)
            if not predictions:
                break

            # 加權隨機選擇
            degrees = [d for d, _ in predictions]
            weights = [p for _, p in predictions]
            chosen = random.choices(degrees, weights=weights, k=1)[0]

            result.append({
                "chord": self.degree_to_chord(chosen, key),
                "degree": chosen,
            })
            prev = current
            current = chosen

        return result

    def get_stats(self):
        """回傳模型統計"""
        return {
            "total_songs": self.total_songs,
            "total_transitions": self.total_transitions,
            "bigram_states": len(self.bigram),
            "trigram_states": len(self.trigram),
        }


# ---- Singleton ----
_predictor = None


def get_predictor(chords_dir=None):
    """取得全域 predictor instance（lazy init）"""
    global _predictor
    if _predictor is None:
        if chords_dir is None:
            chords_dir = Path(__file__).parent.parent.parent / "data" / "chords"
        _predictor = ChordPredictor()
        _predictor.train(str(chords_dir))
    return _predictor


def retrain(chords_dir=None):
    """重新訓練模型"""
    global _predictor
    if chords_dir is None:
        chords_dir = Path(__file__).parent.parent.parent / "data" / "chords"
    _predictor = ChordPredictor()
    stats = _predictor.train(str(chords_dir))
    return stats


# ---- CLI 測試 ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = "W:/data/chords"
    print(f"Training from: {data_dir}")

    predictor = ChordPredictor()
    stats = predictor.train(data_dir)
    print(f"Songs: {stats['total_songs']}, Transitions: {stats['total_chord_changes']}")
    print(f"Model: {predictor.get_stats()}")

    print("\n=== 預測測試 ===")
    tests = [
        (["C"], "C"),
        (["C", "F"], "C"),
        (["C", "F", "G"], "C"),
        (["Am", "F", "C"], "C"),
        (["Dm", "G"], "C"),
    ]
    for chords, key in tests:
        results = predictor.suggest(chords, key=key, top_k=5)
        ctx = " → ".join(chords)
        preds = ", ".join(f"{r['chord']}({r['degree']} {r['probability']:.0%})" for r in results)
        print(f"  {ctx:20s} → {preds}")

    print("\n=== 生成測試（C 大調 16 小節）===")
    progression = predictor.generate(key="C", length=16)
    print("  " + " → ".join(f"{c['chord']}({c['degree']})" for c in progression))
