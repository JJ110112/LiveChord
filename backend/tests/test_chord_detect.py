import unittest
from unittest import mock

import numpy as np

from backend import chord_detect


class TestChordDetectAudioLoad(unittest.TestCase):
    def test_load_audio_mono_falls_back_to_librosa_when_soundfile_rejects_format(self):
        samples = 22050
        stereo = np.vstack(
            [
                np.linspace(1.0, 3.0, samples, dtype=np.float32),
                np.linspace(3.0, 5.0, samples, dtype=np.float32),
            ]
        )

        with mock.patch.object(
            chord_detect.sf,
            "info",
            side_effect=RuntimeError("Format not recognised"),
        ), mock.patch.object(
            chord_detect.librosa,
            "load",
            return_value=(stereo, 44100),
        ) as load_mock, mock.patch.object(
            chord_detect.librosa,
            "resample",
            side_effect=lambda y, orig_sr, target_sr: y,
        ) as resample_mock:
            y, truncated = chord_detect._load_audio_mono("broken.m4a", 22050)

        load_mock.assert_called_once_with("broken.m4a", sr=None, mono=False)
        resample_mock.assert_called_once()
        self.assertFalse(truncated)
        self.assertEqual(y.dtype, np.float32)
        self.assertEqual(len(y), samples)
        np.testing.assert_allclose(y[:3], np.array([2.0, 2.0000906, 2.0001814], dtype=np.float32), rtol=1e-5, atol=1e-5)

    def test_load_audio_mono_re_raises_soundfile_error_when_fallback_fails(self):
        err = RuntimeError("Format not recognised")
        with mock.patch.object(chord_detect.sf, "info", side_effect=err), mock.patch.object(
            chord_detect.librosa,
            "load",
            side_effect=RuntimeError("audioread failed"),
        ):
            with self.assertRaises(RuntimeError) as raised:
                chord_detect._load_audio_mono("broken.m4a", 22050)

        self.assertIs(raised.exception, err)

    def test_load_model_raises_clear_error_when_btc_weights_are_missing(self):
        with mock.patch.object(chord_detect, "_model", None), mock.patch(
            "os.path.isfile", return_value=False
        ):
            with self.assertRaises(FileNotFoundError) as raised:
                chord_detect._load_model()

        self.assertIn("btc_model_large_voca.pt", str(raised.exception))
        self.assertIn("Restore backend/btc/btc_model_large_voca.pt", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
