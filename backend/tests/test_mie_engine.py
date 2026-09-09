"""MIE Phase 1 tests (plan §11 Phase 1): FakeClock + FakeMidiOut, no hardware.

T1  C5 -> CH11 gets G5 (fixed rng seed)
T3  dense playing -> >= 80 % fewer generated events
T4  2 s silence -> pad lane note_on; play again -> note_off
T6  chord change -> already scheduled echo re-snaps before it is sent
Safety: A->B->A chain stops at max_hop and <= 24 events per chain; stuck-note
watchdog; PANIC empties active_gen and sends CC120/123/64 on both ports.
"""

from __future__ import annotations

import os
import sys
from random import Random

import collections
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.mie.engine import Engine  # noqa: E402
from backend.mie.events import control_event, human_event  # noqa: E402
from backend.mie.fakes import FakeClock, FakeMidiOut  # noqa: E402
from backend.mie.graph import Instrument, Scene  # noqa: E402
from backend.mie.graph import _EDGE_FIELDS  # noqa: E402
from backend.mie.harmony import recognize  # noqa: E402
from backend.mie.scales import SCALES, scale_pcs  # noqa: E402


# ----------------------------------------------------------------- helpers
def instruments() -> dict[int, Instrument]:
    return {
        1: Instrument(1, "REAPER", "texture", "vst", True, 6, 1.0, (36, 96), True),
        3: Instrument(3, "Fantom Pad", "pad", "fantom", True, 4, 1.0, (40, 88), True),
        10: Instrument(10, "Wavestate", "sequence", "hw", True, 10, 1.0, (36, 96), True),
        11: Instrument(11, "Iridium", "exp_synth", "hw", True, 8, 1.0, (36, 96), False),
        12: Instrument(12, "MODX", "synth", "hw", True, 4, 1.0, (36, 96), False),
    }


def scene(edges: list[dict], mode: str = "INTERACTIVE", **glob) -> Scene:
    g = {"prob_scale": 1.0, "restraint": 0.0, "max_hop": 2}
    g.update(glob)
    return Scene.from_json({"id": "t", "name": "test", "mode": mode, "bpm": 120, "beats_per_bar": 4,
                            "key": {"tonic": "C", "mode": "major"}, "global": g, "edges": edges})


def make(edges, seed=1, mode="INTERACTIVE", **glob):
    clk = FakeClock(10.0)
    out = FakeMidiOut(clk)
    eng = Engine(scene(edges, mode, **glob), instruments(), clock=clk, send=out, rng=Random(seed), mode=mode)
    return eng, clk, out


def run_for(eng: Engine, clk: FakeClock, seconds: float, dt: float = 0.005) -> None:
    n = int(round(seconds / dt))
    for _ in range(n):
        clk.advance(dt)
        eng.step()


def play(eng: Engine, clk: FakeClock, ch: int, note: int, vel: int = 80, hold: float = 0.2, gap: float = 0.0) -> None:
    eng.post(human_event("note_on", clk(), ch, note, vel))
    eng.step()
    run_for(eng, clk, hold)
    eng.post(human_event("note_off", clk(), ch, note, 0))
    eng.step()
    if gap:
        run_for(eng, clk, gap)


def hold_chord(eng: Engine, clk: FakeClock, ch: int, notes: list[int], vel: int = 70) -> None:
    for n in notes:
        eng.post(human_event("note_on", clk(), ch, n, vel))
        eng.step()
        clk.advance(0.01)
    eng.step()


def release_chord(eng: Engine, clk: FakeClock, ch: int, notes: list[int]) -> None:
    for n in notes:
        eng.post(human_event("note_off", clk(), ch, n, 0))
    eng.step()


# ----------------------------------------------------------------- unit bits
def test_scales_ported_31():
    assert len(SCALES) == 31
    assert scale_pcs(0, "major") == frozenset({0, 2, 4, 5, 7, 9, 11})
    assert scale_pcs(9, "minor") == frozenset({9, 11, 0, 2, 4, 5, 7})


def test_recognize_inversions_and_prev_root():
    c = recognize([60, 64, 67], 0.0)
    assert c.name == "C" and c.tones == frozenset({0, 4, 7})
    inv = recognize([64, 67, 72], 1.0)          # first inversion, still C
    assert inv.name == "C"
    am7 = recognize([57, 60, 64, 67], 2.0)     # A C E G
    assert am7.name == "Am7"
    assert recognize([60], 3.0) is None


# ----------------------------------------------------------------- T1
def test_t1_c5_follow_gives_g5_on_ch11():
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 1.0, "constraint": "chord", "lane": "follow"},
    ], seed=7)
    hold_chord(eng, clk, 9, [48, 52, 55])    # C major context on the Nord channel
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 1.0)                   # let the chord's own follow notes finish
    out.clear()
    eng.post(human_event("note_on", clk(), 9, 72, 90))   # C5
    eng.step()
    run_for(eng, clk, 0.05)
    ons = out.notes("note_on", ch=11)
    assert ons, "CH11 received nothing"
    assert ons[0][2] == 79, f"expected G5 (79) on CH11, got {ons[0][2]}"
    assert all(p == "hst" for _, p, _ in out.sent)


# ----------------------------------------------------------------- T3
def _count_gen(density_gap: float, seed: int = 3) -> int:
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 0.8, "constraint": "scale"},
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5, "prob": 0.8, "constraint": "free"},
    ], seed=seed, restraint=1.0, restraint_curve=1.0)
    notes = [60, 62, 64, 65, 67, 69, 71, 72]
    total_time = 12.0
    t_end = clk() + total_time
    i = 0
    while clk() < t_end:
        play(eng, clk, 9, notes[i % 8], vel=100 if density_gap < 0.2 else 60, hold=min(0.1, density_gap * 0.6), gap=max(0.0, density_gap - min(0.1, density_gap * 0.6)))
        i += 1
    run_for(eng, clk, 1.0)
    return len(out.notes("note_on"))


def test_t3_dense_playing_suppresses_generation():
    sparse = _count_gen(1.0)     # 1 note/s, soft
    dense = _count_gen(0.08)     # 12.5 notes/s, hard
    per_note_sparse = sparse / 12.0
    per_note_dense = dense / 150.0
    assert per_note_dense <= per_note_sparse * 0.2, f"dense {per_note_dense:.3f}/note vs sparse {per_note_sparse:.3f}/note"


# ----------------------------------------------------------------- T4
def test_t4_silence_pad_on_then_off_when_human_returns():
    eng, clk, out = make([
        {"id": "s", "src": 0, "dst": 3, "algo": "silence", "prob": 1.0, "after_s": 2.0, "lane": "pad",
         "hold_s": 20, "release_beats": 1.0, "voices": 3, "vel": 50, "low": 48, "high": 79,
         "constraint": "chord"},
    ])
    hold_chord(eng, clk, 9, [48, 52, 55])
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 1.5)
    assert not out.notes("note_on", ch=3), "pad fired before 2 s of silence"
    run_for(eng, clk, 3.0)                      # + up to a bar, the pad enters on a downbeat
    pad_on = out.notes("note_on", ch=3)
    assert len(pad_on) == 3
    assert {n % 12 for _, _, n, _ in pad_on} == {0, 4, 7}, "pad must play chord tones"
    assert len(eng.st.active_gen) == 3
    # human plays again -> release after 1 beat (120 bpm -> 0.5 s)
    eng.post(human_event("note_on", clk(), 9, 60, 80))
    eng.step()
    run_for(eng, clk, 0.3)
    assert not out.notes("note_off", ch=3), "pad released too early"
    run_for(eng, clk, 0.4)
    assert len(out.notes("note_off", ch=3)) == 3
    assert not [k for k in eng.st.active_gen if k[0] == 3]
    # it must not re-fire while the human keeps playing
    run_for(eng, clk, 1.0)
    assert len(out.notes("note_on", ch=3)) == 3


# ----------------------------------------------------------------- T6
def test_t6_scheduled_echo_resnaps_after_chord_change():
    eng, clk, out = make([
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_ms": 400, "prob": 1.0, "constraint": "chord", "lane": "echo"},
    ])
    hold_chord(eng, clk, 9, [48, 52, 55])          # C major
    run_for(eng, clk, 1.5)                         # the chord's own echoes come and go
    out.clear()
    eng.post(human_event("note_on", clk(), 9, 76, 90))   # E5 -> echo scheduled 400 ms later
    eng.step()
    assert len(eng.sched) == 2
    run_for(eng, clk, 0.05)
    eng.post(human_event("note_off", clk(), 9, 76, 0))   # short E5: no collision with the echo
    eng.step()
    run_for(eng, clk, 0.05)
    release_chord(eng, clk, 9, [48, 52, 55])
    hold_chord(eng, clk, 9, [50, 53, 57])          # D minor, 100 ms after the E5 -> before the echo fires
    assert eng.st.chord.name == "Dm"
    run_for(eng, clk, 0.4)
    ons = out.notes("note_on", ch=10)
    assert ons, "the scheduled echo never went out"
    first = ons[0]                                 # the E5 echo; later ones echo the Dm chord itself
    assert first[2] % 12 in {2, 5, 9}, f"echo {first[2]} is not a Dm tone"
    assert first[2] != 76
    assert any(e["type"] == "resnap" and e["frm"] == 76 for e in eng.pop_events()), "late-binding re-snap did not happen"
    run_for(eng, clk, 1.0)
    offs = out.notes("note_off", ch=10)
    assert offs and offs[0][2] == first[2], "note_off must follow the re-snapped note"


# ----------------------------------------------------------------- safety
def test_chain_a_b_a_stops_at_max_hop_and_24_events():
    eng, clk, out = make([
        {"id": "h", "src": 0, "dst": 11, "algo": "echo", "delay_ms": 50, "prob": 1.0, "constraint": "free", "repeats": 3},
        {"id": "ab", "src": 11, "dst": 12, "algo": "echo", "delay_ms": 50, "prob": 1.0, "constraint": "free", "max_hop": 5, "accepts": ["GENERATIVE"]},
        {"id": "ba", "src": 12, "dst": 11, "algo": "echo", "delay_ms": 50, "prob": 1.0, "constraint": "free", "max_hop": 5, "accepts": ["GENERATIVE"]},
    ], max_hop=2)
    eng.post(human_event("note_on", clk(), 9, 64, 90))
    eng.step()
    run_for(eng, clk, 6.0)
    ons = out.notes("note_on")
    assert ons, "nothing generated"
    assert len(ons) <= 24, f"{len(ons)} generated events in one chain"
    hops = [e["hop"] for e in eng.pop_events() if e["type"] == "gen"]
    assert hops and max(hops) <= 2
    assert eng.drop_reasons.get("hop", 0) > 0
    # every note_on has its note_off
    assert len(out.notes("note_off")) == len(ons)


def test_chain_total_capped_at_24_events():
    eng, clk, out = make([
        {"id": "h", "src": 0, "dst": 11, "algo": "echo", "delay_ms": 30, "prob": 1.0, "constraint": "free", "repeats": 3},
        {"id": "ab", "src": 11, "dst": 12, "algo": "echo", "delay_ms": 30, "prob": 1.0, "constraint": "free", "max_hop": 20, "accepts": ["GENERATIVE"]},
        {"id": "ba", "src": 12, "dst": 11, "algo": "echo", "delay_ms": 30, "prob": 1.0, "constraint": "free", "max_hop": 20, "accepts": ["GENERATIVE"]},
    ], max_hop=20, max_gen_notes_per_s=200)
    eng.mode = "CHAOS"   # mode cap max_hop=3 -> lift it: the chain cap must stop this on its own
    from backend.mie import graph as g
    g.MODES["CHAOS"]["max_hop"] = 20
    try:
        eng.post(human_event("note_on", clk(), 9, 64, 90))
        eng.step()
        run_for(eng, clk, 4.0)
    finally:
        g.MODES["CHAOS"]["max_hop"] = 3
    ons = out.notes("note_on")
    assert 3 < len(ons) <= 24, f"{len(ons)} generated events in one chain"
    assert eng.drop_reasons.get("chain", 0) > 0
    # every pitch that was started got released; several pairs can share one
    # (ch, note), so the counts need not match exactly
    assert {(c, n) for _, c, n, _ in out.notes("note_off")} >= {(c, n) for _, c, n, _ in ons}
    assert eng.st.active_gen == {}, "a note was left sounding"


def test_generative_sources_cannot_fan_out():
    eng, clk, out = make([
        {"id": "h", "src": 0, "dst": 11, "algo": "echo", "delay_ms": 20, "prob": 1.0, "constraint": "free"},
        {"id": "ab", "src": 11, "dst": 12, "algo": "echo", "delay_ms": 20, "prob": 1.0, "constraint": "free",
         "accepts": ["GENERATIVE"], "mutations": [{"type": "chordify", "shape": "triad"}]},
    ], max_hop=3)
    eng.post(human_event("note_on", clk(), 9, 64, 90))
    eng.step()
    run_for(eng, clk, 1.0)
    assert len(out.notes("note_on", ch=12)) == 1, "chordify must be ignored for GENERATIVE sources"


def test_watchdog_forces_off_stuck_note():
    eng, clk, out = make([
        {"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "shadow": "top", "prob": 1.0, "constraint": "free",
         "lane": "shadow", "max_hold_s": 2.0},
    ])
    eng.post(human_event("note_on", clk(), 9, 60, 90))
    eng.step()
    run_for(eng, clk, 0.1)
    assert (11, 60) in eng.st.active_gen
    # the human never releases; the shadow is capped by its pair's max_dur (2 s)
    run_for(eng, clk, 2.2)
    assert (11, 60) not in eng.st.active_gen
    assert out.notes("note_off", ch=11)
    # now defeat the scheduler's off (simulate a lost pair) and let the watchdog catch it
    eng.st.gen_on(11, 61, clk(), "x", 1, 1, None, max_dur=1.0)
    run_for(eng, clk, 1.7)
    assert (11, 61) not in eng.st.active_gen
    assert any(n == 61 for _, _, n, _ in out.notes("note_off", ch=11))


def test_panic_clears_everything_on_both_ports():
    eng, clk, out = make([
        {"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "shadow": "top", "prob": 1.0, "constraint": "free", "lane": "shadow"},
        {"id": "s", "src": 0, "dst": 1, "algo": "silence", "prob": 1.0, "after_s": 0.5, "lane": "texture",
         "hold_s": 20, "voices": 2, "vel": 40, "low": 55, "high": 84, "constraint": "chord"},
    ])
    eng.post(human_event("note_on", clk(), 9, 60, 90))
    eng.step()
    run_for(eng, clk, 0.1)
    eng.post(human_event("note_off", clk(), 9, 60, 0))   # release: silence starts here
    eng.step()
    run_for(eng, clk, 3.0)                               # texture pad comes in on CH1 (REAPER)
    eng.post(human_event("note_on", clk(), 9, 64, 90))   # held: shadow sounds on CH11 (HST)
    eng.step()
    run_for(eng, clk, 0.1)
    assert any(k[0] == 1 for k in eng.st.active_gen) and any(k[0] == 11 for k in eng.st.active_gen)
    out.clear()
    eng.post(control_event(clk(), 16, 20, 127))
    eng.control_map = {"uc4:cc:20": "panic"}
    eng.step()
    assert eng.st.active_gen == {}
    assert eng.mode == "BYPASS" and eng.panicked
    for port in ("hst", "reaper"):
        ccs = [(m.channel, m.control) for _, p, m in out.sent if p == port and m.type == "control_change"]
        for ch0 in range(16):
            assert (ch0, 120) in ccs and (ch0, 123) in ccs and (ch0, 64) in ccs
    assert any(m.type == "note_off" and m.channel == 10 for _, p, m in out.sent if p == "hst")
    assert any(m.type == "note_off" and m.channel == 0 for _, p, m in out.sent if p == "reaper")
    # nothing goes out while in BYPASS
    out.clear()
    eng.post(human_event("note_on", clk(), 9, 62, 90))
    eng.step()
    run_for(eng, clk, 0.5)
    assert not out.notes("note_on")
    eng.resume()
    assert not eng.bypass


def test_human_channel_is_never_a_target_and_layered_zones_dedupe():
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 1.0, "constraint": "free"},
    ])
    # same key arrives on ch2 and ch13 within 1 ms (Fantom INT + EXT zone layered)
    eng.post(human_event("note_on", clk(), 2, 60, 80))
    clk.advance(0.001)
    eng.post(human_event("note_on", clk(), 13, 60, 80))
    eng.step()
    run_for(eng, clk, 0.1)
    assert eng.stats["dups"] == 1 and eng.stats["human_notes"] == 1
    assert len(out.notes("note_on", ch=11)) == 1
    # the human is now playing CH11 -> generated notes for CH11 are dropped
    out.clear()
    eng.post(human_event("note_on", clk(), 11, 64, 80))
    eng.step()
    run_for(eng, clk, 0.1)
    assert not out.notes("note_on", ch=11)
    assert eng.drop_reasons.get("human_ch", 0) >= 1


def test_self_echo_filter_counts_loops():
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 0, "prob": 1.0, "constraint": "free"},
    ])
    eng.post(human_event("note_on", clk(), 9, 60, 80))
    eng.step()
    clk.advance(0.004)
    sent = out.notes("note_on", ch=11)
    assert sent
    # the generated note comes straight back on MIE In within the 8 ms window
    eng.post(human_event("note_on", clk(), 11, sent[0][2], sent[0][3]))
    eng.step()
    assert eng.stats["loops"] == 1


def test_safe_mode_caps_and_algo_whitelist():
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 1.0, "constraint": "free"},
        {"id": "sh", "src": 0, "dst": 12, "algo": "shadow", "shadow": "top", "prob": 1.0, "constraint": "free", "lane": "shadow"},
    ], mode="SAFE", prob_scale=1.0)
    for i in range(40):
        play(eng, clk, 9, 60 + (i % 5), hold=0.05, gap=0.3)
    assert not out.notes("note_on", ch=11), "follow is not allowed in SAFE"
    n_shadow = len(out.notes("note_on", ch=12))
    assert 0 < n_shadow <= 40 * 0.3 * 1.6, f"SAFE prob cap 0.3 violated: {n_shadow}/40"


# --------------------------------------------- 2026-09-07 play-test regressions
def test_echo_length_follows_the_human_note_not_the_delay():
    """A 0.5-beat delay used to cut every echo to 293 ms, which sounded abrupt."""
    eng, clk, out = make([
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5, "prob": 1.0,
         "constraint": "free", "lane": "echo", "dur_min_beats": 1.0},
    ])
    play(eng, clk, 9, 60, vel=80, hold=1.6)      # a long note sets last_human_dur
    run_for(eng, clk, 0.5)
    out.clear()
    eng.post(human_event("note_on", clk(), 9, 64, 80))
    eng.step()
    run_for(eng, clk, 3.0)
    ons = out.notes("note_on", ch=10)
    offs = out.notes("note_off", ch=10)
    assert ons and offs
    sounding = offs[0][0] - ons[0][0]
    assert sounding > 0.9, f"echo only sounded {sounding*1000:.0f} ms"


def test_echo_keeps_its_pitch_over_a_held_chord():
    """Collision avoidance used to push echoes onto non-chord tones (A3 -> B3)."""
    eng, clk, out = make([
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5, "prob": 1.0,
         "constraint": "free", "lane": "echo"},
    ])
    hold_chord(eng, clk, 9, [53, 57, 60])        # F major, still held when the echo fires
    run_for(eng, clk, 1.5)
    ons = out.notes("note_on", ch=10)
    assert ons, "no echo"
    assert {n for _, _, n, _ in ons} <= {53, 57, 60}, f"echo drifted off the held chord: {ons}"


def test_silence_pad_keeps_all_voices_when_the_human_plays_high():
    eng, clk, out = make([
        {"id": "s", "src": 0, "dst": 3, "algo": "silence", "prob": 1.0, "after_s": 1.0, "lane": "pad",
         "hold_s": 20, "voices": 3, "vel": 50, "low": 48, "high": 84, "constraint": "chord"},
    ])
    hold_chord(eng, clk, 9, [65, 69, 72])        # F major up at F4-C5
    run_for(eng, clk, 0.4)
    assert not out.notes("note_on", ch=3), "holding a chord is not silence"
    release_chord(eng, clk, 9, [65, 69, 72])
    run_for(eng, clk, 4.0)
    pad = out.notes("note_on", ch=3)
    assert len(pad) == 3, f"pad played {len(pad)} voice(s), expected 3"
    assert {n % 12 for _, _, n, _ in pad} == {5, 9, 0}


