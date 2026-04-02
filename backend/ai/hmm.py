"""
HMM 發射矩陣 + Viterbi 解碼器
用於 Jazzify 重配和聲的旋律相容性驗證

發射矩陣 P(melody_note | chord):
  從現有和弦資料 + melody_extractor 建立
  「某和弦下出現某旋律音的機率」

Viterbi 解碼器:
  給定旋律序列，找出全曲最優和弦路徑
"""

import math
import numpy as np
from collections import defaultdict, Counter
from pathlib import Path

from .preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE, parse_chord_name


class EmissionMatrix:
    """和弦→旋律音的發射機率 P(melody_note | chord_degree)"""

    def __init__(self):
        # chord_degree → {pitch_class: count}
        self.counts = defaultdict(Counter)
        self.total_per_chord = Counter()

    def save(self, path):
        """儲存發射矩陣至 JSON"""
        import json
        data = {
            "counts": {k: dict(v) for k, v in self.counts.items()},
            "total_per_chord": dict(self.total_per_chord),
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        """從 JSON 載入發射矩陣"""
        import json
        em = cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, v in data["counts"].items():
            em.counts[k] = Counter({int(pc): cnt for pc, cnt in v.items()})
        em.total_per_chord = Counter(data["total_per_chord"])
        return em

    def add_observation(self, chord_degree, melody_midi):
        """記錄一次觀測：在 chord_degree 下出現 melody_midi"""
        pc = melody_midi % 12  # pitch class 0-11
        self.counts[chord_degree][pc] += 1
        self.total_per_chord[chord_degree] += 1

    def probability(self, chord_degree, melody_midi, smoothing=0.01):
        """P(melody_note | chord)"""
        pc = melody_midi % 12
        if chord_degree not in self.counts:
            return 1.0 / 12  # uniform
        total = self.total_per_chord[chord_degree]
        count = self.counts[chord_degree].get(pc, 0)
        # Laplace smoothing
        return (count + smoothing) / (total + smoothing * 12)

    def log_probability(self, chord_degree, melody_midi, smoothing=0.01):
        return math.log(self.probability(chord_degree, melody_midi, smoothing))

    def get_stats(self):
        return {
            "chord_states": len(self.counts),
            "total_observations": sum(self.total_per_chord.values()),
        }

    def top_notes_for_chord(self, chord_degree, top_k=5):
        """某和弦下最常出現的旋律音"""
        if chord_degree not in self.counts:
            return []
        total = self.total_per_chord[chord_degree]
        return [
            {"note": SEMI_TO_NOTE[pc], "probability": round(cnt / total, 3)}
            for pc, cnt in self.counts[chord_degree].most_common(top_k)
        ]


class ViterbiDecoder:
    """Viterbi 解碼器：給定旋律，找最優和弦路徑"""

    def __init__(self, transition_probs, emission_matrix, states):
        """
        transition_probs: dict of dict, transition_probs[from_state][to_state] = probability
        emission_matrix: EmissionMatrix instance
        states: list of possible chord degree strings
        """
        self.trans = transition_probs
        self.emission = emission_matrix
        self.states = states

    def decode(self, melody_midi_seq, top_k=5):
        """Viterbi 解碼（log-space + top-K 剪枝）

        Args:
            melody_midi_seq: [60, 64, 67, ...] MIDI 音高序列
            top_k: 每步保留前 K 個狀態（剪枝）

        Returns:
            best_path: [chord_degree, ...] 最優和弦路徑
            log_prob: 路徑的 log 機率
        """
        if not melody_midi_seq or not self.states:
            return [], float("-inf")

        T = len(melody_midi_seq)
        S = len(self.states)

        # 初始機率：均勻分佈（或可從 groove_dict 取）
        log_init = math.log(1.0 / S)

        # dp[t] = {state: (log_prob, prev_state)}
        dp = [dict() for _ in range(T)]

        # t=0: 初始化
        obs = melody_midi_seq[0]
        scores = []
        for s in self.states:
            lp = log_init + self.emission.log_probability(s, obs)
            dp[0][s] = (lp, None)
            scores.append((lp, s))

        # 剪枝：只保留 top_k
        scores.sort(reverse=True)
        active = {s for _, s in scores[:top_k]}
        dp[0] = {s: v for s, v in dp[0].items() if s in active}

        # t=1..T-1
        for t in range(1, T):
            obs = melody_midi_seq[t]
            candidates = []

            for s in self.states:
                emit_lp = self.emission.log_probability(s, obs)
                best_lp = float("-inf")
                best_prev = None

                for prev_s in dp[t - 1]:
                    prev_lp = dp[t - 1][prev_s][0]
                    # 轉移機率
                    trans_lp = math.log(
                        self.trans.get(prev_s, {}).get(s, 1e-6)
                    )
                    total = prev_lp + trans_lp + emit_lp
                    if total > best_lp:
                        best_lp = total
                        best_prev = prev_s

                if best_prev is not None:
                    dp[t][s] = (best_lp, best_prev)
                    candidates.append((best_lp, s))

            # 剪枝
            candidates.sort(reverse=True)
            active = {s for _, s in candidates[:top_k]}
            dp[t] = {s: v for s, v in dp[t].items() if s in active}

        # 回溯
        if not dp[T - 1]:
            return [], float("-inf")

        best_final = max(dp[T - 1].items(), key=lambda x: x[1][0])
        best_state = best_final[0]
        best_log_prob = best_final[1][0]

        path = [best_state]
        for t in range(T - 1, 0, -1):
            _, prev = dp[t][path[-1]]
            if prev is None:
                break
            path.append(prev)
        path.reverse()

        return path, best_log_prob


def build_emission_from_songs(chords_dir, music_dir=None):
    """從現有和弦資料 + 音檔建立發射矩陣

    用現有的 chord time segments 和 melody_extractor 配對
    """
    from .preprocess import load_all_chord_sequences, chord_to_degree, detect_key_from_chords

    emission = EmissionMatrix()
    songs = load_all_chord_sequences(chords_dir)

    if not music_dir:
        # 沒有音檔目錄，用理論推導：和弦組成音有高發射機率
        print("Building theoretical emission matrix (no audio)...")
        from .jazz_rules import parse_root_quality

        QUALITY_INTERVALS = {
            "": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10],
            "m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11],
            "m7b5": [0, 3, 6, 10], "dim": [0, 3, 6], "9": [0, 4, 7, 10, 14],
            "m9": [0, 3, 7, 10, 14], "maj9": [0, 4, 7, 11, 14],
            "sus4": [0, 5, 7], "6": [0, 4, 7, 9],
        }

        for song in songs:
            key_semi = detect_key_from_chords(song["chords"])
            for chord_name in song["chords"]:
                degree = chord_to_degree(chord_name, key_semi)
                if not degree:
                    continue
                root_semi, quality = parse_root_quality(chord_name)
                if root_semi is None:
                    continue

                intervals = QUALITY_INTERVALS.get(quality, QUALITY_INTERVALS.get("", [0, 4, 7]))
                for iv in intervals:
                    midi = 60 + (root_semi + iv) % 12
                    # 組成音：高權重
                    for _ in range(5):
                        emission.add_observation(degree, midi)
                # 音階內的其他音：低權重
                scale_intervals = [0, 2, 4, 5, 7, 9, 11]
                for iv in scale_intervals:
                    midi = 60 + (key_semi + iv) % 12
                    emission.add_observation(degree, midi)

        print(f"Built emission matrix: {emission.get_stats()}")
    else:
        # 有音檔：用 melody_extractor 配對
        print(f"Building emission matrix from audio ({music_dir})...")
        from .melody_extractor import MelodyExtractor
        import os, json

        extractor = MelodyExtractor()
        processed = 0

        for song in songs[:50]:  # 先做 50 首原型
            audio_path = os.path.join(music_dir, song["path"])
            if not os.path.isfile(audio_path):
                continue

            try:
                melody = extractor.extract_melody(audio_path)
                key_semi = detect_key_from_chords(song["chords"])

                # 對每個 melody event，找對應時間點的和弦
                chord_timeline = []
                for i, chord_name in enumerate(song["chords"]):
                    # 簡化：用 chord 序列的索引推算時間
                    degree = chord_to_degree(chord_name, key_semi)
                    if degree:
                        chord_timeline.append(degree)

                # 配對：melody → chord (用時間重疊)
                # 此處簡化為均勻分配
                if chord_timeline and melody:
                    for evt in melody:
                        # 根據時間找對應和弦
                        chord_idx = min(
                            int(evt["start"] / (song.get("duration", 180) / max(len(chord_timeline), 1))),
                            len(chord_timeline) - 1
                        )
                        emission.add_observation(chord_timeline[chord_idx], evt["midi"])

                processed += 1
                if processed % 10 == 0:
                    print(f"  Processed {processed} songs...")

            except Exception as e:
                continue

        print(f"Built emission from {processed} songs: {emission.get_stats()}")

    return emission


