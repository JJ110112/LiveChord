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
_BAR_PREF_MIN_RATIO = 0.70  # 整小節優先: share of chord changes on bar lines that marks a bar-aligned song
_BAR_PREF_MIN_LONG_SHARE = 0.75  # …or share of time spent in chords ≥ 1 bar long


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


def _dp_bars(H: List[float], onsets: List[float], tol: float):
    """Choose bar heads on the half-bar grid H. Returns (bars, hits, flips)."""
    import bisect
    ons = sorted(onsets)
    def hit(t: float) -> float:
        k = bisect.bisect_left(ons, t - tol)
        return 1.0 if k < len(ons) and ons[k] <= t + tol else 0.0
    n = len(H)
    NEG = -1e9
    best = [NEG] * n
    prev = [-1] * n
    steps = {2: 0.15, 1: -1.6, 3: -1.6}   # regular spacing gets a small bonus, odd spacing a cost
    for i in range(n):
        h = hit(H[i])
        if i < 2:
            best[i] = h
            continue
        for step, bonus in steps.items():
            j = i - step
            if j >= 0 and best[j] > NEG / 2:
                cand = best[j] + h + bonus
                if cand > best[i]:
                    best[i], prev[i] = cand, j
    end = max(range(n), key=lambda i: best[i])
    seq = []
    i = end
    while i >= 0:
        seq.append(H[i])
        i = prev[i]
    seq.reverse()
    flips = sum(1 for a, b in zip(seq, seq[1:]) if abs(H.index(b) - H.index(a)) != 2)
    hits = sum(hit(t) for t in seq)
    return seq, hits, flips


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
    if compound and len(H) >= 6:
        # Local phase: a held chorus ending can add an odd half-bar, after
        # which every chord sits on the other parity. Pick bar heads on the
        # half-bar grid by DP — 2 pulses per bar normally, 1 or 3 allowed at a
        # cost — maximising chord-onset hits.
        dp_bars, dp_hits, flips = _dp_bars(H, onsets, tol)
        if dp_bars and len(dp_bars) >= 2 and flips:
            bars = dp_bars
            align_after = round(dp_hits / max(1, len(onsets)), 3)
            meta["phase_flips"] = flips

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
    # 整小節優先 — whole-bar preference. If the song changes chords on bar
    # lines most of the time, a change that landed on a half-bar line is far
    # more likely a BTC boundary that drifted by half a bar than a genuine
    # half-bar harmony. Re-quantize such songs at bar resolution (majority
    # chord per bar). Genuinely half-bar songs (ratio below the gate) keep
    # the finer grid.
    bar_pref = {"applied": False}
    if compound and len(quantized) >= 6:
        bar_set = {round(b, 3) for b in bars}
        changes = [q["time"] for q in quantized[1:]]
        on_bar = sum(1 for t in changes if round(t, 3) in bar_set)
        ratio_bar = on_bar / len(changes) if changes else 0.0
        # Second gate: when BTC drift is pervasive the change ratio is low
        # even though harmonies are bar-long — so also accept songs whose
        # time is mostly spent in chords ≥ 1 bar (Libera: 83 %).
        tot_t = sum(q["end"] - q["time"] for q in quantized) or 1.0
        long_t = sum(q["end"] - q["time"] for q in quantized if q["end"] - q["time"] >= expected * 0.9)
        long_share = long_t / tot_t
        bar_pref = {"applied": False, "bar_change_ratio": round(ratio_bar, 3), "long_chord_share": round(long_share, 3), "changes": len(changes)}
        if ratio_bar >= _BAR_PREF_MIN_RATIO or long_share >= _BAR_PREF_MIN_LONG_SHARE:
            bq: List[Dict] = []
            ci = 0
            for k in range(len(bars) - 1):
                s0, s1 = bars[k], bars[k + 1]
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
                if bq and bq[-1]["chord"] == best.get("chord"):
                    bq[-1]["end"] = round(s1, 3)
                else:
                    q = dict(best)
                    q["time"], q["end"] = round(s0, 3), round(s1, 3)
                    bq.append(q)
            # No-drop guard: a chord that was at least ~half a bar long in the
            # detection must survive — give it the bar it overlaps most (a
            # 50/50 bar split otherwise lets the neighbour swallow it).
            if len(bq) >= 2:
                bar_of: List = []
                for k in range(len(bars) - 1):
                    mid = (bars[k] + bars[k + 1]) / 2
                    bar_of.append(next((q for q in bq if q["time"] <= mid < q["end"]), None))
                rescued = 0
                rescued_ex: List[Dict] = []
                locked: set = set()
                for idx, c in enumerate(chords):
                    c0, c1 = float(c.get("time", 0)), float(c.get("end", c.get("time", 0)))
                    if c1 - c0 < expected * 0.45:
                        continue
                    if any(b is not None and b.get("chord") == c.get("chord") and b["time"] < c1 and b["end"] > c0 for b in bar_of):
                        continue
                    # Candidate bars by overlap; never take a bar another
                    # rescued chord already locked (that caused F → Bb7
                    # overwrite chains), and prefer bars whose holder keeps
                    # at least one other bar.
                    cands = sorted(((min(bars[k + 1], c1) - max(bars[k], c0), k) for k in range(len(bars) - 1)), reverse=True)
                    cands = [(ov, k) for ov, k in cands if ov > 0 and k not in locked]
                    best_k = -1
                    for ov, k in cands:
                        holder = bar_of[k]
                        held = sum(1 for b in bar_of if b is holder) if holder is not None else 0
                        if holder is None or held >= 2:
                            best_k = k
                            break
                    if best_k < 0 and cands:
                        best_k = cands[0][1]
                    if best_k >= 0:
                        locked.add(best_k)
                        q = dict(c)
                        q["time"], q["end"] = round(bars[best_k], 3), round(bars[best_k + 1], 3)
                        bar_of[best_k] = q
                        rescued += 1
                        rescued_ex.append({"chord": c.get("chord"), "at": round(c0, 2), "bar": round(bars[best_k], 2)})
                if rescued:
                    rebuilt: List[Dict] = []
                    for k, q in enumerate(bar_of):
                        if q is None:
                            continue
                        s0, s1 = round(bars[k], 3), round(bars[k + 1], 3)
                        if rebuilt and rebuilt[-1]["chord"] == q["chord"] and abs(rebuilt[-1]["end"] - s0) < 0.01:
                            rebuilt[-1]["end"] = s1
                        else:
                            nq = dict(q)
                            nq["time"], nq["end"] = s0, s1
                            rebuilt.append(nq)
                    bq = rebuilt
                bar_pref["rescued"] = rescued
                bar_pref["rescued_examples"] = rescued_ex[:20]
                bar_pref["applied"] = True
                bar_pref["chords_half_bar"] = len(quantized)
                bar_pref["chords_whole_bar"] = len(bq)
                quantized = bq

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
        "bar_preference": bar_pref,
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
