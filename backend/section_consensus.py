"""Cross-section chord consensus (serve-time, non-destructive).

When a human labelled three or more sections with the same type (主歌 x4),
those sections carry the same tune; a different arrangement under one of
them makes BTC rename the same harmony (Gm7b5 → Bbm6, C7 → Edim7,
Dbmaj7 → Fm). Each section is sampled on a half-bar grid scaled to its own
length, and per slot the majority chord across sections is found.

Rule — deliberately conservative (see feedback_chord_noise_filter_aggressiveness):
  * Only runs on human sections (never on algorithmic ones), groups of >= 3
    sections whose lengths are within ±20 % of the group median.
  * A slot's majority needs >= 2 votes and a strict plurality. A slot with
    a strict (> half) majority the chord is incompatible with blocks the
    rename; a slot with only a plurality abstains.
  * A minority chord is replaced only when its pitch classes are a subset of
    the majority chord's (a dominant 7th also allows its b9, so the rootless
    dim7 counts). Cm vs Eb or Bbm7 vs Eb are real substitutions → kept.
  * A chord event is renamed only when every non-abstaining slot it holds
    votes for the same replacement.

Stamps ``section_consensus_meta`` = {applied, replaced, examples, groups}.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from chord_table import get_chord_notes, root_to_semitone, parse_chord

_MAX_EXAMPLES = 12
_MIN_GROUP = 3
_MAX_SHIFT = 2
_MAX_LEN_DEV = 0.2   # sibling sections must be within ±20 % of the median length


def _pcs(name: str) -> Optional[frozenset]:
    try:
        notes = get_chord_notes(name)
        return frozenset(root_to_semitone(n) % 12 for n in notes) if notes else None
    except Exception:
        return None


def _compatible(minor: str, major: str) -> bool:
    a, b = _pcs(minor), _pcs(major)
    if not a or not b:
        return False
    root, quality, _ = parse_chord(major)
    if root and quality == "7":   # C7 also covers C7b9 → rootless Edim7
        b = b | {(root_to_semitone(root) + 1) % 12}
    return a <= b


def _bar_seconds(data: Dict) -> float:
    bpm = data.get("bpm") or 0
    nominal = 240.0 / bpm if bpm else 2.0
    grid = data.get("bars") or data.get("downbeats") or []
    if len(grid) >= 4:
        gaps = sorted(b - a for a, b in zip(grid, grid[1:]) if b > a)
        # downbeats[] sometimes carries half-bar or beat pulses → trust only bar-sized gaps
        if gaps and gaps[len(gaps) // 2] >= 0.6 * nominal:
            return gaps[len(gaps) // 2]
    return nominal


def _dominant(chords: List[Dict], s0: float, s1: float) -> Optional[int]:
    best, best_ov = None, 0.0
    for i, c in enumerate(chords):
        ov = min(c.get("end", c["time"]), s1) - max(c["time"], s0)
        if ov > best_ov:
            best, best_ov = i, ov
    return best


def _cost(x: Optional[str], y: Optional[str]) -> float:
    if x is None or y is None:
        return 1.0
    if x == y:
        return 0.0
    return 0.5 if _compatible(x, y) or _compatible(y, x) else 1.0


def _dtw_map(ref: List[Optional[str]], other: List[Optional[str]]) -> Dict[int, int]:
    """Subsequence DTW: ref slot k → index into `other` (free start/end in
    `other`, which carries _MAX_SHIFT slack slots on both sides). Each ref slot
    takes the first other-slot on the path; a band keeps it local."""
    INF = float("inf")
    n, m = len(ref), len(other)
    D = [[INF] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        D[0][j] = 0.0
    for k in range(1, n + 1):
        for j in range(max(1, k - 1), min(m, k + 2 * _MAX_SHIFT + 1) + 1):
            D[k][j] = _cost(ref[k - 1], other[j - 1]) + min(D[k - 1][j - 1], D[k - 1][j] + 0.2, D[k][j - 1] + 0.2)
    j = min(range(1, m + 1), key=lambda x: D[n][x])
    k, out = n, {}
    while k > 0 and j > 0:
        out[k - 1] = j - 1
        steps = [(D[k - 1][j - 1], k - 1, j - 1), (D[k - 1][j] + 0.2, k - 1, j), (D[k][j - 1] + 0.2, k, j - 1)]
        _, k, j = min(steps)
    return out


def apply_consensus(chords: List[Dict], sections: List[Dict], bar_sec: float):
    groups: Dict[str, List[Dict]] = {}
    for s in sections or []:
        if float(s.get("end", 0)) > float(s.get("start", 0)):
            groups.setdefault(s.get("type") or s.get("label") or "", []).append(s)
    renames: Dict[int, str] = {}
    meta_groups = {}
    half = max(0.25, bar_sec / 2)
    for typ, secs in groups.items():
        durs = sorted(float(s["end"]) - float(s["start"]) for s in secs)
        med = durs[len(durs) // 2] if durs else 0
        # A section much shorter/longer than its siblings is not the same
        # phrase (or its boundary is off) — scaling it onto the grid would
        # compare the wrong bars.
        secs = [s for s in secs if med and abs(float(s["end"]) - float(s["start"]) - med) <= _MAX_LEN_DEV * med]
        if len(secs) < _MIN_GROUP:
            meta_groups[typ] = {"sections": len(secs), "skipped": "fewer than %d same-length sections" % _MIN_GROUP}
            continue
        n = max(1, round(med / half))
        # Human boundaries are approximate and tempo drifts inside a section
        # (verse 3 at 66 BPM vs verse 1 at 60), so each section is aligned to
        # a reference section by DTW over slack-extended slots, not by a
        # single whole-section shift.
        seqs = []
        for s in secs:
            a0, d = float(s["start"]), float(s["end"]) - float(s["start"])
            step = d / n
            seqs.append([_dominant(chords, a0 + k * step, a0 + (k + 1) * step)
                         for k in range(-_MAX_SHIFT, n + _MAX_SHIFT)])
        nm = lambda i: chords[i]["chord"] if i is not None else None
        core = [[nm(i) for i in sq[_MAX_SHIFT:_MAX_SHIFT + n]] for sq in seqs]
        ref = max(range(len(secs)), key=lambda j: sum(x == y for o in range(len(secs)) if o != j
                                                     for x, y in zip(core[j], core[o])))
        grid = [[None] * len(secs) for _ in range(n)]
        for j in range(len(secs)):
            if j == ref:
                for k in range(n):
                    grid[k][j] = seqs[j][_MAX_SHIFT + k]
                continue
            for k, i in _dtw_map(core[ref], [nm(i) for i in seqs[j]]).items():
                grid[k][j] = seqs[j][i]
        votes: Dict[int, set] = {}   # chord idx → replacement names voted by its slots
        for row in grid:
            names = [chords[i]["chord"] if i is not None else None for i in row]
            counts = {x: names.count(x) for x in names if x}
            if not counts:
                continue
            ranked = sorted(counts.items(), key=lambda kv: -kv[1])
            maj, cnt = ranked[0]
            plurality = cnt >= 2 and (len(ranked) == 1 or ranked[1][1] < cnt)
            strict = plurality and cnt * 2 > len(secs)
            for i, nm in zip(row, names):
                if i is None:
                    continue
                if nm == maj or (strict and not _compatible(nm, maj)):
                    votes.setdefault(i, set()).add(nm)          # keep
                elif plurality and _compatible(nm, maj):
                    votes.setdefault(i, set()).add(maj)         # rename
                # else: no clear majority here → abstain
        changed = 0
        for i, v in votes.items():
            if len(v) == 1:
                new = next(iter(v))
                if new != chords[i]["chord"] and i not in renames:
                    renames[i] = new
                    changed += 1
        meta_groups[typ] = {"sections": len(secs), "slots": n, "renamed": changed}
    out, examples = [], []
    for i, c in enumerate(chords):
        if i in renames:
            examples.append({"time": round(c["time"], 2), "from": c["chord"], "to": renames[i]})
            c = {**c, "chord": renames[i], "consensus_from": c["chord"]}
        if out and out[-1]["chord"] == c["chord"] and abs(out[-1].get("end", 0) - c["time"]) < 0.05:
            out[-1] = {**out[-1], "end": c.get("end", c["time"])}
        else:
            out.append(c)
    meta = {"applied": bool(renames), "replaced": len(renames),
            "examples": examples[:_MAX_EXAMPLES], "groups": meta_groups}
    return out, meta


def snap_sections_to_bars(sections: List[Dict], bars: Optional[List[float]],
                          chords: Optional[List[Dict]] = None):
    """Serve-time only: move each inner human section boundary onto the
    nearest bar line when within half a bar (the beat-density regularizer
    rebuilds bars[], so boundaries drawn on the old grid sit off the new one).
    The first start and last end stay; a section is never shrunk below one
    bar. A boundary that sits on a chord change (within 0.15 s) is anchored
    by the harmony and stays — there the bar grid is the suspect (Countdown's
    bridge starts on a Bbm the rebuilt grid puts on beat 3). The saved
    annotation file is not touched. Returns (sections, meta)."""
    import bisect
    grid = sorted(float(b) for b in (bars or []))
    out = [dict(s) for s in sections or []]
    if len(grid) < 2 or len(out) < 2:
        return out, {"applied": False, "moved": 0}
    onsets = sorted(float(c.get("time", 0)) for c in chords or [])
    moved, anchored, examples = 0, 0, []
    for i in range(1, len(out)):
        t = float(out[i].get("start", 0))
        jo = bisect.bisect_left(onsets, t)
        if any(0 <= x < len(onsets) and abs(onsets[x] - t) <= 0.15 for x in (jo - 1, jo)):
            anchored += 1
            continue
        if abs(float(out[i - 1].get("end", t)) - t) > 0.05:   # not a shared boundary
            continue
        j = bisect.bisect_left(grid, t)
        cands = [k for k in (j - 1, j) if 0 <= k < len(grid)]
        k = min(cands, key=lambda x: abs(grid[x] - t))
        bar = (grid[min(k + 1, len(grid) - 1)] - grid[max(k - 1, 0)]) / max(1, min(k + 1, len(grid) - 1) - max(k - 1, 0))
        near = grid[k]
        if abs(near - t) < 0.01 or abs(near - t) >= 0.5 * bar - 1e-6:
            continue
        if near - float(out[i - 1]["start"]) < bar * 0.9 or float(out[i]["end"]) - near < bar * 0.9:
            continue
        out[i]["start"] = out[i - 1]["end"] = round(near, 3)
        moved += 1
        examples.append({"from": round(t, 2), "to": round(near, 2)})
    return out, {"applied": bool(moved), "moved": moved, "chord_anchored": anchored, "examples": examples[:10]}


def load_human_sections(data_dir: Path, username: Optional[str], song_hash: str) -> List[Dict]:
    if not username or not song_hash:
        return []
    f = Path(data_dir) / "users" / str(username) / "human_sections" / f"{song_hash}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("sections") or []
    except (OSError, ValueError):
        return []


def maybe_consensus_for_serve(chord_data: Dict, sections: List[Dict]) -> Dict:
    if not sections:
        return chord_data
    sections, _ = snap_sections_to_bars(sections, chord_data.get("bars") or chord_data.get("downbeats"),
                                        chord_data.get("chords"))
    try:
        new_chords, meta = apply_consensus(chord_data.get("chords") or [], sections, _bar_seconds(chord_data))
        if meta.get("applied"):
            chord_data["chords"] = new_chords
        chord_data["section_consensus_meta"] = meta
    except Exception as exc:  # never break serving
        chord_data["section_consensus_meta"] = {"applied": False, "reason": f"error: {exc}"}
    return chord_data
