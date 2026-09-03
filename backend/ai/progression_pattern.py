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
    step = bar_sec * step_bars
    edges: List[float] = []
    if downbeats and len(downbeats) >= 4:
        # Use the real bar lines (rubato-safe); half-bar slots take midpoints.
        lines = sorted(float(d) for d in downbeats)
        # extend to cover the chord span at the median spacing
        while lines[0] - bar_sec > t_start:
            lines.insert(0, lines[0] - bar_sec)
        while lines[-1] + bar_sec * 0.75 < t_end:
            lines.append(lines[-1] + bar_sec)
        for i, b in enumerate(lines):
            edges.append(b)
            if step_bars < 1 and i + 1 < len(lines):
                edges.append((b + lines[i + 1]) / 2)
        edges = [e for e in edges if e < t_end - step * 0.25]
    else:
        t = t_start
        while t < t_end - step * 0.25:
            edges.append(t)
            t += step
    toks: List[Dict] = []
    ci = 0
    for idx, e in enumerate(edges):
        s0 = e
        s1 = edges[idx + 1] if idx + 1 < len(edges) else e + step
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


_ROMAN_DEG = {v: k for k, v in _ROMAN.items()}


def _fam_split(fam: str):
    """'♭vi' → (8, 'minor'); 'V' → (7, 'major')."""
    up = fam.upper()
    deg = _ROMAN_DEG.get(up)
    if deg is None:
        return None
    return deg, ("minor" if fam != up else "major")


def _relative(gram: tuple):
    """Transposition-invariant form: degrees relative to the first chord."""
    parts = [_fam_split(f) for f in gram]
    if any(p is None for p in parts):
        return None
    d0 = parts[0][0]
    return tuple(((d - d0) % 12, fam) for d, fam in parts)


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
            # Canonical = min over rotations of the transposition-invariant
            # form, so a verse that modulates up a minor third still counts.
            rots = [g[k:] + g[:k] for k in range(p)]
            if p >= _TRANSPOSE_MIN_LEN:
                rel_forms = [_relative(r) for r in rots]
                if any(r is None for r in rel_forms):
                    continue
                canon = ("rel",) + min(rel_forms)
            else:
                canon = ("abs",) + min(rots)   # short grams: absolute degrees only
            grams[canon] += 1
            rot_of.setdefault(canon, Counter())[g] += 1
        for canon, cnt in grams.items():
            need = 3 if p == 2 else 2
            if cnt < need:
                continue
            # Coverage with a mild preference for LONGER loops: the periodic
            # filter above already removes "two 4-loops in a row" grams, and a
            # generic 2-gram must not consume the slots of a real 6-chord verse.
            score = cnt * p * (1 + 0.03 * p)
            if best is None or score > best[0]:
                rots = rot_of[canon]
                tonic = [g for g in rots if g[0] in ("I", "i")]
                loop = max(tonic, key=lambda g: rots[g]) if tonic else rots.most_common(1)[0][0]
                best = (score, loop, cnt)
    return (best[1], best[2]) if best else None


_TRANSPOSE_MIN_LEN = 4   # transposition-invariant matching only for loops this long or longer


def _occurrences(toks: List[Dict], loop: tuple, used: List[bool]):
    """Exact matches of `loop` (any transposition when len ≥ _TRANSPOSE_MIN_LEN). Returns (a, b, transpose)."""
    p = len(loop)
    if p < _TRANSPOSE_MIN_LEN:
        occ = []
        i = 0
        while i <= len(toks) - p:
            if not any(used[i:i + p]) and tuple(t["fam"] for t in toks[i:i + p]) == loop:
                occ.append((i, i + p, 0))
                for k in range(i, i + p):
                    used[k] = True
                i += p
            else:
                i += 1
        return occ
    ref = _relative(loop)
    ref_deg = _fam_split(loop[0])[0] if ref else None
    occ = []
    i = 0
    while i <= len(toks) - p:
        g = tuple(t["fam"] for t in toks[i:i + p])
        if not any(used[i:i + p]) and ref is not None and _relative(g) == ref:
            tr = (_fam_split(g[0])[0] - ref_deg) % 12
            occ.append((i, i + p, tr))
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
    # Display the untransposed occurrence (transpose 0) when there is one.
    base = next((o for o in occ if o[2] == 0), occ[0])
    first = toks[base[0]:base[1]]
    step_bars = first[0].get("step_bars", 1.0)
    covered = sum(toks[b - 1]["end"] - toks[a]["start"] for a, b, _ in occ)
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
        "occurrences": [{"start": round(toks[a]["start"], 2), "end": round(toks[b - 1]["end"], 2), "transpose": tr} for a, b, tr in occ],
        "count": len(occ),
        "transposed_count": sum(1 for _, _, tr in occ if tr),
        "coverage": round(covered / total, 3) if total > 0 else 0.0,
    }


