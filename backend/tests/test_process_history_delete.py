import hashlib
import json
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
import process_queue  # noqa: E402


class TestProcessHistoryDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._orig = {
            "DATA_DIR": process_queue.DATA_DIR,
            "CHORDS_DIR": process_queue.CHORDS_DIR,
            "COVERS_DIR": process_queue.COVERS_DIR,
            "MELODIES_DIR": process_queue.MELODIES_DIR,
            "ACCOMP_DIR": process_queue.ACCOMP_DIR,
            "TMP_DIR": process_queue.TMP_DIR,
            "AUDIT_DB_PATH": process_queue.AUDIT_DB_PATH,
            "chord_file_for": process_queue.chord_file_for,
        }

        process_queue.DATA_DIR = self.root
        process_queue.CHORDS_DIR = self.root / "chords"
        process_queue.COVERS_DIR = self.root / "covers"
        process_queue.MELODIES_DIR = self.root / "melodies"
        process_queue.ACCOMP_DIR = self.root / "accompaniments"
        process_queue.TMP_DIR = self.root / "tmp"
        process_queue.AUDIT_DB_PATH = self.root / "audit.db"
        for directory in (
            process_queue.CHORDS_DIR,
            process_queue.COVERS_DIR,
            process_queue.MELODIES_DIR,
            process_queue.ACCOMP_DIR,
            process_queue.TMP_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        process_queue.chord_file_for = (
            lambda h: process_queue.CHORDS_DIR / h[:2] / f"{h}.json"
        )
        process_queue._init_audit_db()

        app = FastAPI()
        app.include_router(process_api.router)
        app.dependency_overrides[process_api._require_user_facing] = lambda: None
        app.dependency_overrides[process_api.get_user_or_anon] = (
            lambda: "anon_guest123"
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for key, value in self._orig.items():
            setattr(process_queue, key, value)
        self.tmp.cleanup()

    def _seed_done_upload(self, username="anon_guest123"):
        job_id = "job-owned"
        result_hash = hashlib.md5(f"__upload/{job_id}".encode("utf-8")).hexdigest()[:12]

        chord_path = process_queue.chord_file_for(result_hash)
        paths = [
            chord_path,
            chord_path.parent / f"{result_hash}.json.bak.beat_this",
            process_queue.COVERS_DIR / f"{result_hash}.jpg",
            process_queue.MELODIES_DIR / f"{result_hash}.json",
            process_queue.ACCOMP_DIR / f"{result_hash}_Arpeggio_L1_default_piano_v7.json",
            self.root / "users" / username / "chords" / f"{result_hash}.json",
            self.root / "users" / username / "human_sections" / f"{result_hash}.json",
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

        for user in (username, "bob"):
            user_dir = self.root / "users" / user
            user_dir.mkdir(parents=True, exist_ok=True)
            (user_dir / "recent.json").write_text(
                json.dumps(
                    {
                        "recent": [
                            {"path": f"__hash/{result_hash}"},
                            {"path": "library/song.flac"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (user_dir / "favorites.json").write_text(
                json.dumps({"favorites": [{"path": f"__hash/{result_hash}"}]}),
                encoding="utf-8",
            )

        with process_queue._audit_conn() as conn:
            conn.execute(
                """INSERT INTO process_audit
                   (job_id, username, source_type, title, status, chord_count,
                    created_at, completed_at, result_hash)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    username,
                    "upload",
                    "Owned Song",
                    "done",
                    3,
                    "2026-05-18T09:00:00",
                    "2026-05-18T09:01:00",
                    "",
                ),
            )
            entry_id = conn.execute("SELECT id FROM process_audit").fetchone()[0]

        return entry_id, result_hash, paths

    def test_anon_identity_can_list_and_delete_own_analysis(self):
        entry_id, result_hash, paths = self._seed_done_upload()

        history = self.client.get("/api/process/my-history?limit=5")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["history"][0]["id"], entry_id)
        self.assertEqual(history.json()["history"][0]["result_hash"], result_hash)

        deleted = self.client.delete(f"/api/process/my-history/{entry_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["result_hash"], result_hash)

        for path in paths:
            self.assertFalse(path.exists(), f"artifact still exists: {path}")
        for user in ("anon_guest123", "bob"):
            recent = json.loads(
                (self.root / "users" / user / "recent.json").read_text(
                    encoding="utf-8"
                )
            )["recent"]
            favorites = json.loads(
                (self.root / "users" / user / "favorites.json").read_text(
                    encoding="utf-8"
                )
            )["favorites"]
            self.assertNotIn(f"__hash/{result_hash}", [r.get("path") for r in recent])
            self.assertNotIn(
                f"__hash/{result_hash}", [r.get("path") for r in favorites]
            )

    def test_wrong_owner_cannot_delete_analysis(self):
        entry_id, result_hash, _paths = self._seed_done_upload()

        self.assertIsNone(process_queue.delete_user_audit_entry(entry_id, "bob"))
        self.assertTrue(process_queue.chord_file_for(result_hash).is_file())


if __name__ == "__main__":
    unittest.main()
