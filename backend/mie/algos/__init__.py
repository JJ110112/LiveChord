"""Algorithm registry (plan §4).  Every algorithm exposes

    run(ev, st, edge, rng) -> list[Proposal]                 # event-triggered
    tick(st, edge, rng, now, lane_state) -> list[Proposal]   # time-triggered (Silence, Density)

All are pure: no I/O, no global state; `lane_state` is a per-edge dict the
engine owns for the timed algorithms.
"""

from __future__ import annotations


def delay_s(edge, st) -> float:
    return edge.delay_beats * st.beat_s + edge.delay_ms / 1000.0


def scaled_vel(edge, vel: int) -> int:
    return max(1, min(127, int(round(vel * edge.vel_scale + edge.vel_offset))))


# submodules import the two helpers above, so they must come after them
from . import echo, follow, shadow, silence, sustain  # noqa: E402

EVENT_ALGOS = {"follow": follow.run, "echo": echo.run, "shadow": shadow.run}
TICK_ALGOS = {"silence": silence.tick, "sustain": sustain.tick}
TICK_SKIP = {"sustain": sustain.on_skip, "silence": silence.on_skip}   # told when a window was refused
RELEASE_ALGOS = {"silence": silence.on_human_note}   # timed lanes that react to the human coming back
