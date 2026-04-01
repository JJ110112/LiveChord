"""
Chord2Vec — 和弦嵌入表示法
使用 Word2Vec (Skip-gram) 概念訓練和弦向量
讓 AI 理解和弦之間的相似性：Dm7 ≈ Fmaj7（共同音多）

依賴：gensim（如果沒有，fallback 到手寫 co-occurrence matrix + SVD）
"""

import json
import os
import numpy as np
from collections import Counter, defaultdict
from pathlib import Path

from .preprocess import build_training_data, NOTE_TO_SEMI, SEMI_TO_NOTE


class Chord2Vec:
    """和弦嵌入模型"""

    def __init__(self, dim=32, window=3):
        self.dim = dim          # 嵌入維度
        self.window = window    # context window size
        self.vocab = {}         # degree → index
        self.index2word = []    # index → degree
        self.vectors = None     # numpy array (vocab_size, dim)
        self.trained = False

    def train(self, chords_dir):
        """從和弦資料訓練嵌入

        Uses SVD on co-occurrence matrix (no gensim dependency)
        """
        degree_sequences, _, stats = build_training_data(chords_dir)

        # 建立詞彙表
        word_counts = Counter()
        for seq in degree_sequences:
            word_counts.update(seq)

        # 只保留出現 >= 2 次的
        self.index2word = [w for w, c in word_counts.most_common() if c >= 2]
        self.vocab = {w: i for i, w in enumerate(self.index2word)}
        vocab_size = len(self.vocab)

        if vocab_size < 3:
            self.vectors = np.zeros((vocab_size, min(self.dim, 1)), dtype=np.float32)
            return stats

        # 建立共現矩陣 (co-occurrence matrix)
        cooccur = np.zeros((vocab_size, vocab_size), dtype=np.float32)

        for seq in degree_sequences:
            for i, word in enumerate(seq):
                if word not in self.vocab:
                    continue
                wi = self.vocab[word]
                # context window
                for j in range(max(0, i - self.window), min(len(seq), i + self.window + 1)):
                    if i == j:
                        continue
                    ctx = seq[j]
                    if ctx not in self.vocab:
                        continue
                    ci = self.vocab[ctx]
                    # 距離加權：越近權重越高
                    dist = abs(i - j)
                    cooccur[wi][ci] += 1.0 / dist

        # PPMI (Positive Pointwise Mutual Information)
        total = cooccur.sum()
        if total == 0:
            return stats

        row_sums = cooccur.sum(axis=1, keepdims=True)
        col_sums = cooccur.sum(axis=0, keepdims=True)

        with np.errstate(divide='ignore', invalid='ignore'):
            pmi = np.log2((cooccur * total) / (row_sums * col_sums))

        pmi[~np.isfinite(pmi)] = 0
        ppmi = np.maximum(pmi, 0)  # 只保留正值

        # SVD 降維
        actual_dim = min(self.dim, vocab_size - 1)
        try:
            U, S, Vt = np.linalg.svd(ppmi, full_matrices=False)
            self.vectors = U[:, :actual_dim] * np.sqrt(S[:actual_dim])
        except np.linalg.LinAlgError:
            # fallback: 直接用 PPMI 的前 N 列
            self.vectors = ppmi[:, :actual_dim]

        # L2 正規化
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        self.vectors /= norms

        self.trained = True
        self.dim = actual_dim

        stats["vocab_size"] = vocab_size
        stats["embedding_dim"] = self.dim
        return stats

    def similar(self, degree, top_k=5):
        """找出最相似的和弦級數

        Args:
            degree: e.g. "IIm7"
            top_k: 回傳數量

        Returns:
            list of (degree, similarity_score)
        """
        if not self.trained or degree not in self.vocab:
            return []

        idx = self.vocab[degree]
        vec = self.vectors[idx]

        # Cosine similarity
        sims = self.vectors @ vec
        # 排除自己
        sims[idx] = -1

        top_indices = np.argsort(sims)[::-1][:top_k]
        return [(self.index2word[i], float(sims[i])) for i in top_indices]

    def get_vector(self, degree):
        """取得和弦向量"""
        if not self.trained or degree not in self.vocab:
            return None
        return self.vectors[self.vocab[degree]].tolist()

    def analogy(self, a, b, c, top_k=3):
        """和弦類比：a 之於 b 如同 c 之於 ?
        例: I:IV = V:? → 可能是 I（因為 V→I 和 I→IV 是相同的四度關係）
        """
        if not self.trained:
            return []
        if a not in self.vocab or b not in self.vocab or c not in self.vocab:
            return []

        va = self.vectors[self.vocab[a]]
        vb = self.vectors[self.vocab[b]]
        vc = self.vectors[self.vocab[c]]

        # b - a + c = ?
        target = vb - va + vc
        target /= (np.linalg.norm(target) + 1e-8)

        sims = self.vectors @ target
        # 排除 a, b, c
        for w in (a, b, c):
            sims[self.vocab[w]] = -1

        top_indices = np.argsort(sims)[::-1][:top_k]
        return [(self.index2word[i], float(sims[i])) for i in top_indices]

    def get_stats(self):
        return {
            "trained": self.trained,
            "vocab_size": len(self.vocab),
            "embedding_dim": self.dim,
        }


# ---- Singleton ----
_model = None


def get_chord2vec(chords_dir=None):
    global _model
    if _model is None:
        if chords_dir is None:
            chords_dir = Path(__file__).parent.parent.parent / "data" / "chords"
        _model = Chord2Vec(dim=32, window=3)
        _model.train(str(chords_dir))
    return _model


# ---- CLI 測試 ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = "W:/data/chords"
    print(f"Training Chord2Vec from: {data_dir}")

    model = Chord2Vec(dim=32, window=3)
    stats = model.train(data_dir)
    print(f"Songs: {stats['total_songs']}, Vocab: {stats.get('vocab_size', '?')}, Dim: {stats.get('embedding_dim', '?')}")

    print("\n=== 相似和弦 ===")
    for deg in ["I", "IV", "V", "V7", "IIm7", "VIm", "Im"]:
        sims = model.similar(deg, top_k=5)
        sim_str = ", ".join(f"{d}({s:.2f})" for d, s in sims)
        print(f"  {deg:8s} ≈ {sim_str}")

    print("\n=== 類比測試 ===")
    analogies = [
        ("I", "IV", "V"),    # I→IV 的關係, V→? 應該是 I
        ("I", "V", "IV"),    # I→V 的關係, IV→? 應該是 I
        ("IIm", "V", "IIm7"),  # IIm→V, IIm7→? 應該是 V7
    ]
    for a, b, c in analogies:
        results = model.analogy(a, b, c, top_k=3)
        res_str = ", ".join(f"{d}({s:.2f})" for d, s in results)
        print(f"  {a}:{b} = {c}:? → {res_str}")
