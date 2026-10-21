"""Serve-time beat-density regularization for simple meters (4/4, 2/4).

Problem it fixes (Doctor-X OST "Countdown to Heaven", 60–66 BPM ballad):
beat_this switches pulse level inside one song — eighth-note ticks (0.5 s)
in verse 1, quarter notes (1.0 s) in verse 2, mixed in between — and its
downbeats switch from whole bars (4.0 s) to half bars (1.8 s). tempo_curve is
built from those gaps, so the player read 120 BPM in verse 1 and 60 in
verse 2: a 2-beat Eb card showed 4 dots and the dots sped up and slowed down.
bar_phase_corrector gives up (cv 0.48), meter_regularizer only handles
6/8 / 3/4, and nothing else re-levels the pulse.

What it does (non-destructive, on the served payload only):
  1. Pulse level = the octave of the song's stored bpm (which already carries
     ingest ballad-halving), so the song tempo is never re-decided — only the
     stretches where the tracker switched level are folded back to it.
     Gate: runs of >= 8 consecutive gaps at another level must cover >= 10 %
     of the time, so steady songs and sporadic missed beats are never touched.
  2. Rebuild beats[]: walk the beats with a local reference period (median
     of the octave-folded gaps around each point, so real tempo drift is
     kept); a beat closer than 0.7 periods to the last kept beat is a
     subdivision and is dropped, a gap of >= 1.5 periods gets beats inserted
     evenly. The phase of the leading run is chosen by the original
     downbeats + chord onsets.
  3. Rebuild downbeats[] / bars[] as every 4th beat; the phase is picked per
     4-bar chunk (Viterbi, switch penalty) by chord onsets + original
     downbeats, so a mid-song tracker dropout does not shift every later bar.
  4. tempo_curve = 60 / next gap per beat; bpm = median.
  5. (active only) snap chord boundaries within 0.45 beat onto the new beats.

Rollout is staged by mode (off / shadow / active, default shadow), see
_mode(). Stamps ``beat_density_meta``.
"""
import copy
import json
import math
import os
import statistics
from typing import Dict, List, Optional

_SIMPLE = {"", "4/4", "2/4", "C"}
_MIN_BEATS = 32
_MIN_MINORITY_SHARE = 0.10
_MIN_MINORITY_GAPS = 8
_CHUNK_BEATS = 16          # 4 bars of 4/4 per phase decision
_SWITCH_PENALTY = 4        # hits a new phase must win by to switch
_ACTIVE_MIN_SHARE = 0.20   # active mode only rewrites songs this far off
_ACTIVE_MAX_SWITCHES = 2   # …and whose bar phase is stable (more switches = grid we can't trust)
_SNAP_TOL = 0.45           # chord boundary snap tolerance, in beats
_MIN_CHORD_BEATS = 0.5     # never shrink a chord below half a beat
_MIN_RUN = 8   # consecutive gaps at the other level
_DROP_RATIO = 0.7
_INSERT_RATIO = 1.5
_WINDOW = 8


def _octave(g: float, ref: float) -> int:
    return int(round(math.log2(g / ref)))


def _fold(g: float, ref: float) -> float:
    return g / (2 ** _octave(g, ref))


def _hits(points: List[float], targets: List[float], tol: float) -> int:
    if not points or not targets:
        return 0
    import bisect
    n = 0
    for t in targets:
        i = bisect.bisect_left(points, t)
        if any(0 <= j < len(points) and abs(points[j] - t) <= tol for j in (i - 1, i)):
            n += 1
    return n