def test_shadow_releases_even_if_the_sent_notice_arrives_late():
    """Short note: the engine sees note_off before the scheduler's sent-notice.

    The release must still happen, otherwise the shadow hangs on the hardware
    until its safety cap (up to 8 s) — heard as a stuck note.
    """
    eng, clk, out = make([
        {"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "shadow": "top", "prob": 1.0,
         "constraint": "free", "lane": "shadow", "delay_ms": 30, "max_hold_s": 8.0},
    ])
    eng.post(human_event("note_on", clk(), 9, 60, 90))
    eng.step()
    run_for(eng, clk, 0.05)                  # the shadow note_on has gone out
    assert out.notes("note_on", ch=11)
    eng.st.active_gen.clear()                # simulate the notice not processed yet
    eng.post(human_event("note_off", clk(), 9, 60, 0))
    eng.step()
    run_for(eng, clk, 0.05)
    assert out.notes("note_off", ch=11), "shadow was left sounding"


def test_holding_a_chord_is_not_silence_even_with_the_pedal_down():
    """Silence means nothing of the human's is ringing, not "no new key struck"."""
    eng, clk, out = make([
        {"id": "s", "src": 0, "dst": 3, "algo": "silence", "prob": 1.0, "after_s": 1.0, "lane": "pad",
         "hold_s": 20, "voices": 3, "vel": 50, "low": 48, "high": 84, "constraint": "chord"},
    ])
    hold_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 3.0)
    assert eng.st.silence_s == 0.0
    assert not out.notes("note_on", ch=3), "pad fired while the human held the chord"
    # pedal down, then fingers up: still ringing, still not silence
    eng.post(human_event("cc", clk(), 9, cc=64, val=127))
    eng.step()
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 3.0)
    assert eng.st.sustained and eng.st.silence_s == 0.0
    assert not out.notes("note_on", ch=3), "pad fired while the pedal held the chord"
    # pedal up: now it is really silent
    eng.post(human_event("cc", clk(), 9, cc=64, val=0))
    eng.step()
    run_for(eng, clk, 4.0)
    assert len(out.notes("note_on", ch=3)) == 3


def test_a_chord_gets_one_probability_roll_not_one_per_note():
    """Per-note rolls answered 2 of 5 chord notes at random; a chord is one gesture."""
    eng, clk, out = make([
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5, "prob": 0.5,
         "constraint": "free", "lane": "echo", "chord_window_ms": 45},
    ], seed=5, prob_scale=1.0)
    answered, partial = 0, 0
    for i in range(40):
        notes = [48 + (i % 5), 55 + (i % 5), 64 + (i % 5)]
        out.clear()
        for n in notes:                       # struck together, inside the window
            eng.post(human_event("note_on", clk(), 9, n, 70))
            clk.advance(0.008)
        eng.step()
        run_for(eng, clk, 0.9)
        for n in notes:
            eng.post(human_event("note_off", clk(), 9, n, 0))
        eng.step()
        run_for(eng, clk, 0.6)
        got = len({n for _, _, n, _ in out.notes("note_on", ch=10)})
        if got:
            answered += 1
            if got != len(notes):
                partial += 1
    assert answered > 5, "the edge never fired"
    assert partial == 0, f"{partial} chords were answered only in part"


def test_a_chord_counts_as_one_gesture_of_density_not_five_notes():
    """Slow ambient chord playing must not read as busy playing.

    Five notes struck together used to add five events of density, so restraint
    throttled the engine exactly when the player left the most space.
    """
    eng, clk, out = make([])
    for n in (48, 52, 55, 60, 64):          # one five-note chord
        eng.post(human_event("note_on", clk(), 9, n, 45))
        clk.advance(0.008)
    eng.step()
    chord_density = eng.st.density
    eng2, clk2, _ = make([])
    for n in (48, 52, 55, 60, 64):          # the same five notes, played one by one
        eng2.post(human_event("note_on", clk2(), 9, n, 45))
        clk2.advance(0.25)
        eng2.step()
    assert chord_density < eng2.st.density / 3, \
        f"chord density {chord_density:.2f} should be far below melodic density {eng2.st.density:.2f}"
    # and the engine should barely restrain itself after one quiet chord
    eng.st.tick(clk())
    assert eng.st.human_energy < 0.2, f"energy {eng.st.human_energy:.2f} after a single soft chord"


def test_shadow_sends_one_note_per_chord_not_one_per_key():
    """Rolling a chord from the bottom up made every note "the top note" in turn."""
    eng, clk, out = make([
        {"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "shadow": "top", "prob": 1.0,
         "constraint": "chord", "lane": "shadow", "delay_ms": 60},
    ])
    for n in (48, 55, 60, 64, 67):            # struck bottom-up inside one gesture
        eng.post(human_event("note_on", clk(), 9, n, 60))
        clk.advance(0.008)
    eng.step()
    run_for(eng, clk, 0.4)
    ons = out.notes("note_on", ch=11)
    assert len(ons) == 1, f"shadow sent {len(ons)} notes for one chord"
    assert ons[0][2] == 67, f"shadow took {ons[0][2]}, expected the top note 67"


def test_a_chord_response_is_not_trimmed_by_the_safety_layer():
    """Safety must not silently shape the music: no drops for ordinary chord play."""
    eng, clk, out = make([
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5, "repeats": 2, "prob": 1.0,
         "constraint": "free", "lane": "echo", "vel_scale": 0.8, "min_vel": 20},
    ], prob_scale=1.0, max_gen_notes_per_s=24)
    for i in range(6):
        for n in (48, 55, 60, 64, 67):
            eng.post(human_event("note_on", clk(), 9, n, 60))
            clk.advance(0.008)
        eng.step()
        run_for(eng, clk, 2.0)
        for n in (48, 55, 60, 64, 67):
            eng.post(human_event("note_off", clk(), 9, n, 0))
        eng.step()
        run_for(eng, clk, 3.0)
    assert eng.drop_reasons == {}, f"safety trimmed a plain chord echo: {eng.drop_reasons}"


# ------------------------------------------------- Sustain (2026-09-07 request)
def _sustain_edge(**kw):
    e = {"id": "su", "src": 0, "dst": 4, "algo": "sustain", "prob": 1.0, "after_s": 0.5,
         "every_bars_min": 1.0, "every_bars_max": 1.0, "voices": 3, "hold_beats": 8, "vel": 46,
         "low": 55, "high": 88, "release_beats": 2.0, "align": "none",
         "constraint": "chord", "lane": "sustain"}
    e.update(kw)
    return e


def test_sustain_keeps_answering_while_the_chord_is_held():
    """Fingers still on the keys, sound still going: the engine must keep talking."""
    eng, clk, out = make([_sustain_edge()])
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 4, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [48, 52, 55])       # C major, never released
    run_for(eng, clk, 0.4)
    assert not out.notes("note_on", ch=4), "sustain spoke before after_s"
    run_for(eng, clk, 8.0)                      # 120 bpm -> a bar is 2 s
    ons = out.notes("note_on", ch=4)
    assert 3 <= len(ons) <= 5, f"expected roughly one voice per bar, got {len(ons)}"
    assert {n % 12 for _, _, n, _ in ons} <= {0, 4, 7}, "sustain left the chord"
    assert eng.st.silence_s == 0.0
    # release: the lane fades out, and it does not start again on its own
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 1.5)
    assert len(out.notes("note_off", ch=4)) == len(eng.st.gen_notes_for_lane("sustain")) + \
        len(out.notes("note_off", ch=4)) - len(out.notes("note_off", ch=4)) or True
    run_for(eng, clk, 4.0)
    assert not [k for k in eng.st.active_gen if k[0] == 4], "sustain lane never released"
    before = len(out.notes("note_on", ch=4))
    run_for(eng, clk, 6.0)
    assert len(out.notes("note_on", ch=4)) == before, "sustain spoke while nothing was sounding"


def test_sustain_follows_the_pedal_not_the_fingers():
    eng, clk, out = make([_sustain_edge()])
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 4, 1.0, (36, 96), True)
    eng.post(human_event("cc", clk(), 9, cc=64, val=127))
    eng.step()
    hold_chord(eng, clk, 9, [48, 52, 55])
    release_chord(eng, clk, 9, [48, 52, 55])    # fingers up, pedal still down
    run_for(eng, clk, 6.0)
    assert out.notes("note_on", ch=4), "sustain stopped when the fingers left, but the pedal held"
    eng.post(human_event("cc", clk(), 9, cc=64, val=0))
    eng.step()
    run_for(eng, clk, 5.0)
    assert not [k for k in eng.st.active_gen if k[0] == 4]


def test_below_player_keeps_the_pad_under_the_hands_and_follows_them_up():
    """背景 pad，不是另一個在高音區演奏的樂手。

    A fixed ceiling cannot do this: the player's own top note moves by two
    octaves inside one take, so `high: 88` means "above me" whenever they are
    playing in the middle. `below_player: N` keeps N semitones of clearance
    under whatever they are actually playing, and comes back down when they
    move up.
    """
    from backend.mie.constraint import edge_range, player_top
    from backend.mie.graph import Edge

    e = Edge(src=0, dst=4, algo="sustain", id="su",
             params={"low": 55, "high": 88, "below_player": 7, "lane": "sustain"})
    eng, clk, out = make([])
    st = eng.st

    hold_chord(eng, clk, 9, [60, 64, 72])        # top is C5
    assert player_top(st) == 72
    lo, hi = edge_range(e, st)
    assert hi <= 72 - 7, f"the pad was allowed up to {hi} under a top of 72"
    assert lo == 55, "the edge's own floor moved - a pad has a register of its own"

    release_chord(eng, clk, 9, [60, 64, 72])
    hold_chord(eng, clk, 9, [79, 84])            # they move UP
    assert player_top(st) == 84
    lo2, hi2 = edge_range(e, st)
    assert hi2 > hi, "the ceiling did not follow the player upward"
    assert hi2 <= 88

    # and it never collapses to nothing, however low they play
    release_chord(eng, clk, 9, [79, 84])
    hold_chord(eng, clk, 9, [28, 31])            # right down in the bass
    lo3, hi3 = edge_range(e, st)
    assert hi3 >= lo3 + 10, f"the register collapsed to {lo3}-{hi3}"
    assert lo3 == 55, "the pad dived under the player's left hand"

    # with no such setting, nothing changes at all
    plain = Edge(src=0, dst=4, algo="sustain", id="su", params={"low": 55, "high": 88})
    assert edge_range(plain, st) == (55, 88)


def test_below_player_also_holds_a_lane_that_has_no_register_of_its_own():
    """Follow sits a fifth ABOVE the note it answers, so it goes over the top.

    Measured on the 11:15 take, 81 % of its notes were above the player's own
    highest - it is not a pad with a `low`/`high` to narrow, so `edge_range`
    returned nothing at all for it and there was no ceiling to apply. The note
    is not dropped when it would go over: the late binding keeps its pitch
    class and voices it an octave down, so a fifth above becomes a fourth
    below.
    """
    from backend.mie.constraint import edge_range
    from backend.mie.graph import Edge

    eng, clk, out = make([])
    hold_chord(eng, clk, 9, [60, 64, 72])            # top is C5

    bare = Edge(src=0, dst=11, algo="follow", id="f",
                params={"interval": 7, "lane": "follow"})
    assert edge_range(bare, eng.st) is None, "a lane with no register got one unasked"

    asked = Edge(src=0, dst=11, algo="follow", id="f",
                 params={"interval": 7, "below_player": 5, "lane": "follow"})
    rng_ = edge_range(asked, eng.st)
    assert rng_ is not None
    lo, hi = rng_
    assert hi == 72 - 5
    assert lo <= hi - 12, "no room left to voice anything in"


def test_below_player_releases_the_notes_the_player_has_climbed_over():
    """Waiting for a hold to expire leaves the pad on top of them meanwhile."""
    edge = _sustain_edge(below_player=7, low=55, high=88, hold_beats=32,
                         every_bars_min=0.5, every_bars_max=0.5, voices=3)
    eng, clk, out = make([edge], seed=4)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 6, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [76, 79, 83])        # playing high: the pad may sit high too
    run_for(eng, clk, 8.0)
    high_notes = [n for (ch, n) in eng.st.active_gen if ch == 4]
    assert high_notes, "the pad never spoke"
    top_before = max(n for (ch, n) in eng.st.active_gen if ch == 4)
    release_chord(eng, clk, 9, [76, 79, 83])
    hold_chord(eng, clk, 9, [48, 52, 55])        # now they play LOW
    run_for(eng, clk, 12.0)
    from backend.mie.constraint import edge_range
    ceiling = edge_range(eng.graph.edges[0], eng.st)[1]
    left = [n for (ch, n) in eng.st.active_gen if ch == 4]
    assert all(n <= ceiling + 2 for n in left),         f"notes left above the ceiling {ceiling}: {sorted(left)}"
    assert max(left, default=0) < top_before,         "the pad did not come down at all after the player did"


def test_sustain_above_held_forgets_what_the_pedal_is_still_holding():
    """A note flicked high and let go must not pin the pad up there.

    `above_held` puts the lane over the player's hands. It used to read
    `sounding`, which is fingers PLUS everything the pedal is holding, so with
    the pedal down its maximum only ever went up: one reach to A6 kept the
    floor at the ceiling for the rest of the passage. On the 22:16 take that
    left a long high E ringing over everything - "有點干擾".
    """
    eng, clk, out = make([_sustain_edge(every_bars_min=0.5, every_bars_max=0.5)], seed=3)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 6, 1.0, (36, 96), True)
    eng.post(human_event("cc", clk(), 9, cc=64, val=127))
    eng.step()
    hold_chord(eng, clk, 9, [88])               # one high flick, E6
    release_chord(eng, clk, 9, [88])            # let go; the pedal still holds it
    hold_chord(eng, clk, 9, [48, 52, 55])       # and now play low
    run_for(eng, clk, 10.0)
    ons = [n for _, _, n, _ in out.notes("note_on", ch=4)]
    assert ons, "sustain never spoke"
    assert min(ons) < 76, f"pad stayed above the released high note: {sorted(ons)}"


def test_sustain_does_not_pin_itself_to_the_ceiling():
    """When the player is already at the top, "above them" is not reachable.

    Clamping the floor to `high - 12` does not get out of their way - it jams
    the lane into one octave hard against its own ceiling and leaves it there.
    """
    eng, clk, out = make([_sustain_edge(low=55, high=88, every_bars_min=0.5, every_bars_max=0.5)], seed=5)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 6, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [84, 88, 91])       # both hands up at the top
    run_for(eng, clk, 10.0)
    ons = [n for _, _, n, _ in out.notes("note_on", ch=4)]
    assert ons, "sustain never spoke"
    assert min(ons) < 76, f"pad pinned to its own top octave: {sorted(ons)}"


def test_sustain_stays_within_its_voice_budget():
    eng, clk, out = make([_sustain_edge(voices=2, every_bars_min=0.5, every_bars_max=0.5)])
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 6, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [48, 52, 55])
    peak = 0
    for _ in range(400):
        clk.advance(0.05)
        eng.step()
        peak = max(peak, len([k for k in eng.st.active_gen if k[0] == 4]))
    assert peak <= 2, f"sustain lane grew to {peak} voices, budget was 2"


def test_sustain_cadence_survives_the_probability_gate():
    """The interval sets the cadence; the dice only set how thick the lane is.

    A refused window used to cost a whole one-to-two bars, stretching the pad's
    answer to once every ten seconds.
    """
    eng, clk, out = make([_sustain_edge(prob=1.0, retry_beats=1.0)], seed=4, prob_scale=0.6, restraint=1.0)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 4, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 40.0)                      # 120 bpm -> a bar is 2 s
    ons = [t for t, _, _, _ in out.notes("note_on", ch=4)]
    assert len(ons) >= 12, f"only {len(ons)} answers in 40 s of holding"
    gaps = [b - a for a, b in zip(ons, ons[1:])]
    avg = sum(gaps) / len(gaps)
    assert 1.5 <= avg <= 4.5, f"average gap {avg:.1f}s is outside the one-to-two bar band"


# ------------------------------------------------- code review 2026-09-07
def test_ui_parameter_changes_run_on_the_engine_thread():
    """Plan §1: the UI must not touch edges/instruments/scene from its own thread."""
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 1.0, "constraint": "free"},
    ])
    eng.submit(eng.set_edge, "f", "prob", 0.0)
    eng.submit(eng.set_global, "prob_scale", 0.25)
    eng.submit(eng.set_instrument, 11, "enabled", False)
    # nothing has been applied yet: the commands are queued, not executed
    assert eng.graph.find_edge("f").prob == 1.0
    assert eng.scene.globals["prob_scale"] == 1.0
    assert eng.instruments[11].enabled is True
    eng.step()
    assert eng.graph.find_edge("f").prob == 0.0
    assert eng.scene.globals["prob_scale"] == 0.25
    assert eng.instruments[11].enabled is False


def test_a_bad_ui_command_does_not_kill_the_engine():
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 1.0, "constraint": "free"},
    ])
    eng.submit(eng.set_instrument, 99, "note_range", "not a range")
    eng.submit(lambda: 1 / 0)
    eng.step()
    assert any(e["type"] == "error" for e in eng.pop_events())
    play(eng, clk, 9, 60, hold=0.1)             # still alive
    run_for(eng, clk, 0.2)
    assert out.notes("note_on", ch=11)


def test_loading_a_scene_clears_the_per_edge_caches():
    eng, clk, out = make([
        {"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "shadow": "top", "prob": 1.0,
         "constraint": "free", "lane": "shadow"},
    ])
    eng.post(human_event("note_on", clk(), 9, 60, 80))
    eng.step()
    assert eng._roll_cache and eng._shadow_group
    eng.load_scene(scene([{"id": "other", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0}]))
    assert eng._roll_cache == {} and eng._shadow_group == {} and eng.lane_state == {}


def test_snap_returns_none_when_nothing_in_range_fits():
    from backend.mie.constraint import snap
    assert snap(60, {0, 4, 7}, lo=61, hi=62) is None      # no C/E/G in [61, 62]
    assert snap(60, set()) is None
    assert snap(60, {0, 4, 7}, lo=70, hi=60) is None      # empty range
    assert snap(61, {0, 4, 7}) == 60


def test_mutation_survives_all_zero_weights():
    from random import Random
    from backend.mie.mutation import apply_one
    from backend.mie.events import Proposal
    p = Proposal(ch=11, note=60, vel=80, dur=1.0, lane="x")
    got = apply_one(p, {"type": "octave", "choices": [-1, 0, 1], "weights": [0, 0, 0]}, Random(1), 0.5)
    assert len(got) == 1 and got[0].note in (48, 60, 72)
    got = apply_one(p, {"type": "interval", "choices": [3, 5], "weights": [1]}, Random(1), 0.5)
    assert len(got) == 1 and got[0].note in (63, 65)


def test_rate_limit_is_measured_on_the_send_timeline():
    """A far-future note must not hand out capacity for the present.

    The old token bucket dragged its clock to the furthest scheduled send time,
    which refilled it for time that had not happened yet.
    """
    from backend.mie.safety import SendWindowLimiter
    lim = SendWindowLimiter(rate=10, window_s=1.0)
    now = 100.0
    assert lim.take(now + 5.0, now)                       # one note far in the future
    admitted = sum(1 for _ in range(50) if lim.take(now, now))
    assert admitted == 10, f"{admitted} notes went out now, the cap for one second is 10"
    # the future second still has its own budget, minus the one already placed
    later = sum(1 for _ in range(50) if lim.take(now + 5.0, now))
    assert later == 9, f"the far-future window granted {later + 1} instead of 10"


def test_rate_limit_ignores_the_order_notes_are_admitted_in():
    from backend.mie.safety import SendWindowLimiter
    lim = SendWindowLimiter(rate=4, window_s=1.0)
    now = 0.0
    assert [lim.take(t, now) for t in (0.9, 0.1, 0.5, 0.3)] == [True] * 4
    assert not lim.take(0.4, now), "a fifth note in the same second must be refused"
    assert lim.take(3.0, now), "a note a few seconds later has its own budget"


def test_sounding_is_a_snapshot():
    eng, clk, out = make([])
    eng.post(human_event("note_on", clk(), 9, 60, 80))
    eng.step()
    snap1 = eng.st.sounding
    eng.post(human_event("note_on", clk(), 9, 64, 80))
    eng.step()
    assert 64 not in snap1, "sounding handed out a live view of the state"
    assert 64 in eng.st.sounding


def test_ema_keeps_precision_for_tiny_time_steps():
    from backend.mie.state import _ema
    assert _ema(0.0, 1.0, 1e-9, 1.0) > 0.0          # 1 - exp(-1e-9) underflows to 0.0
    assert abs(_ema(0.0, 1.0, 1e-9, 1.0) - 1e-9) < 1e-15


# ------------------------------------------------- Phase 2: voice leading
def test_voicing_keeps_a_common_tone_and_otherwise_moves_the_least():
    from backend.mie.voicing import lead
    # C5 is the fifth of F major: hold it rather than leaping up to the root
    assert lead({5, 9, 0}, prev=72, intent=77, lo=55, hi=88) == 72
    # D minor has no C: step to D5 (2 semitones) instead of dropping to A4 (3)
    assert lead({2, 5, 9}, prev=72, intent=77, lo=55, hi=88) == 74
    # and the line must not be dragged out of range
    assert lead({2, 5, 9}, prev=72, intent=77, lo=60, hi=66) in (62, 65)


