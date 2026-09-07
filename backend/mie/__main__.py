"""livechord-mie process entry point (plan §0.1).  ProArt 16 only, never the NUC.

    python -m backend.mie [--scene 01] [--mode SAFE] [--port 8810] [--no-ui]
    python -m backend.mie --list-ports

Console keys:  p = PANIC   r = resume   b = BYPASS   s = stats   m <MODE>   q = quit
Any exit path (q, Ctrl+C, crash) runs PANIC first (plan §7-8).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from random import Random

from .engine import Engine
from .graph import DATA_DIR, load_instruments, load_scene, list_scenes
from .io_rtmidi import MidiIO, wall_clock


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", default="01", help="scene id or path (data/mie/scenes)")
    ap.add_argument("--mode", default=None, help="OFF|BYPASS|SAFE|AMBIENT|INTERACTIVE|GENERATIVE|CHAOS (default: scene)")
    ap.add_argument("--ports", default=os.path.join(DATA_DIR, "ports.json"))
    ap.add_argument("--instruments", default=os.path.join(DATA_DIR, "instruments.json"))
    ap.add_argument("--control-map", default=os.path.join(DATA_DIR, "control_map.json"))
    ap.add_argument("--port", type=int, default=8810, help="UI http/websocket port")
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--list-ports", action="store_true")
    ap.add_argument("--log-file", default=None, help="also append the fault log here")
    ap.add_argument("--event-log", default=None,
                    help="session event log (default: data/logs/mie/session-<time>.jsonl)")
    ap.add_argument("--no-event-log", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)

    handlers: list = [logging.StreamHandler(sys.stderr)]
    if a.log_file:
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers, force=True)

    if a.list_ports:
        import mido
        mido.set_backend("mido.backends.rtmidi")
        print("inputs:", *mido.get_input_names(), sep="\n  ")
        print("outputs:", *mido.get_output_names(), sep="\n  ")
        return 0

    ports_cfg = load_json(a.ports)
    instruments = load_instruments(a.instruments)
    control_map = {k: v for k, v in load_json(a.control_map).items() if not k.startswith("_")}
    scene = load_scene(a.scene)
    clock = wall_clock

    evlog = None
    if not a.no_event_log:
        from .eventlog import EventLog
        evlog = EventLog(a.event_log)

    io = MidiIO(ports_cfg, None, clock)  # queue attached below
    engine = Engine(scene, instruments, clock=clock, send=io.send, rng=Random(a.seed), mode=a.mode,
                    control_map=control_map, event_sink=evlog.log if evlog else None)
    io.q = engine.in_queue
    io.open()
    for k, v in io.names.items():
        print(f"[port] {k:<10} = {v}")
    print(f"[scene] {scene.id} {scene.name}  mode={engine.mode}  edges={len(scene.edges)}  scenes={[s['id'] for s in list_scenes()]}")
    if evlog is not None:
        evlog.header(scene=scene.id, mode=engine.mode, ports=io.names,
                     edges=[e.to_dict() for e in scene.edges])
        evlog.snapshots_from(engine, engine.stop)
        print(f"[log] {evlog.path}")

    ui = None
    if not a.no_ui:
        from .ws_server import UiServer

        def on_msg(msg: dict) -> None:
            """WebSocket thread. Nothing here touches engine state directly:
            every change is handed to the engine thread via `submit` (plan §1).
            PANIC is the one exception - it must not wait behind the queue."""
            t = msg.get("type")
            if t == "panic":
                engine.panic("ui")
            elif t == "resume":
                engine.submit(engine.resume)
            elif t == "mode":
                engine.submit(engine.set_mode, str(msg.get("value", "SAFE")))
            elif t == "set":
                parts = str(msg.get("path", "")).split(".")
                if parts[0] == "global" and len(parts) == 2:
                    engine.submit(engine.set_global, parts[1], msg.get("value"))
                elif parts[0] == "edge" and len(parts) == 3:
                    engine.submit(engine.set_edge, parts[1], parts[2], msg.get("value"))
                elif parts[0] == "inst" and len(parts) == 3:
                    engine.submit(engine.set_instrument, int(parts[1]), parts[2], msg.get("value"))
            elif t == "scene":
                try:
                    sc = load_scene(str(msg.get("id", "01")))       # file I/O off the engine thread
                except Exception:
                    logging.getLogger("mie.ui").exception("mie: scene load failed")
                    return
                engine.submit(engine.load_scene, sc)
            elif t == "playhead":
                engine.submit(_apply_playhead, engine, msg)
            elif t == "action":
                engine.submit(engine.apply_action, str(msg.get("action", "")), int(msg.get("value", 127)))

        ui = UiServer(engine, port=a.port, on_message=on_msg)
        ui.start()
        print(f"[ui] http://127.0.0.1:{a.port}/mie   ws://127.0.0.1:{a.port}/ws")

    sched_th = engine.sched.start()
    eng_th = engine.start()
    print("[keys] p=PANIC  r=resume  b=BYPASS  s=stats  m <MODE>  q=quit   (Ctrl+C also PANICs)")

    def console() -> None:
        while not engine.stop.is_set():
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:
                return
            c = line.strip()
            if c == "p":
                engine.panic("console")
            elif c == "r":
                engine.submit(engine.resume)
                print("[mode] resume queued")
            elif c == "b":
                engine.submit(engine.set_mode, "BYPASS")
            elif c == "s":
                snap = engine.snapshot()
                print(f"[stats] {snap['stats']} drops={snap['drops']} jitter={snap['jitter']} state={snap['state']}")
            elif c.startswith("m "):
                engine.submit(engine.set_mode, c[2:].strip().upper())
            elif c == "q":
                engine.stop.set()

    threading.Thread(target=console, name="mie-console", daemon=True).start()

    last_report = clock()
    try:
        while not engine.stop.is_set():
            time.sleep(0.25)
            # plan §7-10: UI gone for 5 s while sending -> PANIC (only if a UI was ever connected)
            # The grace has to outlast a page reload: on 2026-09-07 a hard refresh
            # to pick up a new ?v= tripped the 5 s window, the engine sat in
            # BYPASS, and the player played 40 seconds into silence without a
            # sign. The UC4 PANIC button needs no panel, so waiting longer for
            # the operator's browser costs little.
            grace = float(engine.scene.globals.get("ws_grace_s", 12.0))
            if ui is not None and ui.client_count == 0 and not engine.bypass and \
                    getattr(ui, "_had_client", False) and time.time() - ui.last_client_seen > grace:
                engine.panic("ws_lost")
                ui._had_client = False
            if ui is not None and ui.client_count:
                ui._had_client = True
            if clock() - last_report >= 10:
                s = engine.snapshot()
                print(f"[report] mode={s['mode']} human={s['stats']['human_notes']} gen={s['stats']['gen_sent']} "
                      f"drop={s['stats']['dropped']} loops={s['stats']['loops']} jitter={s['jitter']} "
                      f"chord={s['state']['chord']} energy={s['state']['energy']}")
                last_report = clock()
    except KeyboardInterrupt:
        print("\nCtrl+C")
    finally:
        engine.stop.set()
        engine.sched.stop.set()
        engine.panic("exit")
        if ui is not None:
            ui.shutdown()
        io.close()
        s = engine.snapshot()
        print(f"[final] {s['stats']} jitter={s['jitter']}")
        if evlog is not None:
            evlog.close({"stats": s["stats"], "drops": s["drops"], "jitter": s["jitter"],
                         "edge_fires": {e.id: engine.edge_fires.get(e.id, 0) for e in engine.graph.edges}})
            print(f"[log] {evlog.stats}")
    return 0


def _apply_playhead(engine: Engine, msg: dict) -> None:
    """Player context (plan §10): chord/key/tempo from the chord JSON timeline."""
    from .harmony import ChordInfo
    from .scales import NOTE_NAMES
    now = engine.clock()
    key = msg.get("key")
    if key:
        parts = str(key).replace("m", " minor").split()
        tonic = parts[0].replace("♭", "b")
        flats = {"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10}
        pc = flats.get(tonic, NOTE_NAMES.index(tonic) if tonic in NOTE_NAMES else 0)
        engine.st.set_key(pc, parts[1] if len(parts) > 1 else "major", "player")
    if msg.get("bpm"):
        engine.st.set_tempo(float(msg["bpm"]), now, "player",
                            downbeat_t=now - float(msg.get("beat_phase", 0.0)) * 60.0 / float(msg["bpm"]),
                            beats_per_bar=int(msg.get("beats_per_bar", 4)))
    chord = msg.get("chord")
    if chord and msg.get("chord_pcs"):
        pcs = frozenset(int(x) % 12 for x in msg["chord_pcs"])
        engine.st.set_chord(ChordInfo(str(chord), int(msg.get("chord_root", min(pcs))) % 12, "", pcs, now))


if __name__ == "__main__":
    sys.exit(main())
