"""
音訊和弦自動偵測 v3
策略：HPSS → CQT chroma (tuning-corrected) → bar-level → HMM Viterbi decoding
HMM 用轉移機率偏好常見和弦進行（ii-V-I 等），大幅改善偵測準確度
"""

import numpy as np
import librosa

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# ---------------------------------------------------------------------------
# 和弦模板
# ---------------------------------------------------------------------------
CHORD_TYPES = {
    "":     ([0, 4, 7],     [1.0, 0.9, 0.9]),       # Major
    "m":    ([0, 3, 7],     [1.0, 0.9, 0.9]),       # Minor
    "7":    ([0, 4, 7, 10], [1.0, 0.8, 0.8, 0.7]),  # Dom7
    "m7":   ([0, 3, 7, 10], [1.0, 0.8, 0.8, 0.7]),  # Min7
    "maj7": ([0, 4, 7, 11], [1.0, 0.8, 0.8, 0.65]), # Maj7
    "dim":  ([0, 3, 6],     [1.0, 0.9, 0.9]),       # Dim
    "aug":  ([0, 4, 8],     [1.0, 0.9, 0.9]),       # Aug
    "sus4": ([0, 5, 7],     [1.0, 0.9, 0.9]),       # Sus4
}

CHORD_NAMES = []  # ["C", "Cm", "C7", "Cm7", ..., "N"]
CHORD_TEMPLATES = []  # (N, 12) matrix

def _build():
    for i, root in enumerate(NOTE_NAMES):
        for suffix, (intervals, weights) in CHORD_TYPES.items():
            t = np.zeros(12)
            for iv, w in zip(intervals, weights):
                t[(i + iv) % 12] = w
            t = t / (np.linalg.norm(t) + 1e-8)
            CHORD_NAMES.append(root + suffix)
            CHORD_TEMPLATES.append(t)
    # "N" = no chord / silence
    CHORD_NAMES.append("N")
    CHORD_TEMPLATES.append(np.zeros(12))

_build()

N_CHORDS = len(CHORD_NAMES)
TMPL_MATRIX = np.array(CHORD_TEMPLATES)  # (N_CHORDS, 12)

# ---------------------------------------------------------------------------
# HMM 轉移機率（偏好常見和弦進行）
# ---------------------------------------------------------------------------

def _build_transition_matrix() -> np.ndarray:
    """
    建立和弦轉移機率矩陣。
    - 自我轉移（維持同一和弦）機率最高
    - 常見進行（ii-V-I, IV-V-I 等）給予較高機率
    - 不常見轉移給予低但非零的機率
    """
    n = N_CHORDS
    # 基礎：均勻分佈（很低的底線）
    T = np.full((n, n), 0.001)

    # 自我轉移（不能太高，否則過度平滑）
    for i in range(n):
        T[i, i] = 0.25

    # 和弦類型索引
    def _idx(root_i, suffix):
        name = NOTE_NAMES[root_i % 12] + suffix
        try:
            return CHORD_NAMES.index(name)
        except ValueError:
            return -1

    # 常見轉移規則（相對於每個根音）
    for root in range(12):
        types = ["", "m", "7", "m7", "maj7", "dim", "aug", "sus4"]
        for src_type in types:
            src = _idx(root, src_type)
            if src < 0:
                continue

            # 同根音不同類型（如 C → Cm, C7 → C）
            for dst_type in types:
                dst = _idx(root, dst_type)
                if dst >= 0 and dst != src:
                    T[src, dst] = max(T[src, dst], 0.05)

            # 四度上行（最常見：C → F, Dm → G 等）
            for dst_type in types:
                dst = _idx((root + 5) % 12, dst_type)
                if dst >= 0:
                    T[src, dst] = max(T[src, dst], 0.08)

            # 五度上行（V-I: G → C）
            for dst_type in types:
                dst = _idx((root + 7) % 12, dst_type)
                if dst >= 0:
                    T[src, dst] = max(T[src, dst], 0.08)

            # 二度上行（ii → iii, etc）
            for dst_type in types:
                dst = _idx((root + 2) % 12, dst_type)
                if dst >= 0:
                    T[src, dst] = max(T[src, dst], 0.04)

            # 半音移動（chromatic approach）
            for delta in [1, 11]:  # +1, -1 semitone
                for dst_type in types:
                    dst = _idx((root + delta) % 12, dst_type)
                    if dst >= 0:
                        T[src, dst] = max(T[src, dst], 0.02)

            # 常見爵士進行
            # ii-V: root → root+5 (Dm7 → G7)
            dst = _idx((root + 5) % 12, "7")
            if dst >= 0 and src_type in ("m7", "m"):
                T[src, dst] = max(T[src, dst], 0.12)

            # V-I: root → root+5 (G7 → C)
            if src_type == "7":
                for dst_type in ["", "m", "m7", "maj7"]:
                    dst = _idx((root + 5) % 12, dst_type)
                    if dst >= 0:
                        T[src, dst] = max(T[src, dst], 0.12)

            # IV-I: root → root-5 = root+7
            for dst_type in ["", "m", "7"]:
                dst = _idx((root + 7) % 12, dst_type)
                if dst >= 0:
                    T[src, dst] = max(T[src, dst], 0.06)

    # 正規化每一行
    for i in range(n):
        row_sum = T[i].sum()
        if row_sum > 0:
            T[i] /= row_sum

    return T