def regularize(chord_data: Dict) -> Dict:
    ts = str(chord_data.get("time_signature") or "")
    if ts not in _SIMPLE:
        return {"applied": False, "reason": f"meter {ts}"}
    if (chord_data.get("meter_regularizer_meta") or {}).get("applied"):
        return {"applied": False, "reason": "meter_regularizer applied"}
    beats = sorted(float(b) for b in (chord_data.get("beats") or []))
    if len(beats) < _MIN_BEATS:
        return {"applied": False, "reason": "too few beats"}
    gaps = [b - a for a, b in zip(beats, beats[1:]) if b - a > 0.05]
    med = statistics.median(gaps)

    # 1. pulse level by time share
    share: Dict[int, float] = {}
    count: Dict[int, int] = {}
    for g in gaps:
        o = _octave(g, med)
        share[o] = share.get(o, 0.0) + g
        count[o] = count.get(o, 0) + 1
    total = sum(share.values())
    major = max(share, key=share.get)
    # Only SUSTAINED runs at another level count — a missed beat here and
    # there (one 2x gap) is tracker jitter, not a pulse-level switch.
    minority_t, minority_n, run = 0.0, 0, []
    for g in gaps + [None]:
        o = _octave(g, med) if g is not None else None
        if run and (o is None or o != _octave(run[0], med)):
            if _octave(run[0], med) != major and len(run) >= _MIN_RUN:
                minority_t += sum(run)
                minority_n += len(run)
            run = []
        if g is not None:
            run.append(g)
    meta = {"median_gap": round(med, 3),
            "octave_time_share": {str(k): round(v / total, 3) for k, v in sorted(share.items())}}
    if minority_t / total < _MIN_MINORITY_SHARE or minority_n < _MIN_MINORITY_GAPS:
        return {"applied": False, "reason": "steady pulse", **meta}
    # The song's stored bpm (incl. ingest ballad-halving, bpm_correction) is
    # the octave authority: this stage repairs LOCAL switches only, it never
    # re-decides the song's tempo.
    bpm = float(chord_data.get("bpm") or 0)
    ref = 60.0 / bpm if bpm > 0 else med * (2 ** major)
    level = statistics.median([_fold(g, ref) for g in gaps])

    folded = [_fold(g, level) for g in gaps]

    def local_period(i: int) -> float:
        lo, hi = max(0, i - _WINDOW), min(len(folded), i + _WINDOW + 1)
        return statistics.median(folded[lo:hi])

    onsets = [float(c.get("time", 0)) for c in chord_data.get("chords") or []]
    old_down = [float(d) for d in (chord_data.get("downbeats") or [])]

    # 2. rebuild beats; try both phases for the first beat (eighth-note intro)
    def walk(start: int) -> List[float]:
        kept = [beats[start]]
        for i in range(start + 1, len(beats)):
            p = local_period(min(i - 1, len(folded) - 1))
            d = beats[i] - kept[-1]
            if d < _DROP_RATIO * p:
                continue
            if d >= _INSERT_RATIO * p:
                k = int(round(d / p))
                for j in range(1, k):
                    kept.append(kept[-1] + (beats[i] - kept[-1]) / (k - j + 1))
            kept.append(beats[i])
        return kept

    cands = [walk(0)]
    if len(beats) > 1 and beats[1] - beats[0] < _DROP_RATIO * local_period(0):
        cands.append(walk(1))
    tol = 0.2 * level
    new_beats = max(cands, key=lambda bs: 2 * _hits(bs, old_down, tol) + _hits(bs, onsets, tol))

    # 3. bars every 4 beats; the phase is chosen PER 4-BAR CHUNK (Viterbi
    # with a switch penalty) so one dropped/extra beat mid-song does not push
    # every later bar line off — a switch leaves one short/long bar there.
    bars, phase_path = _local_phase_bars(new_beats, old_down, onsets, tol, level)

    # 4. tempo
    new_gaps = [b - a for a, b in zip(new_beats, new_beats[1:])]
    curve = [{"t": round(b, 3), "bpm": round(60.0 / g, 2)} for b, g in zip(new_beats, new_gaps) if g > 0]
    old_bpm = chord_data.get("bpm")
    chord_data["beats"] = [round(b, 4) for b in new_beats]
    chord_data["downbeats"] = [round(b, 4) for b in bars]
    chord_data["bars"] = [round(b, 4) for b in bars]
    chord_data["tempo_curve"] = curve
    chord_data["bpm"] = round(60.0 / statistics.median(new_gaps), 1)
    meta.update({
        "applied": True,
        "minority_share": round(minority_t / total, 3),
        "beat_sec": round(level, 3),
        "beats_before": len(beats), "beats_after": len(new_beats),
        "downbeats_before": len(old_down), "downbeats_after": len(bars),
        "bpm_before": old_bpm, "bpm_after": chord_data["bpm"],
        "bar_phase": phase_path[0] if phase_path else 0,
        "phase_switches": sum(1 for a, b in zip(phase_path, phase_path[1:]) if a != b),
    })
    return meta


