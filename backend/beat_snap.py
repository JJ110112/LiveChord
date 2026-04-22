"""Beat tracking + chord-boundary snapping.

Two engines are exposed:

- ``analyze_and_snap`` — legacy librosa.beat.beat_track (single global BPM).
  Used by ``migrate_add_beat_info.py`` for the original ~4500-song retrofit.

- ``analyze_and_snap_dynamic`` — madmom RNN+DBN tracker that emits per-beat
  times (so it follows live/rubato tempo) plus downbeats and a tempo_curve.
  Falls back to ``analyze_and_snap`` when madmom isn't installed. New code
  should call this; ``auto_worker._auto_detect_loop`` already does.

Why snap vs re-detect: BTC already produces *correct* chord NAMES at
frame-accurate times; the visible "3.3 beats" display is slop because the
boundaries aren't grid-aligned. Re-detecting chords would be expensive and
risky. Beat tracking + snapping fixes the display with ~1-2s of CPU per song
(librosa) / ~30s (madmom) and no change to the chord identities.

Snap tolerance is conservative (0.25s) so we don't pull a syncopated boundary
onto a wrong beat. Anything farther than that is left untouched.
"""

from __future__ import annotations

from typing import Optional, Tuple

import librosa
import numpy as np

# Optional madmom — install procedure documented in backend/requirements.txt.
try:  # pragma: no cover — environmental
    from madmom.audio.signal import Signal as _MmSignal
    from madmom.features.beats import (
        DBNBeatTrackingProcessor as _MmDBNBeat,
        RNNBeatProcessor as _MmRNNBeat,
    )
    from madmom.features.downbeats import (
        DBNDownBeatTrackingProcessor as _MmDBNDownbeat,
        RNNDownBeatProcessor as _MmRNNDownbeat,
    )

    HAS_MADMOM = True
except Exception:  # ImportError or any madmom init failure
    HAS_MADMOM = False


# Max distance a chord boundary may be from the nearest detected beat before
# we leave it alone (0.25s ≈ 1/2 beat at 120 BPM). Keeps syncopated/anacrustic
# boundaries from getting yanked onto a wrong grid line.
_SNAP_TOLERANCE_SEC = 0.25

# Tempo range outside which we distrust beat_track and skip snap. Beat tracker
# sometimes locks onto halftime/doubletime; those would shift the whole song
# and make things worse than the pre-snap state.
_BPM_MIN = 40.0
_BPM_MAX = 240.0

# Schema version for the dynamic-beat fields (beats[], downbeats[], tempo_curve).
# Bumped when the extraction algorithm or fields change so AI-accompaniment
# caches downstream can detect they need a regen.
BEAT_VERSION = 1


def _snap_to_grid(chords: list, beat_times_sorted: np.ndarray) -> int:
    """Move each chord's time/end onto the nearest beat within tolerance.

    Mutates ``chords`` in place. Returns count of fields snapped. Also
    re-aligns chord[i].end == chord[i+1].time after the per-field pass to
    avoid 1-sample gaps from independent rounding.
    """
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

    for i in range(len(chords) - 1):
        if "end" in chords[i] and "time" in chords[i + 1]:
            chords[i]["end"] = chords[i + 1]["time"]

    return snapped


def _tempo_curve_from_beats(beats: list, window: int = 4) -> list:
    """Sliding-window local BPM. window=4 → bpm = 60 * 3 / (b[i+3] - b[i]).

    Output is a list of ``{"t": midpoint_seconds, "bpm": rounded}`` records,
    one per advance of the window. Empty when fewer than ``window`` beats.
    Frontend / AI accompaniment use this as a piecewise-linear lookup table.
    """
    if len(beats) < window:
        return []
    out = []
    for i in range(len(beats) - window + 1):
        span = beats[i + window - 1] - beats[i]
        if span <= 0:
            continue
        bpm = 60.0 * (window - 1) / span
        t_mid = (beats[i] + beats[i + window - 1]) / 2.0
        out.append({"t": round(t_mid, 3), "bpm": round(bpm, 2)})
    return out


