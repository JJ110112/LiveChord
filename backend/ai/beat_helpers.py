"""Shared helpers for consuming the dynamic-beat fields written by
``backend/beat_snap.analyze_and_snap_dynamic``.

These exist so accompaniment / dynamics / pedal code can read local BPM at
a specific time without each caller re-implementing a binary search over
``tempo_curve``. Falls back to a scalar bpm when tempo_curve is empty
(legacy chord JSONs, or when madmom wasn't installed at detect time).
"""

from __future__ import annotations

from bisect import bisect_left
from typing import List, Dict, Optional


def local_bpm_at(tempo_curve: Optional[List[Dict]], t: float,
                 fallback_bpm: float = 120.0) -> float:
    """Piecewise-linear lookup of local BPM at time ``t`` (seconds).

    Args:
        tempo_curve: list of ``{"t": seconds, "bpm": float}`` records, sorted
            by ``t``. Pass ``None`` or ``[]`` to skip and use fallback.
        t: time in seconds.
        fallback_bpm: returned when tempo_curve is empty / missing.

    Returns:
        BPM at ``t``. Constant extrapolation outside the curve's time range
        (so song-tail extrapolation doesn't drift to absurd values).
    """
    if not tempo_curve:
        return fallback_bpm
    # tempo_curve is small (~one entry per beat — a few hundred max),
    # but bisect avoids re-iterating per-event during humanization.
    times = [pt["t"] for pt in tempo_curve]
    if t <= times[0]:
        return float(tempo_curve[0]["bpm"])
    if t >= times[-1]:
        return float(tempo_curve[-1]["bpm"])
    idx = bisect_left(times, t)
    # idx points at the first entry >= t. Linearly interpolate between
    # idx-1 and idx.
    t0, b0 = times[idx - 1], float(tempo_curve[idx - 1]["bpm"])
    t1, b1 = times[idx], float(tempo_curve[idx]["bpm"])
    span = t1 - t0
    if span <= 0:
        return b0
    frac = (t - t0) / span
    return b0 + (b1 - b0) * frac


def beat_duration_at(tempo_curve: Optional[List[Dict]], t: float,
                     fallback_bpm: float = 120.0) -> float:
    """Convenience: ``60.0 / local_bpm_at(...)``. The most common call site."""
    bpm = local_bpm_at(tempo_curve, t, fallback_bpm)
    return 60.0 / bpm if bpm > 0 else 60.0 / fallback_bpm
