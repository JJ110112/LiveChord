"""livechord-mie process entry point (plan §0.1).  ProArt 16 only, never the NUC.

    python -m backend.mie [--scene 01] [--mode SAFE] [--port 8810] [--no-ui]
    python -m backend.mie --list-ports

Console keys:  p = PANIC   r = resume   b = BYPASS   s = stats   m <MODE>   q = quit
Any exit path (q, Ctrl+C, crash) runs PANIC first (plan §7-8).
"""

from __future__ import annotations

import argparse
import json
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
    a = ap.parse_args(argv)

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

    io = MidiIO(ports_cfg, None, clock)  # queue attached below
    engine = Engine(scene, instruments, clock=clock, send=io.send, rng=Random(a.seed), mode=a.mode,
                    control_map=control_map)
    io.q = engine.in_queue
    io.open()
    for k, v in io.names.items():
        print(f"[port] {k:<10} = {v}")
    print(f"[scene] {scene.id} {scene.name}  mode={engine.mode}  edges={len(scene.edges)}  scenes={[s['id'] for s in list_scenes()]}")

    ui = None
    if not a.no_ui:
        from .ws_server import UiServer

        def on_msg(msg: dict) -> None:
            t = msg.get("type")
            if t == "panic":
                engine.panic("ui")
            elif t == "resume":
                engine.resume()
            elif t == "mode":
                engine.set_mode(str(msg.get("value", "SAFE")))
            elif t == "set":
                path = str(msg.get("path", ""))
                parts = path.split(".")
                if parts[0] == "global" and len(parts) == 2:
                    engine.set_global(parts[1], msg.get("value"))
                elif parts[0] == "edge" and len(parts) == 3:
                    engine.set_edge(parts[1], parts[2], msg.get("value"))
                elif parts[0] == "inst" and len(parts) == 3:
                    engine.set_instrument(int(parts[1]), parts[2], msg.get("value"))
            elif t == "scene":
                try:
                    engine.load_scene(load_scene(str(msg.get("id", "01"))))
                except Exception as e:  # noqa: BLE001
                    print(f"[scene] load failed: {e}")
            elif t == "playhead":
                _apply_playhead(engine, msg)
            elif t == "action":
                engine.apply_action(str(msg.get("action", "")), int(msg.get("value", 127)))

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
                engine.resume()
                print(f"[mode] {engine.mode}")
            elif c == "b":
                engine.set_mode("BYPASS")
            elif c == "s":
                snap = engine.snapshot()
                print(f"[stats] {snap['stats']} drops={snap['drops']} jitter={snap['jitter']} state={snap['state']}")
            elif c.startswith("m "):
                engine.set_mode(c[2:].strip().upper())
                print(f"[mode] {engine.mode}")
            elif c == "q":
                engine.stop.set()

    threading.Thread(target=console, name="mie-console", daemon=True).start()

    last_report = clock()
    try:
        while not engine.stop.is_set():
            time.sleep(0.25)
            # plan §7-10: UI gone for 5 s while sending -> PANIC (only if a UI was ever connected)
            if ui is not None and ui.client_count == 0 and not engine.bypass and \
                    getattr(ui, "_had_client", False) and time.time() - ui.last_client_seen > 5.0:
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
