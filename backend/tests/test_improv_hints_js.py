"""frontend/js/improv-hints.js — the player's 即興提示 logic.

The player draws these as rings on the keyboard while the user improvises a
second voice (third / sixth under the melody at phrase ends) and walks the
left-hand bass by step. A wrong hint is worse than none — the user plays it
in real time — so these pin the musical rules, not just the shapes.

Runs the module under Node; skipped where Node is not installed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "frontend" / "js" / "improv-hints.js"
NODE = shutil.which("node")

# chord name -> [root, 3rd, 5th] pitch classes, the shape player.js builds
# from chordCache notes.
TONES = {
    "C": [0, 4, 7], "G": [7, 11, 2], "Am": [9, 0, 4], "F": [5, 9, 0],
    "Dm": [2, 5, 9], "G7": [7, 11, 2, 5], "E": [4, 8, 11],
}


def run(expr):
    script = (
        f"const H = require({json.dumps(str(JS))});"
        f"const TONES = {json.dumps(TONES)};"
        "const tonesOf = (n) => TONES[n] || [];"
        f"process.stdout.write(JSON.stringify({expr}));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def chords(*names):
    return [{"chord": n, "time": i * 2.0, "end": i * 2.0 + 2.0} for i, n in enumerate(names)]


@unittest.skipUnless(NODE, "node not installed")
class TestBassLine(unittest.TestCase):
    def bass(self, *names):
        return run(f"H.bassLine({json.dumps(chords(*names))}, tonesOf)")

    def test_pop_progression_walks_down_by_step(self):
        # The textbook case from the practice notes: C → G/B → Am, the left
        # hand stepping Do-Si-La instead of jumping C-G-A.
        line = self.bass("C", "G", "Am")
        self.assertEqual([b["pc"] for b in line], [0, 11, 9])
        self.assertEqual([b["slash"] for b in line], [False, True, False])
        steps = [abs(b["midi"] - a["midi"]) for a, b in zip(line, line[1:])]
        self.assertTrue(all(s <= 2 for s in steps), steps)

    def test_root_wins_when_it_is_already_close(self):
        # Am → Dm → G → C: roots move by 4ths/5ths that land within a few
        # semitones of each other; inversions would only blur the cadence.
        line = self.bass("Am", "Dm", "G", "C")
        self.assertEqual([b["slash"] for b in line], [False, False, False, False])

    def test_deceptive_cadence_keeps_the_root_near_the_top_of_the_range(self):
        # Found on "At seventeen": Dm7 → G7 put G at the top of the range and
        # the next Am lost its root to the range cap, showing Am/E. V → vi is
        # a step in the bass (G → A) and must stay that way.
        line = self.bass("Dm", "G7", "Am")
        self.assertEqual([b["pc"] for b in line], [2, 7, 9])
        self.assertLessEqual(abs(line[2]["midi"] - line[1]["midi"]), 2)

    def test_written_slash_chord_is_respected(self):
        line = self.bass("C", "C/E", "F")
        self.assertEqual(line[1]["pc"], 4)

    def test_bass_stays_in_left_hand_register(self):
        line = self.bass("C", "G", "Am", "F", "C", "G", "E", "Am")
        self.assertTrue(all(36 <= b["midi"] <= 55 for b in line))

    def test_no_chord_gives_no_bass(self):
        self.assertIsNone(self.bass("C", "N", "G")[1])


@unittest.skipUnless(NODE, "node not installed")
class TestApproach(unittest.TestCase):
    def test_step_needs_no_approach(self):
        self.assertIsNone(run("H.approachNote(48, 47, H.scalePcs('C'))"))

    def test_whole_step_gets_the_chromatic_passing_note(self):
        self.assertEqual(run("H.approachNote(48, 50, H.scalePcs('C'))"), 49)

    def test_wider_move_approaches_from_the_scale_step_before_target(self):
        # C2 → F2 in C major: E2 leads into F, coming up from below.
        self.assertEqual(run("H.approachNote(36, 41, H.scalePcs('C'))"), 40)
        # Going down C3 → A2: B2 is the scale step above A.
        self.assertEqual(run("H.approachNote(48, 45, H.scalePcs('C'))"), 47)

    def test_approach_note_stays_in_the_left_hand(self):
        self.assertLess(run("H.approachNote(53, 57, H.scalePcs('C'))"), run("H.SPLIT"))

    def test_minor_key_uses_the_minor_scale(self):
        # A minor, A2 → D3 from below: C#3 is not in the key; C3 is.
        self.assertEqual(run("H.approachNote(45, 50, H.scalePcs('Am'))"), 48)


@unittest.skipUnless(NODE, "node not installed")
class TestPhraseEndHarmony(unittest.TestCase):
    def test_third_and_sixth_under_the_melody(self):
        # Melody A4 over F: the choir notes are F4 (third below) and C4 (sixth).
        out = run("H.harmonyBelow(69, [5, 9, 0])")
        self.assertEqual(out, [{"midi": 65, "label": "3"}, {"midi": 60, "label": "6"}])

    def test_right_hand_never_crosses_into_the_left_hand(self):
        # Melody E4 over C used to suggest G3 too — the left hand's bass
        # register (up to A3). Only C4 is above the split now.
        out = run("H.harmonyBelow(64, [0, 4, 7])")
        self.assertEqual([h["midi"] for h in out], [60])
        self.assertTrue(all(h["midi"] >= run("H.SPLIT") for h in out))

    def test_harmony_stays_within_one_hand_span(self):
        # Melody C6 over F: A5 (third) and F5 (fifth) fit; C5 an octave down
        # is a stretch for one hand and is never offered.
        out = run("H.harmonyBelow(84, [5, 9, 0], 3)")
        self.assertTrue(all(84 - h["midi"] <= 9 for h in out), out)

    def test_never_a_second_below(self):
        # Melody D5 over G7: C5 (the 7th) is a second away and would rub.
        out = run("H.harmonyBelow(74, [7, 11, 2, 5])")
        self.assertNotIn(72, [h["midi"] for h in out])
        self.assertEqual(out[0], {"midi": 71, "label": "3"})

    def test_only_long_or_breathing_notes_qualify(self):
        beat = 0.5
        notes = [
            {"start": 0.0, "end": 0.25, "midi": 60},   # eighth, passing — no
            {"start": 0.25, "end": 1.25, "midi": 62},  # two beats — yes
            {"start": 1.25, "end": 1.75, "midi": 64},  # one beat then a breath — yes
            {"start": 2.25, "end": 2.5, "midi": 65},   # short — no
            {"start": 2.5, "end": 3.0, "midi": 67},    # one beat, next follows at once — no
            {"start": 3.0, "end": 3.2, "midi": 69},
        ]
        got = run(f"H.phraseEndNotes({json.dumps(notes)}, {beat}).map(n => n.midi)")
        self.assertEqual(got, [62, 64])

    def test_anticipated_phrase_end_of_a_beat_and_a_quarter_qualifies(self):
        # Syncopated phrase end (At Seventeen): pushed in half a beat early,
        # 1.25 beats long, the next phrase follows without a breath.
        beat = 0.5
        notes = [{"start": 0.0, "end": 0.625, "midi": 64},
                 {"start": 0.625, "end": 0.8, "midi": 62}]
        got = run(f"H.phraseEndNotes({json.dumps(notes)}, {beat}).map(n => n.midi)")
        self.assertEqual(got, [64])


if __name__ == "__main__":
    unittest.main()
