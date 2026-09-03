"""Chord-progression pattern summary for the player (階段 1 of 進行分析).

Given a song's chord timeline, find the repeating loop(s) the song is built on
— e.g. "D → Bm7 → Em7 → A" (I–vi–ii–V), 2 bars per chord, 8-bar loop covering
93% of the song — plus where each occurrence starts. Those occurrence starts
double as phrase-boundary candidates for section detection (階段 2).

Pure functions, no I/O. Used by GET /api/progression/summary.
"""
import re
import statistics
from collections import Counter
from typing import Dict, List, Optional

try:
    from .preprocess import parse_chord_name
except ImportError:  # pragma: no cover - flat import when run from backend/
    from preprocess import parse_chord_name

_ROMAN = {0: "I", 1: "♭II", 2: "II", 3: "♭III", 4: "III", 5: "IV",
          6: "♭V", 7: "V", 8: "♭VI", 9: "VI", 10: "♭VII", 11: "VII"}
_NOTE_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Well-known progressions keyed by their degree "family" string (case = quality
# family, extensions stripped). Rotations are matched too and flagged.
KNOWN_PROGRESSIONS = {
    "I-V-vi-IV": "萬用四和弦 (Axis)",
    "vi-IV-I-V": "情歌 6451",
    "I-vi-IV-V": "50s 進行 (Doo-wop)",
    "I-vi-ii-V": "1625 迴轉",
    "IV-V-iii-vi": "王道進行 4536",
    "ii-V-I": "ii–V–I",
    "I-IV-V": "三和弦 I–IV–V",
    "I-IV-V-IV": "I–IV–V–IV",
    "I-IV-I-V": "I–IV–I–V",
    "I-V-vi-iii-IV-I-IV-V": "卡農進行",
    "I-IV-vi-V": "I–IV–vi–V",
    "vi-V-IV-V": "vi–V–IV–V",
    "I-V-IV-V": "I–V–IV–V",
    "I-♭VII-IV": "Mixolydian I–♭VII–IV",
    "i-♭VII-♭VI-V": "安達魯西亞終止",
    "I-iii-vi-IV": "I–iii–vi–IV",
    "I-iii-IV-V": "I–iii–IV–V",
    "vi-ii-V-I": "五度圈 vi–ii–V–I",
    "i-iv-V": "小調 i–iv–V",
    "i-VI-III-VII": "小調 6415 (i–VI–III–VII)",
    "i-♭VI-♭III-♭VII": "小調 6415 (i–♭VI–♭III–♭VII)",
    "I-vi-IV-V-I": "50s 進行",
}


def _key_semi(key: str) -> int:
    m = re.match(r"^([A-G])([b#])?", key or "C")
    if not m:
        return 0
    return (_NOTE_SEMI[m.group(1)] + (1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0)) % 12


def _is_minor_family(quality: str) -> bool:
    q = quality or ""
    if q.startswith("maj"):
        return False
    return bool(re.match(r"^(m(?!aj)|min|dim|ø|°|-)", q))


def _roman(chord: str, key_semi: int, with_ext: bool) -> Optional[str]:
    semi, quality = parse_chord_name(chord)
    if semi is None:
        return None
    base = _ROMAN[(semi - key_semi) % 12]
    minor = _is_minor_family(quality or "")
    r = base.lower() if minor else base
    if not with_ext:
        return r
    ext = quality or ""
    ext = re.sub(r"^(min|m)(?!aj)", "", ext)  # case already carries minor-ness
    return r + ext


def _condense(chords: List[Dict], key_semi: int) -> List[Dict]:
    """Merge consecutive identical chord names into tokens with start/end."""
    toks: List[Dict] = []
    for c in chords:
        name = c.get("chord")
        if not name or name in ("N", "X"):
            continue
        t0 = float(c.get("time", 0.0))
        t1 = float(c.get("end", t0))
        fam = _roman(name, key_semi, False)
        if fam is None:
            continue
        if toks and toks[-1]["name"] == name:
            toks[-1]["end"] = max(toks[-1]["end"], t1)
            continue
        toks.append({"name": name, "fam": fam, "roman": _roman(name, key_semi, True), "start": t0, "end": t1})
    return toks


def _grid_tokens(chords: List[Dict], key_semi: int, downbeats: Optional[List[float]], bar_sec: float) -> List[Dict]:
    """Sample the chord that dominates each grid slot (one bar, or half a bar
    when chords typically change faster than once per bar). Passing chords
    shorter than the slot vanish, and loop lengths come out in bars."""
    valid = [c for c in chords if c.get("chord") and c["chord"] not in ("N", "X")]
    if not valid:
        return []
    durs = [float(c.get("end", c["time"])) - float(c["time"]) for c in valid]
    med = statistics.median(durs) if durs else bar_sec
    step_bars = 0.5 if med < 0.75 * bar_sec else 1.0
    t_start = float(valid[0]["time"])
    t_end = max(float(c.get("end", c["time"])) for c in valid)
    # Grid anchored on real downbeats when we have them, else on the first chord.
    if downbeats and len(downbeats) >= 4:
        anchor = min(downbeats, key=lambda d: abs(d - t_start))
    else:
        anchor = t_start
    step = bar_sec * step_bars
    edges = []
    t = anchor
    while t > t_start + 1e-6:
        t -= step
    while t < t_end - step * 0.25:
        edges.append(t)
        t += step
    toks: List[Dict] = []
    ci = 0
    for e in edges:
        s0, s1 = e, e + step
        best, best_ov = None, 0.0
        while ci > 0 and float(valid[ci]["time"]) > s0:
            ci -= 1
        j = ci
        while j < len(valid) and float(valid[j]["time"]) < s1:
            c = valid[j]
            ov = min(s1, float(c.get("end", c["time"]))) - max(s0, float(c["time"]))
            if ov > best_ov:
                best, best_ov = c, ov
            j += 1
        ci = max(0, j - 1)
        if not best or best_ov < step * 0.3:
            continue
        name = best["chord"]
        fam = _roman(name, key_semi, False)
        if fam is None:
            continue
        toks.append({"name": name, "fam": fam, "roman": _roman(name, key_semi, True), "start": s0, "end": s1, "step_bars": step_bars})
    return toks


def _bar_seconds(downbeats: Optional[List[float]], bpm: Optional[float], toks: List[Dict]) -> float:
    if downbeats and len(downbeats) >= 4:
        diffs = [b - a for a, b in zip(downbeats, downbeats[1:]) if b > a]
        if diffs:
            return float(statistics.median(diffs))
    if bpm and bpm > 0:
        return 4 * 60.0 / float(bpm)
    durs = [t["end"] - t["start"] for t in toks if t["end"] > t["start"]]
    return float(statistics.median(durs)) if durs else 2.0


def _best_gram(seq: List[str], used: List[bool], min_p: int = 2, max_p: int = 8):
    """Pick the (rotation-invariant) n-gram that explains the most still-unused
    chord tokens. Returns (loop_tuple, count) or None."""
    best = None
    n = len(seq)
    for p in range(min_p, max_p + 1):
        if n < 2 * p:
            break
        grams: Counter = Counter()
        rot_of: Dict[tuple, Counter] = {}
        for i in range(n - p + 1):
            if any(used[i:i + p]):
                continue
            g = tuple(seq[i:i + p])
            if len(set(g)) < 2:
                continue
            # Skip grams that are themselves a repeated shorter loop (I V I V).
            if any(p % q == 0 and g == g[:q] * (p // q) for q in range(1, p)):
                continue
            canon = min(g[k:] + g[:k] for k in range(p))
            grams[canon] += 1
            rot_of.setdefault(canon, Counter())[g] += 1
        for canon, cnt in grams.items():
            need = 3 if p == 2 else 2
            if cnt < need:
                continue
            # Coverage with a mild preference for shorter loops (an 8-gram made
            # of two 4-loops must not beat the 4-loop).
            score = cnt * p * (1 - 0.02 * p)
            if best is None or score > best[0]:
                rots = rot_of[canon]
                tonic = [g for g in rots if g[0] in ("I", "i")]
                loop = max(tonic, key=lambda g: rots[g]) if tonic else rots.most_common(1)[0][0]
                best = (score, loop, cnt)
    return (best[1], best[2]) if best else None


def _occurrences(toks: List[Dict], loop: tuple, used: List[bool]):
    p = len(loop)
    occ = []
    i = 0
    while i <= len(toks) - p:
        if not any(used[i:i + p]) and tuple(t["fam"] for t in toks[i:i + p]) == loop:
            occ.append((i, i + p))
            for k in range(i, i + p):
                used[k] = True
            i += p
        else:
            i += 1
    return occ


def _known_name(loop: tuple):
    key = "-".join(loop)
    if key in KNOWN_PROGRESSIONS:
        return KNOWN_PROGRESSIONS[key], False
    for k in range(1, len(loop)):
        rot = "-".join(loop[k:] + loop[:k])
        if rot in KNOWN_PROGRESSIONS:
            return KNOWN_PROGRESSIONS[rot], True
    return None, False


def _describe(toks: List[Dict], occ, loop: tuple, bar_sec: float, total: float, key_semi: int) -> Dict:
    p = len(loop)
    first = toks[occ[0][0]:occ[0][1]]
    step_bars = first[0].get("step_bars", 1.0)
    covered = sum(toks[b - 1]["end"] - toks[a]["start"] for a, b in occ)
    # Collapse consecutive identical slots for display: D D Bm7 Bm7 → D(2) Bm7(2).
    cond: List[Dict] = []
    for t in first:
        if cond and cond[-1]["name"] == t["name"]:
            cond[-1]["bars"] += step_bars
        else:
            cond.append({"name": t["name"], "roman": t["roman"], "fam": t["fam"], "bars": step_bars})
    cond_fam = tuple(c["fam"] for c in cond)
    loop_bars = round(p * step_bars, 1)
    bars_each = [c["bars"] for c in cond]
    name, rotated = _known_name(cond_fam)
    return {
        "chords": [c["name"] for c in cond],
        "roman": [c["roman"] for c in cond],
        "roman_family": list(cond_fam),
        "roman_text": "–".join(c["roman"] for c in cond),
        "chord_bars": [round(b, 2) for b in bars_each],
        "known_name": name,
        "known_rotated": rotated,
        "length": len(cond),
        "bars_per_chord": round(statistics.median(bars_each), 2) if bars_each else 0,
        "loop_bars": loop_bars,
        "occurrences": [{"start": round(toks[a]["start"], 2), "end": round(toks[b - 1]["end"], 2)} for a, b in occ],
        "count": len(occ),
        "coverage": round(covered / total, 3) if total > 0 else 0.0,
    }


def analyze_progression(chords: List[Dict], key: str = "C", downbeats: Optional[List[float]] = None,
                        bpm: Optional[float] = None, max_patterns: int = 3) -> Dict:
    key_semi = _key_semi(key)
    bar_sec = _bar_seconds(downbeats, bpm, _condense(chords, key_semi))
    toks = _grid_tokens(chords, key_semi, downbeats, bar_sec)
    if len(toks) < 4:
        return {"key": key, "patterns": [], "tokens": len(toks), "note": "too few chords"}
    total = toks[-1]["end"] - toks[0]["start"]
    seq = [t["fam"] for t in toks]
    max_p = 16 if toks[0].get("step_bars", 1.0) < 1 else 8
    used = [False] * len(toks)
    patterns = []

    for _ in range(max_patterns):
        best = _best_gram(seq, used, max_p=max_p)
        if not best:
            break
        loop, _cnt = best
        occ = _occurrences(toks, loop, used)
        if len(occ) < 2:
            break
        desc = _describe(toks, occ, loop, bar_sec, total, key_semi)
        # Ignore loops that explain almost nothing (a couple of bars in a 4-minute song).
        if desc["coverage"] < 0.08 and len(occ) * len(loop) < 8:
            break
        patterns.append(desc)

    patterns.sort(key=lambda x: -x["coverage"])
    # Phrase boundary candidates: every loop occurrence start (階段 2 input).
    bounds = sorted({o["start"] for pt in patterns for o in pt["occurrences"]})
    return {
        "key": key,
        "bar_seconds": round(bar_sec, 3),
        "duration": round(total, 2),
        "tokens": len(toks),
        "patterns": patterns,
        "phrase_boundaries": [round(b, 2) for b in bounds],
        "explained": round(sum(p["coverage"] for p in patterns), 3),
    }
