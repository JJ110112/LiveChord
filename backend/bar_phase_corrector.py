"""Serve-time bar phase + meter corrector via brute-force search.

Phase 1 ONNX bar_arbitrator was trained mostly on pop 4/4 and tends to:
  - Force bpb=4 even on 3/4 / 6/4 fusion (Snarky Puppy "Thorn", T-Square)
  - Pick phase=0 even when chord changes clearly land on phase=2 (funk
    backbeat songs like The Crusaders' "Wayne's Pop")

Rule-based brute-force search avoids that bias. For each candidate
(beats_per_bar, phase) we generate a regular downbeat grid by selecting
every Nth beat starting at the phase offset, then score it by how well
chord change times align to those downbeats. Best-scoring grid wins —
but only adopted when alignment improves by at least _MIN_GAIN over the
existing downbeats[]. Otherwise pass through.

Cheap: ~7 alignment evaluations per song (bpb 3/4 × phase 0-3) ≈
milliseconds. No training, deterministic, surgical.

Non-destructive: mutates only the response payload, never on-disk data.
"""

from typing import Dict, List, Optional, Tuple


# Alignment tolerance: chord change is "on a downbeat" if within this many
# seconds of a downbeat. 0.10s is the same value used by quality_audit.py
# and is loose enough to absorb BTC's chord-boundary jitter (~0.05s) plus
# beat tracker quantization (~0.02s).
_ALIGN_TOL_SEC = 0.10

# Refined alignment must improve by at least this absolute amount over the
# current downbeats[] alignment for the correction to apply. Same threshold
# used by phase1_refine in bar_arbitrator. Avoids touching songs that are
# already roughly correct.
_MIN_GAIN = 0.05

# Pre-flight gates — these mirror bar_arbitrator's gates so behaviour is
# consistent across the two layers.
_MIN_CHORD_CHANGES = 8       # need enough boundary signal to score reliably
_MIN_BEATS = 32              # need enough beat grid for any bpb candidate
_ALREADY_CLEAN_ALIGN = 0.65  # high align AND regular cv => skip

# REGULARITY-fix path: when current downbeats are messy (high cv), even
# if accidental hits make alignment look OK, the player downbeat-highlight
# jumps around — that's the user-perceived bug. Replace with regular grid
# as long as alignment doesn't drop badly.
#
# Tightened from 0.20/0.05/0.15 after Brian Culbertson "Twilight" regression:
# corrector applied bpb=3 phase=2 to a real 4/4 song because (a) jazz intro
# inflated cv to 0.25, just over old 0.20 threshold, and (b) some 1.6s
# chord segments accidentally aligned to a 1.62s "bpb=3" grid. Result was
# a corrupted bar_gap (2.96 spb) that chord_splitter then rejected,
# leaving the song with neither sensible downbeats nor any splits.
_CV_MESSY = 0.30             # current cv above this -> consider regularity path
_ALIGN_DROP_TOL = 0.03       # tighter — barely any alignment loss tolerated
_MIN_BEST_ALIGN = 0.20       # new grid must hit at least this absolute

# Candidate meters to try. 3 covers 3/4 waltz, 4 covers 4/4 standard pop.
# 6/8 is rare in pop and BTC's beat tracker usually emits 8th notes anyway,
# making the "real bar" already encoded as bpb=6 in beats — skip for v1.
_BPB_CANDIDATES = (3, 4)


def _gap_cv(times: List[float]) -> Optional[float]:
    """Coefficient of variation of consecutive gaps. None when too few items.
    Used to detect "messy" downbeats — high cv means inter-bar distance varies
    wildly, which makes player downbeat-highlight jump around even though
    individual hits may align by accident.
    """
    if len(times) < 3:
        return None
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1) if times[i + 1] - times[i] > 0.05]
    if len(gaps) < 2:
        return None
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return (var ** 0.5) / mean