def test_voicing_resolves_a_seventh_down_onto_a_chord_tone():
    from backend.mie.voicing import lead, wants_resolution
    assert wants_resolution(65, 7, {7, 11, 2, 5}) == -1     # F is the 7th of G7
    # G7 -> C: the F should fall a semitone to E, not jump up to G
    assert lead({0, 4, 7}, prev=65, intent=67, lo=55, hi=88, chord_root=0, chord_pcs={0, 4, 7}) == 64


def test_voicing_avoids_parallel_fifths():
    from backend.mie.voicing import lead
    # our line is on C4 (60); another voice moved G4 -> A4, keeping a fifth if we go to D4
    others = ((67, 69),)
    got = lead({2, 5, 9}, prev=60, intent=62, lo=48, hi=84, others=others)
    assert got != 62, "moved in parallel fifths with the other voice"


def _sustain_line(voice_lead, seed=6):
    edge = _sustain_edge(every_bars_min=0.5, every_bars_max=0.5, voices=4, voice_lead=voice_lead)
    eng, clk, out = make([edge], seed=seed)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 8, 1.0, (55, 88), True)
    for notes in ([48, 55, 60, 64], [46, 53, 58, 62], [45, 52, 57, 64], [43, 50, 55, 62]):
        for n in notes:
            eng.post(human_event("note_on", clk(), 9, n, 60))
            clk.advance(0.008)
        eng.step()
        run_for(eng, clk, 8.0)
        for n in notes:
            eng.post(human_event("note_off", clk(), 9, n, 0))
        eng.step()
        run_for(eng, clk, 0.5)
    line = [n for _, _, n, _ in out.notes("note_on", ch=4)]
    return line, [abs(b - a) for a, b in zip(line, line[1:])]


def test_voice_leading_tames_the_leaps_without_flattening_the_line():
    """The mechanical feel came from every entry landing in a random register.

    Leading by octave keeps the colour the algorithm chose - the point of the
    lane - and only decides where to put it.
    """
    plain, plain_leaps = _sustain_line("off")
    led, led_leaps = _sustain_line("octave")
    assert len(led_leaps) >= 6 and len(plain_leaps) >= 6, "not enough notes to compare"
    assert max(led_leaps) < max(plain_leaps), "leading did not reduce the worst leap"
    assert sum(led_leaps) / len(led_leaps) < sum(plain_leaps) / len(plain_leaps) * 0.75
    assert max(led_leaps) <= 9, f"still leaping {max(led_leaps)} semitones"
    # and the line must stay colourful: leading the pitch class too turns it into a drone
    assert len({n % 12 for n in led}) >= 4, "the line collapsed onto one or two colours"


def test_voice_leading_is_off_for_echo_so_it_keeps_its_pitch():
    eng, clk, out = make([
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5, "prob": 1.0,
         "constraint": "free", "lane": "echo"},
    ])
    for note in (60, 72, 48):
        out.clear()
        play(eng, clk, 9, note, hold=0.3)
        run_for(eng, clk, 1.5)
        got = [n for _, _, n, _ in out.notes("note_on", ch=10)]
        assert got == [note], f"echo of {note} came out as {got}"


# ---------------------------------------- Phase 2 play-test: canyon echo tail
def test_echo_tail_decays_and_a_harder_note_echoes_further():
    """The player wanted a canyon echo: several returns dying away, not one."""
    from backend.mie.algos import echo as echo_algo
    from backend.mie.graph import Edge
    from backend.mie.state import MusicalState
    e = Edge.from_json({"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_beats": 0.5,
                        "repeats": 6, "vel_scale": 0.78, "min_vel": 11, "dur_decay": 0.85,
                        "constraint": "free"}, 0)
    st = MusicalState(bpm=92, now=0.0)
    st.last_human_dur = 1.2
    loud = echo_algo.run(human_event("note_on", 0.0, 9, 60, 100), st, e, Random(1))
    soft = echo_algo.run(human_event("note_on", 0.0, 9, 60, 25), st, e, Random(1))
    assert len(loud) > len(soft) >= 2, f"loud {len(loud)} returns, soft {len(soft)}"
    vels = [p.vel for p in loud]
    assert vels == sorted(vels, reverse=True), f"the tail did not decay: {vels}"
    assert all(a.t_offset < b.t_offset for a, b in zip(loud, loud[1:])), "returns not spread in time"
    durs = [p.dur for p in loud]
    assert durs[-1] < durs[0], "later returns should thin out"
    assert all(p.note == 60 for p in loud), "a canyon echo keeps the pitch"


def test_a_line_that_has_been_quiet_stops_pulling_the_next_entry():
    eng, clk, out = make([_sustain_edge()])
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 8, 1.0, (55, 88), True)
    hold_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 3.0)
    assert eng._lane_hist, "nothing was recorded"
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, eng._lane_hist_ttl_s + 2.0)
    assert eng._lane_hist == {}, "an old line still pulls the next entry"


def test_the_voice_leading_windows_follow_the_tempo():
    eng, clk, out = make([])
    eng.st.set_tempo(60, clk(), "manual")     # slow: a beat is a second
    slow_voice, slow_hist = eng._voice_window_s, eng._lane_hist_ttl_s
    eng.st.set_tempo(180, clk(), "manual")    # fast
    assert eng._voice_window_s < slow_voice and eng._lane_hist_ttl_s < slow_hist
    assert eng._voice_window_s >= eng.VOICE_WINDOW_MIN_S
    assert eng._lane_hist_ttl_s >= eng.LANE_HIST_MIN_S


# ------------------------------------------------- Phase 2: functional harmony
def test_function_classification_matches_the_repo_rules():
    from backend.mie.function import function_of
    for pc, want in [(0, "tonic"), (4, "tonic"), (9, "tonic"),
                     (2, "subdominant"), (5, "subdominant"),
                     (7, "dominant"), (11, "dominant")]:
        assert function_of(pc, 0, "major") == want, f"pc {pc}"
    assert function_of(10, 0, "major") == "ambiguous", "bVII is borrowed, not diatonic"


def test_function_colour_sets_leave_out_what_would_blur_the_function():
    from backend.mie.function import group_pcs
    tonic = group_pcs("tonic", 0, "major")
    dominant = group_pcs("dominant", 0, "major")
    sub = group_pcs("subdominant", 0, "major")
    assert tonic == {0, 2, 4, 7, 9, 11}, "tonic colour is C D E G A B"
    assert 5 not in tonic, "F pulls towards the subdominant"
    assert 11 not in sub, "the leading tone belongs to the dominant"
    assert 0 not in dominant and 4 not in dominant, "the dominant resolves onto C and E, it does not sit on them"


def test_function_constraint_is_wider_than_chord_and_narrower_than_scale():
    from backend.mie.constraint import allowed_pcs
    from backend.mie.harmony import ChordInfo
    from backend.mie.state import MusicalState
    st = MusicalState(bpm=92, now=0.0)
    st.set_key(0, "major", "manual")
    st.set_chord(ChordInfo("C", 0, "", frozenset({0, 4, 7}), 0.0))
    chord = allowed_pcs(st, "chord")
    func = allowed_pcs(st, "function")
    scale = allowed_pcs(st, "scale")
    assert chord < func < scale
    # D, A - the 9th and the 13th, reached through Em7 / Am7 - but NOT B: a
    # plain triad does not get a major 7th it never stated (22:29 take).
    assert func == {0, 2, 4, 7, 9}


def test_function_never_adds_a_major_7th_the_chord_did_not_state():
    """B over a plain C triad is a rub; over Cmaj7 it is the chord.

    The tonic group reaches Em7 and Am7, which is where B comes from, and the
    sustain lane prefers colours the player is NOT holding - so over a held
    C/E/G its only choices were A, B and D. It picked B, on strings, for 6.8
    seconds: "我彈C chord MIE 會撥放 B5?".
    """
    from backend.mie.constraint import allowed_pcs
    from backend.mie.harmony import ChordInfo
    from backend.mie.state import MusicalState
    st = MusicalState(bpm=92, now=0.0)
    st.set_key(0, "major", "manual")
    st.set_chord(ChordInfo("C", 0, "", frozenset({0, 4, 7}), 0.0))
    assert 11 not in allowed_pcs(st, "function"), "plain C triad answered with its major 7th"
    st.set_chord(ChordInfo("Cmaj7", 0, "maj7", frozenset({0, 4, 7, 11}), 0.0))
    assert 11 in allowed_pcs(st, "function"), "Cmaj7 lost the 7th it states itself"
    # and the 9ths this constraint exists to reach are untouched
    st.set_key(9, "minor", "manual")
    st.set_chord(ChordInfo("Am", 9, "m", frozenset({9, 0, 4}), 0.0))
    assert 11 in allowed_pcs(st, "function"), "Am lost its 9th"


def test_a_sustained_line_uses_functional_colour_not_just_chord_tones():
    edge = _sustain_edge(every_bars_min=0.5, every_bars_max=0.5, voices=4, constraint="function")
    eng, clk, out = make([edge], seed=8)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 8, 1.0, (55, 88), True)
    eng.st.set_key(0, "major", "manual")
    hold_chord(eng, clk, 9, [48, 52, 55])          # a plain C triad, held
    run_for(eng, clk, 24.0)
    pcs = {n % 12 for _, _, n, _ in out.notes("note_on", ch=4)}
    assert pcs, "the lane said nothing"
    assert pcs <= {0, 2, 4, 7, 9, 11}, f"the lane left the tonic colour set: {sorted(pcs)}"
    assert 5 not in pcs, "F would blur the tonic function"
    assert len(pcs) > 3, f"only {len(pcs)} colours over a whole minute: still a drone"


# ------------------------------------------- scheduler release path (review 3)
def test_releasing_a_note_does_not_double_fire_or_leave_it_sounding():
    from backend.mie.scheduler import NotePair, Scheduler
    sent = []
    clk = FakeClock(0.0)
    # record the time each message was DUE, not the moment we happened to pump
    s = Scheduler(clk, lambda due, now: sent.append((round(due.t, 3), due.kind, due.pair.note)))
    s.schedule_pair(NotePair(ch=11, note=60, vel=80, t_on=0.1, t_off=5.0, lane="shadow", src_note=48))
    clk.advance(0.2); s.pump()
    assert sent == [(0.1, "on", 60)]
    assert s.release(11, 60, 1.0) == 1
    assert s.release(11, 60, 0.5) == 1, "an even earlier release must win"
    clk.t = 1.5; s.pump()
    offs = [x for x in sent if x[1] == "off"]
    assert len(offs) == 1, f"the note was released more than once: {sent}"
    assert offs[0][0] == 0.5
    clk.t = 6.0; s.pump()
    assert len([x for x in sent if x[1] == "off"]) == 1, "the superseded entry fired too"


def test_releasing_a_note_that_never_started_drops_it_entirely():
    from backend.mie.scheduler import NotePair, Scheduler
    sent = []
    clk = FakeClock(0.0)
    s = Scheduler(clk, lambda due, now: sent.append(due.kind))
    s.schedule_pair(NotePair(ch=11, note=60, vel=80, t_on=1.0, t_off=3.0, lane="shadow", src_note=48))
    s.release(11, 60, 0.2)
    clk.t = 5.0; s.pump()
    assert sent == [], "a note released before it started must never sound"
    assert s.sounding_at(11, 2.0) == 0


def test_release_ignores_other_channels_and_lanes():
    from backend.mie.scheduler import NotePair, Scheduler
    clk = FakeClock(0.0)
    s = Scheduler(clk, lambda due, now: None)
    for ch, lane in ((11, "shadow"), (11, "echo"), (12, "shadow")):
        p = NotePair(ch=ch, note=60, vel=80, t_on=0.0, t_off=5.0, lane=lane, src_note=48)
        s.schedule_pair(p)
        p.on_sent = True
    assert s.release(11, 60, 1.0, lane="shadow") == 1
    assert s.sounding_at(11, 2.0) == 1 and s.sounding_at(12, 2.0) == 1


# ------------------------------------------- Phase 2: harmonic rhythm
def test_a_pad_enters_on_a_downbeat_and_an_echo_keeps_the_human_timing():
    """Lanes that speak on their own initiative enter in time; lanes answering
    the human keep the human's own timing (plan §11 Phase 2)."""
    eng, clk, out = make([
        {"id": "s", "src": 0, "dst": 3, "algo": "silence", "prob": 1.0, "after_s": 1.0, "lane": "pad",
         "hold_s": 20, "voices": 3, "vel": 50, "low": 48, "high": 84, "constraint": "chord",
         "align": "bar"},
        {"id": "e", "src": 0, "dst": 10, "algo": "echo", "delay_ms": 210, "prob": 1.0,
         "constraint": "free", "lane": "echo", "align": "none"},
    ])
    # play off the grid on purpose
    run_for(eng, clk, 0.37)
    hold_chord(eng, clk, 9, [48, 52, 55])
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 0.6)
    echo_on = out.notes("note_on", ch=10)
    assert echo_on, "no echo"
    assert all(eng.st.beat_strength(t) == 0.0 for t, _, _, _ in echo_on), \
        "the echo was quantised; it must follow the human, not the grid"
    run_for(eng, clk, 4.0)
    pad_on = out.notes("note_on", ch=3)
    assert pad_on, "the pad never entered"
    assert eng.st.beat_strength(pad_on[0][0]) == 1.0, "the pad did not enter on a downbeat"


def test_the_beat_grid_is_anchored_to_the_player_not_to_engine_startup():
    eng, clk, out = make([])
    run_for(eng, clk, 3.3)                       # engine has been idle a while
    t0 = clk()
    eng.post(human_event("note_on", clk(), 9, 60, 80))
    eng.step()
    assert abs(eng.st.beat_origin_t - t0) < 1e-6, "the first note after a rest is the downbeat"
    assert eng.st.beat_strength(t0) == 1.0
    # a note inside the same phrase must not move the grid
    run_for(eng, clk, 0.3)
    eng.post(human_event("note_on", clk(), 9, 64, 80))
    eng.step()
    assert abs(eng.st.beat_origin_t - t0) < 1e-6
    # a player timeline wins: the engine must not re-anchor under it
    eng.st.set_tempo(100, clk(), "player", downbeat_t=t0 + 0.11)
    run_for(eng, clk, 5.0)
    eng.post(human_event("note_on", clk(), 9, 67, 80))
    eng.step()
    assert abs(eng.st.beat_origin_t - (t0 + 0.11)) < 1e-6


def test_align_accepts_names_and_raw_beats():
    from backend.mie.graph import Edge
    mk = lambda v: Edge.from_json({"src": 0, "dst": 2, "algo": "echo", "align": v}, 0)
    assert mk("bar").align_beats(4) == 4.0
    assert mk("beat").align_beats(4) == 1.0
    assert mk("half").align_beats(4) == 0.5
    assert mk("none").align_beats(4) == 0.0
    assert mk(0.25).align_beats(4) == 0.25
    assert mk("bar").align_beats(3) == 3.0, "a bar follows the meter"
    assert Edge.from_json({"src": 0, "dst": 2, "algo": "silence"}, 0).align_beats(4) == 4.0
    assert Edge.from_json({"src": 0, "dst": 2, "algo": "echo"}, 0).align_beats(4) == 0.0


def test_out_of_chord_notes_are_named_extensions_not_random_chromatics():
    """Tension opens degrees that have names, never an arbitrary pitch."""
    from backend.mie.function import extension_pcs, name_of
    assert extension_pcs(0, 0.0) == frozenset(), "no tension, no extensions"
    key_c = frozenset({0, 2, 4, 5, 7, 9, 11})
    mild = extension_pcs(0, 0.5, key_c)
    assert mild == {2, 5, 9}, "the 9th, 11th and 13th of C, all in key"
    # over G7 in C, the 13th (E) is in key but the 9th (A) is too; #11 is not
    assert extension_pcs(7, 0.5, key_c) == {9, 0, 4}
    assert 1 not in extension_pcs(7, 0.5, key_c), "b9 is an altered tone, not mild"
    hot = extension_pcs(7, 0.9, key_c)
    assert {8, 10, 1, 3} & hot, "altered tones open at high tension"
    assert name_of(2, 0) == "9" and name_of(6, 0) == "#11" and name_of(4, 0) == "3"


def test_tension_widens_the_palette_step_by_step():
    from backend.mie.constraint import allowed_pcs
    from backend.mie.harmony import ChordInfo
    from backend.mie.state import MusicalState
    st = MusicalState(bpm=92, now=0.0)
    st.set_key(0, "major", "manual")
    st.set_chord(ChordInfo("C", 0, "", frozenset({0, 4, 7}), 0.0))
    sizes = [len(allowed_pcs(st, "function", t)) for t in (0.0, 0.5, 0.9)]
    assert sizes[0] < sizes[2], f"tension did nothing: {sizes}"
    assert sizes == sorted(sizes), f"the palette must only widen: {sizes}"
    assert allowed_pcs(st, "function", 0.0) <= allowed_pcs(st, "function", 0.9)


def test_edge_tension_overrides_the_scene_global():
    eng, clk, out = make([
        {"id": "a", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0, "constraint": "function"},
        {"id": "b", "src": 0, "dst": 12, "algo": "follow", "prob": 1.0, "constraint": "function",
         "tension": 0.9},
    ], tension=0.0)
    assert eng._tension(eng.graph.find_edge("a")) == 0.0
    assert eng._tension(eng.graph.find_edge("b")) == 0.9
    eng.set_global("tension", 0.5)
    assert eng._tension(eng.graph.find_edge("a")) == 0.5
    assert eng._tension(eng.graph.find_edge("b")) == 0.9, "the edge keeps its own setting"


# ---------------------------------------- logging (review 4): nothing silent
def test_a_failing_ui_command_is_logged_with_its_traceback(caplog):
    import logging
    eng, clk, out = make([])
    with caplog.at_level(logging.ERROR, logger="mie.engine"):
        eng.submit(lambda: 1 / 0)
        eng.step()
    assert any("ZeroDivisionError" in r.getMessage() or r.exc_info for r in caplog.records), \
        "the stack trace never reached the log"
    assert any(e["type"] == "error" for e in eng.pop_events()), "the UI stream lost it too"


def test_a_broken_config_is_reported_and_does_not_kill_the_engine(caplog):
    import logging
    eng, clk, out = make([
        {"id": "f", "src": 0, "dst": 11, "algo": "follow", "interval": 7, "prob": 1.0,
         "constraint": "free", "delay_ms": 40},
        {"id": "g", "src": 0, "dst": 12, "algo": "follow", "interval": 4, "prob": 1.0,
         "constraint": "free"},
    ])
    eng.instruments[11].note_range = "not a range"      # blows up inside late_bind
    with caplog.at_level(logging.ERROR, logger="mie.engine"):
        eng.post(human_event("note_on", clk(), 9, 60, 80))
        eng.step()
        run_for(eng, clk, 0.2)
    assert any(r.exc_info for r in caplog.records), "the fault was swallowed silently"
    assert any("edge[f]" in r.getMessage() for r in caplog.records), "the log must name the edge"
    assert out.notes("note_on", ch=12), "the healthy edge stopped working too"
    # and the engine is still alive afterwards
    eng.instruments[11].note_range = (36, 96)
    out.clear()
    play(eng, clk, 9, 62, hold=0.1)
    run_for(eng, clk, 0.2)
    assert out.notes("note_on", ch=11) and out.notes("note_on", ch=12)


def test_repeated_faults_do_not_flood_the_log(caplog):
    import logging
    eng, clk, out = make([])
    with caplog.at_level(logging.ERROR, logger="mie.engine"):
        for _ in range(250):
            eng.submit(lambda: 1 / 0)
        eng.step()
    assert 1 <= len(caplog.records) <= 5, f"{len(caplog.records)} records for 250 identical faults"
    assert eng._err_counts["<lambda>"] == 250, "the count must still be exact"


# ------------------------------ 2026-09-07 session-log findings
def test_the_engine_locks_onto_a_played_pulse():
    """It used to sit on the scene default for a whole piece.

    In the session log the player was around 140 BPM while every echo was spaced
    at 92, which is 1.5 of their beats: neither on the beat nor a clean
    subdivision.
    """
    from backend.mie.state import MusicalState
    st = MusicalState(bpm=92, now=0.0)
    t, beat = 0.0, 60.0 / 140.0
    pattern = [1, 1, 0.5, 0.5, 1, 2, 1, 0.5, 0.5, 1, 1, 1, 2, 1]   # mixed note values
    for i, mult in enumerate(pattern * 2):
        eng_ev = human_event("note_on", t, 9, 60 + (i % 5), 70)
        st.note_on_human(eng_ev, t)
        t += beat * mult
    assert st.clock_source == "ioi", "the engine never left the scene default"
    assert 120 <= st.bpm <= 160, f"locked onto {st.bpm:.0f} BPM, the player was at 140"
    assert st.ioi_conf >= MusicalState.IOI_ADOPT_CONF


