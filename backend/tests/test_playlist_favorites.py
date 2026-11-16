"""/api/playlist: favorites source + shuffle ``start``.

The homepage 我的最愛 card gets the same ▶ / 🔀 buttons as Jam music, and the
player's playlist panel has a Random button that reshuffles what comes next
without interrupting the current song. Every player page rebuilds its queue
from the URL (source + seed + start), so these tests pin that the order is
reproducible and that the current song stays first after Random.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import music_api  # noqa: E402
import user_api  # noqa: E402


def _call(**kw):
    args = dict(source="favorites", path="", group_id="", style="", mode="sequential",
                seed="", start="", limit=10000, request=None, authorization=None)
    args.update(kw)
    return music_api.playlist(**args)


class TestFavoritesQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fav = Path(self.tmp.name) / "favorites.json"
        fav.write_text(json.dumps({"favorites": [
            {"path": "@1/Jazz/Z/newest.flac"},       # saved order = newest first
            {"path": "__hash/abc123"},                # processed upload: not path-playable
            {"path": "@1/Classics/A/oldest.flac"},
        ]}), encoding="utf-8")
        lib = [{"path": "@1/Jazz/Z/newest.flac", "title": "Newest Song", "artist": "Z"}]
        self.patches = [
            mock.patch("auth_api.get_current_user", return_value="hitea"),
            mock.patch.object(user_api, "_get_user_file", return_value=fav),
            mock.patch("data_cache.get_library_tracks", return_value=lib),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_sequential_keeps_the_users_order_not_path_order(self):
        # Folder queues sort by path; favorites must play in the order the
        # card shows them, or ▶ on the card starts on a different song.
        out = _call()
        self.assertEqual([t["path"] for t in out["tracks"]],
                         ["@1/Jazz/Z/newest.flac", "@1/Classics/A/oldest.flac"])
        self.assertEqual(out["tracks"][0]["title"], "Newest Song")   # from library cache
        self.assertEqual(out["tracks"][1]["title"], "oldest")        # filename fallback

    def test_hash_favorites_are_left_out(self):
        self.assertNotIn("__hash/abc123", [t["path"] for t in _call()["tracks"]])

    def test_shuffle_is_reproducible_from_the_seed(self):
        a = _call(mode="shuffle", seed="s1")["tracks"]
        b = _call(mode="shuffle", seed="s1")["tracks"]
        self.assertEqual(a, b)


class TestShuffleStart(unittest.TestCase):
    def setUp(self):
        self.tracks = [{"path": f"@1/F/{i:02d}.flac", "title": str(i)} for i in range(30)]
        self.p = mock.patch.object(music_api, "_tracks_for_source",
                                   return_value=("F", list(self.tracks)))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_random_keeps_current_song_first_and_reshuffles_the_rest(self):
        current = "@1/F/17.flac"
        a = _call(source="folder", mode="shuffle", seed="r1", start=current)["tracks"]
        b = _call(source="folder", mode="shuffle", seed="r2", start=current)["tracks"]
        self.assertEqual(a[0]["path"], current)
        self.assertEqual(b[0]["path"], current)
        self.assertNotEqual(a[1:], b[1:])                 # a new seed changes what comes next
        self.assertEqual(sorted(t["path"] for t in a), sorted(t["path"] for t in self.tracks))

    def test_start_is_ignored_in_sequential_mode(self):
        out = _call(source="folder", mode="sequential", start="@1/F/17.flac")
        self.assertEqual(out["tracks"][0]["path"], "@1/F/00.flac")
        self.assertEqual(out["start"], "")

    def test_unknown_start_leaves_the_shuffle_alone(self):
        a = _call(source="folder", mode="shuffle", seed="r1")["tracks"]
        b = _call(source="folder", mode="shuffle", seed="r1", start="@1/gone.flac")["tracks"]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
