"""Audio feature extraction for RH melody song-type routing experiments."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from .melody_candidate import stem_path


AUDIO_FEATURE_SCHEMA_VERSION = 1
AUDIO_FEATURE_SOURCE = "song_type_audio_features_v1"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_MAX_SECONDS = 120.0
STEM_NAMES = ("vocals", "bass", "drums", "other")


def extract_mix_audio_features(
    audio_path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> Dict[str, Any]:
    """Load an audio file and return cheap song-type classifier features."""

    y, sr = load_audio_mono(audio_path, sample_rate=sample_rate, max_seconds=max_seconds)
    return summarize_audio_array(y, sr)


def summarize_audio_array(y: Sequence[float] | np.ndarray, sample_rate: int) -> Dict[str, Any]:
    """Summarize a mono audio array with robust scalar features.

    The features are intentionally cheap and model-agnostic. They are meant to be
    joined with metadata tokens, not to replace a stronger classifier later.
    """

    librosa = _import_librosa()
    audio = _as_audio_array(y)
    sr = int(sample_rate) if sample_rate else DEFAULT_SAMPLE_RATE
    duration_s = float(len(audio) / sr) if sr > 0 else 0.0
    if len(audio) == 0 or duration_s <= 0:
        return _empty_feature_row()

    frame_length = min(2048, max(256, len(audio)))
    hop_length = max(128, frame_length // 4)
    rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length)[0]
    rms_mean = _safe_mean(rms)
    rms_std = _safe_std(rms)
    rms_cov = float(rms_std / rms_mean) if rms_mean > 1e-9 else 0.0

    try:
        onset_frames = librosa.onset.onset_detect(
            y=audio,
            sr=sr,
            hop_length=hop_length,
            units="frames",
            backtrack=False,
        )
        onset_density = float(len(onset_frames) / duration_s) if duration_s else 0.0
    except Exception:
        onset_density = 0.0

    try:
        harmonic, percussive = librosa.effects.hpss(audio)
        harmonic_energy = _mean_square(harmonic)
        percussive_energy = _mean_square(percussive)
        hpss_harmonic_ratio = _safe_ratio(harmonic_energy, harmonic_energy + percussive_energy)
    except Exception:
        hpss_harmonic_ratio = None

    try:
        flatness = librosa.feature.spectral_flatness(y=audio, n_fft=frame_length, hop_length=hop_length)[0]
        spectral_flatness_mean: Optional[float] = _safe_mean(flatness)
    except Exception:
        spectral_flatness_mean = None

    try:
        centroid = librosa.feature.spectral_centroid(y=audio, sr=sr, n_fft=frame_length, hop_length=hop_length)[0]
        spectral_centroid_mean: Optional[float] = _safe_mean(centroid)
    except Exception:
        spectral_centroid_mean = None

    try:
        zcr = librosa.feature.zero_crossing_rate(audio, frame_length=frame_length, hop_length=hop_length)[0]
        zero_crossing_rate_mean: Optional[float] = _safe_mean(zcr)
    except Exception:
        zero_crossing_rate_mean = None

    return {
        "schema_version": AUDIO_FEATURE_SCHEMA_VERSION,
        "feature_source": AUDIO_FEATURE_SOURCE,
        "sample_rate": sr,
        "analyzed_duration_s": duration_s,
        "rms_mean": rms_mean,
        "rms_std": rms_std,
        "rms_cov": rms_cov,
        "onset_density_per_s": max(0.0, onset_density),
        "hpss_harmonic_ratio": hpss_harmonic_ratio,
        "spectral_flatness_mean": spectral_flatness_mean,
        "spectral_centroid_mean": spectral_centroid_mean,
        "zero_crossing_rate_mean": zero_crossing_rate_mean,
    }


def cached_stem_energy_features(
    data_dir: str | Path,
    song_hash: str,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> Dict[str, Any]:
    """Return vocal-stem energy ratios from already cached stems only."""

    root = Path(data_dir)
    paths = {name: stem_path(root, song_hash, name) for name in STEM_NAMES}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        return {
            "stem_status": "missing_cached_stems",
            "missing_stems": missing,
            "vocal_stem_energy_ratio": None,
            "vocal_vs_other_energy_ratio": None,
        }

    energies: Dict[str, float] = {}
    for name, path in paths.items():
        y, _sr = load_audio_mono(path, sample_rate=sample_rate, max_seconds=max_seconds)
        energies[name] = _mean_square(y)
    total = sum(energies.values())
    vocal = energies.get("vocals", 0.0)
    other = energies.get("other", 0.0)
    return {
        "stem_status": "cached_stems",
        "missing_stems": [],
        "stem_energy": energies,
        "vocal_stem_energy_ratio": _safe_ratio(vocal, total),
        "vocal_vs_other_energy_ratio": _safe_ratio(vocal, vocal + other),
    }


def build_audio_feature_row(
    row: Mapping[str, Any],
    *,
    audio_path: str | Path,
    data_dir: str | Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    include_cached_stems: bool = True,
) -> Dict[str, Any]:
    """Create a JSONL-ready feature row for one held-out label item."""

    song_hash = str(row.get("song_hash") or row.get("hash") or "").strip()
    if not song_hash:
        raise ValueError("song hash is required")
    audio_file = Path(audio_path)
    if not audio_file.is_file():
        raise FileNotFoundError(str(audio_file))

    mix = extract_mix_audio_features(audio_file, sample_rate=sample_rate, max_seconds=max_seconds)
    stem_features = (
        cached_stem_energy_features(data_dir, song_hash, sample_rate=sample_rate, max_seconds=max_seconds)
        if include_cached_stems
        else {"stem_status": "not_requested"}
    )
    return {
        "schema_version": AUDIO_FEATURE_SCHEMA_VERSION,
        "feature_source": AUDIO_FEATURE_SOURCE,
        "song_hash": song_hash,
        "hash": song_hash,
        "survey_id": str(row.get("survey_id") or ""),
        "path": str(row.get("path") or ""),
        "title": str(row.get("title") or ""),
        "artist": str(row.get("artist") or ""),
        "duration_s": _optional_float(row.get("duration_s") or row.get("duration")),
        "candidate_hint": str(row.get("candidate_hint") or ""),
        "resolved_label": str(row.get("resolved_label") or row.get("human_label") or ""),
        "audio_path": str(audio_file),
        "mix": mix,
        "stems": stem_features,
        "status": "ok",
    }


def load_audio_mono(
    audio_path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> tuple[np.ndarray, int]:
    """Load mono audio with librosa at a classifier-friendly sample rate."""

    librosa = _import_librosa()
    path = str(audio_path)
    duration = max(0.1, float(max_seconds)) if max_seconds else None
    y, sr = librosa.load(path, sr=int(sample_rate), mono=True, duration=duration)
    return _as_audio_array(y), int(sr)


def _empty_feature_row() -> Dict[str, Any]:
    return {
        "schema_version": AUDIO_FEATURE_SCHEMA_VERSION,
        "feature_source": AUDIO_FEATURE_SOURCE,
        "sample_rate": DEFAULT_SAMPLE_RATE,
        "analyzed_duration_s": 0.0,
        "rms_mean": 0.0,
        "rms_std": 0.0,
        "rms_cov": 0.0,
        "onset_density_per_s": 0.0,
        "hpss_harmonic_ratio": None,
        "spectral_flatness_mean": None,
        "spectral_centroid_mean": None,
        "zero_crossing_rate_mean": None,
    }


def _as_audio_array(y: Sequence[float] | np.ndarray) -> np.ndarray:
    audio = np.asarray(y, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=0)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)
    return np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)


def _mean_square(y: Sequence[float] | np.ndarray) -> float:
    audio = _as_audio_array(y)
    if audio.size == 0:
        return 0.0
    return float(np.mean(np.square(audio, dtype=np.float32)))


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    if not math.isfinite(float(denominator)) or denominator <= 1e-12:
        return None
    value = float(numerator) / float(denominator)
    return max(0.0, min(1.0, value))


def _safe_mean(values: Sequence[float] | np.ndarray) -> float:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.nanmean(arr))


def _safe_std(values: Sequence[float] | np.ndarray) -> float:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.nanstd(arr))


def _optional_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _import_librosa():
    import librosa

    return librosa
