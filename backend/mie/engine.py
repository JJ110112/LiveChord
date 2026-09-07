"""The MIE engine: one thread that owns `MusicalState`, runs the pipeline
(plan §3) and hands (note_on, note_off) pairs to the scheduler.

Threads (plan §1):
    rtmidi callbacks -> in_queue -> Engine.loop()  (only mutator of state)
    Scheduler thread  -> emit()  -> MIDI out; posts a "sent" notice back into
                                    in_queue so bookkeeping + feedback (step 10)
                                    happen on the engine thread
    UI thread reads `snapshot()` / `pop_events()` (copies, no shared mutation)

For tests everything is driven synchronously: `post()` + `step(now)`.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from random import Random
from typing import Callable, Optional

from . import algos, mutation
from .constraint import collision_for, constrain, late_bind
from .events import MieEvent, Proposal, next_id
from .graph import ALGOS_PHASE1, MODES, InteractionGraph, Instrument, Scene
from .io_rtmidi import panic_messages
from .probability import p_eff, roll
from .safety import Safety, SelfEchoFilter
from .scheduler import Due, NotePair, Scheduler
from .state import MusicalState

DUP_WINDOW_S = 0.005      # Fantom layered zones: same key on two channels within 5 ms
TICK_S = 0.05


def port_for(ch: int) -> str:
    return "reaper" if ch == 1 else "hst"


class Engine:
    def __init__(self, scene: Scene, instruments: dict[int, Instrument], *,
                 clock: Callable[[], float], send: Callable[[str, object], None],
                 rng: Optional[Random] = None, mode: Optional[str] = None,
                 control_map: Optional[dict] = None):
        import mido
        self.mido = mido
        self.clock = clock
        self.send = send
        self.rng = rng or Random()
        self.scene = scene
        self.instruments = instruments
        self.graph = InteractionGraph(scene, instruments)
        self.st = MusicalState(bpm=scene.bpm, beats_per_bar=scene.beats_per_bar, key=scene.key, now=clock())
        self.safety = Safety(instruments, scene.globals)
        self.echo_filter = SelfEchoFilter(0.008)
        self.sched = Scheduler(clock, self._emit, self._before_on)
        self.in_queue: "queue.Queue[MieEvent | tuple]" = queue.Queue()
        self.mode = mode or scene.mode or "SAFE"
        if self.mode not in MODES:
            self.mode = "SAFE"
        self.lane_state: dict[str, dict] = {}
        self.stats = {"human_notes": 0, "gen_sched": 0, "gen_sent": 0, "dropped": 0, "loops": 0,
                      "panics": 0, "controls": 0, "dups": 0}
        self.edge_fires: dict[str, int] = {}
        self.drop_reasons: dict[str, int] = {}
        self._roll_cache: dict[str, tuple] = {}   # edge id -> (group start t, p or None)
        self._shadow_group: dict[str, tuple] = {}  # edge id -> (gesture start t, src note)
        self.edge_last: dict[str, float] = {}
        self.ui_events: deque[dict] = deque(maxlen=400)
        self._ui_lock = threading.Lock()
        self.last_control: Optional[dict] = None
        self.control_map = control_map or {}
        self.panicked = False
        self.stop = threading.Event()
        self._t0 = clock()
        self._last_tick = clock()
        self.human_note_count = 0

    # ------------------------------------------------------------------ modes
    @property
    def bypass(self) -> bool:
        return self.mode in ("OFF", "BYPASS")

    @property
    def mode_caps(self) -> dict:
        return MODES.get(self.mode, {})

    @property
    def allowed_algos(self) -> tuple:
        return tuple(self.mode_caps.get("algos") or ALGOS_PHASE1)

    @property
    def eff_max_hop(self) -> int:
        return min(int(self.scene.globals.get("max_hop", 2)), int(self.mode_caps.get("max_hop", 3)))

    @property
    def eff_chaos(self) -> float:
        return float(self.mode_caps.get("chaos", self.scene.globals.get("chaos", 0.0)))

    def set_mode(self, mode: str) -> None:
        mode = str(mode).upper()
        if mode not in MODES:
            return
        was = self.mode
        self.mode = mode
        if self.bypass and not was in ("OFF", "BYPASS"):
            self._release_everything(self.clock())
        if not self.bypass:
            self.panicked = False
        self._ui("mode", mode=mode)

    def resume(self) -> None:
        if self.mode in ("OFF", "BYPASS"):
            self.set_mode(self.scene.mode if self.scene.mode not in ("OFF", "BYPASS") else "SAFE")
        self.panicked = False

    def set_global(self, key: str, value) -> None:
        self.scene.globals[key] = value
        self.safety.set_globals(self.scene.globals)
        self._ui("set", path=f"global.{key}", value=value)

    def set_edge(self, edge_id: str, key: str, value) -> bool:
        e = self.graph.find_edge(edge_id)
        if e is None:
            return False
        if hasattr(e, key) and key not in ("params", "last_fire_t"):
            cur = getattr(e, key)
            if isinstance(cur, set):
                value = set(value)
            elif isinstance(cur, bool):
                value = bool(value)
            elif isinstance(cur, int) and not isinstance(cur, bool):
                value = int(value)
            elif isinstance(cur, float):
                value = float(value)
            setattr(e, key, value)
        else:
            e.params[key] = value
        self._ui("set", path=f"edge.{edge_id}.{key}", value=value)
        return True

    def set_instrument(self, ch: int, key: str, value) -> bool:
        inst = self.instruments.get(int(ch))
        if inst is None or not hasattr(inst, key):
            return False
        if key == "enabled" and not value:
            self._release_channel(int(ch), self.clock())
        setattr(inst, key, type(getattr(inst, key))(value) if not isinstance(getattr(inst, key), tuple) else tuple(value))
        self._ui("set", path=f"inst.{ch}.{key}", value=value)
        return True

    def load_scene(self, scene: Scene) -> None:
        now = self.clock()
        self._release_everything(now, fade_s=0.5)
        self.scene = scene
        self.graph = InteractionGraph(scene, self.instruments)
        self.safety.set_globals(scene.globals)
        self.lane_state.clear()
        if scene.key is not None:
            self.st.set_key(scene.key.tonic_pc, scene.key.mode, "scene")
        if self.st.clock_source == "scene":
            self.st.set_tempo(scene.bpm, now, "scene", beats_per_bar=scene.beats_per_bar)
        self._ui("scene", id=scene.id, name=scene.name)

    # ---------------------------------------------------------------- driving
    def post(self, ev) -> None:
        self.in_queue.put(ev)

    def drain(self, now: Optional[float] = None) -> int:
        n = 0
        while True:
            try:
                item = self.in_queue.get_nowait()
            except queue.Empty:
                return n
            n += 1
            t = self.clock() if now is None else now
            if isinstance(item, tuple):
                self._on_sent(item[0], item[1], t)
            else:
                self.handle(item, t)

    def step(self, now: Optional[float] = None) -> None:
        """Synchronous test driver: drain queue, pump scheduler, tick, drain again."""
        now = self.clock() if now is None else now
        self.drain(now)
        self.sched.pump(now)
        self.drain(now)
        if now - self._last_tick >= TICK_S:
            self.tick(now)
        self.sched.pump(now)
        self.drain(now)

    def loop(self) -> None:
        """Engine thread body."""
        while not self.stop.is_set():
            try:
                item = self.in_queue.get(timeout=TICK_S)
            except queue.Empty:
                item = None
            now = self.clock()
            if item is not None:
                if isinstance(item, tuple):
                    self._on_sent(item[0], item[1], now)
                else:
                    self.handle(item, now)
                self.drain(now)
            if now - self._last_tick >= TICK_S:
                self.tick(now)

    def start(self) -> threading.Thread:
        th = threading.Thread(target=self.loop, name="mie-engine", daemon=True)
        th.start()
        return th

    # ------------------------------------------------------------- pipeline
    def handle(self, ev: MieEvent, now: float) -> None:
        if ev.origin == "CONTROL":
            self._handle_control(ev, now)
            return
        if ev.origin == "GENERATIVE":
            self._fire_edges(ev, now)
            return
        # ---- HUMAN ----
        if ev.kind in ("cc", "pc", "clock"):
            # CH16 carries the Fantom's own scene bank/program changes: ignore.
            if ev.kind == "cc" and ev.cc == 64 and ev.ch != 16:
                self.st.set_sustain(ev.ch, (ev.val or 0) >= 64, now)
                self._ui("pedal", ch=ev.ch, val=ev.val)
            return
        if ev.note is None:
            return
        if ev.is_note_on:
            if self.echo_filter.is_echo(ev.ch, ev.note, True, now):
                self.stats["loops"] += 1
                self._ui("loop", ch=ev.ch, note=ev.note)
                return
            h = self.st.held.get(ev.note)
            dup = h is not None and (now - h.t_on) < DUP_WINDOW_S and h.ch != ev.ch
            self.st.note_on_human(ev, now, duplicate=dup)
            if dup:
                self.stats["dups"] += 1
                return
            self.stats["human_notes"] += 1
            ev.ctx = self.st.snapshot(now)
            self._ui("human", ch=ev.ch, note=ev.note, vel=ev.vel, chord=ev.ctx.chord)
            if self.bypass:
                return
            self._human_came_back(now)
            self._fire_edges(ev, now)
        elif ev.is_note_off:
            if self.echo_filter.is_echo(ev.ch, ev.note, False, now):
                self.stats["loops"] += 1
                return
            dur = self.st.note_off_human(ev, now)
            ev.dur_hint = dur
            if self.bypass:
                return
            for e in self.graph.candidate_edges("HUMAN", ev.ch, 0, now, self.allowed_algos):
                if e.algo != "shadow":
                    continue
                # Release straight from the scheduler by the human note this
                # shadow is bound to. Going through `active_gen` alone loses the
                # race when a short note is released before the scheduler's
                # "note sent" notice reaches the engine thread, and the shadow
                # then hangs until its safety cap.
                self.sched.release_by_src(e.dst, e.lane, ev.note, now)
                self._apply_offs(algos.shadow.run(ev, self.st, e, self.rng), e, now)

    def _human_came_back(self, now: float) -> None:
        for e in self.graph.timed_edges(self.allowed_algos):
            ls = self.lane_state.setdefault(e.id, {})
            fn = algos.RELEASE_ALGOS.get(e.algo)
            if fn and ls.get("fired"):
                self._apply_offs(fn(self.st, e, now, ls), e, now)

    def _fire_edges(self, ev: MieEvent, now: float) -> None:
        edges = self.graph.candidate_edges(ev.origin, ev.ch, ev.hop, now, self.allowed_algos)
        if not edges:
            return
        fired = self._roll_grouped(edges, ev, now)
        for e, p in fired:
            fn = algos.EVENT_ALGOS.get(e.algo)
            if fn is None:
                continue
            props = [x for x in fn(ev, self.st, e, self.rng) if x.kind == "on"]
            if not props:
                continue
            props = mutation.apply(props, e.mutations, self.rng, chaos=self.eff_chaos, origin=ev.origin,
                                   beat_s=self.st.beat_s)
            self._record_fire(e, now, p)
            self._schedule_props(props, ev, e, now)

    def _roll_grouped(self, edges: list, ev: MieEvent, now: float) -> list[tuple]:
        """Probability gate with chord grouping.

        Rolling per note turns a five-note chord into "two of the five notes got
        an echo", which sounds arbitrary. Notes struck within `chord_window_ms`
        of each other share one decision per edge, so a chord is answered as a
        chord or not at all.
        """
        if ev.origin != "HUMAN" or not ev.is_note_on:
            return roll(edges, self.scene.globals, self.st.human_energy, self.rng, self.mode_caps)
        fresh, cached = [], []
        for e in edges:
            win = float(e.params.get("chord_window_ms", 45)) / 1000.0
            c = self._roll_cache.get(e.id)
            if c is not None and (now - c[0]) <= win:
                if c[1] is not None:
                    cached.append((e, c[1]))
            else:
                fresh.append(e)
        fired = roll(fresh, self.scene.globals, self.st.human_energy, self.rng, self.mode_caps)
        won = {id(e) for e, _ in fired}
        for e in fresh:
            self._roll_cache[e.id] = (now, next((p for f, p in fired if f is e), None) if id(e) in won else None)
        return fired + cached

    def _record_fire(self, e, now: float, p: float) -> None:
        e.last_fire_t = now
        self.edge_fires[e.id] = self.edge_fires.get(e.id, 0) + 1
        self.edge_last[e.id] = now
        self._ui("edge", edge=e.id, src=e.src, dst=e.dst, algo=e.algo, p=round(p, 3))

    def _schedule_props(self, props: list[Proposal], ev: MieEvent, edge, now: float) -> int:
        hop = ev.hop + 1
        ttl = now + 4.0 * self.st.beat_s
        reason = self.safety.check_lineage(hop=hop, max_hop=self.eff_max_hop, ttl_wall=ev.ttl_wall,
                                           root_id=ev.root_id, now=now, origin=ev.origin, fanout=len(props))
        if reason:
            self._drop(reason, edge, props[0], now, n=len(props))
            return 0
        inst = self.instruments.get(edge.dst)
        caps = self.mode_caps
        if edge.algo == "shadow" and ev.origin == "HUMAN":
            self._regroup_shadow(edge, ev, now)
        n_ok = 0
        for p in props:
            if "dur_scale" in caps:
                p = p.clone(dur=p.dur * float(caps["dur_scale"]))
            if "vel_max" in caps:
                p = p.clone(vel=min(p.vel, int(caps["vel_max"])))
            cp = constrain(p, self.st, edge, inst)
            if cp is None:
                self._drop("constraint", edge, p, now)
                continue
            adm = self.safety.admit(cp, self.st, now, pending_on_ch=self.sched.pending_on(cp.ch),
                                    t_send=now + max(0.0, cp.t_offset))
            if not adm.ok:
                self._drop(adm.reason or "safety", edge, cp, now)
                continue
            if adm.steal is not None:
                self._force_off(adm.steal[0], adm.steal[1], now, "steal")
            dur = self.safety.clamp_dur(cp, inst)
            t_on = now + max(0.0, cp.t_offset)
            pair = NotePair(ch=cp.ch, note=cp.note, vel=cp.vel, t_on=t_on, t_off=t_on + dur, lane=cp.lane,
                            origin="GENERATIVE", root_id=ev.root_id, parent_id=ev.event_id, hop=hop,
                            edge_id=edge.id, constraint=edge.constraint, follow_off=cp.follow_off,
                            src_note=cp.src_note, max_dur=dur, ttl_wall=min(ev.ttl_wall, t_on + 4.0 * self.st.beat_s),
                            collision=collision_for(edge))
            self.sched.schedule_pair(pair)
            self.safety.count_chain(ev.root_id, now)
            self.stats["gen_sched"] += 1
            n_ok += 1
            self._ui("sched", ch=pair.ch, note=pair.note, vel=pair.vel, lane=pair.lane, hop=hop,
                     edge=edge.id, in_ms=round((t_on - now) * 1000), dur_ms=round(dur * 1000))
        return n_ok

    def _regroup_shadow(self, edge, ev: MieEvent, now: float) -> None:
        """Keep one shadow per chord, not one per note of it.

        Shadow decides "is this the top note?" against what is held at that
        instant, so a chord rolled from the bottom up makes every note the top
        in turn and all five reach the instrument. Within one gesture the later
        note wins and the earlier shadow is withdrawn.
        """
        win = float(edge.params.get("chord_window_ms", 45)) / 1000.0
        prev = self._shadow_group.get(edge.id)
        same_gesture = prev is not None and (now - prev[0]) <= win
        if same_gesture and prev[1] != ev.note:
            self.sched.release_by_src(edge.dst, edge.lane, prev[1], now)
            for (ch, n), g in list(self.st.active_gen.items()):
                if ch == edge.dst and g.lane == edge.lane and g.src_note == prev[1]:
                    self._force_off(ch, n, now, "regroup")
        self._shadow_group[edge.id] = ((prev[0] if same_gesture else now), ev.note)

    def _apply_offs(self, props: list[Proposal], edge, now: float) -> None:
        for p in props:
            if p.kind != "off":
                continue
            at = now + max(0.0, p.t_offset)
            n = self.sched.release(p.ch, p.note, at, lane=p.lane, src_note=p.src_note)
            if n == 0 and (p.ch, p.note) in self.st.active_gen:
                # note_on went out but its off is not in the heap any more (e.g. after steal): send now
                self._force_off(p.ch, p.note, at, "release")

    def _drop(self, reason: str, edge, p: Proposal, now: float, n: int = 1) -> None:
        self.stats["dropped"] += n
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + n
        self._ui("drop", reason=reason, edge=getattr(edge, "id", "?"), ch=p.ch, note=p.note, lane=p.lane)

    # ----------------------------------------------------------- scheduler
    def _before_on(self, pair: NotePair, now: float) -> bool:
        """Scheduler thread: late-binding re-snap right before the note_on."""
        if self.bypass or self.stop.is_set():
            return False
        try:
            if pair.ch in self.st.human_chs:
                self._drop_async("human_ch_late", pair)
                return False
            n2 = late_bind(pair.note, pair.constraint, self.st, self.instruments.get(pair.ch), pair.collision)
            if n2 is None:
                self._drop_async("resnap", pair)
                return False
            if n2 != pair.note:
                self._ui("resnap", ch=pair.ch, frm=pair.note, to=n2)
                pair.note = n2
        except Exception:
            return True
        return True

    def _drop_async(self, reason: str, pair: NotePair) -> None:
        self.stats["dropped"] += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1
        self._ui("drop", reason=reason, edge=pair.edge_id, ch=pair.ch, note=pair.note, lane=pair.lane)

    def _emit(self, due: Due, now: float) -> None:
        """Scheduler thread: the only place generated MIDI leaves the process."""
        p = due.pair
        if due.kind == "raw":
            port, msg = due.payload
            self.send(port, msg)
            return
        if p is None:
            return
        if due.kind == "on":
            msg = self.mido.Message("note_on", channel=p.ch - 1, note=p.note, velocity=p.vel)
        else:
            msg = self.mido.Message("note_off", channel=p.ch - 1, note=p.note, velocity=0)
        self.send(port_for(p.ch), msg)
        self.echo_filter.note_sent(p.ch, p.note, due.kind == "on", now)
        self.in_queue.put((due.kind, p))

    def _on_sent(self, kind: str, p: NotePair, now: float) -> None:
        """Engine thread: bookkeeping + feedback (pipeline step 10)."""
        if kind == "on":
            self.stats["gen_sent"] += 1
            self.st.gen_on(p.ch, p.note, now, p.lane, p.root_id, p.hop, p.src_note, p.max_dur)
            self._ui("gen", ch=p.ch, note=p.note, vel=p.vel, lane=p.lane, hop=p.hop, edge=p.edge_id)
            if self.graph.edges_from(p.ch) and not self.bypass:
                fb = MieEvent(event_id=next_id(), kind="note_on", t_wall=now, ch=p.ch, note=p.note, vel=p.vel,
                              origin="GENERATIVE", root_id=p.root_id, parent_id=p.parent_id, source_ch=p.ch,
                              hop=p.hop, ttl_wall=p.ttl_wall, lane=p.lane)
                self._fire_edges(fb, now)
        else:
            self.st.gen_off(p.ch, p.note)

    def _force_off(self, ch: int, note: int, now: float, why: str = "") -> None:
        self.sched.release(ch, note, now)
        if (ch, note) in self.st.active_gen:
            self.send(port_for(ch), self.mido.Message("note_off", channel=ch - 1, note=note, velocity=0))
            self.echo_filter.note_sent(ch, note, False, now)
            self.st.gen_off(ch, note)
            self._ui("off", ch=ch, note=note, why=why)

    def _release_channel(self, ch: int, now: float) -> None:
        for (c, n) in list(self.st.active_gen):
            if c == ch:
                self._force_off(c, n, now, "disable")

    def _release_everything(self, now: float, fade_s: float = 0.0) -> None:
        sounding = self.sched.cancel_all()
        for p in sounding:
            self.st.active_gen.setdefault((p.ch, p.note), None)
        for (c, n) in list(self.st.active_gen):
            self._force_off(c, n, now + fade_s, "release_all")
        self.st.active_gen.clear()
        for ls in self.lane_state.values():
            ls["fired"] = False

    # ---------------------------------------------------------------- tick
    def tick(self, now: float) -> None:
        self._last_tick = now
        self.st.tick(now)
        if not self.bypass:
            for e in self.graph.timed_edges(self.allowed_algos):
                lanes_ok = self.mode_caps.get("silence_lanes")
                if lanes_ok and e.lane not in lanes_ok:
                    continue
                ls = self.lane_state.setdefault(e.id, {})
                fn = algos.TICK_ALGOS.get(e.algo)
                if fn is None:
                    continue
                props = fn(self.st, e, self.rng, now, ls)
                if not props:
                    continue
                p = p_eff(e, self.scene.globals, self.st.human_energy, self.mode_caps)
                if self.rng.random() >= p:
                    self._ui("skip", edge=e.id, p=round(p, 3))
                    continue
                root = MieEvent(event_id=next_id(), kind="tick", t_wall=now, ch=0, origin="HUMAN",
                                root_id=next_id(), source_ch=0, hop=0, lane=e.lane)
                self._record_fire(e, now, p)
                self._schedule_props(props, root, e, now)
        for (ch, note) in self.safety.watchdog(self.st, now):
            self._force_off(ch, note, now, "watchdog")
        self.safety.prune_chains(now)

    # --------------------------------------------------------------- PANIC
    def panic(self, reason: str = "manual") -> None:
        now = self.clock()
        self.stats["panics"] += 1
        self.panicked = True
        self.mode = "BYPASS"
        sounding = self.sched.cancel_all()
        active: dict[tuple[int, int], float] = {k: g.t_on for k, g in self.st.active_gen.items()}
        for p in sounding:
            active.setdefault((p.ch, p.note), p.t_on)
        by_port = {"hst": {}, "reaper": {}}
        for k, t in active.items():
            by_port[port_for(k[0])][k] = t
        for port in ("hst", "reaper"):
            for m in panic_messages(by_port[port]):
                try:
                    self.send(port, m)
                except Exception:
                    pass
        self.st.active_gen.clear()
        for ls in self.lane_state.values():
            ls["fired"] = False
        self._ui("panic", reason=reason, notes=len(active))

    # ------------------------------------------------------------- control
    def _handle_control(self, ev: MieEvent, now: float) -> None:
        self.stats["controls"] += 1
        key = f"{ev.port}:cc:{ev.cc}"
        self.last_control = {"key": key, "ch": ev.ch, "cc": ev.cc, "val": ev.val, "t": now}
        action = self.control_map.get(key)
        self._ui("control", key=key, val=ev.val, action=action)
        if not action:
            return
        self.apply_action(action, ev.val)

    def apply_action(self, action: str, val: int) -> None:
        pressed = val >= 64
        if action == "panic":
            if pressed:
                self.panic("uc4")
        elif action == "engine.toggle":
            if pressed:
                self.resume() if self.bypass else self.set_mode("BYPASS")
        elif action == "engine.on":
            if pressed:
                self.resume()
        elif action == "engine.off":
            if pressed:
                self.set_mode("BYPASS")
        elif action.startswith("mode."):
            if pressed:
                self.set_mode(action.split(".", 1)[1])
        elif action.startswith("global."):
            key = action.split(".", 1)[1]
            if key in ("prob_scale", "chaos", "restraint"):
                self.set_global(key, round(val / 127.0, 3))
            elif key == "bpm":
                self.st.set_tempo(40 + val, self.clock(), "manual")
        elif action.startswith("inst.") and action.endswith(".enabled"):
            ch = int(action.split(".")[1])
            if pressed:
                inst = self.instruments.get(ch)
                if inst:
                    self.set_instrument(ch, "enabled", not inst.enabled)

    # -------------------------------------------------------------- UI
    def _ui(self, typ: str, **kw) -> None:
        kw["type"] = typ
        kw["t"] = round(self.clock() - self._t0, 3)
        with self._ui_lock:
            self.ui_events.append(kw)

    def pop_events(self) -> list[dict]:
        with self._ui_lock:
            out = list(self.ui_events)
            self.ui_events.clear()
        return out

    def snapshot(self) -> dict:
        now = self.clock()
        return {
            "t": round(now - self._t0, 3),
            "mode": self.mode, "bypass": self.bypass, "panicked": self.panicked,
            "scene": {"id": self.scene.id, "name": self.scene.name, "mode": self.scene.mode,
                      "global": self.scene.globals, "bpm": self.scene.bpm},
            "state": self.st.to_dict(now),
            "restraint": round(p_eff(_UNIT_EDGE, {"prob_scale": 1.0, "restraint": self.scene.globals.get("restraint", 1.0),
                                                  "restraint_curve": self.scene.globals.get("restraint_curve", 1.0)},
                                     self.st.human_energy), 3),
            "stats": dict(self.stats), "drops": dict(self.drop_reasons),
            "jitter": self.sched.jitter_summary(), "pending": len(self.sched),
            "edges": [dict(e.to_dict(), fires=self.edge_fires.get(e.id, 0),
                           ago=round(now - self.edge_last[e.id], 2) if e.id in self.edge_last else None)
                      for e in self.graph.edges],
            "instruments": [i.to_dict() for i in self.instruments.values()],
            "last_control": self.last_control,
        }


class _UnitEdge:
    prob = 1.0
    params: dict = {}


_UNIT_EDGE = _UnitEdge()
