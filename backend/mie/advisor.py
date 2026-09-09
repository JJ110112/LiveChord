"""Live advisory: name a measurable mismatch, and the setting that fixes it.

The proposal this came from wanted the engine to detect the GENRE being played
and switch style automatically. Measured over six takes, the features available
cannot do that: the texture reading is dominated by `quiet` and `melody` in
every single take, and the chord qualities are mostly two-note fragments
(`5`, `(3)`, `m(3)`) because the reading flickers as fingers land and lift.
Nothing there separates worship from jazz from pop, so a genre detector would
be guessing and calling it intelligence.

What the same data DOES support is naming a gap between what the player is
doing and what the engine is set up to answer with. Each signal below is a
fact plus the name of the control that changes it, and every threshold was
read off the player's own takes rather than invented:

    take          rich%   gen/human   tension   biggest lane overlap
    2026-09-08 22:16   9%     1.71       0        shadow 94%
    2026-09-08 22:29  10%     1.51       0        sustain 85%
    2026-09-08 22:42  36%     2.33       0        phrase 82%
    2026-09-08 22:54   8%     1.52       0        shadow 85%
    2026-09-09 10:20  42%     0.99       0        shadow 80%
    2026-09-09 10:24  10%     1.62       0        shadow 88%

  * RICHNESS separates cleanly at 15 %: four takes sit at 8-10 % and two at
    36-42 %. Tension was 0 in all six, so on those two the engine was refusing
    the harmony the player was actually playing.
  * DENSITY: the whole-take average of the one that felt too full ("整體變密了")
    was 2.33, but the advisor reads a 45 s window, and windows reach 2.0 in
    takes nobody minded. Swept across the recordings, 2.5 separates them
    exactly: 21 firings in that take, none at all in the four the player was
    happy with.
  * OVERLAP: the proposal asked for 80 % across all lanes, which every take
    exceeds - Shadow, Echo, Phrase and Follow all take their pitch from the
    player, so overlapping is their entire job and reporting them for it would
    make this a light that is always on. Only the algorithms that choose their
    own register count: a pad sitting inside the player's hands is a fault, a
    shadow doing it is the feature.

It never acts. It never changes anything on its own. Each piece of advice
carries the exact settings that would apply it, so pressing the button is one
ordinary parameter change that Ctrl+Z puts back.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

# Only these ALGORITHMS choose their own register; everything else derives its
# pitch from the player's, so overlapping the player is what it is FOR. An
# allow-list, not a deny-list: naming the doubling lanes meant `phrase` slipped
# through and was reported in five takes out of six for replaying the phrase
# it had just been given, which is its entire job. Keyed by algorithm rather
# than lane name because a scene names its own lanes.
INDEPENDENT_ALGOS = frozenset({"sustain", "silence", "density"})

WINDOW_S = 45.0             # how far back "what you are playing now" reaches
MIN_CHORDS = 20             # below this the richness figure is noise
MIN_HUMAN = 25              # below this the density figure is noise
# A pad speaks about once every five to ten seconds, so 45 s gives it five or
# six notes - a bar of twelve would have meant this signal could only ever fire
# for the fast lanes, which are exactly the ones it must not fire for. Small
# samples are handled by `confirm`: it has to read the same way twice.
MIN_LANE_NOTES = 5          # below this an overlap figure is noise

RICH_AT = 0.20              # extensions / altered / dim / aug share
TENSION_FOR_RICH = 0.35     # the level at which 9ths and 13ths are allowed at all
# 2.5, measured rather than guessed. 2.0 fired in every one of the four takes
# of 2026-09-09 11:xx, which the player did NOT find too full - a 45 s window
# touches 2.0 often while the take averages 1.0-1.8, and advice that appears in
# every take is not advice. Swept over the recordings, 2.5 fires 21 times in
# the 22:42 take ("整體變密了" - window median 2.70) and NOT ONCE in any of the
# four that drew no complaint (window medians 1.10-1.64).
DENSE_AT = 2.5              # generated notes per human note
# Also measured: 0.35 spoke once in a take the player was happy with (whole
# take 0.92, one window dipping below). The quiet end is the less useful of the
# two anyway - the engine being restrained is rarely news - so it is set where
# it only catches a lane that has actually been switched off by accident.
SPARSE_AT = 0.25
OVERLAP_AT = 0.80

_RICH_MARKS = ("7", "9", "11", "13", "6")


def is_rich(quality: str) -> bool:
    """Does this chord say something a plain triad does not?"""
    q = quality or ""
    return q in ("dim", "aug", "m7b5") or any(m in q for m in _RICH_MARKS)


class Advisor:
    """Rolling counters, and a reading taken from them on demand.

    Everything here runs on the engine thread, at chord rate and note rate, and
    does no more work than appending to three bounded deques.
    """

    def __init__(self, window_s: float = WINDOW_S):
        self.window_s = float(window_s)
        self._chords: deque = deque()       # (t, rich)
        self._human: deque = deque()        # t
        self._gen: deque = deque()          # (t, lane, note)
        self._human_notes: deque = deque()  # (t, note)
        self._last_ids: set = set()

    # ---- the three things it watches ------------------------------------
    def note_chord(self, now: float, quality: str) -> None:
        self._chords.append((now, is_rich(quality)))

    def note_human(self, now: float, note: int) -> None:
        self._human.append(now)
        self._human_notes.append((now, note))

    def note_gen(self, now: float, lane: str, note: int) -> None:
        self._gen.append((now, lane, note))

    def confirm(self, advices: list) -> list:
        """Only advice that was true LAST time too.

        A 45 s window moves, and a reading that crosses a threshold for one
        evaluation and falls back is noise - worse than noise on a panel the
        player glances at mid-phrase. Two readings in a row, eight seconds
        apart, is the cheapest way to mean it.
        """
        now_ids = {a["id"] for a in advices}
        keep = [a for a in advices if a["id"] in self._last_ids]
        self._last_ids = now_ids
        return keep

    def _prune(self, now: float) -> None:
        cut = now - self.window_s
        for d in (self._chords, self._human, self._gen, self._human_notes):
            while d and (d[0][0] if isinstance(d[0], tuple) else d[0]) < cut:
                d.popleft()

    # ---- the reading ----------------------------------------------------
    def measure(self, now: float) -> dict:
        self._prune(now)
        chords = list(self._chords)
        human = list(self._human)
        gen = list(self._gen)
        notes = [n for _, n in self._human_notes]
        m = {
            "chords": len(chords),
            "rich": (sum(1 for _, r in chords if r) / len(chords)) if chords else 0.0,
            "human": len(human),
            "gen": len(gen),
            "per_human": (len(gen) / len(human)) if human else 0.0,
            "lanes": {},
            "hands": None,
        }
        if len(notes) >= MIN_HUMAN:
            s = sorted(notes)
            lo, hi = s[len(s) // 10], s[9 * len(s) // 10]   # the middle 80 %
            m["hands"] = (lo, hi)
            per: dict = {}
            for _, lane, n in gen:
                per.setdefault(lane, []).append(n)
            for lane, ns in per.items():
                if len(ns) < MIN_LANE_NOTES:
                    continue
                m["lanes"][lane] = {
                    "n": len(ns),
                    "overlap": sum(1 for n in ns if lo <= n <= hi) / len(ns),
                }
        return m


def advise(m: dict, globals_: dict, edges: list) -> list[dict]:
    """Turn a reading into advice. Pure: same numbers in, same advice out.

    Each entry carries `fix`, a list of (path, value) the panel can apply
    verbatim through the ordinary `set` message - so applying advice is an
    ordinary parameter change, visible on the sliders and undone with Ctrl+Z.
    """
    out: list[dict] = []
    tension = float(globals_.get("tension", 0.0) or 0.0)

    # A. the engine is refusing harmony the player is actually playing
    if m["chords"] >= MIN_CHORDS and m["rich"] >= RICH_AT and tension < TENSION_FOR_RICH:
        out.append({
            "id": "tension_gap",
            "level": "info",
            "text": f'你彈的和弦有 {round(m["rich"] * 100)}% 帶延伸音或變化音，'
                    f'但張力是 {tension:g}——引擎只能用和弦內音回應',
            "why": "tension 0.35 以上才允許 9 / 11 / 13；0.85 以上才有變化音",
            "fix": [("global.tension", 0.5)],
            "fix_label": "把張力調到 0.5",
            "alt": {"style": "jazz"},
        })

    # B. how much the engine is saying for each thing the player says
    if m["human"] >= MIN_HUMAN:
        if m["per_human"] >= DENSE_AT:
            out.append({
                "id": "too_dense",
                "level": "warn",
                "text": f'你每彈 1 個音，引擎回你 {m["per_human"]:.1f} 個',
                "why": "上一次你說「整體變密了」時是 2.3",
                "fix": [("global.density", max(0.15, round(float(globals_.get("density", 0.5) or 0.5) - 0.2, 2)))],
                "fix_label": "厚度降 0.2",
                "alt": {"style": "sparse"},
            })
        elif m["per_human"] <= SPARSE_AT:
            out.append({
                "id": "too_sparse",
                "level": "info",
                "text": f'你每彈 1 個音，引擎只回 {m["per_human"]:.2f} 個',
                "why": "可能是機率或厚度太低，也可能就是你要的留白",
                "fix": [("global.density", min(0.95, round(float(globals_.get("density", 0.5) or 0.5) + 0.2, 2)))],
                "fix_label": "厚度加 0.2",
            })

    # C. a lane that picks its OWN register, sitting inside the player's hands
    if m["hands"]:
        lo, hi = m["hands"]
        by_lane = {}
        for e in edges:
            by_lane.setdefault(e.lane, e)
        for lane, d in sorted(m["lanes"].items(), key=lambda kv: -kv[1]["overlap"]):
            if d["overlap"] < OVERLAP_AT:
                continue
            e = by_lane.get(lane)
            if e is None or e.algo not in INDEPENDENT_ALGOS:
                continue
            fix = []
            if e.params.get("above_held") is not True and "low" in e.params:
                fix.append((f"edge.{e.id}.above_held", True))
            else:
                fix.append((f"edge.{e.id}.octave", int(e.octave) + 1))
            out.append({
                "id": f"overlap_{lane}",
                "level": "info",
                "text": f'「{lane}」有 {round(d["overlap"] * 100)}% 的音落在你雙手之間'
                        f'（{_nn(lo)}–{_nn(hi)}）',
                "why": "這條線自己挑音域，擠在你手上會糊掉；Shadow / 回音不算，那是它們的工作",
                "fix": fix,
                "fix_label": "讓它讓開",
            })
            break                       # one at a time; the worst one
    return out


_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _nn(n: int) -> str:
    return f"{_NOTE_NAMES[n % 12]}{n // 12 - 1}"
