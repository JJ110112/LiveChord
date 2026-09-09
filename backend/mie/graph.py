"""Instruments, edges, scenes and the interaction graph (plan §2.3-§2.5, §7 modes)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .harmony import KeyInfo
from .scales import NOTE_NAMES

log = logging.getLogger("mie.graph")

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data", "mie")
SCENE_DIR = os.path.join(DATA_DIR, "scenes")

ALGOS_PHASE1 = ("follow", "echo", "shadow", "silence", "sustain", "phrase")

# plan §7 operating modes -> overrides applied on top of the scene globals
MODES: dict[str, dict] = {
    "OFF":         {"send": False},
    "BYPASS":      {"send": False},
    "SAFE":        {"prob_scale_max": 0.3, "max_hop": 1, "chaos": 0.0,
                    "algos": ("shadow", "echo", "silence", "sustain", "phrase"),
                    "timed_lanes": ("pad", "sustain", "phrase")},
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

    # how a lane's entries are quantised, in beats: 0 = not at all, 1 = beat,
    # 4 = bar (plan §11 Phase 2 harmonic rhythm). Time-driven lanes enter on
    # their own initiative, so they align; lanes answering a human note keep
    # the human's own timing.
    ALIGN_NAMES = {"none": 0.0, "off": 0.0, "half": 0.5, "beat": 1.0, "bar": -1.0, "downbeat": -1.0}
    ALIGN_DEFAULT = {"silence": "bar", "sustain": "beat", "phrase": "none"}

    @property
    def lane(self) -> str:
        return self.params.get("lane") or self.algo

    def wants(self, texture: Optional[str]) -> bool:
        """Does this edge want to speak over the way the player is playing now?

        `when: {"texture": ["sustained", "arpeggio"]}` on an edge, or the
        shorthand `texture: [...]`. An edge that names none takes everything,
        so scenes written before this keep working unchanged.

        A MISSING reading passes: a condition should narrow a scene
        deliberately, not silence it in the moment the classifier had nothing
        to say. A reading that simply is not in the list does NOT pass - that
        is the whole point of the setting. So a name that is not a real texture
        silences the lane for ever, which is exactly the failure `_mute_reason`
        exists to catch; it says so on the row rather than leaving the player
        to wonder.
        """
        want = self.params.get("texture")
        if want is None:
            want = (self.params.get("when") or {}).get("texture")
        if not want or texture is None:
            return True
        if isinstance(want, str):
            want = [want]
        return texture in want

    def align_beats(self, beats_per_bar: int) -> float:
        """Grid this edge quantises to, in beats. -1 means one bar."""
        v = self.params.get("align", self.ALIGN_DEFAULT.get(self.algo, "none"))
        if isinstance(v, (int, float)):
            g = float(v)
        else:
            g = self.ALIGN_NAMES.get(str(v).lower(), 0.0)
        return float(beats_per_bar) if g < 0 else g

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
    presets: dict = field(default_factory=dict)
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
                   beats_per_bar=int(d.get("beats_per_bar", 4)),
                   presets=dict(d.get("presets") or {}), path=path)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "mode": self.mode, "global": self.globals,
                "bpm": self.bpm, "beats_per_bar": self.beats_per_bar,
                "key": {"tonic": NOTE_NAMES[self.key.tonic_pc], "mode": self.key.mode} if self.key else None,
                "edges": [e.to_dict() for e in self.edges],
                "presets": self.presets}


# Every scene global the engine actually reads. A style (or a scene) may only
# set one of these: `"scale": "blues"` was written into a style and did nothing
# at all, because the scale is derived from the key's mode and nothing ever
# looks for that key. A setting that is silently ignored is the exact failure
# this project keeps paying for.
KNOWN_GLOBALS = frozenset({
    "prob_scale", "chaos", "restraint", "restraint_curve", "max_hop",
    "max_gen_notes_per_s", "max_chain_events", "max_dur_s", "sustain_dur_s",
    "avoid_semitone", "tension", "density", "density_complement", "time", "time_steps",
    "master_gain", "master_cc", "master_ch", "ws_grace_s", "freeze_max_s",
})


# What the player had going last time. Small on purpose: the engine has to come
# up the way they left it, not carry a second copy of the scene. A scene file is
# still the only place settings live; this only says WHICH one, and which style
# was laid over it.
UI_STATE_PATH = os.path.join(DATA_DIR, "last_session.json")


def load_ui_state(path: Optional[str] = None) -> dict:
    try:
        with open(path or UI_STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}          # first run, or someone deleted it: not an error


def save_ui_state(patch: dict, path: Optional[str] = None) -> None:
    """Merge `patch` into what is remembered. Never raises: this is a convenience,
    and losing it must not take a take down."""
    path = path or UI_STATE_PATH
    try:
        cur = load_ui_state(path)
        cur.update({k: v for k, v in patch.items() if v is not None})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)          # atomic: a half-written file would be worse
    except OSError:
        pass


def load_styles(path: Optional[str] = None) -> list:
    """The named intervention styles (plan §11 Phase 2, 介入風格預設).

    A style is a bundle of settings that ALREADY EXIST, keyed by ALGORITHM
    rather than by edge id: a scene names its own edges (`phrase_modx`,
    `iridium_to_wavestate`), so a style that referenced them would fit exactly
    one rig. Missing or unreadable is not an error - the panel simply offers no
    styles.
    """
    path = path or os.path.join(DATA_DIR, "styles.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for s in raw.get("styles", []):
        if isinstance(s, dict) and s.get("id"):
            out.append(s)
    return out


def load_scene(path_or_id: str) -> Scene:
    path = path_or_id
    if not os.path.exists(path):
        cands = [f for f in sorted(os.listdir(SCENE_DIR)) if f.startswith(path_or_id) and f.endswith(".json")]
        if not cands:
            raise FileNotFoundError(f"scene {path_or_id!r} not found in {SCENE_DIR}")
        path = os.path.join(SCENE_DIR, cands[0])
    with open(path, encoding="utf-8") as f:
        return Scene.from_json(json.load(f), path)


def save_scene(scene: "Scene", as_id: Optional[str] = None) -> str:
    """Write a scene back to disk, atomically. Returns the path written.

    Everything the player tunes on the panel lives only in memory until this
    runs: an evening of finding the right decay and the right register is
    thrown away by the next restart, which is a poor way to treat work that
    can only be done by ear.

    `as_id` saves a copy under a new id instead of overwriting.
    """
    d = scene.to_dict()
    if as_id:
        d["id"] = as_id
        path = os.path.join(SCENE_DIR, f"{as_id}.json")
    else:
        path = scene.path or os.path.join(SCENE_DIR, f"{scene.id}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)          # never leave a half-written scene behind
    return path


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
                    log.warning("mie: scene file %s is unreadable, skipping", f, exc_info=True)
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

    def candidate_edges(self, origin: str, ch: int, hop: int, now: float,
                        allowed_algos: Optional[Iterable[str]] = None,
                        texture: Optional[str] = None) -> list[Edge]:
        src = 0 if origin == "HUMAN" else ch
        out = []
        allowed = set(allowed_algos) if allowed_algos else None
        for e in self.edges_from(src):
            if not e.enabled or origin not in e.accepts or hop > e.max_hop:
                continue
            if not e.wants(texture):
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

    def timed_edges(self, allowed_algos: Optional[Iterable[str]] = None,
                    texture: Optional[str] = None) -> list[Edge]:
        allowed = set(allowed_algos) if allowed_algos else None
        return [e for e in self.edges if e.enabled and e.algo in ("silence", "sustain", "phrase", "density")
                and (allowed is None or e.algo in allowed) and e.wants(texture)
                and (self.instruments.get(e.dst) is not None and self.instruments[e.dst].enabled)]

    def by_role(self, role: str) -> list[Instrument]:
        return [i for i in self.instruments.values() if i.role == role and i.enabled]

    def find_edge(self, edge_id: str) -> Optional[Edge]:
        for e in self.edges:
            if e.id == edge_id:
                return e
        return None
