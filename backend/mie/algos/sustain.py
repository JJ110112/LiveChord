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
from . import OPEN_ORDER, pedal_pc, scaled_vel, spacing_gap, t_beats, t_secs, time_scale


def _candidates(st: MusicalState, edge: Edge, lane_notes: list[int], tension: float = 0.0) -> list[int]:
    """Notes the lane may add, ranked: the ones that bring new colour come first.

    The palette is the edge's own constraint, so `constraint: "function"` lets a
    line over a plain C triad reach the tones of Em7 and Am7 as well - the notes
    an accompanist would use - instead of circling the same three chord tones.
    Asking the constraint here rather than hardcoding chord tones is what makes
    the setting mean anything: late binding can only narrow what we propose.
    """
    from ..constraint import allowed_pcs, edge_range   # late: constraint pulls in state
    pcs = set(allowed_pcs(st, edge.constraint, tension))
    if not pcs:
        return []
    # ONE definition of this lane's register, shared with the late binding, or
    # the voice leading would re-pick above a ceiling this function respected.
    rng = edge_range(edge, st)
    low, high = rng if rng else (int(edge.params.get("low", 55)),
                                 int(edge.params.get("high", 88)))
    # `below_player` and `above_held` say opposite things - stay under me, sit
    # over me - so the specific one wins and the other is not consulted. A pad
    # asked to do both would do neither predictably.
    if edge.params.get("below_player"):
        pass
    elif edge.params.get("above_held", True):
        # ABOVE WHAT THE FINGERS ARE PLAYING NOW, not above everything the
        # pedal is still holding. `sounding` is fingers plus pedal, so with the
        # pedal down its maximum is a ratchet over the whole pedalled passage:
        # one flick up to A6 pinned this floor for the next ten seconds. On the
        # 22:16 take that clamped the lane into 76-88 - a single octave hard
        # against the ceiling, with E at BOTH ends of it - and the player heard
        # "一直有個高兩個八度的 mi 長音，有點干擾". Measured: the sent notes'
        # median was 81 (F5-E6 nearly throughout); reading `held` instead puts
        # it at 67. Hands lifted with the pedal down still get a floor, from
        # what is ringing - there is nothing else to be above.
        # And only while there is still room to BE above them. When the
        # player's top is already inside the lane's top octave, sitting above
        # it is not possible; clamping to `high - 12` does not get out of their
        # way, it just pins the lane to the ceiling and leaves it there. The
        # point of this floor is "do not sit inside their chord", and when they
        # are at the top of the register the way out is downwards.
        ref = st.held or st.sounding
        if ref and max(ref) - 4 <= high - 12:
            low = max(low, max(ref) - 4)
    human_pcs = {n % 12 for n in st.sounding}
    lane_pcs = {n % 12 for n in lane_notes}
    fresh, doubled = [], []
    for n in range(low, high + 1):
        if (n % 12) not in pcs or n in lane_notes:
            continue
        if (n % 12) in lane_pcs:
            continue                      # the lane already covers this colour
        (doubled if (n % 12) in human_pcs else fresh).append(n)
    out = fresh or doubled
    # This lane builds its chord one note at a time and the caller picks at
    # RANDOM from what comes back, so with the whole register on offer the
    # result has no spacing at all - 「絕對不彈簡單的 Root Position 三和弦」 is
    # about the shape, and until now there was no shape. Narrowing the list to
    # the notes that sit at least `gap` from every voice already down is what
    # gives one; the choice among them stays random, so the lane still evolves.
    gap = spacing_gap(edge)
    if gap > 1 and lane_notes and out:
        room = [n for n in out if all(abs(n - m) >= gap for m in lane_notes)]
        if room:
            out = room
    return out


def _pedal_note(st: MusicalState, edge: Edge, lane_notes: list[int]) -> int | None:
    """The note this lane holds under everything, if it is not holding it yet.

    Lowest first, and read from the KEY rather than the chord: a floor that
    follows the harmony is a bass line, not a pedal. 「即使上層和弦在變換，
    低音仍維持不變」.
    """
    pc = pedal_pc(st, edge)
    if pc is None:
        return None
    from ..constraint import edge_range
    rng = edge_range(edge, st)
    low, high = rng if rng else (int(edge.params.get("low", 55)),
                                 int(edge.params.get("high", 88)))
    n = low + ((pc - low) % 12)
    if n > high:
        return None
    return None if n in lane_notes else n


