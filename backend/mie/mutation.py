"""Step 6: mutations (plan §5).  Pure; the constraint step always runs after this.

Fan-out mutations (chordify / rhythm) are only produced for HUMAN-origin
sources; the safety layer (§7-4) enforces that independently.
"""

from __future__ import annotations

from random import Random

from .events import Proposal

CHORD_SHAPES = {"octave": [0, 12], "fifth": [0, 7], "triad": [0, 4, 7], "seventh": [0, 4, 7, 11]}
FANOUT_TYPES = {"chordify", "rhythm"}


def _weighted(rng: Random, choices: list, weights: list | None):
    if not weights:
        return rng.choice(choices)
    tot = float(sum(weights))
    x = rng.random() * tot
    for c, w in zip(choices, weights):
        if x < w:
            return c
        x -= w
    return choices[-1]


def apply_one(p: Proposal, m: dict, rng: Random, beat_s: float) -> list[Proposal]:
    t = m.get("type")
    if t == "octave":
        return [p.clone(note=p.note + 12 * int(_weighted(rng, m.get("choices", [-1, 0, 1]), m.get("weights"))))]
    if t == "interval":
        return [p.clone(note=p.note + int(_weighted(rng, m.get("choices", [3, 5, 7]), m.get("weights"))))]
    if t == "dur":
        return [p.clone(dur=p.dur * float(_weighted(rng, m.get("scale", [0.5, 1, 2]), m.get("weights"))))]
    if t == "vel":
        j = int(m.get("jitter", 12))
        return [p.clone(vel=max(1, min(127, p.vel + rng.randint(-j, j))))]
    if t == "chordify":
        shape = CHORD_SHAPES.get(m.get("shape", "octave"), [0, 12])
        spread = float(m.get("spread_ms", 12)) / 1000.0
        return [p.clone(note=p.note + iv, t_offset=p.t_offset + i * spread) for i, iv in enumerate(shape)]
    if t == "rhythm":
        pattern = str(m.get("pattern", "x---"))
        grid = float(m.get("grid_beats", 0.25)) * beat_s
        out = []
        k = 0
        for i, c in enumerate(pattern):
            if c == "x":
                out.append(p.clone(t_offset=p.t_offset + i * grid, vel=max(1, int(p.vel * (0.85 ** k))), dur=min(p.dur, grid)))
                k += 1
        return out or [p]
    return [p]


def apply(props: list[Proposal], mutations: list[dict], rng: Random, *, chaos: float, origin: str,
          beat_s: float) -> list[Proposal]:
    out: list[Proposal] = []
    for p in props:
        cur = [p]
        for m in mutations or []:
            if m.get("type") in FANOUT_TYPES and origin != "HUMAN":
                continue  # §7-4: generated sources stay one-to-one
            nxt = []
            for c in cur:
                nxt.extend(apply_one(c, m, rng, beat_s))
            cur = nxt
        # chaos: extra random mutation / delay (plan §5)
        if chaos > 0:
            nxt = []
            for c in cur:
                if rng.random() < chaos * 0.5:
                    c = apply_one(c, rng.choice([{"type": "octave"}, {"type": "interval"}, {"type": "dur"}, {"type": "vel"}]), rng, beat_s)[0]
                if rng.random() < chaos * 0.2:
                    c = c.clone(t_offset=c.t_offset + rng.random() * beat_s)
                nxt.append(c)
            cur = nxt
        out.extend(cur)
    return out