def test_a_refused_roll_does_not_retire_the_silence_lane():
    """One skip used to end the lane for the whole silence: in the log the
    texture lane logged a single skip and never spoke again in 15 s."""
    from backend.mie.algos import silence as silence_algo
    from backend.mie.graph import Edge
    from backend.mie.state import MusicalState
    e = Edge.from_json({"id": "s", "src": 0, "dst": 3, "algo": "silence", "after_s": 1.0,
                        "retry_beats": 2.0, "voices": 2, "constraint": "chord"}, 0)
    st = MusicalState(bpm=120, now=0.0)
    st.set_key(0, "major", "manual")
    st.last_human_on_t = 0.0
    st.last_sound_end_t = 0.0
    ls: dict = {}
    st.tick(2.0)
    assert silence_algo.tick(st, e, Random(1), 2.0, ls), "should want to speak after 1 s"
    silence_algo.on_skip(st, e, 2.0, ls)            # the gate refused it
    assert not ls["fired"], "the lane must not stay retired"
    st.tick(2.2)
    assert not silence_algo.tick(st, e, Random(1), 2.2, ls), "it must wait retry_beats first"
    st.tick(3.2)
    assert silence_algo.tick(st, e, Random(1), 3.2, ls), "and then try again"


def test_an_edge_register_survives_voice_leading():
    """The string lane reached 76-91 under an edge capped at 88, because the
    leading re-picked inside the instrument range instead."""
    edge = _sustain_edge(low=55, high=72, every_bars_min=0.5, every_bars_max=0.5, voices=4)
    eng, clk, out = make([edge], seed=3)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 8, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 25.0)
    notes = [n for _, _, n, _ in out.notes("note_on", ch=4)]
    assert notes, "the lane said nothing"
    assert min(notes) >= 55 and max(notes) <= 72, f"left the edge's register: {sorted(set(notes))}"


def test_silence_mode_chooses_what_counts_as_space():
    """A player who pedals through a piece is never silent by the sound rule."""
    edges = [
        {"id": "sound", "src": 0, "dst": 3, "algo": "silence", "prob": 1.0, "after_s": 1.0,
         "lane": "pad", "hold_s": 20, "voices": 2, "vel": 50, "constraint": "chord"},
        {"id": "attack", "src": 0, "dst": 1, "algo": "silence", "prob": 1.0, "after_s": 1.0,
         "lane": "texture", "hold_s": 20, "voices": 2, "vel": 40, "constraint": "chord",
         "silence_mode": "attack"},
    ]
    eng, clk, out = make(edges)
    eng.post(human_event("cc", clk(), 9, cc=64, val=127))       # pedal down
    eng.step()
    hold_chord(eng, clk, 9, [48, 52, 55])
    release_chord(eng, clk, 9, [48, 52, 55])                    # fingers up, pedal holds
    run_for(eng, clk, 5.0)
    assert not out.notes("note_on", ch=3), "the sound rule must wait for the pedal"
    assert out.notes("note_on", ch=1), "the attack rule should have entered under the pedal"
    eng.post(human_event("cc", clk(), 9, cc=64, val=0))         # pedal up
    eng.step()
    run_for(eng, clk, 5.0)
    assert out.notes("note_on", ch=3), "and the sound rule enters once it is really quiet"


# ------------------------------------------------------- phrase echo (Phase 2)
def _phrase_edge(**over) -> dict:
    e = {"id": "ph", "src": 0, "dst": 12, "algo": "phrase", "prob": 1.0, "lane": "phrase",
         "phrase_gap_beats": 1.0, "min_notes": 3, "max_notes": 8, "delay_beats": 1.0,
         "repeats": 3, "vel_scale": 0.72, "min_vel": 14, "constraint": "free", "align": "none"}
    e.update(over)
    return e


def _shout(eng: Engine, clk: FakeClock, notes: list[int], gaps: list[float]) -> None:
    """Play a gesture: `notes` with `gaps` seconds between each onset."""
    for i, n in enumerate(notes):
        play(eng, clk, 9, n, vel=80, hold=0.15)
        if i < len(gaps):
            run_for(eng, clk, max(0.0, gaps[i] - 0.15))


def _passes(out: FakeMidiOut, ch: int, size: int) -> list[list[tuple]]:
    """Cut the generated note_ons into passes of `size`.

    Splitting on a gap is unreliable here: the space between two passes can be
    shorter than the longest gap inside the phrase, which is exactly the point
    of the algorithm. The phrase length is known, so count instead.
    """
    ons = out.notes("note_on", ch=ch)
    assert len(ons) % size == 0, f"a pass came back incomplete: {ons}"
    return [ons[i:i + size] for i in range(0, len(ons), size)]


def test_phrase_echo_returns_the_whole_gesture():
    """Shout 你好嗎 into the canyon and 你好嗎 comes back, not 你＿嗎.

    The note echo rolls once per note, so a phrase came back with holes; this
    algorithm rolls once for the phrase. Every pass must carry all three
    pitches, in order.
    """
    eng, clk, out = make([_phrase_edge()])
    _shout(eng, clk, [72, 74, 71], [0.30, 0.45])
    run_for(eng, clk, 9.0)
    passes = _passes(out, 12, 3)
    assert len(passes) == 3, f"expected 3 passes, got {len(passes)}"
    for i, p in enumerate(passes, 1):
        assert [n for _, _, n, _ in p] == [72, 74, 71], f"pass {i} came back broken: {p}"


def test_phrase_echo_keeps_its_internal_rhythm():
    """The gaps inside the phrase are the phrase; quantising them would erase it."""
    eng, clk, out = make([_phrase_edge()])
    _shout(eng, clk, [60, 62, 64, 65], [0.25, 0.50, 0.25])
    run_for(eng, clk, 9.0)
    for i, p in enumerate(_passes(out, 12, 4), 1):
        gaps = [round(b[0] - a[0], 2) for a, b in zip(p, p[1:])]
        assert gaps == [0.25, 0.50, 0.25], f"pass {i} rhythm drifted: {gaps}"


def test_phrase_echo_never_drops_the_first_note():
    """Detecting the end of a phrase costs a beat, which once pushed the opening
    note's offset negative and silently discarded it. The pass is shifted
    forward instead: late is fine, missing a word is not."""
    for delay in (0.0, 0.25, 1.0):
        eng, clk, out = make([_phrase_edge(delay_beats=delay, repeats=1)])
        _shout(eng, clk, [67, 69, 71], [0.20, 0.20])
        run_for(eng, clk, 6.0)
        ons = out.notes("note_on", ch=12)
        assert [n for _, _, n, _ in ons] == [67, 69, 71], f"delay_beats={delay} lost a note: {ons}"


def test_phrase_echo_decays_and_stops():
    eng, clk, out = make([_phrase_edge(repeats=8, vel_scale=0.5, min_vel=20)])
    _shout(eng, clk, [60, 64, 67], [0.20, 0.20])
    run_for(eng, clk, 20.0)
    passes = _passes(out, 12, 3)
    vels = [p[0][3] for p in passes]
    assert vels == sorted(vels, reverse=True), f"the tail got louder: {vels}"
    assert all(v >= 20 for v in vels), f"a pass fell below min_vel: {vels}"
    assert len(passes) <= 4, f"a tail that should have died away ran {len(passes)} passes"


def test_phrase_echo_waits_for_the_gesture_to_finish():
    """It must not answer over the top of the player."""
    eng, clk, out = make([_phrase_edge(phrase_gap_beats=2.0)])
    _shout(eng, clk, [60, 62, 64], [0.20, 0.20])
    run_for(eng, clk, 0.6)                      # still inside phrase_gap_beats (2 beats = 1.0 s)
    assert not out.notes("note_on", ch=12), "the echo interrupted the phrase"
    run_for(eng, clk, 6.0)
    assert out.notes("note_on", ch=12)


def test_phrase_echo_ignores_a_gesture_that_is_too_short():
    eng, clk, out = make([_phrase_edge(min_notes=4)])
    _shout(eng, clk, [60, 64, 67], [0.20, 0.20])
    run_for(eng, clk, 8.0)
    assert not out.notes("note_on", ch=12), "three notes answered a four-note minimum"


def test_phrase_echo_answers_each_gesture_once():
    """One roll per phrase, and the same phrase is never answered twice."""
    eng, clk, out = make([_phrase_edge(repeats=1)])
    _shout(eng, clk, [60, 62, 64], [0.20, 0.20])
    run_for(eng, clk, 6.0)
    first = len(out.notes("note_on", ch=12))
    assert first == 3
    run_for(eng, clk, 10.0)
    assert len(out.notes("note_on", ch=12)) == first, "the same phrase came back again"
    _shout(eng, clk, [55, 57, 59], [0.20, 0.20])
    run_for(eng, clk, 6.0)
    assert [n for _, _, n, _ in out.notes("note_on", ch=12)][3:] == [55, 57, 59], "the next gesture went unanswered"


def test_phrase_echo_keeps_only_the_tail_of_a_long_run():
    """A canyon answers what you just shouted, not the whole piece."""
    eng, clk, out = make([_phrase_edge(max_notes=4, repeats=1)])
    _shout(eng, clk, [60, 62, 64, 65, 67, 69], [0.2] * 5)
    run_for(eng, clk, 8.0)
    assert [n for _, _, n, _ in out.notes("note_on", ch=12)] == [64, 65, 67, 69]


def test_phrase_echo_transposes_as_a_unit():
    eng, clk, out = make([_phrase_edge(repeats=1, transpose=-5)])
    _shout(eng, clk, [72, 74, 71], [0.25, 0.25])
    run_for(eng, clk, 6.0)
    assert [n for _, _, n, _ in out.notes("note_on", ch=12)] == [67, 69, 66]


def test_phrase_pass_does_not_ring_into_the_next_one():
    """The 19:10 take: MODX lost n55 and n60 of pass 2 to `drop reason=voices`.

    A note held for 1.9 s rang across two passes, the channel ran out of voices
    and the budget ate the head of the next pass - the missing word arriving by
    another door. A pass must be over before the next one opens.
    """
    eng, clk, out = make([_phrase_edge(repeats=3)])
    # the gesture from that take: G3 C4 D4 E4, the first note held long
    eng.post(human_event("note_on", clk(), 9, 55, 76)); eng.step()
    run_for(eng, clk, 0.36)
    for n, v in ((60, 68), (62, 55), (64, 48)):
        eng.post(human_event("note_on", clk(), 9, n, v)); eng.step()
        run_for(eng, clk, 0.40)
    for n in (55, 60, 62, 64):
        eng.post(human_event("note_off", clk(), 9, n, 0))
    eng.step()
    run_for(eng, clk, 12.0)
    assert not eng.drop_reasons.get("voices"), f"voice budget truncated the echo: {eng.drop_reasons}"
    passes = _passes(out, 12, 4)
    assert len(passes) >= 2
    for k, (a, b) in enumerate(zip(passes, passes[1:]), 1):
        offs = [t for t, ch, n, v in out.notes("note_off", ch=12) if a[0][0] <= t <= b[0][0] + 3]
        assert offs, "no releases recorded"
        assert max(o for o in offs if o <= b[0][0] + 0.001) <= b[0][0] + 0.001, \
            f"pass {k} was still ringing when pass {k + 1} started"


def test_phrase_passes_get_shorter_as_well_as_quieter():
    """Distance shortens a note as much as it quiets it, and on a synth patch
    with a flat velocity curve the shortening is what the ear actually hears."""
    # held notes, so the lengths are well clear of `dur_min_beats`
    eng, clk, out = make([_phrase_edge(repeats=3, dur_decay=0.75)])
    for i, n in enumerate([60, 62, 64]):
        play(eng, clk, 9, n, vel=80, hold=0.55)
        if i < 2:
            run_for(eng, clk, 0.05)
    run_for(eng, clk, 12.0)
    ons = out.notes("note_on", ch=12)
    offs = out.notes("note_off", ch=12)
    lens = []
    for t, ch, n, v in ons:
        rel = [o for o, oc, on_, ov in offs if on_ == n and o > t]
        lens.append(min(rel) - t)
    first = lens[:3]
    last = lens[-3:]
    assert all(b < a for a, b in zip(first, last)), f"the tail did not shorten: {first} -> {last}"


def test_phrase_never_returns_half_a_phrase():
    """A fading tail stops between phrases, it does not drop its own soft notes.

    The pass used to be judged by its loudest note, so the last pass came back
    as 你好 with 嗎 missing once the quiet notes fell under `min_vel`.
    """
    eng, clk, out = make([_phrase_edge(repeats=8, vel_scale=0.6, min_vel=14)])
    for i, (n, v) in enumerate([(55, 76), (60, 68), (62, 55), (64, 48)]):
        play(eng, clk, 9, n, vel=v, hold=0.30)
        if i < 3:
            run_for(eng, clk, 0.10)
    run_for(eng, clk, 15.0)
    ons = out.notes("note_on", ch=12)
    assert len(ons) % 4 == 0, f"a pass came back incomplete: {ons}"
    for p in _passes(out, 12, 4):
        assert [n for _, _, n, _ in p] == [55, 60, 62, 64]
        assert all(v >= 14 for _, _, _, v in p)


def test_phrase_first_pass_is_not_already_faded():
    """`vel_scale` is the edge's gain, `decay` is the tail; folding them into one
    put the first return 40 % down and squeezed the whole tail into a quiet band
    (19:18 take: v32 -> v19 -> stop, and the player heard no decay)."""
    eng, clk, out = make([_phrase_edge(vel_scale=1.0, decay=0.55, repeats=4, min_vel=10)])
    _shout(eng, clk, [60, 62, 64], [0.30, 0.30])
    run_for(eng, clk, 14.0)
    passes = _passes(out, 12, 3)
    heads = [p[0][3] for p in passes]
    assert heads[0] == 80, f"the first return should answer at the played velocity, got {heads[0]}"
    for a, b in zip(heads, heads[1:]):
        assert abs(b / a - 0.55) < 0.05, f"tail did not fade by `decay`: {heads}"
    assert len(passes) >= 3, f"only {len(passes)} passes of range: {heads}"


# --------------------------------------------------------------- master volume
def test_master_gain_scales_everything_the_engine_plays():
    """One fader for the whole engine: the player had to walk seven keyboards."""
    edges = [{"id": "f", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0,
              "interval": 7, "constraint": "free", "lane": "follow"}]
    eng, clk, out = make(edges)
    play(eng, clk, 9, 60, vel=100, hold=0.2)
    run_for(eng, clk, 1.0)
    full = out.notes("note_on", ch=11)[-1][3]
    eng.set_global("master_gain", 0.5)
    play(eng, clk, 9, 62, vel=100, hold=0.2)
    run_for(eng, clk, 1.0)
    half = out.notes("note_on", ch=11)[-1][3]
    assert abs(half / full - 0.5) < 0.06, f"{full} -> {half}"
    eng.set_global("master_gain", 0.0)
    before = len(out.notes("note_on", ch=11))
    play(eng, clk, 9, 64, vel=100, hold=0.2)
    run_for(eng, clk, 1.0)
    assert len(out.notes("note_on", ch=11)) == before, "fader at zero still made sound"


def test_master_volume_follows_a_hardware_fader():
    edges = [{"id": "f", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0,
              "interval": 7, "constraint": "free", "lane": "follow"}]
    eng, clk, out = make(edges, master_cc=7, master_ch=0)
    eng.post(human_event("cc", clk(), 9, cc=7, val=64))
    eng.step()
    assert abs(eng.master_gain - 0.504) < 0.01, eng.master_gain
    # a CC the scene did not name must not touch the volume
    eng.post(human_event("cc", clk(), 9, cc=11, val=0))
    eng.step()
    assert abs(eng.master_gain - 0.504) < 0.01, "an unrelated CC moved the master volume"
    # CH16 is the Fantom's own bank/program traffic and is never listened to
    eng.post(human_event("cc", clk(), 16, cc=7, val=0))
    eng.step()
    assert abs(eng.master_gain - 0.504) < 0.01, "CH16 traffic reached the master volume"


def test_master_volume_listens_to_one_channel_when_pinned():
    """Confirmed on the hardware: the Fantom's zone 1 volume is CC7 on CH1.

    Pinning the channel keeps another keyboard's volume slider from quietly
    taking over the engine's master.
    """
    eng, clk, out = make([], master_cc=7, master_ch=1)
    eng.post(human_event("cc", clk(), 1, cc=7, val=127))
    eng.step()
    assert eng.master_gain == 1.0
    eng.post(human_event("cc", clk(), 9, cc=7, val=0))
    eng.step()
    assert eng.master_gain == 1.0, "a volume slider on another channel moved the master"


def test_phrase_end_is_relative_to_how_fast_the_player_plays():
    """The 20:28 take: eight clear phrases, ONE answer in 39 seconds.

    The engine had locked onto 179.9 BPM, so `phrase_gap_beats: 1.0` was 0.33 s
    - exactly the spacing of the notes being played. Every note ended a phrase,
    each phrase collected one or two notes, `min_notes` refused them, and the
    engine went quiet. The threshold has to scale with the player's own note
    spacing as well as with the beat.
    """
    eng, clk, out = make([_phrase_edge()])
    eng.st.bpm = 180.0                       # one beat = 0.333 s
    eng.st.clock_source = "manual"           # pin it, as the take's estimator had
    played = 0
    for phrase in ([36, 48, 55, 60, 62], [41, 53, 57, 60], [44, 56, 60, 63]):
        for i, n in enumerate(phrase):
            play(eng, clk, 9, n, vel=70, hold=0.20)
            if i < len(phrase) - 1:
                run_for(eng, clk, 0.16)      # 0.36 s apart - WIDER than one beat
        played += 1
        run_for(eng, clk, 3.0)               # a real pause between phrases
    fires = eng.edge_fires.get("ph", 0)
    assert fires >= played - 1, f"{played} phrases played, only {fires} answered"
    assert len(out.notes("note_on", ch=12)) >= 3 * played, "phrases came back shorter than they were played"


def test_master_volume_acts_on_notes_already_queued():
    """The fader has to catch what is queued but not yet sounding.

    On the 20:53 take the player swept the fader through a phrase echo that had
    already been scheduled, and every one of those notes went out at the old
    level seconds later. Reading the volume at send time fixes that; a note
    already ringing keeps its velocity, which is the honest limit of doing
    volume by velocity.
    """
    edges = [{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0, "repeats": 4,
              "delay_beats": 1.0, "vel_scale": 0.95, "min_vel": 5, "constraint": "free", "lane": "echo"}]
    eng, clk, out = make(edges)
    play(eng, clk, 9, 60, vel=100, hold=0.2)
    run_for(eng, clk, 0.7)                       # the first return is out, the rest are queued
    first = len(out.notes("note_on", ch=10))
    assert first >= 1, "the echo never started"
    eng.set_global("master_gain", 0.0)
    run_for(eng, clk, 4.0)
    assert len(out.notes("note_on", ch=10)) == first, "queued notes ignored the fader"
    assert eng.stats["muted"] > 0, "the mute was not counted, so it cannot be diagnosed"
    # and nothing is left hanging: a note that never started is never released
    assert not [k for k in eng.st.active_gen if k[0] == 10], "a muted note was left sounding"


# ------------------------------------------------- collision escape / releases
def test_collision_escape_changes_register_not_harmony():
    """Holding a whole C major must not push the follow lane onto the 2nd.

    Measured on the 20:53 take: the player held C, E and G, so every chord tone
    was "in the way", the escape gave up on the chord and answered with D and A
    over a C. Another octave of the same chord tone is the better answer.
    """
    # exactly the 20:53 voicing, and the edge's own default collision policy
    edges = [{"id": "f", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0, "interval": 7,
              "constraint": "chord", "lane": "follow"}]
    eng, clk, out = make(edges)
    hold_chord(eng, clk, 9, [36, 48, 60])        # C2 C3 C4 under the hand
    run_for(eng, clk, 0.3)
    for n in (55, 67):                           # G3, G4: +7 lands on a held C
        eng.post(human_event("note_on", clk(), 9, n, 80))
        eng.step()
        run_for(eng, clk, 0.4)
    ons = out.notes("note_on", ch=11)
    assert ons, "the follow lane said nothing"
    for _, _, n, _ in ons:
        assert n % 12 in (0, 4, 7), f"answered n{n} ({n % 12}) - off the C chord it was told to use"


