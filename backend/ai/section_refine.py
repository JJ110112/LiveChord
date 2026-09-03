"""階段 2 — refine algorithmic section boundaries with chord-loop structure.

``section_detect`` slices the song into fixed windows, so its boundaries land
on window multiples, not musical phrase starts. ``progression_pattern`` knows
where each repetition of the song's chord loops begins. This module:

  1. snaps every interior section boundary to the nearest loop-occurrence
     start (or, failing that, the nearest bar line) within a tolerance;
  2. splits a section that spans two different loops at the switch point,
     labelling the new part by the label that loop carries elsewhere in the
     song (verse-loop → 主歌, chorus-loop → 副歌) when that is unambiguous;
  3. drops sections that collapsed to less than one bar.

Human annotations are never touched (callers skip mode == "human-loop").
Returns (sections, meta) — sections keep the detector's dict shape.
"""
from typing import Dict, List, Optional, Tuple

try:
    from .section_detect import SECTION_TYPES
except ImportError:  # pragma: no cover
    from section_detect import SECTION_TYPES

_SNAP_TOL_BARS = 1.5     # boundary may move up to this many bars to reach a loop start
_MIN_SECTION_BARS = 2.0  # sections shorter than this after refinement are merged away


def _nearest(points: List[float], t: float) -> Optional[float]:
    if not points:
        return None
    return min(points, key=lambda p: abs(p - t))


def _restyle(sec: Dict, sec_type: str) -> Dict:
    info = SECTION_TYPES.get(sec_type)
    out = dict(sec)
    out["type"] = sec_type
    if info:
        out["label"], out["emoji"], out["color"] = info["label"], info["emoji"], info["color"]
    return out


def _dominant_pattern(patterns: List[Dict], s0: float, s1: float) -> Tuple[int, float]:
    best_i, best_ov = -1, 0.0
    for i, p in enumerate(patterns):
        ov = sum(max(0.0, min(s1, o["end"]) - max(s0, o["start"])) for o in p.get("occurrences", []))
        if ov > best_ov:
            best_i, best_ov = i, ov
    span = max(1e-6, s1 - s0)
    return best_i, best_ov / span


def refine_sections(sections: List[Dict], analysis: Dict, bars: Optional[List[float]] = None) -> Tuple[List[Dict], Dict]:
    patterns = (analysis or {}).get("patterns") or []
    loop_starts = sorted({float(o["start"]) for p in patterns for o in p.get("occurrences", [])})
    bar_sec = float((analysis or {}).get("bar_seconds") or 0) or 2.0
    bar_lines = [float(b) for b in (bars or [])]
    meta: Dict = {"applied": False, "snapped": 0, "split": 0, "dropped": 0, "loop_starts": len(loop_starts)}
    if not sections or (not loop_starts and not bar_lines):
        meta["reason"] = "no-structure"
        return sections, meta

    secs = [dict(s) for s in sections]
    tol = _SNAP_TOL_BARS * bar_sec

    # 1. Snap interior boundaries (sections are contiguous: end[i] == start[i+1]).
    for i in range(1, len(secs)):
        t = float(secs[i]["start"])
        target = _nearest(loop_starts, t)
        if target is None or abs(target - t) > tol:
            alt = _nearest(bar_lines, t)
            target = alt if alt is not None and abs(alt - t) <= bar_sec * 0.5 else None
        if target is None or abs(target - t) < 0.05:
            continue
        # never cross the neighbours' starts
        lo = float(secs[i - 1]["start"]) + bar_sec
        hi = float(secs[i].get("end", t)) - bar_sec
        if not (lo <= target <= hi):
            continue
        secs[i]["start"] = round(target, 2)
        secs[i - 1]["end"] = round(target, 2)
        meta["snapped"] += 1

    # 2. Split sections that switch loops mid-way; label by the loop's usual label.
    if len(patterns) >= 2:
        # pattern index → most common section type among sections it dominates
        votes: Dict[int, Dict[str, float]] = {}
        for s in secs:
            pi, ratio = _dominant_pattern(patterns, float(s["start"]), float(s["end"]))
            if pi >= 0 and ratio >= 0.5:
                votes.setdefault(pi, {})
                votes[pi][s.get("type", "verse")] = votes[pi].get(s.get("type", "verse"), 0.0) + (float(s["end"]) - float(s["start"]))
        usual = {pi: max(v.items(), key=lambda kv: kv[1])[0] for pi, v in votes.items() if v}

        out: List[Dict] = []
        for s in secs:
            s0, s1 = float(s["start"]), float(s["end"])
            # loop-occurrence timeline inside this section
            segs = []
            for pi, p in enumerate(patterns):
                for o in p.get("occurrences", []):
                    a, b = max(s0, float(o["start"])), min(s1, float(o["end"]))
                    if b - a >= bar_sec * 0.9:
                        segs.append((a, b, pi))
            segs.sort()
            # find the first switch point between two different patterns
            cut = None
            for k in range(1, len(segs)):
                if segs[k][2] != segs[k - 1][2] and segs[k][0] - s0 >= _MIN_SECTION_BARS * bar_sec and s1 - segs[k][0] >= _MIN_SECTION_BARS * bar_sec:
                    cut = (segs[k][0], segs[k - 1][2], segs[k][2])
                    break
            if cut is None:
                out.append(s)
                continue
            t, before_pi, after_pi = cut
            first = dict(s); first["end"] = round(t, 2)
            second = dict(s); second["start"] = round(t, 2)
            if usual.get(before_pi) and usual.get(after_pi) and usual[before_pi] != usual[after_pi]:
                first = _restyle(first, usual[before_pi])
                second = _restyle(second, usual[after_pi])
            out.extend([first, second])
            meta["split"] += 1
        secs = out

    # 3. Drop / merge sections that became too short.
    cleaned: List[Dict] = []
    for s in secs:
        if float(s["end"]) - float(s["start"]) < _MIN_SECTION_BARS * bar_sec and cleaned:
            cleaned[-1]["end"] = s["end"]
            meta["dropped"] += 1
        else:
            cleaned.append(s)

    meta["applied"] = bool(meta["snapped"] or meta["split"] or meta["dropped"])
    return cleaned, meta
