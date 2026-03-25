"""音訊和弦自動偵測 — 基於 librosa chroma 特徵"""

import numpy as np
import librosa


# 12 個半音對應的和弦模板（大三和弦 + 小三和弦）
NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# 和弦模板：每個和弦是一個 12 維的 chroma 向量
CHORD_TEMPLATES = {}

def _build_templates():
    """建立大三、小三、屬七、小七和弦模板"""
    for i, root in enumerate(NOTE_NAMES):
        # Major: 1, 3, 5
        t = np.zeros(12)
        t[i] = 1.0
        t[(i + 4) % 12] = 0.8
        t[(i + 7) % 12] = 0.8
        CHORD_TEMPLATES[root] = t / np.linalg.norm(t)

        # Minor: 1, b3, 5
        t = np.zeros(12)
        t[i] = 1.0
        t[(i + 3) % 12] = 0.8
        t[(i + 7) % 12] = 0.8
        CHORD_TEMPLATES[root + "m"] = t / np.linalg.norm(t)

        # Dominant 7: 1, 3, 5, b7
        t = np.zeros(12)
        t[i] = 1.0
        t[(i + 4) % 12] = 0.7
        t[(i + 7) % 12] = 0.7
        t[(i + 10) % 12] = 0.5
        CHORD_TEMPLATES[root + "7"] = t / np.linalg.norm(t)

        # Minor 7: 1, b3, 5, b7
        t = np.zeros(12)
        t[i] = 1.0
        t[(i + 3) % 12] = 0.7
        t[(i + 7) % 12] = 0.7
        t[(i + 10) % 12] = 0.5
        CHORD_TEMPLATES[root + "m7"] = t / np.linalg.norm(t)

        # Major 7: 1, 3, 5, 7
        t = np.zeros(12)
        t[i] = 1.0
        t[(i + 4) % 12] = 0.7
        t[(i + 7) % 12] = 0.7
        t[(i + 11) % 12] = 0.5
        CHORD_TEMPLATES[root + "maj7"] = t / np.linalg.norm(t)

_build_templates()


def detect_chords(audio_path: str, hop_length: int = 8192,
                  min_duration: float = 1.0,
                  merge_threshold: float = 0.5) -> list:
    """
    從音訊檔案偵測和弦。

    Args:
        audio_path: 音訊檔案路徑
        hop_length: STFT hop length（影響時間解析度）
        min_duration: 最短和弦持續時間（秒）
        merge_threshold: 合併相同和弦的時間門檻

    Returns:
        [{"time": 0.0, "end": 4.5, "chord": "C"}, ...]
    """
    # 載入音訊
    y, sr = librosa.load(audio_path, sr=22050, mono=True)

    # 計算 chroma 特徵
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    # 時間軸
    times = librosa.frames_to_time(np.arange(chroma.shape[1]),
                                    sr=sr, hop_length=hop_length)

    # 對每個 frame 匹配最佳和弦
    raw_chords = []
    for i in range(chroma.shape[1]):
        frame = chroma[:, i]
        if np.max(frame) < 0.05:
            # 靜音 frame
            raw_chords.append(("N", 0.0))
            continue

        frame_norm = frame / (np.linalg.norm(frame) + 1e-8)
        best_chord = "N"
        best_score = -1

        for name, template in CHORD_TEMPLATES.items():
            score = float(np.dot(frame_norm, template))
            if score > best_score:
                best_score = score
                best_chord = name

        raw_chords.append((best_chord, best_score))

    # 合併連續相同和弦
    merged = []
    i = 0
    while i < len(raw_chords):
        chord, score = raw_chords[i]
        start = times[i]
        j = i + 1
        while j < len(raw_chords) and raw_chords[j][0] == chord:
            j += 1
        end = times[j] if j < len(times) else times[-1] + hop_length / sr
        duration = end - start

        if chord != "N" and duration >= min_duration:
            merged.append({
                "time": round(float(start), 2),
                "end": round(float(end), 2),
                "chord": chord,
            })
        i = j

    # 再次合併：如果兩個相同和弦間隔很短
    final = []
    for item in merged:
        if (final and final[-1]["chord"] == item["chord"]
                and item["time"] - final[-1]["end"] < merge_threshold):
            final[-1]["end"] = item["end"]
        else:
            final.append(item)

    return final


def detect_key(audio_path: str) -> str:
    """偵測調性"""
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    # 平均 chroma
    mean_chroma = np.mean(chroma, axis=1)
    # Krumhansl-Schmuckler key profiles
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                              2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    best_key = "C"
    best_corr = -1

    for i in range(12):
        rolled = np.roll(mean_chroma, -i)
        corr_major = float(np.corrcoef(rolled, major_profile)[0, 1])
        corr_minor = float(np.corrcoef(rolled, minor_profile)[0, 1])

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
    print(f"偵測: {path}")

    key = detect_key(path)
    print(f"調性: {key}")

    chords = detect_chords(path)
    print(f"和弦: {len(chords)} 個")
    for c in chords:
        print(f"  {c['time']:6.1f}s - {c['end']:6.1f}s  {c['chord']}")