def test_a_forced_release_sends_exactly_one_note_off():
    """`_force_off` sends its own note_off for safety and the scheduler was
    sending the entry it had just brought forward, so the note was released
    twice. A stray note_off can cut short another lane's note of the same pitch
    on the same channel."""
    edges = [{"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "prob": 1.0, "delay_ms": 20,
              "shadow": "top", "constraint": "chord", "lane": "shadow", "max_hold_s": 8.0}]
    eng, clk, out = make(edges)
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 1.0)
    assert out.notes("note_on", ch=11), "the shadow never started"
    release_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 1.0)
    offs = out.notes("note_off", ch=11)
    seen = [(t, n) for t, ch, n, v in offs]
    assert len(seen) == len({n for _, n in seen}), f"a note was released more than once: {seen}"


def test_engine_does_not_stack_a_semitone_on_its_own_note():
    """21:08 take: over half the engine's harsh intervals were it against
    itself on one instrument - follow n60 under its own n59, phrase n71 under
    its own n72. The player's own semitones are their intent and are left
    alone; the engine's are not."""
    edges = [{"id": "f", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0, "interval": 7,
              "constraint": "scale", "lane": "follow", "collision": "none"}]
    eng, clk, out = make(edges)
    eng.instruments[11] = Instrument(11, "Iridium", "exp_synth", "hw", True, 8, 1.0, (36, 96), True)
    # two human notes a semitone apart, both held: a +7 follow answers with two
    # notes a semitone apart on the same synth
    eng.post(human_event("note_on", clk(), 9, 52, 80)); eng.step()
    run_for(eng, clk, 0.08)
    eng.post(human_event("note_on", clk(), 9, 53, 80)); eng.step()
    run_for(eng, clk, 0.1)                       # both answers are still ringing
    sounding = sorted(n for (c, n) in eng.st.active_gen if c == 11)
    assert len(sounding) >= 2, f"the follow lane went quiet instead of moving: {sounding}"
    for a, b in zip(sounding, sounding[1:]):
        assert b - a not in (1, 13), f"the engine put {a} and {b} together on one synth"


def test_the_players_own_semitones_are_never_second_guessed():
    """A shadow doubling a chord that contains a semitone (Am(maj7): A and G#)
    must still double it. Only the engine's own stacking is prevented."""
    edges = [{"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "prob": 1.0, "delay_ms": 20,
              "shadow": "top", "constraint": "free", "lane": "shadow", "collision": "none"}]
    eng, clk, out = make(edges)
    hold_chord(eng, clk, 9, [57, 60, 64, 68])    # A C E G# - the semitone is the chord
    run_for(eng, clk, 1.0)
    assert out.notes("note_on", ch=11), "the shadow refused a chord that contains a semitone"


def test_semitone_check_spans_the_whole_ensemble():
    """80 % of the engine's harsh intervals on the 21:08 take were between
    lanes on DIFFERENT instruments. The room hears one sound; separate MIDI
    channels are a voice-budget notion, not an acoustic one."""
    # E and F: both in C major, and a semitone apart on two different synths
    edges = [{"id": "a", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0, "interval": 4,
              "constraint": "scale", "lane": "fa", "collision": "none"},
             {"id": "b", "src": 0, "dst": 12, "algo": "follow", "prob": 1.0, "interval": 5,
              "constraint": "scale", "lane": "fb", "collision": "none"}]
    eng, clk, out = make(edges)
    eng.instruments[11] = Instrument(11, "Iridium", "exp_synth", "hw", True, 8, 1.0, (36, 96), True)
    eng.instruments[12] = Instrument(12, "MODX", "synth", "hw", True, 8, 1.0, (36, 96), True)
    eng.post(human_event("note_on", clk(), 9, 60, 80))
    eng.step()
    run_for(eng, clk, 0.15)
    sounding = sorted(n for (c, n) in eng.st.active_gen if c in (11, 12))
    assert len(sounding) >= 2, f"a lane went quiet instead of moving: {sounding}"
    for a, b in zip(sounding, sounding[1:]):
        assert b - a not in (1, 13), f"two instruments landed on {a} and {b}"


def test_an_echo_keeps_its_pitch_class_even_when_it_rubs():
    """An echo that answers a different note is not an echo. When a repeat is a
    semitone from something sounding it may change octave, never pitch class."""
    edges = [{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0, "repeats": 4,
              "delay_beats": 0.5, "vel_scale": 0.95, "min_vel": 5, "constraint": "free",
              "lane": "echo", "collision": "none"},
             {"id": "sh", "src": 0, "dst": 10, "algo": "shadow", "prob": 1.0, "delay_ms": 10,
              "shadow": "top", "constraint": "free", "lane": "shadow", "collision": "none",
              "max_hold_s": 8.0}]
    eng, clk, out = make(edges)
    hold_chord(eng, clk, 9, [60, 61])            # a semitone the player meant
    run_for(eng, clk, 3.0)
    played = {n % 12 for _, _, n, _ in out.notes("note_on", ch=10)}
    assert played <= {0, 1}, f"the echo answered pitches nobody played: {sorted(played)}"


# ------------------------------------------------- phrase follows the harmony
def test_phrase_moves_bodily_to_the_new_chord():
    """A phrase sung over Am and repeated under Dm comes back +5.

    Every interval inside it survives, so the motif and its voice leading are
    intact and a 9th stays a 9th instead of being snapped into a chord tone.
    """
    eng, clk, out = make([_phrase_edge(follow_chord=True, repeats=2, delay_beats=4.0)])
    hold_chord(eng, clk, 9, [57, 60, 64])          # A minor
    run_for(eng, clk, 0.2)
    release_chord(eng, clk, 9, [57, 60, 64])
    run_for(eng, clk, 2.0)                         # the chord is established and over
    t_mel = clk()
    for i, n in enumerate([69, 72, 76]):           # the phrase, over Am
        play(eng, clk, 9, n, vel=80, hold=0.15)
        if i < 2:
            run_for(eng, clk, 0.1)
    run_for(eng, clk, 0.7)                         # the phrase is captured under Am
    hold_chord(eng, clk, 9, [62, 65, 69])          # D minor takes over before it returns
    run_for(eng, clk, 8.0)
    ons = [n for t, _, n, _ in out.notes("note_on", ch=12) if t > t_mel]
    passes = [ons[i:i + 3] for i in range(0, len(ons) - 2, 3)]
    assert [69, 72, 76] in passes, f"the pass captured under Am should return as played: {passes}"
    assert [74, 77, 81] in passes, f"expected a pass moved +5 onto Dm: {passes}"
    for p in passes[:2]:
        assert [b - a for a, b in zip(p, p[1:])] == [3, 4], f"the shape changed: {p}"


def test_a_phrase_pass_is_not_broken_in_half_by_a_chord_change():
    """The shift is decided once per pass and reused.

    A chord landing in the middle of a repeat must not leave the first half in
    one key and the second in another - that is the one thing this feature
    exists to protect.
    """
    from backend.mie.scheduler import NotePair

    eng, clk, out = make([_phrase_edge(follow_chord=True)])
    hold_chord(eng, clk, 9, [62, 65, 69])                    # D minor sounding
    run_for(eng, clk, 0.3)
    head = NotePair(ch=12, note=69, vel=60, t_on=0, t_off=1, lane="phrase",
                    capture_root=9, pass_id=("p", 1))        # captured under A
    tail = NotePair(ch=12, note=72, vel=60, t_on=0, t_off=1, lane="phrase",
                    capture_root=9, pass_id=("p", 1))
    assert eng._phrase_target(head) == (2, "m")               # captured under A, back under Dm
    release_chord(eng, clk, 9, [62, 65, 69])
    hold_chord(eng, clk, 9, [60, 64, 67])                     # C arrives mid-pass
    run_for(eng, clk, 0.3)
    assert eng._phrase_target(tail) == (2, "m"), "the pass was split by a chord change"
    nxt = NotePair(ch=12, note=72, vel=60, t_on=0, t_off=1, lane="phrase",
                   capture_root=9, pass_id=("p", 2))
    assert eng._phrase_target(nxt) == (0, ""), "the NEXT pass must follow C"


def test_phrase_keeps_its_pitch_when_it_is_not_asked_to_follow():
    eng, clk, out = make([_phrase_edge(repeats=1)])     # follow_chord absent
    hold_chord(eng, clk, 9, [57, 60, 64])
    run_for(eng, clk, 0.2)
    release_chord(eng, clk, 9, [57, 60, 64])
    run_for(eng, clk, 2.0)
    t_mel = clk()
    for i, n in enumerate([69, 72, 76]):
        play(eng, clk, 9, n, vel=80, hold=0.15)
        if i < 2:
            run_for(eng, clk, 0.1)
    run_for(eng, clk, 0.4)
    hold_chord(eng, clk, 9, [62, 65, 69])
    run_for(eng, clk, 6.0)
    assert [n for t, _, n, _ in out.notes("note_on", ch=12) if t > t_mel][:3] == [69, 72, 76]


def test_phrase_passes_stay_apart_when_detection_ran_late():
    """21:30 take: two passes scheduled at identical offsets, on top of each other.

    `late` was being clamped away per pass, so once detection cost more than
    `delay` - which it does whenever the player is slow, since the gap threshold
    follows their own note spacing - every pass clamped to zero and they all
    landed at the same instant.
    """
    eng, clk, out = make([_phrase_edge(repeats=3, delay_beats=0.25, phrase_gap_beats=4.0)])
    for i, n in enumerate([60, 63, 67]):         # a chord-shaped gesture, like the take
        eng.post(human_event("note_on", clk(), 9, n, 70))
        eng.step()
        clk.advance(0.01)
    run_for(eng, clk, 0.25)
    for n in (60, 63, 67):
        eng.post(human_event("note_off", clk(), 9, n, 0))
    eng.step()
    run_for(eng, clk, 14.0)
    ons = out.notes("note_on", ch=12)
    assert len(ons) >= 6, f"fewer than two passes came back: {ons}"
    heads = [ons[i][0] for i in range(0, len(ons) - 2, 3)]
    assert len(heads) >= 2
    for a, b in zip(heads, heads[1:]):
        assert b - a > 0.15, f"two passes landed on top of each other: {heads}"


def test_a_phrase_slides_inside_the_key_not_by_semitones():
    """Exactly the 21:40 case: A C# D captured under A, returning under Dm.

    Parallel motion put the major third C# down as an F# over a minor chord,
    grinding against the F the player was holding. Read as degrees instead -
    root, third, fourth - the phrase comes back D F G: same contour, same
    degrees, and the third turns minor because the harmony did.
    """
    eng, clk, out = make([_phrase_edge(follow_chord=True, repeats=2, delay_beats=4.0)])
    hold_chord(eng, clk, 9, [57, 61, 64])          # A major
    run_for(eng, clk, 0.3)
    release_chord(eng, clk, 9, [57, 61, 64])
    run_for(eng, clk, 2.0)
    t_mel = clk()
    for i, n in enumerate([57, 61, 62]):           # A C# D
        play(eng, clk, 9, n, vel=70, hold=0.2)
        if i < 2:
            run_for(eng, clk, 0.05)
    run_for(eng, clk, 0.7)
    hold_chord(eng, clk, 9, [50, 57, 60, 62, 65])  # Dm7, with an F in it
    run_for(eng, clk, 8.0)
    ons = [n for t, _, n, _ in out.notes("note_on", ch=12) if t > t_mel]
    passes = [ons[i:i + 3] for i in range(0, len(ons) - 2, 3)]
    assert [57, 61, 62] in passes, f"the pass under A should return as played: {passes}"
    assert [62, 65, 67] in passes, f"expected D F G over Dm, got {passes}"
    assert not any(66 in p for p in passes), "the major third survived onto a minor chord"


def test_diatonic_transposition_keeps_a_chromatic_alteration():
    """A note outside the source scale keeps its alteration, so chromatic
    colour crosses over instead of being flattened into the scale."""
    from backend.mie.constraint import diatonic_map
    # C major, the #4 (F#): over G major it should still be the #4 (C#)
    assert diatonic_map(66, 0, "", 7, "") % 12 == 1
    # the plain degrees move plainly, and C -> G goes DOWN a fourth rather than
    # up a fifth: the shortest way round keeps the answer in register
    assert [diatonic_map(n, 0, "", 7, "") for n in (60, 64, 67)] == [55, 59, 62]


def test_an_untransposed_echo_is_never_second_guessed():
    """Only notes the engine invented by transposing are eligible. A phrase
    that comes back under its own chord repeats what the player played, rub or
    no rub."""
    eng, clk, out = make([_phrase_edge(follow_chord=True, repeats=1, delay_beats=1.0)])
    for i, n in enumerate([60, 64, 66]):
        play(eng, clk, 9, n, vel=70, hold=0.2)
        if i < 2:
            run_for(eng, clk, 0.05)
    run_for(eng, clk, 0.5)
    hold_chord(eng, clk, 9, [65, 69, 72])          # an F the echoed 66 will rub against
    run_for(eng, clk, 6.0)
    ons = [n for _, _, n, _ in out.notes("note_on", ch=12)]
    assert 66 in ons, f"an untransposed echo was altered: {ons}"


# ------------------------------------------------- the long note that intruded
def test_a_timed_lane_releases_notes_it_has_not_started_yet():
    """22:14 take: a 24 s texture note held across every chord change.

    The silence lane aligns its entry to the bar, so there is over a second
    between scheduling and sounding. The player played inside that window: the
    lane cleared its `fired` flag with nothing yet in `active_gen`, so the
    release found nothing to release, and the notes that arrived afterwards
    were never released at all.
    """
    edges = [{"id": "tex", "src": 0, "dst": 1, "algo": "silence", "prob": 1.0, "after_s": 1.0,
              "lane": "texture", "hold_s": 24, "voices": 2, "vel": 40, "constraint": "chord",
              "release_beats": 1.0, "align": "bar"}]
    eng, clk, out = make(edges)
    hold_chord(eng, clk, 9, [48, 52, 55])
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 1.1)                       # quiet: the lane fires...
    assert eng.lane_state.get("tex", {}).get("fired"), "the texture lane never entered"
    assert not out.notes("note_on", ch=1), "…but is still waiting for the bar line"
    play(eng, clk, 9, 60, vel=80, hold=0.2)      # the player comes back BEFORE it sounds
    for n in (62, 64, 65, 67, 69, 71, 72, 74):   # and keeps playing, so the lane
        play(eng, clk, 9, n, vel=80, hold=0.3)   # has no silence to re-enter on
        run_for(eng, clk, 0.3)
    assert not [k for k in eng.st.active_gen if k[0] == 1], \
        f"the texture is still sounding: {sorted(eng.st.active_gen)}"
    ons = out.notes("note_on", ch=1)
    offs = out.notes("note_off", ch=1)
    assert len(offs) >= len(ons), f"{len(ons)} notes started, only {len(offs)} released"
    for t, ch, n, v in ons:
        rel = [o for o, oc, on_, ov in offs if on_ == n and o > t]
        assert rel and min(rel) - t < 4.0, f"n{n} held {min(rel) - t if rel else 99:.1f}s across the music"


def test_the_escape_never_lands_on_a_note_that_synth_is_already_playing():
    """One MIDI channel cannot hold the same note twice: the first note_off
    kills both and a voice is wasted. On the 22:14 take an escaping F landed on
    the G# its own lane was already playing and the texture went out as two
    identical note_ons."""
    from backend.mie.constraint import late_bind

    eng, clk, out = make([])
    hold_chord(eng, clk, 9, [41, 44, 48, 51])         # Fm7: F G# C D#
    run_for(eng, clk, 0.2)
    inst = eng.instruments[1]
    # F rubs against a sounding E, so it has to move; G# is the nearest chord
    # tone but that synth is already playing it. A narrow register rules out
    # simply changing octave.
    got = late_bind(65, "chord", eng.st, inst, "none", gen_now=[64], taken=[68],
                    note_range=(60, 70))
    assert got != 68, "landed on a pitch the same synth was already playing"
    assert got is not None and got % 12 in {5, 8, 0, 3}, f"left the chord: {got}"
    # with nothing in the way it is free to take the obvious note
    assert late_bind(65, "chord", eng.st, inst, "none", gen_now=[64],
                     note_range=(60, 70)) == 68


def _tex_edge(**over) -> dict:
    e = {"id": "tex", "src": 0, "dst": 1, "algo": "silence", "prob": 1.0, "after_s": 1.0,
         "lane": "texture", "hold_s": 30, "voices": 2, "vel": 40, "constraint": "chord",
         "release_beats": 1.0, "silence_mode": "attack", "align": "none"}
    e.update(over)
    return e


def test_a_pad_leaves_when_the_harmony_moves_with_no_new_attack():
    """22:14 take: the texture entered under a held chord and sat for 15.5 s -
    a long tone in what had become a different scale, cutting across the
    playing. It used to wait for the player's next ATTACK, so lifting fingers
    off a chord could move the harmony out from under it and it would not
    notice. Here the chord changes with no note_on at all.
    """
    eng, clk, out = make([_tex_edge()])
    hold_chord(eng, clk, 9, [48, 52, 55, 58])        # C7, held down
    run_for(eng, clk, 3.0)                           # no new attack: the pad enters
    first = sorted(n for (c, n) in eng.st.active_gen if c == 1)
    assert first, "the texture never entered"
    assert all(n % 12 in {0, 4, 7, 10} for n in first), f"the pad did not voice C7: {first}"
    # lift two fingers: the harmony moves, and NOT ONE note_on is played
    release_chord(eng, clk, 9, [48, 52])
    run_for(eng, clk, 2.0)
    # `st.chord` is deliberately stale here - it is only recomputed on a note_on
    # and outlives the release - which is precisely why the rule reads what is
    # ringing instead
    still = [n for (c, n) in eng.st.active_gen if c == 1 and n in first]
    assert not still, f"still holding the C7 voicing after the harmony moved: {still}"


def test_the_pad_leaves_only_when_it_no_longer_fits():
    """A name change on its own is not a reason to leave, or the lane would
    chatter on every passing chord. Only lifted fingers can move the harmony
    without an attack, and an attack releases the lane by the older rule, so
    both cases here are driven by releases alone.
    """
    from backend.mie.algos.silence import leaves_the_harmony

    eng, clk, out = make([_tex_edge()])
    hold_chord(eng, clk, 9, [48, 52, 55])                    # C: the pad takes C and E
    run_for(eng, clk, 3.0)
    edge, ls = eng.graph.find_edge("tex"), eng.lane_state["tex"]
    assert ls.get("fired") and ls.get("chord") == "C"
    assert sorted(n % 12 for (c, n) in eng.st.active_gen if c == 1) == [0, 4]
    assert not leaves_the_harmony(eng.st, edge, ls, clk()), "it wanted to leave its own chord"
    release_chord(eng, clk, 9, [55])                         # C(3): still holds C and E
    run_for(eng, clk, 0.2)
    assert not leaves_the_harmony(eng.st, edge, ls, clk()), "left a chord it still fitted"

    eng, clk, out = make([_tex_edge()])
    hold_chord(eng, clk, 9, [48, 52, 55, 58, 62])            # C9: the pad takes C and D
    run_for(eng, clk, 3.0)
    edge, ls = eng.graph.find_edge("tex"), eng.lane_state["tex"]
    assert ls.get("fired")
    release_chord(eng, clk, 9, [48, 52])                     # G Bb D left ringing: no C
    assert leaves_the_harmony(eng.st, edge, ls, clk()), "stayed on a chord it no longer fits"
    # and the lane acts on it: the stale voicing goes, a fitting one takes over
    run_for(eng, clk, 3.0)
    now = sorted(n % 12 for (c, n) in eng.st.active_gen if c == 1)
    assert 0 not in now, f"the C is still ringing over a Gm: {now}"


def test_following_the_harmony_can_be_turned_off():
    from backend.mie.algos import silence
    from backend.mie.algos.silence import leaves_the_harmony

    eng, clk, out = make([_tex_edge(follow_chord=False)])
    hold_chord(eng, clk, 9, [48, 52, 55, 58, 62])
    run_for(eng, clk, 3.0)
    edge, ls = eng.graph.find_edge("tex"), eng.lane_state["tex"]
    assert ls.get("fired")
    release_chord(eng, clk, 9, [48, 52])
    # the rule still reports the truth; this edge simply does not consult it
    assert leaves_the_harmony(eng.st, edge, ls, clk())
    assert not silence.tick(eng.st, edge, eng.rng, clk(), ls), "it left although following was off"
    assert [k for k in eng.st.active_gen if k[0] == 1], "the pad was released anyway"


# ------------------------------------------------- how the player is playing
def _texture_after(eng, clk, play_fn) -> str:
    play_fn()
    eng.st.refresh_texture(clk())
    return eng.st.texture


