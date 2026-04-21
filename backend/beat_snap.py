"""Beat tracking + chord-boundary snapping.

Used by:
- ``auto_worker._auto_detect_loop`` — runs after BTC to write ``bpm`` and snap
  chord times to the detected beat grid. New songs benefit automatically.
- ``migrate_add_beat_info.py`` — retrofits the ~4500 existing chord JSONs that
  were written before this module existed.

Design notes
------------
Why snap vs re-detect: BTC already produces *correct* chord NAMES at
frame-accurate times; the visible "3.3 beats" display is slop because the
boundaries aren't grid-aligned. Re-detecting chords would be expensive and
risky. Beat tracking + snapping fixes the display with ~1-2s of CPU per song
and no change to the chord identities.

Snap tolerance is conservative (0.25s) so we don't pull a syncopated boundary
onto a wrong beat. Anything farther than that is left untouched.
"""

from __future__ import annotations

from typing import Optional, Tuple

import librosa
import numpy as np


# Max distance a chord boundary may be from the nearest detected beat before
# we leave it alone (0.25s ≈ 1/2 beat at 120 BPM). Keeps syncopated/anacrustic
# boundaries from getting yanked onto a wrong grid line.
_SNAP_TOLERANCE_SEC = 0.25

# Tempo range outside which we distrust beat_track and skip snap. Beat tracker
# sometimes locks onto halftime/doubletime; those would shift the whole song
# and make things worse than the pre-snap state.
_BPM_MIN = 40.0
_BPM_MAX = 240.0


def analyze_and_snap(audio_path: str, chords: list) -> Tuple[Optional[float], int]:
    """Run beat tracking and snap ``chords[*].time`` / ``chords[*].end`` in-place.

    Args:
        audio_path: absolute or resolvable path to the audio file.
        chords: list of ``{time, end, chord}`` dicts. Mutated in place.

    Returns:
        ``(bpm, n_snapped)`` — ``bpm`` is ``None`` on failure (audio unreadable,
        too few beats detected, tempo out of range). ``n_snapped`` is how many
        time values were moved onto a beat.
    """
    if not chords:
        return None, 0

    try:
        # sr=22050 is fine for beat tracking and ~4× faster to load than native.
        y, sr = librosa.load(audio_path, sr=22050, mono=True)
    except Exception:
        return None, 0

    try:
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        return None, 0

    if len(beat_times) < 4:
        return None, 0

    bpm = float(tempo) if np.isscalar(tempo) else float(np.asarray(tempo).ravel()[0])
    if not (_BPM_MIN <= bpm <= _BPM_MAX):
        return bpm, 0

    beat_times_sorted = np.sort(np.asarray(beat_times, dtype=np.float64))
    snapped = 0

    def _snap(t):
        nonlocal snapped
        if t is None:
            return t
        idx = int(np.searchsorted(beat_times_sorted, t))
        candidates = []
        if idx > 0:
            candidates.append(beat_times_sorted[idx - 1])
        if idx < len(beat_times_sorted):
            candidates.append(beat_times_sorted[idx])
        if not candidates:
            return t
        nearest = min(candidates, key=lambda b: abs(b - t))
        if abs(nearest - t) <= _SNAP_TOLERANCE_SEC:
            snapped += 1
            return round(float(nearest), 3)
        return t

    for c in chords:
        if "time" in c:
            c["time"] = _snap(c["time"])
        if "end" in c:
            c["end"] = _snap(c["end"])

    # After individual snap, realign so chord[i].end meets chord[i+1].time
    # exactly. Prevents 1-sample gaps / overlaps from independent snapping.
    for i in range(len(chords) - 1):
        if "end" in chords[i] and "time" in chords[i + 1]:
            chords[i]["end"] = chords[i + 1]["time"]

    return bpm, snapped
