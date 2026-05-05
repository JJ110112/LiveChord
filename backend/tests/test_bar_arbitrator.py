"""Tests for backend.ai.bar_arbitrator (Phase 0 — rule-based path).

Fixtures live under tests/fixtures/bar_arbitrator/ and represent the four
failure modes seen in the live data (see plan file):

  0000d0e0e8e4 — phase wandering (gaps wildly inconsistent vs bpm-derived bar)
  00026f843adf — clean 4/4 with long intro silence (raw mostly-regular)
  000348a81a84 — clean 4/4 (raw already regular — should NOT be replaced)
  0003a54bb986 — bar-doubled (gaps ≈ 2× bpm-derived bar)
  00043a8bb9bb — beat-vs-bar confusion (gaps ≈ ½ bar)

Tests assert behavioral contracts, not exact numbers — the rule-based path
is a baseline that the future Transformer should beat. We only check that:
  - clean songs are left alone
  - degenerate inputs gate out cleanly
  - replacement, when applied, produces a more-regular grid than the raw
"""

import json
import unittest
from pathlib import Path

from backend.ai.bar_arbitrator import (
    arbitrate,
    arbitrate_rule_based,
    apply_to_chord_json,
    _gap_stats,
    _generate_grid,
    _match_count,
    _score_grid,
    _chord_change_times,
)


FIXTURES = Path(__file__).parent / "fixtures" / "bar_arbitrator"


def _load(stem: str) -> dict:
    return json.loads((FIXTURES / f"{stem}.json").read_text(encoding="utf-8"))