def analyze_and_snap(audio_path: str, chords: list) -> Tuple[Optional[float], int]:
    """Legacy librosa-only path. See module docstring."""
    if not chords:
        return None, 0

    try:
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
    snapped = _snap_to_grid(chords, beat_times_sorted)
    return bpm, snapped


def _madmom_extract(audio_path: str) -> Optional[dict]:
    """Run madmom RNN+DBN beat + downbeat tracking. Returns None on failure."""
    if not HAS_MADMOM:
        return None
    try:
        sig = _MmSignal(audio_path, sample_rate=44100, num_channels=1)
        rnn = _MmRNNBeat()
        acts = rnn(sig)
        dbn = _MmDBNBeat(min_bpm=_BPM_MIN, max_bpm=_BPM_MAX, fps=100)
        beats = dbn(acts).tolist()
        if len(beats) < 4:
            return None

        # Downbeats — separate model with 3/4 + 4/4 hypotheses
        try:
            db_rnn = _MmRNNDownbeat()
            db_acts = db_rnn(sig)
            db_dbn = _MmDBNDownbeat(beats_per_bar=[3, 4], fps=100)
            db_out = db_dbn(db_acts)
            downbeats = [round(float(t), 4) for t, pos in db_out if int(pos) == 1]
        except Exception:
            downbeats = []

        return {"beats": beats, "downbeats": downbeats}
    except Exception:
        return None


def analyze_and_snap_dynamic(audio_path: str, chords: list, *,
                             prefer_madmom: bool = False) -> dict:
    """Beat tracking + chord-boundary snap, returning a uniform dict shape.

    Args:
        audio_path: absolute or resolvable path to the audio file.
        chords: list of ``{time, end, chord}`` dicts. Mutated in place.
        prefer_madmom: when True, run madmom RNN+DBN to capture rubato
            (~30s/song) — used by the on-demand /api/process/upgrade-beats
            endpoint and the migration script. Default False = ingest path
            stays fast (librosa, ~1-2s) so uploads/YT analyses don't add
            half a minute of wait. Players show a "升級節拍" tool button
            for users who want the rubato treatment after the fact.

    Returns a dict with all fields needed to enrich chord JSON. On total
    failure (audio unreadable, too few beats), returns the same dict shape
    with empty arrays and ``bpm = None``.
    """
    out = {
        "bpm": None,
        "n_snapped": 0,
        "beats": [],
        "downbeats": [],
        "tempo_curve": [],
        "beats_source": None,
        "beat_version": BEAT_VERSION,
    }
    if not chords:
        return out

    mad = _madmom_extract(audio_path) if prefer_madmom else None
    if mad is not None:
        beats = mad["beats"]
        downbeats = mad["downbeats"]
        tempo_curve = _tempo_curve_from_beats(beats, window=4)
        bpms = [pt["bpm"] for pt in tempo_curve]
        bpm_median = float(np.median(bpms)) if bpms else None

        out["beats"] = [round(float(t), 4) for t in beats]
        out["downbeats"] = downbeats
        out["tempo_curve"] = tempo_curve
        out["beats_source"] = "madmom"

        if bpm_median is not None and _BPM_MIN <= bpm_median <= _BPM_MAX:
            out["bpm"] = round(bpm_median, 2)
            beat_arr = np.sort(np.asarray(beats, dtype=np.float64))
            out["n_snapped"] = _snap_to_grid(chords, beat_arr)
        return out

    # Fallback: librosa static path. We still emit a flat tempo_curve so the
    # frontend has a uniform consumption shape regardless of source.
    bpm, n_snap = analyze_and_snap(audio_path, chords)
    if bpm is not None:
        out["bpm"] = round(bpm, 2)
        out["n_snapped"] = n_snap
        out["beats_source"] = "librosa-fallback"
        # Tempo_curve as a single point — frontend lookup degrades to constant
        out["tempo_curve"] = [{"t": 0.0, "bpm": round(bpm, 2)}]
    return out
