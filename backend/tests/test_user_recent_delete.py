import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import user_api  # noqa: E402


class TestUserRecentDelete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._orig_data_dir = user_api.DATA_DIR
        user_api.DATA_DIR = self.root

        app = FastAPI()
        app.include_router(user_api.router)
        app.dependency_overrides[user_api.get_current_user] = lambda: "alice"
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        user_api.DATA_DIR = self._orig_data_dir
        self.tmp.cleanup()

    def test_delete_recent_record_keeps_matching_favorite(self):
        user_dir = self.root / "users" / "alice"
        user_dir.mkdir(parents=True, exist_ok=True)
        demo_path = "__hash/demo123"
        (user_dir / "recent.json").write_text(
            json.dumps(
                {
                    "recent": [
                        {"path": demo_path, "title": "Demo Song"},
                        {"path": "library/song.flac"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (user_dir / "favorites.json").write_text(
            json.dumps({"favorites": [{"path": demo_path}]}),
            encoding="utf-8",
        )

        res = self.client.delete("/api/recent", params={"path": demo_path})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"ok": True})

        recent = json.loads((user_dir / "recent.json").read_text(encoding="utf-8"))[
            "recent"
        ]
        favorites = json.loads(
            (user_dir / "favorites.json").read_text(encoding="utf-8")
        )["favorites"]
        self.assertEqual([r["path"] for r in recent], ["library/song.flac"])
        self.assertEqual([f["path"] for f in favorites], [demo_path])


if __name__ == "__main__":
    unittest.main()
