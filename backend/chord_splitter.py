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

try:
    from bar_phase_corrector import fragmentation_risk
except ImportError:
    try:
        from backend.bar_phase_corrector import fragmentation_risk
    except ImportError:
        def fragmentation_risk(chords, downbeats, bpm, bpb=4):
            return {"penalty": 0.0, "bad_fragments": 0, "patterns": {}, "examples": []}


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
_FRAGMENT_GUARD_PENALTY = 0.18
_SAME_CHORD_FRAGMENT_BEATS = 1.25
_SAME_CHORD_ONE_BAR_EDGE_BEATS = 2.35
_SAME_CHORD_MERGE_TOTAL_BEATS = (3.3, 5.5)


def _bpb_class(downbeats: List[float], bpm: float) -> float:
    """Median(downbeat_gap) / seconds_per_beat = beats per bar implied by the
    downbeats grid. 0 when not computable."""
    bar_gap = _median_bar_gap(downbeats)
    if not bar_gap or bpm <= 0:
        return 0.0
    spb = 60.0 / bpm
    if spb <= 0:
        return 0.0
    return bar_gap / spb


def _resolve_split_downbeats(chord_data: Dict) -> Optional[List[float]]:
    """Decide which downbeats to split on, or None to skip splitting.

    Splitting on bad downbeats would chop chords mid-bar and make things worse
    than the overflow it's trying to fix, so we gate on:
      - downbeats list has >=2 entries (need at least one bar interval)
      - beats_source is one of the high-quality detectors, OR bar_arbitrator
        ran and applied a correction (= it vetted the grid)
      - median(downbeat_gap) implies a plausible beats_per_bar (3 to 5) given
        chord_data["bpm"]. beat_refiner sometimes over-densifies downbeats
        (e.g. emits one per 2 beats instead of one per 4), which would make
        the splitter chop bars in half. See LiveChord-3kh.

    "Doubled-downbeats" fallback (LiveChord-11w): when the raw grid implies
    bpb≈2 (a half-bar emission, common for slow ballads off beat_this), but
    using every other downbeat lands in [3,5], we use the halved grid. The
    song really IS 4/4; the tracker just emitted twice the rate. Without
    this, every slow pop ballad bypasses auto-split.
    """
    raw = chord_data.get("downbeats") or []
    if len(raw) < 2:
        return None
    source = (chord_data.get("beats_source") or "").lower()
    # Prefix check rather than exact-set match so we accept future variants
    # without re-editing this gate. We were bitten once when ingest emitted
    # "beat_this-modal" to disambiguate the Modal-dispatched path; the exact
    # whitelist rejected it and the splitter silently no-op'd on every public
    # song until the offending suffix was traced back here. Also covers older
    # JSONs on disk that may still have the suffixed name.
    source_ok = (
        source.startswith("beat_this")
        or source.startswith("beat-this")
        or source == "madmom"
    )
    bar_correction = chord_data.get("bar_correction") or {}
    if not (source_ok or bar_correction.get("applied")):
        return None

    bpm = float(chord_data.get("bpm") or 0)
    db_floats = [float(d) for d in raw]

    # When BPM (or bar_gap) is unavailable we skip the bpb sanity check and
    # trust the source — that's what the old gate did. Tests rely on it
    # (downbeats grid + recognized source + no bpm → confident).
    bpb = _bpb_class(db_floats, bpm)
    if bpb == 0.0:
        return db_floats

    # Primary: raw downbeats imply plausible bpb. Window stretches down
    # to 2.7 (instead of a hard 3.0) so genuine 3/4 songs whose median
    # downbeat gap lands at 2.93-2.99 due to a couple of dropped/extra
    # downbeats among 100+ entries don't get rejected by 0.05 of fuzz.
    # Upper stays at 5.0 to keep doubled-density (≥6) out — the halved
    # fallback below handles those.
    if 2.7 <= bpb <= 5.0:
        return db_floats

    # Halved-downbeats fallback: if the raw grid is at half-bar density
    # (bpb ≈ 2), using every other downbeat doubles the bar gap and may
    # land us back in the plausible window. Window [1.5, 2.3] catches
    # both the ballad case (bpb≈2.12) and the slow 4/4 with denser
    # half-bar emissions; doubled lands at [3.0, 4.6], inside the
    # primary window.
    if 1.5 <= bpb <= 2.3:
        halved = db_floats[::2]
        bpb_h = _bpb_class(halved, bpm)
        if 2.7 <= bpb_h <= 5.0:
            return halved

    return None


def _is_confident(chord_data: Dict) -> bool:
    """Backwards-compat alias — returns True when downbeats can be resolved."""
    return _resolve_split_downbeats(chord_data) is not None


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