TRANS_MATRIX = _build_transition_matrix()
LOG_TRANS = np.log(TRANS_MATRIX + 1e-30)


# ---------------------------------------------------------------------------
# Viterbi 解碼
# ---------------------------------------------------------------------------

def _viterbi(obs_loglik: np.ndarray) -> list:
    """
    Viterbi 演算法找出最可能的和弦序列。
    obs_loglik: (N_CHORDS, T) 每個 frame 每個和弦的觀測對數似然
    """
    n_states, n_frames = obs_loglik.shape

    # 初始機率：均勻
    log_pi = np.full(n_states, -np.log(n_states))

    # Viterbi
    V = np.zeros((n_states, n_frames))
    B = np.zeros((n_states, n_frames), dtype=int)

    V[:, 0] = log_pi + obs_loglik[:, 0]

    for t in range(1, n_frames):
        for j in range(n_states):
            candidates = V[:, t - 1] + LOG_TRANS[:, j]
            B[j, t] = np.argmax(candidates)
            V[j, t] = candidates[B[j, t]] + obs_loglik[j, t]

    # Backtrack
    path = np.zeros(n_frames, dtype=int)
    path[-1] = np.argmax(V[:, -1])
    for t in range(n_frames - 2, -1, -1):
        path[t] = B[path[t + 1], t + 1]

    return path.tolist()


# ---------------------------------------------------------------------------
# 後處理：修正 ii-V-I 誤判
# ---------------------------------------------------------------------------

_ROOT_MAP = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
             "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
             "A#": 10, "Bb": 10, "B": 11}


def _parse_root(chord: str) -> int:
    """取得和弦根音的半音值"""
    if len(chord) >= 2 and chord[1] in ('#', 'b'):
        return _ROOT_MAP.get(chord[:2], -1)
    return _ROOT_MAP.get(chord[:1], -1)


def _fix_ii_v_i(segments: list) -> list:
    """
    修正常見的誤判模式：
    - Xmaj7→Ym7 如果 X-Y 是五度關係 → 改為 Xm7→Y7
    - Xsus4 前後都是同根音和弦 → 合併
    """
    if len(segments) < 2:
        return segments

    result = list(segments)

    for i in range(len(result) - 1):
        a = result[i]["chord"]
        b = result[i + 1]["chord"]
        root_a = _parse_root(a)
        root_b = _parse_root(b)

        if root_a < 0 or root_b < 0:
            continue

        interval = (root_b - root_a) % 12

        # Xmaj7 → Ym7 where Y is 5 semitones up → likely Xm7 → Y7
        if "maj7" in a and "m7" in b and interval == 5:
            base_a = a.replace("maj7", "")
            base_b = b.replace("m7", "")
            result[i] = {**result[i], "chord": base_a + "m7"}
            result[i + 1] = {**result[i + 1], "chord": base_b + "7"}

        # Xmaj7 → Ym where Y is 5 semitones up → likely Xm → Y7
        elif "maj7" in a and b.endswith("m") and not "m7" in b and interval == 5:
            base_a = a.replace("maj7", "")
            result[i] = {**result[i], "chord": base_a + "m"}

        # Gsus4 in context of Cm → likely G7
        elif "sus4" in a:
            # If next chord resolves down a 5th (G→C), this is V-I
            if interval == 5:
                base = a.replace("sus4", "")
                result[i] = {**result[i], "chord": base + "7"}

    return result


# ---------------------------------------------------------------------------
# 觀測似然
# ---------------------------------------------------------------------------

