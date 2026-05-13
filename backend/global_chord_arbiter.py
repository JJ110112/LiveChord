"""Song-level chord/beat arbitration diagnostics.

Local repair layers are intentionally conservative: they fix card fragments,
tail gaps, and obvious BPM mistakes. Some songs need a higher viewpoint. A
free-time vocal intro can still carry a harmonic cycle, a chorus can use a
2-beat card grammar, and a later verse can modulate the same grammar upward.

This module records those song-level signals in ``global_arbiter_meta`` and can
apply a small set of high-confidence serve-time corrections. Stored chord JSON
is not rewritten.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Dict, List, Optional, Tuple

try:
    from ai.preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE, chord_to_degree, parse_chord_name
except ImportError:
    from backend.ai.preprocess import NOTE_TO_SEMI, SEMI_TO_NOTE, chord_to_degree, parse_chord_name


_MIN_LONG_INTRO_SEC = 24.0
_MAX_INTRO_END_SEC = 75.0
_LOW_CONF_CV = 0.45
_LOW_CONF_MIN_GAP_SEC = 0.9
_DISPLAY_BPM_MIN = 40.0
_DISPLAY_BPM_MAX = 220.0


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _root_quality(chord: str) -> Tuple[Optional[int], str]:
    root, quality = parse_chord_name(chord or "")
    return root, quality or ""


def _root_name(pc: int, prefer_flats: bool = False) -> str:
    if prefer_flats:
        flats = {1: "Db", 3: "Eb", 6: "Gb", 8: "Ab", 10: "Bb"}
        if pc % 12 in flats:
            return flats[pc % 12]
    return SEMI_TO_NOTE[pc % 12]


def _quality_family(quality: str) -> str:
    q = quality or ""
    if "m" in q and "maj" not in q:
        return "m"
    if "7" in q:
        return "7"
    return ""


def _chord_root_pc(name: str) -> Optional[int]:
    root, _ = _root_quality(name)
    return root


def _same_root(name: str, target: str) -> bool:
    a = _chord_root_pc(name)
    b = _chord_root_pc(target)
    return a is not None and a == b


def _same_minor_root(name: str, target: str) -> bool:
    root, quality = _root_quality(name)
    target_root, target_quality = _root_quality(target)
    if root is None or target_root is None or root != target_root:
        return False
    return _quality_family(quality) == "m" or _quality_family(target_quality) == "m"


def _same_dominant_root(name: str, target: str) -> bool:
    root, quality = _root_quality(name)
    target_root, target_quality = _root_quality(target)
    if root is None or target_root is None or root != target_root:
        return False
    # Dominant sevenths often arrive as a split Eb7/Eb or Bb7/Bbm7 pair in
    # soft passages. Root continuity is more reliable than the raw quality.
    return _quality_family(quality) in ("", "7") or _quality_family(target_quality) == "7"


def _beat_stats(values: List[float], start: float, end: float) -> Dict:
    pts = sorted(v for v in values if start <= v < end)
    gaps = [pts[i + 1] - pts[i] for i in range(len(pts) - 1) if pts[i + 1] - pts[i] > 0.05]
    if not gaps:
        return {"count": len(pts), "median_gap": None, "bpm": None, "cv": None}
    mean = statistics.mean(gaps)
    med = statistics.median(gaps)
    cv = statistics.pstdev(gaps) / mean if mean > 0 and len(gaps) > 1 else 0.0
    return {
        "count": len(pts),
        "median_gap": round(med, 3),
        "bpm": round(60.0 / med, 1) if med > 0 else None,
        "cv": round(cv, 3),
    }


def _dedupe_names(chords: List[Dict], merge_consecutive: bool = True) -> List[Dict]:
    out: List[Dict] = []
    for c in chords:
        name = str(c.get("chord") or "").strip()
        if not name:
            continue
        if merge_consecutive and out and out[-1]["chord"] == name:
            out[-1]["end"] = c.get("end", out[-1].get("end"))
        else:
            item = dict(c)
            item["time"] = _float(c.get("time"))
            item["end"] = _float(c.get("end"), item["time"])
            item["chord"] = name
            out.append(item)
    return out


def _find_intro_cycle(chords: List[Dict]) -> Optional[Dict]:
    if len(chords) < 5:
        return None
    first = chords[0]
    first_name = first["chord"]
    dur = _float(first.get("end")) - _float(first.get("time"))
    if dur < _MIN_LONG_INTRO_SEC or _float(first.get("end")) > _MAX_INTRO_END_SEC:
        return None

    names = [c["chord"] for c in chords[:16]]
    # Look for first chord returning after 2-8 subsequent changes:
    # G | Em Am D7 G -> cycle G Em Am D7.
    for idx in range(2, min(9, len(names))):
        if names[idx] == first_name:
            cycle = names[:idx]
            if 3 <= len(cycle) <= 8:
                root, _ = _root_quality(first_name)
                return {
                    "type": "free_time_long_intro",
                    "long_chord": first_name,
                    "long_span": [round(first["time"], 3), round(first["end"], 3)],
                    "suggested_cycle": cycle,
                    "suggested_key": _root_name(root) if root is not None else None,
                    "suggested_card_beats": 4,
                    "confidence": 0.78,
                }
    return None


def _key_score(chord_names: List[str], key_pc: int) -> float:
    if not chord_names:
        return 0.0
    diatonic = {0, 2, 4, 5, 7, 9, 11}
    strong = {0, 5, 7, 9}  # I, IV, V, vi roots in major-key pop.
    score = 0.0
    total = 0.0
    for i, name in enumerate(chord_names):
        root, _ = _root_quality(name)
        if root is None:
            continue
        rel = (root - key_pc) % 12
        w = 2.0 if i in (0, len(chord_names) - 1) else 1.0
        total += w
        if rel in diatonic:
            score += w
        if rel in strong:
            score += 0.35 * w
    return score / total if total else 0.0


def _template_bonus(chord_names: List[str], key_pc: int) -> float:
    roots = []
    for name in chord_names:
        root, _ = _root_quality(name)
        if root is None:
            continue
        roots.append((root - key_pc) % 12)
    if len(roots) < 4:
        return 0.0

    def match_at(pattern: List[int], start: int = 0) -> bool:
        if len(roots) < start + len(pattern):
            return False
        return roots[start:start + len(pattern)] == pattern

    bonus = 0.0
    # Verse grammar: I vi ii V(7), optionally rotated after a long held tonic.
    verse = [0, 9, 2, 7]
    for start in range(0, min(4, len(roots) - len(verse) + 1)):
        if match_at(verse, start):
            bonus = max(bonus, 0.45)

    # Chorus grammar in 獨上西樓-like pop: I III vi I II V V7.
    # III and II are non-diatonic major chords, but function as bright
    # secondary-dominant colors in the tonic key. Root-vote scoring alone
    # incorrectly prefers D; this template keeps the musical center at G/Em.
    if match_at([0, 4, 9, 0, 2, 7], 0):
        bonus = max(bonus, 0.60)
    return bonus


def _local_key(chord_names: List[str]) -> Optional[Dict]:
    roots = [r for r, _ in (_root_quality(n) for n in chord_names) if r is not None]
    if not roots:
        return None
    candidates = {pc for pc, _ in Counter(roots).most_common(6)}
    first_root, _ = _root_quality(chord_names[0])
    if first_root is not None:
        candidates.add(first_root)
    best = max(
        ((pc, _key_score(chord_names, pc) + _template_bonus(chord_names, pc)) for pc in candidates),
        key=lambda x: x[1],
    )
    prefer_flats = any("b" in n[:2] for n in chord_names)
    return {"key": _root_name(best[0], prefer_flats), "score": round(best[1], 3)}


def _window_key_map(chords: List[Dict], window_sec: float = 24.0) -> List[Dict]:
    if not chords:
        return []
    end = max(_float(c.get("end"), _float(c.get("time"))) for c in chords)
    windows: List[Dict] = []
    t = 0.0
    while t < end:
        names = [c["chord"] for c in chords if t <= _float(c.get("time")) < t + window_sec]
        if len(names) >= 3:
            lk = _local_key(names)
            if lk:
                windows.append({"start": round(t, 3), "end": round(min(end, t + window_sec), 3), **lk})
        t += window_sec
    # Merge adjacent same-key windows.
    merged: List[Dict] = []
    for w in windows:
        if merged and merged[-1]["key"] == w["key"]:
            merged[-1]["end"] = w["end"]
            merged[-1]["score"] = round((merged[-1]["score"] + w["score"]) / 2, 3)
        else:
            merged.append(w)
    return merged


def _transposition(a: List[str], b: List[str]) -> Optional[int]:
    if len(a) != len(b) or not a:
        return None
    shifts = []
    for x, y in zip(a, b):
        rx, qx = _root_quality(x)
        ry, qy = _root_quality(y)
        if rx is None or ry is None:
            return None
        if _quality_family(qx) != _quality_family(qy):
            return None
        shifts.append((ry - rx) % 12)
    if len(set(shifts)) == 1:
        return shifts[0]
    return None


def _find_modulated_cycles(chords: List[Dict], base_cycle: List[str]) -> List[Dict]:
    if len(base_cycle) < 3:
        return []
    out: List[Dict] = []
    n = len(base_cycle)
    for i in range(1, max(1, len(chords) - n + 1)):
        seg = [c["chord"] for c in chords[i:i + n]]
        shift = _transposition(base_cycle, seg)
        if shift and shift != 0:
            from_root, _ = _root_quality(base_cycle[0])
            to_root, _ = _root_quality(seg[0])
            prefer_flats = any("b" in n[:2] for n in seg)
            out.append({
                "time": round(chords[i]["time"], 3),
                "from_cycle": base_cycle,
                "to_cycle": seg,
                "from_key": _root_name(from_root) if from_root is not None else None,
                "to_key": _root_name(to_root, prefer_flats) if to_root is not None else None,
                "shift_semitones": shift,
                "confidence": 0.72,
            })
            if len(out) >= 5:
                break
    return out


def _find_two_beat_grammar(chords: List[Dict], bpm: float) -> List[Dict]:
    if bpm <= 0:
        return []
    spb = 60.0 / bpm
    hints: List[Dict] = []
    for i in range(0, max(0, len(chords) - 6)):
        seg = chords[i:i + 7]
        names = [c["chord"] for c in seg]
        # Pattern family: I III vi I II V V7, but keep this absolute and let
        # the local-key score describe the function.
        durs = [(_float(c.get("end")) - _float(c.get("time"))) / spb for c in seg]
        if sum(1 for d in durs[:4] if 1.5 <= d <= 3.4) >= 3 and sum(1 for d in durs[4:] if 1.5 <= d <= 4.6) >= 2:
            lk = _local_key(names)
            degrees = []
            if lk and lk["key"] in NOTE_TO_SEMI:
                degrees = [chord_to_degree(n, NOTE_TO_SEMI[lk["key"]]) for n in names]
            hints.append({
                "start": round(seg[0]["time"], 3),
                "end": round(seg[-1]["end"], 3),
                "chords": names,
                "dur_beats": [round(d, 2) for d in durs],
                "local_key": lk,
                "degrees": degrees,
                "suggested_card_beats": [2, 2, 2, 2, 4, 2, 2],
            })
            if len(hints) >= 4:
                break
    return hints


def _chord_name_for_degree(key_pc: int, degree: int, quality: str, prefer_flats: bool) -> str:
    return _root_name((key_pc + degree) % 12, prefer_flats=prefer_flats) + quality


def _minor_pop_loop_names(key_pc: int, prefer_flats: bool = True) -> List[str]:
    return [
        _chord_name_for_degree(key_pc, 0, "m", prefer_flats),
        _chord_name_for_degree(key_pc, 10, "", prefer_flats),
        _chord_name_for_degree(key_pc, 8, "", prefer_flats),
        _chord_name_for_degree(key_pc, 3, "", prefer_flats),
        _chord_name_for_degree(key_pc, 5, "m7", prefer_flats),
        _chord_name_for_degree(key_pc, 7, "7", prefer_flats),
        _chord_name_for_degree(key_pc, 0, "m", prefer_flats),
    ]


def _root_matches_degree(name: str, key_pc: int, degree: int) -> bool:
    root = _chord_root_pc(name)
    return root is not None and root == (key_pc + degree) % 12


def _find_minor_pop_loop_grammar(chords: List[Dict], bpm: float) -> List[Dict]:
    if bpm <= 0 or len(chords) < 7:
        return []
    spb = 60.0 / bpm
    hints: List[Dict] = []
    degrees = [0, 10, 8, 3, 5, 7, 0]
    beats = [2, 2, 2, 2, 2, 2, 4]
    for i in range(0, len(chords) - 6):
        seg = chords[i:i + 7]
        names = [c["chord"] for c in seg]
        tonic, tonic_quality = _root_quality(names[0])
        if tonic is None or _quality_family(tonic_quality) != "m":
            continue
        roots_ok = sum(1 for name, degree in zip(names, degrees) if _root_matches_degree(name, tonic, degree))
        # Allow one wrong slot inside the line; apply-time grid repair will
        # canonicalize common pop confusions such as bIII -> v or V7 -> V.
        if roots_ok < 6:
            continue
        durs = [(_float(c.get("end")) - _float(c.get("time"))) / spb for c in seg]
        if sum(1 for d in durs[:6] if 1.3 <= d <= 3.2) < 5 or not (3.0 <= durs[6] <= 7.2):
            continue
        prefer_flats = True
        canonical = _minor_pop_loop_names(tonic, prefer_flats=prefer_flats)
        hints.append({
            "start": round(seg[0]["time"], 3),
            "end": round(seg[-1]["end"], 3),
            "key": canonical[0],
            "chords": names,
            "canonical_chords": canonical,
            "dur_beats": [round(d, 2) for d in durs],
            "suggested_card_beats": beats,
            "confidence": round(min(0.92, 0.70 + roots_ok * 0.03), 3),
        })
        if len(hints) >= 64:
            break
    return hints


def _duration_by_name_after(chords: List[Dict], start_idx: int, names: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    wanted = set(names)
    for c in chords[start_idx:start_idx + max(8, len(names) * 3)]:
        name = c["chord"]
        if name not in wanted or name in out:
            continue
        dur = _float(c.get("end")) - _float(c.get("time"))
        if 0.25 <= dur <= 12.0:
            out[name] = dur
        if len(out) == len(wanted):
            break
    return out


def _expand_intro_cycle(chords: List[Dict], hint: Dict) -> Tuple[List[Dict], Optional[Dict]]:
    if not hint or hint.get("type") != "free_time_long_intro":
        return chords, None
    cycle = [str(c) for c in (hint.get("suggested_cycle") or []) if c]
    if len(cycle) < 3:
        return chords, None
    span = hint.get("long_span") or []
    if len(span) != 2:
        return chords, None
    start, end = _float(span[0]), _float(span[1])
    idx = None
    for i, c in enumerate(chords):
        if abs(_float(c.get("time")) - start) < 0.08 and abs(_float(c.get("end")) - end) < 0.08:
            idx = i
            break
    if idx is None:
        return chords, None

    durations = _duration_by_name_after(chords, idx + 1, cycle)
    fallback = statistics.median(durations.values()) if durations else (end - start) / max(1, len(cycle) * 4)
    cursor = start
    generated: List[Dict] = []
    safety = 0
    while cursor < end - 0.1 and safety < 128:
        for name in cycle:
            if cursor >= end - 0.1:
                break
            dur = durations.get(name, fallback)
            item_end = min(end, cursor + dur)
            generated.append({
                "time": round(cursor, 3),
                "end": round(item_end, 3),
                "chord": name,
                "display_beats": int(hint.get("suggested_card_beats") or 4),
                "global_arbiter": "free-time-intro-cycle",
            })
            cursor = item_end
            safety += 1

    if len(generated) < 2:
        return chords, None
    out = chords[:idx] + generated + chords[idx + 1:]
    return out, {
        "type": "expand_intro_cycle",
        "before": 1,
        "after": len(generated),
        "span": [round(start, 3), round(end, 3)],
        "cycle": cycle,
    }


def _apply_two_beat_grammar(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    if not candidates:
        return chords, []
    out = [dict(c) for c in chords]
    corrections: List[Dict] = []
    used = set()
    target_degrees = ["I", "III", "VIm", "I", "II", "V", "V7"]
    for cand in candidates:
        if cand.get("degrees") != target_degrees:
            continue
        names = cand.get("chords") or []
        beats = cand.get("suggested_card_beats") or []
        if len(names) != len(beats):
            continue
        start = _float(cand.get("start"))
        end = _float(cand.get("end"))
        match_idx = None
        for i in range(0, len(out) - len(names) + 1):
            if any(j in used for j in range(i, i + len(names))):
                continue
            if [c.get("chord") for c in out[i:i + len(names)]] != names:
                continue
            if abs(_float(out[i].get("time")) - start) < 0.08 and abs(_float(out[i + len(names) - 1].get("end")) - end) < 0.08:
                match_idx = i
                break
        if match_idx is None:
            continue
        total_beats = sum(float(b) for b in beats)
        if total_beats <= 0 or end <= start:
            continue
        sec_per_unit = (end - start) / total_beats
        cursor = start
        replacement = []
        for name, b in zip(names, beats):
            item_end = end if len(replacement) == len(names) - 1 else cursor + float(b) * sec_per_unit
            replacement.append({
                **out[match_idx + len(replacement)],
                "time": round(cursor, 3),
                "end": round(item_end, 3),
                "chord": name,
                "display_beats": int(b),
                "global_arbiter": "two-beat-chorus-grammar",
            })
            cursor = item_end
        out[match_idx:match_idx + len(names)] = replacement
        used.update(range(match_idx, match_idx + len(names)))
        corrections.append({
            "type": "two_beat_grammar",
            "span": [round(start, 3), round(end, 3)],
            "chords": names,
            "display_beats": beats,
        })
    return out, corrections


def _minor_loop_group_matches(group: List[Dict], target: str, slot_idx: int) -> bool:
    if not group:
        return False
    target_root = _chord_root_pc(target)
    if target_root is None:
        return False
    roots = [_chord_root_pc(c.get("chord") or "") for c in group]
    if target_root in roots:
        return True
    # Common BTC confusions in minor pop loops:
    # bIII (Ab) may appear as v (Cm), and bVI (Db) may be stretched while the
    # bIII arrives late. Let the grid, not the transient chord label, decide.
    if slot_idx == 3:
        return True
    # V7 often loses the seventh and arrives as plain V.
    if slot_idx == 5 and any(r == target_root for r in roots):
        return True
    return False


def _apply_minor_pop_loop_grammar(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    if not candidates:
        return chords, []
    out = [dict(c) for c in chords]
    corrections: List[Dict] = []
    for cand in candidates:
        canonical = [str(c) for c in (cand.get("canonical_chords") or []) if c]
        beats = [int(b) for b in (cand.get("suggested_card_beats") or [])]
        if len(canonical) != 7 or len(beats) != 7:
            continue
        start = _float(cand.get("start"))
        end = _float(cand.get("end"))
        names = cand.get("chords") or []
        match_idx = None
        for i in range(0, len(out) - len(names) + 1):
            if [c.get("chord") for c in out[i:i + len(names)]] != names:
                continue
            if abs(_float(out[i].get("time")) - start) < 0.12 and abs(_float(out[i + len(names) - 1].get("end")) - end) < 0.12:
                match_idx = i
                break
        if match_idx is None:
            continue
        groups = [[out[match_idx + idx]] for idx in range(len(canonical))]
        matches = sum(
            1
            for idx, (group, target) in enumerate(zip(groups, canonical))
            if _minor_loop_group_matches(group, target, idx)
        )
        if matches < 6:
            continue
        changed = 0
        for idx, (target, b) in enumerate(zip(canonical, beats)):
            item = out[match_idx + idx]
            dur = _float(item.get("end"), _float(item.get("time"))) - _float(item.get("time"))
            # Leave long tonic cards to the long-card splitter; otherwise the
            # song-level rule would hide the useful 4+2 visual split.
            if idx in (0, 6) and dur > 2.4:
                item["split_display_beats"] = [4, 2]
                continue
            if item.get("chord") != target:
                item["chord"] = target
                changed += 1
            item["display_beats"] = b
            item["global_arbiter"] = "minor-pop-loop-grammar"
            changed += 1
        if not changed:
            continue
        corrections.append({
            "type": "minor_pop_loop_grammar",
            "span": [round(start, 3), round(end, 3)],
            "key": cand.get("key"),
            "chords": canonical,
            "display_beats": beats,
            "source_chords": cand.get("chords"),
        })
    return out, corrections


def _apply_minor_pop_passing_repairs(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    if not candidates:
        return chords, []
    first = candidates[0]
    key_name = str(first.get("key") or "")
    key_pc, _ = _root_quality(key_name)
    if key_pc is None:
        return chords, []
    out = [dict(c) for c in chords]
    corrections: List[Dict] = []
    v_minor_root = (key_pc + 7) % 12
    merge_targets = {
        (key_pc + 8) % 12: _chord_name_for_degree(key_pc, 8, "", True),
        (key_pc + 3) % 12: _chord_name_for_degree(key_pc, 3, "", True),
    }
    idx = 0
    merges = 0
    while idx < len(out) - 1:
        root, quality = _root_quality(out[idx].get("chord") or "")
        next_root = _chord_root_pc(out[idx + 1].get("chord") or "")
        if root == v_minor_root and _quality_family(quality) == "m" and next_root in merge_targets:
            item = dict(out[idx + 1])
            item["time"] = round(_float(out[idx].get("time")), 3)
            item["end"] = round(_float(out[idx + 1].get("end"), _float(out[idx + 1].get("time"))), 3)
            item["chord"] = merge_targets[next_root]
            item["display_beats"] = 2
            item["global_arbiter"] = "minor-pop-passing-repair"
            out[idx:idx + 2] = [item]
            merges += 1
            idx += 1
            continue
        idx += 1

    canonical_by_root = {
        (key_pc + 10) % 12: _chord_name_for_degree(key_pc, 10, "", True),
        (key_pc + 8) % 12: _chord_name_for_degree(key_pc, 8, "", True),
        (key_pc + 3) % 12: _chord_name_for_degree(key_pc, 3, "", True),
        (key_pc + 5) % 12: _chord_name_for_degree(key_pc, 5, "m7", True),
        (key_pc + 7) % 12: _chord_name_for_degree(key_pc, 7, "7", True),
    }
    first_start = _float(first.get("start"))
    last_end = max(_float(c.get("end"), _float(c.get("start"))) for c in candidates)
    normalized = 0
    for item in out:
        t = _float(item.get("time"))
        if t < first_start - 22.0 or t > last_end + 2.0:
            continue
        root, quality = _root_quality(item.get("chord") or "")
        if root not in canonical_by_root:
            continue
        if root == (key_pc + 7) % 12 and _quality_family(quality) == "m":
            continue
        target = canonical_by_root[root]
        if item.get("chord") != target:
            item["chord"] = target
            normalized += 1
        item["display_beats"] = 2
        item["global_arbiter"] = item.get("global_arbiter") or "minor-pop-loop-grammar"
    if merges or normalized:
        corrections.append({
            "type": "minor_pop_passing_repair",
            "key": key_name,
            "merged_passing_cards": merges,
            "normalized_cards": normalized,
        })
    return out, corrections


def _protect_cycle_continuation(chords: List[Dict], start: float, end: float, label: str) -> Tuple[List[Dict], Optional[Dict]]:
    out = [dict(c) for c in chords]
    changed = 0
    for c in out:
        c_start = _float(c.get("time"))
        c_end = _float(c.get("end"), c_start)
        if c_start < start - 0.05 or c_start >= end - 0.05:
            continue
        if c.get("global_arbiter"):
            continue
        if c_end <= c_start:
            continue
        c["display_beats"] = 4
        c["global_arbiter"] = label
        changed += 1
    if not changed:
        return chords, None
    return out, {"type": label, "span": [round(start, 3), round(end, 3)], "cards": changed, "display_beats": 4}


def _apply_modulated_cycles(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    if not candidates:
        return chords, []
    out = [dict(c) for c in chords]
    corrections = []
    for cand in candidates:
        names = cand.get("to_cycle") or []
        if len(names) < 3:
            continue
        start = _float(cand.get("time"))
        match_idx = None
        for i in range(0, len(out) - len(names) + 1):
            if [c.get("chord") for c in out[i:i + len(names)]] != names:
                continue
            if abs(_float(out[i].get("time")) - start) < 0.08:
                match_idx = i
                break
        if match_idx is None:
            continue
        for j in range(match_idx, match_idx + len(names)):
            out[j]["display_beats"] = 4
            out[j]["global_arbiter"] = "modulated-verse-cycle"
        corrections.append({
            "type": "modulated_cycle",
            "span": [round(_float(out[match_idx].get("time")), 3), round(_float(out[match_idx + len(names) - 1].get("end")), 3)],
            "from_key": cand.get("from_key"),
            "to_key": cand.get("to_key"),
            "chords": names,
            "display_beats": 4,
        })
    return out, corrections


def _transition_matches_at(chords: List[Dict], idx: int, key_pc: int) -> bool:
    if idx < 0 or idx + 7 > len(chords):
        return False
    seg = chords[idx:idx + 7]
    expected_roots = [
        key_pc,
        (key_pc + 4) % 12,
        (key_pc + 9) % 12,
        (key_pc + 7) % 12,
        (key_pc + 7) % 12,
        key_pc,
        (key_pc + 8) % 12,
    ]
    expected_families = ["", "m", "m", "", "7", "", ""]
    for item, root, family in zip(seg, expected_roots, expected_families):
        item_root, quality = _root_quality(item.get("chord") or "")
        if item_root is None or item_root != root:
            return False
        if family and _quality_family(quality) != family:
            return False
    if idx + 10 >= len(chords):
        return True
    # Extra confidence: the bridge should land immediately into the raised
    # tonic's verse grammar, e.g. Ab Fm Bbm Eb7 after G ... Eb.
    next_key = (key_pc + 1) % 12
    next_roots = [(next_key + x) % 12 for x in (0, 9, 2, 7)]
    observed = [_chord_root_pc(c.get("chord") or "") for c in chords[idx + 7:idx + 11]]
    return observed == next_roots


def _apply_modulation_transition(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Protect the tonic-to-raised-tonic bridge after a two-beat chorus.

    In 獨上西樓 the G-key chorus cadence continues with G Bm Em D D7 G Eb,
    where Eb is the pivot into the following Ab verse. The local splitter sees
    the long Em as two cards unless the song-level grammar claims it first.
    """
    target_degrees = ["I", "III", "VIm", "I", "II", "V", "V7"]
    out = [dict(c) for c in chords]
    corrections: List[Dict] = []
    starts: List[Tuple[int, int, Optional[str]]] = []
    for cand in candidates or []:
        if cand.get("degrees") != target_degrees:
            continue
        local_key = (cand.get("local_key") or {}).get("key")
        if local_key not in NOTE_TO_SEMI:
            continue
        key_pc = NOTE_TO_SEMI[local_key]
        start_after = _float(cand.get("end"))
        match_idx = None
        for i, c in enumerate(out):
            if abs(_float(c.get("time")) - start_after) < 0.12:
                match_idx = i
                break
        if match_idx is not None:
            starts.append((match_idx, key_pc, local_key))
    for i in range(0, max(0, len(out) - 10)):
        root = _chord_root_pc(out[i].get("chord") or "")
        if root is not None:
            starts.append((i, root, _root_name(root)))

    used_starts = set()
    for match_idx, key_pc, local_key in starts:
        if match_idx in used_starts or not _transition_matches_at(out, match_idx, key_pc):
            continue
        seg = out[match_idx:match_idx + 7]
        beats = [2, 2, 4, 2, 2, 2, 2]
        for item, beat_count in zip(seg, beats):
            item["display_beats"] = beat_count
            item["global_arbiter"] = "modulation-transition-grammar"
        used_starts.add(match_idx)
        corrections.append({
            "type": "modulation_transition",
            "span": [round(_float(seg[0].get("time")), 3), round(_float(seg[-1].get("end")), 3)],
            "from_key": local_key,
            "to_key": _root_name((key_pc + 1) % 12, prefer_flats=True),
            "chords": [c.get("chord") for c in seg],
            "display_beats": beats,
        })
    return out, corrections


