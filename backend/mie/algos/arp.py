"""Ambient arp (Phase 3): a light shimmer over a lane that is already holding.

The player's brief for the worship pad, verbatim: 「長音 + 隨機/點狀環境琶音
(Ambient Pluck/Arp) + 9度/Sus2 和聲張力，才是現代 Worship 襯底的靈魂所在」, and
「讓長音負責鋪底，同時帶出一個低音量、高音域、輕柔閃爍的 16 分音符加九度琶音」.

Three things make it that rather than an arpeggiator:

  * IT DOES NOT PLAY ON ITS OWN. `with_lane` names a lane that has to be
    sounding before this one may speak, so the shimmer is something the pad
    does, not a second pad. Without that it is just another lane filling the
    same silence, and two lanes answering the same silence is how a mix turns
    to soup.
  * IT IS NOT ON THE GRID. Each note lands within `jitter_ms` of its slot, and
    slots are refused by the edge's own `prob` - 「隨機、非嚴格對拍的點狀音高」.
    A strictly quantised sixteenth line reads as a sequencer, which is the one
    thing an ambient texture must not sound like.
  * IT REACHES FOR COLOUR FIRST. The ninth, then the fifth, then the sixth,
    and the root last - the notes that shimmer rather than the notes that state
    the chord. The pad is already stating the chord.

`lane_state` keys: `next_t` (the next slot), `last` (the note just played, so it
does not repeat).
"""

from __future__ import annotations

from random import Random
from typing import Optional

from ..events import Proposal
from ..graph import Edge
from ..state import MusicalState
from . import scaled_vel, t_beats

# Colour first, statement last. The pad underneath is already saying which
# chord this is; what is wanted up here is 「輕微閃爍」.
ARP_ORDER = (2, 7, 9, 4, 11, 5, 0, 10, 3, 6, 8, 1)


def _sounding(st: MusicalState, lane: str) -> bool:
    return any(g.lane == lane for g in st.active_gen.values())


def _pick(st: MusicalState, edge: Edge, rng: Random, tension: float,
          last: Optional[int]) -> Optional[int]:
    from ..constraint import allowed_pcs, edge_range   # late: constraint pulls in state
    pcs = set(allowed_pcs(st, edge.constraint, tension))
    if not pcs:
        return None
    rng_ = edge_range(edge, st)
    low, high = rng_ if rng_ else (int(edge.params.get("low", 72)),
                                   int(edge.params.get("high", 96)))
    root = st.chord.root_pc if st.chord else st.key.tonic_pc
    notes = [n for n in range(low, high + 1) if (n % 12) in pcs and n != last]
    if not notes:
        return None
    # Weighted, not sorted: a line that always takes the ninth is a trill. The
    # front of ARP_ORDER is likelier, the back is still possible.
    def weight(n: int) -> float:
        return 1.0 / (1 + ARP_ORDER.index((n - root) % 12))
    total = sum(weight(n) for n in notes)
    x = rng.random() * total
    for n in notes:
        x -= weight(n)
        if x <= 0:
            return n
    return notes[-1]


def tick(st: MusicalState, edge: Edge, rng: Random, now: float, lane_state: dict,
         tension: float = 0.0) -> list[Proposal]:
    p = edge.params
    with_lane = str(p.get("with_lane", "") or "")
    if with_lane and not _sounding(st, with_lane):
        # Nothing is holding, so there is nothing to shimmer over. Forget where
        # the grid was: picking it up mid-phrase two minutes later would place
        # the first note at an arbitrary distance from the pad's entry.
        lane_state["next_t"] = None
        lane_state["left"] = "no_pad"
        return []
    if not with_lane and st.quiet_s > float(p.get("after_s", 2.0)):
        return []                        # no pad named and nobody playing

    grid = max(0.03, t_beats(edge, st, "grid_beats", 0.25))
    nxt = lane_state.get("next_t")
    if nxt is None:
        lane_state["next_t"] = now + grid
        return []
    if now < nxt:
        return []
    # Never try to catch up. A late tick - a slow snapshot, a garbage pause -
    # would otherwise fire every slot it missed at once, which is a burst of
    # sixteenths in one instant and the exact opposite of this lane's job.
    lane_state["next_t"] = max(now, nxt) + grid

    note = _pick(st, edge, rng, tension, lane_state.get("last"))
    if note is None:
        return []
    # `last` has to mean the last note that actually WENT OUT, not the last one
    # proposed: the engine rolls the edge's probability AFTER this returns, and
    # a refused slot that had already claimed `last` let the next slot repeat
    # the note before it. Measured before the fix: 91, 91, 91 with a rest
    # between each - a ping, not a shimmer. `on_skip` puts it back.
    lane_state["prev"], lane_state["last"] = lane_state.get("last"), note
    jitter = float(p.get("jitter_ms", 25)) / 1000.0
    offset = rng.uniform(-jitter, jitter) if jitter > 0 else 0.0
    vel = scaled_vel(edge, int(p.get("vel", 30)) + rng.randint(-4, 4))
    dur = t_beats(edge, st, "dur_beats", 0.5)
    semis = edge.transpose + 12 * edge.octave
    return [Proposal(ch=edge.dst, note=note + semis, vel=vel, dur=dur, lane=edge.lane,
                     t_offset=max(0.0, offset + jitter))]


def on_skip(st: MusicalState, edge: Edge, now: float, lane_state: dict) -> None:
    """A refused slot is a REST, and rests are what make this a texture.

    The other tick algorithms come back early when the dice go against them,
    because for them a refusal costs a whole musical event. Here the refusal IS
    the musical event: at `prob` 0.35 roughly two slots in three are silent,
    which is what 「輕微閃爍」 means. Retrying would fill them in.

    What it does undo is the note this slot had claimed, so the next one is
    still choosing against what was last HEARD.
    """
    if "prev" in lane_state:
        lane_state["last"] = lane_state.pop("prev")
