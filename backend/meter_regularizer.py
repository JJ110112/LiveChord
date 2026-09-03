"""Serve-time meter regularization for compound / triple meters (6/8, 3/4).

Problem it fixes (Libera "The Lark's Last Song", a rubato 6/8 choir piece):
beat_this emits eighth-note ticks with jitter (gaps 0.08–1.54 s around a
0.46 s median) and dotted-quarter pulses as downbeats; the compound-meter
detector then takes every other pulse as a bar without knowing the phase, so
bars[] sat half a bar away from the real chord changes, chord cards showed
5/7/8 dots instead of 6, and a 36-beat verse counted 33.

What it does (non-destructive, on the served payload only):
  1. Clean bars[]: drop bars that arrive far too early, insert bars into gaps
     that are ~2× the local bar length (keeps rubato — each bar keeps its own
     length; only outliers are repaired).
  2. Pick the bar phase (shift by k subdivisions) that best aligns bar lines
     with chord onsets. Only switches when clearly better than the current.
  3. Rebuild beats[] as an even subdivision of every bar (6 eighths / 3
     quarters), and downbeats[] as bar lines (+ half-bar pulses for 6/8, the
     convention the rest of the player expects).
  4. Snap chord boundaries to the nearest bar / half-bar line when within one
     subdivision, keeping chord[i].end == chord[i+1].time.

Stamps ``meter_regularizer_meta`` so admin/debug can see what happened.
"""
import statistics
from typing import Dict, List, Optional, Tuple

_SUPPORTED = {"6/8": 6, "3/4": 3, "12/8": 12, "9/8": 9}
_MIN_BARS = 8
_PHASE_MIN_GAIN = 0.10      # new phase must align this much better (fraction of chord onsets)
_SNAP_TOL_UNITS = 1.0       # chord boundary snap tolerance in subdivisions
_MIN_CHORD_UNITS = 1.0      # never shrink a chord below one subdivision


def _median_gap(xs: List[float]) -> Optional[float]:
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b > a]
    return statistics.median(gaps) if gaps else None


def _clean_bars(bars: List[float], expected: float) -> Tuple[List[float], int, int]:
    """Repair outlier gaps. Returns (bars, dropped, inserted)."""
    out: List[float] = []
    dropped = inserted = 0
    for b in bars:
        if not out:
            out.append(b)
            continue
        gap = b - out[-1]
        if gap <= 0.55 * expected:
            dropped += 1          # spurious early bar line
            continue
        ratio = gap / expected
        if ratio >= 1.6:
            n = int(round(ratio))
            if n >= 2:
                step = gap / n
                for k in range(1, n):
                    out.append(out[-1] + step)
                    inserted += 1
        out.append(b)
    return out, dropped, inserted


def _alignment(onsets: List[float], lines: List[float], tol: float) -> float:
    if not onsets or not lines:
        return 0.0
    hit = 0
    j = 0
    for t in sorted(onsets):
        while j + 1 < len(lines) and lines[j + 1] <= t:
            j += 1
        best = min(abs(lines[j] - t), abs(lines[j + 1] - t) if j + 1 < len(lines) else 1e9)
        if best <= tol:
            hit += 1
    return hit / len(onsets)


def _shift_bars(bars: List[float], k: int, subdiv: int) -> List[float]:
    """Shift every bar line by k subdivisions of its own bar length."""
    if k == 0:
        return list(bars)
    out = []
    for i, b in enumerate(bars):
        nxt = bars[i + 1] if i + 1 < len(bars) else (b + (b - bars[i - 1] if i > 0 else 0))
        out.append(b + (nxt - b) * k / subdiv)
    return out


def _subdivide(bars: List[float], subdiv: int, tail_len: float) -> List[float]:
    beats: List[float] = []
    for i, b in enumerate(bars):
        length = (bars[i + 1] - b) if i + 1 < len(bars) else tail_len
        if length <= 0:
            continue
        for j in range(subdiv):
            beats.append(round(b + length * j / subdiv, 4))
    return beats


def _nearest(lines: List[float], t: float) -> float:
    return min(lines, key=lambda x: abs(x - t))


