"""OMR pipeline tests: MusicXML parsing, page rendering, job flow, API pre-flight.

The OMR engines themselves (homr / oemer) are not exercised here — they are
optional heavy installs. ``run_engine`` is stubbed so the rest of the pipeline
is verified without them.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import omr_processor  # noqa: E402
import score_parser  # noqa: E402

# Grand staff, divisions=2 (quarter = 2), 3/4, tempo 90 → 60 mid-bar 2.
#  m1 RH: [C5+E5] quarter, D5 half tied → m2 D5 quarter, rest half
#  m1 LH: C3 dotted half          m2 LH: G2 quarter, forward
PIANO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Fixture</work-title></work>
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>2</divisions><key><fifths>0</fifths></key>
        <time><beats>3</beats><beat-type>4</beat-type></time><staves>2</staves>
        <clef number="1"><sign>G</sign></clef><clef number="2"><sign>F</sign></clef></attributes>
      <direction><sound tempo="90"/></direction>
      <note><pitch><step>C</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><staff>1</staff></note>
      <note><chord/><pitch><step>E</step><octave>5</octave></pitch><duration>2</duration><voice>1</voice><staff>1</staff></note>
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><tie type="start"/><voice>1</voice><staff>1</staff></note>
      <backup><duration>6</duration></backup>
      <note><pitch><step>C</step><octave>3</octave></pitch><duration>6</duration><voice>5</voice><staff>2</staff></note>
    </measure>
    <measure number="2">
      <note><pitch><step>D</step><octave>5</octave></pitch><duration>2</duration><tie type="stop"/><voice>1</voice><staff>1</staff></note>
      <direction><sound tempo="60"/></direction>
      <note><rest/><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <backup><duration>6</duration></backup>
      <note><pitch><step>G</step><octave>2</octave></pitch><duration>2</duration><voice>5</voice><staff>2</staff></note>
      <forward><duration>4</duration></forward>
    </measure>
  </part>
</score-partwise>
"""

# 4/4 with a one-beat pickup, no tempo marking, single treble staff.
PICKUP_XML = """<?xml version="1.0"?>
<score-partwise>
  <part-list><score-part id="P1"/></part-list>
  <part id="P1">
    <measure number="0" implicit="yes">
      <attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign></clef></attributes>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration></note>
    </measure>
    <measure number="1">
      <note><pitch><step>C</step><alter>1</alter><octave>5</octave></pitch><duration>4</duration></note>
    </measure>
  </part>
</score-partwise>
"""


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p


