"""Serve-time chord noise filter — absorb BTC noise-tail chords.

BTC sometimes briefly flips to a different chord in the last 1 beat of a
bar before the next bar's chord starts. The result is a 0.5-1s chord
event sandwiched between two longer chords whose only function in the
chord JSON is visual noise — for example:

    Ebaug [28.38, 29.86] (2.8 beats, on downbeat -> off)
    F     [29.86, 30.42] (1.0 beat,  off -> on downbeat)   <-- noise
    G     [30.42, 34.23] (7.1 beats, on downbeat -> ...)

The user expects Ebaug to span 4 beats — the 1-beat F is the noise tail
the user wants gone. We absorb it into the previous chord (extend
prev.end to noise.end and drop noise).

Conservative rules (ALL must hold to absorb):
  - duration <= 1.0 * spb  (no more than one beat)
  - start NOT near a downbeat (chord change happened mid-bar)
  - end IS near a downbeat (chord ends at a bar line)
  - previous chord exists and is adjacent (no gap)

Real passing chords (1-2 beats placed musically, e.g. ii-V tail in a
turnaround) typically don't satisfy all four — they sit between two
DIFFERENT chord names, both of which are themselves longer than 1 bar,
and they often start ON a downbeat. The filter leaves those alone.

Non-destructive: runs on the response payload only.
"""

from typing import Iterable, List, Dict


# Below this duration, a chord is candidate for noise absorption.
# 1.0 × secs-per-beat = no longer than one beat at the song's BPM.
_NOISE_DUR_BEATS = 1.0

# Tolerance for "is this time near a downbeat" check.
_DB_TOL_SEC = 0.10

# Tolerance for "are these two chords adjacent" check.
_ADJ_TOL_SEC = 0.05


def _is_near(t: float, points: List[float], tol: float) -> bool:
    for p in points:
        if abs(p - t) <= tol:
            return True
    return False


def filter_noise_tails(
    chords: List[Dict],
    downbeats: Iterable[float],
    bpm: float,
) -> List[Dict]:
    """Return a new chord list with noise-tail chords absorbed into the
    previous chord. Pass-through when bpm or downbeats unavailable."""
    if not chords or len(chords) < 3 or bpm <= 0:
        return list(chords)
    db_sorted = sorted(float(d) for d in downbeats if d is not None)
    if not db_sorted:
        return list(chords)
    spb = 60.0 / bpm
    threshold = spb * _NOISE_DUR_BEATS

    out = [dict(c) for c in chords]
    # Walk in reverse so popping doesn't shift indices we still need.
    # Skip first (no prev) and never touch last (caller may rely on song-end).
    for i in range(len(out) - 2, 0, -1):
        c = out[i]
        prev = out[i - 1]
        if c.get("time") is None or c.get("end") is None:
            continue
        if prev.get("end") is None:
            continue
        dur = float(c["end"]) - float(c["time"])
        if dur <= 0 or dur > threshold:
            continue
        # Boundary checks: tail noise has off-db start + on-db end
        if _is_near(float(c["time"]), db_sorted, _DB_TOL_SEC):
            continue
        if not _is_near(float(c["end"]), db_sorted, _DB_TOL_SEC):
            continue
        # Adjacency: previous chord must end where noise starts
        if abs(float(prev["end"]) - float(c["time"])) > _ADJ_TOL_SEC:
            continue
        # Absorb: extend previous, drop noise
        prev["end"] = c["end"]
        out.pop(i)
    return out


def maybe_filter_for_serve(chord_data: Dict) -> Dict:
    """Apply noise filter to ``chord_data["chords"]`` if data is sufficient.

    Mutates and returns ``chord_data``. Adds ``noise_filter_meta`` with
    {applied, reason, before, after, absorbed} so debug/admin can see
    what happened. Always non-destructive — caller may write the result
    to the response without affecting on-disk data.
    """
    chords = chord_data.get("chords") or []
    bpm = float(chord_data.get("bpm") or 0)
    downbeats = chord_data.get("downbeats") or []

    if not chords or bpm <= 0 or len(downbeats) < 2:
        chord_data["noise_filter_meta"] = {
            "applied": False, "reason": "insufficient-data",
            "before": len(chords), "after": len(chords), "absorbed": 0,
        }
        return chord_data

    before_n = len(chords)
    new_chords = filter_noise_tails(chords, downbeats, bpm)
    absorbed = before_n - len(new_chords)
    chord_data["chords"] = new_chords
    chord_data["noise_filter_meta"] = {
        "applied": absorbed > 0,
        "reason": "ok" if absorbed > 0 else "no-noise-found",
        "before": before_n,
        "after": len(new_chords),
        "absorbed": absorbed,
    }
    return chord_data
