"""Follow (plan §4-1): target = human note + interval, same velocity (scaled),
duration = the human's previous note duration (one beat for the first note)."""

from __future__ import annotations

from random import Random

from ..events import MieEvent, Proposal
from ..graph import Edge
from ..state import MusicalState
from . import delay_s, scaled_vel


def run(ev: MieEvent, st: MusicalState, edge: Edge, rng: Random) -> list[Proposal]:
    if not ev.is_note_on:
        return []
    interval = int(edge.params.get("interval", 7)) + edge.transpose + 12 * edge.octave
    dur = (st.last_human_dur or st.beat_s) * edge.dur_scale
    return [Proposal(ch=edge.dst, note=ev.note + interval, vel=scaled_vel(edge, ev.vel),
                     dur=max(0.05, dur), lane=edge.lane, t_offset=delay_s(edge, st))]
