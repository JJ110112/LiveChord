"""MIDI ports (mido + python-rtmidi) for the engine.  Extracted from the Phase 0
probe; port names come from data/mie/ports.json (case-insensitive substrings).

Input callbacks run on rtmidi's thread and only push `MieEvent`s into a
queue; the engine thread is the sole consumer.  Output is a plain `send`.
"""

from __future__ import annotations

import queue
import threading
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
        # PANIC can come from the UI thread while the scheduler thread is sending:
        # rtmidi output ports are not thread-safe, so serialize the bytes.
        self._send_lock = threading.Lock()
        self.names: dict[str, Optional[str]] = {}
        self.in_count = 0
        self.uc4_count = 0

    ENUM_TIMEOUT_S = 6.0

    def _list_ports(self):
        """Enumerate MIDI ports, but never wait for ever.

        Windows MIDI enumeration can block indefinitely when the subsystem is
        wedged - a driver left open by a force-killed process, or several
        programs holding every input at once. It happened on 2026-09-08 with
        eight Auracle instances and a monitor open: the engine printed nothing,
        listened on no port and could not be quit, because this call never
        returned and every print comes after it.

        The probe runs in a SUBPROCESS, not a thread. The blocking call keeps
        the GIL, so a thread with a timeout cannot help - the main thread never
        gets to notice the timeout. Only a separate process can be abandoned.
        """
        import json
        import subprocess
        import sys

        code = ("import json,mido;mido.set_backend('mido.backends.rtmidi');"
                "print(json.dumps([mido.get_input_names(),mido.get_output_names()]))")
        try:
            r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=self.ENUM_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise SystemExit(
                f"MIDI 埠列舉超過 {self.ENUM_TIMEOUT_S:.0f} 秒沒有回應——Windows 的 MIDI "
                f"子系統卡住了。通常是某個程式把所有輸入抓著不放（MIDI 監看工具、"
                f"多個 Auracle X 實例），或前一個行程被強制結束時沒有放開驅動。"
                f"先關掉那些程式；還是不行就重新插拔 USB，最後才重開機。")
        if r.returncode != 0:
            raise SystemExit(f"MIDI 埠列舉失敗：{(r.stderr or '').strip()[:300]}")
        ins, outs = json.loads(r.stdout)
        return ins, outs

    def open(self) -> None:
        mido = self.mido
        ins, outs = self._list_ports()
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
            with self._send_lock:
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
