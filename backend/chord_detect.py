"""
音訊和弦自動偵測 v4 — BTC (Bi-directional Transformer for Chords)
使用 ISMIR 2019 預訓練 Transformer 模型，取代傳統 DSP + 模板比對
"""

import os
import sys
import numpy as np
import torch
import librosa

# BTC 模型路徑
BTC_DIR = os.path.join(os.path.dirname(__file__), "btc")
sys.path.insert(0, BTC_DIR)

# ---------------------------------------------------------------------------
# 和弦名稱轉換（BTC 輸出 "C:min7" → 本站用 "Cm7"）
# ---------------------------------------------------------------------------

_QUALITY_MAP = {
    "": "",          # major
    ":min": "m",
    ":maj": "",
    ":dim": "dim",
    ":aug": "aug",
    ":min6": "m6",
    ":maj6": "6",
    ":min7": "m7",
    ":minmaj7": "m(maj7)",
    ":maj7": "maj7",
    ":7": "7",
    ":dim7": "dim7",
    ":hdim7": "m7b5",
    ":sus2": "sus2",
    ":sus4": "sus4",
}

# 根音正規化（# → 常用名稱）
_ROOT_NORMALIZE = {
    "C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb",
}


def _btc_to_standard(btc_chord: str) -> str:
    """將 BTC 格式 (C:min7) 轉換為標準格式 (Cm7)"""
    if btc_chord in ("N", "X"):
        return "N"

    # 分離根音和品質
    if ":" in btc_chord:
        root, quality = btc_chord.split(":", 1)
        quality = ":" + quality
    else:
        root = btc_chord
        quality = ""

    # 根音正規化
    root = _ROOT_NORMALIZE.get(root, root)

    # 品質轉換
    suffix = _QUALITY_MAP.get(quality, quality.replace(":", ""))

    return root + suffix


# ---------------------------------------------------------------------------
# BTC 模型（延遲載入，單例）
# ---------------------------------------------------------------------------

_model = None
_config = None
_mean = None
_std = None
_idx_to_chord = None
_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _load_model():
    """延遲載入 BTC 模型（只載入一次）"""
    global _model, _config, _mean, _std, _idx_to_chord

    if _model is not None:
        return

    from btc_model import BTC_model
    from utils.hparams import HParams
    from utils.mir_eval_modules import idx2voca_chord

    old_cwd = os.getcwd()
    os.chdir(BTC_DIR)

    _config = HParams.load("run_config.yaml")
    _config.feature["large_voca"] = True
    _config.model["num_chords"] = 170

    _model = BTC_model(config=_config.model)
    checkpoint = torch.load(
        os.path.join(BTC_DIR, "btc_model_large_voca.pt"),
        map_location=_device, weights_only=False
    )
    _mean = checkpoint["mean"]
    _std = checkpoint["std"]
    _model.load_state_dict(checkpoint["model"])
    
    # 針對極新顯卡 (如 RTX 5080) 的防呆機制：如果 PyTorch 尚未支援該架構，自動退回 CPU
    try:
        _model.to(_device)
        # 測試一下 GPU 否會因為 kernel image 錯誤當掉
        test_tensor = torch.zeros(1, dtype=torch.float32).to(_device)
    except RuntimeError as e:
        print(f"\n⚠️ 發現 GPU 架構過新導致無法推論 ({e})。正在降級為純粹的 i9 CPU 暴力運算模式...")
        _device = torch.device("cpu")
        _model.to(_device)
        
    _model.eval()

    _idx_to_chord = idx2voca_chord()

    os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# 特徵提取（與 BTC 原始程式碼一致）
# ---------------------------------------------------------------------------

def _audio_to_features(audio_path: str):
    """提取 CQT 特徵（與 BTC 訓練時一致）"""
    sr = _config.mp3["song_hz"]  # 22050
    n_bins = _config.feature["n_bins"]  # 144
    bins_per_octave = _config.feature["bins_per_octave"]  # 24
    hop_length = _config.feature["hop_length"]  # 2048
    inst_len = _config.mp3["inst_len"]  # 10.0

    y, _ = librosa.load(audio_path, sr=sr, mono=True)

    # 分段計算 CQT（與原始程式碼一致，避免 OOM）
    feature = None
    pos = 0
    seg_samples = int(sr * inst_len)
    while pos + seg_samples < len(y):
        chunk = librosa.cqt(y[pos:pos + seg_samples], sr=sr,
                            n_bins=n_bins, bins_per_octave=bins_per_octave,
                            hop_length=hop_length)
        feature = chunk if feature is None else np.concatenate((feature, chunk), axis=1)
        pos += seg_samples

    # 最後一段
    if pos < len(y):
        chunk = librosa.cqt(y[pos:], sr=sr, n_bins=n_bins,
                            bins_per_octave=bins_per_octave, hop_length=hop_length)
        feature = chunk if feature is None else np.concatenate((feature, chunk), axis=1)

    feature = np.log(np.abs(feature) + 1e-6)
    fps = inst_len / _config.model["timestep"]  # 每個 frame 對應的秒數
    duration = len(y) / sr

    return feature, fps, duration


# ---------------------------------------------------------------------------
# 推論
# ---------------------------------------------------------------------------

