"""Shadow (plan §4-5): forward only the top / bottom / root voice, and release
when the human releases (follow_off), not after a fixed duration."""

from __future__ import annotations

from random import Random

from ..events import MieEvent, Proposal
from ..graph import Edge
from ..state import MusicalState
from . import delay_s, scaled_vel


def _target_note(ev: MieEvent, st: MusicalState, which: str) -> int | None:
    held = set(st.held) | ({ev.note} if ev.is_note_on else set())
    if not held:
        return None
    if which == "top":
        return ev.note if ev.note >= max(held) else None
    if which == "bottom":
        return ev.note if ev.note <= min(held) else None
    if which == "root":
        if st.chord is None:
            return None
        # root in the octave just below the played note
        root = st.chord.root_pc
        n = ev.note - ((ev.note - root) % 12)
        return n if n != ev.note else n - 12
    return ev.note


def run(ev: MieEvent, st: MusicalState, edge: Edge, rng: Random) -> list[Proposal]:
    which = str(edge.params.get("shadow", "top"))
    if ev.is_note_on:
        n = _target_note(ev, st, which)
        if n is None:
            return []
        n += edge.transpose + 12 * edge.octave
        cap = float(edge.params.get("max_hold_s", 8.0))
        return [Proposal(ch=edge.dst, note=n, vel=scaled_vel(edge, ev.vel), dur=cap, lane=edge.lane,
                         t_offset=delay_s(edge, st), follow_off=True, src_note=ev.note)]
    if ev.is_note_off:
        # release every shadow note bound to this human note on this lane/channel
        out = []
        for (ch, note), g in list(st.active_gen.items()):
            if ch == edge.dst and g.lane == edge.lane and g.src_note == ev.note:
                out.append(Proposal(ch=ch, note=note, vel=0, dur=0.0, lane=edge.lane, kind="off", src_note=ev.note))
        return out
    return []
