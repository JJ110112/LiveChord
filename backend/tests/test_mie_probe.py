"""Hardware-free tests for the MIE Phase 0 probe helpers.

Why these matter: the probe's whole job is to prove the mioXL routing cannot loop
and that PANIC really silences everything. If the self-echo detector or the PANIC
packet were wrong, Phase 0 would pass on a broken rig.
"""

import unittest

import mido

from backend.mie.probe import (
    LatencyStats,
    SelfEchoFilter,
    is_panic_trigger,
    panic_messages,
    pick_port,
)


class PickPortTest(unittest.TestCase):
    def test_case_insensitive_substring_first_hit(self):
        names = ["mioXL HST 1 0", "mioXL HST 2 1", "Faderfox UC4 2"]
        self.assertEqual(pick_port(names, "hst 2"), "mioXL HST 2 1")
        self.assertEqual(pick_port(names, "uc4"), "Faderfox UC4 2")

    def test_missing_or_null_pattern(self):
        self.assertIsNone(pick_port(["a"], "zzz"))
        self.assertIsNone(pick_port(["a"], None))  # optional ports may be null in ports.json


class SelfEchoFilterTest(unittest.TestCase):
    def test_our_note_coming_back_inside_window_is_a_loop(self):
        f = SelfEchoFilter(window_s=0.030)
        f.note_sent(11, 67, True, t=10.000)
        self.assertTrue(f.is_echo(11, 67, True, t=10.005))

    def test_same_note_from_human_outside_window_is_not_a_loop(self):
        # The human may legitimately play the very note we just echoed; only a
        # near-instant copy is evidence of a routing loop.
        f = SelfEchoFilter(window_s=0.030)
        f.note_sent(11, 67, True, t=10.000)
        self.assertFalse(f.is_echo(11, 67, True, t=10.200))

    def test_key_includes_channel_and_on_off(self):
        f = SelfEchoFilter(window_s=0.030)
        f.note_sent(11, 67, True, t=10.000)
        self.assertFalse(f.is_echo(9, 67, True, t=10.001))   # different instrument
        self.assertFalse(f.is_echo(11, 67, False, t=10.001))  # note_off is a different event


class PanicTest(unittest.TestCase):
    def test_panic_covers_all_16_channels_with_120_123_and_sustain_release(self):
        msgs = panic_messages()
        cc = {(m.channel, m.control) for m in msgs if m.type == "control_change"}
        for ch0 in range(16):
            self.assertIn((ch0, 120), cc)
            self.assertIn((ch0, 123), cc)
            self.assertIn((ch0, 64), cc)
        self.assertTrue(all(m.value == 0 for m in msgs if m.type == "control_change"))

    def test_active_notes_get_explicit_note_off_after_the_cc_block(self):
        # SWAM-style VSTs ignore CC123; the explicit note_off is the only thing that stops them.
        msgs = panic_messages({(11, 67): 1.0, (1, 60): 1.0})
        offs = [m for m in msgs if m.type == "note_off"]
        self.assertEqual({(m.channel + 1, m.note) for m in offs}, {(11, 67), (1, 60)})
        self.assertTrue(all(m.velocity == 0 for m in offs))
        self.assertGreater(msgs.index(offs[0]), 47)  # 16 ch × 3 CC come first

    def test_uc4_trigger_any_button_press_but_not_encoder_sweep(self):
        cfg = {"cc": None, "value_min": 127}
        self.assertTrue(is_panic_trigger(mido.Message("control_change", control=30, value=127), cfg))
        self.assertFalse(is_panic_trigger(mido.Message("control_change", control=21, value=64), cfg))
        self.assertFalse(is_panic_trigger(mido.Message("note_on", note=60, velocity=127), cfg))

    def test_uc4_trigger_bound_to_one_cc(self):
        cfg = {"cc": 40, "value_min": 127}
        self.assertTrue(is_panic_trigger(mido.Message("control_change", control=40, value=127), cfg))
        self.assertFalse(is_panic_trigger(mido.Message("control_change", control=41, value=127), cfg))


class LatencyStatsTest(unittest.TestCase):
    def test_summary_reports_p50_p95_max_in_ms(self):
        s = LatencyStats()
        for late_ms in [0.5, 1.0, 1.5, 2.0, 9.0]:
            s.add(due=100.0, actual=100.0 + late_ms / 1000.0)
        out = s.summary()
        self.assertIn("n=5", out)
        self.assertIn("p50=1.50 ms", out)
        self.assertIn("max=9.00 ms", out)


