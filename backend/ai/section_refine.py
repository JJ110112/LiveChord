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


def refine_sections(sections: List[Dict], analysis: Dict, bars: Optional[List[float]] = None,
                    chords: Optional[List[Dict]] = None, melody: Optional[List[Dict]] = None) -> Tuple[List[Dict], Dict]:
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

    # 0. Structure-first: when the loops explain a solid share of the song and
    #    the detector's windows are finer than the loops themselves, rebuild the
    #    sections from the loop occurrences (each occurrence = one phrase,
    #    gaps between them = one section each) instead of snapping junk.
    mel = None
    if melody and bars:
        try:
            from .melody_similarity import MelodyBars
        except ImportError:  # pragma: no cover
            from melody_similarity import MelodyBars
        mb = MelodyBars(melody, bars)
        mel = mb if mb.usable else None
    meta["melody_used"] = mel is not None
    structural = _structure_first(secs, patterns, bar_sec, chords or [], mel, meta)
    if structural is not None:
        meta.update({"applied": True, "mode": "structure-first", "sections_before": len(secs), "sections_after": len(structural)})
        return structural, meta

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


_STRUCT_MIN_COVERAGE = 0.30
_RANK_LABELS = ["verse", "chorus", "bridge"]


def _majority_type(secs: List[Dict], s0: float, s1: float) -> Optional[str]:
    votes: Dict[str, float] = {}
    for s in secs:
        ov = max(0.0, min(s1, float(s["end"])) - max(s0, float(s["start"])))
        if ov > 0:
            votes[s.get("type", "verse")] = votes.get(s.get("type", "verse"), 0.0) + ov
    return max(votes.items(), key=lambda kv: kv[1])[0] if votes else None


_STRUCT_MIN_LOOP_BARS = 4.0   # loops shorter than this are riffs, not phrases


import re as _re

_GAP_SIMILAR = 0.45   # Jaccard of chord (root, minor) bags: a gap this similar to an earlier chorus gap is a chorus too


def _chord_bag(chords: List[Dict], s0: float, s1: float) -> set:
    bag = set()
    for c in chords:
        t = float(c.get("time", 0))
        if t < s0 or t >= s1:
            continue
        m = _re.match(r"^([A-G][b#]?)(.*?)(?:/[A-G][b#]?)?$", c.get("chord") or "")
        if not m:
            continue
        minor = bool(_re.match(r"^(m(?!aj)|min|-|dim|ø|°)", m.group(2)))
        bag.add((m.group(1), minor))
    return bag


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


_MEL_VERSE_MIN = 0.45      # a transposed loop occurrence must sound at least this much like the verse
_MEL_EXTEND_MIN = 0.65     # each gap-opening bar this similar to some verse bar belongs to the verse (contiguous run)
_MEL_EXTEND_MAX_BARS = 8


