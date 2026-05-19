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

import re
from typing import Iterable, List, Dict, Tuple


# Below this duration, a chord is candidate for noise absorption.
# 1.2 × secs-per-beat = up to ~1.2 beats. BTC quantization can stretch a
# 1-beat noise by ~0.02s past the spb boundary; 1.0 was too tight and
# missed user-reported cases on Bet You Wanna (F at 29.86-30.42 = 0.56s
# vs spb 0.54s). 1.2 catches the noise, still well below typical 2-beat
# real passing chords (which also tend to start ON a downbeat anyway).
_NOISE_DUR_BEATS = 1.2

# Same-root ornament repairs target cases like Bb(1) -> Bbsus4(3) inside
# one musical bar. They differ from real passing chords because the root is
# unchanged and one side is an explicit suspension/add-tone spelling.
_ORNAMENT_MIN_TOTAL_BEATS = 3.15
_ORNAMENT_MAX_TOTAL_BEATS = 4.85
_ORNAMENT_SHORT_BEATS = 1.25
_ORNAMENT_RE = re.compile(r"(sus|add)", re.IGNORECASE)

# Tolerance for "is this time near a downbeat" check.
_DB_TOL_SEC = 0.10

# Tolerance for "are these two chords adjacent" check.
_ADJ_TOL_SEC = 0.05

# Isolated-short-chord filter constants. Two paths: P1 (same-root sandwich,
# lenient duration) and P2 (different-neighbor extreme-short). See
# C:\Users\hitea\.claude\plans\calm-juggling-octopus.md for the rule derivation
# from the 愛我久一點 golden song.
_ISOLATED_P1_ABS_DUR_SEC = 0.70
_ISOLATED_P1_DUR_BEATS = 1.10
_ISOLATED_P2_ABS_DUR_SEC = 0.40
_ISOLATED_P2_DUR_BEATS = 0.60
_ISOLATED_P2_REL_RATIO = 0.20
_ISOLATED_RULE_VERSION = 1


def _is_near(t: float, points: List[float], tol: float) -> bool:
    for p in points:
        if abs(p - t) <= tol:
            return True
    return False


def _root_quality(chord: str) -> Tuple[str, str]:
    if not chord:
        return "", ""
    m = re.match(r"^([A-G](?:#|b)?)(.*)$", str(chord))
    if not m:
        return "", str(chord)
    return m.group(1), m.group(2) or ""


def _is_ornament_quality(quality: str) -> bool:
    return bool(_ORNAMENT_RE.search(quality or ""))


def _choose_ornament_merge_name(left: Dict, right: Dict) -> str:
    left_name = str(left.get("chord") or "")
    right_name = str(right.get("chord") or "")
    _, left_q = _root_quality(left_name)
    _, right_q = _root_quality(right_name)
    left_orn = _is_ornament_quality(left_q)
    right_orn = _is_ornament_quality(right_q)
    if left_orn != right_orn:
        return right_name if left_orn else left_name
    left_dur = float(left.get("end", left.get("time", 0.0))) - float(left.get("time", 0.0))
    right_dur = float(right.get("end", right.get("time", 0.0))) - float(right.get("time", 0.0))
    return right_name if right_dur > left_dur else left_name


def merge_same_root_ornaments(chords: List[Dict], bpm: float) -> List[Dict]:
    """Merge short same-root sus/add fragments within one nominal bar."""
    if not chords or len(chords) < 2 or bpm <= 0:
        return list(chords)
    spb = 60.0 / bpm
    if spb <= 0:
        return list(chords)

    out: List[Dict] = []
    i = 0
    while i < len(chords):
        if i + 1 >= len(chords):
            out.append(dict(chords[i]))
            break

        left = dict(chords[i])
        right = dict(chords[i + 1])
        if left.get("global_arbiter") or right.get("global_arbiter"):
            out.append(left)
            i += 1
            continue
        try:
            left_start = float(left["time"])
            left_end = float(left["end"])
            right_start = float(right["time"])
            right_end = float(right["end"])
        except (KeyError, TypeError, ValueError):
            out.append(left)
            i += 1
            continue

        left_root, left_q = _root_quality(str(left.get("chord") or ""))
        right_root, right_q = _root_quality(str(right.get("chord") or ""))
        left_beats = (left_end - left_start) / spb
        right_beats = (right_end - right_start) / spb
        total_beats = (right_end - left_start) / spb
        can_merge = (
            left_root
            and left_root == right_root
            and left.get("chord") != right.get("chord")
            and abs(left_end - right_start) <= _ADJ_TOL_SEC
            and _ORNAMENT_MIN_TOTAL_BEATS <= total_beats <= _ORNAMENT_MAX_TOTAL_BEATS
            and min(left_beats, right_beats) <= _ORNAMENT_SHORT_BEATS
            and (_is_ornament_quality(left_q) or _is_ornament_quality(right_q))
        )
        if can_merge:
            merged = dict(left)
            merged["end"] = right_end
            merged["chord"] = _choose_ornament_merge_name(left, right)
            out.append(merged)
            i += 2
            continue

        out.append(left)
        i += 1
    return out


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
        if c.get("global_arbiter") or prev.get("global_arbiter"):
            continue
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
    return merge_same_root_ornaments(out, bpm)


