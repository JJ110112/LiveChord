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

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.mie.engine import Engine  # noqa: E402
from backend.mie.events import control_event, human_event  # noqa: E402
from backend.mie.fakes import FakeClock, FakeMidiOut  # noqa: E402
from backend.mie.graph import Instrument, Scene  # noqa: E402
from backend.mie.harmony import recognize  # noqa: E402
from backend.mie.scales import SCALES, scale_pcs  # noqa: E402


# ----------------------------------------------------------------- helpers
def instruments() -> dict[int, Instrument]:
    return {
        1: Instrument(1, "REAPER", "texture", "vst", True, 6, 1.0, (36, 96), True),
        3: Instrument(3, "Fantom Pad", "pad", "fantom", True, 4, 1.0, (40, 88), True),
        10: Instrument(10, "Wavestate", "sequence", "hw", True, 4, 1.0, (36, 96), True),
        11: Instrument(11, "Iridium", "exp_synth", "hw", True, 4, 1.0, (36, 96), False),
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
    run_for(eng, clk, 1.0)
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
    assert len(out.notes("note_off")) == len(ons)


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
    run_for(eng, clk, 0.8)                               # texture pad comes in on CH1 (REAPER)
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
    run_for(eng, clk, 1.6)
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
    run_for(eng, clk, 1.6)
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