def _structure_first(secs: List[Dict], patterns: List[Dict], bar_sec: float, chords: Optional[List[Dict]] = None,
                     mel=None, meta: Optional[Dict] = None) -> Optional[List[Dict]]:
    chords = chords or []
    meta = meta if meta is not None else {}
    patterns = [p for p in (patterns or []) if float(p.get("loop_bars") or 0) >= _STRUCT_MIN_LOOP_BARS]
    if not patterns or not secs:
        return None
    total = float(secs[-1]["end"]) - float(secs[0]["start"])
    if total <= 0:
        return None
    covered = sum(o["end"] - o["start"] for p in patterns for o in p.get("occurrences", []))
    if covered / total < _STRUCT_MIN_COVERAGE:
        return None
    med_sec = sorted(float(s["end"]) - float(s["start"]) for s in secs)[len(secs) // 2]
    top_loop_sec = float(patterns[0].get("loop_bars") or 0) * bar_sec
    if not top_loop_sec or med_sec >= top_loop_sec * 0.9:
        return None  # detector already works at phrase scale — keep snapping path

    detector_junk = med_sec < top_loop_sec * 0.75
    # Label per loop: detector majority over its occurrences unless the
    # detector is finer than the loops (then rank-based: main loop = verse).
    loop_type: Dict[int, str] = {}
    for i, p in enumerate(patterns):
        t = None
        if not detector_junk:
            votes: Dict[str, float] = {}
            for o in p.get("occurrences", []):
                mt = _majority_type(secs, o["start"], o["end"])
                if mt:
                    votes[mt] = votes.get(mt, 0.0) + (o["end"] - o["start"])
            t = max(votes.items(), key=lambda kv: kv[1])[0] if votes else None
        loop_type[i] = t or _RANK_LABELS[min(i, len(_RANK_LABELS) - 1)]

    events = sorted((o["start"], o["end"], i, o.get("transpose", 0)) for i, p in enumerate(patterns) for o in p.get("occurrences", []))
    # Melody rule 1: a transposed occurrence of the main loop is only a verse
    # if it also sounds like the (untransposed) verses.
    anchors = [(a, b) for a, b, i, tr in events if i == 0 and not tr]
    if mel is not None and anchors:
        kept = []
        demoted = []
        for a, b, i, tr in events:
            if i == 0 and tr:
                sim = mel.best_sim((a, b), anchors)
                if sim < _MEL_VERSE_MIN:
                    demoted.append({"start": round(a, 2), "sim": round(sim, 2)})
                    continue
            kept.append((a, b, i, tr))
        events = kept
        meta["melody_demoted"] = demoted
    events = [(a, b, i) for a, b, i, _ in events]
    out: List[Dict] = []
    cursor = float(secs[0]["start"])
    song_end = float(secs[-1]["end"])

    def add(s0: float, s1: float, sec_type: str):
        if s1 - s0 < bar_sec * 0.5:
            return
        if out and out[-1]["type"] == sec_type and abs(float(out[-1]["end"]) - s0) < 0.05 and (s1 - s0) < bar_sec * _MIN_SECTION_BARS:
            out[-1]["end"] = round(s1, 2)   # tiny tail merges into the previous same-type section
            return
        out.append(_restyle({"start": round(s0, 2), "end": round(s1, 2)}, sec_type))

    chorus_bags: List[set] = []
    for a, b, i in events:
        if a - cursor >= bar_sec * _MIN_SECTION_BARS:
            gap_type = _majority_type(secs, cursor, a) if not detector_junk else None
            if gap_type is None or gap_type == loop_type[i]:
                if not out:
                    gap_type = "intro"
                else:
                    # Gaps between loop phrases: the first one is the chorus;
                    # later ones are a chorus when their chord content looks
                    # like an earlier chorus, otherwise new material → bridge.
                    bag = _chord_bag(chords, cursor, a)
                    if chords and chorus_bags and max(_jaccard(bag, cb) for cb in chorus_bags) < _GAP_SIMILAR:
                        gap_type = "bridge"
                    else:
                        gap_type = "chorus" if loop_type[i] != "chorus" else "bridge"
                        if gap_type == "chorus":
                            chorus_bags.append(bag)
            add(cursor, a, gap_type)
        elif a > cursor and out:
            out[-1]["end"] = round(a, 2)     # absorb a sub-2-bar gap into the previous section
        add(a, b, loop_type[i])
        cursor = b
    if song_end - cursor >= bar_sec * _MIN_SECTION_BARS:
        add(cursor, song_end, "outro")
    elif out:
        out[-1]["end"] = round(song_end, 2)

    # Melody rule 2: a gap that opens with the verse tune (a varied repeat)
    # belongs to the preceding verse — extend it bar by bar while the melody
    # still matches.
    if mel is not None and anchors and len(out) >= 2:
        extended = []
        verse_type = loop_type.get(0, "verse")
        for k in range(1, len(out)):
            prev, cur = out[k - 1], out[k]
            if prev["type"] != verse_type or cur["type"] == verse_type:
                continue
            if k == len(out) - 1:
                continue  # an outro that re-sings the verse tune is still the outro
            g0, g1 = float(cur["start"]), float(cur["end"])
            i0 = mel.bar_index(g0)
            # Walk bar by bar while each bar still matches some verse bar.
            best_k = 0
            for kb in range(0, _MEL_EXTEND_MAX_BARS):
                if i0 + kb + 1 >= len(mel.bars) or mel.bars[i0 + kb + 1] > g1 + 0.01:
                    break
                if mel.bar_best_sim(i0 + kb, anchors) < _MEL_EXTEND_MIN:
                    break
                best_k = kb + 1
            if best_k < 2:
                best_k = 0
            if best_k:
                t1 = mel.bars[i0 + best_k]
                if g1 - t1 < bar_sec * _MIN_SECTION_BARS:
                    t1 = g1  # whole gap was a varied verse
                prev["end"] = round(t1, 2)
                cur["start"] = round(t1, 2)
                extended.append({"at": round(g0, 2), "bars": best_k})
        out = [s for s in out if float(s["end"]) - float(s["start"]) > 0.05]
        meta["melody_extended"] = extended
    # Number repeated phrase labels so the A-B picker can tell them apart.
    return out if len(out) >= 2 else None