def build_transition_probs(chords_dir):
    """從 Markov 模型取得轉移機率字典"""
    from .markov import get_predictor

    predictor = get_predictor(chords_dir)
    trans = {}

    for state, counter in predictor.bigram.items():
        total = sum(counter.values())
        trans[state] = {s: c / total for s, c in counter.items()}

    return trans, list(predictor.bigram.keys())


def load_transition_probs(path):
    """從 JSON 載入轉移機率"""
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["transitions"], data["states"]


# ---- Singleton ----
_emission = None
_MODELS_DIR = Path(__file__).parent.parent.parent / "data" / "models"
_EMISSION_CACHE = _MODELS_DIR / "emission.json"
_TRANS_CACHE = _MODELS_DIR / "transition.json"


def get_emission(chords_dir=None):
    """取得全域 EmissionMatrix（優先從快取載入）"""
    global _emission
    if _emission is None:
        if _EMISSION_CACHE.is_file():
            _emission = EmissionMatrix.load(str(_EMISSION_CACHE))
        else:
            if chords_dir is None:
                chords_dir = Path(__file__).parent.parent.parent / "data" / "chords"
            _emission = build_emission_from_songs(str(chords_dir))
    return _emission


def get_viterbi_decoder(chords_dir=None):
    """取得 ViterbiDecoder（使用快取的矩陣）"""
    em = get_emission(chords_dir)
    if _TRANS_CACHE.is_file():
        trans, states = load_transition_probs(str(_TRANS_CACHE))
    else:
        if chords_dir is None:
            chords_dir = str(Path(__file__).parent.parent.parent / "data" / "chords")
        trans, states = build_transition_probs(chords_dir)
    return ViterbiDecoder(trans, em, states)


