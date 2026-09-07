"""Step 7: musical constraint (plan §4 末段, §6 撞音迴避, §10 late binding).

Pure functions.  `constrain()` runs when a proposal is scheduled; `late_bind()`
runs again in the scheduler right before the note_on goes out, so a chord
change (or a new held note) between scheduling and sending is honoured.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .events import Proposal
from .graph import Edge, Instrument
from .scales import scale_pcs
from .state import MusicalState

ALL_PCS = frozenset(range(12))


def allowed_pcs(st: MusicalState, mode: str) -> frozenset[int]:
    """Pitch classes a proposal may land on under constraint `mode`."""
    if mode == "free":
        return ALL_PCS
    if mode == "chord" and st.chord is not None:
        return st.chord.tones
    # chord requested but no chord known -> fall back to the scale (plan §10)
    return scale_pcs(st.key.tonic_pc, st.scale_id)


def harmonic_pcs(st: MusicalState) -> frozenset[int]:
    """Chord tones if a chord is known, else the scale: what a colliding note moves to."""
    return st.chord.tones if st.chord is not None else scale_pcs(st.key.tonic_pc, st.scale_id)


def snap(note: int, pcs: Iterable[int], prefer: str = "nearest", avoid_pcs: Iterable[int] = (),
         lo: int = 0, hi: int = 127) -> Optional[int]:
    """Nearest note (in semitones) whose pitch class is in `pcs`.

    Ties go away from `avoid_pcs` (the human's held notes) first, then upward.
    `prefer` = nearest | up | down.  Returns None if nothing in [lo, hi] fits.
    """
    pcs = set(pcs)
    if not pcs:
        return None
    avoid = set(avoid_pcs)
    best: Optional[tuple] = None
    for d in range(0, 13):
        if prefer == "nearest":
            cands = (note + d, note - d)
        elif prefer == "up":
            cands = (note + d,)
        else:
            cands = (note - d,)
        for cand in cands:
            if cand < lo or cand > hi or (cand % 12) not in pcs:
                continue
            key = (d, 1 if (cand % 12) in avoid else 0, 0 if cand >= note else 1)
            if best is None or key < best[0]:
                best = (key, cand)
        if best is not None and best[0][1] == 0:
            return best[1]
    return best[1] if best else None


# collision policy (plan §6 撞音迴避): what counts as "the same note the human holds"
#   "octave"  - same pitch +-1 octave (melodic lanes: follow / echo)
#   "unison"  - exactly the same pitch
#   "none"    - doubling is the point (shadow, pad lanes)
COLLISION_DEFAULT = {"shadow": "none", "silence": "none"}


def collision_for(edge) -> str:
    c = edge.params.get("collision")
    if c is None and "avoid_octave" in edge.params:
        c = "octave" if edge.params["avoid_octave"] else "unison"
    return str(c or COLLISION_DEFAULT.get(edge.algo, "octave"))


def collides(note: int, held: Iterable[int], policy: str = "octave") -> bool:
    if policy == "none":
        return False
    dists = (0, 12) if policy == "octave" else (0,)
    return any(abs(note - h) in dists for h in held)


def late_bind(note: int, constraint: str, st: MusicalState, inst: Optional[Instrument],
              collision: str = "octave") -> Optional[int]:
    """Final pitch for a generated note given the state *now*: snap to the
    allowed pitch classes, then move off any note the human is holding
    (plan §6): to the next chord tone, else the next scale tone, else drop."""
    lo, hi = inst.note_range if inst else (0, 127)
    pcs = allowed_pcs(st, constraint)
    held = list(st.held)
    held_pcs = {h % 12 for h in held} if collision != "none" else set()
    n = snap(note, pcs, "nearest", avoid_pcs=held_pcs, lo=lo, hi=hi)
    if n is None:
        return None
    if not collides(n, held, collision):
        return n
    chord_alt = set(harmonic_pcs(st)) - held_pcs
    scale_alt = set(scale_pcs(st.key.tonic_pc, st.scale_id)) - held_pcs
    for alt_pcs in (chord_alt, scale_alt, set(pcs) - held_pcs):
        if not alt_pcs:
            continue
        for cand in (snap(n + 1, alt_pcs, "up", lo=lo, hi=hi), snap(n - 1, alt_pcs, "down", lo=lo, hi=hi)):
            if cand is not None and not collides(cand, held, collision):
                return cand
    return None


def constrain(p: Proposal, st: MusicalState, edge: Edge, inst: Optional[Instrument]) -> Optional[Proposal]:
    """Schedule-time constraint: pitch classes + instrument range and velocity.

    Collision avoidance is deliberately NOT applied here: what matters is what
    the human holds when the note *sounds*, so `late_bind()` does it at send
    time with the collision policy of the edge."""
    if p.kind != "on":
        return p
    note = late_bind(p.note, edge.constraint, st, inst, "none")
    if note is None:
        return None
    vel = int(round(p.vel * (inst.vel_scale if inst else 1.0)))
    return p.clone(note=note, vel=max(1, min(127, vel)))
