"""Auto-rebuild of jam_library_styles.json after library / chord changes.

Why the coalescing matters: the auto worker analyzes ~20 songs per cycle and
a scan can touch thousands of tracks. Each rebuild is a ~10-30s process, so
one rebuild per burst — and never two at once — is the contract.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import jam_library_refresh as jlr  # noqa: E402


class TestJamLibraryRefresh(unittest.TestCase):
    def setUp(self):
        self._orig = (jlr.QUIET_S, jlr.MIN_GAP_S, jlr._POLL_S, jlr._run_builder)
        jlr.QUIET_S, jlr.MIN_GAP_S, jlr._POLL_S = 0.2, 0.0, 0.02
        jlr._state.update(requested_at=0.0, reason="", last_end=0.0,
                          running=False, thread=None, last_result=None)
        self.runs = []
        self.concurrent = 0
        self.max_concurrent = 0
        self.gate = threading.Event()
        self.gate.set()

        def fake():
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.runs.append(jlr._state["reason"])
            self.gate.wait(2)
            self.concurrent -= 1
            return {"ok": True, "seconds": 0}
        jlr._run_builder = fake

    def tearDown(self):
        self._wait_idle()
        jlr.QUIET_S, jlr.MIN_GAP_S, jlr._POLL_S, jlr._run_builder = self._orig

    def _wait_idle(self, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            t = jlr._state["thread"]
            if t is None or not t.is_alive():
                return
            time.sleep(0.02)
        self.fail("refresh thread did not go idle")

    def test_burst_of_requests_is_one_rebuild(self):
        for _ in range(5):
            jlr.request_rebuild("auto chord detect")
        self._wait_idle()
        self.assertEqual(len(self.runs), 1)
        self.assertTrue(jlr.status()["last_result"]["ok"])

    def test_waits_for_quiet_period(self):
        jlr.request_rebuild("library scan")
        time.sleep(0.05)
        self.assertEqual(self.runs, [])          # still inside QUIET_S
        self._wait_idle()
        self.assertEqual(len(self.runs), 1)

    def test_request_during_a_run_triggers_one_more_never_overlapping(self):
        self.gate.clear()
        jlr.request_rebuild("library scan")
        end = time.time() + 2
        while not jlr._state["running"] and time.time() < end:
            time.sleep(0.01)
        jlr.request_rebuild("chord batch")        # arrives mid-build
        jlr.request_rebuild("chord batch")
        self.gate.set()
        self._wait_idle()
        self.assertEqual(len(self.runs), 2)       # the new chords are not lost
        self.assertEqual(self.max_concurrent, 1)

    def test_min_gap_spaces_runs(self):
        jlr.MIN_GAP_S = 0.5
        jlr.request_rebuild("a")
        self._wait_idle()
        t_first = jlr._state["last_end"]
        jlr.request_rebuild("b")
        self._wait_idle()
        self.assertEqual(len(self.runs), 2)
        self.assertGreaterEqual(jlr._state["last_end"] - t_first, 0.5)

    def test_builder_crash_does_not_stop_future_rebuilds(self):
        def boom():
            raise RuntimeError("disk full")
        jlr._run_builder = boom
        jlr.request_rebuild("a")
        self._wait_idle()
        self.assertFalse(jlr.status()["last_result"]["ok"])
        ok_runs = []
        jlr._run_builder = lambda: ok_runs.append(1) or {"ok": True}
        jlr.request_rebuild("b")
        self._wait_idle()
        self.assertEqual(ok_runs, [1])


if __name__ == "__main__":
    unittest.main()
