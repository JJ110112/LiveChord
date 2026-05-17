"""Tests for sample_probs_at_grid() — Phase 1b sample-at-existing-grid.

The sampler runs the beat_refiner model forward once, then maps each input
timestamp to its nearest sigmoid frame. Length parity with the input arrays
is the load-bearing invariant — these tests pin it together with the
clamping rules at the edges and the NaN/Inf safety net.

Tests mock _extract_sigmoid_frames to avoid loading the actual ~3M-param
checkpoint and decoding audio. The function under test is pure dispatch
over the sigmoid arrays, so mocking the extractor exercises 100% of the
sampling logic.
"""

import math
import unittest
from unittest.mock import patch

import numpy as np

from backend.ai.beat_refiner_infer import sample_probs_at_grid
from backend.ai.beat_refiner_features import FRAMES_PER_SEC


def _make_probs(T: int, fill: float = 0.5):
    """Build a (T,) sigmoid array with a known fill value."""
    arr = np.full(T, fill, dtype=np.float32)
    return arr


class TestSampleProbsAtGrid(unittest.TestCase):
    def test_sample_at_grid_length_matches_input(self):
        T = 200
        beat_arr = _make_probs(T, 0.42)
        db_arr = _make_probs(T, 0.81)
        with patch(
            "backend.ai.beat_refiner_infer._extract_sigmoid_frames",
            return_value=(beat_arr, db_arr, T, ""),
        ):
            res = sample_probs_at_grid("/fake/audio.flac",
                                       beats=[1.0, 2.0, 3.0],
                                       downbeats=[1.0])
        self.assertTrue(res["applied"])
        self.assertEqual(res["reason"], "ok")
        self.assertEqual(res["prob_source"], "sample_at_grid")
        self.assertEqual(len(res["beat_probs"]), 3)
        self.assertEqual(len(res["downbeat_probs"]), 1)
        self.assertEqual(res["n_frames"], T)
        # Each sample reads from a constant-fill array, so the value
        # round-trips through _safe_round.
        for p in res["beat_probs"]:
            self.assertAlmostEqual(p, 0.42, places=2)
        for p in res["downbeat_probs"]:
            self.assertAlmostEqual(p, 0.81, places=2)

    def test_sample_at_grid_negative_timestamp_clamps_to_frame_0(self):
        T = 100
        beat_arr = _make_probs(T, 0.0)
        beat_arr[0] = 0.99  # distinguishable value at frame 0
        db_arr = _make_probs(T, 0.0)
        db_arr[0] = 0.77
        with patch(
            "backend.ai.beat_refiner_infer._extract_sigmoid_frames",
            return_value=(beat_arr, db_arr, T, ""),
        ):
            res = sample_probs_at_grid("/fake/audio.flac",
                                       beats=[-0.5, 0.0, 1.0],
                                       downbeats=[-1.2])
        self.assertTrue(res["applied"])
        # First two timestamps both map to frame 0 → 0.99
        self.assertAlmostEqual(res["beat_probs"][0], 0.99, places=2)
        self.assertAlmostEqual(res["beat_probs"][1], 0.99, places=2)
        # Negative downbeat → frame 0 → 0.77
        self.assertAlmostEqual(res["downbeat_probs"][0], 0.77, places=2)

    def test_sample_at_grid_past_end_clamps_to_last_frame(self):
        T = 100
        beat_arr = _make_probs(T, 0.0)
        beat_arr[-1] = 0.55  # distinguishable at the tail
        db_arr = _make_probs(T, 0.0)
        with patch(
            "backend.ai.beat_refiner_infer._extract_sigmoid_frames",
            return_value=(beat_arr, db_arr, T, ""),
        ):
            res = sample_probs_at_grid("/fake/audio.flac",
                                       beats=[10000.0],
                                       downbeats=[5.0])
        self.assertTrue(res["applied"])
        # 10000s vastly exceeds T/FRAMES_PER_SEC → clamped to last frame
        self.assertAlmostEqual(res["beat_probs"][0], 0.55, places=2)

    def test_sample_at_grid_nan_inf_clamped_to_zero(self):
        T = 100
        beat_arr = _make_probs(T, 0.5)
        # Place NaN at the frame the first beat will hit and +Inf at the
        # second; _safe_round must clamp both to 0.0.
        idx0 = int(round(1.0 * FRAMES_PER_SEC))
        idx1 = int(round(2.0 * FRAMES_PER_SEC))
        beat_arr[idx0] = float("nan")
        beat_arr[idx1] = float("inf")
        db_arr = _make_probs(T, 0.5)
        idx_db = int(round(1.5 * FRAMES_PER_SEC))
        db_arr[idx_db] = float("-inf")
        with patch(
            "backend.ai.beat_refiner_infer._extract_sigmoid_frames",
            return_value=(beat_arr, db_arr, T, ""),
        ):
            res = sample_probs_at_grid("/fake/audio.flac",
                                       beats=[1.0, 2.0],
                                       downbeats=[1.5])
        self.assertTrue(res["applied"])
        for p in res["beat_probs"]:
            self.assertEqual(p, 0.0)
        for p in res["downbeat_probs"]:
            self.assertEqual(p, 0.0)
        # Output values must be finite — protects downstream JSON
        # serialization, where NaN/Inf would either crash or serialize as
        # non-standard tokens.
        for p in res["beat_probs"] + res["downbeat_probs"]:
            self.assertTrue(math.isfinite(p))

    def test_sample_at_grid_empty_beats_bails(self):
        # Early bail — no model load, no audio decode. Should not even call
        # _extract_sigmoid_frames.
        with patch(
            "backend.ai.beat_refiner_infer._extract_sigmoid_frames",
        ) as extract_mock:
            res = sample_probs_at_grid("/fake/audio.flac",
                                       beats=[], downbeats=[1.0])
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "no-input-beats")
        self.assertEqual(res["beat_probs"], [])
        self.assertEqual(res["downbeat_probs"], [])
        extract_mock.assert_not_called()

    def test_sample_at_grid_propagates_extraction_failure(self):
        # Extractor bails (no model / audio decode fails / inference throws);
        # the sampler must propagate the reason cleanly with applied=False
        # and empty arrays — never raise across the boundary.
        with patch(
            "backend.ai.beat_refiner_infer._extract_sigmoid_frames",
            return_value=(None, None, 0, "no-model"),
        ):
            res = sample_probs_at_grid("/fake/audio.flac",
                                       beats=[1.0, 2.0],
                                       downbeats=[1.0])
        self.assertFalse(res["applied"])
        self.assertEqual(res["reason"], "no-model")
        self.assertEqual(res["beat_probs"], [])
        self.assertEqual(res["downbeat_probs"], [])
        self.assertEqual(res["n_frames"], 0)


if __name__ == "__main__":
    unittest.main()
