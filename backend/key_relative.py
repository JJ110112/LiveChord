"""Relative major / minor key re-decision.

``chord_detect._key_from_chords`` scores each key by how much chord time is
diatonic. A major key and its relative minor share almost the same chord set,
and the minor template additionally lists V7 / iv7 variants, so secondary
dominants in a major song (C7 = V7/vi in Ab) tip the tie toward the relative
minor. Libera's "The Lark's Last Song" (Ab major, verses cadence Eb7 → Ab) came
out as Fm.

``decide_relative(chords, key)`` keeps the detected pitch set but picks the
tonic between the two relatives using evidence a musician would use:
  * tonic time      — how long the song sits on the candidate's tonic chord
  * cadences        — V7 → I / V → I / IV → I arrivals on the candidate tonic
  * dominant pull   — dominant-7 chords that point at the candidate tonic
  * endings         — the song's last (and first) chord

Flips only when the other relative wins clearly (see decide_relative).
Pure function; used at ingest (chord_detect) and at serve time (chord_api).
"""
import re
from typing import Dict, List, Tuple

_ROOT = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
         "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _parse(chord: str):
    m = re.match(r"^([A-G][b#]?)(.*?)(?:/[A-G][b#]?)?$", chord or "")
    if not m or m.group(1) not in _ROOT:
        return None, ""
    return _ROOT[m.group(1)], m.group(2)


def _is_minor(q: str) -> bool:
    return bool(re.match(r"^(m(?!aj)|min|-)", q or ""))


def _is_dom7(q: str) -> bool:
    return bool(re.match(r"^(7|9|11|13)", q or "")) and not q.startswith("maj")


def _score(chords: List[Dict], tonic: int, minor: bool) -> Dict:
    total = sum(max(0.0, float(c.get("end", c.get("time", 0))) - float(c.get("time", 0))) for c in chords) or 1.0
    tonic_time = 0.0
    cad = 0.0
    dom_pull = 0
    prev = None
    parsed = []
    for c in chords:
        r, q = _parse(c.get("chord"))
        if r is None:
            prev = None
            continue
        dur = max(0.0, float(c.get("end", c.get("time", 0))) - float(c.get("time", 0)))
        is_tonic = (r == tonic) and (_is_minor(q) == minor) and not _is_dom7(q)
        if is_tonic:
            tonic_time += dur
        if r == (tonic + 7) % 12 and _is_dom7(q):
            dom_pull += 1
        if prev is not None and is_tonic:
            pr, pq = prev
            if pr == (tonic + 7) % 12 and not _is_minor(pq):
                cad += 3.0 if _is_dom7(pq) else 2.0
            elif pr == (tonic + 5) % 12 and (_is_minor(pq) == minor or not _is_minor(pq)):
                cad += 0.5
        prev = (r, q)
        parsed.append((r, q))
    endings = 0.0
    if parsed:
        lr, lq = parsed[-1]
        if lr == tonic and _is_minor(lq) == minor:
            endings += 3.0
        fr, fq = parsed[0]
        if fr == tonic and _is_minor(fq) == minor:
            endings += 1.0
    score = 6.0 * (tonic_time / total) + cad + 0.5 * dom_pull + endings
    return {"score": round(score, 2), "tonic_share": round(tonic_time / total, 3), "cadences": cad,
            "dominant_pull": dom_pull, "endings": endings}


def decide_relative(chords: List[Dict], key: str) -> Tuple[str, Dict]:
    """Return (key, meta). key unchanged unless the relative wins clearly."""
    m = re.match(r"^([A-G][b#]?)(m?)$", (key or "").strip())
    if not m or not chords:
        return key, {"applied": False, "reason": "unparsed-key"}
    tonic = _ROOT[m.group(1)]
    minor = m.group(2) == "m"
    use_sharps = "#" in m.group(1)
    if minor:
        rel_tonic, rel_minor = (tonic + 3) % 12, False
    else:
        rel_tonic, rel_minor = (tonic + 9) % 12, True
    cur = _score(chords, tonic, minor)
    rel = _score(chords, rel_tonic, rel_minor)
    names = _SHARP_NAMES if use_sharps else _NAMES
    rel_key = names[rel_tonic] + ("m" if rel_minor else "")
    meta = {"applied": False, "from": key, "candidate": rel_key, "current": cur, "relative": rel}
    # Strict: the relative must win on overall score, actually sit on its
    # tonic more, and win on cadences or endings. Measured on 592 library
    # songs: 35 % flip (almost all minor→major), "To Zanarkand" (E minor
    # with a strong relative-major chorus) correctly stays minor.
    if (rel["score"] >= cur["score"] * 1.5 and rel["score"] >= cur["score"] + 3.0
            and rel["tonic_share"] >= cur["tonic_share"] * 1.5
            and (rel["cadences"] >= cur["cadences"] or rel["endings"] > cur["endings"])):
        meta["applied"] = True
        meta["to"] = rel_key
        return rel_key, meta
    return key, meta


def maybe_fix_relative_key_for_serve(chord_data: Dict) -> Dict:
    try:
        key, meta = decide_relative(chord_data.get("chords") or [], chord_data.get("key") or "")
        if meta.get("applied"):
            chord_data["key_original"] = chord_data.get("key")
            chord_data["key"] = key
        chord_data["key_relative_meta"] = meta
    except Exception as exc:  # never break serving
        chord_data["key_relative_meta"] = {"applied": False, "reason": f"error: {exc}"}
    return chord_data
