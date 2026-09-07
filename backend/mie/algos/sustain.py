"""Sustain (plan §4-11, added 2026-09-07): keep answering while the human holds.

Every other algorithm is triggered by a note_on, so holding a pad chord — the
player's own words: "my fingers never left the keys, the sound is still there" —
produced one answer at the attack and then silence.  Silence is its mirror
image and deliberately does nothing while anything is still ringing.

This one runs on the tick, fires only while the human's sound is *still
sounding* (fingers or sustain pedal), and adds one slowly evolving voice every
`every_bars_min`..`every_bars_max` bars.  When the human's sound finally stops,
the lane fades out after `release_beats`.

`lane_state` keys: `next_t` (when the lane may speak again).
"""

from __future__ import annotations

from random import Random

from ..events import Proposal
from ..graph import Edge
from ..scales import scale_pcs
from ..state import MusicalState
from . import scaled_vel


def _candidates(st: MusicalState, edge: Edge, lane_notes: list[int]) -> list[int]:
    """Chord/scale tones in range, ranked: notes that add colour come first."""
    pcs = set(st.chord.tones) if st.chord else set(scale_pcs(st.key.tonic_pc, st.scale_id))
    if not pcs:
        return []
    low, high = int(edge.params.get("low", 55)), int(edge.params.get("high", 88))
    if edge.params.get("above_held", True) and st.sounding:
        low = max(low, min(high - 12, max(st.sounding) - 4))
    human_pcs = {n % 12 for n in st.sounding}
    lane_pcs = {n % 12 for n in lane_notes}
    fresh, doubled = [], []
    for n in range(low, high + 1):
        if (n % 12) not in pcs or n in lane_notes:
            continue
        if (n % 12) in lane_pcs:
            continue                      # the lane already covers this colour
        (doubled if (n % 12) in human_pcs else fresh).append(n)
    return fresh or doubled


def on_skip(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> None:
    """The probability gate turned this window down.

    The cadence belongs to `every_bars_*`; the dice only decide how thick the
    lane is. Losing a whole window to a failed roll stretched "every one or two
    bars" into "once every ten seconds", so come back within `retry_beats`.
    Sustained failures (the human is playing hard, restraint is low) still keep
    the lane quiet, which is the musical control we want.
    """
    if lane_state.get("next_t") is not None:
        lane_state["next_t"] = now + float(edge.params.get("retry_beats", 1.0)) * st.beat_s


def tick(st: MusicalState, edge: Edge, rng: Random, now: float, lane_state: dict) -> list[Proposal]:
    lane_notes = [note for (ch, note), g in st.active_gen.items()
                  if ch == edge.dst and g.lane == edge.lane]
    if not st.sounding:
        # the human finally stopped: let the lane go
        lane_state["next_t"] = None
        if lane_notes:
            rel = float(edge.params.get("release_beats", 2.0)) * st.beat_s
            return [Proposal(ch=edge.dst, note=n, vel=0, dur=0.0, lane=edge.lane, kind="off",
                             t_offset=rel) for n in lane_notes]
        return []

    bar_s = st.beat_s * st.beats_per_bar
    if lane_state.get("next_t") is None:
        # first tick of this held gesture: wait `after_s` before speaking at all
        lane_state["next_t"] = now + float(edge.params.get("after_s", 1.5))
        return []
    if now < lane_state["next_t"]:
        return []
    lo = float(edge.params.get("every_bars_min", 1.0))
    hi = max(lo, float(edge.params.get("every_bars_max", 2.0)))
    lane_state["next_t"] = now + rng.uniform(lo, hi) * bar_s

    cands = _candidates(st, edge, lane_notes)
    if not cands:
        return []
    note = rng.choice(cands)
    vel = scaled_vel(edge, int(edge.params.get("vel", 46)) + rng.randint(-4, 4))
    hold = float(edge.params.get("hold_beats", 8.0)) * st.beat_s
    # land on the next beat (or bar) so the voice enters in time with the music
    align = str(edge.params.get("align", "beat"))
    if align == "bar":
        t_off = max(0.0, st.next_downbeat_t(now) - now)
    elif align == "beat":
        t_off = max(0.0, st.next_beat_t(now) - now)
    else:
        t_off = 0.0

    out = []
    max_voices = int(edge.params.get("voices", 3))
    if len(lane_notes) >= max_voices:
        oldest = min(((n, g.t_on) for (ch, n), g in st.active_gen.items()
                      if ch == edge.dst and g.lane == edge.lane), key=lambda x: x[1])[0]
        out.append(Proposal(ch=edge.dst, note=oldest, vel=0, dur=0.0, lane=edge.lane, kind="off",
                            t_offset=t_off))
    out.append(Proposal(ch=edge.dst, note=note + edge.transpose + 12 * edge.octave, vel=vel,
                        dur=hold, lane=edge.lane, t_offset=t_off))
    return out
