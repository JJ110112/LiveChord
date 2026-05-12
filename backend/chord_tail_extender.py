"""Serve-time tail completion for chord charts that stop before the beat grid.

Some cached charts, especially older MIDI imports or interrupted BTC runs, end
well before the detected beat/downbeat tail. The player then has no chord cards
for the outro. This module repairs only the response payload: when the last
chord ends far before the last detected beat, repeat a clearly detected suffix
progression; if no suffix progression is evident, extend the final chord.
"""

from __future__ import annotations

from typing import Dict, List, Optional


_MIN_TAIL_GAP_SEC = 8.0
_MIN_TAIL_GAP_RATIO = 0.05
_MAX_PATTERN_LEN = 8
_MIN_PATTERN_LEN = 2


def _target_tail_end(chord_data: Dict) -> Optional[float]:
    values = []
    for key in ("beats", "downbeats"):
        try:
            vals = [float(v) for v in (chord_data.get(key) or []) if v is not None]
        except Exception:
            vals = []
        if vals:
            values.append(max(vals))
    return max(values) if values else None


def _name(chord: Dict) -> str:
    return str(chord.get("chord") or chord.get("name") or "").strip()


def _find_repeating_suffix(chords: List[Dict]) -> Optional[List[Dict]]:
    """Return the shortest suffix pattern whose names repeat immediately."""
    n = len(chords)
    for size in range(_MIN_PATTERN_LEN, min(_MAX_PATTERN_LEN, n // 2) + 1):
        prev = chords[n - 2 * size:n - size]
        cur = chords[n - size:n]
        if [_name(c) for c in prev] == [_name(c) for c in cur]:
            if all((float(c.get("end", 0)) - float(c.get("time", 0))) > 0.1 for c in cur):
                return [dict(c) for c in cur]
    return None


def _append_pattern(chords: List[Dict], pattern: List[Dict], target_end: float) -> List[Dict]:
    out = [dict(c) for c in chords]
    cursor = float(out[-1].get("end", out[-1].get("time", 0.0)))
    added = 0
    while cursor < target_end - 0.05 and added < 512:
        for src in pattern:
            if cursor >= target_end - 0.05:
                break
            dur = max(0.1, float(src.get("end", 0.0)) - float(src.get("time", 0.0)))
            end = min(target_end, cursor + dur)
            item = dict(src)
            item["time"] = round(cursor, 3)
            item["end"] = round(end, 3)
            item["tail_fill"] = True
            out.append(item)
            cursor = end
            added += 1
    return out


def maybe_extend_tail_for_serve(chord_data: Dict) -> Dict:
    chords = chord_data.get("chords") or []
    if not isinstance(chords, list) or not chords:
        return chord_data
    last = chords[-1]
    try:
        last_end = float(last.get("end", last.get("time", 0.0)))
    except Exception:
        return chord_data
    target_end = _target_tail_end(chord_data)
    if target_end is None or target_end <= last_end:
        return chord_data
    gap = target_end - last_end
    if gap <= max(_MIN_TAIL_GAP_SEC, target_end * _MIN_TAIL_GAP_RATIO):
        return chord_data

    pattern = _find_repeating_suffix(chords)
    if pattern:
        new_chords = _append_pattern(chords, pattern, target_end)
        mode = f"repeat-{len(pattern)}"
    else:
        new_chords = [dict(c) for c in chords]
        new_chords[-1]["end"] = round(target_end, 3)
        new_chords[-1]["tail_fill"] = True
        mode = "extend-last"

    chord_data["chords"] = new_chords
    chord_data["tail_fill_meta"] = {
        "applied": True,
        "mode": mode,
        "before": len(chords),
        "after": len(new_chords),
        "last_end_before": round(last_end, 3),
        "target_end": round(target_end, 3),
        "gap_sec": round(gap, 3),
    }
    return chord_data