def _local_phase_bars(beats: List[float], old_down: List[float], onsets: List[float],
                      tol: float, level: float):
    n = len(beats)
    chunks = [(s, min(n, s + _CHUNK_BEATS)) for s in range(0, n, _CHUNK_BEATS)]

    def score(c, k):
        s, e = chunks[c]
        lines = [beats[i] for i in range(s, e) if i % 4 == k]
        lo, hi = beats[s] - tol, beats[e - 1] + tol
        return (2 * _hits(lines, [t for t in old_down if lo <= t <= hi], tol)
                + _hits(lines, [t for t in onsets if lo <= t <= hi], tol))

    best = [[score(0, k), [k]] for k in range(4)]
    for c in range(1, len(chunks)):
        nxt = []
        for k in range(4):
            prev = max(range(4), key=lambda j: best[j][0] - (_SWITCH_PENALTY if j != k else 0))
            v = best[prev][0] - (_SWITCH_PENALTY if prev != k else 0) + score(c, k)
            nxt.append([v, best[prev][1] + [k]])
        best = nxt
    path = max(best, key=lambda x: x[0])[1]
    bars = []
    for (s, e), k in zip(chunks, path):
        for i in range(s, e):
            if i % 4 == k and (not bars or beats[i] - bars[-1] > 1.5 * level):
                bars.append(beats[i])
    if bars and beats[0] < bars[0] - 0.5 * level:   # anacrusis bar line
        bars.insert(0, beats[0])
    return bars, path


def snap_chords_to_beats(chords: List[Dict], beats: List[float], beat_sec: float):
    """Move each inner chord boundary onto the nearest beat when it is within
    _SNAP_TOL beats (BTC boundaries sit 0.3–0.5 s off the pulse), keeping both
    neighbours at least _MIN_CHORD_BEATS long. The first onset and last end
    are never moved. Returns (chords, moved)."""
    import bisect
    out = [dict(c) for c in chords]
    moved = 0
    for i in range(1, len(out)):
        t = float(out[i]["time"])
        j = bisect.bisect_left(beats, t)
        near = min((beats[k] for k in (j - 1, j) if 0 <= k < len(beats)), key=lambda b: abs(b - t), default=None)
        if near is None or abs(near - t) < 1e-3 or abs(near - t) > _SNAP_TOL * beat_sec:
            continue
        prev_start = float(out[i - 1]["time"])
        end = float(out[i].get("end", t))
        if near - prev_start < _MIN_CHORD_BEATS * beat_sec or end - near < _MIN_CHORD_BEATS * beat_sec:
            continue
        out[i]["time"] = round(near, 3)
        out[i - 1]["end"] = round(near, 3)
        moved += 1
    return out, moved


_MODES = ("off", "shadow", "active")
_MODE_CACHE = None


def _mode():
    """off / shadow / active (default shadow) + the severity a song needs for
    active: LIVECHORD_BEAT_DENSITY_MODE or data/settings.json
    beat_density_mode / beat_density_min_share. Restart to pick up changes."""
    global _MODE_CACHE
    if _MODE_CACHE is None:
        cfg = {}
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "settings.json"),
                      encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
        mode = (os.environ.get("LIVECHORD_BEAT_DENSITY_MODE") or str(cfg.get("beat_density_mode", ""))).strip().lower()
        try:
            share = float(cfg.get("beat_density_min_share", _ACTIVE_MIN_SHARE))
        except (TypeError, ValueError):
            share = _ACTIVE_MIN_SHARE
        _MODE_CACHE = (mode if mode in _MODES else "shadow", share)
    return _MODE_CACHE


def maybe_regularize_density_for_serve(chord_data: Dict) -> Dict:
    """shadow: compute on a copy and only stamp beat_density_meta (would_*);
    active: rewrite the grid, but only for severe songs (time at the wrong
    pulse level >= min_share) with a stable bar phase — the rest stay shadow."""
    mode, min_share = _mode()
    if mode == "off":
        return chord_data
    try:
        probe = copy.deepcopy(chord_data)
        meta = regularize(probe)
        severe = (meta.get("applied") and meta.get("minority_share", 0) >= min_share
                  and meta.get("phase_switches", 0) <= _ACTIVE_MAX_SWITCHES)
        if mode == "active" and severe:
            for k in ("beats", "downbeats", "bars", "tempo_curve", "bpm"):
                chord_data[k] = probe[k]
            # The grid is now trusted → pull BTC's off-pulse boundaries onto it.
            chord_data["chords"], meta["chords_snapped"] = snap_chords_to_beats(
                chord_data.get("chords") or [], chord_data["beats"], meta["beat_sec"])
            meta["mode"] = "active"
        elif meta.get("applied"):
            meta = {**meta, "applied": False, "would_apply": True, "mode": mode,
                    "reason": "shadow" if mode == "shadow" else "below severity / unstable phase"}
        chord_data["beat_density_meta"] = meta
    except Exception as exc:  # never break serving
        chord_data["beat_density_meta"] = {"applied": False, "reason": f"error: {exc}"}
    return chord_data