def _alignment(chord_changes: List[float], downbeats: List[float],
               tol: float = _ALIGN_TOL_SEC) -> float:
    """Fraction of chord_changes within ``tol`` of any downbeat."""
    if not chord_changes or not downbeats:
        return 0.0
    sorted_db = sorted(downbeats)
    hits = 0
    for t in chord_changes:
        # Inline binary search — same hot-loop as bar_arbitrator
        lo, hi = 0, len(sorted_db)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_db[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        nearest = float("inf")
        if lo < len(sorted_db):
            nearest = min(nearest, abs(sorted_db[lo] - t))
        if lo > 0:
            nearest = min(nearest, abs(sorted_db[lo - 1] - t))
        if nearest <= tol:
            hits += 1
    return hits / len(chord_changes)


def _grid_from_phase(beats: List[float], phase: int, bpb: int) -> List[float]:
    """Pick every bpb-th beat starting at index ``phase``."""
    return list(beats[phase::bpb])


def search_best_phase(
    chord_changes: List[float],
    beats: List[float],
    bpm: float,
) -> Tuple[int, int, float]:
    """Return (bpb, phase, alignment) of the best grid over bpb 3/4 × phase.

    Rejects candidate (bpb, phase) where the resulting bar_gap doesn't
    correspond to an integer-beat-per-bar count at the song's bpm. e.g.
    if beat tracker emits beats at 1.0s spacing on a 111 BPM song (half-
    density emission, common with beat_this on slow jazz intros), bpb=3
    would imply 3.0s bars at 0.54 spb = bpb=5.55, implausible. Same
    sanity check chord_splitter applies — keeps both layers consistent.
    """
    best_bpb, best_phase, best_align = 4, 0, -1.0
    spb = (60.0 / bpm) if bpm > 0 else 0.0
    for bpb in _BPB_CANDIDATES:
        for phase in range(bpb):
            grid = _grid_from_phase(beats, phase, bpb)
            if len(grid) < 4:
                continue
            # Compute median bar_gap from the candidate grid and check it
            # corresponds to a plausible beats-per-bar at the song's bpm.
            gaps = sorted([grid[i + 1] - grid[i] for i in range(len(grid) - 1)])
            if len(gaps) < 2:
                continue
            bar_gap = gaps[len(gaps) // 2]
            if spb > 0:
                computed_bpb = bar_gap / spb
                # Allow 3/4 (2.5-3.5), 4/4 (3.5-4.5), 6/8 (5.5-6.5)
                if not (2.5 <= computed_bpb <= 4.5 or 5.5 <= computed_bpb <= 6.5):
                    continue
            align = _alignment(chord_changes, grid)
            if align > best_align:
                best_bpb = bpb
                best_phase = phase
                best_align = align
    return best_bpb, best_phase, best_align


def correct_phase(chord_data: Dict) -> Dict:
    """Run brute-force phase/meter search and return result metadata.

    Does NOT mutate chord_data. Caller decides whether to apply
    via maybe_correct_for_serve.

    Two paths to apply:
      (A) GAIN path: regular grid alignment beats current by >= _MIN_GAIN.
          Plain improvement — current is bad, candidate is clearly better.
      (B) REGULARITY path: current cv is high (>= _CV_MESSY) — current
          downbeats are messy even if individual hits happen to align.
          Replace with regular grid as long as alignment doesn't drop
          by more than _ALIGN_DROP_TOL. Visual stability matters more
          than accidental hit count.
    """
    chords = chord_data.get("chords") or []
    beats = chord_data.get("beats") or []
    current_dbs = chord_data.get("downbeats") or []

    result = {
        "applied": False,
        "reason": "",
        "align_before": 0.0,
        "align_after": 0.0,
        "cv_before": -1.0,
        "bpb_after": 4,
        "phase_after": 0,
    }

    if len(chords) < _MIN_CHORD_CHANGES + 1:
        result["reason"] = "too-few-chord-changes"
        return result
    if len(beats) < _MIN_BEATS:
        result["reason"] = "too-few-beats"
        return result

    chord_changes = [c["time"] for c in chords[1:] if c.get("time") is not None]
    if len(chord_changes) < _MIN_CHORD_CHANGES:
        result["reason"] = "too-few-chord-changes"
        return result

    current_align = _alignment(chord_changes, current_dbs)
    current_cv = _gap_cv(current_dbs)
    result["align_before"] = round(current_align, 4)
    result["cv_before"] = round(current_cv, 4) if current_cv is not None else -1.0

    # Already-clean: high alignment AND regular cv => leave alone
    if current_align >= _ALREADY_CLEAN_ALIGN and (current_cv is None or current_cv < _CV_MESSY):
        result["reason"] = f"already-clean align={current_align:.2f} cv={current_cv if current_cv else 0:.2f}"
        return result

    bpm = float(chord_data.get("bpm") or 0)
    bpb, phase, best_align = search_best_phase(chord_changes, beats, bpm)
    result["bpb_after"] = bpb
    result["phase_after"] = phase
    result["align_after"] = round(best_align, 4)

    align_gain = best_align - current_align

    # Path A — clear alignment improvement
    if align_gain >= _MIN_GAIN:
        new_grid = _grid_from_phase(beats, phase, bpb)
        result["applied"] = True
        result["downbeats_after"] = new_grid
        result["reason"] = (
            f"phase-fix-gain bpb={bpb} phase={phase} "
            f"align {current_align:.2f}->{best_align:.2f}"
        )
        return result

    # Path B — current is messy, regular grid is similarly aligned
    if (current_cv is not None and current_cv >= _CV_MESSY
            and align_gain >= -_ALIGN_DROP_TOL
            and best_align >= _MIN_BEST_ALIGN):
        new_grid = _grid_from_phase(beats, phase, bpb)
        result["applied"] = True
        result["downbeats_after"] = new_grid
        result["reason"] = (
            f"regularity-fix bpb={bpb} phase={phase} "
            f"cv {current_cv:.2f}->0.00, align {current_align:.2f}->{best_align:.2f}"
        )
        return result

    result["reason"] = (
        f"no-fix (align {current_align:.2f}->{best_align:.2f}, "
        f"cv={current_cv if current_cv else 0:.2f})"
    )
    return result


def maybe_correct_for_serve(chord_data: Dict) -> Dict:
    """Apply phase corrector and stamp metadata on ``chord_data``.

    Mutates chord_data["downbeats"] when applied. Always sets
    ``bar_phase_meta`` so admin/debug can see what happened. Caller may
    write the result to the response without affecting on-disk data.
    """
    res = correct_phase(chord_data)
    chord_data["bar_phase_meta"] = {
        "applied": res["applied"],
        "reason": res["reason"],
        "align_before": res["align_before"],
        "align_after": res["align_after"],
        "bpb_after": res["bpb_after"],
        "phase_after": res["phase_after"],
    }
    if res["applied"] and res.get("downbeats_after"):
        chord_data["downbeats"] = res["downbeats_after"]
    return chord_data
