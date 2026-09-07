"""Silence (plan §4-9): once the human has been quiet for `after_s`, play chord
tones on a lane (edge-triggered); when the human plays again the lane fades
out after `release_beats`.

`lane_state` (owned by the engine, one dict per edge) keys:
    fired: bool       - the lane is currently sounding / has fired for this silence
    notes: list       - (ch, note) pairs the engine actually started
"""

from __future__ import annotations

from random import Random

from ..events import Proposal
from ..graph import Edge
from ..scales import scale_pcs
from ..state import MusicalState
from . import scaled_vel


def _voicing(st: MusicalState, n_voices: int, low: int, high: int) -> list[int]:
    pcs = sorted(st.chord.tones) if st.chord else sorted(scale_pcs(st.key.tonic_pc, st.scale_id))[:: 2][:3]
    if not pcs:
        return []
    root = st.chord.root_pc if st.chord else st.key.tonic_pc
    ordered = sorted(pcs, key=lambda pc: (pc - root) % 12)
    notes = []
    base = low + ((root - low) % 12)
    for i, pc in enumerate(ordered[:n_voices]):
        n = base + ((pc - base) % 12)
        while notes and n <= notes[-1]:
            n += 12
        if n > high:
            break
        notes.append(n)
    return notes


def tick(st: MusicalState, edge: Edge, rng: Random, now: float, lane_state: dict,
         tension: float = 0.0) -> list[Proposal]:
    after_s = float(edge.params.get("after_s", 2.0))
    if lane_state.get("fired") or st.last_human_on_t is None or st.silence_s < after_s:
        return []
    if now < lane_state.get("retry_t", 0.0):
        return []
    lane_state["fired"] = True
    lane_state["fired_t"] = now
    hold = float(edge.params.get("hold_s", 20.0))
    vel = scaled_vel(edge, int(edge.params.get("vel", 56)))
    n_voices = int(edge.params.get("voices", 3))
    low, high = int(edge.params.get("low", 48)), int(edge.params.get("high", 84))
    spread = float(edge.params.get("spread_ms", 40)) / 1000.0
    notes = []
    if st.held and edge.params.get("above_held", True):
        # prefer sitting above what the human is holding, but never at the cost
        # of losing voices: a one-note "chord" is not a pad (2026-09-07 play test)
        notes = _voicing(st, n_voices, max(low, min(high - 12, max(st.held) + 1)), high)
    if len(notes) < n_voices:
        notes = _voicing(st, n_voices, low, high)
    return [Proposal(ch=edge.dst, note=n + 12 * edge.octave + edge.transpose, vel=vel, dur=hold,
                     lane=edge.lane, t_offset=i * spread) for i, n in enumerate(notes)]


def on_skip(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> None:
    """The probability gate refused this entry.

    The flag was raised before the roll, so one refusal used to retire the lane
    for the whole silence: in the 2026-09-07 log the texture lane logged a
    single skip and never spoke again across fifteen seconds of silence. Let it
    come back after `retry_beats` instead, the same way Sustain does.
    """
    lane_state["fired"] = False
    lane_state["retry_t"] = now + float(edge.params.get("retry_beats", 2.0)) * st.beat_s


def on_human_note(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> list[Proposal]:
    """Human came back: schedule the lane's release after `release_beats`."""
    if not lane_state.get("fired"):
        return []
    lane_state["fired"] = False
    lane_state["retry_t"] = 0.0
    rel = float(edge.params.get("release_beats", 1.0)) * st.beat_s
    return [Proposal(ch=ch, note=note, vel=0, dur=0.0, lane=edge.lane, kind="off", t_offset=rel)
            for (ch, note), g in list(st.active_gen.items()) if ch == edge.dst and g.lane == edge.lane]
