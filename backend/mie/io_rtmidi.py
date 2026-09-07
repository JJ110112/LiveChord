"""MIDI ports (mido + python-rtmidi) for the engine.  Extracted from the Phase 0
probe; port names come from data/mie/ports.json (case-insensitive substrings).

Input callbacks run on rtmidi's thread and only push `MieEvent`s into a
queue; the engine thread is the sole consumer.  Output is a plain `send`.
"""

from __future__ import annotations

import queue
import time
from typing import Callable, Optional

from .events import MieEvent, control_event, human_event


def pick_port(names: list[str], pattern: Optional[str]) -> Optional[str]:
    if not pattern:
        return None
    pat = pattern.lower()
    for n in names:
        if pat in n.lower():
            return n
    return None


class MidiIO:
    """Owns the four ports.  `clock` must be the same clock the engine uses."""

    def __init__(self, cfg: dict, in_queue: "queue.Queue[MieEvent]", clock: Callable[[], float],
                 *, open_out: bool = True):
        import mido
        mido.set_backend("mido.backends.rtmidi")
        self.mido = mido
        self.cfg = cfg
        self.q = in_queue
        self.clock = clock
        self.open_out = open_out
        self.in_port = self.uc4_port = self.hst_out = self.reaper_out = None
        self.names: dict[str, Optional[str]] = {}
        self.in_count = 0
        self.uc4_count = 0

    def open(self) -> None:
        mido = self.mido
        ins, outs = mido.get_input_names(), mido.get_output_names()
        p = self.cfg["ports"]
        name_in = pick_port(ins, p.get("mie_in"))
        if not name_in:
            raise SystemExit(f"MIE In port matching {p.get('mie_in')!r} not found. Inputs: {ins}")
        self.in_port = mido.open_input(name_in, callback=self._on_in)
        self.names["mie_in"] = name_in
        name_uc4 = pick_port(ins, p.get("uc4_in"))
        if name_uc4:
            self.uc4_port = mido.open_input(name_uc4, callback=self._on_uc4)
        self.names["uc4_in"] = name_uc4
        if self.open_out:
            name_out = pick_port(outs, p.get("mie_out"))
            if not name_out:
                raise SystemExit(f"MIE Out port matching {p.get('mie_out')!r} not found. Outputs: {outs}")
            if name_out == name_in:
                raise SystemExit("MIE Out must not be the same endpoint as MIE In (plan §0.2 rule 3)")
            self.hst_out = mido.open_output(name_out)
            self.names["mie_out"] = name_out
            name_rp = pick_port(outs, p.get("reaper_out"))
            if name_rp:
                self.reaper_out = mido.open_output(name_rp)
            self.names["reaper_out"] = name_rp

    def close(self) -> None:
        for prt in (self.in_port, self.uc4_port, self.hst_out, self.reaper_out):
            try:
                if prt:
                    prt.close()
            except Exception:
                pass

    # ---- callbacks (rtmidi thread) ----
    def _on_in(self, msg) -> None:
        now = self.clock()
        self.in_count += 1
        t = msg.type
        if t in ("note_on", "note_off"):
            ev = human_event(t, now, msg.channel + 1, note=msg.note, vel=msg.velocity)
        elif t == "control_change":
            ev = human_event("cc", now, msg.channel + 1, cc=msg.control, val=msg.value)
        elif t == "program_change":
            ev = human_event("pc", now, msg.channel + 1, val=msg.program)
        elif t == "clock":
            ev = human_event("clock", now, 0)
        else:
            return
        self.q.put(ev)

    def _on_uc4(self, msg) -> None:
        self.uc4_count += 1
        if msg.type == "control_change":
            self.q.put(control_event(self.clock(), msg.channel + 1, msg.control, msg.value))
        elif msg.type in ("note_on", "note_off"):
            # treat UC4 pads as buttons: note_on vel>0 == press
            self.q.put(control_event(self.clock(), msg.channel + 1, 1000 + msg.note,
                                     msg.velocity if msg.type == "note_on" else 0))

    # ---- output ----
    def send(self, port: str, msg) -> None:
        prt = self.reaper_out if port == "reaper" else self.hst_out
        if prt is not None:
            prt.send(msg)

    def has_port(self, port: str) -> bool:
        return (self.reaper_out if port == "reaper" else self.hst_out) is not None


def panic_messages(active: Optional[dict] = None):
    """PANIC packet for ONE out port (plan §7-10)."""
    import mido
    msgs = []
    for ch0 in range(16):
        msgs.append(mido.Message("control_change", channel=ch0, control=120, value=0))
        msgs.append(mido.Message("control_change", channel=ch0, control=123, value=0))
        msgs.append(mido.Message("control_change", channel=ch0, control=64, value=0))
    for (ch, note) in sorted(active or {}):
        msgs.append(mido.Message("note_off", channel=ch - 1, note=note, velocity=0))
    return msgs


def wall_clock() -> float:
    return time.perf_counter()