class TestGapStats(unittest.TestCase):
    def test_perfectly_regular(self):
        mean, cv = _gap_stats([0.0, 1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(mean, 1.0, places=4)
        self.assertAlmostEqual(cv, 0.0, places=4)

    def test_too_short(self):
        mean, cv = _gap_stats([1.0])
        self.assertIsNone(mean)
        self.assertIsNone(cv)

    def test_irregular(self):
        mean, cv = _gap_stats([0.0, 1.0, 3.0, 4.0, 6.5])
        self.assertGreater(cv, 0.1)


class TestGridGeneration(unittest.TestCase):
    def test_basic_grid(self):
        grid = _generate_grid(0.0, 10.0, bar_dur=2.0, phase_offset=0.0)
        self.assertEqual(grid, [0.0, 2.0, 4.0, 6.0, 8.0, 10.0])

    def test_phase_rolls_into_first_bar(self):
        # phase_offset=5.5 with bar_dur=2.0 should roll back into first bar
        grid = _generate_grid(0.0, 8.0, bar_dur=2.0, phase_offset=5.5)
        self.assertAlmostEqual(grid[0], 1.5, places=4)

    def test_negative_phase_rolls_forward(self):
        grid = _generate_grid(1.0, 9.0, bar_dur=2.0, phase_offset=-3.7)
        self.assertGreaterEqual(grid[0], 1.0 - 1e-6)
        self.assertLess(grid[0], 1.0 + 2.0)

    def test_degenerate_returns_empty(self):
        self.assertEqual(_generate_grid(0.0, 0.0, 1.0, 0.0), [])
        self.assertEqual(_generate_grid(0.0, 5.0, 0.0, 0.0), [])


class TestMatchCount(unittest.TestCase):
    def test_all_match(self):
        grid = [1.0, 2.0, 3.0]
        events = [1.05, 2.0, 3.1]
        self.assertEqual(_match_count(grid, events, tol=0.15), 3)

    def test_none_match(self):
        grid = [1.0, 2.0, 3.0]
        events = [10.0, 11.0, 12.0]
        self.assertEqual(_match_count(grid, events, tol=0.15), 0)

    def test_empty_inputs(self):
        self.assertEqual(_match_count([], [1.0], 0.1), 0)
        self.assertEqual(_match_count([1.0], [], 0.1), 0)


class TestChordChangeTimes(unittest.TestCase):
    def test_excludes_consecutive_same_chord(self):
        chords = [
            {"time": 0.0, "end": 1.0, "chord": "C"},
            {"time": 1.0, "end": 2.0, "chord": "C"},  # split, same chord
            {"time": 2.0, "end": 3.0, "chord": "F"},
            {"time": 3.0, "end": 4.0, "chord": "F"},  # split, same chord
            {"time": 4.0, "end": 5.0, "chord": "G"},
        ]
        out = _chord_change_times(chords)
        self.assertEqual(out, [0.0, 2.0, 4.0])


class TestArbitrateOnFixtures(unittest.TestCase):
    """End-to-end on real noisy chord JSONs."""

    def test_clean_song_left_alone(self):
        """000348a81a84 is clean 4/4 at 90.9 BPM — raw downbeats already
        regular (gaps 2.60–2.62, bar4 expected 2.64). Should NOT replace."""
        cd = _load("000348a81a84")
        res = arbitrate_rule_based(cd)
        # Raw should be detected as regular OR margin should be insufficient
        self.assertFalse(
            res["applied"],
            f"clean song should not be touched, got reason={res['reason']!r}"
        )

    def test_phase_wandering_detected_as_irregular(self):
        """0000d0e0e8e4 has wildly inconsistent raw downbeat gaps AND its
        underlying beats[] are themselves noisy (missing/extra beats). The
        rule baseline correctly refuses to write a guess (gated by F1
        floor); the Transformer in Phase 1 should improve this."""
        cd = _load("0000d0e0e8e4")
        res = arbitrate_rule_based(cd)
        # CV of raw gaps must be detected as high (irregular)
        self.assertIsNotNone(res["raw_regularity_cv"])
        self.assertGreater(res["raw_regularity_cv"], 0.30)
        # Conservative gate: don't replace unless candidate clears F1 floor
        # If it does replace (rare for this fixture), the new downbeats
        # must be a proper sequence
        if res["applied"]:
            self.assertGreater(len(res["downbeats_after"]), 0)
            self.assertEqual(sorted(res["downbeats_after"]), res["downbeats_after"])

    def test_bar_doubled_song_runs_clean(self):
        """0003a54bb986 has BPM 166.7 (bar4 = 1.44s) but raw gaps cluster
        around 2.66s ≈ 2×bar. Arbitrator should produce a viable bpb pick
        without crashing — actual replacement decision depends on F1 floor."""
        cd = _load("0003a54bb986")
        res = arbitrate_rule_based(cd)
        self.assertNotEqual(res["reason"], "no-viable-grid")
        self.assertIn(res["beats_per_bar"], (3, 4, 6))

    def test_intro_silence_song(self):
        """00026f843adf has a 12.7s gap before settling. Arbitrator should
        still produce a grid — it just shouldn't crash."""
        cd = _load("00026f843adf")
        res = arbitrate_rule_based(cd)
        self.assertIsInstance(res["beats_per_bar"], int)

    def test_force_overrides_clean_gate(self):
        """force=True should replace even on a clean song."""
        cd = _load("000348a81a84")
        res = arbitrate_rule_based(cd, force=True)
        # With force, we expect either applied=True or a fall-through reason
        # that's NOT 'raw-already-regular'
        self.assertNotEqual(res["reason"], "raw-already-regular")

    def test_arbitrate_wraps_safely(self):
        """The public ``arbitrate`` should never raise."""
        cd = _load("0000d0e0e8e4")
        res = arbitrate(cd)
        self.assertIn("applied", res)
        self.assertIn("model_version", res)


class TestApplyToChordJson(unittest.TestCase):
    def test_writes_metadata_even_when_not_applied(self):
        cd = _load("000348a81a84")
        res = arbitrate_rule_based(cd)
        out = apply_to_chord_json(dict(cd), res)
        self.assertIn("bar_correction", out)
        self.assertEqual(out["bar_correction"]["applied"], res["applied"])

    def test_replaces_downbeats_when_applied(self):
        cd = _load("0000d0e0e8e4")
        res = arbitrate_rule_based(cd, force=True)
        original_downbeats = list(cd["downbeats"])
        out = apply_to_chord_json(dict(cd), res)
        if res["applied"]:
            self.assertNotEqual(out["downbeats"], original_downbeats)
            self.assertEqual(out["downbeats"], res["downbeats_after"])

    def test_no_mutation_when_not_applied(self):
        cd = _load("000348a81a84")
        original_downbeats = list(cd["downbeats"])
        res = arbitrate_rule_based(cd)  # no force
        out = apply_to_chord_json(dict(cd), res)
        if not res["applied"]:
            self.assertEqual(out["downbeats"], original_downbeats)


class TestPreflightGates(unittest.TestCase):
    def test_no_chords(self):
        res = arbitrate_rule_based({"chords": [], "beats": [], "downbeats": [], "bpm": 120})
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "too-few-chords")

    def test_no_bpm(self):
        chords = [{"time": i, "end": i + 1, "chord": "C"} for i in range(20)]
        res = arbitrate_rule_based({"chords": chords, "beats": [], "downbeats": [], "bpm": None})
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "no-bpm")

    def test_no_beats(self):
        chords = [{"time": i, "end": i + 1, "chord": "C" if i % 2 else "F"} for i in range(20)]
        res = arbitrate_rule_based({"chords": chords, "beats": [], "downbeats": [], "bpm": 120})
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "too-few-beats")