def _compute_obs_loglik(bar_chroma: np.ndarray) -> np.ndarray:
    """
    計算每個 bar 每個和弦的觀測對數似然。
    使用餘弦相似度作為似然。
    """
    n_bars = bar_chroma.shape[1]
    loglik = np.zeros((N_CHORDS, n_bars))

    for t in range(n_bars):
        frame = bar_chroma[:, t]
        energy = np.sum(frame)

        if energy < 0.05:
            # 靜音：N (silence) 給高分
            loglik[:, t] = -10.0
            loglik[N_CHORDS - 1, t] = 0.0  # N chord
            continue

        frame_norm = frame / (np.linalg.norm(frame) + 1e-8)
        # 餘弦相似度
        similarities = TMPL_MATRIX @ frame_norm  # (N_CHORDS,)
        # 轉換為對數似然（放大差異讓觀測主導而非轉移機率）
        loglik[:, t] = similarities * 12.0

    return loglik


# ---------------------------------------------------------------------------
# Chroma 提取
# ---------------------------------------------------------------------------

def _extract_bar_chroma(y: np.ndarray, sr: int) -> tuple:
    """
    提取 bar-synchronous chroma 特徵。
    """
    # 1. HPSS
    y_harmonic = librosa.effects.harmonic(y, margin=4.0)

    # 2. Beat tracking
    tempo, beat_frames = librosa.beat.beat_track(
        y=y_harmonic, sr=sr, hop_length=512, trim=False
    )

    if len(beat_frames) < 4:
        hop = 512
        n_frames = len(y_harmonic) // hop
        beat_frames = np.arange(0, n_frames, int(sr * 0.5 / hop))

    # 3. Tuning-corrected CQT chroma
    tuning = librosa.estimate_tuning(y=y, sr=sr)
    chroma = librosa.feature.chroma_cqt(
        y=y_harmonic, sr=sr, hop_length=512,
        tuning=tuning, n_chroma=12
    )

    # 4. Half-bar aggregation（每 2 拍）
    #    在爵士/流行中和弦可能每半小節變化（如 ii-V turnaround）
    beats_per_seg = 2
    bar_frames = beat_frames[::beats_per_seg]
    bar_chroma = librosa.util.sync(chroma, bar_frames, aggregate=np.mean)

    # 5. L2 正規化
    norms = np.linalg.norm(bar_chroma, axis=0, keepdims=True) + 1e-8
    bar_chroma = bar_chroma / norms

    bar_times = librosa.frames_to_time(bar_frames, sr=sr, hop_length=512)

    return bar_chroma, bar_times


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def detect_chords(audio_path: str, min_beats: int = 1) -> list:
    """
    從音訊檔案偵測和弦（v3: HMM Viterbi）。
    """
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    duration = len(y) / sr

    bar_chroma, bar_times = _extract_bar_chroma(y, sr)

    # 觀測似然
    obs_loglik = _compute_obs_loglik(bar_chroma)

    # Viterbi 解碼
    path = _viterbi(obs_loglik)

    # 轉為片段
    segments = []
    n_bars = len(path)
    i = 0
    while i < n_bars:
        chord_idx = path[i]
        chord_name = CHORD_NAMES[chord_idx]
        start = float(bar_times[i]) if i < len(bar_times) else 0
        j = i + 1
        while j < n_bars and path[j] == chord_idx:
            j += 1
        end = float(bar_times[j]) if j < len(bar_times) else duration

        if chord_name != "N":
            segments.append({
                "time": round(start, 2),
                "end": round(end, 2),
                "chord": chord_name,
            })
        i = j

    # 合併相鄰同名和弦
    merged = []
    for seg in segments:
        if merged and merged[-1]["chord"] == seg["chord"]:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(seg)

    # 過濾太短的片段（< 1.5 秒）：併入前一個或後一個
    MIN_DUR = 1.0
    filtered = []
    for seg in merged:
        dur = seg["end"] - seg["time"]
        if dur < MIN_DUR:
            # 嘗試併入前一個
            if filtered:
                filtered[-1]["end"] = seg["end"]
            # else: 丟棄開頭的超短片段
        else:
            filtered.append(seg)

    # 再次合併
    final = []
    for seg in filtered:
        if final and final[-1]["chord"] == seg["chord"]:
            final[-1]["end"] = seg["end"]
        else:
            final.append(seg)

    # 後處理：修正常見的 HMM 誤判
    # Dmaj7→Gm7→Cm7 其實是 Dm7→G7→Cm7（ii-V-i）
    # 同理：任何 Xmaj7→Ym7 後接 Z 且距離為 ii-V-i 關係
    final = _fix_ii_v_i(final)

    return final