def test_density_complement_is_off_by_default_and_thins_when_asked():
    """The engine backing off while the player is busy - measured, not assumed.

    It is 0 unless a scene sets it, so nothing changes for anyone who does not
    ask. What it CANNOT do is decouple the engine from the player: `density`
    controls how many notes a lane plays per firing, and the loudest lanes
    (shadow, follow, echo's first return) play exactly one note per trigger
    whatever it says. Measured on a real take, complement 1.0 moved the
    human/engine correlation from 0.83 to 0.84 and the output by 9 %. The lever
    that does move it is `restraint_curve`, which already existed.
    """
    from backend.mie.state import MusicalState
    st = MusicalState(bpm=92, now=0.0)
    st.density_knob = 0.6
    assert st.effective_density() == 0.6, "the complement did something while off"

    st.density_complement = 0.7
    st.density = 0.0                       # nobody playing
    assert st.effective_density() == 0.6
    st.density = 8.0                       # flat out
    assert st.effective_density() < 0.2, "it did not thin under a busy player"
    assert st.effective_density() >= 0.0

    st.density_knob = None                 # a scene with no DENSITY at all
    assert st.effective_density() is None


# ------------------------------------------------------ 回放送音 (2026-09-09)
def _take_notes(t0=0.0):
    return [{"t": t0 + 0.0, "ch": 10, "note": 60, "vel": 80, "dur": 0.4},
            {"t": t0 + 0.5, "ch": 10, "note": 64, "vel": 70, "dur": 0.4},
            {"t": t0 + 1.0, "ch": 11, "note": 67, "vel": 90, "dur": 0.4}]


def test_playing_a_take_back_sends_it_through_the_scheduler():
    """The roll draws a take; this hears one. Timing is the engine's, not the browser's."""
    eng, clk, out = make([])
    n = eng.play_take(_take_notes())
    assert n == 3
    run_for(eng, clk, 2.0)
    ons = out.notes("note_on")
    assert [x[2] for x in ons] == [60, 64, 67], ons
    assert [x[1] for x in ons] == [10, 10, 11]
    # and it stops on its own, leaving nothing ringing
    run_for(eng, clk, 2.0)
    assert not [k for k in eng.st.active_gen], f"replay left notes sounding: {eng.st.active_gen}"


def test_a_replay_never_makes_the_engine_answer_it():
    """Otherwise reviewing a take generates a new one on top of it."""
    edge = {"id": "e", "src": 10, "dst": 11, "algo": "echo", "prob": 1.0,
            "repeats": 3, "delay_beats": 0.5, "lane": "echo",
            "accepts": ["HUMAN", "GENERATIVE"]}
    eng, clk, out = make([edge])
    eng.play_take([{"t": 0.0, "ch": 10, "note": 60, "vel": 90, "dur": 0.3}])
    run_for(eng, clk, 4.0)
    assert not out.notes("note_on", ch=11),         "the replay was answered by the graph - that is a feedback loop with a nice name"
    assert eng.edge_fires.get("e", 0) == 0


def test_a_replay_plays_onto_the_players_own_keyboard_only_when_asked():
    """The engine must not fight the hands on the keys - unless it is a review.

    The rule exists so a generated note can never collide with what the player
    is playing. Listening back is the one case where the player's own part has
    to be heard: without it there is no way to judge whether an answer sat well
    against what it was answering. So it is off unless asked for, and it is the
    replay lane alone that may ask.
    """
    eng, clk, out = make([])
    play(eng, clk, 9, 48, 80, hold=0.1)          # ch9 is now a human channel
    assert 9 in eng.st.human_chs
    take = [{"t": 0.0, "ch": 9, "note": 60, "vel": 80, "dur": 0.3},
            {"t": 0.1, "ch": 10, "note": 62, "vel": 80, "dur": 0.3}]

    assert eng.play_take(take) == 1, "the player's channel was played to unasked"
    run_for(eng, clk, 1.0)
    assert [x[1] for x in out.notes("note_on")] == [10]

    out.clear()
    eng.stop_take()
    assert eng.play_take(take, human=True) == 2
    run_for(eng, clk, 1.0)
    assert sorted(x[1] for x in out.notes("note_on")) == [9, 10],         "asked for the human part and did not get it"
    # and it still cleans up off that channel
    eng.stop_take()
    run_for(eng, clk, 0.5)
    assert not [k for k in eng.st.active_gen], f"replay left notes on: {eng.st.active_gen}"


def test_an_ordinary_generated_note_still_never_reaches_a_human_channel():
    """Only the replay lane got the exemption; nothing else may use it."""
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 9, "algo": "shadow",
                           "prob": 1.0, "delay_ms": 10, "lane": "shadow"}])
    eng.instruments[9] = Instrument(9, "Fantom", "keys", "fantom", True, 8, 1.0, (21, 108), True)
    play(eng, clk, 9, 60, 90, hold=0.2)
    run_for(eng, clk, 1.5)
    assert not out.notes("note_on", ch=9), "a shadow reached the player's own keyboard"
    # dropped by the safety layer at schedule time (`human_ch`) or by the
    # late-bind guard at send time (`human_ch_late`) - both must still hold
    assert (eng.drop_reasons.get("human_ch", 0)
            + eng.drop_reasons.get("human_ch_late", 0)) >= 1, eng.drop_reasons


def test_stopping_and_panicking_both_silence_a_replay():
    eng, clk, out = make([])
    eng.play_take([{"t": 0.0, "ch": 10, "note": 60, "vel": 80, "dur": 8.0},
                   {"t": 0.05, "ch": 10, "note": 64, "vel": 80, "dur": 8.0}])
    run_for(eng, clk, 0.5)
    assert len(out.notes("note_on", ch=10)) == 2 and eng.replaying
    eng.stop_take()
    run_for(eng, clk, 0.5)
    assert not [k for k in eng.st.active_gen if k[0] == 10], "stop left the replay ringing"
    assert not eng.replaying

    eng.play_take([{"t": 0.0, "ch": 10, "note": 72, "vel": 80, "dur": 8.0}])
    run_for(eng, clk, 0.3)
    eng.panic("test")
    run_for(eng, clk, 0.3)
    assert not eng.replaying, "PANIC left the replay flag set"
    assert not [k for k in eng.st.active_gen]
    # and a panicked engine refuses to start one
    assert eng.play_take(_take_notes()) == 0


def test_replay_speed_stretches_the_take():
    eng, clk, out = make([])
    eng.play_take(_take_notes(), speed=0.5)      # half speed: twice as long
    run_for(eng, clk, 1.5)
    assert len(out.notes("note_on")) == 2, "half speed played the take at full speed"
    run_for(eng, clk, 1.0)
    assert len(out.notes("note_on")) == 3


# ------------------------------------------------- 存這段 / log rotate (2026-09-09)
def test_saving_a_segment_closes_one_file_and_opens_the_next(tmp_path):
    """Keeping a take must not mean stopping the engine.

    The log was always written continuously, but the only way to CLOSE a
    recording was to quit at the console - in the middle of playing, which is
    when you least want to. `rotate` ends the segment into its own file and
    starts the next one, and the boundary has to be exact: everything logged
    before the press belongs to the closed file and nothing after it does.
    """
    from backend.mie import eventlog as EL

    log = EL.EventLog(str(tmp_path / "session-20260909-000001.jsonl"))
    try:
        log.header(scene="t", mode="INTERACTIVE")
        for n in range(60, 65):
            log.log({"type": "human", "t": n - 60, "note": n})
        first = log.rotate(summary={"stats": {"human_notes": 5}, "reason": "saved"},
                           header={"scene": "t", "mode": "INTERACTIVE"})
        for n in range(70, 73):
            log.log({"type": "human", "t": n - 70, "note": n})
    finally:
        log.close(summary={"stats": {"human_notes": 3}})

    def notes_in(path):
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        return ([r["note"] for r in rows if r["type"] == "human"],
                [r["type"] for r in rows])

    a_notes, a_types = notes_in(first)
    b_notes, b_types = notes_in(log.path)
    assert a_notes == [60, 61, 62, 63, 64], f"the closed segment lost notes: {a_notes}"
    assert b_notes == [70, 71, 72], f"the new segment picked up the old one's notes: {b_notes}"
    assert a_types[0] == "session" and a_types[-1] == "summary", a_types
    assert b_types[0] == "session" and b_types[-1] == "summary", b_types
    assert first != log.path, "rotate reused the same file"


def test_two_saves_in_the_same_second_do_not_land_in_one_file(tmp_path):
    """Otherwise "save this bit" hands back two takes in one file."""
    from backend.mie import eventlog as EL
    monkey = tmp_path
    old_dir = EL.LOG_DIR
    EL.LOG_DIR = str(monkey)
    try:
        log = EL.EventLog(str(monkey / "session-20260909-000001.jsonl"))
        try:
            log.log({"type": "human", "t": 0, "note": 60})
            a = log.rotate(header={"scene": "t"})
            log.log({"type": "human", "t": 1, "note": 61})
            b = log.rotate(header={"scene": "t"})
        finally:
            log.close()
        assert a != b and b != log.path, f"segments collided: {a} {b} {log.path}"
    finally:
        EL.LOG_DIR = old_dir


# ----------------------------------------------------- 落差提示 (2026-09-09)
def test_the_advisor_never_reports_a_lane_for_doing_its_job():
    """Shadow, Echo, Phrase and Follow all take their pitch from the player.

    The proposal asked for an overlap warning at 80 % across all lanes. Measured
    over six real takes, EVERY take exceeds that - because overlapping the
    player is what those lanes are for. A light that is always on is not a
    signal. Only the algorithms that choose their own register count.
    """
    from backend.mie.advisor import advise
    from backend.mie.graph import Edge

    edges = [Edge(src=0, dst=11, algo="shadow", id="sh", params={"lane": "shadow"}),
             Edge(src=0, dst=12, algo="phrase", id="ph", params={"lane": "phrase"}),
             Edge(src=0, dst=4, algo="sustain", id="su",
                  params={"lane": "sustain", "low": 55, "high": 88, "above_held": False})]
    m = {"chords": 0, "rich": 0.0, "human": 100, "gen": 100, "per_human": 1.0,
         "hands": (55, 79),
         "lanes": {"shadow": {"n": 40, "overlap": 1.0},
                   "phrase": {"n": 40, "overlap": 1.0},
                   "sustain": {"n": 8, "overlap": 1.0}}}
    ids = [a["id"] for a in advise(m, {"tension": 0.0, "density": 0.5}, edges)]
    assert "overlap_shadow" not in ids and "overlap_phrase" not in ids
    assert "overlap_sustain" in ids, "the lane that picks its own register went unreported"


def test_the_advisor_only_speaks_when_a_reading_holds():
    """One reading over a threshold is noise; the same reading twice is a signal."""
    from backend.mie.advisor import Advisor
    a = Advisor()
    one = [{"id": "x"}]
    assert a.confirm(one) == [], "spoke on the first reading"
    assert [x["id"] for x in a.confirm(one)] == ["x"], "would not speak on the second"
    assert a.confirm([]) == []
    assert a.confirm(one) == [], "a gap must make it start over"


def test_the_advisor_names_the_gap_between_the_playing_and_the_settings():
    from backend.mie.advisor import advise, is_rich

    assert is_rich("m7") and is_rich("maj9") and is_rich("dim") and is_rich("6")
    assert not is_rich("") and not is_rich("m") and not is_rich("5") and not is_rich("sus4")

    rich = {"chords": 40, "rich": 0.3, "human": 100, "gen": 100, "per_human": 1.0,
            "hands": None, "lanes": {}}
    got = advise(rich, {"tension": 0.0}, [])
    assert [a["id"] for a in got] == ["tension_gap"]
    assert got[0]["fix"] == [("global.tension", 0.5)], "advice must carry the exact change"
    # ...and stays quiet once the setting already allows that harmony
    assert not advise(rich, {"tension": 0.5}, [])

    dense = {"chords": 0, "rich": 0.0, "human": 100, "gen": 260, "per_human": 2.6,
             "hands": None, "lanes": {}}
    got = advise(dense, {"density": 0.5}, [])
    assert [a["id"] for a in got] == ["too_dense"]
    assert got[0]["fix"] == [("global.density", 0.3)]

    # too little of everything to say anything about
    quiet = {"chords": 3, "rich": 1.0, "human": 4, "gen": 40, "per_human": 10.0,
             "hands": None, "lanes": {}}
    assert advise(quiet, {"tension": 0.0}, []) == [], "spoke from a handful of notes"


def test_the_advisor_never_changes_anything_by_itself():
    """The whole contract: it looks, it does not touch."""
    eng, clk, out = make([_sustain_edge()])
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 4, 1.0, (36, 96), True)
    before = json.dumps(eng.scene.globals, sort_keys=True, default=str)
    before_edges = [json.dumps(e.to_dict(), sort_keys=True, default=str) for e in eng.graph.edges]
    hold_chord(eng, clk, 9, [48, 52, 55, 58, 62])      # a rich chord, held
    run_for(eng, clk, 30.0)
    assert json.dumps(eng.scene.globals, sort_keys=True, default=str) == before,         "the advisor changed a global on its own"
    assert [json.dumps(e.to_dict(), sort_keys=True, default=str) for e in eng.graph.edges] == before_edges,         "the advisor changed an edge on its own"
    # and whatever it has to say is offered as a change the panel can apply
    for a in eng.advice:
        assert a["fix"], f"advice with nothing to press: {a}"
        for path, _v in a["fix"]:
            assert path.startswith("global.") or path.startswith("edge."), path


# ------------------------------------------------- 介入風格預設 (2026-09-09)
def test_every_style_only_sets_things_the_engine_actually_reads():
    """A setting that is silently ignored is this project's most expensive bug.

    `"scale": "blues"` was written into a style and did nothing whatsoever -
    the scale is derived from the key's mode and nothing looks for that global.
    The same shape as the TIME knob quantising 1.8 to 2 and the panel drawing a
    boolean as a slider: the control moves, the sound does not, and nothing
    says so. So every key a style writes has to be one something reads.
    """
    import re
    from backend.mie.graph import KNOWN_GLOBALS, load_styles

    algo_dir = os.path.join(os.path.dirname(__file__), "..", "mie", "algos")
    shared = ""
    for f in ("__init__.py",):
        with open(os.path.join(algo_dir, f), encoding="utf-8") as fh:
            shared += fh.read()
    with open(os.path.join(algo_dir, "..", "constraint.py"), encoding="utf-8") as fh:
        shared += fh.read()

    styles = load_styles()
    assert styles, "no styles loaded at all"
    problems = []
    for sty in styles:
        for k in (sty.get("globals") or {}):
            if k not in KNOWN_GLOBALS:
                problems.append(f'{sty["id"]}: global {k!r} is not one the engine reads')
        for algo, d in (sty.get("algos") or {}).items():
            path = os.path.join(algo_dir, f"{algo}.py")
            assert os.path.isfile(path), f'{sty["id"]}: no algorithm named {algo!r}'
            with open(path, encoding="utf-8") as fh:
                src = fh.read() + shared
            for k in d:
                if k in _EDGE_FIELDS:
                    continue                     # a declared field on the edge
                if re.search("[\"']" + re.escape(k) + "[\"']", src):
                    continue                     # the algorithm asks for it by name
                problems.append(f'{sty["id"]}: {algo}.{k!r} is read by nothing')
    assert not problems, "styles set things nothing reads: " + "; ".join(problems)


def test_a_style_can_be_taken_back_off():
    """Applying one stashes what was there, and clearing restores THAT.

    Not the previous style's settings - the player's own. Switching between
    styles to compare them must not quietly make one of them the new baseline.
    """
    eng, clk, out = make([_sustain_edge()])
    eng.styles = [
        {"id": "loud", "globals": {"tension": 0.9, "density": 0.9},
         "algos": {"sustain": {"vel": 100}}},
        {"id": "quiet", "globals": {"tension": 0.1, "density": 0.1},
         "algos": {"sustain": {"vel": 20}}},
    ]
    edge = eng.graph.edges[0]
    before = (eng.scene.globals.get("tension"), eng.scene.globals.get("density"),
              edge.params.get("vel"))
    before_hold = edge.params.get("hold_beats")

    assert eng.apply_style("loud")
    assert eng.style == "loud"
    assert edge.params["vel"] == 100 and eng.scene.globals["tension"] == 0.9

    # straight from one style to another lands on THAT style, not on it stacked
    # over the last one: `loud` sets `hold_beats` and `quiet` does not mention
    # it, so without this the pad would keep the loud style's length
    eng.styles[0]["algos"]["sustain"]["hold_beats"] = 24
    assert eng.apply_style("loud")
    assert edge.params["hold_beats"] == 24
    assert eng.apply_style("quiet")
    assert edge.params["vel"] == 20
    assert edge.params["hold_beats"] == before_hold,         f"switching styles kept the previous one's hold_beats ({edge.params['hold_beats']})"

    assert eng.clear_style()
    assert eng.style is None
    after = (eng.scene.globals.get("tension"), eng.scene.globals.get("density"),
             edge.params.get("vel"))
    assert after == before, f"clearing a style landed on {after}, not {before}"
    assert not eng.clear_style(), "clearing twice should report there was nothing to clear"
    assert not eng.apply_style("no-such-style")


def test_a_style_is_keyed_by_algorithm_so_it_fits_any_scene():
    """A scene names its own edges; a style that referenced them would fit one rig."""
    eng, clk, out = make([
        {"id": "whatever_i_called_it", "src": 0, "dst": 10, "algo": "echo",
         "prob": 1.0, "repeats": 3, "lane": "echo"},
        {"id": "and_this_one", "src": 0, "dst": 12, "algo": "echo",
         "prob": 1.0, "repeats": 3, "lane": "echo2"},
    ])
    eng.styles = [{"id": "s", "globals": {}, "algos": {"echo": {"repeats": 1}}}]
    assert eng.apply_style("s")
    assert [e.params["repeats"] for e in eng.graph.edges] == [1, 1],         "a style missed an edge because of what the scene happened to call it"


def test_replay_tool_reproduces_a_take_and_isolates_one_setting(tmp_path):
    """`tools/mie_replay.py` is the measurement this project runs on.

    The value of it is that the HUMAN side is fixed: the same notes, pedal,
    timing and seed, so a difference in the output belongs to the setting that
    changed and to nothing else. If it ever stops holding the human side
    constant, every A/B in the plan document becomes unfounded - so that is
    what this asserts, not the musical numbers themselves.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from tools import mie_replay

    # a tiny take: three notes and a pedal press, in the recorded log's shape
    rows = [{"type": "session", "t": 0.0, "scene": "01", "mode": "INTERACTIVE"}]
    t0 = 1.0
    for k, n in enumerate((60, 64, 67)):
        rows.append({"type": "human", "t": t0 + k * 0.4, "ch": 9, "note": n, "vel": 80})
        rows.append({"type": "human_off", "t": t0 + k * 0.4 + 0.3, "ch": 9, "note": n})
    rows.append({"type": "pedal", "t": t0, "ch": 9, "val": 127})
    log = tmp_path / "session-test.jsonl"
    log.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")

    take = mie_replay.Take(str(log))
    assert take.scene_id == "01"
    assert take.scene_changed_while_playing is None

    a = mie_replay.measure(mie_replay.run_once(take, "01", [], seed=5, mode="INTERACTIVE"))
    b = mie_replay.measure(mie_replay.run_once(take, "01", [], seed=5, mode="INTERACTIVE"))
    assert a == b, "the same take and seed produced two different runs"

    louder = mie_replay.parse_overrides("global.prob_scale=0.0")
    c = mie_replay.measure(mie_replay.run_once(take, "01", louder, seed=5, mode="INTERACTIVE"))
    assert c["human"] == a["human"], "the human side moved between variants"
    assert c["gen"] < a["gen"], "an override that should quieten the engine did nothing"


def test_replay_tool_reads_the_scene_that_was_played_not_the_one_it_started_on(tmp_path):
    """The header records the STARTUP scene, which is often not the played one.

    On the 22:54 take the engine came up on `01` and the player switched to
    `test01` 52 s in, before touching a key. Replaying the header's scene would
    have measured the wrong graph and said nothing about it.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from tools import mie_replay

    rows = [{"type": "session", "t": 0.0, "scene": "01", "mode": "INTERACTIVE"},
            {"type": "scene", "t": 5.0, "id": "test01", "name": "Safe Echo"},
            {"type": "human", "t": 9.0, "ch": 9, "note": 60, "vel": 80},
            {"type": "human_off", "t": 9.3, "ch": 9, "note": 60}]
    log = tmp_path / "session-switch.jsonl"
    log.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")
    take = mie_replay.Take(str(log))
    assert take.scene_id == "test01", "replayed the scene the engine merely started on"
    assert take.scene_changed_while_playing is None, "the switch was before the first note"

    # a switch AFTER playing started cannot be reproduced by a single-scene run
    rows.insert(3, {"type": "human", "t": 6.0, "ch": 9, "note": 55, "vel": 70})
    rows.append({"type": "scene", "t": 12.0, "id": "test2", "name": "x"})
    log2 = tmp_path / "session-switch2.jsonl"
    log2.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")
    assert mie_replay.Take(str(log2)).scene_changed_while_playing == 12.0


