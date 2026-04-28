"""Audio feature extraction + cache for the beat_refiner model.

What goes into the cache (audio-only, 98 channels):

  Indices  Source                       Shape  Why
  -------  --------------------------- ------ --------------------------
  0..83    log-CQT magnitude (84 bins)  84    Pitch content; aligns with
                                              BTC's CQT so the chord
                                              boundary head sees the same
                                              spectrum BTC saw.
  84..95   chroma (12 bins)             12    Octave-folded pitch class —
                                              cheap proxy for harmonic
                                              rhythm.
  96       onset strength (normalized)   1    Onset spikes anchor beats
                                              where percussion is present.
  97       RMS energy (normalized)       1    Section-level dynamics; rises
                                              for chorus / intros differ.

What is NOT cached (added at training/inference time):

  98       initial beat grid (one-hot)   1    From beats[] in chord JSON.
  99       initial downbeat grid         1    From downbeats[].

Why not cache initial beat grid: those depend on the *current* beat
tracker (beat_this v5). Caching them would force a full re-extract every
time we swap trackers. Audio features are tracker-independent — they
only depend on (sample_rate, hop_length, n_bins, ``FEATURE_VERSION``).

Frame rate
----------
``sr=22050, hop_length=2048`` → 10.766 frames per second
                              ≈ 92.88 ms per frame.

This is the same hop/sr BTC uses (``backend/btc/run_config.yaml``),
so cached features can be reused by chord-boundary post-processing
without resampling.

Cache layout
------------
``<cache_root>/<hash[:2]>/<hash>.npz``

Sharded to mirror ``data/chords/`` and avoid any single dir having 50k
files. ``hash`` = the chord JSON's stem (12-char hex), so audio ↔ label
pairing is one lookup.

Each .npz contains:

    features        float32 (T, 98)
    sr              int
    hop_length      int
    n_cqt_bins      int
    feature_version int
    duration_sec    float

A version mismatch on load triggers re-extraction (caller's choice).

Size budget
-----------
~ 98 ch × 4 B × 2,000 frames ≈ 784 KB per 3-min song
× 50,000 songs ≈ 40 GB total — fits anywhere on V:\\.

Usage
-----
Library use::

    from backend.ai.beat_refiner_features import (
        extract_features, cache_features, load_cached,
        compute_labels, build_initial_grid_channels,
        FEATURE_VERSION, N_AUDIO_CHANNELS, N_MODEL_CHANNELS,
    )

    feat = extract_features("song.flac")          # one-shot
    npz_path = cache_features("song.flac", "V:/data/features", hash="abc")
    cached = load_cached(npz_path)                 # dict or None on miss

    # Model input = audio features ⊕ initial-grid channels
    model_input = build_initial_grid_channels(
        cached["features"], beats, downbeats,
        n_frames=cached["features"].shape[0],
    )

    # Per-frame labels for training
    labels = compute_labels(chord_data, n_frames=cached["features"].shape[0])

CLI smoke test::

    python -m backend.ai.beat_refiner_features --audio path/to/song.flac

This module avoids torch — only numpy / librosa / soundfile. It's safe
to import from any worker (no GPU init).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Frame-axis spec — bumping any of these is a breaking change for the
# whole pipeline (cache, model, training labels). Keep them as constants
# so callers can assert on them.
# ---------------------------------------------------------------------------

SAMPLE_RATE = 22050
HOP_LENGTH = 2048
N_CQT_BINS = 84            # 7 octaves × 12 semitones, starts at C1
BINS_PER_OCTAVE = 12
FMIN_NOTE = "C1"           # 32.7 Hz; covers piano range and below

# Frame timing derived from (sr, hop). Computed once for clarity.
FRAME_DURATION_SEC = HOP_LENGTH / SAMPLE_RATE  # ≈ 0.0929
FRAMES_PER_SEC = SAMPLE_RATE / HOP_LENGTH      # ≈ 10.766

# Channel layout
N_AUDIO_CHANNELS = N_CQT_BINS + 12 + 1 + 1     # = 98 (cached)
N_GRID_CHANNELS = 2                             # beat + downbeat (dynamic)
N_MODEL_CHANNELS = N_AUDIO_CHANNELS + N_GRID_CHANNELS  # = 100

# Bumped when any feature semantic changes. Cache files with a different
# version are silently ignored on load (caller re-extracts).
FEATURE_VERSION = 1

# Hard cap on song length to bound memory + extraction time. Mirrors
# chord_detect.MAX_ANALYZE_SECONDS so a song that BTC can chord-detect
# can also be feature-extracted.
MAX_AUDIO_SECONDS = 900.0  # 15 min


# ---------------------------------------------------------------------------
# Audio loading
# ---------------------------------------------------------------------------

def _load_audio_mono(audio_path: Union[str, Path]) -> np.ndarray:
    """Load an audio file as mono float32 at SAMPLE_RATE.

    Tries ``soundfile`` first (handles unicode paths cleanly on Windows
    and avoids the ffmpeg fallback that breaks on the NUC). Falls back
    to ``librosa.load`` for formats soundfile doesn't decode.

    Truncates anything past ``MAX_AUDIO_SECONDS``.
    """
    import soundfile as sf
    import librosa

    p = str(audio_path)
    try:
        y, sr = sf.read(p, dtype="float32", always_2d=False)
    except Exception as e:
        logger.debug("soundfile failed on %s (%s) — falling back to librosa", p, e)
        y, sr = librosa.load(p, sr=None, mono=False)
        y = y.astype(np.float32, copy=False)

    # Mono-down
    if y.ndim == 2:
        y = y.mean(axis=1)

    # Resample to SAMPLE_RATE if needed
    if sr != SAMPLE_RATE:
        y = librosa.resample(y, orig_sr=sr, target_sr=SAMPLE_RATE)
        sr = SAMPLE_RATE

    max_samples = int(MAX_AUDIO_SECONDS * SAMPLE_RATE)
    if len(y) > max_samples:
        y = y[:max_samples]

    return y


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(audio_path: Union[str, Path]) -> Dict:
    """Extract the 98-channel audio feature stack for one song.

    Returns a dict with:
      features        float32 (T, N_AUDIO_CHANNELS)
      sr              int
      hop_length      int
      n_cqt_bins      int
      feature_version int
      duration_sec    float

    Raises on truly broken audio (file not found, decode error). Caller
    should catch and skip the song.
    """
    import librosa

    y = _load_audio_mono(audio_path)
    duration = len(y) / SAMPLE_RATE

    # CQT — power magnitude in dB. ``hop_length`` and ``n_bins`` exactly
    # match BTC's preprocessing so the same spectrum is reused downstream.
    cqt_mag = np.abs(librosa.cqt(
        y=y, sr=SAMPLE_RATE, hop_length=HOP_LENGTH,
        n_bins=N_CQT_BINS, bins_per_octave=BINS_PER_OCTAVE,
        fmin=librosa.note_to_hz(FMIN_NOTE),
    )).astype(np.float32)
    log_cqt = librosa.amplitude_to_db(cqt_mag, ref=np.max).astype(np.float32)
    # Normalize log_cqt to roughly [-1, 0] (ref=np.max gives [-80, 0] in dB).
    # Scale to [-1, 0] so all channels live on a comparable magnitude.
    log_cqt = log_cqt / 80.0  # 80 dB dynamic range
    log_cqt = np.clip(log_cqt, -1.0, 0.0)

    # Chroma derived from the same CQT — cheaper than chroma_cqt's
    # internal recomputation and guaranteed time-aligned.
    # cqt_mag is (n_bins, T); reshape to (n_octaves, 12, T) then sum over octaves.
    n_oct = N_CQT_BINS // BINS_PER_OCTAVE
    chroma = cqt_mag[:n_oct * BINS_PER_OCTAVE].reshape(n_oct, BINS_PER_OCTAVE, -1).sum(axis=0)
    # L1-normalize per frame so chroma is a probability-ish over pitch class.
    chroma_sum = chroma.sum(axis=0, keepdims=True) + 1e-8
    chroma = (chroma / chroma_sum).astype(np.float32)

    # Onset strength — librosa returns a 1-D array per hop already.
    # We pass the same hop_length so frames align; it computes its own STFT.
    onset = librosa.onset.onset_strength(
        y=y, sr=SAMPLE_RATE, hop_length=HOP_LENGTH
    ).astype(np.float32)
    # Normalize to [0, 1] using max — onset strength is non-negative and
    # song-relative magnitudes are what matter (we want spikes to stand out).
    onset_max = onset.max() if onset.size else 1.0
    if onset_max > 1e-6:
        onset = onset / onset_max

    # RMS energy — also same hop. ``frame_length`` defaults to 2048, fine.
    rms = librosa.feature.rms(
        y=y, frame_length=HOP_LENGTH * 2, hop_length=HOP_LENGTH
    )[0].astype(np.float32)
    rms_max = rms.max() if rms.size else 1.0
    if rms_max > 1e-6:
        rms = rms / rms_max

    # Reconcile lengths — librosa is mostly consistent at the same hop but
    # cqt sometimes emits one extra frame at the end. Truncate everything
    # to the shortest channel.
    T = min(log_cqt.shape[1], chroma.shape[1], onset.shape[0], rms.shape[0])
    log_cqt = log_cqt[:, :T]
    chroma = chroma[:, :T]
    onset = onset[:T]
    rms = rms[:T]

    # Stack: (T, N_AUDIO_CHANNELS)
    features = np.empty((T, N_AUDIO_CHANNELS), dtype=np.float32)
    features[:, :N_CQT_BINS] = log_cqt.T
    features[:, N_CQT_BINS:N_CQT_BINS + 12] = chroma.T
    features[:, N_CQT_BINS + 12] = onset
    features[:, N_CQT_BINS + 13] = rms

    return {
        "features": features,
        "sr": SAMPLE_RATE,
        "hop_length": HOP_LENGTH,
        "n_cqt_bins": N_CQT_BINS,
        "feature_version": FEATURE_VERSION,
        "duration_sec": duration,
    }


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def features_path_for(song_hash: str, cache_root: Union[str, Path]) -> Path:
    """Compute the .npz path for a given song hash, sharded by hash[:2]."""
    cache_root = Path(cache_root)
    return cache_root / song_hash[:2] / f"{song_hash}.npz"


def cache_features(audio_path: Union[str, Path], song_hash: str,
                   cache_root: Union[str, Path], *, force: bool = False
                   ) -> Path:
    """Extract and persist features for one song.

    Returns the cache .npz path. If a valid cache already exists and
    ``force=False``, returns immediately without re-extracting.
    """
    out_path = features_path_for(song_hash, cache_root)
    if out_path.exists() and not force:
        # Trust the version — loader will reject stale ones.
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    feat = extract_features(audio_path)

    # Save uncompressed: features are mostly dense floats, so .npz
    # compression buys ~10% at the cost of 5x slower load.
    np.savez(
        out_path,
        features=feat["features"],
        sr=np.int32(feat["sr"]),
        hop_length=np.int32(feat["hop_length"]),
        n_cqt_bins=np.int32(feat["n_cqt_bins"]),
        feature_version=np.int32(feat["feature_version"]),
        duration_sec=np.float32(feat["duration_sec"]),
    )
    return out_path


def load_cached(npz_path: Union[str, Path]) -> Optional[Dict]:
    """Load a cached feature .npz. Returns None on version mismatch or
    missing file (so the trainer can re-extract transparently)."""
    p = Path(npz_path)
    if not p.exists():
        return None
    try:
        d = np.load(p)
    except Exception as e:
        logger.warning("failed to load %s: %s", p, e)
        return None
    if int(d.get("feature_version", -1)) != FEATURE_VERSION:
        return None
    if int(d.get("n_cqt_bins", -1)) != N_CQT_BINS:
        return None
    if int(d.get("hop_length", -1)) != HOP_LENGTH:
        return None
    if int(d.get("sr", -1)) != SAMPLE_RATE:
        return None
    return {
        "features": d["features"],
        "sr": int(d["sr"]),
        "hop_length": int(d["hop_length"]),
        "n_cqt_bins": int(d["n_cqt_bins"]),
        "feature_version": int(d["feature_version"]),
        "duration_sec": float(d["duration_sec"]),
    }


# ---------------------------------------------------------------------------
# Time-axis utilities
# ---------------------------------------------------------------------------

def time_to_frame(t: float) -> int:
    """Round a time (seconds) to the nearest frame index."""
    return int(round(t * FRAMES_PER_SEC))


def frame_to_time(f: int) -> float:
    """Convert a frame index to its center time (seconds)."""
    return f / FRAMES_PER_SEC


def rasterize_times(times: List[float], n_frames: int) -> np.ndarray:
    """Convert a list of event times to a per-frame indicator (1 where an
    event lands, 0 otherwise). Times outside [0, n_frames) are clipped.
    """
    grid = np.zeros(n_frames, dtype=np.float32)
    for t in times:
        f = time_to_frame(t)
        if 0 <= f < n_frames:
            grid[f] = 1.0
    return grid


# ---------------------------------------------------------------------------
# Model-input assembly
# ---------------------------------------------------------------------------

def build_initial_grid_channels(audio_features: np.ndarray,
                                beats: List[float],
                                downbeats: List[float]) -> np.ndarray:
    """Concatenate the audio feature stack with beat / downbeat one-hot
    channels. Returns ``(T, N_MODEL_CHANNELS)``.

    ``audio_features`` must be ``(T, N_AUDIO_CHANNELS)`` — exactly what
    ``extract_features`` and ``load_cached`` produce.
    """
    if audio_features.shape[1] != N_AUDIO_CHANNELS:
        raise ValueError(
            f"audio_features has {audio_features.shape[1]} channels, "
            f"expected {N_AUDIO_CHANNELS}"
        )
    T = audio_features.shape[0]
    beat_grid = rasterize_times(beats, T)
    db_grid = rasterize_times(downbeats, T)
    out = np.empty((T, N_MODEL_CHANNELS), dtype=np.float32)
    out[:, :N_AUDIO_CHANNELS] = audio_features
    out[:, N_AUDIO_CHANNELS] = beat_grid
    out[:, N_AUDIO_CHANNELS + 1] = db_grid
    return out


# ---------------------------------------------------------------------------
# Per-frame label generation (training-time only; not cached)
# ---------------------------------------------------------------------------

def _chord_change_times(chords: List[Dict]) -> List[float]:
    """Strictly-monotonic chord-onset times. Same semantics as the
    bar_arbitrator helper — keep this one self-contained so this module
    has no backend.ai dependency for label generation."""
    if not chords:
        return []
    out = [float(chords[0].get("time", 0.0))]
    last = chords[0].get("chord")
    for c in chords[1:]:
        cur = c.get("chord")
        if cur != last:
            out.append(float(c.get("time", 0.0)))
            last = cur
    return out


def compute_labels(chord_data: Dict, n_frames: int) -> Dict[str, np.ndarray]:
    """Per-frame supervisory labels for one song.

    Returns a dict with ``(n_frames,)`` float32 arrays:
      beat_label              1.0 at frames containing a beat
      downbeat_label          1.0 at frames containing a downbeat
      chord_boundary_label    1.0 at frames where a chord change starts

    All labels are hard one-hot. Loss-aware label smoothing (e.g. ±1
    frame Gaussian blur) belongs in the trainer, not here, so we keep a
    single source of truth for "which frames are positives."
    """
    beats = chord_data.get("beats") or []
    downbeats = chord_data.get("downbeats") or []
    chords = chord_data.get("chords") or []

    return {
        "beat_label": rasterize_times(list(map(float, beats)), n_frames),
        "downbeat_label": rasterize_times(list(map(float, downbeats)), n_frames),
        "chord_boundary_label": rasterize_times(_chord_change_times(chords), n_frames),
    }


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", type=Path, required=True,
                    help="Path to an audio file (FLAC/WAV/MP3)")
    ap.add_argument("--cache-root", type=Path, default=None,
                    help="If set, cache the features under this dir")
    ap.add_argument("--hash", type=str, default="smoke_test",
                    help="Song hash used for cache filename")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    import time as _time
    t0 = _time.time()
    feat = extract_features(args.audio)
    extract_dt = _time.time() - t0

    arr = feat["features"]
    print(f"audio:       {args.audio}")
    print(f"duration:    {feat['duration_sec']:.2f}s")
    print(f"frames:      {arr.shape[0]} ({arr.shape[0] / FRAMES_PER_SEC:.2f}s coverage)")
    print(f"channels:    {arr.shape[1]}  (expected {N_AUDIO_CHANNELS})")
    print(f"dtype:       {arr.dtype}")
    print(f"extract_dt:  {extract_dt:.2f}s "
          f"({feat['duration_sec'] / extract_dt:.1f}x realtime)")
    print(f"cqt range:   [{arr[:, :N_CQT_BINS].min():.3f}, "
          f"{arr[:, :N_CQT_BINS].max():.3f}]")
    print(f"chroma sum/frame (mean):  "
          f"{arr[:, N_CQT_BINS:N_CQT_BINS+12].sum(axis=1).mean():.3f}  (~1.0 expected)")
    print(f"onset range: [{arr[:, N_CQT_BINS+12].min():.3f}, "
          f"{arr[:, N_CQT_BINS+12].max():.3f}]")
    print(f"rms range:   [{arr[:, N_CQT_BINS+13].min():.3f}, "
          f"{arr[:, N_CQT_BINS+13].max():.3f}]")

    if args.cache_root:
        out = cache_features(args.audio, args.hash, args.cache_root, force=True)
        size_kb = out.stat().st_size / 1024
        print(f"cached:      {out} ({size_kb:.0f} KB)")
        roundtrip = load_cached(out)
        assert roundtrip is not None, "load_cached returned None"
        assert np.allclose(roundtrip["features"], feat["features"]), "roundtrip mismatch"
        print("roundtrip:   ok")


if __name__ == "__main__":
    main()
