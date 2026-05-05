"""BPM sanity checks applied at ingest before writing the chord JSON.

Currently ships one heuristic — ballad halving — for the case where madmom
or librosa locks onto doubletime for slow pop ballads (detected 138 BPM
when the song is actually ~69). Three features must *all* agree before we
halve, so a real 140 BPM pop song is never touched.

See doc/QA.md §BPM for the rollout decision (ingest-only, no backfill).
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


# Range in which doubletime errors are most common for pop ballads. Anything
# outside stays as-is — too-fast (>150) is usually real dance/rock, too-slow
# already sits in the ballad band.
_HALVE_BPM_LOW = 130.0
_HALVE_BPM_HIGH = 150.0

# Thresholds tuned on a handful of test tracks (see commit message).
# Lower = more ballad-like.
_MAX_ONSET_DENSITY = 3.0   # onsets per second
_MAX_RMS_VARIANCE = 0.015  # coefficient of variation of RMS envelope

_DEFAULT_META = {
    "applied": False,
    "reason": "",
    "onset_density": None,
    "rms_cov": None,
    "original": None,
}


def _audio_features(audio_path: str) -> Optional[Tuple[float, float]]:
    """Return (onset_density, rms_cov) or None on failure.

    Deliberately cheap — mono 22050 Hz, no spectrogram beyond what librosa's
    onset/rms helpers already compute. Adds ~1s per song at ingest.
    """
    try:
        import librosa
        y, sr = librosa.load(audio_path, sr=22050, mono=True)
        if y.size == 0:
            return None
        duration = float(len(y)) / float(sr)
        if duration < 5.0:
            return None

        onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units="frames")
        onset_density = float(len(onset_frames)) / duration

        rms = librosa.feature.rms(y=y)[0]
        mean = float(np.mean(rms))
        if mean <= 0:
            return None
        rms_cov = float(np.std(rms)) / mean

        return onset_density, rms_cov
    except Exception:
        return None


def ballad_halving_check(
    bpm: float,
    audio_path: str,
    bpm_range: Tuple[float, float] = (_HALVE_BPM_LOW, _HALVE_BPM_HIGH),
) -> Tuple[float, dict]:
    """Decide whether to halve a suspected doubletime ballad BPM.

    Returns (corrected_bpm, meta). ``meta["applied"]`` is True only when all
    three gates fire: BPM in ``bpm_range`` AND onset_density < 3.0/s AND RMS
    CoV < 0.015. The three-gate AND is deliberate — user picked the conservative
    option, favoring false-negatives (miss a true ballad) over false-positives
    (halve a genuine 140 BPM pop track).

    ``bpm_range`` defaults to madmom's typical doubletime band (130-150). Beat
    trackers that lock onto eighth-note grid (e.g. beat_this on slow piano /
    classical) need a wider range like (130, 220) to catch the doubled tempo.
    The other two gates (onset density + RMS CoV) protect against halving
    genuine fast songs even when the BPM gate widens.
    """
    meta = dict(_DEFAULT_META)
    meta["original"] = bpm

    low, high = bpm_range
    if not bpm or bpm < low or bpm > high:
        return bpm, meta

    feats = _audio_features(audio_path)
    if feats is None:
        meta["reason"] = "audio-features-unavailable"
        return bpm, meta

    onset_density, rms_cov = feats
    meta["onset_density"] = round(onset_density, 3)
    meta["rms_cov"] = round(rms_cov, 4)

    if onset_density < _MAX_ONSET_DENSITY and rms_cov < _MAX_RMS_VARIANCE:
        meta["applied"] = True
        meta["reason"] = "ballad-heuristic"
        return bpm / 2.0, meta

    meta["reason"] = "density-or-dynamics-too-high"
    return bpm, meta


def apply_halving_to_beat_info(beat_info: dict) -> dict:
    """Mutate a beat_info dict in place when halving was applied.

    Called after ballad_halving_check returns applied=True. Halves the
    tempo_curve BPMs and thins downbeats (keep every other one so the metric
    stride matches the corrected tempo). Beats[] array is kept as-is — it
    acts as a sub-beat grid under the new tempo.
    """
    tempo_curve = beat_info.get("tempo_curve") or []
    beat_info["tempo_curve"] = [
        {"t": p.get("t", 0.0), "bpm": round(float(p.get("bpm", 0.0)) / 2.0, 2)}
        for p in tempo_curve
    ]

    downbeats = beat_info.get("downbeats") or []
    if len(downbeats) >= 2:
        beat_info["downbeats"] = downbeats[::2]

    return beat_info