# ---------------------------------------------------------------------------
# Key detection
# ---------------------------------------------------------------------------

_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                           2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                           2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def detect_key(audio_path: str) -> str:
    """
    偵測調性：先偵測和弦，再從和弦分佈推導調性。
    比純 chroma profile 更準確，因為 V 和弦不會被誤認為主調。
    """
    chords = detect_chords(audio_path)
    if not chords:
        return _detect_key_chroma(audio_path)

    return _key_from_chords(chords)


def _key_from_chords(chords: list) -> str:
    """
    從和弦序列推導調性。
    對每個可能的調性，計算和弦在該調性中的「適配度」。
    """
    # 各調性的順階和弦（大調 + 小調）
    # Major: I, ii, iii, IV, V, vi, vii°
    # Minor: i, ii°, III, iv, v/V, VI, VII
    MAJOR_SCALE = [
        (0, ""), (2, "m"), (4, "m"), (5, ""), (7, ""), (9, "m"), (11, "dim"),
        # 七和弦版本
        (0, "maj7"), (2, "m7"), (4, "m7"), (5, "maj7"), (7, "7"), (9, "m7"),
    ]
    MINOR_SCALE = [
        (0, "m"), (2, "dim"), (3, ""), (5, "m"), (7, "m"), (8, ""), (10, ""),
        # 七和弦版本
        (0, "m7"), (3, "maj7"), (5, "m7"), (7, "7"), (8, "maj7"), (10, "7"),
        # 爵士常用：Dorian ii = m7（非 dim）, 和聲小調 V7
        (2, "m7"), (2, "m"),
        # 也常見 C7（小調中的 I7 藍調）
        (0, "7"),
    ]

    # 計算每個和弦的加權時長
    chord_weight = {}
    for c in chords:
        name = c["chord"]
        dur = c["end"] - c["time"]
        chord_weight[name] = chord_weight.get(name, 0) + dur

    best_key = "C"
    best_score = -1

    for tonic in range(12):
        # Major key score
        score_major = 0
        for interval, suffix in MAJOR_SCALE:
            target = NOTE_NAMES[(tonic + interval) % 12] + suffix
            score_major += chord_weight.get(target, 0)

        # Minor key score
        score_minor = 0
        for interval, suffix in MINOR_SCALE:
            target = NOTE_NAMES[(tonic + interval) % 12] + suffix
            score_minor += chord_weight.get(target, 0)

        if score_major > best_score:
            best_score = score_major
            best_key = NOTE_NAMES[tonic]
        if score_minor > best_score:
            best_score = score_minor
            best_key = NOTE_NAMES[tonic] + "m"

    return best_key


def _detect_key_chroma(audio_path: str) -> str:
    """備援：純 chroma profile 調性偵測"""
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    y_harmonic = librosa.effects.harmonic(y, margin=4.0)

    tuning = librosa.estimate_tuning(y=y, sr=sr)
    chroma = librosa.feature.chroma_cqt(
        y=y_harmonic, sr=sr, hop_length=512, tuning=tuning
    )

    rms = librosa.feature.rms(y=y_harmonic, hop_length=512)[0]
    min_len = min(len(rms), chroma.shape[1])
    rms = rms[:min_len]
    chroma = chroma[:, :min_len]
    weights = rms / (np.sum(rms) + 1e-8)
    mean_chroma = chroma @ weights

    best_key = "C"
    best_corr = -1

    for i in range(12):
        rolled = np.roll(mean_chroma, -i)
        corr_major = float(np.corrcoef(rolled, _MAJOR_PROFILE)[0, 1])
        corr_minor = float(np.corrcoef(rolled, _MINOR_PROFILE)[0, 1])

        if corr_major > best_corr:
            best_corr = corr_major
            best_key = NOTE_NAMES[i]
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_key = NOTE_NAMES[i] + "m"

    return best_key


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python chord_detect.py <audio_file>")
        sys.exit(1)

    path = sys.argv[1]
    print(f"Detecting: {path}")

    key = detect_key(path)
    print(f"Key: {key}")

    chords = detect_chords(path)
    print(f"Chords: {len(chords)}")
    for c in chords:
        print(f"  {c['time']:6.1f}s - {c['end']:6.1f}s  {c['chord']}")
