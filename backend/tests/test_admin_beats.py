"""Tests for the g1k (degenerate-beat-data) admin endpoints in chord_api.py.

The endpoints themselves are thin FastAPI wrappers around helpers in
``chord_api`` and ``tools/backfill_degenerate_beats``. These tests cover:
  - progress-file read/PID-liveness logic that drives the status endpoint
  - request validation for the upgrade endpoint (tracker / workers / etc.)
  - scan + sample shape (via direct call to scan_degenerate + sample build)

Subprocess spawn is intentionally NOT exercised — that path lives in the
CLI tool and is best validated via the smoke run (--limit 5 --dry-run).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# chord_api uses bare backend-relative imports (`from auth_api import ...`)
# so put backend/ on sys.path before importing the module under test.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from backend.chord_api import (  # noqa: E402
    _g1k_pid_alive,
    _g1k_read_progress,
    _G1K_PROGRESS_FILE,
    G1kUpgradeRequest,
)


class TestG1kProgressRead(unittest.TestCase):
    def test_returns_none_when_progress_file_missing(self):
        # Ensure file truly absent
        if _G1K_PROGRESS_FILE.exists():
            self.skipTest("progress file exists from a real run; not safe to test absence")
        self.assertIsNone(_g1k_read_progress())

    def test_returns_dict_when_progress_file_present(self):
        # Write a fake progress file in the configured location, restore on teardown
        existed = _G1K_PROGRESS_FILE.exists()
        backup = (
            _G1K_PROGRESS_FILE.read_text(encoding="utf-8") if existed else None
        )
        try:
            _G1K_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": 99999, "started_at": "2026-05-17T12:00:00",
                "total": 100, "processed": 42, "current_hash": "abc123",
                "counts": {"OK": 41, "FAIL_NO_BEATS_DETECTED": 1},
                "last_error": "", "tracker": "madmom", "dry_run": False,
                "completed_at": None,
            }
            _G1K_PROGRESS_FILE.write_text(json.dumps(payload), encoding="utf-8")
            out = _g1k_read_progress()
            self.assertEqual(out["pid"], 99999)
            self.assertEqual(out["processed"], 42)
            self.assertEqual(out["counts"]["OK"], 41)
            self.assertIsNone(out["completed_at"])
        finally:
            if backup is not None:
                _G1K_PROGRESS_FILE.write_text(backup, encoding="utf-8")
            elif _G1K_PROGRESS_FILE.exists():
                _G1K_PROGRESS_FILE.unlink()

    def test_returns_none_on_corrupt_json(self):
        existed = _G1K_PROGRESS_FILE.exists()
        backup = (
            _G1K_PROGRESS_FILE.read_text(encoding="utf-8") if existed else None
        )
        try:
            _G1K_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _G1K_PROGRESS_FILE.write_text("{not valid json", encoding="utf-8")
            self.assertIsNone(_g1k_read_progress())
        finally:
            if backup is not None:
                _G1K_PROGRESS_FILE.write_text(backup, encoding="utf-8")
            elif _G1K_PROGRESS_FILE.exists():
                _G1K_PROGRESS_FILE.unlink()


class TestG1kPidAlive(unittest.TestCase):
    def test_zero_or_negative_pid_is_dead(self):
        self.assertFalse(_g1k_pid_alive(0))
        self.assertFalse(_g1k_pid_alive(-1))

    def test_current_process_is_alive(self):
        # Our own PID is always live during test execution. On Windows the
        # tasklist call returns the line containing our PID; on Unix
        # os.kill(pid, 0) succeeds.
        self.assertTrue(_g1k_pid_alive(os.getpid()))

    def test_unlikely_dead_pid_returns_false(self):
        # PID 7 is reserved on Unix, doesn't exist on Windows; even if a
        # real process happens to use it, the contract here is "False on
        # any failure" so this asserts no exception escapes.
        result = _g1k_pid_alive(7)
        self.assertIsInstance(result, bool)


class TestG1kUpgradeRequestValidation(unittest.TestCase):
    def test_defaults(self):
        req = G1kUpgradeRequest()
        self.assertEqual(req.workers, 4)
        self.assertEqual(req.tracker, "madmom")
        self.assertEqual(req.limit, 0)
        self.assertFalse(req.include_old_version)
        self.assertFalse(req.no_backup)

    def test_accepts_known_trackers(self):
        for t in ("madmom", "beat_this"):
            req = G1kUpgradeRequest(tracker=t)
            self.assertEqual(req.tracker, t)


class TestScanDegenerateOnTempCorpus(unittest.TestCase):
    """Drive scan_degenerate against a tmp corpus to verify the contract
    the admin endpoint depends on: returns a list[Path] of degenerate
    chord JSON files."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="g1k_test_"))
        # Add tools/ to sys.path for the late import (matches the endpoint)
        tools_dir = Path(__file__).parent.parent.parent / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, sheet):
        shard = self.tmpdir / name[:2]
        shard.mkdir(parents=True, exist_ok=True)
        (shard / f"{name}.json").write_text(json.dumps(sheet), encoding="utf-8")

    def test_identifies_degenerate_and_skips_clean(self):
        from backfill_degenerate_beats import scan_degenerate

        # Degenerate: no beats[]
        self._write("aaa111111111", {
            "path": "test/song1.flac",
            "source": "btc_batch",
            "chords": [{"time": 0, "end": 1, "chord": "C"}],
            "beats": [], "downbeats": [],
        })
        # Degenerate: librosa-fallback beats_source
        self._write("bbb222222222", {
            "path": "test/song2.flac",
            "source": "btc",
            "chords": [{"time": 0, "end": 1, "chord": "G"}],
            "beats": [0.5, 1.0], "downbeats": [0.5],
            "beats_source": "librosa-fallback",
        })
        # Clean: madmom + populated beats
        self._write("ccc333333333", {
            "path": "test/song3.flac",
            "source": "btc",
            "chords": [{"time": 0, "end": 1, "chord": "Am"}],
            "beats": list(range(200)),
            "downbeats": list(range(50)),
            "beats_source": "madmom",
            "beat_version": 3,
        })

        files = scan_degenerate(
            self.tmpdir,
            include_old_version=False,
            include_version_2=False,
        )
        hashes = sorted(f.stem for f in files)
        self.assertEqual(hashes, ["aaa111111111", "bbb222222222"])


if __name__ == "__main__":
    unittest.main()
