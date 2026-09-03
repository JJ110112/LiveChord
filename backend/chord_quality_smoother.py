"""Same-root chord-quality smoothing (serve-time, non-destructive).

BTC often flickers on extensions within one harmony: Fm → Fm6 → Fm, or
Bm7 → Bm6 → Bm7. Phrase detection already collapses extensions when it
compares degrees, but every other consumer (chord cards, split tools,
progression analysis, jianpu) sees three different chords.

Rule — deliberately conservative (see feedback_chord_noise_filter_aggressiveness):
  * Only chords whose root AND third-family (major / minor / dim / aug / sus)
    match BOTH neighbours are candidates ("sandwiched"). A chord at the edge of
    a same-root run is never touched, so C → C7 → F keeps its secondary
    dominant.
  * The candidate must be shorter than one bar.
  * It is replaced by the quality that holds the most time in the surrounding
    same-root run; equal neighbours are then merged.
  * Second rule: two same-family chords that together fill at most one bar
    (Bm7 | Bm6) are one harmony split by a boundary flicker → the longer one.
  * No song-level canonicalisation: a 50/50 Fm vs Fm6 across verses, or a
    dominant 7th against a tonic triad, is left as detected.

Stamps ``quality_smooth_meta`` = {applied, replaced, examples}.
"""
import re
import statistics
from typing import Dict, List, Optional, Tuple

try:
    from ai.preprocess import parse_chord_name
except ImportError:  # pragma: no cover
    from preprocess import parse_chord_name

_MAX_EXAMPLES = 8


def _family(quality: str) -> str:
    q = (quality or "").strip()
    if re.match(r"^(m7b5|ø|°|dim)", q):
        return "dim"
    if re.match(r"^(aug|\+)", q):
        return "aug"
    if q.startswith("sus") or re.match(r"^\d*sus", q):
        return "sus"
    if re.match(r"^(m(?!aj)|min|-)", q):
        return "minor"
    return "major"


def _key(chord: str) -> Optional[Tuple[int, str, str]]:
    """(root_pc, family, bass) or None for N/X."""
    semi, quality = parse_chord_name(chord)
    if semi is None:
        return None
    bass = ""
    m = re.search(r"/([A-G][b#]?)$", chord or "")
    if m:
        bass = m.group(1)
    return semi, _family(quality or ""), bass


def _bar_seconds(chord_data: Dict) -> float:
    for field in ("bars", "downbeats"):
        xs = [float(x) for x in (chord_data.get(field) or [])]
        gaps = [b - a for a, b in zip(xs, xs[1:]) if b > a]
        if len(gaps) >= 4:
            med = statistics.median(gaps)
            if field == "downbeats" and str(chord_data.get("time_signature") or "") == "6/8":
                med *= 2  # downbeats[] carries half-bar pulses for 6/8
            return med
    bpm = float(chord_data.get("bpm") or 0)
    return 4 * 60.0 / bpm if bpm > 0 else 2.0


def smooth_qualities(chords: List[Dict], bar_sec: float) -> Tuple[List[Dict], Dict]:
    n = len(chords)
    if n < 3:
        return chords, {"applied": False, "replaced": 0, "examples": []}
    keys = [_key(c.get("chord")) for c in chords]
    dur = [float(c.get("end", c.get("time", 0))) - float(c.get("time", 0)) for c in chords]

    # Maximal runs of identical (root, family, bass).
    runs: List[Tuple[int, int]] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and keys[j + 1] is not None and keys[j + 1] == keys[i]:
            j += 1
        runs.append((i, j))
        i = j + 1

    out = [dict(c) for c in chords]
    replaced = 0
    examples = []
    rules = {"sandwich": 0, "split_bar": 0}

    def _replace(k: int, to: str, rule: str):
        nonlocal replaced
        if len(examples) < _MAX_EXAMPLES:
            examples.append({"time": round(float(out[k]["time"]), 2), "from": out[k]["chord"], "to": to, "rule": rule})
        out[k]["chord"] = to
        replaced += 1
        rules[rule] += 1

    for a, b in runs:
        if keys[a] is None:
            continue
        names = {}
        for k in range(a, b + 1):
            names[out[k]["chord"]] = names.get(out[k]["chord"], 0.0) + dur[k]
        if len(names) < 2:
            continue
        dominant = max(names.items(), key=lambda kv: kv[1])[0]
        if b - a >= 2:
            # Rule 1 — sandwiched flicker: X, X', X → X (interior, short).
            for k in range(a + 1, b):
                if out[k]["chord"] != dominant and dur[k] < bar_sec:
                    _replace(k, dominant, "sandwich")
        elif b - a == 1:
            # Rule 2 — one harmony split inside a single bar (Bm7 | Bm6 both
            # shorter than a bar, together no longer than a bar) → longer one.
            if dur[a] < bar_sec and dur[b] < bar_sec and dur[a] + dur[b] <= bar_sec * 1.05:
                k = a if dur[a] < dur[b] else b
                _replace(k, dominant, "split_bar")

    # (A song-level "minority quality" rule was tried and rejected: it turned
    # the real Ab7 of a modulated verse into Ab because the tonic triad
    # dominates the song. Dominant-7 vs triad is functional, not flicker.)

    if not replaced:
        return chords, {"applied": False, "replaced": 0, "examples": []}

    # Merge now-identical neighbours.
    merged: List[Dict] = []
    for c in out:
        if merged and merged[-1]["chord"] == c["chord"] and abs(float(merged[-1].get("end", 0)) - float(c.get("time", 0))) < 0.05:
            merged[-1]["end"] = c.get("end", merged[-1].get("end"))
        else:
            merged.append(c)
    return merged, {"applied": True, "replaced": replaced, "rules": rules, "merged_away": len(out) - len(merged), "examples": examples}


def maybe_smooth_for_serve(chord_data: Dict) -> Dict:
    try:
        chords = chord_data.get("chords") or []
        new_chords, meta = smooth_qualities(chords, _bar_seconds(chord_data))
        if meta.get("applied"):
            chord_data["chords"] = new_chords
        chord_data["quality_smooth_meta"] = meta
    except Exception as exc:  # never break serving
        chord_data["quality_smooth_meta"] = {"applied": False, "reason": f"error: {exc}"}
    return chord_data