def regularize(chord_data: Dict) -> Dict:
    ts = str(chord_data.get("time_signature") or "").strip()
    subdiv = _SUPPORTED.get(ts)
    meta: Dict = {"applied": False, "time_signature": ts}
    if not subdiv:
        meta["reason"] = "unsupported-meter"
        return meta
    chords = chord_data.get("chords") or []
    raw_beats = [float(b) for b in (chord_data.get("beats") or [])]
    pulses = sorted({float(d) for d in (chord_data.get("downbeats") or []) if d is not None})
    if len(pulses) < _MIN_BARS or len(raw_beats) < 16:
        meta["reason"] = "too-few-pulses"
        return meta
    unit = _median_gap(raw_beats)          # eighth (6/8) or quarter (3/4)
    pulse_gap = _median_gap(pulses)
    if not unit or not pulse_gap or unit <= 0 or pulse_gap <= 0:
        meta["reason"] = "no-gap"
        return meta
    compound = ts in ("6/8", "12/8")
    half = subdiv / 2.0
    ratio = pulse_gap / unit

    # Clean the tracker's pulse list at its own level (drop early extras,
    # fill gaps that are ~2× the local pulse spacing).
    pulses, dropped, inserted = _clean_bars(pulses, pulse_gap)

    if compound:
        # Build a half-bar grid H, then bars = every other H line (parity by
        # chord-onset alignment). Tracker pulses are either half-bars (ratio≈3)
        # or full bars (ratio≈6) — both become H.
        if abs(ratio - subdiv) < abs(ratio - half):
            H = []
            for i, p in enumerate(pulses):
                H.append(p)
                if i + 1 < len(pulses):
                    H.append((p + pulses[i + 1]) / 2)
        else:
            H = list(pulses)
        parity_candidates = [H[k::2] for k in (0, 1)]
    else:
        H = list(pulses)
        parity_candidates = [list(pulses)]

    onsets = [float(c["time"]) for c in chords if c.get("chord") and c["chord"] not in ("N", "X")]
    tol = unit * 0.5
    scored = [(_alignment(onsets, cand, tol), i, cand) for i, cand in enumerate(parity_candidates) if len(cand) >= 2]
    if not scored:
        meta["reason"] = "no-candidates"
        return meta
    scored.sort(key=lambda x: -x[0])
    align_after, parity, bars = scored[0]
    align_before = scored[-1][0] if len(scored) > 1 else align_after
    if compound and len(scored) > 1 and align_after < scored[-1][0] + _PHASE_MIN_GAIN:
        # No clear winner: keep the tracker's own phase (parity 0).
        align_after, parity, bars = next(x for x in scored if x[1] == 0)

    expected = _median_gap(bars) or (unit * subdiv)
    # Extend the grid to cover the chord tail.
    last_end = max([float(c.get("end", c.get("time", 0))) for c in chords] + [bars[-1]])
    while bars[-1] + expected * 0.75 < last_end:
        bars.append(bars[-1] + (bars[-1] - bars[-2] if len(bars) > 1 else expected))

    tail_len = bars[-1] - bars[-2] if len(bars) > 1 else expected
    beats = _subdivide(bars, subdiv, tail_len)
    if compound:
        halves = [round(b + (bars[i + 1] - b) / 2, 4) for i, b in enumerate(bars) if i + 1 < len(bars)]
        downbeats = sorted(bars + halves)
        snap_lines = downbeats
    else:
        downbeats = list(bars)
        snap_lines = beats  # 3/4 chords may change on any quarter

    # Quantize chords to the half-bar grid (6/8) or beat grid (3/4): each
    # cell takes the chord with the largest overlap, then equal neighbours
    # merge. BTC boundaries that drift by less than half a cell disappear;
    # chords shorter than a cell are absorbed by their neighbours.
    cells = snap_lines
    quantized: List[Dict] = []
    ci = 0
    for k in range(len(cells) - 1):
        s0, s1 = cells[k], cells[k + 1]
        best, best_ov = None, 0.0
        while ci > 0 and float(chords[ci].get("time", 0)) > s0:
            ci -= 1
        j = ci
        while j < len(chords) and float(chords[j].get("time", 0)) < s1:
            c = chords[j]
            ov = min(s1, float(c.get("end", c.get("time", 0)))) - max(s0, float(c.get("time", 0)))
            if ov > best_ov:
                best, best_ov = c, ov
            j += 1
        ci = max(0, j - 1)
        if best is None or best_ov <= 0:
            continue
        if quantized and quantized[-1]["chord"] == best.get("chord"):
            quantized[-1]["end"] = round(s1, 3)
        else:
            q = dict(best)
            q["time"], q["end"] = round(s0, 3), round(s1, 3)
            quantized.append(q)
    # Keep the very first onset (an intro pickup may start before the grid).
    if quantized and chords:
        first_t = float(chords[0].get("time", 0))
        if first_t < quantized[0]["time"] and quantized[0]["time"] - first_t < expected:
            quantized[0]["time"] = round(first_t, 3)
    moved = len(chords) - len(quantized)
    new_chords = quantized if len(quantized) >= 2 else [dict(c) for c in chords]

    chord_data["bars"] = [round(b, 4) for b in bars]
    chord_data["beats"] = beats
    chord_data["downbeats"] = [round(d, 4) for d in downbeats]
    chord_data["chords"] = new_chords
    meta.update({
        "applied": True,
        "subdivisions": subdiv,
        "unit_sec": round(unit, 4),
        "pulse_gap_sec": round(pulse_gap, 4),
        "bar_sec": round(expected, 4),
        "pulses_dropped": dropped,
        "pulses_inserted": inserted,
        "parity": parity,
        "align_before": round(align_before, 3),
        "align_after": round(align_after, 3),
        "chords_before": len(chords), "chords_after": len(new_chords),
        "bar_count": len(bars),
    })
    return meta


def maybe_regularize_for_serve(chord_data: Dict) -> Dict:
    """Serve-time hook: regularize compound/triple meters, stamp metadata."""
    try:
        chord_data["meter_regularizer_meta"] = regularize(chord_data)
    except Exception as exc:  # never break serving
        chord_data["meter_regularizer_meta"] = {"applied": False, "reason": f"error: {exc}"}
    return chord_data
