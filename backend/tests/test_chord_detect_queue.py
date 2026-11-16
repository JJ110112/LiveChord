"""POST /api/chords/detect must not hold the request (or the uvicorn GIL).

Field report 2026-11-15: skipping through the EDM Jam queue froze the player
~30 s. The old endpoint ran BTC + a melody-to-chord fallback + beat tracking
inside the request, the last two on threads of the uvicorn process; a trivial
/api/config/public took 20 s meanwhile. These tests pin the loose-coupled
contract: the endpoint only enqueues, the status route reports progress, and
the worker writes the chord JSON atomically before flipping to done.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import chord_api  # noqa: E402
import chord_detect_queue as q  # noqa: E402


class _InlinePool:
    """Stands in for the ProcessPoolExecutor: runs the job in-process."""

    def submit(self, fn, *args):
        fut = mock.Mock()
        fut.result.return_value = fn(*args)
        return fut


class TestEndpointOnlyEnqueues(unittest.TestCase):
    def test_post_returns_without_running_detection(self):
        with tempfile.NamedTemporaryFile(suffix=".flac", delete=False) as f:
            audio = f.name
        with mock.patch.object(chord_api, "resolve_path", return_value=audio), \
                mock.patch.object(q, "enqueue", return_value="queued") as enq, \
                mock.patch("chord_detect.detect_chords_and_key_isolated") as btc:
            t0 = time.time()
            out = chord_api.detect_chords_api(path="@1/EDM/song.flac")
            self.assertLess(time.time() - t0, 0.5)
        btc.assert_not_called()
        enq.assert_called_once()
        self.assertEqual(out["status"], "queued")

    def test_status_unknown_job_is_none_not_error(self):
        # After a NUC restart the in-memory record is gone; the player must
        # get a definite answer rather than poll forever.
        self.assertEqual(chord_api.detect_chords_status(path="@1/never/queued.flac")["status"], "none")


class TestWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.chords_dir = Path(self.tmp.name)
        q._jobs.clear()

    def tearDown(self):
        q._jobs.clear()
        self.tmp.cleanup()

    def _run(self, btc_chords):
        import chord_cache
        cf = self.chords_dir / "ab" / "abcd.json"
        with mock.patch("chord_detect.detect_chords_and_key_isolated", return_value=(btc_chords, "C")), \
                mock.patch.object(q, "_get_pool", return_value=_InlinePool()), \
                mock.patch.object(q, "_sub_fallback_and_beats",
                                  side_effect=lambda a, c, k, d: (c, {"bpm": 120.04})), \
                mock.patch.object(chord_cache, "chord_file_for", return_value=cf), \
                mock.patch.object(chord_cache, "ensure_chord_bucket",
                                  side_effect=lambda h: cf.parent.mkdir(parents=True, exist_ok=True)), \
                mock.patch.object(chord_cache, "update_entry_from_file"):
            q._jobs["abcd"] = q._Record(hash="abcd", path="@1/EDM/song.flac", audio_path="x.flac")
            q._process_one("abcd")
        return cf

    def test_done_only_after_chord_json_written(self):
        chords = [{"time": 0.0, "end": 2.0, "chord": "C"}]
        cf = self._run(chords)
        st = q.get_status("abcd")
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["result"]["chord_count"], 1)
        sheet = json.loads(cf.read_text(encoding="utf-8"))
        self.assertEqual(sheet["chords"], chords)
        self.assertEqual(sheet["bpm"], 120.0)
        self.assertFalse(Path(str(cf) + ".tmp").exists())

    def test_failure_is_reported_not_swallowed(self):
        with mock.patch("chord_detect.detect_chords_and_key_isolated", side_effect=RuntimeError("cuda oom")):
            q._jobs["dead"] = q._Record(hash="dead", path="p.flac", audio_path="x.flac")
            q._process_one("dead")
        st = q.get_status("dead")
        self.assertEqual(st["status"], "error")
        self.assertIn("cuda oom", st["error"])

    def test_duplicate_enqueue_while_running(self):
        q._jobs["abcd"] = q._Record(hash="abcd", path="p.flac", audio_path="x.flac", status=q.RUNNING)
        self.assertEqual(q.enqueue("abcd", "p.flac", "x.flac"), "duplicate")


if __name__ == "__main__":
    unittest.main()