class TestScoreParser(_TmpDir):
    def setUp(self):
        super().setUp()
        self.score = score_parser.parse_musicxml(self.write("piano.musicxml", PIANO_XML))

    def _notes(self, name):
        return [n for n in self.score["notes"] if n["name"] == name]

    def test_tied_note_is_one_held_key(self):
        # The waterfall must show D5 as one 3-beat bar, not a re-struck key at the barline.
        d5 = self._notes("D5")
        self.assertEqual(len(d5), 1)
        self.assertAlmostEqual(d5[0]["quarter_duration"], 3.0)

    def test_chord_notes_share_onset(self):
        # <chord/> notes are simultaneous; chord detection depends on that.
        self.assertEqual(self._notes("C5")[0]["time"], self._notes("E5")[0]["time"])
        self.assertAlmostEqual(self._notes("D5")[0]["quarter_time"], 1.0)

    def test_grand_staff_splits_hands(self):
        self.assertEqual({n["name"] for n in self.score["left_hand"]}, {"C3", "G2"})
        self.assertEqual({n["name"] for n in self.score["right_hand"]}, {"C5", "E5", "D5"})
        self.assertTrue(all(n["voice_lane"] == "lh_score" for n in self.score["left_hand"]))

    def test_backup_puts_lh_at_bar_start(self):
        # After <backup>, LH notes start where the bar starts, not after the RH notes.
        self.assertEqual(self._notes("C3")[0]["time"], 0.0)
        self.assertAlmostEqual(self._notes("G2")[0]["time"], 2.0, places=3)  # 3 q @ 90 bpm

    def test_tempo_change_only_affects_later_time(self):
        # tempo 60 sits after bar 2's first quarter: q 0-4 at 90 bpm (2.667 s), q 4-6 at 60 (+2 s).
        self.assertAlmostEqual(self.score["duration"], 8 / 3 + 2.0, places=3)
        self.assertAlmostEqual(self.score["tempo_map"][1]["quarter"], 4.0)
        self.assertEqual([t["bpm"] for t in self.score["tempo_map"]], [90.0, 60.0])
        self.assertAlmostEqual(self._notes("D5")[0]["duration"], 2.0, places=3)  # 3 q @ 90
        self.assertEqual(self.score["tempo_source"], "score")

    def test_bar_grid_matches_meter(self):
        self.assertEqual(self.score["time_signature"], "3/4")
        self.assertEqual(len(self.score["measures"]), 2)
        self.assertEqual(len(self.score["beats"]), 6)
        self.assertAlmostEqual(self.score["downbeats"][1], 2.0, places=3)

    def test_schema_v2_fields(self):
        n = self.score["notes"][0]
        self.assertEqual(n["schema_version"], 2)
        self.assertEqual(n["gate_ratio"], 1.0)
        self.assertAlmostEqual(n["end"], n["time"] + n["duration"], places=3)
        json.dumps(score_parser.public_view(self.score))  # must be serialisable

    def test_pickup_bar_keeps_its_short_length(self):
        # An anacrusis padded to a full 4/4 bar would push every downbeat 3 beats late.
        s = score_parser.parse_musicxml(self.write("pickup.xml", PICKUP_XML))
        self.assertAlmostEqual(s["measures"][0]["quarter_length"], 1.0)
        self.assertAlmostEqual(s["measures"][1]["quarter_start"], 1.0)
        self.assertEqual(s["notes"][1]["midi"], 73)  # C#5 via <alter>
        self.assertEqual(s["tempo_source"], "default")
        self.assertEqual(s["bpm"], score_parser.DEFAULT_BPM)

    def test_pages_concatenate_in_order(self):
        p = self.write("piano.musicxml", PIANO_XML)
        s = score_parser.parse_musicxml([p, p])
        self.assertEqual(len(s["measures"]), 4)
        c5 = [n for n in s["notes"] if n["name"] == "C5"]
        self.assertAlmostEqual(c5[1]["quarter_time"], 6.0)
        # page 2 restates tempo 90 → its first bar runs at 90 again, not 60
        self.assertAlmostEqual(c5[1]["time"], self.score["duration"], places=3)

    def test_continuation_page_meter_guess_is_ignored(self):
        # homr read page 2 of a 6/8 piece (no printed time signature) as 3/4.
        # Same bar length → must stay 6/8, or beats/downbeats change mid-piece.
        p1 = self.write("p1.xml", PIANO_XML.replace("<beats>3</beats><beat-type>4</beat-type>",
                                                    "<beats>6</beats><beat-type>8</beat-type>"))
        p2 = self.write("p2.xml", PIANO_XML)  # 3/4
        s = score_parser.parse_musicxml([p1, p2])
        self.assertEqual([t["time_signature"] for t in s["time_signatures"]], ["6/8"])
        # a guess whose bars don't hold its length (2/4 label, 3-quarter bars) is dropped too
        p4 = self.write("p4.xml", PIANO_XML.replace("<beats>3</beats>", "<beats>2</beats>"))
        s = score_parser.parse_musicxml([p1, p4])
        self.assertEqual([t["time_signature"] for t in s["time_signatures"]], ["6/8"])
        # a different bar length that the bars confirm (full 4-quarter bar) is a real change
        p5 = self.write("p5.xml", PICKUP_XML)  # 4/4
        s = score_parser.parse_musicxml([p1, p5])
        self.assertEqual([t["time_signature"] for t in s["time_signatures"]], ["6/8", "4/4"])

    def test_rejects_non_musicxml(self):
        with self.assertRaises(score_parser.ScoreParseError):
            score_parser.parse_musicxml(self.write("x.xml", "<html></html>"))

    def test_midi_export_keeps_tie_and_hands(self):
        import mido
        out = score_parser.to_midi(self.score, self.tmp / "s.mid")
        mf = mido.MidiFile(str(out))
        self.assertEqual(len(mf.tracks), 3)  # meta + RH + LH
        rh = mf.tracks[1]
        tick, on = 0, {}
        lengths = {}
        for msg in rh:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                on[msg.note] = tick
            elif msg.type in ("note_off", "note_on"):
                lengths[msg.note] = tick - on[msg.note]
        self.assertEqual(lengths[74], 3 * 480)  # D5 tied: 3 quarters


