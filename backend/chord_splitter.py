"""Serve-time chord splitter — split chords spanning bar boundaries.

Long chords (8/12/16 beats in 4/4) overflow the beat-dot row in the player UI
and make the per-beat dot animation visually inconsistent. Rather than asking
the user to run the manual Auto Split tool, we split at every interior
downbeat just before sending chord JSON to the player.

Non-destructive: this runs on the response payload, never on the stored file.
The original chord array (incl. user manual edits) stays in storage; the
serve-time copy is what the player consumes.
"""

import statistics
from typing import Iterable, List, Dict, Optional


# How close to a chord boundary a downbeat must be to count as "the same point"
# (i.e. NOT an interior split). Chord and downbeat times come from independent
# detectors snapped to the same beat grid, so they can drift a few ms.
_BOUNDARY_EPSILON_SEC = 0.05

# Reject any split that would produce a segment shorter than this fraction of
# the song's median bar length. Without this, a chord that ends ~0.2s past a
# downbeat (common at chord-change boundaries) gets split into "1 bar + tiny
# sliver" — visually worse than the un-split overflow we're trying to fix.
# Also collapses splits caused by duplicate/near-duplicate downbeat entries.
#
# 0.20 (was 0.40) lets pre-emptive pickup chords (~0.9 beat = 0.4-0.5s in pop
# at 110-130 BPM) survive as their own segment, instead of being absorbed back
# into a 1.25-bar 5-dot card with the downbeat on dot 2. Verified on
# "Give Your Heart a Break" (125 BPM): 0:23 B card was 5 dots, after this
# becomes 1-dot pickup + 4-dot bar with downbeat on dot 1. Sub-beat artifacts
# (e.g. 0.05s ghosts at chord boundaries) are still well below the new
# 0.20×bar = ~0.4s threshold and get dropped.
_MIN_SEG_BAR_FRAC = 0.20

# Trigger virtual-downbeat interpolation when the gap between two consecutive
# split boundaries (incl. chord start/end) exceeds this multiple of the median
# bar. beat_this/madmom occasionally miss 1-2 downbeats inside long held
# chords, leaving a 5-7s "bar" — without interpolation those would render as
# one giant chord card with overflowing dots, which is the bug we're fixing.
_GAP_INTERPOLATION_THRESHOLD = 1.5


def _is_confident(chord_data: Dict) -> bool:
    """Decide whether the song's downbeats are trustworthy enough to split on.

    Splitting on bad downbeats would chop chords mid-bar and make things worse
    than the overflow it's trying to fix, so we gate on:
      - downbeats list has >=2 entries (need at least one bar interval)
      - beats_source is one of the high-quality detectors, OR bar_arbitrator
        ran and applied a correction (= it vetted the grid)
      - median(downbeat_gap) implies a plausible beats_per_bar (3 to 5) given
        chord_data["bpm"]. beat_refiner sometimes over-densifies downbeats
        (e.g. emits one per 2 beats instead of one per 4), which would make
        the splitter chop bars in half. See LiveChord-3kh.
    """
    downbeats = chord_data.get("downbeats") or []
    if len(downbeats) < 2:
        return False
    source = (chord_data.get("beats_source") or "").lower()
    source_ok = source in {"madmom", "beat_this", "beat-this"}
    bar_correction = chord_data.get("bar_correction") or {}
    if not (source_ok or bar_correction.get("applied")):
        return False

    # Bar/BPM sanity gate. Compute median gap; expected beats per bar
    # = median_gap / (60/bpm). If outside [3, 5], the downbeats are at
    # the wrong granularity and we should NOT split (player rendering
    # would show 2x/4x apparent dot speed).
    bar_gap = _median_bar_gap([float(d) for d in downbeats])
    bpm = float(chord_data.get("bpm") or 0)
    if bar_gap and bpm > 0:
        spb = 60.0 / bpm
        if spb > 0:
            bpb = bar_gap / spb
            if bpb < 3.0 or bpb > 5.0:
                return False
    return True


def _interior_downbeats(start: float, end: float, downbeats: List[float]) -> List[float]:
    """Return downbeats strictly inside (start, end), excluding boundary ties.

    Uses _BOUNDARY_EPSILON_SEC so a downbeat that is "at" the chord start/end
    (within rounding) is treated as a boundary, not an interior split point.
    """
    return [
        d for d in downbeats
        if (d - start) > _BOUNDARY_EPSILON_SEC
        and (end - d) > _BOUNDARY_EPSILON_SEC
    ]


def _median_bar_gap(downbeats: List[float]) -> Optional[float]:
    """Median time between consecutive downbeats — the song's "bar length".

    Returned in seconds, or None if there aren't enough downbeats. We use the
    median (not mean) so a single duplicate/dropped downbeat doesn't skew the
    threshold.
    """
    if len(downbeats) < 2:
        return None
    gaps = [downbeats[i + 1] - downbeats[i] for i in range(len(downbeats) - 1)]
    gaps = [g for g in gaps if g > 0.1]  # drop duplicate-downbeat artifacts
    if not gaps:
        return None
    return statistics.median(gaps)


def _interpolate_oversized_gaps(boundaries: List[float], bar_gap: float) -> List[float]:
    """Insert evenly-spaced virtual boundaries inside any gap that's
    significantly larger than the median bar — fills in for downbeats the
    beat tracker missed, so a 6.6s "bar" gets split into 2-3 normal bars.

    Each oversized gap is divided into ``round(gap / bar_gap)`` equal parts.
    No-op if bar_gap is unknown or no gap exceeds the threshold.
    """
    if bar_gap <= 0:
        return boundaries
    threshold = bar_gap * _GAP_INTERPOLATION_THRESHOLD
    out = [boundaries[0]]
    for i in range(len(boundaries) - 1):
        a, b = boundaries[i], boundaries[i + 1]
        gap = b - a
        if gap > threshold:
            n_bars = max(2, round(gap / bar_gap))
            step = gap / n_bars
            for k in range(1, n_bars):
                out.append(a + k * step)
        out.append(b)
    return out