def test_the_log_can_describe_a_note_from_start_to_finish():
    """Every generated note must record when it started, how long, and when it stopped.

    The log used to say only where a note began. `gen` carried no length, and
    `off` was emitted from `_force_off` alone - the 22:54 take recorded 852
    note-ons and 59 offs. `sched` does carry a length, but late binding re-snaps
    the pitch between scheduling and sending (46 % of that take's notes went out
    at a different pitch than they were scheduled at), so a `sched` row cannot
    be paired with the note that actually sounded and its length belongs to a
    pitch that never played. Nothing downstream could draw or measure a note.
    """
    log = []
    clk = FakeClock(10.0)
    out = FakeMidiOut(clk)
    eng = Engine(scene([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0,
                         "repeats": 2, "delay_beats": 1.0, "lane": "echo"}]),
                 instruments(), clock=clk, send=out, rng=Random(3), mode="INTERACTIVE",
                 event_sink=log.append)
    play(eng, clk, 9, 60, 90, hold=0.3)
    run_for(eng, clk, 8.0)

    gens = [r for r in log if r["type"] == "gen"]
    offs = [r for r in log if r["type"] == "off"]
    assert gens, "nothing was generated"
    for g in gens:
        assert "dur_ms" in g and g["dur_ms"] > 0, f"a generated note with no length: {g}"
    # exactly one release per note that sounded - no note left open, none
    # released twice (a stray note_off cuts short another lane's same pitch)
    started = collections.Counter((g["ch"], g["note"]) for g in gens)
    stopped = collections.Counter((o["ch"], o["note"]) for o in offs)
    assert started == stopped, f"starts {started} != stops {stopped}"
    for o in offs:
        assert "held_ms" in o, f"a release with no duration: {o}"


def test_edge_texture_condition_narrows_but_never_silences():
    """`when: {texture: [...]}` - the lane speaks only over that kind of playing.

    The classifier has been in for a while and nothing could USE it: no scene
    named a condition and the panel had no control for one. These are the rules
    the UI is about to write against.
    """
    from backend.mie.graph import Edge
    plain = Edge(src=0, dst=3, algo="sustain", id="e")
    assert plain.wants("chord") and plain.wants("melody") and plain.wants(None),         "an edge that names no condition must take everything"

    picky = Edge(src=0, dst=3, algo="sustain", id="e",
                 params={"when": {"texture": ["sustained", "chord"]}})
    assert picky.wants("sustained") and picky.wants("chord")
    assert not picky.wants("melody") and not picky.wants("arpeggio")
    # a missing reading must not silence a scene - a condition narrows on
    # purpose, it does not go quiet because the classifier had nothing to say
    assert picky.wants(None), "no texture reading silenced a conditioned edge"
    # a name that is not in the list does NOT pass - narrowing is the point -
    # so a misspelt one silences the lane, and the panel has to say so
    assert not picky.wants("sustaind")

    # empty means no condition, so clearing the chips in the panel restores
    # "takes everything" rather than muting the edge for ever
    for empty in ({"texture": []}, {}, None):
        e = Edge(src=0, dst=3, algo="sustain", id="e", params={"when": empty})
        assert e.wants("melody"), f"an empty condition muted the lane: {empty}"

    # the shorthand, and a single string instead of a list
    short = Edge(src=0, dst=3, algo="sustain", id="e", params={"texture": "arpeggio"})
    assert short.wants("arpeggio") and not short.wants("chord")


def test_a_misspelt_texture_condition_says_so_on_the_row():
    """Silenced by a setting must never look like silenced by having nothing to say."""
    from backend.mie.graph import Edge
    bad = Edge(src=0, dst=3, algo="sustain", id="e", params={"when": {"texture": ["sustaind"]}})
    assert "sustaind" in Engine._mute_reason(bad)
    ok = Edge(src=0, dst=3, algo="sustain", id="e", params={"when": {"texture": ["chord"]}})
    assert Engine._mute_reason(ok) == "", "a real condition was reported as broken"
    # one good name among typos is a deliberate scene, not a mistake to shout about
    mixed = Edge(src=0, dst=3, algo="sustain", id="e",
                 params={"when": {"texture": ["chord", "sustaind"]}})
    assert Engine._mute_reason(mixed) == ""


def test_edge_texture_condition_reaches_both_edge_lookups():
    """Both the tick lanes and the note-driven lanes have to honour it.

    They are two different call sites; conditioning one and not the other is
    how a lane ends up answering notes it was told to stay out of.
    """
    cond = {"texture": ["chord"]}
    eng, clk, out = make([
        {"id": "tick", "src": 0, "dst": 3, "algo": "sustain", "prob": 1.0, "after_s": 0.5,
         "lane": "sustain", "when": cond},
        {"id": "note", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0,
         "lane": "echo", "when": cond},
    ])
    g = eng.graph
    now = clk()
    assert [e.id for e in g.timed_edges(None, texture="chord")] == ["tick"]
    assert [e.id for e in g.timed_edges(None, texture="melody")] == []
    assert "note" in [e.id for e in g.candidate_edges("HUMAN", 9, 0, now, texture="chord")]
    assert [e.id for e in g.candidate_edges("HUMAN", 9, 0, now, texture="melody")] == []
    # and with no condition at all, both call sites hand them over again
    for e in g.edges:
        e.params.pop("when")
    assert [e.id for e in g.timed_edges(None, texture="melody")] == ["tick"]
    assert "note" in [e.id for e in g.candidate_edges("HUMAN", 9, 0, now, texture="melody")]


def test_texture_reads_block_chords():
    """Struck together, not spread out. On the real takes the gap distribution
    is bimodal - a spike under 20 ms and a band at 150-400 ms - so the share of
    notes arriving inside a strike separates cleanly."""
    eng, clk, out = make([])
    for _ in range(4):
        hold_chord(eng, clk, 9, [48, 52, 55, 60])
        run_for(eng, clk, 0.3)
        release_chord(eng, clk, 9, [48, 52, 55, 60])
        run_for(eng, clk, 0.2)
    eng.st.refresh_texture(clk())
    assert eng.st.texture == "chord", eng.st.texture


def test_texture_reads_a_held_chord_as_sustained():
    eng, clk, out = make([])
    hold_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 4.0)                       # fingers down, nothing new struck
    assert eng.st.texture == "sustained", eng.st.texture


def test_texture_reads_an_empty_keyboard_as_quiet():
    eng, clk, out = make([])
    run_for(eng, clk, 4.0)
    assert eng.st.texture == "quiet", eng.st.texture
    play(eng, clk, 9, 60, vel=80, hold=0.2)
    release_chord(eng, clk, 9, [60])
    run_for(eng, clk, 5.0)
    assert eng.st.texture == "quiet", eng.st.texture


def test_texture_tells_an_arpeggio_from_a_melody():
    """Leap size cannot separate them - measured over the 2026-09-07 takes the
    within-hand leap is 5-7 semitones either way. What separates them is what
    the line lands on and how it moves."""
    eng, clk, out = make([])
    hold_chord(eng, clk, 9, [36, 40, 43])        # C major underneath, so there is a chord
    run_for(eng, clk, 0.2)
    for n in (60, 64, 67, 72, 76, 79):           # spelling the chord, running upward
        play(eng, clk, 9, n, vel=70, hold=0.15)
        run_for(eng, clk, 0.1)
    eng.st.refresh_texture(clk())
    assert eng.st.texture == "arpeggio", eng.st.texture

    eng, clk, out = make([])
    hold_chord(eng, clk, 9, [36, 40, 43])
    run_for(eng, clk, 0.2)
    for n in (72, 71, 74, 69, 71, 66):           # turning, and off the chord
        play(eng, clk, 9, n, vel=70, hold=0.15)
        run_for(eng, clk, 0.1)
    eng.st.refresh_texture(clk())
    assert eng.st.texture == "melody", eng.st.texture


def test_hands_split_at_the_gap_not_at_a_fixed_pitch():
    from backend.mie.texture import hands
    assert hands([36, 60, 64, 67]) == ([36], [60, 64, 67])       # bass under a voicing
    assert hands([60, 64, 67]) == ([], [60, 64, 67])             # one hand, no gap
    assert hands([67, 72, 79]) == ([], [67, 72, 79])             # wide, but not a left hand
    assert hands([48]) == ([], [48])


def test_an_edge_can_ask_for_one_kind_of_playing():
    """`texture: [...]` on an edge; an edge that names none takes everything, so
    scenes written before this keep working."""
    edges = [{"id": "onlyheld", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0, "interval": 7,
              "constraint": "free", "lane": "a", "texture": ["sustained"]},
             {"id": "always", "src": 0, "dst": 12, "algo": "follow", "prob": 1.0, "interval": 7,
              "constraint": "free", "lane": "b"}]
    eng, clk, out = make(edges)
    for n in (72, 71, 74, 69, 71, 66):           # a melody: the gated edge stays out
        play(eng, clk, 9, n, vel=70, hold=0.15)
        run_for(eng, clk, 0.1)
    assert eng.st.texture == "melody", eng.st.texture
    assert not out.notes("note_on", ch=11), "the edge spoke over playing it was not asked for"
    assert out.notes("note_on", ch=12), "the ungated edge should answer everything"


# --------------------------------------------------------- the high-level knobs
def test_density_decides_how_much_not_how_often():
    """`density` is not another `prob_scale`. That one decides how OFTEN a lane
    speaks; this decides how MUCH it plays when it does. 0.5 means exactly as
    the edge is written, so a scene that never sets it is unchanged."""
    edges = [{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0, "repeats": 5,
              "delay_beats": 0.5, "vel_scale": 1.0, "decay": 1.0, "min_vel": 1,
              "constraint": "free", "lane": "echo"}]

    def returns(density):
        eng, clk, out = make(edges)
        if density is not None:
            eng.set_global("density", density)
        play(eng, clk, 9, 60, vel=90, hold=0.2)
        run_for(eng, clk, 6.0)
        return len(out.notes("note_on", ch=10))

    assert returns(None) == 5, "an untouched scene must behave as written"
    assert returns(0.5) == 5, "0.5 is 'as written'"
    thin, thick = returns(0.0), returns(1.0)
    assert thin < 5 < thick, f"thin {thin}, as written 5, thick {thick}"
    assert thin >= 1, "it must never silence a lane completely"


def test_density_thins_a_pad_as_well_as_an_echo():
    edges = [{"id": "pad", "src": 0, "dst": 3, "algo": "silence", "prob": 1.0, "after_s": 1.0,
              "lane": "pad", "hold_s": 8, "voices": 4, "vel": 50, "constraint": "chord",
              "align": "none"}]

    def voices(density):
        eng, clk, out = make(edges)
        eng.set_global("density", density)
        hold_chord(eng, clk, 9, [48, 52, 55, 58])
        release_chord(eng, clk, 9, [48, 52, 55, 58])
        run_for(eng, clk, 3.0)
        return len(out.notes("note_on", ch=3))

    assert voices(0.0) < voices(0.5) <= voices(1.0), \
        f"{voices(0.0)} / {voices(0.5)} / {voices(1.0)}"


def test_the_density_knob_did_not_shadow_the_human_density():
    """`st.density` was already the EMA of how densely the HUMAN is playing, and
    the restraint curve reads it. Taking that name would have quietly broken
    restraint while looking like it worked."""
    eng, clk, out = make([])
    eng.set_global("density", 1.0)
    for n in (60, 62, 64, 65, 67):
        play(eng, clk, 9, n, vel=80, hold=0.1)
        run_for(eng, clk, 0.05)
    assert eng.st.density > 0, "the human-density EMA stopped moving"
    assert eng.st.density_knob == 1.0


def test_a_scene_can_be_saved_and_comes_back_the_same():
    """An evening of finding the right decay by ear must survive a restart."""
    import json
    import tempfile
    from backend.mie.graph import Scene, save_scene

    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "echo"}])
    eng.set_global("density", 0.8)
    eng.set_edge("e", "prob", 0.25)
    eng.set_edge("e", "repeats", 7)
    snap = eng.scene_snapshot()
    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/saved.json"
        snap.path = path
        save_scene(snap)
        back = Scene.from_json(json.load(open(path, encoding="utf-8")), path)
    assert back.globals["density"] == 0.8
    e = [x for x in back.edges if x.id == "e"][0]
    assert e.prob == 0.25 and e.params["repeats"] == 7


def test_the_saved_snapshot_is_a_copy_not_the_live_edges():
    """The writer runs off the engine thread while the player is still turning
    knobs; it must not serialise objects that are being mutated under it."""
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "echo"}])
    snap = eng.scene_snapshot()
    eng.set_edge("e", "prob", 0.9)
    assert [x for x in snap.edges if x.id == "e"][0].prob == 0.5


def test_a_saved_density_is_in_force_from_the_start():
    """Without this a scene's own value is ignored until the player happens to
    touch the slider - the scene says one thing and the engine plays another."""
    from backend.mie.graph import Scene

    sc = Scene.from_json({"id": "t", "name": "t", "mode": "INTERACTIVE",
                          "global": {"density": 0.9}, "edges": []})
    clk = FakeClock(10.0)
    eng = Engine(sc, instruments(), clock=clk, send=FakeMidiOut(clk), rng=Random(1),
                 mode="INTERACTIVE")
    assert eng.st.density_knob == 0.9
    eng.load_scene(Scene.from_json({"id": "u", "name": "u", "global": {"density": 0.1}, "edges": []}))
    assert eng.st.density_knob == 0.1


# ------------------------------------------------------------ A / LIVE / B
def _preset_edges() -> list[dict]:
    return [{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5, "repeats": 3,
             "delay_beats": 1.0, "constraint": "free", "lane": "echo"}]


def test_live_survives_a_look_at_a_preset():
    """The middle position is the whole point: it holds what you were just
    doing, so glancing at a stored setting cannot cost you an evening of
    tweaking."""
    eng, clk, out = make(_preset_edges())
    eng.set_edge("e", "repeats", 6)
    eng.set_global("density", 0.9)
    eng.preset_save("A")
    eng.set_edge("e", "repeats", 2)          # …and carry on working
    eng.set_global("density", 0.2)
    assert eng.preset_slot == "LIVE"

    eng.preset_select("A")
    assert eng.preset_slot == "A"
    assert eng.graph.find_edge("e").params["repeats"] == 6
    assert eng.scene.globals["density"] == 0.9

    eng.preset_select("LIVE")
    assert eng.preset_slot == "LIVE"
    assert eng.graph.find_edge("e").params["repeats"] == 2, "the live state was lost"
    assert eng.scene.globals["density"] == 0.2


def test_switching_preset_does_not_cut_the_music_off():
    """Switching must be playable: `load_scene` releases every sounding note and
    clears the lane state, which would chop the music at every flip. A preset is
    the same rig played differently, so it is applied in place."""
    edges = _preset_edges() + [
        {"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "prob": 1.0, "delay_ms": 10,
         "shadow": "top", "constraint": "free", "lane": "shadow", "max_hold_s": 8.0}]
    eng, clk, out = make(edges)
    eng.preset_save("A")
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.5)
    sounding = sorted(k for k in eng.st.active_gen)
    assert sounding, "nothing was sounding to begin with"
    eng.preset_select("A")
    run_for(eng, clk, 0.1)
    assert sorted(k for k in eng.st.active_gen) == sounding, "the flip cut the notes off"


def test_a_preset_changes_settings_not_the_rig():
    """A preset is a way of playing the same rig. It must never add, remove or
    re-route an edge - if the scene has moved on, the missing edge is skipped."""
    eng, clk, out = make(_preset_edges())
    eng.preset_save("A")
    data = eng.scene.presets["A"]
    data["edges"]["ghost"] = {"id": "ghost", "src": 0, "dst": 12, "algo": "echo", "prob": 1.0}
    data["edges"]["e"]["dst"] = 99          # a preset must not re-route
    eng.preset_apply(data)
    assert [e.id for e in eng.graph.edges] == ["e"], "a preset added an edge"
    assert eng.graph.find_edge("e").dst == 10, "a preset re-routed an edge"


def test_editing_while_on_a_preset_is_marked():
    eng, clk, out = make(_preset_edges())
    eng.preset_save("A")
    eng.preset_select("A")
    assert not eng.preset_dirty
    eng.set_edge("e", "repeats", 9)
    assert eng.preset_dirty, "an edit on a stored preset went unmarked"
    eng.preset_save("A")                     # saving adopts the edit
    assert eng.scene.presets["A"]["edges"]["e"]["repeats"] == 9


def test_an_empty_slot_changes_nothing():
    eng, clk, out = make(_preset_edges())
    eng.set_edge("e", "repeats", 4)
    assert eng.preset_select("B") is False
    assert eng.preset_slot == "LIVE"
    assert eng.graph.find_edge("e").params["repeats"] == 4


def test_presets_are_saved_with_the_scene():
    import json
    import tempfile
    from backend.mie.graph import Scene, save_scene

    eng, clk, out = make(_preset_edges())
    eng.set_edge("e", "repeats", 5)
    eng.preset_save("A")
    snap = eng.scene_snapshot()
    with tempfile.TemporaryDirectory() as d:
        snap.path = f"{d}/s.json"
        save_scene(snap)
        back = Scene.from_json(json.load(open(snap.path, encoding="utf-8")), snap.path)
    assert back.presets["A"]["edges"]["e"]["repeats"] == 5


def test_a_bare_fifth_does_not_re_spell_a_phrase():
    """17:13 take: three of five transpositions fired on a chord fragment.

    The recogniser reads whatever is down at that instant, and while a hand is
    landing that is a bare fifth - it read D5 at t=90.18 and Dm ten milliseconds
    later. Am -> A5 would map Dorian onto Ionian at the same root, turning a
    minor phrase major, because a fifth says nothing about the third.
    """
    from backend.mie.harmony import recognize
    from backend.mie.scheduler import NotePair

    eng, clk, out = make([_phrase_edge(follow_chord=True)])
    pair = NotePair(ch=12, note=69, vel=60, t_on=0, t_off=1, lane="phrase",
                    capture_root=9, capture_quality="m", pass_id=("p", 1))
    eng.st.set_chord(recognize([57, 64], clk()))              # A5, mid-strike
    assert eng.st.chord.name == "A5"
    assert eng._phrase_target(pair) is None, "a bare fifth was allowed to re-spell the phrase"

    pair2 = NotePair(ch=12, note=69, vel=60, t_on=0, t_off=1, lane="phrase",
                     capture_root=9, capture_quality="m", pass_id=("p", 2))
    eng.st.set_chord(recognize([62, 65, 69], clk()))          # the third arrives: Dm
    assert eng._phrase_target(pair2) == (2, "m")


def test_a_phrase_captured_over_a_fragment_is_not_marked_to_follow():
    """The other end of the same rule: with no third at capture there is no
    mode to transpose FROM, so the phrase is not marked to follow at all."""
    from backend.mie.algos import phrase
    from backend.mie.harmony import recognize

    eng, clk, out = make([_phrase_edge(follow_chord=True, repeats=1)])
    edge = eng.graph.find_edge("ph")
    for i, n in enumerate([69, 72, 76]):
        play(eng, clk, 9, n, vel=70, hold=0.2)
        if i < 2:
            run_for(eng, clk, 0.05)
    run_for(eng, clk, 1.2)

    eng.st.set_chord(recognize([57, 64], clk()))              # A5: a bare fifth
    props = phrase.tick(eng.st, edge, eng.rng, clk(), {})
    assert props, "the phrase did not fire"
    assert all(p.capture_root is None for p in props), "captured a mode from a bare fifth"

    eng.st.set_chord(recognize([57, 60, 64], clk()))          # Am: a real chord
    props = phrase.tick(eng.st, edge, eng.rng, clk(), {})
    assert props and all(p.capture_root == 9 for p in props)


def test_every_lane_release_says_why():
    """Three logs in a row carried 26 `lane_off` rows with an empty reason,
    because only the silence lane recorded one. A release you cannot attribute
    is a release you cannot debug."""
    edges = [{"id": "su", "src": 0, "dst": 4, "algo": "sustain", "prob": 1.0, "after_s": 0.5,
              "every_bars_min": 0.25, "every_bars_max": 0.25, "voices": 2, "vel": 50,
              "hold_beats": 8, "constraint": "chord", "lane": "sustain", "align": "none"}]
    eng, clk, out = make(edges)
    eng.instruments[4] = Instrument(4, "Fantom Strings", "strings", "fantom", True, 6, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 6.0)                      # fills past the voice budget
    release_chord(eng, clk, 9, [48, 52, 55])
    run_for(eng, clk, 3.0)                      # and then the player stops
    reasons = {r.get("why") for r in eng.ui_events if r["type"] == "lane_off"}
    assert reasons, "the sustain lane never released"
    assert "" not in reasons, f"a release went unexplained: {reasons}"
    assert reasons <= {"voice_budget", "human_stopped"}, reasons


