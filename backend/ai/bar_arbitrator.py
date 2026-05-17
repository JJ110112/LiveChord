"""Bar/downbeat arbitrator — post-processes beat tracker output to fix
phase drift, beats-per-bar confusion, and intermittent downbeat noise.

Why this exists
---------------
beat_this / madmom / librosa each emit a ``downbeats[]`` list that is often
locally noisy:
  - phase wandering (gaps of 1.4s, 3.3s, 0.7s, 0.6s, ... when bar should be 2.8s)
  - bar-doubling (every other bar marked, gaps = 2 × bar_dur)
  - beat-vs-bar confusion (every beat marked as a downbeat, gaps = beat_dur)
  - intro silence not stripped (huge gap up front then settles)

The chord JSON's existing fields (chords[], beats[], downbeats[], bpm,
tempo_curve) carry enough information to detect and correct these *post-hoc*
without re-running expensive audio models.

This module is the deterministic baseline. A Transformer model can later be
trained on a corpus of (raw beat tracker output → human-corrected ground truth)
and dropped in via ``arbitrate(model_path=...)``. The model is OPTIONAL — the
rule-based path is what ships in Phase 0.

Phase 0 scope: feature extraction + rule-based arbitrator + chord JSON
metadata writer. No torch / onnx imports here. Personal-mode 8800 only — not
called from beta 8801 (the calling site in process_queue.py gates on
``LIVECHORD_MODE != "beta"``).

Rule-based baseline is intentionally conservative
-------------------------------------------------
The deterministic path only acts on cases it can identify with high
confidence. It will NOT replace raw downbeats when the candidate grid
scores below ``_MIN_CANDIDATE_F1`` — typically because beat_this's own
``beats[]`` are also noisy (missing beats, false-positive sub-beats), so
no regular grid built on top of them will land on the chord changes.

In other words, the rule baseline reliably handles:
  - Clean songs (gated out by raw-already-regular)
  - Forced recompute via ``force=True`` (admin opt-in)

It does NOT handle phase wandering or beat-vs-bar confusion when the
underlying beat tracking is broken. That's deliberately left to the
Phase 1 Transformer model, which will learn to use chord roots / quality
patterns to recover bar phase even when local beats are unreliable.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from .preprocess import parse_chord_name
except ImportError:
    from preprocess import parse_chord_name

try:
    from backend.bar_phase_corrector import fragmentation_risk as _fragmentation_risk
except ImportError:
    try:
        from bar_phase_corrector import fragmentation_risk as _fragmentation_risk
    except ImportError:
        def _fragmentation_risk(chords, downbeats, bpm, bpb=4):
            return {"penalty": 0.0, "bad_fragments": 0, "patterns": {}, "examples": []}


# ---------------------------------------------------------------------------
# Feature spec — frozen here so training and inference agree
# ---------------------------------------------------------------------------

# Total per-beat feature dimension. Must match FEATURE_DIM in
# scripts/train_bar_arbitrator.py. Bumping this is a breaking change —
# requires retraining + ONNX re-export.
FEATURE_DIM = 28

# Chord quality buckets (indices into the 8-dim one-hot in feature 14-21).
# Keep this list stable — order is baked into trained weights.
_QUALITY_BUCKETS = (
    "maj",   # plain major: "", "maj", "M"
    "min",   # minor: "m", "min" (but not "maj")
    "dim",   # diminished: "dim", "°", "ø" (treat half-dim as dim for this bucket)
    "aug",   # augmented: "aug", "+"
    "sus",   # suspended: "sus", "sus2", "sus4"
    "7",     # dominant 7: contains "7" but not "9"/"11"/"13"/"maj"
    "ext",   # extended: contains "9", "11", "13"
    "none",  # no chord (N / X / unparseable)
)

# Distance saturation for "dist to nearest chord change" feature (seconds).
# Beyond this, chord boundaries don't carry useful local signal — clamp to
# avoid distorting the input distribution with rare long-hold gaps.
_DIST_SAT_SEC = 4.0


def _quality_bucket_idx(quality: Optional[str]) -> int:
    """Map a chord quality string to one of the 8 buckets above."""
    if quality is None:
        return 7  # "none"
    q = quality.lower()
    if "9" in q or "11" in q or "13" in q:
        return 6  # "ext"
    if "sus" in q:
        return 4
    if "dim" in q or "°" in q or "ø" in q:
        return 2
    if "aug" in q or "+" in q:
        return 3
    if "7" in q and "maj" not in q:
        return 5
    if q.startswith("m") and not q.startswith("maj"):
        return 1
    return 0  # "maj" (default)


# ---------------------------------------------------------------------------
# Tunables (deliberately conservative — false-negative > false-positive)
# ---------------------------------------------------------------------------

# Beats-per-bar candidates to try. 4/4 dominates pop, 3/4 for waltzes,
# 6/8 we treat as 6 beats per bar (compound). Excluded: 5, 7, 11 — too rare,
# would inflate the search space and risk over-fitting noisy detections.
_BPB_CANDIDATES = (3, 4, 6)

# Min chord activity over which arbitration is allowed to act. Songs with
# < 8 chords are too short to vote reliably — leave them as-is.
_MIN_CHORDS = 8

# Min beats[] count required for a regular grid hypothesis. Below this, fall
# back to BPM-derived synthetic grid (less precise but still useful).
_MIN_BEATS = 16

# Tolerances for matching grid-tick to event (seconds). Beat ticks are
# precise (0.12s ≈ 1/4 beat at 120 BPM); chord-change times are coarser
# because BTC frames are 0.2-0.4s wide.
_GRID_MATCH_TOL_BEAT = 0.12
_GRID_MATCH_TOL_CHORD = 0.20

# Phase-search resolution as a fraction of the bar duration. 0.05 = try
# offsets at every 5% of the bar (20 candidates per bpb). Trade-off: smaller
# = more accurate, larger = faster. 0.05 → ≈ 60 candidate grids total
# across (bpb=3,4,6), feasible at < 5ms even on a low-end CPU.
_PHASE_SEARCH_FRAC = 0.05

# Coefficient of variation threshold for "raw downbeats are already regular".
# Below this, we leave them alone unless explicit ``force=True``. CV measures
# gap-to-gap variability — clean 4/4 from beat_this is typically < 0.05; mild
# rubato pushes 0.05–0.10; phase wandering / bar-doubling jumps to > 0.20.
_RAW_REGULAR_CV = 0.10

# Minimum chord-change F1 the candidate grid must achieve before we trust it
# enough to replace raw. Below this, the song probably has too few harmonic
# anchors for any grid to score well — better to keep raw than write a guess.
_MIN_CANDIDATE_F1 = 0.35

# Minimum mean per-beat downbeat probability across the chosen grid before
# we accept the model output. Empirically the trained model gives ~0.6+ on
# clean songs and ~0.3 on songs where it's uncertain. Mid-0.4 is the gate.
_MIN_MODEL_CONFIDENCE = 0.40


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class ArbitrationResult(dict):
    """Return shape from ``arbitrate``. dict subclass so it's JSON-friendly.

    Keys:
      applied: bool — did we actually replace the downbeats?
      reason: str — short human-readable explanation
      model_version: str — "rule_v1" today, "model_v1" after Phase 1
      downbeats_before: list[float]
      downbeats_after: list[float]
      beats_per_bar: int
      phase_offset_sec: float — first downbeat in the corrected grid
      raw_regularity_cv: float | None — coefficient of variation of raw gaps
      score_before: float
      score_after: float
    """


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _chord_root_pc(chord_str: str) -> Optional[int]:
    """Return chord root pitch class (0-11) or None for N/X/unparseable."""
    pc, _ = parse_chord_name(chord_str)
    return pc


def _chord_at_time(chords: List[Dict], t: float) -> Optional[Dict]:
    """Return the chord record covering time ``t``, or None.

    Linear scan — chord lists are usually < 200 entries, so binary search
    isn't worth the complexity in this hot-but-tiny path. Caller iterates
    per-beat (a few hundred) so total cost is bounded.
    """
    for c in chords:
        if c.get("time", 0) <= t < c.get("end", c.get("time", 0)):
            return c
    return None


def _chord_change_times(chords: List[Dict]) -> List[float]:
    """Return list of chord-onset times. First chord's time included.

    These are the "harmonic rhythm" anchors — bar lines tend to fall on or
    near chord changes in pop music.
    """
    if not chords:
        return []
    out = [float(chords[0].get("time", 0.0))]
    last_chord = chords[0].get("chord")
    for c in chords[1:]:
        cur = c.get("chord")
        if cur != last_chord:  # only true changes, not consecutive same-chord splits
            out.append(float(c.get("time", 0.0)))
            last_chord = cur
    return out


def _bisect_left(seq: List[float], v: float) -> int:
    """Inline bisect — avoids the function-call cost of stdlib ``bisect`` in
    the per-beat hot loop."""
    lo, hi = 0, len(seq)
    while lo < hi:
        mid = (lo + hi) // 2
        if seq[mid] < v:
            lo = mid + 1
        else:
            hi = mid
    return lo


def build_beat_features(chord_data: Dict):
    """Build the per-beat feature matrix for one song.

    Returns a numpy ndarray of shape ``(T, FEATURE_DIM)`` where T = number
    of beats in ``chord_data["beats"]``. Returns ``None`` when the song is
    unusable (no beats, no chords).

    Features (in order, must match the slicing in any consumer):

      0     inter_beat_dt           — seconds to next beat (last beat: 0)
      1     chord_change_in_beat    — 1 if a chord boundary lies in [b_i, b_{i+1})
      2-13  chord_root_pc one-hot   — 12 dims, all zero for "no chord"
      14-21 chord_quality bucket    — 8 dims (see _QUALITY_BUCKETS)
      22    dist_to_prev_change_norm — clamped to _DIST_SAT_SEC, ÷ sat
      23    dist_to_next_change_norm — same
      24    pos_in_song             — beat_time / song_duration
      25    relative_bpm            — local_beat_dur / median_beat_dur
      26    cc_density_last_4_norm  — chord changes in last 4 beats / 4
      27    cc_density_next_4_norm  — chord changes in next 4 beats / 4

    Imported lazily to keep module-level import cost low for callers that
    only use the rule-based path.
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy unavailable — build_beat_features returns None")
        return None

    beats = chord_data.get("beats") or []
    chords = chord_data.get("chords") or []
    if len(beats) < 2 or not chords:
        return None

    T = len(beats)
    feat = np.zeros((T, FEATURE_DIM), dtype=np.float32)

    # Pre-compute per-beat chord lookup via two-pointer sweep — O(T + N_chords)
    chord_idx_at_beat = [None] * T
    j = 0
    for i, t in enumerate(beats):
        # Advance j to the chord whose [time, end) contains t (or past t)
        while j < len(chords) and chords[j].get("end", chords[j].get("time", 0)) <= t:
            j += 1
        if j < len(chords) and chords[j].get("time", 0) <= t:
            chord_idx_at_beat[i] = j

    # Chord change times (sorted)
    cc = _chord_change_times(chords)

    # Inter-beat gaps + median for relative_bpm
    gaps = [beats[i + 1] - beats[i] for i in range(T - 1)] + [0.0]
    pos_gaps = [g for g in gaps[:-1] if g > 0]
    median_gap = sorted(pos_gaps)[len(pos_gaps) // 2] if pos_gaps else 0.5

    # Song duration for pos_in_song normalization
    song_dur = max(
        beats[-1],
        chord_data.get("duration") or 0.0,
        chords[-1].get("end", 0.0) if chords else 0.0,
        0.001,
    )

    for i, t in enumerate(beats):
        # 0: inter_beat_dt
        feat[i, 0] = gaps[i]

        # 1: chord_change_in_beat — any cc time in [t, beats[i+1] or t+median_gap)
        end_t = beats[i + 1] if i + 1 < T else t + median_gap
        idx = _bisect_left(cc, t)
        feat[i, 1] = 1.0 if (idx < len(cc) and cc[idx] < end_t) else 0.0

        # 2-13: chord_root_pc one-hot
        ci = chord_idx_at_beat[i]
        quality = None
        if ci is not None:
            chord_str = chords[ci].get("chord", "")
            pc, quality = parse_chord_name(chord_str)
            if pc is not None:
                feat[i, 2 + pc] = 1.0
        # else: leave one-hot all-zero (interpreted as "no chord")

        # 14-21: chord_quality bucket
        feat[i, 14 + _quality_bucket_idx(quality)] = 1.0

        # 22: dist_to_prev_change (saturated)
        prev_idx = idx - 1 if idx > 0 else None
        if prev_idx is not None and prev_idx < len(cc):
            d = max(0.0, t - cc[prev_idx])
            feat[i, 22] = min(d, _DIST_SAT_SEC) / _DIST_SAT_SEC
        else:
            feat[i, 22] = 1.0  # no prior change → saturated

        # 23: dist_to_next_change (saturated)
        if idx < len(cc):
            d = max(0.0, cc[idx] - t)
            feat[i, 23] = min(d, _DIST_SAT_SEC) / _DIST_SAT_SEC
        else:
            feat[i, 23] = 1.0

        # 24: pos_in_song (clamped to [0, 1])
        feat[i, 24] = max(0.0, min(1.0, t / song_dur))

        # 25: relative_bpm — local gap vs median (1.0 = on tempo)
        feat[i, 25] = (gaps[i] / median_gap) if (median_gap > 0 and gaps[i] > 0) else 1.0

        # 26-27: chord-change density in last/next 4 beats — windowed
        win_back_t = beats[max(0, i - 4)]
        win_fwd_t = beats[min(T - 1, i + 4)]
        cc_back = _bisect_left(cc, t) - _bisect_left(cc, win_back_t)
        cc_fwd = _bisect_left(cc, win_fwd_t) - _bisect_left(cc, t)
        feat[i, 26] = cc_back / 4.0
        feat[i, 27] = cc_fwd / 4.0

    return feat


def _gap_stats(seq: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Return (mean_gap, coefficient_of_variation) for a sorted sequence.

    Used to assess regularity of an arbitrary timeline (raw downbeats,
    candidate grid, etc). Returns (None, None) if too short.
    """
    if len(seq) < 3:
        return None, None
    gaps = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None, None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    std = math.sqrt(var)
    return mean, std / mean


# ---------------------------------------------------------------------------
# Grid generation + scoring
# ---------------------------------------------------------------------------

def _generate_grid(start_t: float, end_t: float, bar_dur: float,
                   phase_offset: float) -> List[float]:
    """Generate evenly-spaced bar-line times.

    Args:
        start_t: don't emit ticks before this (typically first beat or first chord time).
        end_t: don't emit ticks after this (typically last beat or last chord end).
        bar_dur: spacing in seconds.
        phase_offset: time of the first tick (will be adjusted forward by k*bar_dur
                      until it lands in [start_t, start_t + bar_dur)).

    Returns: list of monotonically increasing tick times.
    """
    if bar_dur <= 0 or end_t <= start_t:
        return []
    # Roll phase forward until it's in the first bar of the song
    while phase_offset > start_t:
        phase_offset -= bar_dur
    while phase_offset + bar_dur <= start_t:
        phase_offset += bar_dur
    out = []
    t = phase_offset
    # Hard cap to prevent runaway loops on bad inputs
    max_ticks = int((end_t - start_t) / bar_dur) + 4
    for _ in range(max_ticks):
        if t > end_t:
            break
        if t >= start_t - 1e-6:
            out.append(round(t, 4))
        t += bar_dur
    return out


def _match_count(grid: List[float], events: List[float], tol: float) -> int:
    """Count grid ticks that have at least one event within ``tol`` seconds.

    Grid and events both sorted ascending. Two-pointer sweep, O(n + m).
    """
    if not grid or not events:
        return 0
    matches = 0
    j = 0
    for g in grid:
        # Advance j to first event >= g - tol
        while j < len(events) and events[j] < g - tol:
            j += 1
        if j < len(events) and events[j] <= g + tol:
            matches += 1
    return matches


def _score_grid(grid: List[float], beats: List[float], chord_changes: List[float],
                bar_dur: float) -> float:
    """F1-style score: balances precision (grid ticks land on real events)
    and recall (real chord changes have a nearby grid tick).

    Why F1 not pure precision: a dense grid with many ticks can game pure
    recall (any chord change has SOME tick within tol just by saturation).
    A sparse grid with few ticks can game pure precision (each tick happens
    to land on an event, but most of the song is uncovered).

    The chord-change F1 dominates (weight 0.7) because harmonic rhythm is
    the strongest bar-line cue; beat alignment (precision only, 0.3) is a
    tie-breaker for songs where chord changes are sparse.
    """
    if not grid:
        return 0.0
    n_grid = len(grid)

    # Chord changes — F1
    if chord_changes:
        cc_grid_hits = _match_count(grid, chord_changes, _GRID_MATCH_TOL_CHORD)
        cc_event_hits = _match_count(chord_changes, grid, _GRID_MATCH_TOL_CHORD)
        cc_precision = cc_grid_hits / n_grid
        cc_recall = cc_event_hits / len(chord_changes)
        cc_f1 = (
            2 * cc_precision * cc_recall / (cc_precision + cc_recall)
            if (cc_precision + cc_recall) > 0 else 0.0
        )
    else:
        cc_f1 = 0.0

    # Beat alignment — precision only (every grid tick should land on a beat;
    # the reverse "every beat is on a bar line" is false by definition)
    if beats:
        bt_precision = _match_count(grid, beats, _GRID_MATCH_TOL_BEAT) / n_grid
    else:
        bt_precision = 0.0

    return 0.7 * cc_f1 + 0.3 * bt_precision


# ---------------------------------------------------------------------------
# Rule-based arbitration
# ---------------------------------------------------------------------------

def _search_best_grid(beats: List[float], chord_changes: List[float],
                      bpm: float, span_start: float, span_end: float
                      ) -> Tuple[int, float, float, List[float]]:
    """Brute-force search over (beats_per_bar, phase_offset).

    Returns: (best_bpb, best_phase_offset, best_score, best_grid).

    For each bpb candidate (3, 4, 6), we know bar_dur = 60/bpm * bpb. We
    sweep phase across one bar in steps of _PHASE_SEARCH_FRAC * bar_dur and
    keep the highest-scoring (bpb, phase). Chord-change times are the
    primary anchor, beats[] tie-break.
    """
    best = (4, span_start, -1.0, [])
    if bpm <= 0:
        return best
    beat_dur = 60.0 / bpm
    for bpb in _BPB_CANDIDATES:
        bar_dur = beat_dur * bpb
        if bar_dur <= 0 or bar_dur > (span_end - span_start) / 2:
            # Bar is so long the grid would have < 2 ticks — skip
            continue
        step = max(0.01, bar_dur * _PHASE_SEARCH_FRAC)
        phase = span_start
        end_phase = span_start + bar_dur
        while phase < end_phase:
            grid = _generate_grid(span_start, span_end, bar_dur, phase)
            score = _score_grid(grid, beats, chord_changes, bar_dur)
            if score > best[2]:
                best = (bpb, phase, score, grid)
            phase += step
    return best


def arbitrate_rule_based(chord_data: Dict, *, force: bool = False) -> ArbitrationResult:
    """Deterministic bar-line arbitration. Reads from a chord JSON dict
    (post ``analyze_and_snap_dynamic`` shape) and returns an
    :class:`ArbitrationResult`.

    Does NOT mutate ``chord_data``. Callers decide whether to write the
    result back into the JSON.

    Args:
        chord_data: dict with keys ``chords``, ``beats``, ``downbeats``,
            ``bpm`` (and optional ``tempo_curve``).
        force: when True, replace the raw downbeats even if they look
            already-regular. Used by admin recompute with ``mode=force``.
    """
    chords = chord_data.get("chords") or []
    beats = chord_data.get("beats") or []
    raw_downbeats = chord_data.get("downbeats") or []
    bpm = chord_data.get("bpm")

    result = ArbitrationResult(
        applied=False,
        reason="",
        model_version="rule_v1",
        downbeats_before=list(raw_downbeats),
        downbeats_after=list(raw_downbeats),
        beats_per_bar=4,
        phase_offset_sec=0.0,
        raw_regularity_cv=None,
        score_before=0.0,
        score_after=0.0,
    )

    # ------------------------------------------------------------------
    # Pre-flight gates — return early when we can't help
    # ------------------------------------------------------------------
    if len(chords) < _MIN_CHORDS:
        result["reason"] = "too-few-chords"
        return result
    if not bpm or bpm <= 0:
        result["reason"] = "no-bpm"
        return result
    if len(beats) < _MIN_BEATS:
        # We can still synthesize a grid from BPM alone, but with no beats[]
        # to score against, the result is essentially "BPM × 4" — not worth
        # writing as a "correction".
        result["reason"] = "too-few-beats"
        return result

    chord_changes = _chord_change_times(chords)
    if len(chord_changes) < 4:
        result["reason"] = "too-few-chord-changes"
        return result

    span_start = min(beats[0], chord_changes[0])
    span_end = max(beats[-1], chord_changes[-1])
    if span_end <= span_start:
        result["reason"] = "degenerate-time-span"
        return result

    # ------------------------------------------------------------------
    # Assess raw regularity (the gate that decides whether to replace)
    # ------------------------------------------------------------------
    raw_mean_gap, raw_cv = _gap_stats(raw_downbeats)
    result["raw_regularity_cv"] = round(raw_cv, 4) if raw_cv is not None else None

    # ``score_before`` exists only for telemetry now — we don't use it as a
    # comparison gate (a dense raw downbeats[] always wins F1 by saturation).
    # We score raw using the bar_dur whose value most closely matches the
    # raw gap, just so the number printed in metadata is informative.
    raw_score = 0.0
    if raw_downbeats and raw_mean_gap and raw_mean_gap > 0:
        beat_dur = 60.0 / bpm
        best_bpb_for_raw = min(_BPB_CANDIDATES,
                               key=lambda k: abs(beat_dur * k - raw_mean_gap))
        raw_score = _score_grid(raw_downbeats, beats, chord_changes,
                                beat_dur * best_bpb_for_raw)
    result["score_before"] = round(raw_score, 4)

    # ------------------------------------------------------------------
    # Search for the best corrected grid
    # ------------------------------------------------------------------
    best_bpb, best_phase, best_score, best_grid = _search_best_grid(
        beats, chord_changes, bpm, span_start, span_end
    )
    result["beats_per_bar"] = best_bpb
    result["phase_offset_sec"] = round(best_phase, 4) if best_grid else 0.0
    result["score_after"] = round(best_score, 4)

    if not best_grid:
        result["reason"] = "no-viable-grid"
        return result

    # ------------------------------------------------------------------
    # Replacement decision — three gates
    # ------------------------------------------------------------------

    # Gate 1: raw is already regular → keep it (unless forced)
    raw_frag = _fragmentation_risk(
        chords, raw_downbeats, float(bpm), result.get("beats_per_bar", 4)
    )
    raw_already_clean = (
        raw_cv is not None and raw_cv < _RAW_REGULAR_CV
        and raw_downbeats and len(raw_downbeats) >= 8
        and float(raw_frag.get("penalty", 0.0)) < 0.18
    )
    if raw_already_clean and not force:
        result["reason"] = "raw-already-regular"
        return result

    # Gate 2: candidate grid must clear absolute F1 floor — below this the
    # song lacks the harmonic structure to anchor any grid confidently
    if best_score < _MIN_CANDIDATE_F1 and not force:
        result["reason"] = f"candidate-f1-too-low ({best_score:.2f})"
        return result

    # Otherwise: replace. Either raw is irregular enough that any plausible
    # grid is an improvement, or force=True bypasses both gates.
    result["downbeats_after"] = best_grid
    result["applied"] = True
    result["reason"] = (
        f"rule-replace bpb={best_bpb} raw_cv={raw_cv:.2f} f1={best_score:.2f}"
        + (" forced" if force else "")
    )
    return result


# ---------------------------------------------------------------------------
# Model-based arbitration (ONNX)
# ---------------------------------------------------------------------------

# bpb classes — must match scripts/train_bar_arbitrator.py BPB_CLASSES order.
# The ONNX bpb_logits output is over this index space.
_MODEL_BPB_CLASSES = (3, 4, 6)
_MODEL_VERSION = "model_v1"

# Cached ONNX session per process. None means "not loaded yet"; False means
# "tried to load and failed" (so we don't retry on every call).
_onnx_session_cache: Dict[str, Any] = {}


def _get_onnx_session(model_path: str):
    """Return cached onnxruntime InferenceSession, or None if unavailable."""
    cached = _onnx_session_cache.get(model_path)
    if cached is False:
        return None
    if cached is not None:
        return cached
    try:
        import onnxruntime as ort
    except ImportError:
        logger.warning("onnxruntime not installed — model_v1 path disabled")
        _onnx_session_cache[model_path] = False
        return None
    try:
        sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        _onnx_session_cache[model_path] = sess
        return sess
    except Exception as e:
        logger.warning("failed to load ONNX model %s: %s", model_path, e)
        _onnx_session_cache[model_path] = False
        return None


def _decode_downbeats_from_probs(beats: List[float], db_probs,
                                 beats_per_bar: int) -> Tuple[List[float], float]:
    """Pick the phase offset (which beat is downbeat) that maximizes the
    sum of model downbeat probabilities along the resulting bar grid.

    Constraint: bars are exactly ``beats_per_bar`` beats apart. So the only
    free parameter is which of the first ``beats_per_bar`` beats anchors
    the grid.

    Returns: (downbeat_times, mean_confidence).
    """
    n = len(beats)
    if n == 0 or beats_per_bar <= 0:
        return [], 0.0
    best_offset = 0
    best_score = -1.0
    best_count = 0
    for offset in range(beats_per_bar):
        idxs = list(range(offset, n, beats_per_bar))
        if not idxs:
            continue
        score = sum(float(db_probs[i]) for i in idxs)
        if score > best_score:
            best_score = score
            best_offset = offset
            best_count = len(idxs)
    if best_count == 0:
        return [], 0.0
    grid = [round(beats[i], 4) for i in range(best_offset, n, beats_per_bar)]
    mean_conf = best_score / best_count
    return grid, mean_conf


def arbitrate_model_based(chord_data: Dict, model_path: str, *,
                          force: bool = False) -> ArbitrationResult:
    """Run the ONNX-trained Transformer arbitrator on ``chord_data``.

    Falls back to rule-based when the model is unavailable or its
    confidence is too low. Mutates nothing — caller decides on
    apply_to_chord_json.
    """
    sess = _get_onnx_session(model_path)
    if sess is None:
        # Hard fall back to rule baseline
        return arbitrate_rule_based(chord_data, force=force)

    chords = chord_data.get("chords") or []
    beats = chord_data.get("beats") or []
    raw_downbeats = chord_data.get("downbeats") or []

    result = ArbitrationResult(
        applied=False, reason="", model_version=_MODEL_VERSION,
        downbeats_before=list(raw_downbeats), downbeats_after=list(raw_downbeats),
        beats_per_bar=4, phase_offset_sec=0.0,
        raw_regularity_cv=None, score_before=0.0, score_after=0.0,
    )

    # Pre-flight gates (mirror rule baseline so behavior is consistent)
    if len(chords) < _MIN_CHORDS:
        result["reason"] = "too-few-chords"
        return result
    if len(beats) < _MIN_BEATS:
        result["reason"] = "too-few-beats"
        return result

    raw_mean_gap, raw_cv = _gap_stats(raw_downbeats)
    result["raw_regularity_cv"] = round(raw_cv, 4) if raw_cv is not None else None

    # Already-regular gate (model path also respects this — replacing a
    # clean song is risky, model could regress something the user already
    # likes). Forced recompute bypasses.
    raw_frag = _fragmentation_risk(
        chords, raw_downbeats, float(chord_data.get("bpm") or 120.0), 4
    )
    raw_already_clean = (
        raw_cv is not None and raw_cv < _RAW_REGULAR_CV
        and raw_downbeats and len(raw_downbeats) >= 8
        and float(raw_frag.get("penalty", 0.0)) < 0.18
    )
    if raw_already_clean and not force:
        result["reason"] = "raw-already-regular"
        return result

    # Build features
    try:
        feat = build_beat_features(chord_data)
    except Exception as e:
        logger.warning("build_beat_features error: %s", e)
        feat = None
    if feat is None or feat.shape[0] < _MIN_BEATS:
        result["reason"] = "feature-extraction-failed"
        return result

    # Run ONNX
    try:
        import numpy as np
        x = feat[None, ...].astype(np.float32)
        mask = np.zeros((1, x.shape[1]), dtype=bool)
        db_logits, bpb_logits = sess.run(
            ["downbeat_logits", "bpb_logits"],
            {"features": x, "padding_mask": mask},
        )
        # Sigmoid for db, argmax for bpb
        db_probs = 1.0 / (1.0 + np.exp(-db_logits[0]))
        bpb_idx = int(bpb_logits[0].argmax())
        beats_per_bar = _MODEL_BPB_CLASSES[bpb_idx]
    except Exception as e:
        logger.warning("ONNX inference failed: %s", e)
        return arbitrate_rule_based(chord_data, force=force)

    # Decode to a regular downbeat grid via per-phase probability sum
    grid, mean_conf = _decode_downbeats_from_probs(beats, db_probs, beats_per_bar)
    result["beats_per_bar"] = beats_per_bar
    result["score_after"] = round(float(mean_conf), 4)
    if grid:
        result["phase_offset_sec"] = round(grid[0], 4)

    if not grid:
        result["reason"] = "model-decode-empty"
        return result

    # Confidence gate — analogous to rule path's F1 floor
    if mean_conf < _MIN_MODEL_CONFIDENCE and not force:
        result["reason"] = f"model-confidence-too-low ({mean_conf:.2f})"
        return result

    result["downbeats_after"] = grid
    result["applied"] = True
    result["reason"] = (
        f"model-replace bpb={beats_per_bar} raw_cv={raw_cv if raw_cv is not None else float('nan'):.2f} "
        f"conf={mean_conf:.2f}" + (" forced" if force else "")
    )
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def arbitrate(chord_data: Dict, *, model_path: Optional[str] = None,
              force: bool = False) -> ArbitrationResult:
    """Main arbitrator. Uses the ONNX model if ``model_path`` is provided
    and loadable; otherwise falls back to the rule-based baseline.

    Args:
        chord_data: post-snap chord JSON dict.
        model_path: optional path to ``bar_arbitrator_v1.onnx``. None or
            unloadable model → rule-based path.
        force: skip the "raw already regular" / "low confidence" gates.

    Returns: ArbitrationResult — never raises; on internal error returns
        applied=False with reason describing the failure.
    """
    try:
        if model_path:
            return arbitrate_model_based(chord_data, model_path, force=force)
        return arbitrate_rule_based(chord_data, force=force)
    except Exception as e:
        logger.warning("bar_arbitrator failed: %s", e)
        return ArbitrationResult(
            applied=False,
            reason=f"exception: {type(e).__name__}",
            model_version="rule_v1" if not model_path else _MODEL_VERSION,
            downbeats_before=list(chord_data.get("downbeats") or []),
            downbeats_after=list(chord_data.get("downbeats") or []),
            beats_per_bar=4,
            phase_offset_sec=0.0,
            raw_regularity_cv=None,
            score_before=0.0,
            score_after=0.0,
        )


def apply_to_chord_json(chord_data: Dict, result: ArbitrationResult) -> Dict:
    """Mutate ``chord_data`` to write the arbitrator result.

    Adds ``bar_correction`` metadata (always, even when applied=False, so
    we know we tried) and replaces ``downbeats`` only when applied=True.
    Does not touch beats / bpm / tempo_curve.

    Snapshot for restore: on first apply, the *current* downbeats are
    snapshotted into ``bar_correction.downbeats_original``. Subsequent
    apply→restore→apply cycles preserve the very-first snapshot — the
    snapshot is set once and never overwritten, so the restore endpoint
    always returns to truly-original raw downbeats from the beat tracker.
    """
    prev = chord_data.get("bar_correction") or {}
    bar_meta = {
        "applied": result["applied"],
        "reason": result["reason"],
        "model_version": result["model_version"],
        "beats_per_bar": result["beats_per_bar"],
        "phase_offset_sec": result["phase_offset_sec"],
        "raw_regularity_cv": result["raw_regularity_cv"],
        "score_before": result["score_before"],
        "score_after": result["score_after"],
        "n_downbeats_before": len(result["downbeats_before"]),
        "n_downbeats_after": len(result["downbeats_after"]),
    }

    # Preserve any pre-existing snapshot — never overwrite truly-original
    if "downbeats_original" in prev:
        bar_meta["downbeats_original"] = prev["downbeats_original"]
    elif result["applied"]:
        # First-time apply: snapshot current (= raw) downbeats so restore works.
        # ``downbeats_before`` from result is exactly that snapshot, captured
        # before the arbitrator ran.
        bar_meta["downbeats_original"] = list(result["downbeats_before"])

    chord_data["bar_correction"] = bar_meta
    if result["applied"]:
        chord_data["downbeats"] = result["downbeats_after"]
    return chord_data


# ---------------------------------------------------------------------------
# Phase 1 — audio-level beat_refiner integration
#
# Different layer from arbitrate_model_based above:
#   arbitrate_model_based  — 28-feature-per-beat ONNX, picks the downbeat
#                            phase given a *trusted* beats[] grid.
#   phase1_refine          — audio-level Compact Transformer that re-emits
#                            both beats[] AND downbeats[] from CQT/chroma/
#                            onset/RMS + the initial grid as a hint.
#
# Use one or the other depending on whether you trust raw beats[]. For the
# 3+1 chord-split problem (driven by misaligned beats[] in low-rhythm
# passages), only phase1_refine can help — the ONNX model treats those
# beats as ground truth.
# ---------------------------------------------------------------------------

# Tunables for the phase1 quality gate. The gate exists because the v1
# refiner over-emits peaks on songs whose input grid is already clean,
# and we don't want to corrupt good data.
_PHASE1_INPUT_CLEAN_CV = 0.10        # input cv < this → skip refine
_PHASE1_INPUT_CLEAN_ALIGN = 0.70     # input alignment > this → skip refine
_PHASE1_ALIGN_TOL = 0.07             # mir_eval beat tolerance
_PHASE1_ALIGN_MIN_GAIN = 0.02        # refined align must beat input by ≥ this.
                                     # v2 testing showed permissive -0.05
                                     # adopted "no-improvement" outputs that
                                     # added cv noise; require small positive
                                     # gain to ensure refinement is worth it.
_PHASE1_COUNT_RATIO = (0.5, 1.8)     # refined count must stay in this band
                                     # × input count (avoids drastic over-
                                     # emission)
_PHASE1_BPB_MIN = 3.0                # refined median(downbeat_gap)/median(beat_gap)
_PHASE1_BPB_MAX = 5.0                # must stay in [3, 5] when input is in same band.
                                     # Catches 4/4 -> 2/4 (downbeat doubling) and
                                     # 4/4 -> 8/8 (downbeat halving) — both produce
                                     # apparent meter shift that breaks chord_splitter
                                     # + player rendering even though raw alignment
                                     # may improve. See LiveChord-3kh.


def _bpb_from_gaps(beats: List[float], downbeats: List[float]) -> Optional[float]:
    """Estimate beats-per-bar = median(downbeat_gap) / median(beat_gap).

    Returns None when either side has too few entries to estimate. Used by
    phase1_refine to detect meter shifts (4/4 -> 2/4 etc) caused by the
    model over-densifying downbeats — those shifts pass alignment but break
    the chord_splitter + player rendering pipeline downstream.
    """
    if len(beats) < 5 or len(downbeats) < 3:
        return None
    bgaps = sorted([beats[i + 1] - beats[i] for i in range(len(beats) - 1)])
    dgaps = sorted([downbeats[i + 1] - downbeats[i] for i in range(len(downbeats) - 1)])
    bgaps = [g for g in bgaps if g > 0.05]
    dgaps = [g for g in dgaps if g > 0.1]
    if not bgaps or not dgaps:
        return None
    bmed = bgaps[len(bgaps) // 2]
    dmed = dgaps[len(dgaps) // 2]
    if bmed <= 0:
        return None
    return dmed / bmed


def _alignment(chord_changes: List[float], downbeats: List[float],
               tol: float = _PHASE1_ALIGN_TOL) -> float:
    """Fraction of chord changes within ``tol`` of any downbeat.

    Same metric used by ``scripts/build_training_corpus.py`` to grade
    silver-tier candidates. Higher = downbeats agree with harmony.
    """
    if not chord_changes or not downbeats:
        return 0.0
    sorted_db = sorted(downbeats)
    hits = 0
    for t in chord_changes:
        # Inline bisect_left — same hot-loop pattern as elsewhere here
        lo, hi = 0, len(sorted_db)
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_db[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        i = lo
        nearest = float("inf")
        if i < len(sorted_db):
            nearest = min(nearest, abs(sorted_db[i] - t))
        if i > 0:
            nearest = min(nearest, abs(sorted_db[i - 1] - t))
        if nearest <= tol:
            hits += 1
    return hits / len(chord_changes)


def phase1_refine(chord_data: Dict, audio_path: str, *,
                  force: bool = False,
                  checkpoint_path: Optional[str] = None
                  ) -> ArbitrationResult:
    """Audio-level beat / downbeat refinement via the trained Compact
    Transformer (``backend/ai/beat_refiner_*``).

    Reads from ``chord_data["beats"]``, ``chord_data["downbeats"]``,
    ``chord_data["chords"]`` and the audio at ``audio_path``. Does NOT
    mutate ``chord_data`` — the caller decides whether to write back.

    Quality gate (in order):
      1. Pre-flight: too few beats/chords → bail (same as rule path).
      2. Input-already-clean: low cv + high alignment → skip, save GPU.
      3. Refiner failure: model missing / inference error → bail with
         the refiner's reason.
      4. Output-not-better: refined alignment < input - 0.05, OR refined
         downbeat count is wildly off from input → reject refined.

    ``force=True`` skips gates 2 and 4 (admin recompute).

    Returns an :class:`ArbitrationResult` with both downbeats_before/after
    and (extended) ``beats_before``/``beats_after`` fields when applied.
    """
    chords = chord_data.get("chords") or []
    beats = chord_data.get("beats") or []
    raw_downbeats = chord_data.get("downbeats") or []

    result = ArbitrationResult(
        applied=False,
        reason="",
        model_version="phase1_v1",
        downbeats_before=list(raw_downbeats),
        downbeats_after=list(raw_downbeats),
        beats_before=list(beats),
        beats_after=list(beats),
        beat_probs=[],
        downbeat_probs=[],
        beats_per_bar=4,  # phase1 model doesn't predict bpb
        phase_offset_sec=0.0,
        raw_regularity_cv=None,
        score_before=0.0,
        score_after=0.0,
    )

    # ------------------------------------------------------------------
    # Gate 1 — pre-flight (same as rule path)
    # ------------------------------------------------------------------
    if len(chords) < _MIN_CHORDS:
        result["reason"] = "too-few-chords"
        return result
    if len(beats) < _MIN_BEATS:
        result["reason"] = "too-few-beats"
        return result

    chord_changes = _chord_change_times(chords)
    if len(chord_changes) < 4:
        result["reason"] = "too-few-chord-changes"
        return result

    in_mean_gap, in_cv = _gap_stats(raw_downbeats)
    in_align = _alignment(chord_changes, raw_downbeats)
    result["raw_regularity_cv"] = round(in_cv, 4) if in_cv is not None else None
    result["score_before"] = round(in_align, 4)

    # ------------------------------------------------------------------
    # Gate 2 — skip when input is already clean
    # ------------------------------------------------------------------
    input_clean = (
        in_cv is not None and in_cv < _PHASE1_INPUT_CLEAN_CV
        and in_align > _PHASE1_INPUT_CLEAN_ALIGN
        and len(raw_downbeats) >= 8
    )
    if input_clean and not force:
        result["reason"] = (
            f"input-already-clean (cv={in_cv:.3f}, align={in_align:.3f})"
        )
        return result

    # ------------------------------------------------------------------
    # Run refiner — late import keeps this module light when no audio path
    # ------------------------------------------------------------------
    try:
        from .beat_refiner_infer import refine
    except ImportError:
        try:
            from beat_refiner_infer import refine  # script-mode fallback
        except ImportError as e:
            result["reason"] = f"refiner-import-failed: {e}"
            return result

    try:
        refine_out = refine(
            audio_path, beats, raw_downbeats,
            checkpoint_path=checkpoint_path,
        )
    except Exception as e:
        result["reason"] = f"refiner-exception: {type(e).__name__}: {e}"
        return result

    if not refine_out.get("applied"):
        result["reason"] = f"refiner-bailed: {refine_out.get('reason', '?')}"
        return result

    refined_beats = refine_out["refined_beats"]
    refined_downbeats = refine_out["refined_downbeats"]

    # ------------------------------------------------------------------
    # Gate 4 — output-not-better quality check
    # ------------------------------------------------------------------
    out_align = _alignment(chord_changes, refined_downbeats)
    out_mean_gap, out_cv = _gap_stats(refined_downbeats)
    result["score_after"] = round(out_align, 4)

    in_db_count = max(1, len(raw_downbeats))
    out_db_count = len(refined_downbeats)
    count_ratio = out_db_count / in_db_count

    align_ok = out_align >= (in_align + _PHASE1_ALIGN_MIN_GAIN)
    count_ok = (_PHASE1_COUNT_RATIO[0] <= count_ratio <= _PHASE1_COUNT_RATIO[1])

    # Meter-shift check: refining is harmful if it changes the apparent
    # beats_per_bar (e.g. 4/4 -> 2/4 by doubling downbeats, or 4/4 -> 8/8
    # by halving). chord_splitter trusts downbeats and over-splits, player
    # forces tsBeats dots evenly across short cards = visible 2x/4x speed.
    # When BOTH input and output have estimable bpb and input is in the
    # plausible [3, 5] band, refined bpb must also stay in that band.
    in_bpb = _bpb_from_gaps(beats, raw_downbeats)
    out_bpb = _bpb_from_gaps(refined_beats, refined_downbeats)
    bpb_ok = True
    if in_bpb is not None and out_bpb is not None:
        if _PHASE1_BPB_MIN <= in_bpb <= _PHASE1_BPB_MAX:
            bpb_ok = _PHASE1_BPB_MIN <= out_bpb <= _PHASE1_BPB_MAX

    if not (align_ok and count_ok and bpb_ok) and not force:
        result["reason"] = (
            f"refiner-not-better: in(align={in_align:.2f},cv="
            f"{in_cv if in_cv is not None else float('nan'):.2f},"
            f"n_db={in_db_count},bpb={in_bpb if in_bpb is not None else float('nan'):.2f})"
            f" -> out(align={out_align:.2f},cv="
            f"{out_cv if out_cv is not None else float('nan'):.2f},"
            f"n_db={out_db_count},bpb={out_bpb if out_bpb is not None else float('nan'):.2f})"
        )
        return result

    # ------------------------------------------------------------------
    # Adopt refined output
    # ------------------------------------------------------------------
    result["downbeats_after"] = refined_downbeats
    result["beats_after"] = refined_beats
    result["beat_probs"] = list(refine_out.get("beat_probs") or [])
    result["downbeat_probs"] = list(refine_out.get("downbeat_probs") or [])
    result["phase_offset_sec"] = (
        round(refined_downbeats[0], 4) if refined_downbeats else 0.0
    )
    result["applied"] = True
    result["reason"] = (
        f"phase1-applied: align {in_align:.2f}->{out_align:.2f}, "
        f"cv {in_cv if in_cv is not None else float('nan'):.2f}->"
        f"{out_cv if out_cv is not None else float('nan'):.2f}, "
        f"n_beats {len(beats)}->{len(refined_beats)}, "
        f"n_downbeats {in_db_count}->{out_db_count}, "
        f"refiner_dt={refine_out.get('elapsed_sec', 0):.2f}s"
        + (" forced" if force else "")
    )
    return result


def restore_from_snapshot(chord_data: Dict) -> Tuple[bool, str]:
    """Revert ``chord_data["downbeats"]`` to the original raw beat-tracker
    output snapshotted in ``bar_correction.downbeats_original``.

    Returns (ok, reason). Mutates ``chord_data`` in place.

    The snapshot is preserved (NOT cleared) so user can re-apply later and
    the second restore still returns the same truly-original.
    """
    bar = chord_data.get("bar_correction") or {}
    snap = bar.get("downbeats_original")
    if not isinstance(snap, list):
        return False, "no-snapshot"
    chord_data["downbeats"] = list(snap)
    bar["applied"] = False
    bar["reason"] = "user-restored"
    bar["n_downbeats_after"] = len(snap)
    chord_data["bar_correction"] = bar
    return True, "restored"
