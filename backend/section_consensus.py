"""Cross-section chord consensus (serve-time, non-destructive).

When a human labelled three or more sections with the same type (主歌 x4),
those sections carry the same tune; a different arrangement under one of
them makes BTC rename the same harmony (Gm7b5 → Bbm6, C7 → Edim7,
Dbmaj7 → Fm). Each section is sampled on a half-bar grid scaled to its own
length, and per slot the majority chord across sections is found.

Rule — deliberately conservative (see feedback_chord_noise_filter_aggressiveness):
  * Only runs on human sections (never on algorithmic ones), groups of >= 3.
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


def apply_consensus(chords: List[Dict], sections: List[Dict], bar_sec: float):
    groups: Dict[str, List[Dict]] = {}
    for s in sections or []:
        if float(s.get("end", 0)) > float(s.get("start", 0)):
            groups.setdefault(s.get("type") or s.get("label") or "", []).append(s)
    renames: Dict[int, str] = {}
    meta_groups = {}
    half = max(0.25, bar_sec / 2)
    for typ, secs in groups.items():
        if len(secs) < _MIN_GROUP:
            continue
        durs = sorted(float(s["end"]) - float(s["start"]) for s in secs)
        n = max(1, round(durs[len(durs) // 2] / half))
        # Human boundaries are approximate: sample each section with a little
        # slack and shift it (up to _MAX_SHIFT slots) to best match the others.
        seqs = []
        for s in secs:
            a, d = float(s["start"]), float(s["end"]) - float(s["start"])
            step = d / n
            seqs.append([_dominant(chords, a + k * step, a + (k + 1) * step)
                         for k in range(-_MAX_SHIFT, n + _MAX_SHIFT)])
        names = lambda seq: [chords[i]["chord"] if i is not None else None for i in seq]
        shifts = [0] * len(secs)
        for _ in range(2):
            for j in range(len(secs)):
                def score(sh):
                    mine = names(seqs[j][_MAX_SHIFT + sh:_MAX_SHIFT + sh + n])
                    return sum(sum(1 for x, y in zip(mine, names(seqs[o][_MAX_SHIFT + shifts[o]:_MAX_SHIFT + shifts[o] + n])) if x and x == y)
                               for o in range(len(secs)) if o != j) - 0.1 * abs(sh)
                shifts[j] = max(range(-_MAX_SHIFT, _MAX_SHIFT + 1), key=score)
        grid = [[seqs[j][_MAX_SHIFT + shifts[j] + k] for j in range(len(secs))] for k in range(n)]
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
    try:
        new_chords, meta = apply_consensus(chord_data.get("chords") or [], sections, _bar_seconds(chord_data))
        if meta.get("applied"):
            chord_data["chords"] = new_chords
        chord_data["section_consensus_meta"] = meta
    except Exception as exc:  # never break serving
        chord_data["section_consensus_meta"] = {"applied": False, "reason": f"error: {exc}"}
    return chord_data
