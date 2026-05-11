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

# Fragment-risk scoring: shifted downbeats can make normal 4/4 chords render
# as 1+3 / 3+1, and boundary jitter can create 4+1 tails. Treat this as a
# phase-search penalty rather than a hard ban so real pickups survive.
_FRAG_BOUNDARY_EPSILON_SEC = 0.05
_FRAG_ONE_BEAT_MAX = 1.25
_FRAG_LONG_SEG_MIN = 2.50
_FRAG_FULL_BAR_RANGE = (3.5, 4.5)
_FRAG_FIVE_BEAT_RANGE = (4.5, 5.5)
_MAX_FRAGMENT_PENALTY = 0.45
_FRAGMENT_REJECT_PENALTY = 0.18


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


def _interior_points(start: float, end: float, points: List[float]) -> List[float]:
    return [
        p for p in points
        if (p - start) > _FRAG_BOUNDARY_EPSILON_SEC
        and (end - p) > _FRAG_BOUNDARY_EPSILON_SEC
    ]


def fragmentation_risk(
    chords: List[Dict],
    downbeats: List[float],
    bpm: float,
    bpb: int = 4,
) -> Dict:
    """Estimate whether a downbeat grid would create suspicious fragments.

    Returns metadata used by phase scoring and QA. It does not mutate chords.
    """
    result = {
        "penalty": 0.0,
        "bad_fragments": 0,
        "candidate_splits": 0,
        "patterns": {},
        "examples": [],
    }
    if not chords or not downbeats or bpm <= 0 or bpb <= 0:
        return result

    spb = 60.0 / bpm
    if spb <= 0:
        return result
    db_sorted = sorted(float(d) for d in downbeats if d is not None)
    if not db_sorted:
        return result

    patterns = {}

    def add_pattern(name: str, chord: Dict, seg_beats: List[float]) -> None:
        patterns[name] = patterns.get(name, 0) + 1
        result["bad_fragments"] += 1
        if len(result["examples"]) < 5:
            result["examples"].append({
                "time": round(float(chord.get("time", 0.0)), 3),
                "end": round(float(chord.get("end", chord.get("time", 0.0))), 3),
                "chord": chord.get("chord"),
                "pattern": name,
                "segments": [round(v, 2) for v in seg_beats],
            })

    for chord in chords:
        start = chord.get("time")
        end = chord.get("end")
        if start is None or end is None:
            continue
        start_f = float(start)
        end_f = float(end)
        if end_f <= start_f:
            continue
        inner = _interior_points(start_f, end_f, db_sorted)
        if not inner:
            continue

        boundaries = [start_f] + inner + [end_f]
        seg_beats = [(boundaries[i + 1] - boundaries[i]) / spb
                     for i in range(len(boundaries) - 1)]
        dur_beats = (end_f - start_f) / spb
        result["candidate_splits"] += len(inner)

        min_seg = min(seg_beats)
        max_seg = max(seg_beats)
        if (_FRAG_FULL_BAR_RANGE[0] <= dur_beats <= _FRAG_FULL_BAR_RANGE[1]
                and min_seg <= _FRAG_ONE_BEAT_MAX
                and max_seg >= _FRAG_LONG_SEG_MIN):
            left = round(seg_beats[0])
            right = round(sum(seg_beats[1:]))
            if left <= 1:
                add_pattern("1+3", chord, seg_beats)
            elif right <= 1:
                add_pattern("3+1", chord, seg_beats)
            else:
                add_pattern("one-bar-fragment", chord, seg_beats)
        elif (_FRAG_FIVE_BEAT_RANGE[0] <= dur_beats <= _FRAG_FIVE_BEAT_RANGE[1]
              and min_seg <= _FRAG_ONE_BEAT_MAX
              and max_seg >= (bpb - 0.5)):
            left = round(seg_beats[0])
            right = round(sum(seg_beats[1:]))
            if left <= 1:
                add_pattern("1+4", chord, seg_beats)
            elif right <= 1:
                add_pattern("4+1", chord, seg_beats)
            else:
                add_pattern("five-beat-fragment", chord, seg_beats)

    candidate_splits = max(1, result["candidate_splits"])
    bad = result["bad_fragments"]
    penalty = min(
        _MAX_FRAGMENT_PENALTY,
        0.45 * (bad / candidate_splits) + max(0, bad - 8) * 0.01,
    )
    result["penalty"] = round(penalty, 4)
    result["patterns"] = patterns
    return result