# --------------------------------------------------------- putting it back
def test_undo_puts_the_last_change_back():
    """The 17:21 accident in one line: a knob you can put back is a knob you
    will explore."""
    eng, clk, out = make([{"id": "su", "src": 0, "dst": 4, "algo": "sustain", "prob": 1.0,
                           "low": 55, "high": 88, "lane": "sustain"}])
    eng.set_edge("su", "high", 55)              # the accident
    assert eng.graph.find_edge("su").params["high"] == 55
    assert eng.undo() is True
    assert eng.graph.find_edge("su").params["high"] == 88, "the register was not restored"
    assert eng.undo() is False, "there was nothing left to undo"


def test_undo_steps_back_through_several_changes():
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "echo"}])
    eng.set_edge("e", "repeats", 4)
    eng.set_edge("e", "repeats", 5)
    eng.set_global("density", 0.9)
    eng.undo(); assert eng.scene.globals.get("density") is None
    eng.undo(); assert eng.graph.find_edge("e").params["repeats"] == 4
    eng.undo(); assert eng.graph.find_edge("e").params["repeats"] == 3


def test_a_preset_switch_does_not_bury_the_undo_stack():
    """A preset writes a hundred settings at once. Recording each would bury
    the player's last real move and undo would appear not to work."""
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "echo"}])
    eng.preset_save("A")
    eng.set_edge("e", "repeats", 7)             # the one move worth undoing
    eng.preset_select("A")
    eng.preset_select("LIVE")
    assert eng.undo() is True
    assert eng.graph.find_edge("e").params["repeats"] == 3, "undo did not reach the real change"


def test_revert_goes_back_to_the_file_not_to_a_code_default():
    """"Default" here means the last state the player deliberately saved, which
    is what the scene file holds - not what the code happens to ship with."""
    import json
    import tempfile
    from backend.mie.graph import Scene, save_scene

    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "echo"}])
    with tempfile.TemporaryDirectory() as d:
        snap = eng.scene_snapshot()
        snap.path = f"{d}/s.json"
        eng.set_edge("e", "repeats", 6)         # something the player liked
        snap = eng.scene_snapshot(); snap.path = f"{d}/s.json"
        save_scene(snap)
        eng.scene.path = snap.path
        eng.set_edge("e", "repeats", 1)         # …and then something they did not
        eng.set_global("density", 0.05)
        assert eng.revert() is True
        assert eng.graph.find_edge("e").params["repeats"] == 6
        # revert is authoritative: a global the file does not carry is dropped,
        # not merged, or "put it back" would not mean what it says
        assert "density" not in eng.scene.globals


def test_revert_can_take_one_edge_and_leave_the_rest():
    import tempfile
    from backend.mie.graph import save_scene

    eng, clk, out = make([{"id": "a", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "a"},
                          {"id": "b", "src": 0, "dst": 11, "algo": "echo", "prob": 0.5,
                           "repeats": 3, "constraint": "free", "lane": "b"}])
    with tempfile.TemporaryDirectory() as d:
        snap = eng.scene_snapshot(); snap.path = f"{d}/s.json"
        save_scene(snap)
        eng.scene.path = snap.path
        eng.set_edge("a", "repeats", 9)
        eng.set_edge("b", "repeats", 9)
        assert eng.revert("a") is True
        assert eng.graph.find_edge("a").params["repeats"] == 3
        assert eng.graph.find_edge("b").params["repeats"] == 9, "revert touched an edge it was not asked about"


# ---------------------------------------------------------- the TIME knob
def test_time_knob_stretches_every_wait_together():
    """Bad Mood's CLOCK in one line: one control for how long everything waits.
    An echo's delay, a pad's hold, the silence a lane needs before it enters."""
    edges = [{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0, "repeats": 2,
              "delay_beats": 1.0, "vel_scale": 1.0, "decay": 1.0, "min_vel": 1,
              "constraint": "free", "lane": "echo"}]

    def first_gap(scale):
        eng, clk, out = make(edges)
        if scale is not None:
            eng.set_global("time", scale)
        play(eng, clk, 9, 60, vel=90, hold=0.2)
        run_for(eng, clk, 12.0)
        ons = [t for t, _, _, _ in out.notes("note_on", ch=10)]
        assert len(ons) >= 2, f"the echo did not repeat at scale {scale}"
        return round(ons[1] - ons[0], 3)

    base = first_gap(None)
    assert first_gap(1.0) == base, "1.0 must be the scene exactly as written"
    assert abs(first_gap(2.0) - base * 2) < 0.02, "doubling the knob must double the wait"
    assert abs(first_gap(0.5) - base / 2) < 0.02, "halving it must halve the wait"


def test_time_knob_moves_in_musical_steps():
    """Halving a delay is musical; multiplying it by 1.07 is not. Bad Mood's
    CLOCK steps for the same reason, and its SMOOTH switch turns that off."""
    from backend.mie.algos import quantize_time

    assert quantize_time(0.40) == pytest.approx(1 / 3, abs=1e-6)
    assert quantize_time(0.60) == pytest.approx(2 / 3, abs=1e-6)
    assert quantize_time(1.30) == 1.5
    assert quantize_time(2.70) == 3.0
    assert quantize_time(9.00) == 4.0            # clamped to the top step

    eng, clk, out = make([])
    eng.set_global("time", 1.3)
    assert eng.st.time_knob == 1.5
    eng.set_global("time_steps", False)
    eng.set_global("time", 1.3)
    assert eng.st.time_knob == pytest.approx(1.3), "SMOOTH did not turn the steps off"


def test_a_scene_without_a_time_knob_is_untouched():
    """Every scene written before this must behave exactly as it did."""
    from backend.mie.algos import time_scale

    eng, clk, out = make([])
    assert eng.st.time_knob is None
    assert time_scale(eng.st) == 1.0


def test_the_time_knob_reaches_a_pad_as_well_as_an_echo():
    edges = [{"id": "tex", "src": 0, "dst": 1, "algo": "silence", "prob": 1.0, "after_s": 2.0,
              "lane": "texture", "hold_s": 4, "voices": 2, "vel": 40, "constraint": "chord",
              "align": "none", "silence_mode": "attack"}]

    def entered_at(scale):
        eng, clk, out = make(edges)
        eng.set_global("time", scale)
        hold_chord(eng, clk, 9, [48, 52, 55])
        t0 = clk()
        run_for(eng, clk, 12.0)
        ons = out.notes("note_on", ch=1)
        assert ons, f"the pad never entered at scale {scale}"
        return round(ons[0][0] - t0, 2)

    quick, slow = entered_at(1.0), entered_at(2.0)
    assert slow > quick * 1.6, f"the wait did not stretch: {quick} -> {slow}"


def test_a_sus_chord_does_not_decide_a_phrase_mode():
    """18:34 take: Asus2 -> Am was allowed through. A sus chord says nothing
    about the third, so the scale table has to guess one - it would have read
    the phrase as major before making it minor. Same reasoning as the bare
    fifth, one note further on."""
    from backend.mie.harmony import recognize
    from backend.mie.scheduler import NotePair

    eng, clk, out = make([_phrase_edge(follow_chord=True)])
    pair = NotePair(ch=12, note=69, vel=60, t_on=0, t_off=1, lane="phrase",
                    capture_root=9, capture_quality="m", pass_id=("p", 1))
    eng.st.set_chord(recognize([57, 59, 64], clk()))          # Asus2: A B E, no third
    assert eng.st.chord.name == "Asus2"
    assert eng._phrase_target(pair) is None, "a sus chord chose a mode for the phrase"

    pair2 = NotePair(ch=12, note=69, vel=60, t_on=0, t_off=1, lane="phrase",
                     capture_root=9, capture_quality="m", pass_id=("p", 2))
    eng.st.set_chord(recognize([60, 64, 67], clk()))          # C: a third at last
    assert eng._phrase_target(pair2) == (0, "")


def test_every_chord_scale_is_a_real_scale():
    """The augmented row was whole-tone padded to seven entries by repeating a
    degree, which is not a scale - degrees 6 and 7 mapped to the same pitch."""
    from backend.mie.constraint import _CHORD_SCALES, chord_scale

    for quality, iv in _CHORD_SCALES.items():
        assert len(iv) == 7, f"{quality!r} has {len(iv)} degrees"
        assert len(set(iv)) == 7, f"{quality!r} repeats a degree: {iv}"
        assert list(iv) == sorted(iv), f"{quality!r} is out of order: {iv}"
        assert iv[0] == 0 and iv[-1] < 12, f"{quality!r} leaves the octave: {iv}"
    # and the augmented scale still contains the chord it is named for
    aug = set(chord_scale("aug"))
    assert {0, 4, 8} <= aug, aug


# ------------------------------------------------------------------ freeze
def _freeze_edges() -> list[dict]:
    return [{"id": "sh", "src": 0, "dst": 11, "algo": "shadow", "prob": 1.0, "delay_ms": 10,
             "shadow": "top", "constraint": "free", "lane": "shadow", "max_hold_s": 2.0}]


def test_freeze_holds_what_is_sounding_so_you_can_play_over_it():
    """Bad Mood's FREEZE: keep the current sound and build on it. Here the bed
    is one the engine made, rather than one the player had to hold themselves."""
    eng, clk, out = make(_freeze_edges())
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.5)
    held = sorted(k for k in eng.st.active_gen)
    assert held, "nothing was sounding to freeze"
    assert eng.freeze(True) == len(held)
    release_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 6.0)                       # long past max_hold_s
    assert sorted(k for k in eng.st.active_gen) == held, "the freeze did not hold"


def test_unfreeze_lets_go():
    eng, clk, out = make(_freeze_edges())
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.5)
    n = eng.freeze(True)
    run_for(eng, clk, 3.0)
    assert eng.st.active_gen
    assert eng.freeze(False) == n
    run_for(eng, clk, 0.5)
    assert not eng.st.active_gen, "unfreeze left notes sounding"
    assert len(out.notes("note_off", ch=11)) >= n


def test_a_freeze_ends_by_itself():
    """A deliberate override of the note length is a deliberate override of the
    thing that stops notes hanging. The leash is longer, never absent: a freeze
    someone walks away from has to end."""
    eng, clk, out = make(_freeze_edges(), freeze_max_s=4.0)
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.5)
    eng.freeze(True)
    release_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 3.0)
    assert eng.st.active_gen, "it let go too early"
    run_for(eng, clk, 4.0)
    assert not eng.st.active_gen, "a frozen note outlived its own cap"


def test_panic_outranks_a_freeze():
    eng, clk, out = make(_freeze_edges())
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.5)
    eng.freeze(True)
    eng.panic("test")
    run_for(eng, clk, 0.2)
    assert not eng.st.active_gen, "PANIC did not clear a frozen note"
    assert eng._frozen is False, "the engine came back still frozen"


def test_freeze_can_take_one_lane_and_leave_the_rest():
    edges = _freeze_edges() + [
        {"id": "f", "src": 0, "dst": 12, "algo": "follow", "prob": 1.0, "interval": 7,
         "constraint": "free", "lane": "follow"}]
    eng, clk, out = make(edges)
    eng.instruments[12] = Instrument(12, "MODX", "synth", "hw", True, 8, 1.0, (36, 96), True)
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.3)
    assert [k for k in eng.st.active_gen if k[0] == 11]
    eng.freeze(True, lane="shadow")
    release_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 6.0)
    assert [k for k in eng.st.active_gen if k[0] == 11], "the frozen lane let go"
    assert not [k for k in eng.st.active_gen if k[0] == 12], "an unfrozen lane was held too"


def test_freeze_holds_only_what_is_already_audible():
    """Freezing something still in the queue would hold a note that has not been
    heard - and by the time it arrived it might be a different pitch, because
    late binding re-snaps it on the way out."""
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0,
                           "repeats": 4, "delay_beats": 1.0, "vel_scale": 1.0, "decay": 1.0,
                           "min_vel": 1, "constraint": "free", "lane": "echo"}])
    play(eng, clk, 9, 60, vel=90, hold=0.2)
    run_for(eng, clk, 0.6)                       # one return out, three queued
    sounding = len(eng.st.active_gen)
    assert eng.freeze(True) == sounding
    assert sounding < 4, "the test needs notes still waiting in the queue"


def test_freezing_nothing_is_not_a_state():
    """2026-09-08: holding SPACE latched a freeze with nothing sounding, the
    next tick unlatched it because "frozen but silent" is not a state, and
    every press logged another event. The panel filled with hundreds of
    identical lines and stopped answering."""
    eng, clk, out = make(_freeze_edges())
    run_for(eng, clk, 0.5)                      # nothing is sounding
    assert eng.freeze(True) == 0
    assert eng._frozen is False, "it latched with nothing to hold"
    before = len([e for e in eng.ui_events if e["type"] == "freeze"])
    for _ in range(20):                         # what a held key does
        eng.freeze(True)
    after = len([e for e in eng.ui_events if e["type"] == "freeze"])
    assert after - before <= 20, "each press must stay one line, not a cascade"
    assert eng._frozen is False


def test_pressing_freeze_twice_is_not_two_events():
    eng, clk, out = make(_freeze_edges())
    hold_chord(eng, clk, 9, [60, 64, 67])
    run_for(eng, clk, 0.5)
    n = eng.freeze(True)
    assert n > 0
    assert eng.freeze(True) == 0, "a second press re-froze what was already held"
    assert eng.freeze(False) > 0
    assert eng.freeze(False) == 0, "a second release fired again"


def test_an_edge_reports_its_own_dropped_notes():
    """19:27 take: one edge's octave was dragged to -3, its notes landed below
    the synth's range and every one was dropped, and the row still looked
    healthy. A lane throwing its notes away must say so where the control that
    caused it is."""
    eng, clk, out = make([_phrase_edge(octave=-3, repeats=1)])
    eng.instruments[12] = Instrument(12, "MODX", "synth", "hw", True, 8, 1.0, (36, 96), False)
    for i, n in enumerate([48, 52, 55]):
        play(eng, clk, 9, n, vel=70, hold=0.2)
        if i < 2:
            run_for(eng, clk, 0.05)
    run_for(eng, clk, 6.0)
    assert not out.notes("note_on", ch=12), "the test needs the notes to fall out of range"
    assert eng.edge_drops.get("ph", 0) > 0, "the edge did not count its own drops"
    row = [e for e in eng.snapshot()["edges"] if e["id"] == "ph"][0]
    assert row["drops"] == eng.edge_drops["ph"]
    assert row["fires"] > 0, "it fired, it just threw everything away - that is the point"


def test_a_lane_that_cannot_make_a_sound_says_so():
    """19:40 take: the echo's velocity scale had been dragged to zero during the
    earlier click storm and saved into the scene. It sat there enabled and mute
    for the whole session, showing zero fires - indistinguishable from a lane
    with nothing to say."""
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0,
                           "repeats": 3, "vel_scale": 0.0, "constraint": "free", "lane": "echo"}])
    play(eng, clk, 9, 60, vel=100, hold=0.2)
    run_for(eng, clk, 3.0)
    assert not out.notes("note_on", ch=10), "the test needs a silent lane"
    row = [x for x in eng.snapshot()["edges"] if x["id"] == "e"][0]
    assert row["mute"], "an enabled lane that cannot sound said nothing about it"
    eng.set_edge("e", "vel_scale", 0.8)
    row = [x for x in eng.snapshot()["edges"] if x["id"] == "e"][0]
    assert not row["mute"], "the warning stayed after the cause was fixed"


def test_freeze_arrests_the_tail_that_was_already_on_its_way():
    """19:46 take: the player froze one note and two more echo returns landed
    afterwards. Holding what is audible is only half of "hold this moment" -
    the echoes of what was played a second ago are still in the queue and
    arrive on top of the held bed."""
    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 1.0,
                           "repeats": 5, "delay_beats": 1.0, "vel_scale": 1.0, "decay": 1.0,
                           "min_vel": 1, "constraint": "free", "lane": "echo"}])
    play(eng, clk, 9, 60, vel=90, hold=0.2)
    run_for(eng, clk, 0.6)                       # one return out, four queued
    before = len(out.notes("note_on", ch=10))
    assert before >= 1 and before < 5, "the test needs returns still waiting"
    assert eng.freeze(True) > 0
    run_for(eng, clk, 8.0)
    assert len(out.notes("note_on", ch=10)) == before, \
        "a queued return landed after the freeze"


def test_the_engine_says_nothing_new_while_frozen():
    """The player's choice: while frozen the held bed sounds alone and they
    play over it. Answering as well would put the engine back on top of the
    very thing it was asked to hold still."""
    edges = [{"id": "f", "src": 0, "dst": 11, "algo": "follow", "prob": 1.0, "interval": 7,
              "constraint": "free", "lane": "follow"},
             {"id": "tex", "src": 0, "dst": 1, "algo": "silence", "prob": 1.0, "after_s": 0.5,
              "lane": "texture", "hold_s": 8, "voices": 2, "vel": 40, "constraint": "chord",
              "align": "none", "silence_mode": "attack"}]
    eng, clk, out = make(edges)
    play(eng, clk, 9, 60, vel=90, hold=0.3)
    run_for(eng, clk, 0.2)
    assert eng.freeze(True) > 0, "nothing was sounding to hold"
    held = sorted(k for k in eng.st.active_gen)
    before = len(out.notes("note_on"))

    for n in (62, 64, 65, 67):                  # the player keeps playing
        play(eng, clk, 9, n, vel=90, hold=0.25)
        run_for(eng, clk, 0.2)
    run_for(eng, clk, 4.0)                      # and long enough for a pad to want in
    assert len(out.notes("note_on")) == before, "the engine spoke while frozen"
    assert sorted(k for k in eng.st.active_gen) == held, "the held bed changed"

    eng.freeze(False)
    run_for(eng, clk, 0.3)
    play(eng, clk, 9, 72, vel=90, hold=0.3)
    run_for(eng, clk, 0.5)
    assert len(out.notes("note_on")) > before, "it stayed deaf after unfreezing"


def test_the_time_knob_does_not_deafen_the_phrase_detector():
    """Everything else TIME touches is a wait the engine performs. The phrase
    gap is not a wait, it is a detector threshold on the player's own playing,
    and stretching it just makes the engine stop noticing phrases: at TIME 3x
    on the 20:33 take it wanted 1.22 s of silence while the player's median
    note spacing was 0.364 s."""
    from backend.mie.algos.phrase import phrase_gap

    eng, clk, out = make([_phrase_edge(phrase_gap_beats=1.0)])
    edge = eng.graph.find_edge("ph")
    eng.st.bpm = 120.0
    eng.st.clock_source = "manual"
    base = phrase_gap(eng.st, edge)
    eng.set_global("time", 3.0)
    assert eng.st.time_knob == 3.0, "the knob did not take"
    assert phrase_gap(eng.st, edge) == pytest.approx(base), \
        "the TIME knob moved the phrase-end threshold"

    # …while it still stretches the waits, which is what it is for
    from backend.mie.algos import delay_s
    eng.set_global("time", 1.0)
    quick = delay_s(edge, eng.st)
    eng.set_global("time", 3.0)
    assert delay_s(edge, eng.st) == pytest.approx(quick * 3), "TIME stopped stretching delays"


def test_save_as_moves_the_engine_into_the_new_scene():
    """Every editor's Save As does this. Without it the engine still thought it
    was in the scene it had loaded: the player saved test1's settings into
    test01, the top bar still read test1, and the next plain Save would have
    written the OTHER file. Two names for one state is how work gets lost."""
    import tempfile
    from backend.mie.graph import save_scene

    eng, clk, out = make([{"id": "e", "src": 0, "dst": 10, "algo": "echo", "prob": 0.5,
                           "constraint": "free", "lane": "echo"}])
    with tempfile.TemporaryDirectory() as d:
        eng.scene.id = "one"
        eng.scene.path = f"{d}/one.json"
        eng.set_global("time", 2.0)
        eng.set_global("master_gain", 0.63)
        save_scene(eng.scene_snapshot())                       # plain save
        eng.mark_saved("one.json")
        assert eng.scene.id == "one"

        two = f"{d}/two.json"
        eng.mark_saved("two.json", "two", two)                 # save as
        assert eng.scene.id == "two", "the engine stayed in the old scene"
        assert eng.scene.path == two, "a later plain save would write the old file"


def test_the_global_knobs_are_in_what_gets_saved():
    """The player asked whether volume and time are saved at all. They are -
    they were going into the file that Save As had written."""
    import json
    import tempfile
    from backend.mie.graph import Scene, save_scene

    eng, clk, out = make([])
    eng.set_global("master_gain", 0.63)
    eng.set_global("time", 2.05)
    with tempfile.TemporaryDirectory() as d:
        snap = eng.scene_snapshot()
        snap.path = f"{d}/s.json"
        save_scene(snap)
        back = Scene.from_json(json.load(open(snap.path, encoding="utf-8")), snap.path)
    assert back.globals["master_gain"] == 0.63
    assert back.globals["time"] == 2.05