class TestOmrProcessor(_TmpDir):
    def test_pdf_renders_one_png_per_page(self):
        import fitz
        pdf = self.tmp / "s.pdf"
        with fitz.open() as doc:
            doc.new_page(); doc.new_page()
            doc.save(str(pdf))
        pages = omr_processor.render_pages(pdf, self.tmp / "pages", dpi=50)
        self.assertEqual([p.name for p in pages], ["page_001.png", "page_002.png"])

    def test_page_limit_enforced(self):
        import fitz
        pdf = self.tmp / "big.pdf"
        with fitz.open() as doc:
            for _ in range(3):
                doc.new_page()
            doc.save(str(pdf))
        with self.assertRaises(omr_processor.OmrError):
            omr_processor.render_pages(pdf, self.tmp / "pages", dpi=50, max_pages=2)

    def test_jpg_normalised_to_png(self):
        from PIL import Image
        jpg = self.tmp / "s.jpg"
        Image.new("L", (40, 30), 255).save(jpg)
        pages = omr_processor.render_pages(jpg, self.tmp / "pages")
        with Image.open(pages[0]) as im:
            self.assertEqual((im.format, im.mode), ("PNG", "RGB"))

    def test_job_survives_one_failed_page_and_reports_it(self):
        import fitz
        orig_dir, orig_run = omr_processor.OMR_DIR, omr_processor.run_engine
        omr_processor.OMR_DIR = self.tmp / "omr"
        fixture = self.write("fixture.musicxml", PIANO_XML)

        def fake_run(engine, image, out_dir, timeout=0):
            if image.name == "page_002.png":
                raise omr_processor.OmrError("unreadable")
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / f"{image.stem}.musicxml"
            shutil.copy(fixture, dst)
            return dst

        omr_processor.run_engine = fake_run
        try:
            src = self.tmp / "in.pdf"
            with fitz.open() as doc:
                doc.new_page(); doc.new_page(); doc.new_page()
                doc.save(str(src))
            job = omr_processor.OmrJob(job_id="a" * 32, input_path=str(src), engine="homr")
            omr_processor.process_job(job)
            res = omr_processor.load_result(job.job_id)
            self.assertEqual(res["job"]["pages"], 3)
            self.assertEqual(res["job"]["pages_recognized"], 2)
            self.assertEqual(len(res["job"]["warnings"]), 1)  # gap is surfaced, not hidden
            self.assertEqual(len(res["score"]["measures"]), 4)
            self.assertTrue((omr_processor.job_dir(job.job_id) / "score.mid").is_file())
            # status falls back to disk when the in-memory record is gone (restart)
            self.assertEqual(omr_processor.get_status(job.job_id)["status"], "done")
        finally:
            omr_processor.OMR_DIR, omr_processor.run_engine = orig_dir, orig_run

    def test_all_pages_failing_is_an_error(self):
        orig_dir, orig_run = omr_processor.OMR_DIR, omr_processor.run_engine
        omr_processor.OMR_DIR = self.tmp / "omr"

        def fail(*a, **k):
            raise omr_processor.OmrError("nope")

        omr_processor.run_engine = fail
        try:
            from PIL import Image
            png = self.tmp / "in.png"
            Image.new("RGB", (10, 10), "white").save(png)
            job = omr_processor.OmrJob(job_id="b" * 32, input_path=str(png), engine="oemer")
            with self.assertRaises(omr_processor.OmrError):
                omr_processor.process_job(job)
            self.assertIsNone(omr_processor.load_result(job.job_id))
        finally:
            omr_processor.OMR_DIR, omr_processor.run_engine = orig_dir, orig_run


class TestOmrApi(_TmpDir):
    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import omr_api
        from auth_api import get_current_user
        from personal_mode import require_personal_mode

        self.omr_api = omr_api
        app = FastAPI()
        app.include_router(omr_api.router)
        app.dependency_overrides[get_current_user] = lambda: "tester"
        app.dependency_overrides[require_personal_mode] = lambda: None
        self.client = TestClient(app)
        self._orig = (omr_processor.OMR_DIR, omr_processor.resolve_engine, omr_processor.enqueue)
        omr_processor.OMR_DIR = self.tmp / "omr"
        omr_processor.resolve_engine = lambda e=None: "homr"
        self.enqueued = []
        omr_processor.enqueue = lambda *a, **k: self.enqueued.append(a)

    def tearDown(self):
        omr_processor.OMR_DIR, omr_processor.resolve_engine, omr_processor.enqueue = self._orig
        super().tearDown()

    def _post(self, name, data):
        return self.client.post("/api/omr/convert", files={"file": (name, data)})

    def test_no_engine_is_503_before_upload_is_kept(self):
        def none(e=None):
            raise omr_processor.OmrError("no OMR engine installed")
        omr_processor.resolve_engine = none
        r = self._post("a.png", b"\x89PNG....")
        self.assertEqual(r.status_code, 503)
        self.assertFalse((self.tmp / "omr").exists())

    def test_wrong_extension_and_spoofed_content_rejected(self):
        self.assertEqual(self._post("a.gif", b"GIF89a").status_code, 400)
        self.assertEqual(self._post("a.pdf", b"\x89PNG not a pdf").status_code, 400)
        self.assertEqual(self.enqueued, [])

    def test_valid_png_enqueues_and_returns_immediately(self):
        r = self._post("song.png", b"\x89PNG\r\n")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(len(self.enqueued), 1)
        self.assertEqual(body["status_url"], f"/api/omr/status/{body['job_id']}")

    def test_bad_job_id_cannot_traverse(self):
        self.assertEqual(self.client.get("/api/omr/download/..%2F..%2Fx/json").status_code, 404)
        self.assertEqual(self.client.get("/api/omr/status/nothex").status_code, 404)


if __name__ == "__main__":
    unittest.main()
