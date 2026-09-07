"""Step 7: musical constraint (plan §4 末段, §6 撞音迴避, §10 late binding).

Pure functions.  `constrain()` runs when a proposal is scheduled; `late_bind()`
runs again in the scheduler right before the note_on goes out, so a chord
change (or a new held note) between scheduling and sending is honoured.
"""

from __future__ import annotations

from typing import Iterable, Optional

from . import function, voicing
from .events import Proposal
from .graph import Edge, Instrument
from .scales import scale_pcs
from .state import MusicalState

ALL_PCS = frozenset(range(12))


def allowed_pcs(st: MusicalState, mode: str, tension: float = 0.0) -> frozenset[int]:
    """Pitch classes a proposal may land on under constraint `mode`.

    `chord`    - the literal chord tones: safest, but three pitch classes is
                 not enough colour for a lane that keeps speaking
    `function` - the chord plus the tones of the key's other chords with the
                 same harmonic function (plan §11 Phase 2); wider than `chord`
                 but, unlike `scale`, it leaves out the notes that would blur
                 the function
    `scale`    - anything in the key
    `free`     - anything
    """
    if mode == "free":
        return ALL_PCS
    if st.chord is not None:
        if mode == "chord":
            return st.chord.tones
        if mode == "function":
            return function.colour_pcs(st.chord.tones, st.chord.root_pc,
                                       st.key.tonic_pc, st.key.mode, tension)
    # no chord recognised yet -> fall back to the scale (plan §10)
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
    if not pcs or lo > hi:
        return None
    avoid = set(avoid_pcs)
    best_key: Optional[tuple] = None
    best_note: Optional[int] = None
    for d in range(0, 13):
        if prefer == "nearest":
            cands = (note,) if d == 0 else (note + d, note - d)
        elif prefer == "up":
            cands = (note + d,)
        else:
            cands = (note - d,)
        for cand in cands:
            if cand < lo or cand > hi or (cand % 12) not in pcs:
                continue
            key = (d, 1 if (cand % 12) in avoid else 0, 0 if cand >= note else 1)
            if best_key is None or key < best_key:
                best_key, best_note = key, cand
        # a hit that is not on an avoided pitch class cannot be beaten by a
        # larger distance, so stop as soon as we have one
        if best_key is not None and best_key[1] == 0:
            return best_note
    return best_note        # None when nothing in [lo, hi] has an allowed pitch class


# collision policy (plan §6 撞音迴避): what counts as "the same note the human holds"
#   "octave"  - same pitch +-1 octave (melodic lanes: follow / echo)
#   "unison"  - exactly the same pitch
#   "none"    - doubling is the point (shadow, pad lanes)
#   echo is "none" on purpose: repeating the note the human is still holding is
#   the whole point, and pushing it to a neighbouring scale tone turned echoes
#   into wrong notes over a held chord (2026-09-07 play test).
COLLISION_DEFAULT = {"shadow": "none", "silence": "none", "echo": "none", "sustain": "none"}


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


# Voice leading modes (plan §11 Phase 2):
#   "off"     - nearest legal pitch to what the algorithm proposed (Phase 1)
#   "octave"  - keep the pitch class the algorithm chose, pick the register that
#               leads best from the lane's previous note
#   "free"    - the leading may change the pitch class too
#
#   sustain  - "octave": the algorithm picks the colour on purpose (it avoids
#              the pitch classes the lane already covers), and the leaping that
#              sounded mechanical was in the register. Letting the leading
#              re-pick the pitch class collapses the line into a drone.
#   echo     - off: it must keep the pitch it is repeating
#   shadow   - off: it tracks one specific voice of the human's chord
#   silence  - off: it emits a whole voicing at once, there is no single line
#   follow   - off by default: its point is the interval above the human, so
#              leading it turns a parallel line into a counter-line. Set
#              "voice_lead": "octave" on the edge to keep the interval but
#              smooth the register.
VOICE_LEAD_DEFAULT = {"sustain": "octave"}
VOICE_LEAD_MODES = ("off", "octave", "free")


def voice_lead_for(edge) -> str:
    v = edge.params.get("voice_lead")
    if v is None:
        return VOICE_LEAD_DEFAULT.get(edge.algo, "off")
    if isinstance(v, bool):
        return "free" if v else "off"
    v = str(v)
    return v if v in VOICE_LEAD_MODES else "off"


def late_bind(note: int, constraint: str, st: MusicalState, inst: Optional[Instrument],
              collision: str = "octave", *, voice_lead: str = "off",
              prev: Optional[int] = None, others: tuple = (),
              tension: float = 0.0, note_range: Optional[tuple] = None) -> Optional[int]:
    """Final pitch for a generated note given the state *now*.

    Snap to the allowed pitch classes - by voice leading from `prev` when the
    edge asks for it, otherwise by nearest pitch - then move off any note the
    human is holding (plan §6): to the next chord tone, else the next scale
    tone, else drop.
    """
    lo, hi = inst.note_range if inst else (0, 127)
    if note_range:
        # An edge that sets low/high means that register. Voice leading used to
        # re-pick inside the instrument range and quietly ignore it, which is
        # how the string lane ended up at 76-91 under an edge capped at 88.
        lo, hi = max(lo, int(note_range[0])), min(hi, int(note_range[1]))
        if lo > hi:
            lo, hi = inst.note_range if inst else (0, 127)
    pcs = allowed_pcs(st, constraint, tension)
    held = list(st.held)
    held_pcs = {h % 12 for h in held} if collision != "none" else set()
    if voice_lead != "off" and prev is not None:
        lead_pcs = pcs
        if voice_lead == "octave" and (note % 12) in pcs:
            lead_pcs = frozenset({note % 12})     # keep the colour, choose the register
        n = voicing.lead(lead_pcs, prev=prev, intent=note, lo=lo, hi=hi, avoid_pcs=held_pcs,
                         chord_root=st.chord.root_pc if st.chord else None,
                         chord_pcs=st.chord.tones if st.chord else pcs, others=others)
    else:
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


def edge_range(edge) -> Optional[tuple]:
    """The register an edge asked for, if it named one."""
    lo, hi = edge.params.get("low"), edge.params.get("high")
    return (int(lo), int(hi)) if lo is not None and hi is not None else None


def constrain(p: Proposal, st: MusicalState, edge: Edge, inst: Optional[Instrument], *,
              prev: Optional[int] = None, others: tuple = (),
              tension: float = 0.0) -> Optional[Proposal]:
    """Schedule-time constraint: pitch classes + instrument range and velocity.

    Collision avoidance is deliberately NOT applied here: what matters is what
    the human holds when the note *sounds*, so `late_bind()` does it at send
    time with the collision policy of the edge."""
    if p.kind != "on":
        return p
    note = late_bind(p.note, edge.constraint, st, inst, "none",
                     voice_lead=voice_lead_for(edge), prev=prev, others=others, tension=tension,
                     note_range=edge_range(edge))
    if note is None:
        return None
    vel = int(round(p.vel * (inst.vel_scale if inst else 1.0)))
    return p.clone(note=note, vel=max(1, min(127, vel)))
