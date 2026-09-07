"""Instruments, edges, scenes and the interaction graph (plan §2.3-§2.5, §7 modes)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .harmony import KeyInfo
from .scales import NOTE_NAMES

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data", "mie")
SCENE_DIR = os.path.join(DATA_DIR, "scenes")

ALGOS_PHASE1 = ("follow", "echo", "shadow", "silence", "sustain")

# plan §7 operating modes -> overrides applied on top of the scene globals
MODES: dict[str, dict] = {
    "OFF":         {"send": False},
    "BYPASS":      {"send": False},
    "SAFE":        {"prob_scale_max": 0.3, "max_hop": 1, "chaos": 0.0,
                    "algos": ("shadow", "echo", "silence", "sustain"),
                    "timed_lanes": ("pad", "sustain")},
    "AMBIENT":     {"dur_scale": 2.0, "vel_max": 70, "prefer_lanes": ("pad", "texture")},
    "INTERACTIVE": {"max_hop": 2},
    "GENERATIVE":  {"max_hop": 2},
    "CHAOS":       {"max_hop": 3},
}


@dataclass(slots=True)
class Instrument:
    ch: int
    name: str
    role: str = "synth"
    group: str = "hw"
    enabled: bool = True
    max_voices: int = 4
    vel_scale: float = 1.0
    note_range: tuple[int, int] = (24, 108)
    sustain_ok: bool = False

    @classmethod
    def from_json(cls, ch: int, d: dict) -> "Instrument":
        nr = d.get("note_range") or [24, 108]
        return cls(ch=ch, name=d.get("name", f"CH{ch}"), role=d.get("role", "synth"),
                   group=d.get("group", "hw"), enabled=bool(d.get("enabled", True)),
                   max_voices=int(d.get("max_voices", 4)), vel_scale=float(d.get("vel_scale", 1.0)),
                   note_range=(int(nr[0]), int(nr[1])), sustain_ok=bool(d.get("sustain_ok", False)))

    def to_dict(self) -> dict:
        return {"ch": self.ch, "name": self.name, "role": self.role, "group": self.group,
                "enabled": self.enabled, "max_voices": self.max_voices, "vel_scale": self.vel_scale,
                "note_range": list(self.note_range), "sustain_ok": self.sustain_ok}


def load_instruments(path: Optional[str] = None) -> dict[int, Instrument]:
    path = path or os.path.join(DATA_DIR, "instruments.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): Instrument.from_json(int(k), v) for k, v in raw.items() if not k.startswith("_")}


_EDGE_FIELDS = {"src", "dst", "algo", "prob", "delay_beats", "delay_ms", "transpose", "octave",
                "vel_scale", "vel_offset", "dur_scale", "mutations", "constraint", "max_hop",
                "accepts", "cooldown_ms", "enabled", "id"}


@dataclass(slots=True)
class Edge:
    src: int                      # 0 = HUMAN, 1-15 = channel
    dst: int
    algo: str
    prob: float = 1.0
    delay_beats: float = 0.0
    delay_ms: float = 0.0
    transpose: int = 0
    octave: int = 0
    vel_scale: float = 1.0
    vel_offset: int = 0
    dur_scale: float = 1.0
    mutations: list = field(default_factory=list)
    constraint: str = "scale"     # chord | scale | free
    max_hop: int = 2
    accepts: set = field(default_factory=lambda: {"HUMAN", "GENERATIVE"})
    cooldown_ms: float = 0.0
    enabled: bool = True
    id: str = ""
    params: dict = field(default_factory=dict)   # algo-specific: interval, shadow, after_s, lane, ...
    last_fire_t: float = -1e9

    @classmethod
    def from_json(cls, d: dict, idx: int) -> "Edge":
        kw = {k: v for k, v in d.items() if k in _EDGE_FIELDS}
        params = {k: v for k, v in d.items() if k not in _EDGE_FIELDS and not k.startswith("_")}
        if "accepts" in kw:
            kw["accepts"] = set(kw["accepts"])
        kw.setdefault("id", f"e{idx}")
        return cls(params=params, **kw)

    @property
    def lane(self) -> str:
        return self.params.get("lane") or self.algo

    def to_dict(self) -> dict:
        d = {"id": self.id, "src": self.src, "dst": self.dst, "algo": self.algo, "prob": self.prob,
             "delay_beats": self.delay_beats, "delay_ms": self.delay_ms, "transpose": self.transpose,
             "octave": self.octave, "vel_scale": self.vel_scale, "vel_offset": self.vel_offset,
             "dur_scale": self.dur_scale, "mutations": self.mutations, "constraint": self.constraint,
             "max_hop": self.max_hop, "accepts": sorted(self.accepts), "cooldown_ms": self.cooldown_ms,
             "enabled": self.enabled}
        d.update(self.params)
        return d


@dataclass
class Scene:
    id: str
    name: str
    mode: str = "SAFE"
    globals: dict = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    key: Optional[KeyInfo] = None
    bpm: float = 92.0
    beats_per_bar: int = 4
    path: Optional[str] = None

    GLOBAL_DEFAULTS = {"prob_scale": 0.6, "chaos": 0.0, "restraint": 1.0, "restraint_curve": 1.0,
                       "max_hop": 2, "max_gen_notes_per_s": 12, "max_chain_events": 24,
                       "max_dur_s": 8.0, "sustain_dur_s": 30.0}

    @classmethod
    def from_json(cls, d: dict, path: Optional[str] = None) -> "Scene":
        g = dict(cls.GLOBAL_DEFAULTS)
        g.update(d.get("global", {}))
        key = None
        if d.get("key"):
            tonic = d["key"].get("tonic", "C")
            tonic_pc = NOTE_NAMES.index(tonic) if tonic in NOTE_NAMES else 0
            key = KeyInfo(tonic_pc, d["key"].get("mode", "major"), 1.0, "scene")
        edges = [Edge.from_json(e, i) for i, e in enumerate(d.get("edges", []))]
        return cls(id=str(d.get("id", "00")), name=d.get("name", "scene"), mode=d.get("mode", "SAFE"),
                   globals=g, edges=edges, key=key, bpm=float(d.get("bpm", 92)),
                   beats_per_bar=int(d.get("beats_per_bar", 4)), path=path)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "mode": self.mode, "global": self.globals,
                "bpm": self.bpm, "beats_per_bar": self.beats_per_bar,
                "key": {"tonic": NOTE_NAMES[self.key.tonic_pc], "mode": self.key.mode} if self.key else None,
                "edges": [e.to_dict() for e in self.edges]}


def load_scene(path_or_id: str) -> Scene:
    path = path_or_id
    if not os.path.exists(path):
        cands = [f for f in sorted(os.listdir(SCENE_DIR)) if f.startswith(path_or_id) and f.endswith(".json")]
        if not cands:
            raise FileNotFoundError(f"scene {path_or_id!r} not found in {SCENE_DIR}")
        path = os.path.join(SCENE_DIR, cands[0])
    with open(path, encoding="utf-8") as f:
        return Scene.from_json(json.load(f), path)


def list_scenes() -> list[dict]:
    out = []
    if os.path.isdir(SCENE_DIR):
        for f in sorted(os.listdir(SCENE_DIR)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(SCENE_DIR, f), encoding="utf-8") as fh:
                        d = json.load(fh)
                    out.append({"id": str(d.get("id", f[:-5])), "name": d.get("name", f), "file": f})
                except Exception:
                    continue
    return out


class InteractionGraph:
    """Edges indexed by source; the probability matrix is just a view of this."""

    def __init__(self, scene: Scene, instruments: dict[int, Instrument]):
        self.scene = scene
        self.instruments = instruments
        self._by_src: dict[int, list[Edge]] = {}
        for e in scene.edges:
            self._by_src.setdefault(e.src, []).append(e)

    @property
    def edges(self) -> list[Edge]:
        return self.scene.edges

    def edges_from(self, src: int) -> list[Edge]:
        return self._by_src.get(src, [])

    def candidate_edges(self, origin: str, ch: int, hop: int, now: float, allowed_algos: Optional[Iterable[str]] = None) -> list[Edge]:
        src = 0 if origin == "HUMAN" else ch
        out = []
        allowed = set(allowed_algos) if allowed_algos else None
        for e in self.edges_from(src):
            if not e.enabled or origin not in e.accepts or hop > e.max_hop:
                continue
            if allowed is not None and e.algo not in allowed:
                continue
            inst = self.instruments.get(e.dst)
            if inst is None or not inst.enabled:
                continue
            if e.cooldown_ms and (now - e.last_fire_t) * 1000.0 < e.cooldown_ms:
                continue
            out.append(e)
        return out

    def timed_edges(self, allowed_algos: Optional[Iterable[str]] = None) -> list[Edge]:
        allowed = set(allowed_algos) if allowed_algos else None
        return [e for e in self.edges if e.enabled and e.algo in ("silence", "sustain", "density")
                and (allowed is None or e.algo in allowed)
                and (self.instruments.get(e.dst) is not None and self.instruments[e.dst].enabled)]

    def by_role(self, role: str) -> list[Instrument]:
        return [i for i in self.instruments.values() if i.role == role and i.enabled]

    def find_edge(self, edge_id: str) -> Optional[Edge]:
        for e in self.edges:
            if e.id == edge_id:
                return e
        return None
