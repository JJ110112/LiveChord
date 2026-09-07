"""Echo (plan §4-2): the note again after delay_beats/delay_ms, velocity decaying
by vel_scale^k, `repeats` times.  Constraint default is free (keep the pitch)."""

from __future__ import annotations

from random import Random

from ..events import MieEvent, Proposal
from ..graph import Edge
from ..state import MusicalState
from . import delay_s


def run(ev: MieEvent, st: MusicalState, edge: Edge, rng: Random) -> list[Proposal]:
    if not ev.is_note_on:
        return []
    repeats = max(1, int(edge.params.get("repeats", 1)))
    d = delay_s(edge, st)
    if d <= 0:
        d = 0.5 * st.beat_s
    base_dur = ev.dur_hint or st.last_human_dur or 0.5 * st.beat_s
    dur = max(0.05, min(base_dur, d * 0.9) * edge.dur_scale)
    note = ev.note + edge.transpose + 12 * edge.octave
    out = []
    for k in range(1, repeats + 1):
        vel = int(round(ev.vel * (edge.vel_scale ** k) + edge.vel_offset))
        if vel < 1:
            break
        out.append(Proposal(ch=edge.dst, note=note, vel=min(127, vel), dur=dur, lane=edge.lane, t_offset=d * k))
    return out
