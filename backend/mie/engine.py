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

import logging
import math
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from random import Random
from typing import Callable, Optional

from . import algos, mutation
from .algos import quantize_time, t_secs
from .constraint import (collision_for, constrain, diatonic_map, edge_range, late_bind,
                         voice_lead_for)
from .harmony import states_a_third
from .events import MieEvent, Proposal, next_id
from .graph import _EDGE_FIELDS, ALGOS_PHASE1, MODES, InteractionGraph, Instrument, Scene
from .io_rtmidi import panic_messages
from .probability import p_eff, roll
from .safety import Safety, SelfEchoFilter
from .scheduler import Due, NotePair, Scheduler
from .advisor import Advisor, advise
from .texture import TEXTURES
from .state import MusicalState

log = logging.getLogger("mie.engine")

DUP_WINDOW_S = 0.005      # Fantom layered zones: same key on two channels within 5 ms
TICK_S = 0.05


@dataclass(slots=True)
class _Command:
    """A parameter change handed to the engine thread (see `Engine.submit`)."""
    fn: Callable
    args: tuple = ()
    kw: dict = field(default_factory=dict)


# ------------------------------------------------------------------ 自走音量
# Each edge already HAS its own volume - `vel_scale`, the 力度× slider. What it
# did not have is a life of its own: it sat wherever it was put until a hand
# moved it. Bad Mood's knobs drift on their own, and a pad whose level never
# moves is the difference between a held chord and a section breathing.
#
# One slow oscillator per edge, off unless a scene asks:
#
#     "vel_drift": {"depth": 0.35, "beats": 24, "shape": "sine"}
#
# `depth` is how far either side of the level you set - 0.35 means the lane
# ranges from 0.65x to 1.35x of its own 力度×, so the setting stays the centre
# of what happens rather than being overruled. `beats` is one full cycle, read
# through the TIME knob like every other wait in the scene. It never silences a
# lane and never doubles it: the multiplier is clamped, because a drift that
# can reach zero is indistinguishable from a fault.
DRIFT_SHAPES = ("sine", "triangle", "ramp", "breathe")


def drift_at(now: float, depth: float, period_s: float, shape: str, phase: float = 0.0) -> float:
    """A multiplier around 1.0. Deterministic in `now`, so two notes sent in the
    same instant get the same level however many lanes are running."""
    if depth <= 0 or period_s <= 0:
        return 1.0
    x = ((now / period_s) + phase) % 1.0
    if shape == "ramp":                       # saw: swell, then drop back
        v = 2.0 * x - 1.0
    elif shape == "triangle":
        v = 4.0 * abs(x - 0.5) - 1.0
    elif shape == "breathe":                  # longer in, shorter out, like a breath
        v = math.sin(math.pi * (x ** 0.7)) * 2.0 - 1.0
    else:                                     # sine
        v = math.sin(2.0 * math.pi * x)
    return max(0.15, min(2.0, 1.0 + depth * v))


def swell_at(now: float, depth: float, period_s: float, shape: str, phase: float = 0.0,
             top: float = 1.0) -> int:
    """The breath, as a MIDI controller value 0-127.

    A pad that is loud the instant it arrives and stays exactly there until it
    stops is the "呆板" in 「單純的長音持續按著」: velocity decides how a note
    STARTS and nothing after that moves. This is what moves after that.

    `top` is the LOUDEST it ever gets while the lane is sounding, and it is the
    answer to a pad whose peak is too strong. Velocity is not: a pad patch is
    normally built to sound the same however hard the key is struck, so dragging
    強度 from 0.9 down to 0.05 - thirty times, on the 19:15 take - changes the
    number in the note and not much in the room. The controller is what that
    patch actually listens to.

    While the lane is quiet the channel is always handed back at 127, whatever
    `top` says. That is not a stylistic choice - it is the safety property that
    makes any of this usable. CC11 and CC1 are channel-wide and sticky: a value
    left at 40 quietens the next note anybody sends on that channel, including
    the player's own passthrough. Only a resting value of 127 can be given back
    with one message, and everything that stops this - the lane going quiet,
    PANIC, the engine exiting - writes exactly that.
    """
    hi = 127.0 * max(0.0, min(1.0, top))
    if depth <= 0 or period_s <= 0:
        return int(round(hi))
    x = ((now / period_s) + phase) % 1.0
    if shape == "ramp":                       # saw: swell, then drop back
        v = x
    elif shape == "triangle":
        v = 1.0 - 2.0 * abs(x - 0.5)
    elif shape == "breathe":                  # longer in, shorter out, like a breath
        v = math.sin(math.pi * (x ** 0.7))
    else:                                     # sine
        v = 0.5 + 0.5 * math.sin(2.0 * math.pi * x)
    lo = hi * (1.0 - max(0.0, min(1.0, depth)))
    return int(round(lo + (hi - lo) * max(0.0, min(1.0, v))))


def port_for(ch: int) -> str:
    return "reaper" if ch == 1 else "hst"


