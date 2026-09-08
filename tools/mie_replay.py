"""Replay a recorded MIE take through the engine, and A/B it under changed settings.

This is the tool that found every musical fix this week. None of them were
found by looking at the engine's output: each one came from taking the
player's OWN performance, feeding the human events back through the real
engine with one setting changed, and comparing the two runs.

    decay 0.55 ->  1 transposition / 147 notes
    decay 0.75 ->  6 transpositions / 233 notes

The point is that the human side is FIXED. The same notes, the same pedal, the
same timing, the same seed - so any difference in the output belongs to the
setting you changed and to nothing else. Judging a change by playing again
cannot do this: you never play the same take twice, and the engine's own dice
move as well.

MIE runs on the ProArt 16 only; this tool needs no MIDI hardware and opens no
ports, so it is safe to run while the instruments are asleep.

Usage
-----
    # what happened, summarised
    python tools/mie_replay.py data/logs/mie/session-20260908-225456.jsonl

    # the same take under a different scene
    python tools/mie_replay.py <log> --scene test01

    # A/B one setting: baseline first, then each variant
    python tools/mie_replay.py <log> --set phrase_modx.decay=0.55 \
                                     --set phrase_modx.decay=0.75

    # sweep one setting over several values
    python tools/mie_replay.py <log> --sweep phrase_modx.decay=0.55,0.65,0.75,0.85

    # several settings moving together, as one variant
    python tools/mie_replay.py <log> --set "phrase_modx.decay=0.75,phrase_modx.repeats=3"

    # global knobs too, and the notes each lane produced
    python tools/mie_replay.py <log> --set global.density=0.8 --by-lane

    # write the replayed events out, to draw or diff later
    python tools/mie_replay.py <log> --out-dir data/logs/mie/replay

Reading the table
-----------------
`human` is identical in every row by construction - if it is not, the run
diverged and the comparison is void, so it is printed as a check. Everything
else is the engine's answer to the same performance.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys
from random import Random
from typing import Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.mie.engine import Engine                       # noqa: E402
from backend.mie.events import human_event                  # noqa: E402
from backend.mie.fakes import FakeClock, FakeMidiOut        # noqa: E402
from backend.mie.graph import (DATA_DIR, load_instruments,  # noqa: E402
                               load_scene)

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
STEP_S = 0.005          # the engine tick the takes were recorded at


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


# --------------------------------------------------------------- the take
class Take:
    """The human half of a recorded session: notes, pedal, and nothing else.

    Deliberately NOT the engine's own events. Replaying those would replay the
    answer as well as the question, which is the one thing this tool must not
    do.
    """

    def __init__(self, path: str):
        self.path = path
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        self.rows = rows
        self.header = rows[0] if rows and rows[0].get("type") == "session" else {}
        self.notes = [r for r in rows if r["type"] in ("human", "human_off")]
        self.pedal = [r for r in rows if r["type"] == "pedal"]
        if not self.notes:
            raise SystemExit(f"{path}: no human events - nothing to replay")
        self.end = max(r["t"] for r in self.notes)

    @property
    def scene_id(self) -> str:
        """The scene the take was actually PLAYED on.

        The header records the scene the engine started with, which is often
        not the one that was played: on the 22:54 take the engine came up on
        `01` and the player switched to `test01` 52 s in, before touching a
        key. Replaying the header's scene would have measured the wrong graph
        and said nothing about it.
        """
        switches = [r for r in self.rows if r.get("type") == "scene" and r.get("id")]
        if switches:
            return str(switches[-1]["id"])
        return str(self.header.get("scene") or "01")

    @property
    def scene_changed_while_playing(self) -> Optional[float]:
        """When a scene switch happened AFTER the first note, if it did.

        A replay runs one scene from end to end, so a take that changed graph
        halfway through cannot be reproduced and the numbers would be a blend
        of two engines. Say so rather than quietly averaging them.
        """
        first_note = min(r["t"] for r in self.notes)
        later = [r["t"] for r in self.rows
                 if r.get("type") == "scene" and r.get("id") and r["t"] > first_note]
        return later[0] if later else None

    def describe(self) -> str:
        started = self.header.get("started", "?")
        n = sum(1 for r in self.notes if r["type"] == "human")
        return (f"{os.path.basename(self.path)}  started {started}  "
                f"scene {self.scene_id}  {n} human notes  {self.end:.0f}s")


# ------------------------------------------------------------- one run
def parse_overrides(spec: str) -> list[tuple[str, str, object]]:
    """`phrase_modx.decay=0.75,global.density=0.8` -> [(target, key, value)]."""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part or "." not in part.split("=")[0]:
            raise SystemExit(f"--set expects edge.key=value or global.key=value, got {part!r}")
        path, raw = part.split("=", 1)
        target, key = path.rsplit(".", 1)
        try:
            value: object = json.loads(raw)
        except ValueError:
            value = raw                       # a bare string like `function`
        out.append((target.strip(), key.strip(), value))
    return out


def apply_overrides(scene, overrides) -> None:
    for target, key, value in overrides:
        if target == "global":
            scene.globals[key] = value
            continue
        edge = next((e for e in scene.edges if e.id == target), None)
        if edge is None:
            raise SystemExit(f"no edge {target!r} in scene {scene.id} "
                             f"(have: {', '.join(e.id for e in scene.edges)})")
        # a declared field is a real attribute; everything else is a param.
        # Writing `prob` into params would silently do nothing - the engine
        # reads it off the dataclass.
        if hasattr(edge, key) and key != "params":
            setattr(edge, key, value)
        else:
            edge.params[key] = value


def run_once(take: Take, scene_id: str, overrides, seed: int, mode: str,
             tail_s: float = 20.0) -> dict:
    clock = FakeClock(0.0)
    out = FakeMidiOut(clock)
    scene = load_scene(scene_id)
    apply_overrides(scene, overrides)
    instruments = load_instruments(os.path.join(DATA_DIR, "instruments.json"))
    with open(os.path.join(DATA_DIR, "control_map.json"), encoding="utf-8") as f:
        control_map = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    events: list[dict] = []
    eng = Engine(scene, instruments, clock=clock, send=out, rng=Random(seed),
                 mode=mode, control_map=control_map, event_sink=events.append)

    i = j = 0
    t = 0.0
    end = take.end + tail_s
    while t < end:
        while i < len(take.notes) and take.notes[i]["t"] <= t:
            e = take.notes[i]
            i += 1
            eng.in_queue.put(human_event(
                "note_on" if e["type"] == "human" else "note_off",
                t, e["ch"], e["note"], e.get("vel", 64) or 64))
        while j < len(take.pedal) and take.pedal[j]["t"] <= t:
            pe = take.pedal[j]
            j += 1
            eng.in_queue.put(human_event("cc", t, pe["ch"], cc=64, val=pe["val"]))
        eng.step(t)
        t += STEP_S
        clock.t = t
    # let anything still queued be released rather than counting as a stuck note
    eng.panic("replay_end")
    for _ in range(400):
        t += STEP_S
        clock.t = t
        eng.step(t)
    return {"events": events, "engine": eng, "midi": out}


# ------------------------------------------------------------- measuring
def measure(res: dict) -> dict:
    events = res["events"]
    eng = res["engine"]
    kinds = collections.Counter(e["type"] for e in events)
    gens = [e for e in events if e["type"] == "gen"]
    human = [e for e in events if e["type"] == "human"]
    notes = [g["note"] for g in gens]
    lanes = collections.Counter(g["lane"] for g in gens)
    per_edge = collections.Counter(g["edge"] for g in gens)
    durs = [g.get("dur_ms", 0) for g in gens if not g.get("follow")]
    # median pitch per lane. This is the measurement that caught the strings
    # lane pinning itself to its own ceiling (median B5, range B4-E6) - a
    # register problem is invisible in a note COUNT and obvious in this one.
    by_lane_notes = collections.defaultdict(list)
    for g in gens:
        by_lane_notes[g["lane"]].append(g["note"])
    m = {
        "lane_pitch": {k: statistics.median(v) for k, v in by_lane_notes.items()},
        "human": len(human),
        "gen": len(gens),
        "per_human": len(gens) / len(human) if human else 0.0,
        "dropped": eng.stats.get("dropped", 0),
        "muted": eng.stats.get("muted", 0),
        "loops": eng.stats.get("loops", 0),
        "errors": kinds.get("error", 0),
        "phrase_shift": kinds.get("phrase_shift", 0),
        "fires": dict(eng.edge_fires),
        "lanes": lanes,
        "per_edge": per_edge,
        "median_note": statistics.median(notes) if notes else 0,
        "median_dur_ms": statistics.median(durs) if durs else 0,
        "drop_reasons": dict(eng.drop_reasons),
    }
    return m


LABEL_W = 44


def label_for(overrides) -> str:
    if not overrides:
        return "as recorded"
    parts = []
    for t, k, v in overrides:
        # the edge id is usually the same across a sweep and the key is the
        # interesting half, so drop the id when every override shares one
        parts.append(f"{t}.{k}={json.dumps(v)}")
    return " ".join(parts)


def fit(label: str) -> str:
    """Keep the columns lined up; a run-on label is worse than a clipped one."""
    return label if len(label) <= LABEL_W else label[:LABEL_W - 1] + "…"


def print_table(rows: list[tuple[str, dict]], by_lane: bool) -> None:
    head = (f'{"variant":<{LABEL_W}} {"human":>6} {"gen":>6} {"/human":>7} {"drop":>5} '
            f'{"mute":>5} {"loop":>5} {"err":>4} {"shift":>6}')
    print(head)
    print("-" * len(head))
    base = rows[0][1] if rows else None
    for label, m in rows:
        print(f'{fit(label):<{LABEL_W}} {m["human"]:6d} {m["gen"]:6d} {m["per_human"]:7.2f} '
              f'{m["dropped"]:5d} {m["muted"]:5d} {m["loops"]:5d} {m["errors"]:4d} '
              f'{m["phrase_shift"]:6d}')
    if base is not None and any(m["human"] != base["human"] for _, m in rows):
        print("\n!! the human side differs between runs - the comparison is void")

    if by_lane:
        every = sorted({k for _, m in rows for k in m["lanes"]})
        print()
        print(f'{"notes per lane":<{LABEL_W}} ' + " ".join(f"{k:>10}" for k in every))
        for label, m in rows:
            print(f'{fit(label):<{LABEL_W}} ' + " ".join(f'{m["lanes"].get(k, 0):10d}' for k in every))
        print()
        print(f'{"median pitch":<{LABEL_W}} ' + " ".join(f"{k:>10}" for k in every))
        for label, m in rows:
            cells = []
            for k in every:
                v = m["lane_pitch"].get(k)
                cells.append(f"{note_name(int(v)):>10}" if v is not None else f'{"-":>10}')
            print(f'{fit(label):<{LABEL_W}} ' + " ".join(cells))

    edges = sorted({k for _, m in rows for k in m["fires"]})
    if len(rows) > 1 and edges:
        print()
        print(f'{"fires":<{LABEL_W}} ' + " ".join(f"{e[:11]:>11}" for e in edges))
        for label, m in rows:
            print(f'{fit(label):<{LABEL_W}} ' + " ".join(f'{m["fires"].get(e, 0):11d}' for e in edges))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="a session JSONL under data/logs/mie/")
    ap.add_argument("--scene", default=None,
                    help="scene to replay through (default: the one the take was recorded on)")
    ap.add_argument("--set", dest="sets", action="append", default=[],
                    help="one variant: edge.key=value[,edge.key=value...]; repeatable")
    ap.add_argument("--sweep", default=None,
                    help="edge.key=v1,v2,v3 - one variant per value")
    ap.add_argument("--seed", type=int, default=7,
                    help="the engine's dice; the same seed for every variant (default 7)")
    ap.add_argument("--mode", default="INTERACTIVE")
    ap.add_argument("--by-lane", action="store_true", help="also break the notes down per lane")
    ap.add_argument("--out-dir", default=None,
                    help="write each variant's events out as JSONL")
    ap.add_argument("--no-baseline", action="store_true",
                    help="skip the unchanged run (only meaningful with --set/--sweep)")
    a = ap.parse_args(argv)

    take = Take(a.log)
    scene_id = a.scene or take.scene_id
    print(take.describe())
    print(f"replaying through scene {scene_id}, seed {a.seed}, mode {a.mode}")
    switched = take.scene_changed_while_playing
    if switched is not None:
        print(f"!! the take changed scene at t={switched:.1f}s, after playing had started - "
              f"a replay runs ONE scene throughout, so these numbers are not that take")
    print()

    variants: list[list] = []
    if not a.no_baseline:
        variants.append([])
    for spec in a.sets:
        variants.append(parse_overrides(spec))
    if a.sweep:
        if "=" not in a.sweep:
            raise SystemExit("--sweep expects edge.key=v1,v2,v3")
        path, raw = a.sweep.split("=", 1)
        for v in raw.split(","):
            variants.append(parse_overrides(f"{path}={v.strip()}"))
    if not variants:
        variants.append([])

    rows = []
    for ov in variants:
        res = run_once(take, scene_id, ov, a.seed, a.mode)
        m = measure(res)
        label = label_for(ov)
        rows.append((label, m))
        if a.out_dir:
            os.makedirs(a.out_dir, exist_ok=True)
            safe = (label.replace(" ", "_").replace("=", "-").replace('"', "")
                        .replace("/", "-") or "baseline")
            p = os.path.join(a.out_dir, f"{os.path.basename(a.log)[:-6]}.{safe}.jsonl")
            with open(p, "w", encoding="utf-8") as f:
                for e in res["events"]:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            print(f"[out] {p}")

    print_table(rows, a.by_lane)
    worst = max((m["drop_reasons"] for _, m in rows), key=len, default={})
    if worst:
        print(f"\ndrop reasons: {worst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