# ------------------------------------------------------- 內聲部流動 (Phase 4)
# 「不要讓 Pad 只是死死按著 C 和弦。讓 MIE 在 Sustained Pad 響起時，在中高音域
# 自動加入 Csus2 -> C -> Csus4 -> C 的微弱內聲部動態，讓襯底內部自己產生解開的
# 線條。」
#
# The cycle is written from the chord ROOT in semitones. Both forms resolve
# back to the third every other step, because the suspension is only worth
# anything if you hear it let go: sus2, third, sus4, third.
SUS_CYCLE = {
    "major": (2, 4, 5, 4),
    "minor": (2, 3, 5, 3),
}


def _third_kind(st: MusicalState) -> str:
    """Major or minor, read from what is actually sounding."""
    if st.chord is None:
        return "major"
    tones = {(t - st.chord.root_pc) % 12 for t in st.chord.tones}
    return "minor" if 3 in tones and 4 not in tones else "major"


def _motion(st: MusicalState, edge: Edge, now: float, lane_state: dict,
            lane_notes: list[int]) -> list[Proposal]:
    """Move ONE inner voice one step along the suspension cycle.

    Inner: never the lowest note the lane is holding and never the highest.
    The bottom is the floor the harmony sits on - moving it is a chord change,
    not a suspension - and the top is the line the ear follows, where a
    wandering voice reads as a melody that keeps changing its mind.

    The moving note asks for `scale` rather than the lane's own constraint. A
    suspension is a NON-chord tone by definition: proposed under `chord` it
    would be snapped back to the third, and the whole gesture would do nothing
    and say nothing.
    """
    if str(edge.params.get("voice_motion", "") or "") != "sus":
        return []
    if len(lane_notes) < 3:
        return []                     # nothing is INSIDE anything yet
    every = t_beats(edge, st, "motion_beats", 8.0)
    nxt = lane_state.get("motion_t")
    if nxt is None:
        lane_state["motion_t"] = now + every
        return []
    if now < nxt:
        return []
    lane_state["motion_t"] = now + every

    root = st.chord.root_pc if st.chord else st.key.tonic_pc
    cycle = SUS_CYCLE[_third_kind(st)]
    inner = sorted(lane_notes)[1:-1]
    # Prefer a voice that is already somewhere on the cycle - it has a next
    # step. Otherwise take the lowest inner voice and start it at the third.
    on_cycle = [(n, cycle.index((n - root) % 12)) for n in inner
                if (n - root) % 12 in cycle]
    if on_cycle:
        old, i = on_cycle[0]
        want = cycle[(i + 1) % len(cycle)]
    else:
        old, want = inner[0], cycle[1]
    # The NEAREST note of the wanted pitch class, not the one an octave up.
    # Voice leading is the whole point of a suspension: it has to be heard as
    # the same voice moving a step, not as a note vanishing here and another
    # appearing a tenth away - and the naive "add the interval" arithmetic put
    # it straight on top of a voice the lane was already holding.
    lo = int(edge.params.get("low", 55))
    hi = int(edge.params.get("high", 88))
    pc = (root + want) % 12
    cands = [n for n in range(lo, hi + 1)
             if n % 12 == pc and n != old and n not in lane_notes]
    if not cands:
        return []
    new = min(cands, key=lambda n: (abs(n - old), n))
    if abs(new - old) > 7:
        return []                     # too far to read as one voice moving
    hold = t_beats(edge, st, "hold_beats", 8.0)
    return [
        Proposal(ch=edge.dst, note=old, vel=0, dur=0.0, lane=edge.lane, kind="off"),
        Proposal(ch=edge.dst, note=new, vel=scaled_vel(edge, int(edge.params.get("vel", 46))),
                 dur=hold, lane=edge.lane, constraint="scale"),
    ]


def _pedal_hold(st: MusicalState, edge: Edge, lane_notes: list[int]) -> int | None:
    """Which of the lane's notes is the pedal, and must not be retired."""
    pc = pedal_pc(st, edge)
    if pc is None or not lane_notes:
        return None
    same = [n for n in lane_notes if n % 12 == pc]
    return min(same) if same else None