def analyze_progression(chords: List[Dict], key: str = "C", downbeats: Optional[List[float]] = None,
                        bpm: Optional[float] = None, max_patterns: int = 3,
                        bars: Optional[List[float]] = None) -> Dict:
    key_semi = _key_semi(key)
    # 6/8 songs carry half-bar pulses in downbeats[]; bars[] (bar lines only)
    # is the right grid when the arbiter / meter_regularizer provided it.
    grid = bars if bars and len(bars) >= 4 else downbeats
    bar_sec = _bar_seconds(grid, bpm, _condense(chords, key_semi))
    toks = _grid_tokens(chords, key_semi, grid, bar_sec)
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


# ---------------------------------------------------------------------------
# Song-level description: genre / tempo / harmonic vocabulary + per-section
# pattern usage. Called by the API after analyze_progression().
# ---------------------------------------------------------------------------
def _tempo_label(bpm: Optional[float]) -> str:
    if not bpm:
        return ""
    b = float(bpm)
    if b < 76:
        return "慢板"
    if b < 100:
        return "中慢板"
    if b < 130:
        return "中板"
    return "快板"


def describe_style(chords: List[Dict], key: str, bpm: Optional[float], genre: str, patterns: List[Dict]) -> Dict:
    names = [c.get("chord") for c in chords if c.get("chord") and c["chord"] not in ("N", "X")]
    n = max(1, len(names))
    sevenths = sum(1 for c in names if re.search(r"(maj7|m7|7|9|11|13)", c))
    dims = sum(1 for c in names if re.search(r"(dim|°|ø|m7b5)", c))
    sus = sum(1 for c in names if "sus" in c)
    slash = sum(1 for c in names if "/" in c)
    uniq = len(set(names))
    minor = key.endswith("m")
    tags = []
    if sevenths / n > 0.6:
        tags.append("七和弦為主，爵士 / R&B 色彩")
    elif sevenths / n > 0.25:
        tags.append("三和弦混七和弦")
    else:
        tags.append("三和弦為主，流行 / 民謠質地")
    if dims / n > 0.05:
        tags.append("有減和弦經過")
    if sus / n > 0.08:
        tags.append("常用掛留和弦")
    if slash / n > 0.1:
        tags.append("轉位低音線")
    if uniq <= 5:
        tags.append(f"只用 {uniq} 種和弦，結構精簡")
    elif uniq >= 16:
        tags.append(f"和弦種類多達 {uniq} 種，和聲豐富")
    main = patterns[0] if patterns else None
    if main and main["coverage"] >= 0.6:
        tags.append(f"整首幾乎都在跑同一個 {main['loop_bars']:g} 小節循環")
    elif len(patterns) >= 2:
        tags.append("主歌副歌各有自己的循環")
    elif not patterns:
        tags.append("沒有固定循環，和弦一直在走")
    try:
        from .accompaniment_generator import suggest_style
    except ImportError:  # pragma: no cover
        from accompaniment_generator import suggest_style
    try:
        suggested = suggest_style(genre=genre or "", bpm=float(bpm or 120))[:3]
    except Exception:
        suggested = []
    return {
        "genre": genre or "",
        "tempo_label": _tempo_label(bpm),
        "bpm": round(float(bpm), 1) if bpm else None,
        "mode": "小調" if minor else "大調",
        "tags": tags,
        "suggested_styles": suggested,
        "unique_chords": uniq,
    }


def map_sections(sections: List[Dict], patterns: List[Dict], chords: List[Dict], key_semi: int) -> List[Dict]:
    """For each detected section, which loop covers it (by overlapped time)."""
    out = []
    for sec in sections or []:
        s0, s1 = float(sec.get("start", 0)), float(sec.get("end", 0))
        if s1 <= s0:
            continue
        best_i, best_ov = -1, 0.0
        for i, p in enumerate(patterns):
            ov = sum(max(0.0, min(s1, o["end"]) - max(s0, o["start"])) for o in p["occurrences"])
            if ov > best_ov:
                best_i, best_ov = i, ov
        ratio = best_ov / (s1 - s0)
        # Chord run inside the section (condensed) for the "free" description.
        run = []
        for c in chords:
            t = float(c.get("time", 0))
            if t < s0 or t >= s1 or not c.get("chord") or c["chord"] in ("N", "X"):
                continue
            if not run or run[-1] != c["chord"]:
                run.append(c["chord"])
        out.append({
            "type": sec.get("type"), "label": sec.get("label") or sec.get("type"),
            "color": sec.get("color"), "start": round(s0, 2), "end": round(s1, 2),
            "pattern": best_i if ratio >= 0.3 else -1,
            "pattern_ratio": round(ratio, 2),
            "chord_run": run[:12], "chord_run_more": max(0, len(run) - 12),
        })
    return out