def _drop_fragment_boundaries(boundaries: List[float], bar_gap: float) -> List[float]:
    """Drop interior boundaries that would create obvious 1+3/3+1/4+1 cards.

    This is the local last line of defense. Phase arbitration handles repeated
    bad fragments across a song; this catches isolated boundary jitter where a
    single near-one-bar chord would otherwise become a full bar plus a tiny
    same-chord tail.
    """
    if bar_gap <= 0 or len(boundaries) <= 2:
        return boundaries
    total_bars = (boundaries[-1] - boundaries[0]) / bar_gap
    if not (0.85 <= total_bars <= 1.45):
        return boundaries

    out = list(boundaries)
    changed = True
    while changed and len(out) > 2:
        changed = False
        segs = [out[i + 1] - out[i] for i in range(len(out) - 1)]
        for idx, dur in enumerate(segs):
            dur_bars = dur / bar_gap
            if dur_bars > 0.32:
                continue
            # Edge tiny segments are the visible 1+3 / 3+1 / 4+1 failure.
            if idx == 0:
                out.pop(1)
                changed = True
                break
            if idx == len(segs) - 1:
                out.pop(-2)
                changed = True
                break
    return out


def merge_same_chord_fragments(chords: List[Dict], bpm: float) -> tuple[List[Dict], Dict]:
    """Merge adjacent same-chord 1+3 / 3+1 / 4+1 fragments.

    This repairs persisted legacy data where the chord list itself already
    contains split fragments (possibly with stale ``auto_split`` flags), so
    merely preventing new serve-time splits is not enough.
    """
    meta = {"applied": False, "merged": 0, "patterns": {}, "examples": []}
    if not chords or len(chords) < 2 or bpm <= 0:
        return list(chords), meta
    spb = 60.0 / bpm
    if spb <= 0:
        return list(chords), meta

    def segment_beats(items: List[Dict]) -> List[float]:
        return [
            (float(seg.get("end", seg.get("time", 0.0))) - float(seg.get("time", 0.0))) / spb
            for seg in items
        ]

    def pattern_name(seg_beats: List[float]) -> str:
        left = round(seg_beats[0]) if seg_beats else 0
        right = round(sum(seg_beats[1:])) if len(seg_beats) > 1 else 0
        if left <= 1:
            return f"1+{max(1, right)}"
        if right <= 1:
            return f"{max(1, left)}+1"
        return "same-chord-fragment"

    def record_merge(merged: Dict, items: List[Dict], seg_beats: List[float]) -> None:
        start = float(items[0].get("time", 0.0))
        end = float(items[-1].get("end", items[-1].get("time", start)))
        pat = pattern_name(seg_beats)
        meta["applied"] = True
        meta["merged"] += len(items) - 1
        meta["patterns"][pat] = meta["patterns"].get(pat, 0) + 1
        if len(meta["examples"]) < 5:
            meta["examples"].append({
                "time": round(start, 3),
                "end": round(end, 3),
                "chord": merged.get("chord"),
                "pattern": pat,
                "segments": [round(v, 2) for v in seg_beats],
            })

    def one_bar_fragment_window(run: List[Dict], start_idx: int, size: int) -> bool:
        if start_idx + size > len(run):
            return False
        items = run[start_idx:start_idx + size]
        start = float(items[0].get("time", 0.0))
        end = float(items[-1].get("end", items[-1].get("time", start)))
        total_beats = (end - start) / spb
        if not (_SAME_CHORD_MERGE_TOTAL_BEATS[0] <= total_beats <= _SAME_CHORD_MERGE_TOTAL_BEATS[1]):
            return False
        segs = segment_beats(items)
        if any(v <= 0 for v in segs):
            return False
        if all(3.35 <= v <= 4.65 for v in segs):
            return False
        return min(segs) <= _SAME_CHORD_ONE_BAR_EDGE_BEATS

    def merge_items(items: List[Dict]) -> Dict:
        merged = dict(items[0])
        merged["end"] = items[-1].get("end", merged.get("end"))
        merged.pop("auto_split", None)
        return merged

    out: List[Dict] = []
    i = 0
    while i < len(chords):
        run = [dict(chords[i])]
        j = i + 1
        while j < len(chords) and chords[j].get("chord") == chords[i].get("chord"):
            prev_end = run[-1].get("end")
            cur_time = chords[j].get("time")
            if prev_end is None or cur_time is None or abs(float(prev_end) - float(cur_time)) > 0.08:
                break
            run.append(dict(chords[j]))
            j += 1

        if len(run) > 1:
            start = float(run[0].get("time", 0.0))
            end = float(run[-1].get("end", run[-1].get("time", start)))
            total_beats = (end - start) / spb
            seg_beats = segment_beats(run)
            min_seg = min(seg_beats) if seg_beats else 99.0
            has_stale_auto_split = any(seg.get("auto_split") for seg in run)
            should_merge = (
                has_stale_auto_split
                or (
                    _SAME_CHORD_MERGE_TOTAL_BEATS[0] <= total_beats <= _SAME_CHORD_MERGE_TOTAL_BEATS[1]
                    and min_seg <= _SAME_CHORD_FRAGMENT_BEATS
                )
            )
            if should_merge:
                merged = merge_items(run)
                out.append(merged)
                record_merge(merged, run, seg_beats)
                i = j
                continue
            merged_run: List[Dict] = []
            k = 0
            run_changed = False
            while k < len(run):
                # Prefer the shortest valid one-bar repair so a true preceding
                # same-chord bar can remain separate while its 3+1 tail merges.
                size = 2 if one_bar_fragment_window(run, k, 2) else 0
                if not size and one_bar_fragment_window(run, k, 3):
                    size = 3
                if size:
                    items = run[k:k + size]
                    merged = merge_items(items)
                    merged_run.append(merged)
                    record_merge(merged, items, segment_beats(items))
                    run_changed = True
                    k += size
                    continue
                merged_run.append(run[k])
                k += 1
            if run_changed:
                out.extend(merged_run)
                i = j
                continue

        out.extend(run)
        i = j
    return out, meta


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
            # Step 1b: remove local 1+3/3+1/4+1 artifacts from shifted or
            # jittered downbeats before the generic small-segment cleanup.
            boundaries = _drop_fragment_boundaries(boundaries, bar_gap)
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
    bpm = float(chord_data.get("bpm") or 0)
    if chords and bpm > 0:
        merged_chords, merge_meta = merge_same_chord_fragments(chords, bpm)
        chord_data["chords"] = merged_chords
        chord_data["same_chord_fragment_meta"] = merge_meta
        chords = merged_chords

    if not chords:
        chord_data["auto_split_meta"] = {
            "applied": False, "reason": "no-chords", "before": 0, "after": 0,
        }
        return chord_data

    resolved = _resolve_split_downbeats(chord_data)
    if resolved is None:
        # Distinguish bpb-rejection from generic low-confidence so the admin
        # / debug UI can tell whether the song was actually evaluated.
        downbeats = chord_data.get("downbeats") or []
        bpm = float(chord_data.get("bpm") or 0)
        reason = "low-confidence-downbeats"
        if len(downbeats) >= 2 and bpm > 0:
            bpb = _bpb_class([float(d) for d in downbeats], bpm)
            if bpb and (bpb < 2.7 or bpb > 5.0):
                reason = f"implausible-bpb={bpb:.2f}"
        chord_data["auto_split_meta"] = {
            "applied": False,
            "reason": reason,
            "before": len(chords),
            "after": len(chords),
        }
        return chord_data

    raw_downbeats = chord_data.get("downbeats") or []
    halved = len(resolved) != len(raw_downbeats)
    bpb = round(_bpb_class(resolved, bpm)) if bpm > 0 else 4
    frag = fragmentation_risk(chords, resolved, bpm, bpb)
    merge_meta = chord_data.get("same_chord_fragment_meta") or {}
    stale_merge_risk = merge_meta.get("applied") and frag.get("bad_fragments", 0) >= 1
    repeated_fragment_risk = frag.get("bad_fragments", 0) >= 3 and float(frag.get("penalty", 0.0)) >= 0.08
    if (float(frag.get("penalty", 0.0)) >= _FRAGMENT_GUARD_PENALTY
            or stale_merge_risk
            or repeated_fragment_risk):
        reason = "fragment-guard"
        if stale_merge_risk:
            reason = "fragment-guard-after-stale-merge"
        chord_data["auto_split_meta"] = {
            "applied": False,
            "reason": reason,
            "before": len(chords),
            "after": len(chords),
            "fragment_guard": {
                "skipped": frag.get("bad_fragments", 0),
                "penalty": frag.get("penalty", 0.0),
                "patterns": frag.get("patterns", {}),
                "examples": frag.get("examples", []),
            },
        }
        return chord_data

    new_chords = split_chords_at_bars(chords, resolved)
    chord_data["chords"] = new_chords
    chord_data["auto_split_meta"] = {
        "applied": True,
        "reason": "ok-halved-downbeats" if halved else "ok",
        "before": len(chords),
        "after": len(new_chords),
        "fragment_guard": {
            "skipped": 0,
            "penalty": frag.get("penalty", 0.0),
            "patterns": frag.get("patterns", {}),
        },
    }
    return chord_data
