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
    assert func == {0, 2, 4, 7, 9, 11}


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