def _drop_small_segment_boundaries(boundaries: List[float], min_seg: float) -> List[float]:
    """Iteratively remove the inner boundary that creates the smallest segment
    until every segment is >= min_seg (or only the start/end remain).

    Handles the two real-world failure modes:
      - chord ends a fraction of a second past a downbeat → tail sliver
      - duplicate downbeat entries (e.g. 103.22 and 103.30) → micro segment
    """
    while len(boundaries) > 2:
        seg_lens = [boundaries[i + 1] - boundaries[i] for i in range(len(boundaries) - 1)]
        min_idx = min(range(len(seg_lens)), key=lambda i: seg_lens[i])
        if seg_lens[min_idx] >= min_seg:
            break
        # Decide which interior boundary to drop to merge the smallest segment
        # into a neighbour. Edge segments only have one inner boundary to drop;
        # middle segments pick the side whose neighbour is shorter (= the merge
        # that distorts grid alignment the least).
        if min_idx == 0:
            boundaries.pop(1)
        elif min_idx == len(seg_lens) - 1:
            boundaries.pop(-2)
        else:
            prev_len = seg_lens[min_idx - 1]
            next_len = seg_lens[min_idx + 1]
            if prev_len <= next_len:
                boundaries.pop(min_idx)
            else:
                boundaries.pop(min_idx + 1)
    return boundaries


def split_chords_at_bars(
    chords: List[Dict],
    downbeats: Iterable[float],
) -> List[Dict]:
    """Return a new chord list where chords spanning interior downbeats are
    split into bar-aligned segments of the same chord name.

    Each emitted segment carries the original chord's fields, with ``time`` /
    ``end`` overwritten to the bar boundaries and ``auto_split: True`` added on
    the splits (the original-shaped first/last segments also carry it so the
    frontend can identify all auto-emitted segments uniformly).

    Chords without ``end`` (last chord, or malformed) pass through unchanged —
    we can't bar-split a chord whose duration we don't know.

    Segments shorter than ``_MIN_SEG_BAR_FRAC`` of the song's median bar are
    rejected: better to leave a chord as one slightly-long card than to render
    a half-second sliver beside it.
    """
    db_sorted = sorted(float(d) for d in downbeats if d is not None)
    bar_gap = _median_bar_gap(db_sorted)
    min_seg = (bar_gap * _MIN_SEG_BAR_FRAC) if bar_gap else 0.0
    out: List[Dict] = []

    for chord in chords:
        start = chord.get("time")
        end = chord.get("end")
        if start is None or end is None or end <= start:
            out.append(chord)
            continue

        interior = _interior_downbeats(float(start), float(end), db_sorted)
        boundaries = [float(start)] + interior + [float(end)]

        # If no interior downbeats AND no song-wide bar reference, we can't
        # know how to split — pass through. This is the only true bail-out
        # case; everything else is handled by interpolation + min-seg below.
        if not interior and not bar_gap:
            out.append(chord)
            continue

        if bar_gap:
            # Step 1: fill in for missed/absent downbeats so a long held chord
            # (incl. song-end chords where the tracker stops emitting downbeats)
            # gets divided into bar-sized segments.
            boundaries = _interpolate_oversized_gaps(boundaries, bar_gap)
        if min_seg > 0:
            # Step 2: clean up tail slivers and duplicate-downbeat artifacts.
            boundaries = _drop_small_segment_boundaries(boundaries, min_seg)

        # If we collapsed back to just [start, end], no actual split happened
        if len(boundaries) <= 2:
            out.append(chord)
            continue

        for i in range(len(boundaries) - 1):
            seg = dict(chord)
            seg["time"] = boundaries[i]
            seg["end"] = boundaries[i + 1]
            seg["auto_split"] = True
            out.append(seg)

    return out


def maybe_split_for_serve(chord_data: Dict) -> Dict:
    """Apply splitter to ``chord_data["chords"]`` if confidence gate passes.

    Returns the same dict with the chord list possibly replaced. Adds an
    ``auto_split_meta`` key with {applied, reason, before, after} so the UI
    or admin can see what happened. Always non-destructive — caller may write
    the result to the response without affecting on-disk data.
    """
    chords = chord_data.get("chords") or []
    if not chords:
        chord_data["auto_split_meta"] = {
            "applied": False, "reason": "no-chords", "before": 0, "after": 0,
        }
        return chord_data

    if not _is_confident(chord_data):
        # Distinguish bpb-rejection from generic low-confidence so the admin
        # / debug UI can tell whether the song was actually evaluated.
        downbeats = chord_data.get("downbeats") or []
        bpm = float(chord_data.get("bpm") or 0)
        reason = "low-confidence-downbeats"
        if len(downbeats) >= 2 and bpm > 0:
            bar_gap = _median_bar_gap([float(d) for d in downbeats])
            if bar_gap:
                bpb = bar_gap / (60.0 / bpm)
                if bpb < 3.0 or bpb > 5.0:
                    reason = f"implausible-bpb={bpb:.2f}"
        chord_data["auto_split_meta"] = {
            "applied": False,
            "reason": reason,
            "before": len(chords),
            "after": len(chords),
        }
        return chord_data

    downbeats = chord_data.get("downbeats") or []
    new_chords = split_chords_at_bars(chords, downbeats)
    chord_data["chords"] = new_chords
    chord_data["auto_split_meta"] = {
        "applied": True,
        "reason": "ok",
        "before": len(chords),
        "after": len(new_chords),
    }
    return chord_data