def _run_btc(audio_path: str) -> list:
    """
    執行 BTC 推論，回傳原始片段列表。
    Returns: [(start, end, chord_btc_format), ...]
    """
    _load_model()

    feature, fps, duration = _audio_to_features(audio_path)

    # 正規化
    feature = feature.T
    feature = (feature - _mean) / _std
    n_ts = _config.model["timestep"]

    num_pad = n_ts - (feature.shape[0] % n_ts)
    feature = np.pad(feature, ((0, num_pad), (0, 0)), mode="constant", constant_values=0)
    n_inst = feature.shape[0] // n_ts

    lines = []
    start = 0.0
    with torch.no_grad():
        ft = torch.tensor(feature, dtype=torch.float32).unsqueeze(0).to(_device)
        for t in range(n_inst):
            out, _ = _model.self_attn_layers(ft[:, n_ts * t:n_ts * (t + 1), :])
            pred, _ = _model.output_layer(out)
            pred = pred.squeeze()
            for i in range(n_ts):
                if t == 0 and i == 0:
                    prev = pred[i].item()
                    continue
                if pred[i].item() != prev:
                    lines.append((start, fps * (n_ts * t + i), _idx_to_chord[prev]))
                    start = fps * (n_ts * t + i)
                    prev = pred[i].item()
                if t == n_inst - 1 and i + num_pad == n_ts:
                    if start != fps * (n_ts * t + i):
                        lines.append((start, fps * (n_ts * t + i), _idx_to_chord[prev]))
                    break

    return lines


# ---------------------------------------------------------------------------
# 後處理：合併短片段、統一格式
# ---------------------------------------------------------------------------

def _merge_segments(raw_segments: list, min_dur: float = 0.5) -> list:
    """
    合併過短的片段，轉換和弦名稱為標準格式。

    策略：
    1. 過濾 N (silence)
    2. 合併相鄰同名和弦
    3. 過短片段（< min_dur）併入前一個
    4. 再次合併
    """
    # 轉換為標準格式
    converted = []
    for start, end, chord in raw_segments:
        std_name = _btc_to_standard(chord)
        if std_name == "N":
            continue
        converted.append({"time": round(start, 2), "end": round(end, 2), "chord": std_name})

    # 合併相鄰同名
    merged = []
    for seg in converted:
        if merged and merged[-1]["chord"] == seg["chord"]:
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))

    # 過濾太短
    filtered = []
    for seg in merged:
        dur = seg["end"] - seg["time"]
        if dur < min_dur and filtered:
            filtered[-1]["end"] = seg["end"]
        else:
            filtered.append(seg)

    # 再合併
    final = []
    for seg in filtered:
        if final and final[-1]["chord"] == seg["chord"]:
            final[-1]["end"] = seg["end"]
        else:
            final.append(seg)

    return final


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def detect_chords(audio_path: str, min_dur: float = 0.5) -> list:
    """
    從音訊檔案偵測和弦（v4: BTC Transformer）。

    Returns:
        [{"time": 0.0, "end": 4.5, "chord": "Cm7"}, ...]
    """
    raw = _run_btc(audio_path)
    return _merge_segments(raw, min_dur=min_dur)


def detect_key(audio_path: str) -> str:
    """
    從偵測到的和弦推導調性。
    """
    chords = detect_chords(audio_path)
    if not chords:
        return "C"
    return _key_from_chords(chords)


# ---------------------------------------------------------------------------
# 調性推導（從和弦序列）
# ---------------------------------------------------------------------------

NOTE_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# 各調性的順階和弦
_MAJOR_SCALE = [
    (0, ""), (2, "m"), (4, "m"), (5, ""), (7, ""), (9, "m"), (11, "dim"),
    (0, "maj7"), (2, "m7"), (4, "m7"), (5, "maj7"), (7, "7"), (9, "m7"),
]
_MINOR_SCALE = [
    (0, "m"), (2, "dim"), (3, ""), (5, "m"), (7, "m"), (8, ""), (10, ""),
    (0, "m7"), (3, "maj7"), (5, "m7"), (7, "7"), (8, "maj7"), (10, "7"),
    (2, "m7"), (2, "m"), (0, "7"),
]

_ROOT_MAP = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4,
    "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11,
}


def _key_from_chords(chords: list) -> str:
    """從和弦序列推導調性"""
    chord_weight = {}
    for c in chords:
        name = c["chord"]
        dur = c["end"] - c["time"]
        chord_weight[name] = chord_weight.get(name, 0) + dur

    best_key = "C"
    best_score = -1

    for tonic in range(12):
        score_major = sum(
            chord_weight.get(NOTE_NAMES[(tonic + iv) % 12] + suffix, 0)
            for iv, suffix in _MAJOR_SCALE
        )
        score_minor = sum(
            chord_weight.get(NOTE_NAMES[(tonic + iv) % 12] + suffix, 0)
            for iv, suffix in _MINOR_SCALE
        )

        if score_major > best_score:
            best_score = score_major
            best_key = NOTE_NAMES[tonic]
        if score_minor > best_score:
            best_score = score_minor
            best_key = NOTE_NAMES[tonic] + "m"

    return best_key


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
