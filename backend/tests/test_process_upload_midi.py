import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import process_api  # noqa: E402


class TestProcessUploadMidi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.submitted_job = None
        self._orig = {
            "TMP_DIR": process_api.TMP_DIR,
            "check_quota": process_api.check_quota,
            "generate_job_id": process_api.generate_job_id,
            "submit_job": process_api.submit_job,
            "_convert_midi_to_wav": process_api._convert_midi_to_wav,
        }

        process_api.TMP_DIR = self.root
        process_api.check_quota = lambda _u: True
        process_api.generate_job_id = lambda: "job-midi"

        def _fake_submit(job):
            self.submitted_job = job

        def _fake_convert(src_path: Path, dst_path: Path):
            self.assertTrue(src_path.exists())
            dst_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")

        process_api.submit_job = _fake_submit
        process_api._convert_midi_to_wav = _fake_convert

        app = FastAPI()
        app.include_router(process_api.router)
        app.dependency_overrides[process_api._require_user_facing] = lambda: None
        app.dependency_overrides[process_api.get_user_or_anon] = lambda: "tester"
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for key, value in self._orig.items():
            setattr(process_api, key, value)
        self.tmp.cleanup()

    def test_midi_upload_audio_mid_is_converted_to_wav(self):
        res = self.client.post(
            "/api/process/upload",
            files={"file": ("demo.mid", b"MThd\x00\x00\x00\x06", "audio/mid")},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "queued")
        self.assertIsNotNone(self.submitted_job)
        self.assertTrue(self.submitted_job.audio_path.endswith(".wav"))
        self.assertTrue(Path(self.submitted_job.audio_path).exists())
        self.assertFalse((self.root / "job-midi.mid").exists())

    def test_midi_upload_octet_stream_with_mid_extension_is_accepted(self):
        res = self.client.post(
            "/api/process/upload",
            files={
                "file": (
                    "demo.midi",
                    b"MThd\x00\x00\x00\x06",
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(self.submitted_job)
        self.assertTrue(self.submitted_job.audio_path.endswith(".wav"))

    def test_non_audio_non_midi_upload_is_rejected(self):
        res = self.client.post(
            "/api/process/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("不支援的音檔格式", res.json().get("detail", ""))


class TestMidiConversion(unittest.TestCase):
    def test_convert_midi_to_wav_generates_pcm_file(self):
        try:
            import pretty_midi
        except ImportError:
            self.skipTest("pretty_midi not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            midi_path = root / "sample.mid"
            wav_path = root / "sample.wav"

            pm = pretty_midi.PrettyMIDI()
            inst = pretty_midi.Instrument(program=0)
            inst.notes.append(
                pretty_midi.Note(velocity=96, pitch=60, start=0.0, end=0.5)
            )
            pm.instruments.append(inst)
            pm.write(str(midi_path))

            process_api._convert_midi_to_wav(midi_path, wav_path)
            self.assertTrue(wav_path.exists())
            self.assertGreater(wav_path.stat().st_size, 44)


if __name__ == "__main__":
    unittest.main()
