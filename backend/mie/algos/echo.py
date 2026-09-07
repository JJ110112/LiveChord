"""Echo (plan §4-2): the note again after delay_beats/delay_ms, velocity decaying
by vel_scale^k, `repeats` times.  Constraint default is free (keep the pitch).

The player asked for a canyon echo: several returns dying away, not one answer
that appears and vanishes. `repeats` is therefore a ceiling, not a target - the
tail ends when the decay drops the velocity under `min_vel`, so a hard note
echoes further than a soft one, which is what a real echo does. `dur_decay`
shortens each return a little so the tail thins out instead of piling up.
"""

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
    # A tail repeats the SAME pitch on the SAME channel, and MIDI cannot hold
    # two of those at once: if a return outlasts the gap to the next one, the
    # first note_off silences the second. So returns are consecutive, not
    # overlapping - which is also what a canyon actually sounds like. Widen
    # `delay_beats` for longer returns, or raise `max_overlap` if the target
    # instrument handles restrikes well.
    overlap = float(edge.params.get("max_overlap", 1.0))
    cap = d * overlap if repeats > 1 else dur_max     # one return has nothing to pile onto
    dur_floor = min(dur_min, cap)                     # the floor never breaks the cap
    dur = max(dur_floor, min(dur_max, base_dur * edge.dur_scale, cap))
    min_vel = int(edge.params.get("min_vel", 12))
    dur_decay = float(edge.params.get("dur_decay", 1.0))
    spacing = float(edge.params.get("spacing_growth", 1.0))   # >1 spreads the tail out
    note = ev.note + edge.transpose + 12 * edge.octave
    out = []
    t = 0.0
    gap = d
    # Two jobs, two numbers - the same split the phrase echo needed. Folding
    # them into `vel_scale ** k` put the FIRST return already a fifth down and
    # squeezed the whole tail into one quiet band. `decay` falls back to
    # `vel_scale` so a scene that never set it behaves as before.
    fade = float(edge.params.get("decay", edge.vel_scale))
    for k in range(1, repeats + 1):
        vel = int(round(ev.vel * edge.vel_scale * fade ** (k - 1) + edge.vel_offset))
        if vel < min_vel:
            break          # the tail has died away; a v4 repeat is a click, not an echo
        t += gap
        gap *= spacing
        out.append(Proposal(ch=edge.dst, note=note, vel=min(127, vel),
                            dur=max(dur_floor * 0.5, dur * (dur_decay ** (k - 1))),
                            lane=edge.lane, t_offset=t))
    return out