class Engine:
    def __init__(self, scene: Scene, instruments: dict[int, Instrument], *,
                 clock: Callable[[], float], send: Callable[[str, object], None],
                 rng: Optional[Random] = None, mode: Optional[str] = None,
                 control_map: Optional[dict] = None,
                 event_sink: Optional[Callable[[dict], None]] = None):
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
        self._cc_seen: dict[tuple[int, int], tuple[float, int]] = {}
        self._sync_knobs()
        self._phrase_shift: dict[tuple, object] = {}
        self.preset_slot = "LIVE"
        from .graph import scene_stamp as _stamp
        # When the file was last written. A Save compares against this, so an
        # edit made underneath is not silently overwritten.
        self.scene_stamp = _stamp(scene.path)
        from .graph import load_styles
        self.styles = load_styles()
        self.style: Optional[str] = None
        # It only ever LOOKS. Nothing here changes a setting; the panel offers
        # the change and the player presses it.
        self.advisor = Advisor()
        self.advice: list = []
        # Advice the player has answered by deciding. `tension_gap` fired on
        # four takes running after 「時間 張力 留給使用者自己決定」 - and a light
        # that stays on after you have made the decision it is asking for is
        # not advice, it is a fault indicator for a fault that does not exist.
        self.muted_advice: set = set()
        self.replaying = False
        self._advice_t = 0.0
        self._style_before: Optional[dict] = None
        # Settings the player has moved BY HAND. A style may not overwrite one,
        # and neither may the return to the pre-style base: a knob stays where
        # you left it until you turn it, which is how every pedal on the floor
        # behaves. On the 14:39 take they set master_gain to 0.35 and then
        # chose a style; restoring the base put the whole globals dict back and
        # their volume with it.
        self._touched: set = set()
        self.preset_dirty = False
        self._live_backup: Optional[dict] = None
        self._undo: list = []
        self._bulk = False
        self._frozen = False
        self._frozen_lane: Optional[str] = None
        self.echo_filter = SelfEchoFilter(0.008)
        self.sched = Scheduler(clock, self._emit, self._before_on)
        self.in_queue: "queue.Queue[MieEvent | tuple]" = queue.Queue()
        self.mode = mode or scene.mode or "SAFE"
        if self.mode not in MODES:
            self.mode = "SAFE"
        self.lane_state: dict[str, dict] = {}
        # What we have written to each channel's breath controller, so we only
        # send on a change, and so we know which channels have to be given back.
        self._swell_sent: dict[int, tuple[int, int]] = {}    # ch -> (cc, value)
        self._swell_t = 0.0
        self.stats = {"human_notes": 0, "gen_sched": 0, "gen_sent": 0, "dropped": 0, "muted": 0, "loops": 0,
                      "panics": 0, "controls": 0, "dups": 0}
        self.edge_fires: dict[str, int] = {}
        self.edge_drops: dict[str, int] = {}   # so a silenced lane can say so
        self.drop_reasons: dict[str, int] = {}
        self._roll_cache: dict[str, tuple] = {}   # edge id -> (group start t, p or None)
        self._shadow_group: dict[str, tuple] = {}  # edge id -> (gesture start t, src note)
        # (ch, lane) -> (note before last, last note, when it was sent): what a
        # line has just done, so the next note can lead from it (plan §11 Ph2)
        self._lane_hist: dict[tuple[int, str], tuple[Optional[int], int, float]] = {}
        self.edge_last: dict[str, float] = {}
        self.ui_events: deque[dict] = deque(maxlen=400)
        self._ui_lock = threading.Lock()
        self.last_control: Optional[dict] = None
        self.control_map = control_map or {}
        # optional session log; called on the engine and scheduler threads, so
        # it must only hand the event off, never touch the disk itself
        self.event_sink = event_sink
        self.panicked = False
        self.stop = threading.Event()
        self._t0 = clock()
        self._last_tick = clock()
        self.human_note_count = 0
        self._err_counts: dict[str, int] = {}

    def _log_error(self, where: str, exc: BaseException) -> None:
        """Report a swallowed exception without flooding a hot path.

        The stack trace goes to the log the first time and every hundredth time
        after that; the UI event stream always gets a line. Without this a fault
        inside the scheduler callback was invisible unless a browser happened to
        be attached.
        """
        n = self._err_counts.get(where, 0) + 1
        self._err_counts[where] = n
        if n == 1 or n % 100 == 0:
            log.exception("mie: %s failed (%d time(s))", where, n, exc_info=exc)
        self._ui("error", where=where, err=str(exc), n=n)

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
        self._rearm_silence(self.clock())

    def mute_advice(self, advice_id: str, on: bool = True) -> None:
        """Stop being told a thing you have already decided.

        By ID, not by text - the text carries live numbers and would come back
        as a new message every reading. `overlap_<lane>` is per lane, which is
        right: silencing the pad's overlap says nothing about the texture's.
        """
        if on:
            self.muted_advice.add(advice_id)
            self.advice = [a for a in self.advice if a["id"] != advice_id]
        else:
            self.muted_advice.discard(advice_id)
        self._ui("advice_muted", id=advice_id, on=on, n=len(self.muted_advice))

    def _rearm_silence(self, now: float) -> None:
        """After a PANIC, the pad has to earn its way back in.

        Every other lane needs the player to play a note before it can speak.
        The silence lanes need the opposite - they enter BECAUSE nobody is
        playing - and pressing PANIC and then RESUME is a stretch of exactly
        that. So the condition is already satisfied at the moment of RESUME and
        the lane fires on the next tick, playing the very thing that was just
        silenced.

        Measured on the 17:03 take: the pad entered at 132.3 s (C3+E3), the
        player hit PANIC at 147.1 s with four notes sounding, resumed at 149.0,
        and the SAME C3+E3 came back at 149.2 - two tenths of a second later.
        They switched the lane off, and hit PANIC again thirteen seconds after
        that. A stop button that undoes itself is not a stop button.

        So the quiet clock starts again from the resume: the lane waits its own
        `after_s` before it may enter, which is the wait the player would have
        given it anyway had they simply stopped playing.
        """
        for e in self.graph.edges:
            if e.algo != "silence":
                continue
            ls = self.lane_state.setdefault(e.id, {})
            ls["fired"] = False
            ls["retry_t"] = max(float(ls.get("retry_t", 0.0)),
                                now + t_secs(e, self.st, "after_s", 4.0))

    def _phrase_target(self, pair):
        """The chord a captured phrase should be re-read over, or None.

        Frozen ONCE per pass, at whichever note reaches the send path first,
        and reused for the rest: a chord change in the middle of a repeat must
        not leave the first half in one key and the second in another. That is
        the one thing this feature exists to protect.
        """
        if pair.capture_root is None or pair.pass_id is None:
            return None
        if pair.pass_id in self._phrase_shift:
            return self._phrase_shift[pair.pass_id]
        # The LAST SOLID chord, not whatever is down at this instant. Reading
        # the instant cut both ways: on the 17:13 take a mid-strike D5 nearly
        # re-spelled a minor phrase as major, and once that was guarded the
        # 21:00 take swung the other way - 17 of 36 phrases came back over a
        # genuinely different chord and NONE of them transposed, because at the
        # moment the pass landed the reading happened to be a two-note fragment
        # (Am -> Am(3), Dm -> E(3)). `chord_solid` is the last reading that had
        # a third to state, which answers "what harmony are we in" instead of
        # "what keys are down right now".
        chord = self.st.chord_solid
        target = None
        if chord is not None and (chord.root_pc != pair.capture_root
                                  or chord.quality != pair.capture_quality):
            target = (chord.root_pc, chord.quality)
        if len(self._phrase_shift) > 64:
            self._phrase_shift.clear()      # bounded: passes are seconds long
        self._phrase_shift[pair.pass_id] = target
        if target is not None:
            # Say it out loud. On the 21:30 take the feature was live and never
            # moved a note - every phrase came back under the chord it was
            # captured under - and the log could not tell "did not need to fire"
            # from "broken".
            self._ui("phrase_shift", edge=pair.edge_id, frm=pair.capture_root,
                     frm_q=pair.capture_quality, to=target[0], to_q=target[1])
        return target

    # ------------------------------------------------------------- freeze
    # Bad Mood freezes the current sound and repeats it forever while you play
    # over the top (Soup becomes a pad, Flip a repeating chord). Here the
    # equivalent is: hold whatever the engine has sounding RIGHT NOW, so the
    # player can build on a bed the engine made rather than one they had to
    # play themselves and then abandon.
    #
    # It is a deliberate override of the note length, so it is also a deliberate
    # override of the thing that stops notes hanging - which makes it exactly
    # the feature that could hand this project the stuck note it has spent
    # weeks avoiding. Three rules follow from that:
    #   * a frozen note is still known to the watchdog, with a longer leash,
    #     never an exemption from it;
    #   * the leash is finite (`freeze_max_s`), so a freeze someone walks away
    #     from ends by itself;
    #   * PANIC outranks it, like everything else.
    FREEZE_MAX_S = 120.0

    def freeze(self, on: bool = True, lane: Optional[str] = None) -> int:
        """Hold what is sounding (or let it go). Returns how many notes moved."""
        now = self.clock()
        if on:
            if self._frozen:
                return 0                # already holding: pressing again is not an event
            cap = float(self.scene.globals.get("freeze_max_s", self.FREEZE_MAX_S))
            n = 0
            for (ch, note), g in list(self.st.active_gen.items()):
                if lane and g.lane != lane:
                    continue
                self.sched.hold(ch, note, now + cap)
                g.max_dur = max(g.max_dur, cap + (now - g.t_on))   # the watchdog still owns it
                n += 1
            pending = self.sched.drop_pending() if n else 0
            if n == 0:
                # Nothing was sounding, so there is nothing to hold. Latching
                # anyway made the state flip straight back on the next tick
                # ("frozen but silent" is not a state), and every press logged
                # another freeze - the 2026-09-08 panel filled with hundreds of
                # identical `on:true notes:0` lines and the page stopped
                # responding. A press with nothing to hold is simply nothing.
                self._ui("freeze", on=True, notes=0, empty=True)
                return 0
            self._frozen = True
            self._frozen_lane = lane
            self._ui("freeze", on=True, notes=n, lane=lane or "*", cap=cap, cancelled=pending)
            return n
        if not self._frozen:
            return 0                    # not holding: releasing is not an event
        n = 0
        for (ch, note), g in list(self.st.active_gen.items()):
            if lane and g.lane != lane:
                continue
            self._force_off(ch, note, now, "unfreeze")
            n += 1
        self._frozen = False
        self._frozen_lane = None
        self._ui("freeze", on=False, notes=n)
        return n

    # -------------------------------------------------------------- undo
    # The 17:21 take is the argument for this. One drag put a lane's register
    # ceiling onto its floor, the lane played one pitch for four minutes, and
    # nothing on screen said so. Constraining the input (a register is a pair)
    # stops that particular accident; these two stop the NEXT one, whatever it
    # turns out to be. A knob you can put back is a knob you will explore.
    UNDO_DEPTH = 40

    def _remember(self, path: str, old) -> None:
        # A preset or a revert writes a hundred settings at once. Recording each
        # one would bury the player's last real move under the bulk, and undo
        # would appear not to work. Those operations are undone by switching
        # back, not by stepping.
        if self._bulk:
            return
        self._undo.append((path, old))
        if len(self._undo) > self.UNDO_DEPTH:
            self._undo.pop(0)

    def undo(self) -> bool:
        """Put the last parameter change back. Returns False if there is none."""
        if not self._undo:
            self._ui("undo", ok=False)
            return False
        path, old = self._undo.pop()
        parts = path.split(".")
        guard, self._bulk = self._undo, True    # an undo is not itself undoable
        self._undo = []
        try:
            if parts[0] == "global":
                self.set_global(parts[1], old)
            elif parts[0] == "edge":
                self.set_edge(parts[1], parts[2], old)
            elif parts[0] == "inst":
                self.set_instrument(int(parts[1]), parts[2], old)
        finally:
            self._undo, self._bulk = guard, False
        self._ui("undo", ok=True, path=path, value=old)
        return True

    def revert(self, edge_id: Optional[str] = None) -> bool:
        """Put settings back to the scene FILE - the last thing you chose to keep.

        The file is the honest definition of "default" here: not what the code
        ships with, but the last state the player deliberately saved. One edge
        or, with no argument, everything.
        """
        from .graph import load_scene
        if not self.scene.path:
            return False
        try:
            disk = load_scene(self.scene.path)
        except Exception as e:
            self._log_error("revert", e)
            return False
        # going back to the file gives every knob back: it is the one gesture
        # that means "forget what I have been doing"
        if edge_id is None:
            self._touched.clear()
        else:
            self._touched = {p for p in self._touched
                             if not p.startswith(f"edge.{edge_id}.")}
        data = {"globals": dict(disk.globals),
                "edges": {e.id: e.to_dict() for e in disk.edges}}
        if edge_id:
            d = data["edges"].get(edge_id)
            if d is None:
                return False
            data = {"globals": {}, "edges": {edge_id: d}}
        else:
            # Revert is authoritative, not a merge: a setting the file does not
            # have must go, or a stray global would quietly survive being put
            # back and "revert" would not mean what it says.
            for k in [k for k in self.scene.globals if k not in data["globals"]]:
                self.scene.globals.pop(k, None)
        self.preset_apply(data)
        self._undo = []
        self._ui("revert", edge=edge_id or "*")
        return True

    # ------------------------------------------------------------- presets
    # Borrowed from the Bad Mood pedal, which puts two stored settings and a
    # LIVE position on one three-way toggle. The value is not the storage - it
    # is that the middle position holds what you were just doing, so you can
    # flip A / LIVE / B and hear three versions of the same moment. Comparing
    # two settings otherwise means editing, playing, editing back, and playing
    # again, by which time the ear has forgotten the first one.
    PRESET_SLOTS = ("A", "B")

    def preset_capture(self) -> dict:
        """Everything a preset holds: the scene globals and each edge's settings.

        The graph itself (which edges exist, and what they connect) is NOT part
        of a preset. A preset is a way of playing the same rig, so applying one
        must never rebuild the graph - see `preset_apply`.
        """
        return {"globals": dict(self.scene.globals),
                "edges": {e.id: e.to_dict() for e in self.graph.edges}}

    def preset_apply(self, data: dict) -> int:
        """Apply a captured preset IN PLACE. Returns how many edges it touched.

        In place, because the point is to switch while playing. `load_scene`
        releases every sounding note and clears the lane state, which would cut
        the music off at every flip; the same settings applied through the
        ordinary setters leave the lanes, the phrase buffers and the ringing
        notes exactly where they are.
        """
        self._bulk = True
        try:
            return self._apply_settings(data)
        finally:
            self._bulk = False

    def _apply_settings(self, data: dict, skip: Optional[set] = None) -> int:
        """`skip` holds paths the player owns; a style's comings and goings
        must not move them. A preset passes nothing and recalls everything,
        which is what a stored preset means."""
        skip = skip or set()
        for k, v in (data.get("globals") or {}).items():
            if f"global.{k}" not in skip:
                self.set_global(k, v)
        n = 0
        for eid, d in (data.get("edges") or {}).items():
            e = self.graph.find_edge(eid)
            if e is None:
                continue                # the scene has changed under the preset
            for k, v in d.items():
                if k in ("id", "src", "dst", "algo"):
                    continue            # structure, not setting
                if f"edge.{eid}.{k}" not in skip:
                    self.set_edge(eid, k, v)
            n += 1
        return n

    # ----------------------------------------------------------- 回放送音
    # The piano roll draws a take; this plays one back through the instruments.
    # It is a REVIEW tool, so it deliberately does the least it can:
    #
    #   * the notes are scheduled through the ordinary Scheduler, so the timing
    #     is the engine's own (p95 under 2 ms) rather than whatever a browser
    #     animation frame and a WebSocket could manage;
    #   * they are NEVER fed back into the edge graph. A replay must not make
    #     the engine answer the replay - that is a feedback loop with a nice
    #     name;
    #   * they never go to a channel the human is playing on. That rule exists
    #     so the engine cannot fight the player's own hands, and a replay is
    #     not a reason to break it;
    #   * PANIC and BYPASS stop it like anything else, because it goes out
    #     through the same scheduler and the same `_emit`.
    REPLAY_LANE = "replay"
    REPLAY_MAX_NOTES = 4000

    def play_take(self, notes: list, speed: float = 1.0, human: bool = False) -> int:
        """Schedule recorded notes for playback. `notes` are dicts from the panel.

        Each is {t, ch, note, vel, dur} with `t` already relative to the start
        of playback and BEFORE the speed change - the caller sends the window it
        wants heard, and this stretches it.

        `human` allows the player's OWN part back onto their own keyboard's
        channel. Off unless asked for, because that channel is otherwise
        forbidden to the engine; asked for, it is the only way to hear whether
        an answer sat well against what it was answering.
        """
        self.stop_take()
        if self.bypass or self.panicked:
            self._ui("replay", action="refused", why="bypass" if self.bypass else "panicked")
            return 0
        speed = max(0.05, min(4.0, float(speed or 1.0)))
        now = self.clock()
        human_chs = self.st.human_chs
        n = skipped = 0
        for d in notes[:self.REPLAY_MAX_NOTES]:
            try:
                ch = int(d["ch"]); note = int(d["note"])
                t0 = float(d.get("t", 0.0)) / speed
                dur = max(0.03, float(d.get("dur", 0.25)) / speed)
                vel = max(1, min(127, int(d.get("vel", 64))))
            except (KeyError, TypeError, ValueError):
                continue
            if ch in human_chs and not human:
                skipped += 1
                continue
            inst = self.instruments.get(ch)
            # a human channel has no Instrument entry of its own; it is the
            # player's keyboard, and it is being asked for explicitly
            if (inst is None or not inst.enabled) and ch not in human_chs:
                skipped += 1
                continue
            t_on = now + t0
            pair = NotePair(ch=ch, note=note, vel=vel, t_on=t_on, t_off=t_on + dur,
                            lane=self.REPLAY_LANE, origin="GENERATIVE", root_id=0,
                            hop=1, edge_id="replay", constraint="free",
                            max_dur=dur, collision="none",
                            ttl_wall=t_on + dur + 2.0)
            self.sched.schedule_pair(pair)
            n += 1
        self.replaying = n > 0
        self._ui("replay", action="start", notes=n, skipped=skipped,
                 speed=round(speed, 3), human=bool(human))
        return n

    def stop_take(self) -> int:
        """Silence a playback in progress. Safe to call when nothing is playing."""
        now = self.clock()
        n = 0
        for ch in sorted({c for (c, _n) in list(self.st.active_gen)} |
                         set(self.instruments) | self.st.human_chs):
            n += self.sched.release_lane(ch, self.REPLAY_LANE, now)
        for (c, note), g in list(self.st.active_gen.items()):
            if g.lane == self.REPLAY_LANE:
                self._force_off(c, note, now, "replay_stop")
        if self.replaying:
            self._ui("replay", action="stop", released=n)
        self.replaying = False
        return n

    # ------------------------------------------------------- 介入風格預設
    # A style is a NAMED BUNDLE OF SETTINGS THAT ALREADY EXIST - tension,
    # density, the time knob, each algorithm's constraint and weight. It adds
    # no new engine behaviour, which is the point: everything it does is
    # something the player can already reach and can already see move.
    #
    # Keyed by ALGORITHM, never by edge id. A scene names its own edges
    # (`phrase_modx`, `iridium_to_wavestate`), so a style written against those
    # would fit exactly one rig and silently do nothing on the next one.
    def apply_style(self, style_id: str) -> bool:
        style = next((s for s in self.styles if s.get("id") == style_id), None)
        if style is None:
            return False
        # Stash what was there BEFORE the first style is applied, so 取消風格
        # puts back the player's own settings rather than the previous style's.
        # Switching between styles keeps that same original.
        if self._style_before is None:
            self._style_before = self.preset_capture()
        else:
            # Going from one style straight to another must land on THAT style,
            # not on it stacked over the last one: worship sets `hold_beats` and
            # sparse does not mention it, so switching left the pad holding
            # twelve beats under a style whose whole point is less of
            # everything. Put the player's own settings back first, then lay the
            # new style over them - the same ground every time.
            self._restore_style_base()
        self._bulk = True
        kept = 0
        try:
            for k, v in (style.get("globals") or {}).items():
                if f"global.{k}" in self._touched:
                    kept += 1
                    continue
                self.set_global(k, v)
            n = 0
            for e in list(self.graph.edges):
                d = (style.get("algos") or {}).get(e.algo)
                if not d:
                    continue
                for k, v in d.items():
                    if k in ("id", "src", "dst", "algo"):
                        continue
                    if f"edge.{e.id}.{k}" in self._touched:
                        kept += 1
                        continue
                    self.set_edge(e.id, k, v)
                n += 1
        finally:
            self._bulk = False
        self.style = style_id
        self._ui("style", action="apply", id=style_id, edges=n, kept=kept)
        return True

    def release_touched(self, path: Optional[str] = None) -> int:
        """Hand a hand-held setting back, so styles may move it again.

        Without this the overrides only ever accumulate and a style slowly
        stops meaning anything, with nothing on screen to say why.
        """
        n = len(self._touched)
        if path:
            self._touched.discard(path)
            n = 1 if n != len(self._touched) else 0
        else:
            self._touched.clear()
        self._ui("touched", action="release", path=path or "*", n=n)
        return n

    def _restore_style_base(self) -> None:
        """Put the settings back to what they were before any style was applied."""
        self._bulk = True
        try:
            # Overwriting is not enough: a style may INTRODUCE a global the
            # scene never carried (`tension` and `density` are the usual ones),
            # and `_apply_settings` can only write keys it has - so those would
            # be left behind and "取消風格" would quietly not undo itself.
            before = (self._style_before or {}).get("globals") or {}
            for k in [k for k in self.scene.globals
                      if k not in before and f"global.{k}" not in self._touched]:
                del self.scene.globals[k]
            self._apply_settings(self._style_before or {}, skip=self._touched)
            # the derived knobs read straight off the dict, so re-sync them
            # after a deletion as well as after a write
            self.st.density_knob = (None if self.scene.globals.get("density") is None
                                    else float(self.scene.globals["density"]))
            self.st.density_complement = float(self.scene.globals.get("density_complement") or 0.0)
            self.st.time_knob = self._time_knob()
            self.safety.set_globals(self.scene.globals)
        finally:
            self._bulk = False

    def clear_style(self) -> bool:
        """Back to what was there before any style was applied."""
        if self._style_before is None:
            self.style = None
            return False
        self._restore_style_base()
        self._style_before = None
        self.style = None
        self._ui("style", action="clear")
        return True

    def preset_save(self, slot: str) -> bool:
        slot = str(slot).upper()
        if slot not in self.PRESET_SLOTS:
            return False
        self.scene.presets[slot] = self.preset_capture()
        self._ui("preset", action="save", slot=slot)
        return True

    def preset_select(self, slot: str) -> bool:
        """Move the three-way switch. LIVE restores what was there before.

        Leaving LIVE stashes it first, so an evening of tweaking is not lost by
        glancing at a preset - which is exactly the accident the middle position
        exists to prevent.
        """
        slot = str(slot).upper()
        if slot == self.preset_slot:
            return True
        if slot == "LIVE":
            if self._live_backup is not None:
                self.preset_apply(self._live_backup)
                self._live_backup = None
            self.preset_slot, self.preset_dirty = "LIVE", False
            self._ui("preset", action="select", slot="LIVE")
            return True
        if slot not in self.PRESET_SLOTS:
            return False
        data = self.scene.presets.get(slot)
        if not data:
            self._ui("preset", action="empty", slot=slot)
            return False
        if self.preset_slot == "LIVE":
            self._live_backup = self.preset_capture()
        self.preset_apply(data)
        self.preset_slot, self.preset_dirty = slot, False
        self._ui("preset", action="select", slot=slot)
        return True

    def scene_snapshot(self):
        """The scene as it stands, safe to serialise off the engine thread.

        The live `Edge` objects are mutated by `set_edge` while the player turns
        knobs, so the writer gets rebuilt copies rather than the originals.
        """
        from .graph import Edge, Scene
        edges = [Edge.from_json(e.to_dict(), i) for i, e in enumerate(self.graph.edges)]
        return Scene(id=self.scene.id, name=self.scene.name, mode=self.mode,
                     globals=dict(self.scene.globals), edges=edges, key=self.scene.key,
                     bpm=self.scene.bpm, beats_per_bar=self.scene.beats_per_bar,
                     presets=dict(self.scene.presets), path=self.scene.path)

    def mark_saved(self, path: str, as_id: Optional[str] = None,
                   as_path: Optional[str] = None) -> None:
        """The file now matches the engine, so nothing is unsaved any more.

        A save under a new name also moves the engine into that file: from here
        on "the current scene" is the one just written, so a later plain Save
        goes back to the same place.
        """
        self._undo = []
        if as_id:
            self.scene.id = as_id
            if as_path:
                self.scene.path = as_path
        self._ui("saved", path=path, scene=self.scene.id)

    def note_ui(self, typ: str, **kw) -> None:
        """Put a line in the event stream from outside the engine thread."""
        self._ui(typ, **kw)

    @staticmethod
    def _mute_reason(e) -> str:
        """Why an enabled edge cannot make a sound, if it cannot.

        A lane silenced by a setting looks exactly like a lane with nothing to
        say: both show zero fires. On the 19:40 take the echo's velocity scale
        had been dragged to zero during the earlier click storm and saved into
        the scene, so it sat there enabled and mute for the whole session.
        """
        if not e.enabled:
            return ""
        if e.vel_scale <= 0:
            return "力度× 是 0：這條線發不出聲音"
        inst_lo = e.params.get("low")
        inst_hi = e.params.get("high")
        if inst_lo is not None and inst_hi is not None and float(inst_lo) > float(inst_hi):
            return "音域上下限反了"
        # A texture condition that names something the classifier can never
        # produce silences the lane for ever, and it looks exactly like a lane
        # with nothing to say. The panel only writes real names; a hand-edited
        # scene can misspell one.
        want = e.params.get("texture")
        if want is None:
            want = (e.params.get("when") or {}).get("texture")
        if want:
            want = [want] if isinstance(want, str) else list(want)
            unknown = [w for w in want if w not in TEXTURES]
            if unknown and not [w for w in want if w in TEXTURES]:
                return f"彈法條件寫的不是真的彈法：{'、'.join(map(str, unknown))}"
        return ""

    def _gen_sounding(self, ch: int, now: float) -> list:
        """Pitches the engine has sounding that this note has to live with.

        `ensemble` is the default because the measurement said so: over the
        21:08 take, 80 % of the engine's harsh intervals were between lanes on
        DIFFERENT instruments (echo 269 of 331, phrase 151 of 197). The room
        hears one sound; separate MIDI channels are a voice-budget notion, not
        an acoustic one. `instrument` keeps the old per-synth scope, `off`
        disables it.
        """
        mode = self.avoid_semitone
        if mode == "off":
            return []
        return self.sched.sounding_notes(now, None if mode == "ensemble" else ch)

    def _time_knob(self) -> Optional[float]:
        """The TIME control, quantised to musical ratios unless told otherwise.

        Bad Mood's CLOCK moves in harmonised steps because halving a delay is
        musical and multiplying it by 1.07 is not; its SMOOTH switch turns that
        off. `time_steps: false` in the scene does the same here.
        """
        v = self.scene.globals.get("time")
        if v is None:
            return None
        v = max(0.05, float(v))
        if self.scene.globals.get("time_steps", True):
            v = quantize_time(v)
        return v

    @property
    def avoid_semitone(self) -> str:
        """`ensemble` | `instrument` | `off` - how wide the semitone check looks."""
        v = self.scene.globals.get("avoid_semitone", "ensemble")
        if v is True:
            return "instrument"
        if v is False:
            return "off"
        return str(v)

    @property
    def master_gain(self) -> float:
        """One volume for everything the engine plays (plan §9.1).

        Before this the player had to walk the seven keyboards one at a time to
        balance the engine against their own playing. It scales velocity rather
        than sending CC7: a volume CC would write persistent state into someone
        else's synth and would have to be undone on PANIC, and CC7 is often the
        very control they are already using by hand.
        """
        return max(0.0, min(1.0, float(self.scene.globals.get("master_gain", 1.0))))

    def _master_cc(self, ev, now: float) -> None:
        """A hardware fader can own the master volume; `master_cc` says which.

        `master_ch` 0 means any human channel. Listening only - the passthrough
        that carries this same CC to whatever the zone actually plays is never
        touched.
        """
        cc = int(self.scene.globals.get("master_cc", 0) or 0)
        if not cc or ev.cc != cc:
            return
        ch = int(self.scene.globals.get("master_ch", 0) or 0)
        if ch and ev.ch != ch:
            return
        self.set_global("master_gain", round((ev.val or 0) / 127.0, 3))

    def _log_cc_in(self, ev, now: float) -> None:
        """Log incoming CCs so a fader can be identified by moving it.

        Throttled per (ch, cc): a fader sweep is a hundred messages and the
        event stream is for reading, not for drowning in.
        """
        key = (ev.ch, ev.cc)
        last = self._cc_seen.get(key)
        if last and now - last[0] < 0.25 and abs((ev.val or 0) - last[1]) < 8:
            return
        self._cc_seen[key] = (now, ev.val or 0)
        self._ui("cc_in", ch=ev.ch, cc=ev.cc, val=ev.val)

    def set_global(self, key: str, value) -> None:
        if not self._bulk:
            self._touched.add(f"global.{key}")
        self._remember(f"global.{key}", self.scene.globals.get(key))
        self.scene.globals[key] = value
        if key == "density":
            self.st.density_knob = None if value is None else float(value)
        elif key == "density_complement":
            self.st.density_complement = float(value or 0.0)
        elif key in ("time", "time_steps"):
            self.st.time_knob = self._time_knob()
        self.safety.set_globals(self.scene.globals)
        self._ui("set", path=f"global.{key}", value=value)

    def set_edge(self, edge_id: str, key: str, value) -> bool:
        e = self.graph.find_edge(edge_id)
        if e is None:
            return False
        if not self._bulk:
            self._touched.add(f"edge.{edge_id}.{key}")
        # `hasattr` is the wrong test: `lane` is a read-only property computed
        # from params, so writing it raised AttributeError. Only the declared
        # edge fields are real attributes; everything else belongs in params.
        self._remember(f"edge.{edge_id}.{key}",
                       getattr(e, key) if key in _EDGE_FIELDS else e.params.get(key))
        if key == "enabled" and e.enabled and not value:
            # Unticking a lane has to stop it NOW. It did not: on the 20:23 take
            # the player switched silence_pad off at 398.8 s, it entered again
            # 0.2 s later from a proposal already on the scheduler, and they hit
            # PANIC at 416.4 with those two notes still ringing. 全部略過 has
            # always released what it switched off; one checkbox has to mean the
            # same thing, or the difference is a trap.
            now = self.clock()
            self.sched.release(e.dst, None, now, lane=e.lane)
            self._release_lane_on(e.dst, e.lane, now)
            ls = self.lane_state.get(edge_id)
            if ls:
                ls["fired"] = False
        if key == "dst" and int(value) != e.dst:
            # Moving a lane to another instrument leaves whatever it is playing
            # ringing on the old one, with nothing left that knows how to stop
            # it: the note is keyed to (channel, note) and the lane no longer
            # goes there. A held pad would sit on the old synth until the next
            # PANIC. Take it with us.
            self._release_lane_on(e.dst, e.lane, self.clock())
        if key in _EDGE_FIELDS and key not in ("id",):
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
        if self.preset_slot != "LIVE":
            self.preset_dirty = True
        return True

    def set_all_enabled(self, on: bool) -> int:
        """Every edge on, or every edge out of the way. Returns how many moved.

        The point is testing: hearing ONE line means silencing the other ten,
        and doing that a checkbox at a time - eleven clicks out, eleven back -
        is enough friction that nobody does it and lanes go untested.

        `_bulk`, for the same reason a preset is: eleven `enabled` writes would
        bury the player's last real move at the bottom of the undo stack and
        claim they had hand-tuned eleven settings, which would then be held
        back from every style. It is undone by pressing the other button, not
        by stepping.

        Turning them off also stops what those lanes have already scheduled and
        what is already sounding. Without that, "all pass" leaves a pad ringing
        for its remaining fifteen seconds and the silence you asked for arrives
        after you have stopped listening for it.
        """
        now = self.clock()
        guard, self._bulk = self._bulk, True
        try:
            n = 0
            for e in self.graph.edges:
                if bool(e.enabled) != on:
                    e.enabled = on
                    n += 1
            if not on:
                self._release_everything(now)
        finally:
            self._bulk = guard
        self._ui("all_edges", on=on, n=n, total=len(self.graph.edges))
        if self.preset_slot != "LIVE":
            self.preset_dirty = True
        return n

    def set_instrument(self, ch: int, key: str, value) -> bool:
        inst = self.instruments.get(int(ch))
        if inst is None or not hasattr(inst, key):
            return False
        if key == "enabled" and not value:
            self._release_channel(int(ch), self.clock())
        setattr(inst, key, type(getattr(inst, key))(value) if not isinstance(getattr(inst, key), tuple) else tuple(value))
        self._ui("set", path=f"inst.{ch}.{key}", value=value)
        return True

    def _sync_knobs(self) -> None:
        """Push scene-level controls the state has to carry itself.

        Without this a saved `density` is ignored until the player happens to
        touch the slider - the scene says one thing and the engine plays
        another, which is the worst kind of setting.
        """
        d = self.scene.globals.get("density")
        self.st.density_knob = None if d is None else float(d)
        self.st.density_complement = float(self.scene.globals.get("density_complement") or 0.0)
        self.st.time_knob = self._time_knob()

    def load_scene(self, scene: Scene) -> None:
        now = self.clock()
        from .graph import scene_stamp
        self.scene_stamp = scene_stamp(scene.path)
        self._touched.clear()
        self._style_before, self.style = None, None
        self._release_everything(now, fade_s=0.5)
        self.scene = scene
        self.graph = InteractionGraph(scene, self.instruments)
        self.safety.set_globals(scene.globals)
        self._sync_knobs()
        self.lane_state.clear()
        self._shadow_group.clear()      # both are keyed by edge id: the ids are gone
        self._roll_cache.clear()
        self._lane_hist.clear()
        if scene.key is not None:
            self.st.set_key(scene.key.tonic_pc, scene.key.mode, "scene")
        if self.st.clock_source == "scene":
            self.st.set_tempo(scene.bpm, now, "scene", beats_per_bar=scene.beats_per_bar)
        self._ui("scene", id=scene.id, name=scene.name)

    # ---------------------------------------------------------------- driving
    def post(self, ev) -> None:
        self.in_queue.put(ev)

    def submit(self, fn: Callable, *args, **kw) -> None:
        """Run `fn` on the engine thread (plan §1: UI parameter changes go
        through `in_queue` as control messages).

        Everything the UI can change — scene globals, edges, instruments, mode,
        the playhead — is read by the engine thread while it builds notes and by
        the scheduler thread while it re-snaps them. Mutating it from the
        WebSocket thread is the one place that could tear that state, so the
        server hands the call over instead of making it directly.
        """
        self.in_queue.put(_Command(fn, args, kw))

    def drain(self, now: Optional[float] = None) -> int:
        n = 0
        while True:
            try:
                item = self.in_queue.get_nowait()
            except queue.Empty:
                return n
            n += 1
            t = self.clock() if now is None else now
            # The engine thread must survive a bad event or a broken scene: an
            # exception here used to kill it outright and silently, leaving the
            # ports open and nothing listening.
            try:
                if isinstance(item, _Command):
                    self._run_command(item)
                elif isinstance(item, tuple):
                    self._on_sent(item[0], item[1], t)
                else:
                    self.handle(item, t)
            except Exception as e:
                self._log_error("handle", e)

    def _run_command(self, cmd: _Command) -> None:
        try:
            cmd.fn(*cmd.args, **cmd.kw)
        except Exception as e:  # a bad UI message must not kill the engine thread
            self._log_error(getattr(cmd.fn, "__name__", "command"), e)

    def step(self, now: Optional[float] = None) -> None:
        """Synchronous test driver: drain queue, pump scheduler, tick, drain again."""
        now = self.clock() if now is None else now
        self.drain(now)
        self.sched.pump(now)
        self.drain(now)
        if now - self._last_tick >= TICK_S:
            try:
                self.tick(now)
            except Exception as e:
                self._log_error("tick", e)
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
                try:
                    if isinstance(item, _Command):
                        self._run_command(item)
                    elif isinstance(item, tuple):
                        self._on_sent(item[0], item[1], now)
                    else:
                        self.handle(item, now)
                except Exception as e:
                    self._log_error("handle", e)
                self.drain(now)
            if now - self._last_tick >= TICK_S:
                try:
                    self.tick(now)
                except Exception as e:
                    self._log_error("tick", e)

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
            if ev.kind == "cc" and ev.ch != 16:
                if ev.cc == 64:
                    self.st.set_sustain(ev.ch, (ev.val or 0) >= 64, now)
                    self._ui("pedal", ch=ev.ch, val=ev.val)
                else:
                    self._master_cc(ev, now)
                    self._log_cc_in(ev, now)
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
            self.advisor.note_human(now, ev.note)
            if self.st.chord is not None:
                self.advisor.note_chord(now, self.st.chord.quality)
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
            self._ui("human_off", ch=ev.ch, note=ev.note, held_ms=int((dur or 0) * 1000))
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
                # …and anything of this lane that has not started yet. Those
                # notes are not in `active_gen`, so the proposals above cannot
                # see them, and they would otherwise hold for their full length
                # no matter what the player did next.
                rel = float(e.params.get("release_beats", 1.0)) * self.st.beat_s
                self.sched.release_lane(e.dst, e.lane, now + rel)

    def _fire_edges(self, ev: MieEvent, now: float) -> None:
        if self._frozen:
            # The player's choice: while frozen the engine says nothing new.
            # What is held sounds alone and they play over it. Answering as well
            # would put the engine back on top of the bed it was asked to hold
            # still - and the bed is the point.
            return
        edges = self.graph.candidate_edges(ev.origin, ev.ch, ev.hop, now, self.allowed_algos,
                                           texture=self.st.texture)
        if not edges:
            return
        fired = self._roll_grouped(edges, ev, now)
        for e, p in fired:
            fn = algos.EVENT_ALGOS.get(e.algo)
            if fn is None:
                continue
            # One broken edge must not take the rest of the scene down with it:
            # a bad instrument range or algorithm parameter should silence that
            # lane alone, and say so.
            try:
                props = [x for x in fn(ev, self.st, e, self.rng) if x.kind == "on"]
                if not props:
                    continue
                props = mutation.apply(props, e.mutations, self.rng, chaos=self.eff_chaos,
                                       origin=ev.origin, beat_s=self.st.beat_s)
                self._record_fire(e, now, p)
                self._schedule_props(props, ev, e, now)
            except Exception as exc:
                self._log_error(f"edge[{e.id}]", exc)

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
            cp = constrain(p, self.st, edge, inst, tension=self._tension(edge),
                           prev=self._lane_prev(edge.dst, edge.lane),
                           others=self._other_voices(edge.dst, edge.lane, now))
            if cp is None:
                self._drop("constraint", edge, p, now)
                continue
            t_send = self.st.next_grid_t(now + max(0.0, cp.t_offset),
                                         edge.align_beats(self.st.beats_per_bar))
            adm = self.safety.admit(cp, self.st, now, t_send=t_send,
                                    voices_at=self.sched.sounding_at(cp.ch, t_send))
            if not adm.ok:
                self._drop(adm.reason or "safety", edge, cp, now)
                continue
            if adm.steal is not None:
                self._force_off(adm.steal[0], adm.steal[1], now, "steal")
            dur = self.safety.clamp_dur(cp, inst)
            t_on = t_send
            pair = NotePair(ch=cp.ch, note=cp.note, vel=cp.vel, t_on=t_on, t_off=t_on + dur, lane=cp.lane,
                            origin="GENERATIVE", root_id=ev.root_id, parent_id=ev.event_id, hop=hop,
                            edge_id=edge.id, constraint=edge.constraint, follow_off=cp.follow_off,
                            src_note=cp.src_note, max_dur=dur, ttl_wall=min(ev.ttl_wall, t_on + 4.0 * self.st.beat_s),
                            collision=collision_for(edge), voice_lead=voice_lead_for(edge),
                            tension=self._tension(edge), note_range=edge_range(edge, self.st),
                            capture_root=cp.capture_root, capture_quality=cp.capture_quality,
                            pass_id=cp.pass_id, drift=self._drift_of(edge))
            self.sched.schedule_pair(pair)
            self.safety.count_chain(ev.root_id, now)
            self.stats["gen_sched"] += 1
            n_ok += 1
            self._ui("sched", ch=pair.ch, note=pair.note, vel=pair.vel, lane=pair.lane, hop=hop,
                     edge=edge.id, in_ms=round((t_on - now) * 1000), dur_ms=round(dur * 1000))
        return n_ok

    # Both windows are musical, not absolute: at 92 BPM they are about 2.6 s and
    # 10 s, but in a slow ambient passage two lines can be four beats apart and
    # still be moving together, and a line that stopped 16 beats ago should not
    # pull the next entry towards where it happened to be.
    VOICE_WINDOW_BEATS = 4.0
    VOICE_WINDOW_MIN_S = 2.0
    LANE_HIST_BEATS = 16.0
    LANE_HIST_MIN_S = 8.0

    @property
    def _voice_window_s(self) -> float:
        return max(self.VOICE_WINDOW_MIN_S, self.VOICE_WINDOW_BEATS * self.st.beat_s)

    @property
    def _lane_hist_ttl_s(self) -> float:
        return max(self.LANE_HIST_MIN_S, self.LANE_HIST_BEATS * self.st.beat_s)

    def _tension(self, edge) -> float:
        """How far outside the chord a lane may reach (plan §11 Phase 2).

        Per edge if it sets one, else the scene global. It never opens a random
        chromatic: `function.extension_pcs` only unlocks named degrees.
        """
        v = edge.params.get("tension", self.scene.globals.get("tension", 0.0))
        return max(0.0, min(1.0, float(v)))

    def _lane_prev(self, ch: int, lane: str) -> Optional[int]:
        h = self._lane_hist.get((ch, lane))
        return h[1] if h else None

    def _other_voices(self, ch: int, lane: str, now: float) -> tuple:
        """(previous, current) of the other generated lines that just moved,
        so voice leading can see a parallel fifth coming."""
        out = []
        window = self._voice_window_s
        for (c, ln), (prev, last, t) in list(self._lane_hist.items()):
            if (c, ln) == (ch, lane) or prev is None or now - t > window:
                continue
            out.append((prev, last))
        return tuple(out)

    def _expire_lane_hist(self, now: float) -> None:
        """A line that has been quiet for a long time is not a line any more."""
        ttl = self._lane_hist_ttl_s
        for key, (_, _, t) in list(self._lane_hist.items()):
            if now - t > ttl:
                del self._lane_hist[key]

    def _note_lane_sent(self, ch: int, lane: str, note: int, now: float) -> None:
        h = self._lane_hist.get((ch, lane))
        self._lane_hist[(ch, lane)] = ((h[1] if h else None), note, now)

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
            if self.sched.is_frozen(p.ch, p.note):
                continue        # a freeze outranks a lane's own release
            n = self.sched.release(p.ch, p.note, at, lane=p.lane, src_note=p.src_note)
            if n == 0 and (p.ch, p.note) in self.st.active_gen:
                # note_on went out but its off is not in the heap any more (e.g. after steal): send now
                self._force_off(p.ch, p.note, at, "release")

    def _drop(self, reason: str, edge, p: Proposal, now: float, n: int = 1) -> None:
        # Per edge as well as in total. A control can silence a lane without
        # anything on screen saying so: on the 19:27 take the octave of one edge
        # was dragged to -3, its notes landed below the synth's range, and every
        # one was dropped while the row still looked healthy.
        eid = getattr(edge, "id", "?")
        self.edge_drops[eid] = self.edge_drops.get(eid, 0) + n
        self.stats["dropped"] += n
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + n
        self._ui("drop", reason=reason, edge=getattr(edge, "id", "?"), ch=p.ch, note=p.note, lane=p.lane)

    # ----------------------------------------------------------- scheduler
    def _live_range(self, pair: NotePair):
        """The edge's register AS IT IS NOW, not as it was when this was queued.

        `note_range` is captured on the NotePair at schedule time, and for a
        lane that waits - an echo a beat later, a phrase several seconds later
        - the player has moved by the time it sounds. That is exactly the case
        `below_player` exists for, and reading the stale value made it look
        inert: measured over the 14:07 take, an echo arrived 39 semitones above
        the hand that was on the keys, and capping the lane changed the figure
        by one percentage point because the cap was the one from before.

        Only recomputed for edges that ask to track the player; everything else
        keeps the value it was given, and pays nothing.
        """
        e = self.graph.find_edge(pair.edge_id) if pair.edge_id else None
        if e is not None and e.params.get("below_player"):
            return edge_range(e, self.st)
        return pair.note_range

    def _before_on(self, pair: NotePair, now: float) -> bool:
        """Scheduler thread: late-binding re-snap right before the note_on."""
        if self.bypass or self.stop.is_set():
            return False
        try:
            # A replay of the player's OWN part is the one thing allowed onto a
            # human channel. The rule exists so the engine cannot fight the
            # hands that are on the keys; during a review there are no hands on
            # the keys, and without hearing what you played there is no way to
            # judge whether the engine's answer was apt or intrusive - which is
            # the whole reason to listen back. Nothing else reaches this branch:
            # `play_take` is the only place that sets this lane.
            if pair.ch in self.st.human_chs and pair.lane != self.REPLAY_LANE:
                self._drop_async("human_ch_late", pair)
                return False
            target = self._phrase_target(pair)
            if target is not None:
                pair.note = diatonic_map(pair.note, pair.capture_root, pair.capture_quality,
                                         target[0], target[1])
            n2 = late_bind(pair.note, pair.constraint, self.st, self.instruments.get(pair.ch),
                           pair.collision, voice_lead=pair.voice_lead, tension=pair.tension,
                           note_range=self._live_range(pair),
                           prev=self._lane_prev(pair.ch, pair.lane),
                           others=self._other_voices(pair.ch, pair.lane, now),
                           gen_now=self._gen_sounding(pair.ch, now),
                           keep_pc=(pair.constraint == "free"),
                           taken=self.sched.sounding_notes(now, pair.ch))
            if n2 is None:
                self._drop_async("resnap", pair)
                return False
            if n2 != pair.note:
                self._ui("resnap", ch=pair.ch, frm=pair.note, to=n2)
                pair.note = n2
        except Exception as e:
            # Fail open: the note was already constrained when it was scheduled,
            # so sending it is safer than dropping it. But this runs on the
            # scheduler thread at note rate, and swallowing it silently hid the
            # fault completely.
            self._log_error("late_bind", e)
            return True
        return True

    def _drop_async(self, reason: str, pair: NotePair) -> None:
        self.stats["dropped"] += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1
        self._ui("drop", reason=reason, edge=pair.edge_id, ch=pair.ch, note=pair.note, lane=pair.lane)

    # 25 Hz. A controller sweep is heard as smooth from about 20 updates a
    # second and MIDI is a 31250 baud wire shared with the notes; a tick-rate
    # stream would be 200 messages a second per channel for no audible gain.
    SWELL_EVERY_S = 0.04

    def _swell_of(self, edge) -> Optional[tuple]:
        """An edge's breath, as (cc, depth, seconds, shape, phase), or None.

        Two owners, deliberately. The INSTRUMENT says which controller it
        listens to, because that is a fact about the patch loaded on it and
        nothing else can know it - a Fantom pad on CC11, a filter opened by
        CC74, a synth that ignores both. The EDGE says how deep and how slow,
        because that is the music. Either one silent means no breath, and
        nothing is ever sent to an instrument that has not said what to send.
        """
        inst = self.instruments.get(edge.dst)
        cc = int(getattr(inst, "swell_cc", 0) or 0) if inst else 0
        if not cc:
            return None
        d = edge.params.get("swell")
        if not isinstance(d, dict):
            return None
        depth = float(d.get("depth", 0) or 0)
        top = float(d.get("top", 1) if d.get("top") is not None else 1)
        # A ceiling on its own is a reason to speak: "quieter, but not moving"
        # is a perfectly ordinary thing to ask a pad for.
        if depth <= 0 and top >= 1.0:
            return None
        beats = float(d.get("beats", 8) or 8)
        period = max(0.5, beats * self.st.beat_s * (self.st.time_knob or 1.0))
        phase = d.get("phase")
        if phase is None:
            phase = (sum(ord(c) for c in edge.id) % 100) / 100.0
        return (cc, depth, period, str(d.get("shape", "breathe")), float(phase), top)

    def _swell(self, now: float) -> None:
        """Move the breath controller on every channel a breathing lane is using.

        Engine thread, at 25 Hz, and it touches nothing but the wire: no state
        the algorithms read, no proposals, no scheduling. A channel is only
        written while a lane that asked for a breath is actually SOUNDING on it,
        and is handed straight back the moment it is not - see `swell_at` for
        why giving it back matters more than the effect itself.

        One breath per channel. Two lanes on the same synth would otherwise
        write conflicting values to one channel-wide controller at 25 Hz, and
        the loser would not merely be ignored - the two would interleave and
        neither shape would be heard. The deeper one wins, and the panel says
        which channels are shared.
        """
        if now - self._swell_t < self.SWELL_EVERY_S:
            return
        self._swell_t = now
        want: dict[int, tuple] = {}
        if not self.bypass:
            live = {(ch, g.lane) for (ch, _), g in self.st.active_gen.items()}
            for e in self.graph.edges:
                if not e.enabled or (e.dst, e.lane) not in live:
                    continue
                sw = self._swell_of(e)
                if sw is None:
                    continue
                # How far below 127 this lane ever takes the channel. A
                # ceiling counts as much as a dip, or a lane asking only to be
                # quieter would lose to one asking for a shallow wiggle and
                # never be heard at all.
                reach = lambda x: 1.0 - x[5] * (1.0 - x[1])
                if e.dst not in want or reach(sw) > reach(want[e.dst]):
                    want[e.dst] = sw
        for ch, (cc, depth, period, shape, phase, top) in want.items():
            self._write_swell(ch, cc, swell_at(now, depth, period, shape, phase, top))
        for ch in [c for c in self._swell_sent if c not in want]:
            self._rest_swell(ch)

    def _write_swell(self, ch: int, cc: int, val: int) -> None:
        prev = self._swell_sent.get(ch)
        if prev is not None and prev[0] == cc and prev[1] == val:
            return                            # only on a change: the wire is shared
        if prev is not None and prev[0] != cc:
            self._rest_swell(ch)              # the controller itself changed under us
        self._swell_sent[ch] = (cc, val)
        self._send_cc(ch, cc, val)

    def _rest_swell(self, ch: int) -> None:
        """Give a channel its controller back, at the top."""
        sent = self._swell_sent.pop(ch, None)
        if sent is None:
            return
        if sent[1] != 127:
            self._send_cc(ch, sent[0], 127)

    def _rest_all_swells(self) -> None:
        for ch in list(self._swell_sent):
            self._rest_swell(ch)

    def _send_cc(self, ch: int, cc: int, val: int) -> None:
        try:
            self.send(port_for(ch),
                      self.mido.Message("control_change", channel=ch - 1, control=cc, value=val))
        except Exception as e:
            # a dead port is worth knowing about, but a breath must never be the
            # thing that stops the engine playing
            self._swell_sent.pop(ch, None)
            self._log_error("swell_send", e)

    def _drift_of(self, edge) -> Optional[tuple]:
        """An edge's self-moving level, as (depth, seconds, shape, phase).

        Resolved when the note is scheduled - the shape and speed are settings -
        while the PHASE of the wave is read at send time, which is the whole
        point of it moving. Each edge gets its own offset from its id, so two
        lanes drifting at the same speed do not swell in lockstep.
        """
        d = edge.params.get("vel_drift")
        if not isinstance(d, dict):
            return None
        depth = float(d.get("depth", 0) or 0)
        if depth <= 0:
            return None
        beats = float(d.get("beats", 16) or 16)
        period = max(0.5, beats * self.st.beat_s * (self.st.time_knob or 1.0))
        shape = str(d.get("shape", "sine"))
        phase = d.get("phase")
        if phase is None:
            phase = (sum(ord(c) for c in edge.id) % 100) / 100.0
        return (depth, period, shape, float(phase))

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
            # The master volume is read HERE, not when the note was scheduled:
            # the fader has to act on everything not yet sounding, or a sweep
            # leaves seconds of already-queued notes at the old level. A note
            # already ringing keeps its velocity - that is inherent to volume
            # by velocity, and the honest limit of this approach.
            gain = self.master_gain
            if p.drift:
                gain *= drift_at(now, p.drift[0], p.drift[1], p.drift[2], p.drift[3])
            vel = int(round(p.vel * gain))
            if vel < 1:
                p.muted = True      # the fader is down: silence, not a fault
                self.stats["muted"] += 1
                return
            # The drift can push a loud note over the top of what MIDI can say.
            # Clamping here rather than in `drift_at` keeps the oscillator a
            # pure function of time; mido raises on 128 and the note would be
            # lost, which is the one thing a volume must never do.
            vel = min(127, vel)
            p.sent_vel = vel
            msg = self.mido.Message("note_on", channel=p.ch - 1, note=p.note, velocity=vel)
        else:
            if p.muted:
                return              # never started, so nothing to release
            msg = self.mido.Message("note_off", channel=p.ch - 1, note=p.note, velocity=0)
        self.send(port_for(p.ch), msg)
        self.echo_filter.note_sent(p.ch, p.note, due.kind == "on", now)
        self.in_queue.put((due.kind, p))

    def _on_sent(self, kind: str, p: NotePair, now: float) -> None:
        """Engine thread: bookkeeping + feedback (pipeline step 10)."""
        if kind == "on":
            self.stats["gen_sent"] += 1
            self.st.gen_on(p.ch, p.note, now, p.lane, p.root_id, p.hop, p.src_note, p.max_dur)
            self._note_lane_sent(p.ch, p.lane, p.note, now)
            # The LENGTH belongs on this event, not on `sched`. Late binding
            # re-snaps the pitch between scheduling and sending - measured over
            # the 22:54 take, 46 % of notes went out at a different pitch than
            # they were scheduled at - so a `sched` row cannot be paired with
            # the note that actually sounded, and its `dur_ms` describes a pitch
            # that never played. Without this nothing downstream can draw or
            # measure how long a generated note lasted.
            #
            # `dur_ms` is the length PLANNED at send time; `follow` marks the
            # ones whose real release is the human's (Shadow), where the plan is
            # only a safety cap. The matching `off` event carries the truth.
            extra = {"follow": True} if p.follow_off else {}
            self.advisor.note_gen(now, p.lane, p.note)
            self._ui("gen", ch=p.ch, note=p.note, vel=p.sent_vel or p.vel, lane=p.lane,
                     hop=p.hop, edge=p.edge_id, gain=round(self.master_gain, 2),
                     dur_ms=round(max(0.0, p.t_off - now) * 1000), **extra)
            if p.lane != self.REPLAY_LANE and self.graph.edges_from(p.ch) and not self.bypass:
                fb = MieEvent(event_id=next_id(), kind="note_on", t_wall=now, ch=p.ch, note=p.note, vel=p.vel,
                              origin="GENERATIVE", root_id=p.root_id, parent_id=p.parent_id, source_ch=p.ch,
                              hop=p.hop, ttl_wall=p.ttl_wall, lane=p.lane)
                self._fire_edges(fb, now)
        else:
            self.st.gen_off(p.ch, p.note)
            # EVERY release, not only the forced ones. Until now `off` was
            # emitted from `_force_off` alone, so a take recorded 852 note-ons
            # and 59 offs: the log knew when a note started and never when it
            # stopped. `_force_off` marks its own pairs `off_sent` before the
            # scheduler reaches them, so it still logs exactly once.
            #
            # One `gen` in a few hundred has no `off`, and that is correct: when
            # the same pitch retriggers on the same channel before the first has
            # been released, both pairs share ONE note_off - a synth has one
            # voice for a pitch, and a second note_off would cut whatever is
            # sounding there now. Anything reading this log should fall back to
            # the note's own `dur_ms` when no release arrives.
            self._ui("off", ch=p.ch, note=p.note, why="end",
                     held_ms=round(max(0.0, now - p.t_on) * 1000))

    def _force_off(self, ch: int, note: int, now: float, why: str = "") -> None:
        self.sched.thaw(ch, note)       # unfreeze and PANIC outrank a freeze
        g = self.st.active_gen.get((ch, note))
        direct = g is not None
        self.sched.release(ch, note, now, mark_sent=direct)
        if direct:
            self.send(port_for(ch), self.mido.Message("note_off", channel=ch - 1, note=note, velocity=0))
            self.echo_filter.note_sent(ch, note, False, now)
            self.st.gen_off(ch, note)
            # the same shape as a natural release: every `off` says how long
            # the note actually sounded, so a consumer never has two cases
            self._ui("off", ch=ch, note=note, why=why,
                     held_ms=round(max(0.0, now - g.t_on) * 1000))

    def _release_lane_on(self, ch: int, lane: str, now: float) -> None:
        """Everything this lane is sounding on this channel, off now."""
        for (c, n), g in list(self.st.active_gen.items()):
            if c == ch and getattr(g, "lane", None) == lane:
                self._force_off(c, n, now, "moved")

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
        self.replaying = False
        self._frozen, self._frozen_lane = False, None
        for ls in self.lane_state.values():
            ls["fired"] = False

    # ---------------------------------------------------------------- tick
    ADVICE_EVERY_S = 4.0

    def tick(self, now: float) -> None:
        self._last_tick = now
        self.st.tick(now)
        # Every few seconds, not every tick: this walks three bounded deques and
        # nothing on the MIDI path waits for it, but there is no reason to do it
        # two hundred times a second either.
        if now - self._advice_t >= self.ADVICE_EVERY_S:
            self._advice_t = now
            try:
                self.advice = [a for a in self.advisor.confirm(
                    advise(self.advisor.measure(now), self.scene.globals,
                           list(self.graph.edges)))
                    if a["id"] not in self.muted_advice]
            except Exception as e:
                self._log_error("advisor", e)
                self.advice = []
        self._swell(now)
        if not self.bypass:
            self.st.refresh_texture(now)
            timed = [] if self._frozen else self.graph.timed_edges(self.allowed_algos,
                                                                   texture=self.st.texture)
            for e in timed:
                lanes_ok = self.mode_caps.get("timed_lanes")
                if lanes_ok and e.lane not in lanes_ok:
                    continue
                ls = self.lane_state.setdefault(e.id, {})
                fn = algos.TICK_ALGOS.get(e.algo)
                if fn is None:
                    continue
                try:
                    props = fn(self.st, e, self.rng, now, ls, self._tension(e))
                except Exception as exc:
                    self._log_error(f"edge[{e.id}]", exc)
                    continue
                if not props:
                    continue
                # releases a timed lane asks for are never probability-gated
                offs = [x for x in props if x.kind == "off"]
                if offs:
                    self._apply_offs(offs, e, now)
                    # and whatever of this lane has not started yet, same reason
                    # as `_human_came_back`: those notes are not in active_gen
                    rel = float(e.params.get("release_beats", 1.0)) * self.st.beat_s
                    self.sched.release_lane(e.dst, e.lane, now + rel)
                    self._ui("lane_off", edge=e.id, why=ls.get("left", ""), n=len(offs))
                props = [x for x in props if x.kind == "on"]
                if not props:
                    continue
                p = p_eff(e, self.scene.globals, self.st.human_energy, self.mode_caps)
                if self.rng.random() >= p:
                    self._ui("skip", edge=e.id, p=round(p, 3))
                    skip_fn = algos.TICK_SKIP.get(e.algo)
                    if skip_fn is not None:
                        skip_fn(self.st, e, now, ls)
                    continue
                root = MieEvent(event_id=next_id(), kind="tick", t_wall=now, ch=0, origin="HUMAN",
                                root_id=next_id(), source_ch=0, hop=0, lane=e.lane)
                try:
                    self._record_fire(e, now, p)
                    self._schedule_props(props, root, e, now)
                except Exception as exc:
                    self._log_error(f"edge[{e.id}]", exc)
        for (ch, note) in self.safety.watchdog(self.st, now):
            self._force_off(ch, note, now, "watchdog")
        if self._frozen and not self.st.active_gen:
            self._frozen, self._frozen_lane = False, None   # the last held note went
        self.safety.prune_chains(now)
        self._expire_lane_hist(now)

    # --------------------------------------------------------------- PANIC
    def panic(self, reason: str = "manual") -> None:
        now = self.clock()
        # Before anything else. A PANIC that left CC11 at 40 would silence the
        # channel for whatever came next - including the player's own hands -
        # and the one button that must always give everything back would be the
        # button that took something away.
        self._rest_all_swells()
        self.stats["panics"] += 1
        self.panicked = True
        self.mode = "BYPASS"
        sounding = self.sched.cancel_all()
        active: dict[tuple[int, int], float] = {k: g.t_on for k, g in list(self.st.active_gen.items())}
        for p in sounding:
            active.setdefault((p.ch, p.note), p.t_on)
        by_port = {"hst": {}, "reaper": {}}
        for k, t in active.items():
            by_port[port_for(k[0])][k] = t
        for port in ("hst", "reaper"):
            for m in panic_messages(by_port[port]):
                try:
                    self.send(port, m)
                except Exception as e:
                    # keep going: the rest of the PANIC packet and the other
                    # port still have to go out, but a dead port during PANIC is
                    # exactly the thing to know about
                    self._log_error(f"panic_send[{port}]", e)
        self.st.active_gen.clear()
        self.replaying = False      # cancel_all took its notes; say so too
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
        elif action == "freeze":
            self.freeze(not self._frozen) if pressed else None
        elif action == "freeze.hold":
            self.freeze(pressed)              # momentary: held down = frozen
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
        if self.event_sink is not None:
            self.event_sink(kw)

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
            "edits": {"undo": len(self._undo), "unsaved": bool(self._undo)},
            "frozen": self._frozen,
            "preset": {"slot": self.preset_slot, "dirty": self.preset_dirty,
                       "stored": sorted(k for k, v in self.scene.presets.items() if v)},
            "advice": self.advice,
            "muted_advice": sorted(self.muted_advice),
            "touched": sorted(self._touched),
            "replaying": self.replaying,
            "style": {"id": self.style,
                      "list": [{"id": x["id"], "name": x.get("name") or x["id"],
                                "hint": x.get("hint", "")} for x in self.styles]},
            "state": self.st.to_dict(now),
            "restraint": round(p_eff(_UNIT_EDGE, {"prob_scale": 1.0, "restraint": self.scene.globals.get("restraint", 1.0),
                                                  "restraint_curve": self.scene.globals.get("restraint_curve", 1.0)},
                                     self.st.human_energy), 3),
            "stats": dict(self.stats), "drops": dict(self.drop_reasons),
            "jitter": self.sched.jitter_summary(), "pending": len(self.sched),
            "edges": [dict(e.to_dict(), fires=self.edge_fires.get(e.id, 0),
                           drops=self.edge_drops.get(e.id, 0), mute=self._mute_reason(e),
                           ago=round(now - self.edge_last[e.id], 2) if e.id in self.edge_last else None)
                      for e in self.graph.edges],
            "instruments": [i.to_dict() for i in self.instruments.values()],
            "last_control": self.last_control,
        }


class _UnitEdge:
    prob = 1.0
    params: dict = {}


_UNIT_EDGE = _UnitEdge()
