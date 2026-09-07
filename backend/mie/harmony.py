"""Chord recognition from held notes + Krumhansl key estimation (plan §1, §10).

The chord map is the same table `frontend/js/editor.js detectChordFromMIDI`
uses, so the engine and the editor agree on what a set of pitch classes is
called.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .scales import NOTE_NAMES

# interval set (relative to root) -> suffix
CHORD_MAP: dict[tuple[int, ...], str] = {
    (0, 4, 7): "", (0, 3, 7): "m", (0, 4, 7, 10): "7", (0, 3, 7, 10): "m7",
    (0, 4, 7, 11): "maj7", (0, 3, 6): "dim", (0, 4, 8): "aug",
    (0, 2, 7): "sus2", (0, 5, 7): "sus4", (0, 3, 6, 10): "m7b5",
    (0, 2, 4, 7, 11): "maj9", (0, 2, 4, 7, 10): "9", (0, 3, 7, 11): "mMaj7",
    (0, 4, 5, 7, 10): "11", (0, 2, 3, 7, 10): "m9",
    (0, 3, 6, 9): "dim7", (0, 4, 7, 9): "6", (0, 3, 7, 9): "m6",
    # dyads: enough to give the constraint something to hold on to
    (0, 7): "5", (0, 4): "(3)", (0, 3): "m(3)",
}


@dataclass(slots=True, frozen=True)
class ChordInfo:
    name: str
    root_pc: int
    quality: str
    tones: frozenset[int]     # pitch classes
    since_t: float


def recognize(notes: Iterable[int], t: float, prev: Optional[ChordInfo] = None) -> Optional[ChordInfo]:
    """Return the chord spelled by `notes` (MIDI numbers), or None if unknown.

    Tries every pitch class as the root so inversions are found; ties prefer
    the lowest sounding note as root, then the previous chord root.
    """
    notes = list(notes)
    pcs = sorted({n % 12 for n in notes})
    if len(pcs) < 2:
        return None
    lowest = min(notes) % 12
    candidates = []
    for root in pcs:
        rel = tuple(sorted((pc - root) % 12 for pc in pcs))
        suf = CHORD_MAP.get(rel)
        if suf is not None:
            score = 0
            if root == lowest:
                score += 2
            if prev and root == prev.root_pc:
                score += 1
            candidates.append((score, len(rel), root, suf))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], -c[1]))
    _, _, root, suf = candidates[0]
    name = NOTE_NAMES[root] + suf
    if prev and prev.name == name:
        return prev
    return ChordInfo(name=name, root_pc=root, quality=suf, tones=frozenset(pcs), since_t=t)


# --- Krumhansl-Schmuckler key finding -------------------------------------

_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def _corr(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


@dataclass(slots=True, frozen=True)
class KeyInfo:
    tonic_pc: int
    mode: str            # "major" | "minor"
    confidence: float    # 0-1
    source: str          # "player" | "inferred" | "scene" | "manual"


def estimate_key(pc_hist: list[float]) -> Optional[KeyInfo]:
    """Krumhansl correlation over a 12-bin pitch-class histogram (any weighting)."""
    if sum(pc_hist) <= 0:
        return None
    best = None
    scores = []
    for tonic in range(12):
        rot = pc_hist[tonic:] + pc_hist[:tonic]
        for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
            c = _corr(rot, prof)
            scores.append(c)
            if best is None or c > best[0]:
                best = (c, tonic, mode)
    scores.sort(reverse=True)
    margin = scores[0] - scores[1] if len(scores) > 1 else 0.0
    conf = max(0.0, min(1.0, 0.5 * max(0.0, best[0]) + 2.0 * margin))
    return KeyInfo(tonic_pc=best[1], mode=best[2], confidence=conf, source="inferred")