def _group_matches_cycle_target(group: List[Dict], target: str) -> bool:
    if not group:
        return False
    target_root, target_quality = _root_quality(target)
    if target_root is None:
        return False
    if any(_chord_root_pc(c.get("chord") or "") != target_root for c in group):
        return False
    target_family = _quality_family(target_quality)
    first_name = group[0].get("chord") or ""
    if target_family == "m":
        return _same_minor_root(first_name, target) or any(_same_minor_root(c.get("chord") or "", target) for c in group)
    if target_family == "7":
        return any(_same_dominant_root(c.get("chord") or "", target) for c in group)
    return _same_root(first_name, target)


def _find_first_cycle_end(chords: List[Dict], names: List[str], start: float) -> Optional[float]:
    for i in range(0, len(chords) - len(names) + 1):
        if [c.get("chord") for c in chords[i:i + len(names)]] != names:
            continue
        if abs(_float(chords[i].get("time")) - start) < 0.12:
            return _float(chords[i + len(names) - 1].get("end"))
    return None


def _apply_modulated_cycle_repeats(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    if not candidates:
        return chords, []
    out = [dict(c) for c in chords]
    corrections: List[Dict] = []
    for cand in candidates:
        cycle = [str(c) for c in (cand.get("to_cycle") or []) if c]
        if len(cycle) < 3:
            continue
        first_end = _find_first_cycle_end(out, cycle, _float(cand.get("time")))
        if first_end is None:
            continue
        idx = 0
        while idx < len(out):
            if _float(out[idx].get("time")) < first_end - 0.05:
                idx += 1
                continue
            if not _same_root(out[idx].get("chord") or "", cycle[0]):
                idx += 1
                continue

            cursor = idx
            groups: List[List[Dict]] = []
            ok = True
            for target in cycle:
                if cursor >= len(out):
                    ok = False
                    break
                target_root = _chord_root_pc(target)
                if target_root is None or _chord_root_pc(out[cursor].get("chord") or "") != target_root:
                    ok = False
                    break
                group = [out[cursor]]
                cursor += 1
                while cursor < len(out) and _chord_root_pc(out[cursor].get("chord") or "") == target_root:
                    group.append(out[cursor])
                    cursor += 1
                if not _group_matches_cycle_target(group, target):
                    ok = False
                    break
                groups.append(group)

            if not ok or len(groups) != len(cycle):
                idx += 1
                continue

            consumed = cursor - idx
            if consumed <= len(cycle):
                idx += 1
                continue
            replacement: List[Dict] = []
            for target, group in zip(cycle, groups):
                item = dict(group[0])
                item["time"] = round(_float(group[0].get("time")), 3)
                item["end"] = round(_float(group[-1].get("end"), _float(group[-1].get("time"))), 3)
                item["chord"] = target
                item["display_beats"] = 4
                item["global_arbiter"] = "modulated-verse-cycle-repeat"
                replacement.append(item)
            out[idx:cursor] = replacement
            corrections.append({
                "type": "modulated_cycle_repeat",
                "span": [round(_float(replacement[0].get("time")), 3), round(_float(replacement[-1].get("end")), 3)],
                "from_key": cand.get("from_key"),
                "to_key": cand.get("to_key"),
                "chords": cycle,
                "consumed_cards": consumed,
                "display_beats": 4,
            })
            idx += len(replacement)
    return out, corrections


def _cycle_slot_duration(chords: List[Dict], names: List[str], start: float) -> Optional[float]:
    for i in range(0, len(chords) - len(names) + 1):
        if [c.get("chord") for c in chords[i:i + len(names)]] != names:
            continue
        if abs(_float(chords[i].get("time")) - start) >= 0.12:
            continue
        durs = [_float(c.get("end"), _float(c.get("time"))) - _float(c.get("time")) for c in chords[i:i + len(names)]]
        valid = [d for d in durs if 1.0 <= d <= 8.0]
        if len(valid) == len(names):
            return statistics.median(valid)
    return None


def _target_root_for_cycle_name(name: str) -> Optional[int]:
    return _chord_root_pc(name)


def _slot_target_start(group: List[Dict], target: str) -> Optional[float]:
    target_root = _target_root_for_cycle_name(target)
    if target_root is None:
        return None
    for c in group:
        if _chord_root_pc(c.get("chord") or "") == target_root:
            return _float(c.get("time"))
    return None


def _cards_in_slot(chords: List[Dict], start: float, end: float) -> List[Dict]:
    return [
        c for c in chords
        if _float(c.get("time")) < end - 0.05 and _float(c.get("end"), _float(c.get("time"))) > start + 0.05
    ]


def _apply_modulated_grid_repairs(chords: List[Dict], candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Use the first confirmed modulated cycle as a four-beat grid.

    Later phrases can contain passing chords or quality flips inside one card
    (Ab/C7, Fm/Ab, Eb/Eb7/Eb). If their roots still line up on the established
    Ab-cycle grid, canonicalize the card grammar for display.
    """
    if not candidates:
        return chords, []
    out = [dict(c) for c in chords]
    corrections: List[Dict] = []
    for cand in candidates:
        cycle = [str(c) for c in (cand.get("to_cycle") or []) if c]
        if len(cycle) != 4:
            continue
        first_end = _find_first_cycle_end(out, cycle, _float(cand.get("time")))
        slot_dur = _cycle_slot_duration(out, cycle, _float(cand.get("time")))
        if first_end is None or not slot_dur or slot_dur <= 0:
            continue
        patterns = [
            ("modulated-grid-cycle", cycle),
            ("modulated-grid-ending", [cycle[0], cycle[1], cycle[3], cycle[0]]),
        ]
        idx = 0
        while idx < len(out):
            start = _float(out[idx].get("time"))
            if (
                start < first_end - 0.05
                or out[idx].get("global_arbiter")
                or not _same_root(out[idx].get("chord") or "", cycle[0])
            ):
                idx += 1
                continue

            matched = False
            for label, pattern in patterns:
                slot_starts = [start + slot_dur * n for n in range(len(pattern))]
                slot_ends = [start + slot_dur * (n + 1) for n in range(len(pattern))]
                groups = [_cards_in_slot(out, s, e) for s, e in zip(slot_starts, slot_ends)]
                target_starts = [_slot_target_start(group, target) for group, target in zip(groups, pattern)]
                if sum(1 for v in target_starts if v is not None) < len(pattern):
                    continue
                # Keep the repair local to one phrase. If the next target start
                # drifted too far, a ritardando has begun and local timing wins.
                drift = [abs(ts - ss) for ts, ss in zip(target_starts, slot_starts) if ts is not None]
                if drift and max(drift) > max(0.45, slot_dur * 0.22):
                    continue

                phrase_end = start + slot_dur * len(pattern)
                consume_end = phrase_end - 0.05
                cursor = idx
                while cursor < len(out) and _float(out[cursor].get("time")) < consume_end:
                    cursor += 1
                if cursor <= idx + len(pattern):
                    continue
                next_start = _float(out[cursor].get("time")) if cursor < len(out) else None

                starts = [round(float(ts), 3) for ts in target_starts]
                replacement: List[Dict] = []
                for n, target in enumerate(pattern):
                    item = dict(groups[n][0] if groups[n] else out[idx])
                    item["time"] = starts[n]
                    item_end = starts[n + 1] if n + 1 < len(starts) else phrase_end
                    if n + 1 == len(starts) and next_start is not None and starts[n] < next_start < phrase_end + 0.3:
                        item_end = min(item_end, next_start)
                    item["end"] = round(item_end, 3)
                    item["chord"] = target
                    item["display_beats"] = 4
                    item["global_arbiter"] = label
                    replacement.append(item)
                out[idx:cursor] = replacement
                corrections.append({
                    "type": label.replace("-", "_"),
                    "span": [replacement[0]["time"], replacement[-1]["end"]],
                    "from_key": cand.get("from_key"),
                    "to_key": cand.get("to_key"),
                    "chords": pattern,
                    "consumed_cards": cursor - idx,
                    "display_beats": 4,
                })
                idx += len(replacement)
                matched = True
                break
            if not matched:
                idx += 1
    return out, corrections


def _stable_downbeat_gap(downbeats: List[float], bpm: float = 0.0) -> Optional[Dict]:
    pts = sorted(float(v) for v in downbeats if v is not None)
    gaps = [pts[i + 1] - pts[i] for i in range(len(pts) - 1) if pts[i + 1] - pts[i] > 0.5]
    if len(gaps) < 8:
        return None
    med = statistics.median(gaps)
    if med <= 0:
        return None
    mean = statistics.mean(gaps)
    cv = statistics.pstdev(gaps) / mean if mean > 0 and len(gaps) > 1 else 0.0
    if cv > 0.18:
        return None
    factor = 1
    # Acoustic guitar ballads often make beat trackers lock to triplet/sub-beat
    # pulses. When stored BPM is implausibly high but downbeats are steady at a
    # half-bar distance, treat two downbeat gaps as the musical 4/4 bar.
    if bpm >= 150.0 and 1.35 <= med <= 2.4:
        factor = 2
    elif bpm >= 80.0 and 1.35 <= med <= 2.4:
        beats_per_gap = bpm * med / 60.0
        if 2.55 <= beats_per_gap <= 3.35:
            factor = 2
    return {"raw_gap": med, "bar_gap": med * factor, "factor": factor, "cv": cv}


def _compound_12_8_half_bar_gap(chords: List[Dict], downbeats: List[float], bpm: float = 0.0) -> Optional[Dict]:
    """Detect slow 12/8 ballads tracked at triplet-subdivision BPM.

    A 60 BPM dotted-quarter ballad may be stored around 180 BPM. In that
    representation, a strong downbeat cluster near 6 detected ticks is often a
    half-bar marker, while the readable player grammar is a 4-pulse bar twice
    as long. Require both the 6-subdivision cluster and chord durations that
    support the doubled bar so real fast 6/8 songs are not pulled down.
    """
    if bpm < 150.0 or bpm > 210.0:
        return None
    pts = sorted(float(v) for v in downbeats if v is not None)
    gaps = [pts[i + 1] - pts[i] for i in range(len(pts) - 1) if pts[i + 1] - pts[i] > 0.5]
    candidates = [g for g in gaps if 1.45 <= g <= 2.45]
    if len(candidates) < 12:
        return None
    med = statistics.median(candidates)
    if med <= 0:
        return None
    spb = 60.0 / bpm
    beats_per_gap = med / spb if spb > 0 else 0.0
    if not (5.2 <= beats_per_gap <= 6.8):
        return None
    tight = [g for g in candidates if abs(g - med) <= max(0.18, med * 0.10)]
    if len(tight) < max(10, int(len(gaps) * 0.30)):
        return None

    bar_gap = med * 2.0
    half_like = 0
    full_like = 0
    double_like = 0
    for c in chords:
        start = _float(c.get("time"))
        end = _float(c.get("end"), start)
        dur = end - start
        if dur <= 0.5:
            continue
        ratio = dur / bar_gap
        if 0.32 <= ratio <= 0.70:
            half_like += 1
        elif 0.72 <= ratio <= 1.28:
            full_like += 1
        elif 1.65 <= ratio <= 2.35:
            double_like += 1
    structural = full_like + double_like
    observed = half_like + full_like + double_like
    min_structural = max(5, int(observed * 0.25))
    if structural < min_structural or structural < half_like * 0.55:
        return None

    mean = statistics.mean(tight)
    cv = statistics.pstdev(tight) / mean if len(tight) > 1 and mean > 0 else 0.0
    return {
        "raw_gap": med,
        "bar_gap": bar_gap,
        "factor": 2,
        "cv": cv,
        "compound_12_8": True,
        "beats_per_gap": beats_per_gap,
        "half_like": half_like,
        "full_like": full_like,
        "double_like": double_like,
    }


def _merge_same_root_full_bar_fragments(chords: List[Dict], bar_gap: float) -> Tuple[List[Dict], int]:
    out: List[Dict] = []
    idx = 0
    merged = 0
    while idx < len(chords):
        cur = dict(chords[idx])
        if idx + 1 >= len(chords):
            out.append(cur)
            break
        nxt = chords[idx + 1]
        cur_start = _float(cur.get("time"))
        cur_end = _float(cur.get("end"), cur_start)
        next_end = _float(nxt.get("end"), _float(nxt.get("time")))
        combined = next_end - cur_start
        if (
            _same_root(cur.get("chord") or "", nxt.get("chord") or "")
            and 0.72 <= combined / bar_gap <= 1.28
            and cur_end <= _float(nxt.get("time")) + 0.08
        ):
            cur["end"] = round(next_end, 3)
            cur["display_beats"] = 4
            cur["display_arbiter"] = "stable-downbeat-merge"
            cur["global_arbiter"] = cur.get("global_arbiter") or "stable-downbeat-quantize"
            out.append(cur)
            merged += 1
            idx += 2
            continue
        out.append(cur)
        idx += 1
    return out, merged


def _looks_like_half_bar_downbeats(chords: List[Dict], raw_gap: float) -> bool:
    if raw_gap <= 0:
        return False
    ratios = []
    for c in chords:
        start = _float(c.get("time"))
        end = _float(c.get("end"), start)
        dur = end - start
        if dur <= 0.5:
            continue
        ratios.append(dur / raw_gap)
    if len(ratios) < 8:
        return False
    full_bar_like = sum(1 for r in ratios if 1.65 <= r <= 2.35)
    half_bar_like = sum(1 for r in ratios if 0.78 <= r <= 1.25)
    return full_bar_like >= 8 and full_bar_like >= half_bar_like * 1.4


def _split_stable_full_bar_holds(chords: List[Dict], bar_gap: float) -> Tuple[List[Dict], int]:
    repeated_near_bar_one_beat_tails = 0
    if bar_gap > 0:
        for chord in chords:
            start = _float(chord.get("time"))
            end = _float(chord.get("end"), start)
            dur = end - start
            if dur <= 0 or chord.get("display_beats"):
                continue
            bars_float = dur / bar_gap
            if not (1.22 <= bars_float <= 1.30):
                continue
            tail = max(0.0, end - (start + bar_gap))
            if tail <= 0:
                continue
            tail_beats = max(1, min(3, int(round((tail / bar_gap) * 4.0))))
            if tail_beats == 1:
                repeated_near_bar_one_beat_tails += 1

    out: List[Dict] = []
    split_count = 0
    for chord in chords:
        start = _float(chord.get("time"))
        end = _float(chord.get("end"), start)
        dur = end - start
        if dur <= 0 or chord.get("display_beats"):
            out.append(dict(chord))
            continue
        bars_float = dur / bar_gap if bar_gap > 0 else 0.0
        if 1.22 <= bars_float <= 1.68:
            first_end = start + bar_gap
            tail = max(0.0, end - first_end)
            tail_beats = max(1, min(3, int(round((tail / bar_gap) * 4.0)))) if tail > 0 else 1
            # Fast rock/pop songs can overshoot a steady short bar by ~20-30%
            # without reading like a real 4+1 split. Keep those as a single
            # card so we don't spray repeated same-chord 4+1 tails across the
            # section.
            # Some stable 4/4 pop ballads drift just ~1 beat past the barline
            # over and over. Showing a whole row of same-chord 4+1 cards is
            # visually worse than quantizing those slight overshoots back to 4.
            # Keep the original fast-rock shortcut, and also absorb these
            # repeated near-bar tails when they recur across the song.
            if (
                tail_beats == 1
                and bars_float < 1.30
                and (
                    bar_gap < 1.5
                    or repeated_near_bar_one_beat_tails >= 4
                )
            ):
                item = dict(chord)
                item["display_beats"] = 4
                item["display_arbiter"] = "stable-downbeat-near-bar-hold"
                item["global_arbiter"] = item.get("global_arbiter") or "stable-downbeat-quantize"
                out.append(item)
                continue
            for seg_start, seg_end, beats in (
                (start, first_end, 4),
                (first_end, end, tail_beats),
            ):
                item = dict(chord)
                item["time"] = round(seg_start, 3)
                item["end"] = round(seg_end, 3)
                item["display_beats"] = beats
                item["display_arbiter"] = "stable-downbeat-bar-plus-half"
                item["global_arbiter"] = item.get("global_arbiter") or "stable-downbeat-quantize"
                out.append(item)
            split_count += 1
            continue
        bars = int(round(bars_float))
        if bars < 2 or bars > 8 or abs(bars_float - bars) > 0.28:
            out.append(dict(chord))
            continue
        step = dur / bars
        for i in range(bars):
            item = dict(chord)
            item["time"] = round(start + step * i, 3)
            item["end"] = round(end if i == bars - 1 else start + step * (i + 1), 3)
            item["display_beats"] = 4
            item["display_arbiter"] = "stable-downbeat-long-hold"
            item["global_arbiter"] = item.get("global_arbiter") or "stable-downbeat-quantize"
            out.append(item)
        split_count += 1
    return out, split_count


def _apply_downbeat_display_quantization(chords: List[Dict], downbeats: List[float], bpm: float = 0.0) -> Tuple[List[Dict], Optional[Dict]]:
    gap_info = _stable_downbeat_gap(downbeats, bpm)
    if not gap_info:
        gap_info = _compound_12_8_half_bar_gap(chords, downbeats, bpm)
    if not gap_info:
        return chords, None
    if gap_info["factor"] == 1 and _looks_like_half_bar_downbeats(chords, gap_info["raw_gap"]):
        gap_info = {**gap_info, "bar_gap": gap_info["raw_gap"] * 2, "factor": 2}
    bar_gap = gap_info["bar_gap"]
    out, merged = _merge_same_root_full_bar_fragments([dict(c) for c in chords], bar_gap)
    out, split_holds = _split_stable_full_bar_holds(out, bar_gap)
    changed = 0
    for c in out:
        if c.get("display_beats"):
            continue
        start = _float(c.get("time"))
        end = _float(c.get("end"), start)
        dur = end - start
        if dur <= 0:
            continue
        ratio = dur / bar_gap
        beat_count = None
        if 0.32 <= ratio <= 0.70:
            beat_count = 2
        elif 0.80 <= ratio <= 1.36:
            beat_count = 4
        if beat_count:
            c["display_beats"] = beat_count
            c["display_arbiter"] = "stable-downbeat-quantize"
            c["global_arbiter"] = c.get("global_arbiter") or "stable-downbeat-quantize"
            changed += 1
    if not changed and not merged and not split_holds:
        return chords, None
    result = {
        "type": "stable_downbeat_display_quantize",
        "bar_gap": round(bar_gap, 3),
        "raw_gap": round(gap_info["raw_gap"], 3),
        "gap_factor": gap_info["factor"],
        "cards": changed,
        "merged_cards": merged,
        "split_long_holds": split_holds,
    }
    if gap_info.get("compound_12_8"):
        result["type"] = "compound_12_8_display_quantize"
        result["beats_per_gap"] = round(gap_info.get("beats_per_gap") or 0.0, 3)
        result["half_like_cards"] = gap_info.get("half_like", 0)
        result["full_like_cards"] = gap_info.get("full_like", 0)
        result["double_like_cards"] = gap_info.get("double_like", 0)
    return out, result


def _estimate_display_bpm(chords: List[Dict]) -> Optional[Dict]:
    values = []
    for c in chords:
        beats = _float(c.get("display_beats"))
        if beats <= 0:
            continue
        start = _float(c.get("time"))
        end = _float(c.get("end"), start)
        dur = end - start
        if dur <= 0.2:
            continue
        bpm = 60.0 * beats / dur
        if _DISPLAY_BPM_MIN <= bpm <= _DISPLAY_BPM_MAX:
            # Prefer stable groove grammar over free-time intro cards.
            weight = 3.0 if c.get("global_arbiter") == "two-beat-chorus-grammar" else 1.0
            values.append((bpm, weight))
    if not values:
        return None
    expanded = []
    for bpm, weight in values:
        expanded.extend([bpm] * int(weight))
    median_bpm = statistics.median(expanded)
    return {
        "bpm": round(median_bpm, 1),
        "source": "global-arbiter-display-beats",
        "confidence": 0.82 if any(w >= 3.0 for _, w in values) else 0.68,
    }


def apply_global_structure_corrections(chord_data: Dict, meta: Optional[Dict] = None) -> Dict:
    """Apply high-confidence global corrections to the serve-time payload."""
    if not isinstance(chord_data, dict):
        return chord_data
    meta = meta or analyze_global_structure(chord_data)
    explicit_meter = chord_data.get("meter_correction") or {}
    preserve_explicit_meter_cards = bool(
        explicit_meter.get("applied")
        and chord_data.get("time_signature")
        and chord_data.get("display_bpm")
    )
    chords = _dedupe_names(
        chord_data.get("chords") or [],
        merge_consecutive=not preserve_explicit_meter_cards,
    )
    corrections: List[Dict] = []
    for hint in meta.get("hints") or []:
        if hint.get("type") == "free_time_long_intro" and hint.get("confidence", 0) >= 0.75:
            chords, corr = _expand_intro_cycle(chords, hint)
            if corr:
                corrections.append(corr)
                target_degrees = ["I", "III", "VIm", "I", "II", "V", "V7"]
                two_starts = [
                    _float(c.get("start"))
                    for c in (meta.get("two_beat_grammar_candidates") or [])
                    if c.get("degrees") == target_degrees
                ]
                next_two = min(two_starts or [corr["span"][1]])
                chords, cont = _protect_cycle_continuation(chords, corr["span"][1], next_two, "free-time-cycle-continuation")
                if cont:
                    corrections.append(cont)
    chords, mod_corrections = _apply_modulated_cycles(chords, meta.get("modulated_cycle_candidates") or [])
    corrections.extend(mod_corrections)
    chords, grammar_corrections = _apply_two_beat_grammar(chords, meta.get("two_beat_grammar_candidates") or [])
    corrections.extend(grammar_corrections)
    chords, minor_loop_corrections = _apply_minor_pop_loop_grammar(chords, meta.get("minor_pop_loop_candidates") or [])
    corrections.extend(minor_loop_corrections)
    chords, minor_passing_corrections = _apply_minor_pop_passing_repairs(chords, meta.get("minor_pop_loop_candidates") or [])
    corrections.extend(minor_passing_corrections)
    chords, transition_corrections = _apply_modulation_transition(chords, meta.get("two_beat_grammar_candidates") or [])
    corrections.extend(transition_corrections)
    chords, repeat_corrections = _apply_modulated_cycle_repeats(chords, meta.get("modulated_cycle_candidates") or [])
    corrections.extend(repeat_corrections)
    chords, grid_corrections = _apply_modulated_grid_repairs(chords, meta.get("modulated_cycle_candidates") or [])
    corrections.extend(grid_corrections)
    if preserve_explicit_meter_cards:
        meta["display_quantization_skipped"] = {
            "reason": "explicit-meter-card-grid",
            "time_signature": chord_data.get("time_signature"),
        }
    else:
        chords, quant_correction = _apply_downbeat_display_quantization(
            chords,
            [_float(v) for v in (chord_data.get("downbeats") or [])],
            _float(chord_data.get("bpm")),
        )
        if quant_correction:
            corrections.append(quant_correction)
    if corrections:
        chord_data["chords"] = chords
        meta["corrections"] = corrections
        meta["rewritten"] = True
    elif preserve_explicit_meter_cards:
        chord_data["chords"] = chords
        meta["rewritten"] = False
    else:
        meta["rewritten"] = False
    display_bpm = _estimate_display_bpm(chords)
    explicit_meter = chord_data.get("meter_correction") or {}
    preserve_display_bpm = bool(
        explicit_meter.get("applied")
        and chord_data.get("display_bpm")
        and chord_data.get("time_signature")
    )
    if display_bpm and corrections and not preserve_display_bpm:
        chord_data["display_bpm"] = display_bpm["bpm"]
        chord_data["bpm_label"] = f"BPM: {round(display_bpm['bpm'])}"
        meta["display_bpm"] = display_bpm
    elif display_bpm and corrections and preserve_display_bpm:
        meta["display_bpm_skipped"] = {
            "reason": "explicit-meter-display-bpm",
            "candidate": display_bpm,
            "preserved_bpm": chord_data.get("display_bpm"),
        }
    chord_data["global_arbiter_meta"] = meta
    return chord_data


def analyze_global_structure(chord_data: Dict) -> Dict:
    chords = _dedupe_names(chord_data.get("chords") or [])
    beats = [_float(v) for v in (chord_data.get("beats") or [])]
    downbeats = [_float(v) for v in (chord_data.get("downbeats") or [])]
    bpm = _float(chord_data.get("bpm"))
    meta: Dict = {"applied": False, "version": "global-arbiter-v0", "hints": []}
    if len(chords) < 4:
        meta["reason"] = "too-few-chords"
        return meta

    first_end = _float(chords[0].get("end"))
    intro_beat = _beat_stats(beats, 0.0, first_end)
    intro_downbeat = _beat_stats(downbeats, 0.0, first_end)
    meta["intro_beat_confidence"] = {
        "beats": intro_beat,
        "downbeats": intro_downbeat,
        "low_confidence": bool(
            (intro_beat.get("cv") is not None and intro_beat["cv"] >= _LOW_CONF_CV)
            or (intro_beat.get("median_gap") is not None and intro_beat["median_gap"] >= _LOW_CONF_MIN_GAP_SEC)
        ),
    }

    intro = _find_intro_cycle(chords)
    if intro:
        meta["hints"].append(intro)

    meta["local_key_windows"] = _window_key_map(chords)
    if len(meta["local_key_windows"]) >= 2:
        changes = []
        prev = meta["local_key_windows"][0]
        for cur in meta["local_key_windows"][1:]:
            if cur["key"] != prev["key"]:
                changes.append({"time": cur["start"], "from": prev["key"], "to": cur["key"], "confidence": min(prev["score"], cur["score"])})
            prev = cur
        if changes:
            meta["modulation_candidates"] = changes

    if intro:
        mod_cycles = _find_modulated_cycles(chords, intro["suggested_cycle"])
        if mod_cycles:
            meta["modulated_cycle_candidates"] = mod_cycles

    two_beat = _find_two_beat_grammar(chords, bpm)
    if two_beat:
        meta["two_beat_grammar_candidates"] = two_beat

    minor_pop = _find_minor_pop_loop_grammar(chords, bpm)
    if minor_pop:
        meta["minor_pop_loop_candidates"] = minor_pop

    meta["applied"] = bool(
        meta["hints"]
        or meta.get("modulation_candidates")
        or meta.get("two_beat_grammar_candidates")
        or meta.get("minor_pop_loop_candidates")
    )
    meta["reason"] = "ok" if meta["applied"] else "no-global-pattern"
    return meta


def maybe_analyze_global_structure_for_serve(chord_data: Dict) -> Dict:
    if not isinstance(chord_data, dict):
        return chord_data
    return apply_global_structure_corrections(chord_data, analyze_global_structure(chord_data))