def _grid_from_phase(beats: List[float], phase: int, bpb: int) -> List[float]:
    """Pick every bpb-th beat starting at index ``phase``."""
    return list(beats[phase::bpb])


def search_best_phase(
    chord_changes: List[float],
    beats: List[float],
    bpm: float,
    chords: Optional[List[Dict]] = None,
) -> Tuple[int, int, float, Dict]:
    """Return (bpb, phase, alignment) of the best grid over bpb 3/4 × phase.

    Rejects candidate (bpb, phase) where the resulting bar_gap doesn't
    correspond to an integer-beat-per-bar count at the song's bpm. e.g.
    if beat tracker emits beats at 1.0s spacing on a 111 BPM song (half-
    density emission, common with beat_this on slow jazz intros), bpb=3
    would imply 3.0s bars at 0.54 spb = bpb=5.55, implausible. Same
    sanity check chord_splitter applies — keeps both layers consistent.
    """
    best_bpb, best_phase, best_align, best_score = 4, 0, -1.0, -1.0
    best_frag = fragmentation_risk([], [], bpm, 4)
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
            frag = fragmentation_risk(chords or [], grid, bpm, bpb)
            score = align - float(frag.get("penalty", 0.0))
            if score > best_score:
                best_bpb = bpb
                best_phase = phase
                best_align = align
                best_score = score
                best_frag = frag
    return best_bpb, best_phase, best_align, best_frag


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
        "fragment_penalty": 0.0,
        "bad_fragments": 0,
        "fragment_patterns": {},
        "fragment_examples": [],
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
    bpb, phase, best_align, frag = search_best_phase(chord_changes, beats, bpm, chords)
    result["bpb_after"] = bpb
    result["phase_after"] = phase
    result["align_after"] = round(best_align, 4)

    result["fragment_penalty"] = frag.get("penalty", 0.0)
    result["bad_fragments"] = frag.get("bad_fragments", 0)
    result["fragment_patterns"] = frag.get("patterns", {})
    result["fragment_examples"] = frag.get("examples", [])

    align_gain = best_align - current_align
    fragment_ok = float(frag.get("penalty", 0.0)) < _FRAGMENT_REJECT_PENALTY

    # Path A — clear alignment improvement
    if align_gain >= _MIN_GAIN and fragment_ok:
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
            and best_align >= _MIN_BEST_ALIGN
            and fragment_ok):
        new_grid = _grid_from_phase(beats, phase, bpb)
        result["applied"] = True
        result["downbeats_after"] = new_grid
        result["reason"] = (
            f"regularity-fix bpb={bpb} phase={phase} "
            f"cv {current_cv:.2f}->0.00, align {current_align:.2f}->{best_align:.2f}"
        )
        return result

    if not fragment_ok:
        result["reason"] = (
            f"rejected-fragmentation (align {current_align:.2f}->{best_align:.2f}, "
            f"penalty={frag.get('penalty', 0.0):.2f}, bad={frag.get('bad_fragments', 0)})"
        )
    else:
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
        "fragment_penalty": res.get("fragment_penalty", 0.0),
        "bad_fragments": res.get("bad_fragments", 0),
        "fragment_patterns": res.get("fragment_patterns", {}),
        "fragment_examples": res.get("fragment_examples", []),
    }
    if res["applied"] and res.get("downbeats_after"):
        chord_data["downbeats"] = res["downbeats_after"]
    return chord_data
