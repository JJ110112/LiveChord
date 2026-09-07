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
    # The echo should sound as long as the human note it repeats, NOT be cut to
    # the delay: capping it at the delay is what made short chord echoes feel
    # abrupt on the Wavestate (2026-09-07 play test). The human note length is
    # not known yet at note_on, so use the previous one (same as Follow) with a
    # musical floor.
    base_dur = ev.dur_hint or st.last_human_dur or st.beat_s
    dur_min = float(edge.params.get("dur_min_beats", 0.75)) * st.beat_s
    dur_max = float(edge.params.get("dur_max_beats", 8.0)) * st.beat_s
    dur = max(dur_min, min(dur_max, base_dur * edge.dur_scale))
    min_vel = int(edge.params.get("min_vel", 16))
    note = ev.note + edge.transpose + 12 * edge.octave
    out = []
    for k in range(1, repeats + 1):
        vel = int(round(ev.vel * (edge.vel_scale ** k) + edge.vel_offset))
        if vel < min_vel:
            break          # a v4 repeat is not an echo, it is a click
        out.append(Proposal(ch=edge.dst, note=note, vel=min(127, vel), dur=dur, lane=edge.lane, t_offset=d * k))
    return out
