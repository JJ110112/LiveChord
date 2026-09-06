"""MIE Phase 0 probe — port discovery, 250 ms echo, self-echo (T0) detection, PANIC.

Usage (run from the repo root on the performance PC):

    python backend/mie/probe.py list                 # print every MIDI port Windows sees
    python backend/mie/probe.py run                  # echo test using data/mie/ports.json
    python backend/mie/probe.py run --bypass         # T0: listen + log only, send nothing
    python backend/mie/probe.py run --delay 0        # raw pass-through latency
    python backend/mie/probe.py run --target 11      # echo to CH11 (Iridium)
    python backend/mie/probe.py panic                # send PANIC to every configured out port and exit

Console keys while running:  p = PANIC (then BYPASS)   r = resume   q = quit (PANIC first)

What it verifies (plan §11 Phase 0):
  * self-echo: anything arriving on MIE In that matches a note we sent within
    `self_echo_window_ms` is counted as a LOOP and printed loudly (mioXL rule 3/4 broken)
  * with --bypass, every incoming event is printed with its port + channel so the user can
    play each DIN2-8 instrument and confirm nothing but the Fantom copy arrives
  * scheduler jitter: (actual send time - due time) for every echoed note; p50/p95/max
  * PANIC from UC4 (any button press by default), console, or Ctrl+C / any crash
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import statistics
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "data", "mie", "ports.json")

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without hardware)
# ---------------------------------------------------------------------------


def pick_port(names: list[str], pattern: str | None) -> str | None:
    """Case-insensitive substring match; first hit wins. None pattern → None."""
    if not pattern:
        return None
    pat = pattern.lower()
    for n in names:
        if pat in n.lower():
            return n
    return None


class SelfEchoFilter:
    """Remember what we sent; flag incoming events that look like our own output.

    Keyed on (channel 1-16, note, is_on). Anything we sent within `window_s`
    coming back on the input port means the hardware/mioXL route is looping.
    """

    def __init__(self, window_s: float = 0.030):
        self.window_s = window_s
        self._sent: deque[tuple[float, tuple[int, int, bool]]] = deque()

    def note_sent(self, ch: int, note: int, is_on: bool, t: float) -> None:
        self._sent.append((t, (ch, note, is_on)))
        self._prune(t)

    def is_echo(self, ch: int, note: int, is_on: bool, t: float) -> bool:
        self._prune(t)
        key = (ch, note, is_on)
        return any(k == key for _, k in self._sent)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._sent and self._sent[0][0] < cutoff:
            self._sent.popleft()


class LatencyStats:
    def __init__(self):
        self.samples_ms: list[float] = []

    def add(self, due: float, actual: float) -> None:
        self.samples_ms.append((actual - due) * 1000.0)

    def summary(self) -> str:
        s = self.samples_ms
        if not s:
            return "jitter: no samples yet"
        srt = sorted(s)
        p50 = statistics.median(srt)
        p95 = srt[min(len(srt) - 1, int(round(0.95 * (len(srt) - 1))))]
        return f"jitter n={len(s)} p50={p50:.2f} ms p95={p95:.2f} ms max={srt[-1]:.2f} ms"


def panic_messages(active: dict[tuple[int, int], float] | None = None):
    """Build the PANIC packet for ONE out port: CC120 + CC123 + CC64=0 on CH1-16,
    then an explicit note_off (vel 0) for every note we believe is still sounding
    (some VSTs such as SWAM ignore CC123). Returns mido messages."""
    import mido

    msgs = []
    for ch0 in range(16):
        msgs.append(mido.Message("control_change", channel=ch0, control=120, value=0))
        msgs.append(mido.Message("control_change", channel=ch0, control=123, value=0))
        msgs.append(mido.Message("control_change", channel=ch0, control=64, value=0))
    for (ch, note) in sorted(active or {}):
        msgs.append(mido.Message("note_off", channel=ch - 1, note=note, velocity=0))
    return msgs


def is_panic_trigger(msg, cfg: dict) -> bool:
    """UC4 rule: cc=None → any control_change with value >= value_min (a button press)."""
    if msg.type != "control_change":
        return False
    want_cc = cfg.get("cc")
    if want_cc is not None and msg.control != want_cc:
        return False
    return msg.value >= int(cfg.get("value_min", 127))


# ---------------------------------------------------------------------------
# Probe engine
# ---------------------------------------------------------------------------


@dataclass(order=True)
class Due:
    t: float
    seq: int
    msg: object = field(compare=False)
    port: str = field(compare=False)  # "hst" | "reaper"


class Probe:
    def __init__(self, cfg: dict, *, bypass: bool, delay_ms: float, target_ch: int, log_all: bool):
        import mido

        mido.set_backend("mido.backends.rtmidi")
        self.mido = mido
        self.cfg = cfg
        self.bypass = bypass
        self.delay_s = delay_ms / 1000.0
        self.target_ch = target_ch
        self.transpose = int(cfg["echo"].get("transpose", 0))
        self.max_note_s = float(cfg["echo"].get("max_note_s", 8.0))
        self.log_all = log_all

        self.echo_filter = SelfEchoFilter(cfg.get("self_echo_window_ms", 30) / 1000.0)
        self.stats = LatencyStats()
        self.loop_count = 0
        self.in_count = 0
        self.uc4_count = 0
        self.active: dict[tuple[int, int], float] = {}  # (ch, note) -> t_on
        self.human_to_gen: dict[int, int] = {}  # human note -> generated note
        self.heap: list[Due] = []
        self.seq = 0
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.stop = threading.Event()
        self.t0 = time.perf_counter()

        self.in_port = self.uc4_port = self.hst_out = self.reaper_out = None

    # ---- ports ----
    def open_ports(self) -> None:
        mido = self.mido
        ins, outs = mido.get_input_names(), mido.get_output_names()
        p = self.cfg["ports"]
        name_in = pick_port(ins, p.get("mie_in"))
        if not name_in:
            raise SystemExit(f"MIE In port matching {p.get('mie_in')!r} not found. Inputs: {ins}")
        self.in_port = mido.open_input(name_in, callback=self._on_in)
        print(f"[port] MIE In    = {name_in}")

        name_uc4 = pick_port(ins, p.get("uc4_in"))
        if name_uc4:
            self.uc4_port = mido.open_input(name_uc4, callback=self._on_uc4)
            print(f"[port] UC4 In    = {name_uc4}")
        else:
            print(f"[port] UC4 In    = (not found: {p.get('uc4_in')!r}) — console 'p' still works")

        if not self.bypass:
            name_out = pick_port(outs, p.get("mie_out"))
            if not name_out:
                raise SystemExit(f"MIE Out port matching {p.get('mie_out')!r} not found. Outputs: {outs}")
            if name_out == name_in:
                raise SystemExit("MIE Out must not be the same endpoint as MIE In (plan §0.2 rule 3)")
            self.hst_out = mido.open_output(name_out)
            print(f"[port] MIE Out   = {name_out}")
            name_rp = pick_port(outs, p.get("reaper_out"))
            if name_rp:
                self.reaper_out = mido.open_output(name_rp)
                print(f"[port] REAPER    = {name_rp}")
            else:
                print(f"[port] REAPER    = (not found: {p.get('reaper_out')!r}) — CH1 events will be dropped")
        else:
            print("[mode] BYPASS — nothing will be sent; watching for stray input on DIN2-8")

    def close_ports(self) -> None:
        for prt in (self.in_port, self.uc4_port, self.hst_out, self.reaper_out):
            try:
                if prt:
                    prt.close()
            except Exception:
                pass

    # ---- helpers ----
    def _ts(self) -> str:
        return f"{time.perf_counter() - self.t0:8.3f}s"

    def _out_for(self, ch: int):
        return self.reaper_out if ch == 1 else self.hst_out

    def _send_now(self, msg, ch: int) -> None:
        prt = self._out_for(ch)
        if prt is None:
            return
        prt.send(msg)
        if msg.type in ("note_on", "note_off"):
            is_on = msg.type == "note_on" and msg.velocity > 0
            self.echo_filter.note_sent(ch, msg.note, is_on, time.perf_counter())

    def _schedule(self, t_due: float, msg, ch: int) -> None:
        with self.cv:
            self.seq += 1
            heapq.heappush(self.heap, Due(t_due, self.seq, msg, "reaper" if ch == 1 else "hst"))
            self.cv.notify()

    # ---- input callbacks (rtmidi thread) ----
    def _on_in(self, msg) -> None:
        now = time.perf_counter()
        self.in_count += 1
        if msg.type not in ("note_on", "note_off"):
            if self.log_all or self.bypass:
                print(f"{self._ts()} IN  ch{getattr(msg, 'channel', -1) + 1:<2} {msg}")
            return
        ch = msg.channel + 1
        is_on = msg.type == "note_on" and msg.velocity > 0
        if self.echo_filter.is_echo(ch, msg.note, is_on, now):
            self.loop_count += 1
            print(f"{self._ts()} !!! LOOP #{self.loop_count}: our own {'on' if is_on else 'off'} "
                  f"ch{ch} note{msg.note} came back on MIE In — check mioXL rule 3/4 / Fantom Thru")
            return
        if self.log_all or self.bypass:
            print(f"{self._ts()} IN  ch{ch:<2} {'on ' if is_on else 'off'} note={msg.note:<3} vel={msg.velocity}")
        if self.bypass:
            return
        self._echo(msg, ch, is_on, now)

    def _echo(self, msg, human_ch: int, is_on: bool, now: float) -> None:
        mido = self.mido
        tgt = self.target_ch
        if tgt == human_ch:
            return  # never stack generated notes onto the instrument the human is playing
        due = now + self.delay_s
        if is_on:
            gen = max(0, min(127, msg.note + self.transpose))
            self.human_to_gen[msg.note] = gen
            self._schedule(due, mido.Message("note_on", channel=tgt - 1, note=gen, velocity=msg.velocity), tgt)
            # safety net: forced note_off even if the human note_off never arrives
            self._schedule(due + self.max_note_s, mido.Message("note_off", channel=tgt - 1, note=gen, velocity=0), tgt)
        else:
            gen = self.human_to_gen.pop(msg.note, None)
            if gen is not None:
                self._schedule(due, mido.Message("note_off", channel=tgt - 1, note=gen, velocity=0), tgt)

    def _on_uc4(self, msg) -> None:
        self.uc4_count += 1
        if self.log_all:
            print(f"{self._ts()} UC4 {msg}")
        if is_panic_trigger(msg, self.cfg.get("panic", {})):
            print(f"{self._ts()} UC4 → PANIC ({msg})")
            self.panic()

    # ---- scheduler thread ----
    def _scheduler(self) -> None:
        while not self.stop.is_set():
            with self.cv:
                while not self.heap and not self.stop.is_set():
                    self.cv.wait(0.05)
                if self.stop.is_set():
                    return
                item = self.heap[0]
                wait = item.t - time.perf_counter()
                if wait > 0.0015:
                    self.cv.wait(min(wait - 0.001, 0.05))
                    continue
                heapq.heappop(self.heap)
            # spin the last ~1 ms for accuracy
            while time.perf_counter() < item.t:
                pass
            if self.bypass:
                continue
            ch = item.msg.channel + 1
            key = (ch, item.msg.note)
            if item.msg.type == "note_off" and key not in self.active:
                continue  # already released (forced-off after normal off, or after PANIC)
            actual = time.perf_counter()
            self._send_now(item.msg, ch)
            self.stats.add(item.t, actual)
            if item.msg.type == "note_on":
                self.active[key] = actual
            else:
                self.active.pop(key, None)
            print(f"{self._ts()} OUT ch{ch:<2} {item.msg.type:<8} note={item.msg.note:<3} "
                  f"late={ (actual - item.t) * 1000:+.2f} ms")

    # ---- PANIC ----
    def panic(self) -> None:
        with self.cv:
            self.heap.clear()
            active = dict(self.active)
            self.active.clear()
            self.human_to_gen.clear()
            self.bypass = True
        for name, prt in (("HST", self.hst_out), ("REAPER", self.reaper_out)):
            if prt is None:
                continue
            for m in panic_messages(active):
                prt.send(m)
            print(f"{self._ts()} PANIC sent to {name} ({len(active)} explicit note_off) — now BYPASS, press r to resume")

    def resume(self) -> None:
        if self.hst_out is None:
            print("cannot resume: started in --bypass (no out port opened)")
            return
        self.bypass = False
        print(f"{self._ts()} resumed")

    # ---- console ----
    def _console(self) -> None:
        while not self.stop.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:
                return
            c = line.strip().lower()
            if c == "p":
                self.panic()
            elif c == "r":
                self.resume()
            elif c == "q":
                self.stop.set()
            elif c == "s":
                print(self.report())

    def report(self) -> str:
        return (f"[report] in={self.in_count} uc4={self.uc4_count} loops={self.loop_count} "
                f"active={len(self.active)} | {self.stats.summary()}")

    # ---- main ----
    def run(self) -> None:
        self.open_ports()
        print("[keys] p=PANIC  r=resume  s=stats  q=quit   (Ctrl+C also PANICs)")
        th_s = threading.Thread(target=self._scheduler, name="mie-sched", daemon=True)
        th_c = threading.Thread(target=self._console, name="mie-console", daemon=True)
        th_s.start()
        th_c.start()
        last = time.perf_counter()
        try:
            while not self.stop.is_set():
                time.sleep(0.25)
                if time.perf_counter() - last >= 10:
                    print(self.report())
                    last = time.perf_counter()
                # stuck-note watchdog (independent of the scheduled forced-off)
                now = time.perf_counter()
                for (ch, note), t_on in list(self.active.items()):
                    if now - t_on > self.max_note_s + 0.5:
                        print(f"{self._ts()} WATCHDOG forcing off ch{ch} note{note}")
                        self._send_now(self.mido.Message("note_off", channel=ch - 1, note=note, velocity=0), ch)
                        self.active.pop((ch, note), None)
        except KeyboardInterrupt:
            print("\nCtrl+C")
        finally:
            self.stop.set()
            if not self.bypass or self.active:
                self.panic()
            print(self.report())
            if self.loop_count:
                print(f"T0 FAILED: {self.loop_count} self-echo event(s) — fix routing before Phase 1")
            elif self.in_count:
                print("T0 OK: no self-echo observed")
            self.close_ports()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_list() -> None:
    import mido

    mido.set_backend("mido.backends.rtmidi")
    print("MIDI inputs:")
    for n in mido.get_input_names():
        print(f"  {n}")
    print("MIDI outputs:")
    for n in mido.get_output_names():
        print(f"  {n}")
    print("\nCopy the substrings you want into data/mie/ports.json → ports.*")


def cmd_panic(cfg: dict) -> None:
    import mido

    mido.set_backend("mido.backends.rtmidi")
    outs = mido.get_output_names()
    sent = 0
    for key in ("mie_out", "reaper_out"):
        name = pick_port(outs, cfg["ports"].get(key))
        if not name:
            print(f"[panic] {key}: no port matching {cfg['ports'].get(key)!r}")
            continue
        with mido.open_output(name) as prt:
            for m in panic_messages():
                prt.send(m)
        print(f"[panic] sent to {name}")
        sent += 1
    if not sent:
        raise SystemExit("no out port found — nothing sent")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="print all MIDI ports")
    sub.add_parser("panic", help="send PANIC to configured out ports and exit")
    r = sub.add_parser("run", help="echo / T0 probe")
    r.add_argument("--bypass", action="store_true", help="listen and log only; open no out port")
    r.add_argument("--delay", type=float, default=None, help="echo delay in ms (config default 250)")
    r.add_argument("--target", type=int, default=None, help="echo target channel 1-16 (config default)")
    r.add_argument("--log-all", action="store_true", help="print every incoming event even outside --bypass")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        cmd_list()
        return
    cfg = load_config(a.config)
    if a.cmd == "panic":
        cmd_panic(cfg)
        return
    delay = cfg["echo"]["delay_ms"] if a.delay is None else a.delay
    target = cfg["echo"]["target_ch"] if a.target is None else a.target
    if not 1 <= target <= 16:
        raise SystemExit("--target must be 1-16")
    Probe(cfg, bypass=a.bypass, delay_ms=delay, target_ch=target, log_all=a.log_all).run()


if __name__ == "__main__":
    main()
