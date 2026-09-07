"""Silence (plan §4-9): once the human has been quiet for `after_s`, play chord
tones on a lane (edge-triggered); when the human plays again the lane fades
out after `release_beats`.

`lane_state` (owned by the engine, one dict per edge) keys:
    fired: bool       - the lane is currently sounding / has fired for this silence
    notes: list       - (ch, note) pairs the engine actually started
    chord: str        - the chord this entry was voiced for (`follow_chord`)
"""

from __future__ import annotations

from random import Random

from ..events import Proposal
from ..graph import Edge
from ..harmony import recognize
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


def _release(st: MusicalState, edge: Edge, why: str, lane_state: dict) -> list[Proposal]:
    lane_state["fired"] = False
    lane_state["retry_t"] = 0.0
    lane_state["left"] = why
    rel = float(edge.params.get("release_beats", 1.0)) * st.beat_s
    return [Proposal(ch=ch, note=note, vel=0, dur=0.0, lane=edge.lane, kind="off", t_offset=rel)
            for (ch, note), g in list(st.active_gen.items()) if ch == edge.dst and g.lane == edge.lane]


def leaves_the_harmony(st: MusicalState, edge: Edge, lane_state: dict, now: float = 0.0) -> bool:
    """Has the music moved out from under the pad this lane laid down?

    Waiting for the player's next attack is not enough. On the 22:14 take the
    texture entered under a held chord and sat for 15.5 seconds, because that is
    how long the player went without striking a new note - a long tone in what
    had become a different scale, cutting across the playing.

    It reads what is RINGING right now (fingers plus pedal), not `st.chord`.
    `st.chord` is only recomputed on a note_on and deliberately outlives the
    release, so a rule written against it could never fire: by the time the
    chord had moved, an attack had already told the lane to leave. Lifting
    fingers moves the harmony without striking anything, and that is exactly
    the case this rule is for.

    A name change on its own is not a reason to go - a pad that still fits
    should ride through, or the lane would chatter on every passing chord. It
    leaves when it no longer FITS.
    """
    if not lane_state.get("fired"):
        return False
    mine = [note for (ch, note), g in st.active_gen.items()
            if ch == edge.dst and g.lane == edge.lane]
    if not mine:
        return False
    live = recognize(list(st.held) + list(st.sustained), now, st.chord)
    if live is None or live.name == lane_state.get("chord"):
        return False
    return any(note % 12 not in live.tones for note in mine)


def tick(st: MusicalState, edge: Edge, rng: Random, now: float, lane_state: dict,
         tension: float = 0.0) -> list[Proposal]:
    if edge.params.get("follow_chord", True) and leaves_the_harmony(st, edge, lane_state, now):
        return _release(st, edge, "chord_moved", lane_state)
    after_s = float(edge.params.get("after_s", 2.0))
    # What counts as space (per edge):
    #   "sound"  - nothing of the human's is ringing, pedal included. Correct
    #              when a pad should never sit on top of a held chord.
    #   "attack" - no new key struck for `after_s`. A player who pedals through
    #              a whole piece is never silent by the first rule, which is why
    #              the texture lane spoke once in 88 seconds on 2026-09-07.
    mode = str(edge.params.get("silence_mode", "sound"))
    quiet = st.quiet_s if mode == "attack" else st.silence_s
    if lane_state.get("fired") or st.last_human_on_t is None or quiet < after_s:
        return []
    if now < lane_state.get("retry_t", 0.0):
        return []
    lane_state["fired"] = True
    lane_state["fired_t"] = now
    live = recognize(list(st.held) + list(st.sustained), now, st.chord)
    lane_state["chord"] = live.name if live else (st.chord.name if st.chord else None)
    hold = float(edge.params.get("hold_s", 8.0))
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
    return _release(st, edge, "human", lane_state)
