"""
音訊和弦自動偵測 v4 — BTC (Bi-directional Transformer for Chords)
使用 ISMIR 2019 預訓練 Transformer 模型，取代傳統 DSP + 模板比對
"""

import os
import sys
import warnings
import numpy as np
import torch
import librosa
import soundfile as sf
import threading

warnings.filterwarnings("ignore", message="n_fft=.*is too large for input signal")

# 單曲最大分析長度（秒）。超長合輯（例如 29 首精選）會被截斷至此長度，
# 避免 librosa.load 產生超過 NumPy 最大陣列的情況。
MAX_ANALYZE_SECONDS = 900  # 15 分鐘

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
    global _model, _config, _mean, _std, _idx_to_chord, _device
    _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if _model is not None:
        return

    from btc_model import BTC_model  # type: ignore
    from utils.hparams import HParams  # type: ignore
    from utils.mir_eval_modules import idx2voca_chord  # type: ignore

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

def _load_audio_mono(audio_path: str, target_sr: int) -> tuple:
    """
    直接用 soundfile 載入音訊，避開 librosa.load 對 audioread 的 fallback
    （NUC 端無 ffmpeg 會噴 NoBackendError）。超長檔案截斷至 MAX_ANALYZE_SECONDS。
    回傳 (mono_float32_at_target_sr, was_truncated)
    """
    info = sf.info(audio_path)
    native_sr = info.samplerate
    max_frames = int(native_sr * MAX_ANALYZE_SECONDS)
    truncated = info.frames > max_frames
    frames_to_read = max_frames if truncated else -1
    data, _ = sf.read(audio_path, frames=frames_to_read, dtype="float32", always_2d=True)
    y = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if native_sr != target_sr:
        y = librosa.resample(y, orig_sr=native_sr, target_sr=target_sr)
    return y, truncated


def _audio_to_features(audio_path: str):
    """提取 CQT 特徵（與 BTC 訓練時一致）"""
    sr = _config.mp3["song_hz"]  # 22050
    n_bins = _config.feature["n_bins"]  # 144
    bins_per_octave = _config.feature["bins_per_octave"]  # 24
    hop_length = _config.feature["hop_length"]  # 2048
    inst_len = _config.mp3["inst_len"]  # 10.0

    y, truncated = _load_audio_mono(audio_path, sr)
    if truncated:
        print(f"[chord_detect] truncated to {MAX_ANALYZE_SECONDS}s: {audio_path}",
              file=sys.stderr)

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

_inference_lock = threading.Lock()

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

    from scipy.signal import medfilt
    
    all_preds = []
    
    # 這裡的鎖 (可能是 user 自行定義的 Semaphore 或 Lock)
    with _inference_lock:
        with torch.no_grad():
            ft = torch.tensor(feature, dtype=torch.float32).unsqueeze(0).to(_device)
            for t in range(n_inst):
                out, _ = _model.self_attn_layers(ft[:, n_ts * t:n_ts * (t + 1), :])
                pred, _ = _model.output_layer(out)
                pred = pred.squeeze().cpu().numpy()
                all_preds.extend(pred)

    all_preds = np.array(all_preds)
    
    # 針對神經網路的 Frame-level 抖動，施加「眾數濾波 (Majority / Mode Filter)」平滑化！
    # （絕對不能對數值類別使用中值濾波，會產生奇怪的幽靈和弦索引！）
    # fps 約為 10.8。為了達到 Chordify 等級的大區塊和弦 (消除鋼琴 2 拍以內的過門變換)，
    # 我們將 kernel_size 加大到 35 (約 3.2 秒)，這樣可以強制把小於 1.6 秒的變化全部濾除！
    kernel_size = 35
    pad_w = kernel_size // 2
    padded = np.pad(all_preds, (pad_w, pad_w), mode='edge')
    smoothed_preds = np.zeros_like(all_preds)
    
    for i in range(len(all_preds)):
        window = padded[i : i + kernel_size]
        # 取得 window 中出現最多次的 class
        values, counts = np.unique(window, return_counts=True)
        smoothed_preds[i] = values[np.argmax(counts)]
        
    lines = []
    start = 0.0
    prev = smoothed_preds[0]
    
    for i in range(1, len(smoothed_preds)):
        if i >= len(smoothed_preds) - num_pad:
            # 去除 padding 部分
            break
            
        current = smoothed_preds[i]
        if current != prev:
            lines.append((start, fps * i, _idx_to_chord[prev]))
            start = fps * i
            prev = current
            
    # 收尾最後一個和弦
    total_valid_frames = len(smoothed_preds) - num_pad
    if start < fps * total_valid_frames:
        lines.append((start, fps * total_valid_frames, _idx_to_chord[prev]))

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

def detect_chords_and_key(audio_path: str, min_dur: float = 0.5) -> tuple:
    """
    從音訊檔案偵測和弦並推導調性（只執行一次 BTC 推論）。

    Returns:
        (chords_list, key_string)
    """
    raw = _run_btc(audio_path)
    chords = _merge_segments(raw, min_dur=min_dur)
    key = _key_from_chords(chords) if chords else "C"
    return chords, key


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
# 子程序隔離：在獨立程序中執行 BTC 推論，避免 GIL 阻塞 event loop
# ---------------------------------------------------------------------------

_btc_pool = None


def _subprocess_detect(audio_path, min_dur=0.5):
    """在子程序中執行（必須是 top-level function 才能 pickle）"""
    return detect_chords_and_key(audio_path, min_dur)


def detect_chords_and_key_isolated(audio_path: str, min_dur: float = 0.5) -> tuple:
    """
    在獨立子程序中執行 BTC 推論，完全隔離 GIL。
    子程序第一次呼叫會載入模型（約數秒），之後重複使用同一程序。
    """
    global _btc_pool
    if _btc_pool is None:
        from concurrent.futures import ProcessPoolExecutor
        _btc_pool = ProcessPoolExecutor(max_workers=1)
    future = _btc_pool.submit(_subprocess_detect, audio_path, min_dur)
    return future.result(timeout=600)


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