def on_skip(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> None:
    """The probability gate turned this window down.

    The cadence belongs to `every_bars_*`; the dice only decide how thick the
    lane is. Losing a whole window to a failed roll stretched "every one or two
    bars" into "once every ten seconds", so come back within `retry_beats`.
    Sustained failures (the human is playing hard, restraint is low) still keep
    the lane quiet, which is the musical control we want.
    """
    if lane_state.get("next_t") is not None:
        lane_state["next_t"] = now + t_beats(edge, st, "retry_beats", 1.0)
    # A refused suspension must not cost a whole cycle of waiting: the note it
    # was letting go of has already been released by the time the dice are
    # rolled, so the lane is a voice short until this comes back.
    if lane_state.get("motion_t") is not None:
        lane_state["motion_t"] = now + t_beats(edge, st, "retry_beats", 1.0)


def tick(st: MusicalState, edge: Edge, rng: Random, now: float, lane_state: dict,
         tension: float = 0.0) -> list[Proposal]:
    lane_notes = [note for (ch, note), g in list(st.active_gen.items())
                  if ch == edge.dst and g.lane == edge.lane]
    if not st.sounding:
        # the human finally stopped: let the lane go
        lane_state["next_t"] = None
        lane_state["left"] = "human_stopped"     # so the log says WHY it left
        if lane_notes:
            rel = t_beats(edge, st, "release_beats", 2.0)
            return [Proposal(ch=edge.dst, note=n, vel=0, dur=0.0, lane=edge.lane, kind="off",
                             t_offset=rel) for n in lane_notes]
        return []

    # The player moved UP: come down out of their way rather than waiting for
    # these notes to expire on their own. "當我的演奏音域往上移動時，MIE 自動向下
    # 重新配置" - a pad that only re-aims when it next speaks is still sitting on
    # top of them for the length of a hold. The 2-semitone margin is so a top
    # note wobbling either side of the line does not switch the lane on and off.
    ceiling_offs: list = []
    if edge.params.get("below_player") and lane_notes:
        from ..constraint import edge_range
        rng_ = edge_range(edge, st)
        if rng_:
            over = [n for n in lane_notes if n > rng_[1] + 2]
            if over:
                rel = t_beats(edge, st, "release_beats", 2.0)
                ceiling_offs = [Proposal(ch=edge.dst, note=n, vel=0, dur=0.0, lane=edge.lane,
                                         kind="off", t_offset=rel * 0.5) for n in over]
                lane_notes = [n for n in lane_notes if n not in over]

    # The suspension runs on its OWN clock, not the one that decides when to
    # add a voice: it is a thing the chord already down does to itself, and it
    # has to keep happening through the long stretches where the lane is full
    # and adding nothing.
    moved = _motion(st, edge, now, lane_state, lane_notes)
    if moved:
        return ceiling_offs + moved

    bar_s = st.beat_s * st.beats_per_bar
    if lane_state.get("next_t") is None:
        # first tick of this held gesture: wait `after_s` before speaking at all
        lane_state["next_t"] = now + t_secs(edge, st, "after_s", 1.5)
        return ceiling_offs
    if now < lane_state["next_t"]:
        return ceiling_offs
    scale = time_scale(st)
    lo = float(edge.params.get("every_bars_min", 1.0)) * scale
    hi = max(lo, float(edge.params.get("every_bars_max", 2.0)) * scale)
    lane_state["next_t"] = now + rng.uniform(lo, hi) * bar_s

    # The floor goes down first and is not part of the rotation below: a pedal
    # that gets retired as "the oldest voice" is not a pedal.
    note = _pedal_note(st, edge, lane_notes)
    if note is None:
        cands = _candidates(st, edge, lane_notes, tension)
        if not cands:
            return ceiling_offs
        note = rng.choice(cands)
    vel = scaled_vel(edge, int(edge.params.get("vel", 46)) + rng.randint(-4, 4))
    hold = t_beats(edge, st, "hold_beats", 8.0)
    t_off = 0.0     # the engine quantises every lane through `align` (plan §11 Ph2)

    out = list(ceiling_offs)
    max_voices = int(edge.params.get("voices", 3))
    if len(lane_notes) >= max_voices:
        lane_state["left"] = "voice_budget"
        # The HIGHEST goes first when the lane is asked to stay under the
        # player: "如果完整和弦會造成高音超出限制，優先刪除或降低最高聲部".
        # Otherwise the oldest, which is what a pad rotating its colours wants.
        keep = _pedal_hold(st, edge, lane_notes)
        live = [(n, g.t_on) for (ch, n), g in list(st.active_gen.items())
                if ch == edge.dst and g.lane == edge.lane and n in lane_notes
                and n != keep]
        if live:
            oldest = (max(live, key=lambda x: x[0])[0] if edge.params.get("below_player")
                      else min(live, key=lambda x: x[1])[0])
        else:
            oldest = lane_notes[0]
        out.append(Proposal(ch=edge.dst, note=oldest, vel=0, dur=0.0, lane=edge.lane, kind="off",
                            t_offset=t_off))
    out.append(Proposal(ch=edge.dst, note=note + edge.transpose + 12 * edge.octave, vel=vel,
                        dur=hold, lane=edge.lane, t_offset=t_off))
    return out
