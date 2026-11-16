"""Global tuning-offset detection and correction for library audio.

Some recordings (tape / vinyl transfers, mostly 60s-80s) play back at a
slightly wrong speed, so the whole song sits a stable N cents off the
A=440 grid. Danielle Licari "Concerto pour une voix de Saint-Preux"
measures +36 cents in every 30 s window (spread 31..41).

Detection: librosa.estimate_tuning per 30 s window, circular median (the
offset wraps at +-50 cents), circular spread as a consistency check. A
song is fixable only when the offset is clearly non-zero AND stable — a
drifting tape or a deliberately detuned piece is reported, never fixed.

Correction: resample so pitch AND speed are both restored (the original
fault is playback speed, so stretching only the pitch would leave the
tempo wrong). Sharp by c cents -> the audio is 2^(c/1200) too fast ->
resample to sr * 2^(c/1200) and relabel at sr.

Pure functions only; callers run them inside a process pool (CPU-bound).
"""

from __future__ import annotations

import math
import os
from typing import Optional

ANALYZE_SR = 22050
WINDOW_S = 30.0
MIN_OFFSET_CENTS = 10.0   # below this it is within normal recording tolerance
MAX_OFFSET_CENTS = 45.0   # near a quarter-tone the nearest grid is ambiguous
MAX_SPREAD_CENTS = 8.0    # windows disagree more than this -> drift, not offset
MIN_WINDOWS = 3

WRITABLE_EXTS = (".flac", ".wav")


def _circular_stats(cents: list[float]) -> tuple[float, float]:
    """Circular mean + spread for values on a 100-cent circle."""
    angles = [c / 100.0 * 2 * math.pi for c in cents]
    s = sum(math.sin(a) for a in angles) / len(angles)
    c = sum(math.cos(a) for a in angles) / len(angles)
    mean = math.atan2(s, c) / (2 * math.pi) * 100.0
    r = min(1.0, math.hypot(s, c))
    # circular std, mapped back to cents
    spread = math.sqrt(-2.0 * math.log(r)) / (2 * math.pi) * 100.0 if r > 0 else 50.0
    return mean, spread


def analyze_tuning(audio_path: str) -> dict:
    """Measure the global tuning offset of one file.

    Returns {offset_cents, spread_cents, windows, n_windows, fixable, reason}.
    ``offset_cents`` > 0 means the recording is sharp.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(audio_path, sr=ANALYZE_SR, mono=True)
    seg = int(WINDOW_S * sr)
    windows = []
    for i in range(0, max(1, len(y) - seg // 2), seg):
        chunk = y[i:i + seg]
        if len(chunk) < sr * 5 or float(np.sqrt(np.mean(chunk ** 2))) < 1e-3:
            continue  # too short or silent
        windows.append(round(float(librosa.estimate_tuning(y=chunk, sr=sr)) * 100.0, 1))

    out = {"windows": windows, "n_windows": len(windows),
           "offset_cents": 0.0, "spread_cents": 0.0,
           "fixable": False, "reason": ""}
    if len(windows) < MIN_WINDOWS:
        out["reason"] = "too_short"
        return out
    mean, spread = _circular_stats(windows)
    out["offset_cents"] = round(mean, 1)
    out["spread_cents"] = round(spread, 1)
    ext = os.path.splitext(audio_path)[1].lower()
    if abs(mean) < MIN_OFFSET_CENTS:
        out["reason"] = "in_tune"
    elif spread > MAX_SPREAD_CENTS:
        out["reason"] = "drifting"
    elif abs(mean) > MAX_OFFSET_CENTS:
        out["reason"] = "ambiguous_quarter_tone"
    elif ext not in WRITABLE_EXTS:
        out["reason"] = "unsupported_format"
    else:
        out["fixable"] = True
        out["reason"] = "stable_offset"
    return out


def _copy_flac_tags(src: str, dst: str) -> None:
    from mutagen.flac import FLAC
    s, d = FLAC(src), FLAC(dst)
    d.clear()
    d.clear_pictures()
    if s.tags:
        for k in set(s.tags.keys()):
            d[k] = s.tags[k]  # list value keeps multi-valued tags
    for pic in s.pictures:
        d.add_picture(pic)
    d.save()


def correct_file(audio_path: str, offset_cents: float) -> dict:
    """Rewrite ``audio_path`` with pitch+speed restored.

    Original is kept as ``<file>.bak``. Writes ``<file>.tmp<ext>`` first,
    then renames — a failure at any step leaves the original playable.
    Refuses when a .bak already exists (would destroy the true original).
    """
    import numpy as np
    import soundfile as sf
    import soxr

    bak = audio_path + ".bak"
    if os.path.exists(bak):
        raise FileExistsError(f"backup already exists: {bak}")
    base, ext = os.path.splitext(audio_path)
    tmp = f"{base}.tuning_tmp{ext}"

    info = sf.info(audio_path)
    y, sr = sf.read(audio_path, always_2d=True, dtype="float32")
    factor = 2.0 ** (offset_cents / 1200.0)
    out = soxr.resample(y, sr, sr * factor, quality="VHQ")
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 1.0:  # resampling overshoot on brickwalled masters
        out = out / peak
    sf.write(tmp, out, sr, subtype=info.subtype, format=info.format)
    try:
        if ext.lower() == ".flac":
            _copy_flac_tags(audio_path, tmp)
        st = os.stat(audio_path)
        os.replace(audio_path, bak)
        os.replace(tmp, audio_path)  # fresh mtime -> other players rescan it
    except Exception:
        if not os.path.exists(audio_path) and os.path.exists(bak):
            os.replace(bak, audio_path)  # put the original back
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return {
        "factor": factor,
        "duration_before": round(len(y) / sr, 3),
        "duration_after": round(len(out) / sr, 3),
        "orig_size": st.st_size,
        "backup": bak,
    }


def revert_file(audio_path: str) -> None:
    """Restore ``<file>.bak`` over the corrected file."""
    bak = audio_path + ".bak"
    if not os.path.exists(bak):
        raise FileNotFoundError(f"no backup: {bak}")
    os.replace(bak, audio_path)


def verify_corrected(audio_path: str) -> Optional[float]:
    """Re-measure after correction; returns residual offset (cents)."""
    try:
        return analyze_tuning(audio_path)["offset_cents"]
    except Exception:
        return None
