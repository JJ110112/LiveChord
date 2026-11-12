"""Library-wide Jam styles: Christmas supplement + 器樂 buckets.

The curated Christmas bucket in jam_tracks.json only samples the top-level
Christmas/ folder, so Christmas pieces filed under Classics / Relax / Jazz
(Messiah, Libera, Beegie Adair …) never reached the playlist. These tests pin
the keyword rules against the real false positives found in the library and
the merge contract the homepage + player queue rely on.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import build_jam_library_styles as builder  # noqa: E402
import jam_tracks_api  # noqa: E402
from chord_cache import song_hash  # noqa: E402


def _t(path, title="", album=""):
    return {"path": path, "title": title, "album": album, "duration": 180}


class TestClassify(unittest.TestCase):
    def test_christmas_outside_christmas_folder(self):
        # The whole point of the supplement: classical / crossover Christmas
        # pieces that live outside the Christmas/ folder.
        for path in (
            "@1/Classics/Pavarotti/Classical Christmas Songs/Handel Messiah, HWV 56 Pt. 1 Comfort ye.flac",
            "@1/Relax/Libera/Libera - Angels Sing/The Wexford Carol.flac",
            "@1/Jazz/Al Di Meola/Winter Nights/Carol Of The Bells.flac",
            "@1/Relax/Celtic Woman/O Holy Night.flac",
        ):
            self.assertIn(builder.CHRISTMAS, builder.classify(_t(path)), path)

    def test_christmas_folder_counts_even_without_keyword(self):
        tags = builder.classify(_t("@1/Christmas/K-Pop Christmas/24 to 25.flac"))
        self.assertIn(builder.CHRISTMAS, tags)

    def test_known_false_positives_are_not_christmas(self):
        # Each of these matched a naive keyword list in the real library.
        for path in (
            "@1/Other/Soundtracks/FF7/Sign (From Final Fantasy VII Advent Children).flac",
            "@1/Jazz/Carol Albert/Carol Albert - Paradigm Shift/Paradigm Shift.flac",
            "@1/POP/E-POP/Yanni/Heart of Midnight/Carol's Theme.flac",
            "@1/POP/J-POP/Yama/Hitoshizuku - Mistletoe -Kamigami no yadorigi/Memories.flac",
            "@1/Other/Heavy Metal/Symphony X - Iconoclast/Electric Messiah.flac",
            "@1/Relax/K-Drama OST/Lee Sang Gon(NOEL) _ My love is hurt.flac",
        ):
            self.assertNotIn(builder.CHRISTMAS, builder.classify(_t(path)), path)

    def test_instrument_buckets_and_all(self):
        tags = builder.classify(_t("@1/Relax/Kyle Landry/Works for Piano, Vol. III/Canon Variations.flac"))
        self.assertIn("器樂(鋼琴)", tags)
        self.assertIn(builder.INSTRUMENTAL_ALL, tags)
        guitar = builder.classify(_t("@1/Relax/Manuel Granada/Relaxing Spanish Guitar/Dust in the Wind.flac"))
        self.assertIn("器樂(吉他)", guitar)

    def test_harp_bucket_skips_sharp_and_harpsichord(self):
        harp = builder.classify(_t("@1/Classics/Magdalena Hoffmann/Nightscapes/Britten Suite for Harp, Op. 83.flac"))
        self.assertIn("器樂(豎琴)", harp)
        french = builder.classify(_t("@1/Classics/Anaëlle Tourret/Divertissements pour harpe II.flac"))
        self.assertIn("器樂(豎琴)", french)
        # The same étude played on a harp still counts (matched via the folder).
        arranged = builder.classify(_t('@1/Classics/Soothing Classical Harp/Etude Op. 25, No. 1 "Aeolian Harp".flac'))
        self.assertIn("器樂(豎琴)", arranged)
        # "sharp" is in hundreds of key names; harpsichord is its own instrument.
        for path in (
            "@1/Classics/Bach/Prelude in C sharp minor (Piano).flac",
            "@1/Classics/Bach Harpsichord/Goldberg Variations Aria (Harpsichord).flac",
            # Chopin's piano étude nicknames — the library has several piano recordings
            "@1/Classics/Yunchan Lim/Chopin Études/Op. 25 No. 1 in A-Flat Major Aeolian Harp.flac",
            "@1/Classics/Maurizio Pollini/Chopin 12 Études, Op. 25 No. 1 in A-Flat Major Harp Study.flac",
        ):
            self.assertNotIn("器樂(豎琴)", builder.classify(_t(path)), path)

    def test_violin_has_its_own_bucket_and_stays_in_strings(self):
        tags = builder.classify(_t("@1/Classics/Hilary Hahn/Paganini 24 Caprices for Solo Violin, Op. 1.flac"))
        self.assertIn("器樂(小提琴)", tags)
        self.assertIn("器樂(弦樂)", tags)
        # A cello piece must not land in the violin list.
        cello = builder.classify(_t("@1/Classics/Bach/Suite for Solo Violoncello No. 1 (cello).flac"))
        self.assertNotIn("器樂(小提琴)", cello)

    def test_accordion_bucket_includes_bandoneon(self):
        for path in (
            "@1/Relax/Accordeon/The Greatest Accordeon Hits/Olé Guapa.flac",
            "@1/Relax/Ksenija Sidorova/Accordion Recital/Libertango.flac",
            "@1/Classics/Ophélie Gaillard/CelloTango/Desde el alma (Arr. for Cello and Bandoneón).flac",
        ):
            self.assertIn("器樂(手風琴)", builder.classify(_t(path)), path)

    def test_sung_tracks_never_enter_instrumental(self):
        # An opera aria with an orchestra is still a vocal track.
        tags = builder.classify(_t("@1/Classics/Callas/Orchestra Del Teatro Alla Scala/Aria Casta Diva.flac"))
        self.assertNotIn(builder.INSTRUMENTAL_ALL, tags)
        self.assertNotIn("器樂(管弦樂)", tags)

    def test_symphony_x_is_not_orchestral(self):
        tags = builder.classify(_t("@1/Other/Heavy Metal/Symphony X - Live/The Bird-Serpent War.flac"))
        self.assertNotIn("器樂(管弦樂)", tags)

    def test_instrumental_christmas_lands_in_both(self):
        tags = builder.classify(_t("@1/Christmas/Christmas Peacful Piano/Last Christmas.flac"))
        self.assertIn(builder.CHRISTMAS, tags)
        self.assertIn("器樂(鋼琴)", tags)


class TestBuild(unittest.TestCase):
    def test_only_analyzed_songs_are_included(self):
        # A playlist entry without a chord JSON would open an empty player.
        ready = _t("@1/Relax/Piano Peace/Morning.flac")
        unready = _t("@1/Relax/Piano Peace/Evening.flac")
        out = builder.build([ready, unready], {song_hash(ready["path"])})
        paths = [r["path"] for r in out["styles"]["器樂(鋼琴)"]]
        self.assertEqual(paths, [ready["path"]])
        self.assertEqual(out["counts"]["器樂(鋼琴)"], 1)


class TestMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (jam_tracks_api.JAM_TRACKS_FILE, jam_tracks_api.LIBRARY_STYLES_FILE)
        jam_tracks_api.JAM_TRACKS_FILE = root / "jam_tracks.json"
        jam_tracks_api.LIBRARY_STYLES_FILE = root / "jam_library_styles.json"
        jam_tracks_api._cache.update(mtime=0, data=None)
        jam_tracks_api._lib_styles_cache.update(mtime=0, data={})
        curated = [
            {"path": "@1/Christmas/A.flac", "style": "Christmas", "score": 0.9},
            {"path": "@1/Jazz/J.flac", "style": "Jazz", "score": 0.8},
        ]
        jam_tracks_api.JAM_TRACKS_FILE.write_text(json.dumps({
            "total": 2, "target": 2, "by_style": {"Christmas": 1, "Jazz": 1}, "tracks": curated,
        }), encoding="utf-8")
        jam_tracks_api.LIBRARY_STYLES_FILE.write_text(json.dumps({"styles": {
            "Christmas": [
                {"path": "@1/Christmas/A.flac", "style": "Christmas"},       # duplicate of curated
                {"path": "@1/Classics/Messiah.flac", "style": "Christmas"},
                {"path": "@1/Relax/Libera/Carol.flac", "style": "Christmas"},
            ],
            "器樂(全)": [{"path": "@1/Relax/P.flac", "style": "器樂(全)"}],
            "器樂(吉他)": [],
        }}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        jam_tracks_api.JAM_TRACKS_FILE, jam_tracks_api.LIBRARY_STYLES_FILE = self._orig
        jam_tracks_api._cache.update(mtime=0, data=None)
        jam_tracks_api._lib_styles_cache.update(mtime=0, data={})
        self.tmp.cleanup()

    def test_christmas_keeps_curated_first_and_adds_library(self):
        paths = [t["path"] for t in jam_tracks_api.jam_style_tracks("Christmas")]
        self.assertEqual(paths, [
            "@1/Christmas/A.flac", "@1/Classics/Messiah.flac", "@1/Relax/Libera/Carol.flac",
        ])

    def test_styles_endpoint_counts_and_order(self):
        styles = jam_tracks_api.jam_tracks_styles()["styles"]
        names = [s["name"] for s in styles]
        counts = {s["name"]: s["count"] for s in styles}
        self.assertEqual(counts["Christmas"], 3)          # deduped union, not 1 + 3
        self.assertEqual(names[0], "Christmas")           # re-sorted by the merged size
        self.assertEqual(names[-1], "器樂(全)")             # library-only styles follow curated
        self.assertNotIn("器樂(吉他)", names)               # empty bucket hidden

    def test_player_queue_uses_the_same_merged_list(self):
        # The player rebuilds its queue through /api/playlist?source=jam —
        # it must see the same songs the homepage list showed.
        import music_api
        _label, tracks = music_api._tracks_for_source("jam", "", "", "器樂(全)")
        self.assertEqual([t["path"] for t in tracks], ["@1/Relax/P.flac"])

    def test_missing_library_file_falls_back_to_curated(self):
        jam_tracks_api.LIBRARY_STYLES_FILE.unlink()
        paths = [t["path"] for t in jam_tracks_api.jam_style_tracks("Christmas")]
        self.assertEqual(paths, ["@1/Christmas/A.flac"])


if __name__ == "__main__":
    unittest.main()