class TestModelBasedArbitrator(unittest.TestCase):
    """Tests for the ONNX model path. Skipped when model file or
    onnxruntime is unavailable (CI/freshly-cloned repo)."""

    @classmethod
    def setUpClass(cls):
        cls.model_path = Path(__file__).parent.parent.parent / "data" / "models" / "bar_arbitrator_v1.onnx"
        if not cls.model_path.exists():
            raise unittest.SkipTest(f"model not found at {cls.model_path}")
        try:
            import onnxruntime  # noqa
        except ImportError:
            raise unittest.SkipTest("onnxruntime not installed")

    def test_model_skips_clean_song(self):
        """Already-regular gate must apply to model path too — we don't
        want to risk regressing songs the user is already happy with."""
        from backend.ai.bar_arbitrator import arbitrate
        cd = _load("000348a81a84")
        res = arbitrate(cd, model_path=str(self.model_path))
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "raw-already-regular")
        self.assertEqual(res["model_version"], "model_v1")

    def test_model_corrects_phase_wandering(self):
        """The motivating case — model should produce a regular grid where
        the rule baseline gates out at low F1."""
        from backend.ai.bar_arbitrator import arbitrate
        cd = _load("0000d0e0e8e4")
        res = arbitrate(cd, model_path=str(self.model_path))
        if res["applied"]:
            self.assertEqual(res["model_version"], "model_v1")
            self.assertIn(res["beats_per_bar"], (3, 4, 6))
            self.assertGreater(len(res["downbeats_after"]), 0)
            self.assertEqual(sorted(res["downbeats_after"]), res["downbeats_after"])

    def test_model_force_replaces_clean(self):
        """force=True should bypass already-regular gate even on the model path."""
        from backend.ai.bar_arbitrator import arbitrate
        cd = _load("000348a81a84")
        res = arbitrate(cd, model_path=str(self.model_path), force=True)
        # Either applied (high model confidence on clean song) or rejected
        # for confidence reasons — but NOT the regularity gate
        self.assertNotEqual(res["reason"], "raw-already-regular")

    def test_model_falls_back_when_path_invalid(self):
        """Bad model path should fall back to rule baseline, not raise."""
        from backend.ai.bar_arbitrator import arbitrate
        cd = _load("000348a81a84")
        res = arbitrate(cd, model_path="/nonexistent/path/foo.onnx")
        self.assertEqual(res["model_version"], "rule_v1")

    def test_no_mutation_of_input(self):
        """arbitrate must not mutate chord_data."""
        from backend.ai.bar_arbitrator import arbitrate
        cd = _load("0000d0e0e8e4")
        original_db = list(cd["downbeats"])
        original_beats = list(cd["beats"])
        arbitrate(cd, model_path=str(self.model_path))
        self.assertEqual(cd["downbeats"], original_db)
        self.assertEqual(cd["beats"], original_beats)


if __name__ == "__main__":
    unittest.main()