class _FakeOut:
    def __init__(self):
        self.sent = []  # (perf_counter, msg)

    def send(self, msg):
        import time
        self.sent.append((time.perf_counter(), msg))

    def close(self):
        pass


class ProbeFlowTest(unittest.TestCase):
    """Drive Probe with fake ports: human note_on on CH2 must become a delayed note_on on the
    target channel, the human note_off must release it, and PANIC must clear everything."""

    def _probe(self, delay_ms=40):
        import time
        from backend.mie.probe import Probe
        cfg = {"ports": {}, "echo": {"delay_ms": delay_ms, "target_ch": 11, "transpose": 0, "max_note_s": 8.0},
               "panic": {"cc": None, "value_min": 127}, "self_echo_window_ms": 30}
        pr = Probe(cfg, bypass=False, delay_ms=delay_ms, target_ch=11, log_all=False)
        pr.hst_out = _FakeOut()
        pr.reaper_out = _FakeOut()
        import threading
        threading.Thread(target=pr._scheduler, daemon=True).start()
        return pr, time

    def test_echo_on_off_timing_and_channel(self):
        pr, time = self._probe(delay_ms=40)
        t_on = time.perf_counter()
        pr._on_in(mido.Message("note_on", channel=1, note=60, velocity=100))   # human on CH2
        time.sleep(0.12)
        pr._on_in(mido.Message("note_off", channel=1, note=60))
        time.sleep(0.12)
        pr.stop.set()
        sent = pr.hst_out.sent
        self.assertEqual([m.type for _, m in sent], ["note_on", "note_off"])
        self.assertTrue(all(m.channel == 10 and m.note == 60 for _, m in sent))
        self.assertAlmostEqual(sent[0][0] - t_on, 0.040, delta=0.015)  # scheduler honoured the delay
        self.assertEqual(pr.active, {})                                  # note_off released it
        self.assertEqual(pr.reaper_out.sent, [])                         # CH11 never goes to REAPER
        self.assertEqual(len(pr.stats.samples_ms), 2)

    def test_ch1_routes_to_reaper_port(self):
        pr, time = self._probe(delay_ms=10)
        pr.target_ch = 1
        pr._on_in(mido.Message("note_on", channel=1, note=64, velocity=90))
        time.sleep(0.06)
        pr.stop.set()
        self.assertEqual(pr.hst_out.sent, [])
        self.assertEqual([m.type for _, m in pr.reaper_out.sent], ["note_on"])

    def test_human_playing_the_target_instrument_is_not_doubled(self):
        pr, time = self._probe(delay_ms=10)
        pr._on_in(mido.Message("note_on", channel=10, note=60, velocity=100))  # human already on CH11
        time.sleep(0.05)
        pr.stop.set()
        self.assertEqual(pr.hst_out.sent, [])

    def test_panic_clears_pending_and_active_and_sends_explicit_off(self):
        pr, time = self._probe(delay_ms=10)
        pr._on_in(mido.Message("note_on", channel=1, note=60, velocity=100))
        time.sleep(0.05)
        self.assertEqual(pr.active, {(11, 60): pr.active[(11, 60)]})
        pr.panic()
        pr.stop.set()
        msgs = [m for _, m in pr.hst_out.sent]
        self.assertTrue(pr.bypass)
        self.assertEqual(pr.active, {})
        self.assertEqual(pr.heap, [])  # the 8 s forced-off was dropped, not left to fire later
        offs = [m for m in msgs if m.type == "note_off"]
        self.assertEqual([(m.channel + 1, m.note) for m in offs], [(11, 60)])
        self.assertGreaterEqual(sum(1 for m in msgs if m.type == "control_change"), 48)
        self.assertGreaterEqual(sum(1 for m in [m for _, m in pr.reaper_out.sent] if m.type == "control_change"), 48)

    def test_loop_detection_counts_our_own_note_returning(self):
        pr, time = self._probe(delay_ms=10)
        pr._on_in(mido.Message("note_on", channel=1, note=60, velocity=100))
        time.sleep(0.03)
        pr._on_in(mido.Message("note_on", channel=10, note=60, velocity=100))  # our echo came back
        pr.stop.set()
        self.assertEqual(pr.loop_count, 1)
        self.assertEqual(len([m for _, m in pr.hst_out.sent if m.type == "note_on"]), 1)


if __name__ == "__main__":
    unittest.main()