def filter_isolated_short_chords(
    chords: List[Dict],
    downbeats: Iterable[float],
    bpm: float,
) -> Tuple[List[Dict], Dict]:
    """Drop isolated short chord events sandwiched between same-root neighbors
    or extreme-short between different-root neighbors. Beat-aware.

    Returns (new_chords, meta). When the filter cannot run safely (missing
    beat data, fewer than 3 chords) returns the input unchanged with
    ``meta = {applied: False, reason: ...}``.

    See ``calm-juggling-octopus.md`` plan for the rule derivation. Two paths:

      Path 1 — same-root sandwich, lenient absolute duration
        Fires on ``Gmaj7 — D(0.6s) — Gmaj7``, ``F#m7 — F#sus2(0.3s) — F#m7``
      Path 2 — different-neighbor extreme-short with 5x duration gap
        Fires on ``Gmaj7(3.96s) — D(0.33s) — Gbm7(2.17s)``

    Both gated by adjacency + start-off-downbeat. Path 2 additionally requires
    end-off-downbeat to disambiguate from the noise-tail pattern handled by
    ``filter_noise_tails``.
    """
    if not bpm or bpm <= 0 or not chords or len(chords) < 3:
        return list(chords), {"applied": False, "reason": "no_beat_data"}
    db_list = sorted(float(d) for d in (downbeats or []) if d is not None)
    if not db_list:
        return list(chords), {"applied": False, "reason": "no_beat_data"}

    spb = 60.0 / bpm

    def is_near_db(t: float) -> bool:
        return _is_near(t, db_list, _DB_TOL_SEC)

    new_chords: List[Dict] = [dict(chords[0])]
    removed_log: List[Dict] = []

    i = 1
    while i < len(chords) - 1:
        prev = new_chords[-1]            # mutated prev sees Path-2 extensions
        curr = chords[i]
        nxt = chords[i + 1]

        # global_arbiter splits are pre-baked downbeat-quantize artifacts —
        # never touch them (matches filter_noise_tails policy).
        if curr.get("global_arbiter") or prev.get("global_arbiter") or nxt.get("global_arbiter"):
            new_chords.append(dict(curr))
            i += 1
            continue

        try:
            curr_time = float(curr["time"])
            curr_end = float(curr["end"])
            prev_end = float(prev["end"])
            nxt_time = float(nxt["time"])
            nxt_end = float(nxt["end"])
            prev_start = float(prev["time"])
        except (KeyError, TypeError, ValueError):
            new_chords.append(dict(curr))
            i += 1
            continue

        dur = curr_end - curr_time
        prev_dur = prev_end - prev_start
        nxt_dur = nxt_end - nxt_time

        # Gate A — must hold for either path
        is_adjacent = (abs(prev_end - curr_time) <= _ADJ_TOL_SEC
                       and abs(curr_end - nxt_time) <= _ADJ_TOL_SEC)
        is_short_a = 0 < dur <= min(_ISOLATED_P1_ABS_DUR_SEC, _ISOLATED_P1_DUR_BEATS * spb)
        start_off_db = not is_near_db(curr_time)
        if not (is_adjacent and is_short_a and start_off_db):
            new_chords.append(dict(curr))
            i += 1
            continue

        prev_root, _ = _root_quality(str(prev.get("chord") or ""))
        nxt_root, _ = _root_quality(str(nxt.get("chord") or ""))
        triggered = None
        rel_ratio = None

        if prev_root and prev_root == nxt_root:
            triggered = "P1"
        else:
            is_short_p2 = dur <= min(_ISOLATED_P2_ABS_DUR_SEC, _ISOLATED_P2_DUR_BEATS * spb)
            denom = min(prev_dur, nxt_dur)
            is_rel_short = denom > 0 and dur < _ISOLATED_P2_REL_RATIO * denom
            end_off_db = not is_near_db(curr_end)
            if is_short_p2 and is_rel_short and end_off_db:
                triggered = "P2"
                rel_ratio = round(dur / denom, 3)

        if triggered:
            removed_log.append({
                "time": round(curr_time, 3),
                "end": round(curr_end, 3),
                "chord": str(curr.get("chord") or ""),
                "prev_chord": str(prev.get("chord") or ""),
                "prev_end_original": round(prev_end, 3),
                "next_chord": str(nxt.get("chord") or ""),
                "next_time": round(nxt_time, 3),
                "path": triggered,
                "dur_sec": round(dur, 3),
                "rel_short_ratio": rel_ratio,
                "spb": round(spb, 3),
            })
            prev["end"] = nxt_time        # in-place mutation of new_chords[-1]
            i += 1                         # drop curr; nxt becomes the next curr
        else:
            new_chords.append(dict(curr))
            i += 1

    new_chords.append(dict(chords[-1]))

    # Idempotent same-name collapse (handles P1 -> identical neighbors)
    final: List[Dict] = []
    for ch in new_chords:
        if (final and final[-1].get("chord") == ch.get("chord")
                and abs(float(final[-1]["end"]) - float(ch["time"])) <= _ADJ_TOL_SEC):
            final[-1]["end"] = ch["end"]
        else:
            final.append(dict(ch))

    meta = {
        "applied": len(removed_log) > 0,
        "removed_count": len(removed_log),
        "rule_version": _ISOLATED_RULE_VERSION,
        "params": {
            "p1_abs_dur_sec": _ISOLATED_P1_ABS_DUR_SEC,
            "p1_dur_beats": _ISOLATED_P1_DUR_BEATS,
            "p2_abs_dur_sec": _ISOLATED_P2_ABS_DUR_SEC,
            "p2_dur_beats": _ISOLATED_P2_DUR_BEATS,
            "p2_rel_ratio": _ISOLATED_P2_REL_RATIO,
            "db_tol_sec": _DB_TOL_SEC,
            "adj_tol_sec": _ADJ_TOL_SEC,
        },
        "removed": removed_log,
    }
    if not removed_log:
        meta["reason"] = "no_noise_found"
    return final, meta


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
    explicit_meter = chord_data.get("meter_correction") or {}

    if explicit_meter.get("applied") and chord_data.get("time_signature"):
        chord_data["noise_filter_meta"] = {
            "applied": False, "reason": "explicit-meter-card-grid",
            "before": len(chords), "after": len(chords), "absorbed": 0,
        }
        return chord_data

    if not chords or bpm <= 0 or len(downbeats) < 2:
        chord_data["noise_filter_meta"] = {
            "applied": False, "reason": "insufficient-data",
            "before": len(chords), "after": len(chords), "absorbed": 0,
        }
        return chord_data

    before_n = len(chords)
    new_chords = filter_noise_tails(chords, downbeats, bpm)
    absorbed_tail = before_n - len(new_chords)

    # Chain isolated-short filter after tail filter. Non-destructive — operates
    # on the same in-memory list. The on-disk JSON is unchanged; the ingest
    # pipeline writes its own meta when applied at process_queue time.
    new_chords, iso_meta = filter_isolated_short_chords(new_chords, downbeats, bpm)
    absorbed_iso = iso_meta.get("removed_count", 0)

    absorbed = absorbed_tail + absorbed_iso
    chord_data["chords"] = new_chords
    chord_data["noise_filter_meta"] = {
        "applied": absorbed > 0,
        "reason": "ok" if absorbed > 0 else "no-noise-found",
        "before": before_n,
        "after": len(new_chords),
        "absorbed": absorbed,
        "tail_absorbed": absorbed_tail,
        "isolated_absorbed": absorbed_iso,
    }
    if iso_meta.get("applied"):
        chord_data["isolated_chord_filter_serve"] = iso_meta
    return chord_data
