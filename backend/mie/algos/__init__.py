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


def how_many(edge, st, key: str, default: int, lo: int = 1) -> int:
    """A count from the edge, thinned or thickened by the scene's DENSITY knob.

    `density` is one of the three high-level controls (plan §9.1). It is not
    another `prob_scale`: that decides how OFTEN a lane speaks, this decides
    how MUCH it plays when it does - voices in a pad, returns in an echo.
    It is kept as `st.density_knob`, NOT `st.density`, which was already taken
    by the EMA of how densely the human is playing and is what the restraint
    curve reads.
    0.5 means "exactly as the edge is written", so a scene that never sets it
    behaves as before; 0 thins to the floor and 1 is about 1.6x.
    """
    base = int(edge.params.get(key, default))
    d = st.density_knob
    if d is None:
        return max(lo, base)
    return max(lo, int(round(base * (0.4 + 1.2 * float(d)))))


def tail_floor(edge, st, default: int = 12) -> int:
    """`min_vel` for a decaying tail, opened up or closed down by DENSITY.

    Raising `repeats` alone does not make an echo thicker: it is a ceiling the
    decay never reaches - measured on the 22:14 take, density 0.5, 0.75 and 1.0
    all produced exactly 651 notes. What actually decides how long a tail runs
    is how quiet a return may be before it stops, so the knob has to move that
    too or it only ever thins.
    """
    base = int(edge.params.get("min_vel", default))
    d = st.density_knob
    if d is None:
        return base
    return max(1, int(round(base * (1.8 - 1.2 * float(d)))))


# submodules import the two helpers above, so they must come after them
from . import echo, follow, phrase, shadow, silence, sustain  # noqa: E402

EVENT_ALGOS = {"follow": follow.run, "echo": echo.run, "shadow": shadow.run}
TICK_ALGOS = {"silence": silence.tick, "sustain": sustain.tick, "phrase": phrase.tick}
TICK_SKIP = {"sustain": sustain.on_skip, "silence": silence.on_skip,
             "phrase": phrase.on_skip}   # told when a window was refused
RELEASE_ALGOS = {"silence": silence.on_human_note}   # timed lanes that react to the human coming back
