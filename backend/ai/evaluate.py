"""
和弦 AI 模型評測機制
1. Perplexity (困惑度)
2. Pitch-Class Overlap (音高重疊率)
3. 和弦預測準確度
"""

import math
import numpy as np
from collections import Counter

from .preprocess import (
    build_training_data, NOTE_TO_SEMI, SEMI_TO_NOTE,
    parse_chord_name, chord_to_degree
)
from .markov import get_predictor


# 和弦品質 → 組成音的半音偏移
QUALITY_INTERVALS = {
    "": [0, 4, 7],
    "m": [0, 3, 7],
    "7": [0, 4, 7, 10],
    "m7": [0, 3, 7, 10],
    "maj7": [0, 4, 7, 11],
    "m7b5": [0, 3, 6, 10],
    "dim": [0, 3, 6],
    "dim7": [0, 3, 6, 9],
    "aug": [0, 4, 8],
    "9": [0, 4, 7, 10, 14],
    "m9": [0, 3, 7, 10, 14],
    "maj9": [0, 4, 7, 11, 14],
    "13": [0, 4, 7, 10, 14, 21],
    "sus4": [0, 5, 7],
    "sus2": [0, 2, 7],
    "6": [0, 4, 7, 9],
    "m6": [0, 3, 7, 9],
    "add9": [0, 4, 7, 14],
}


def _get_pitch_classes(chord_str):
    """取得和弦的音高類別集合 (set of 0-11)"""
    root_semi, quality = parse_chord_name(chord_str)
    if root_semi is None:
        return set()

    intervals = QUALITY_INTERVALS.get(quality, QUALITY_INTERVALS.get("", [0, 4, 7]))
    return {(root_semi + iv) % 12 for iv in intervals}


def pitch_class_overlap(chord_a, chord_b):
    """計算兩個和弦的音高重疊率 (0~1)"""
    pca = _get_pitch_classes(chord_a)
    pcb = _get_pitch_classes(chord_b)
    if not pca or not pcb:
        return 0.0
    intersection = pca & pcb
    union = pca | pcb
    return len(intersection) / len(union) if union else 0.0


def perplexity(predictor, test_sequences):
    """計算 Markov 模型在測試序列上的困惑度

    Perplexity = 2^(-1/N * sum(log2(P(next|context))))
    越低越好（模型越有信心）
    """
    total_log_prob = 0.0
    total_tokens = 0

    for seq in test_sequences:
        for i in range(1, len(seq)):
            context = seq[max(0, i - 2):i]
            target = seq[i]

            predictions = predictor.predict(context, top_k=100)
            prob_dict = dict(predictions)
            prob = prob_dict.get(target, 1e-6)  # smoothing

            total_log_prob += math.log2(prob)
            total_tokens += 1

    if total_tokens == 0:
        return float("inf")

    avg_log_prob = total_log_prob / total_tokens
    return 2 ** (-avg_log_prob)


def prediction_accuracy(predictor, test_sequences, top_k=3):
    """計算 top-k 預測準確度"""
    correct = 0
    total = 0

    for seq in test_sequences:
        for i in range(1, len(seq)):
            context = seq[max(0, i - 2):i]
            target = seq[i]

            predictions = predictor.predict(context, top_k=top_k)
            predicted = [d for d, _ in predictions]

            if target in predicted:
                correct += 1
            total += 1

    return correct / total if total > 0 else 0.0


def evaluate_reharmonization(original_chords, jazzified_chords):
    """評測重配和聲的品質

    Returns:
        dict with metrics
    """
    if not original_chords or not jazzified_chords:
        return {"error": "empty input"}

    # 1. 平均音高重疊率（原和弦 vs 對應的 jazz 和弦）
    overlaps = []
    min_len = min(len(original_chords), len(jazzified_chords))
    j = 0
    for i in range(len(original_chords)):
        if j >= len(jazzified_chords):
            break
        orig = original_chords[i]
        jazz = jazzified_chords[j]
        # 跳過插入的和弦
        while j < len(jazzified_chords) and jazzified_chords[j].get("inserted"):
            j += 1
        if j >= len(jazzified_chords):
            break
        jazz = jazzified_chords[j]
        ov = pitch_class_overlap(orig.get("chord", ""), jazz.get("chord", ""))
        overlaps.append(ov)
        j += 1

    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

    # 2. 插入和弦數量
    inserted_count = sum(1 for c in jazzified_chords if c.get("inserted"))

    # 3. 延伸音增加數量
    extension_count = 0
    for i in range(min(len(original_chords), len(jazzified_chords))):
        orig_q = original_chords[i].get("chord", "")
        jazz_q = jazzified_chords[i].get("chord", "")
        if len(jazz_q) > len(orig_q):
            extension_count += 1

    return {
        "avg_pitch_overlap": round(avg_overlap, 3),
        "inserted_chords": inserted_count,
        "extension_upgrades": extension_count,
        "original_count": len(original_chords),
        "jazzified_count": len(jazzified_chords),
        "quality_grade": "A" if avg_overlap > 0.5 else "B" if avg_overlap > 0.3 else "C",
    }


def full_evaluation(chords_dir):
    """完整模型評測"""
    degree_sequences, _, stats = build_training_data(chords_dir)

    if len(degree_sequences) < 5:
        return {"error": "not enough data"}

    # 80/20 split
    split = int(len(degree_sequences) * 0.8)
    train_seqs = degree_sequences[:split]
    test_seqs = degree_sequences[split:]

    # Train a fresh predictor on train set
    from .markov import ChordPredictor
    predictor = ChordPredictor()
    # 手動餵入 train sequences
    for seq in train_seqs:
        for i in range(len(seq) - 1):
            predictor.bigram[seq[i]][seq[i + 1]] += 1
            predictor.total_transitions += 1
            if i > 0:
                predictor.trigram[(seq[i - 1], seq[i])][seq[i + 1]] += 1

    # 評測
    ppl = perplexity(predictor, test_seqs)
    acc_top1 = prediction_accuracy(predictor, test_seqs, top_k=1)
    acc_top3 = prediction_accuracy(predictor, test_seqs, top_k=3)
    acc_top5 = prediction_accuracy(predictor, test_seqs, top_k=5)

    return {
        "dataset": {
            "total_songs": stats["total_songs"],
            "train_songs": len(train_seqs),
            "test_songs": len(test_seqs),
            "total_chord_changes": stats["total_chord_changes"],
        },
        "markov_model": {
            "perplexity": round(ppl, 2),
            "accuracy_top1": round(acc_top1, 3),
            "accuracy_top3": round(acc_top3, 3),
            "accuracy_top5": round(acc_top5, 3),
        },
    }


# ---- CLI ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = "W:/data/chords"
    print("Running full evaluation...")
    results = full_evaluation(data_dir)

    print(f"\n=== 資料集 ===")
    for k, v in results.get("dataset", {}).items():
        print(f"  {k}: {v}")

    print(f"\n=== Markov 模型評測 ===")
    for k, v in results.get("markov_model", {}).items():
        print(f"  {k}: {v}")

    # 測試音高重疊
    print(f"\n=== 音高重疊率範例 ===")
    pairs = [("C", "Cmaj7"), ("C", "Am"), ("C", "F"), ("G", "G7"), ("Dm", "Dm7"), ("C", "F#")]
    for a, b in pairs:
        ov = pitch_class_overlap(a, b)
        print(f"  {a:6s} vs {b:6s} → {ov:.2f}")