# ---- CLI ----
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    data_dir = "W:/data/chords"
    print("Building theoretical emission matrix...")
    emission = build_emission_from_songs(data_dir)
    print(f"Stats: {emission.get_stats()}")

    print("\n=== 各和弦下最常見旋律音 ===")
    for deg in ["I", "IV", "V", "V7", "IIm7", "VIm"]:
        notes = emission.top_notes_for_chord(deg, 5)
        n_str = ", ".join(f"{n['note']}({n['probability']:.0%})" for n in notes)
        print(f"  {deg:8s}: {n_str}")

    print("\nBuilding transition probs...")
    trans, states = build_transition_probs(data_dir)
    print(f"States: {len(states)}, Transitions: {sum(len(v) for v in trans.values())}")

    print("\n=== Viterbi 測試：C-E-G 旋律 → 最優和弦 ===")
    decoder = ViterbiDecoder(trans, emission, states[:30])  # 只用前 30 個常見狀態
    melody = [60, 64, 67, 65, 60, 64, 67, 72]  # C-E-G-F-C-E-G-C5
    path, lp = decoder.decode(melody, top_k=10)
    print(f"  Melody: {[SEMI_TO_NOTE[m % 12] for m in melody]}")
    print(f"  Path:   {path}")
    print(f"  Log P:  {lp:.2f}")
