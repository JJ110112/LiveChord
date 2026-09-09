"""MIE event envelope, proposals and context snapshots (plan §2.1).

Everything that enters the engine is a `MieEvent`; everything an algorithm
wants to play is a `Proposal`.  Both are plain dataclasses so the pipeline
steps (algorithm -> mutation -> constraint) stay pure and unit-testable.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from typing import Optional

_ids = itertools.count(1)


def next_id() -> int:
    return next(_ids)


@dataclass(slots=True, frozen=True)
class ContextSnapshot:
    """Frozen copy of the musical context at the moment an event was made."""

    chord: Optional[str]
    key_tonic: int
    key_mode: str
    bpm: float
    beat_phase: float
    bar_pos: int
    register: str
    human_energy: float


@dataclass(slots=True)
class MieEvent:
    event_id: int
    kind: str                    # note_on | note_off | cc | pc | control | clock
    t_wall: float
    ch: int                      # 1-16
    note: Optional[int] = None
    vel: Optional[int] = None
    cc: Optional[int] = None
    val: Optional[int] = None
    dur_hint: Optional[float] = None
    # lineage
    origin: str = "HUMAN"        # HUMAN | GENERATIVE | CONTROL
    root_id: int = 0
    parent_id: Optional[int] = None
    source_ch: int = 0
    hop: int = 0
    ttl_wall: float = math.inf
    lane: str = "human"
    port: str = "mie_in"         # which input port produced it
    ctx: Optional[ContextSnapshot] = None

    @property
    def is_note_on(self) -> bool:
        return self.kind == "note_on" and (self.vel or 0) > 0

    @property
    def is_note_off(self) -> bool:
        return self.kind == "note_off" or (self.kind == "note_on" and not self.vel)


def human_event(kind: str, t: float, ch: int, note: int | None = None, vel: int | None = None,
                cc: int | None = None, val: int | None = None, port: str = "mie_in") -> MieEvent:
    eid = next_id()
    return MieEvent(event_id=eid, kind=kind, t_wall=t, ch=ch, note=note, vel=vel, cc=cc, val=val,
                    origin="HUMAN", root_id=eid, source_ch=ch, hop=0, port=port)


def control_event(t: float, ch: int, cc: int, val: int, port: str = "uc4") -> MieEvent:
    eid = next_id()
    return MieEvent(event_id=eid, kind="cc", t_wall=t, ch=ch, cc=cc, val=val, origin="CONTROL",
                    root_id=eid, source_ch=ch, lane="control", port=port)


@dataclass(slots=True)
class Proposal:
    """One note an algorithm wants to play, relative to the triggering event.

    kind "on"  -> schedule a (note_on, note_off) pair, `dur` seconds long.
    kind "off" -> release a generated note early (Shadow follows the human release).
    `follow_off=True` marks a note whose real release is driven by the human
    note_off; `dur` is then only the safety cap.
    """

    ch: int
    note: int
    vel: int
    dur: float
    lane: str
    t_offset: float = 0.0
    kind: str = "on"
    follow_off: bool = False
    src_note: Optional[int] = None   # human note this proposal is bound to (for follow_off)
    capture_root: Optional[int] = None   # chord root when a phrase was captured
    capture_quality: str = ""            # and its quality, for diatonic transposition
    pass_id: Optional[tuple] = None      # which repeat of that phrase this note belongs to
    # A note may ask for a wider palette than its lane's. A suspension is a
    # NON-chord tone by definition, so a sus4 proposed by a lane set to
    # `chord` would be snapped straight back to the third and the whole
    # gesture would do nothing and say nothing. None = the edge's own.
    constraint: Optional[str] = None

    def clone(self, **kw) -> "Proposal":
        return replace(self, **kw)
