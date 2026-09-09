"""Algorithm registry (plan §4).  Every algorithm exposes

    run(ev, st, edge, rng) -> list[Proposal]                 # event-triggered
    tick(st, edge, rng, now, lane_state) -> list[Proposal]   # time-triggered (Silence, Density)

All are pure: no I/O, no global state; `lane_state` is a per-edge dict the
engine owns for the timed algorithms.
"""

from __future__ import annotations


# The TIME knob (plan §9.1, after Bad Mood's CLOCK). One control that stretches
# or compresses every waiting time in the scene at once - how long an echo waits,
# how long a pad holds, how long a silence has to last before a lane takes it.
# Bad Mood's version moves in harmonised steps rather than continuously, because
# halving a delay is musical and multiplying it by 1.07 is not; `time_steps`
# keeps that, and turning it off gives a plain continuous sweep.
TIME_STEPS = (0.25, 1 / 3, 0.5, 2 / 3, 1.0, 1.5, 2.0, 3.0, 4.0)


def quantize_time(x: float) -> float:
    """Nearest musical ratio, by how far apart they sound rather than in value.

    Distance is measured on the ratios themselves (1/2 to 1 is the same step as
    1 to 2), which is how the ear hears tempo relationships.
    """
    import math
    lx = math.log(max(1e-6, x))
    return min(TIME_STEPS, key=lambda s: abs(math.log(s) - lx))


def time_scale(st) -> float:
    """How much longer everything waits. 1.0 = the scene as written."""
    v = getattr(st, "time_knob", None)
    return 1.0 if v is None else max(0.05, float(v))


def t_beats(edge, st, key: str, default: float) -> float:
    """A duration written in beats, in seconds, stretched by the TIME knob."""
    return float(edge.params.get(key, default)) * st.beat_s * time_scale(st)


def t_secs(edge, st, key: str, default: float) -> float:
    """A duration written in seconds, stretched by the TIME knob."""
    return float(edge.params.get(key, default)) * time_scale(st)


def delay_s(edge, st) -> float:
    return (edge.delay_beats * st.beat_s + edge.delay_ms / 1000.0) * time_scale(st)


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
    d = st.effective_density()
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
    d = st.effective_density()
    if d is None:
        return base
    return max(1, int(round(base * (1.8 - 1.2 * float(d)))))


# submodules import the two helpers above, so they must come after them
# ------------------------------------------------------- 開放聲部 / 持續低音
# Phase 2 of the worship-pad work. The player's brief, verbatim: 「絕對不彈簡單
# 的 Root Position 三和弦」, 「常用 1-5-9 或 1-5-8-3 的寬廣配器，留出大量的中頻
# 空間給主旋律或人聲」, and a pedal tone held under changing harmony.
#
# The order is the voicing. Root first, then the FIFTH - that is what opens the
# middle out - then the ninth, and the third only after them, so the third
# lands an octave up as a tenth instead of filling in the space the voice or
# the melody is supposed to occupy. Everything after that is the leftovers, in
# the order an accompanist would reach for them.
OPEN_ORDER = (0, 7, 2, 4, 9, 10, 11, 5, 3, 6, 8, 1)

# How much air between neighbouring voices. `close` is what the engine did
# before and stays the default: nothing that is already tuned changes shape
# because this arrived.
SPACING_GAP = {"close": 1, "open": 5, "wide": 7}


def spacing_gap(edge) -> int:
    return SPACING_GAP.get(str(edge.params.get("spacing", "close")), 1)


def pedal_pc(st, edge):
    """The pitch class this lane holds under everything, or None.

    Read from the KEY, not from the chord: a pedal that follows the chord is
    not a pedal, it is a bass line. 「即使上層和弦在變換，低音仍維持不變」.
    """
    mode = str(edge.params.get("pedal", "") or "")
    if mode not in ("tonic", "fifth"):
        return None
    return (st.key.tonic_pc + (7 if mode == "fifth" else 0)) % 12


from . import echo, follow, phrase, shadow, silence, sustain  # noqa: E402

EVENT_ALGOS = {"follow": follow.run, "echo": echo.run, "shadow": shadow.run}
TICK_ALGOS = {"silence": silence.tick, "sustain": sustain.tick, "phrase": phrase.tick}
TICK_SKIP = {"sustain": sustain.on_skip, "silence": silence.on_skip,
             "phrase": phrase.on_skip}   # told when a window was refused
RELEASE_ALGOS = {"silence": silence.on_human_note}   # timed lanes that react to the human coming back
