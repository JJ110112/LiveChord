"""frontend/js/chord-speech.js — what the player says for each chord.

The user practises with eyes off the screen, so the spoken words are the
only chord information they get. A wrong word (minor read as major, a
degree counted from the wrong 1, a slash bass dropped) teaches the wrong
progression, so these pin the musical meaning of each callout.

Runs the module under Node; skipped where Node is not installed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "frontend" / "js" / "chord-speech.js"
NODE = shutil.which("node")


def say(chord, **opts):
    script = (
        f"const S = require({json.dumps(str(JS))});"
        f"process.stdout.write(JSON.stringify(S.spokenText({json.dumps(chord)}, {json.dumps(opts)})));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node not installed")
class TestChordNames(unittest.TestCase):
    def test_quality_is_spoken_not_spelled(self):
        # A TTS voice given "Bbm7" reads letters; the callout must carry the
        # flat, the minor and the seventh as words.
        self.assertEqual(say("Bbm7", lang="zh"), "降B 小七")
        self.assertEqual(say("Bbm7", lang="en"), "B flat minor seven")

    def test_major_seven_is_not_dominant_seven(self):
        # maj7 and 7 are different chords; "m" must not swallow "maj".
        self.assertEqual(say("Cmaj7", lang="zh"), "C 大七")
        self.assertEqual(say("C7", lang="zh"), "C 七")
        self.assertEqual(say("Cm", lang="zh"), "C 小")
        self.assertEqual(say("C", lang="zh"), "C")

    def test_slash_bass_is_announced(self):
        # The bass note decides what the left hand plays.
        self.assertEqual(say("G/B", lang="zh"), "G，低音B")
        self.assertEqual(say("G/B", lang="en"), "G over B")

    def test_tensions_and_half_diminished(self):
        self.assertEqual(say("Bm7b5", lang="zh"), "B 半減七")
        self.assertEqual(say("G7b9", lang="en"), "G seven flat nine")
        self.assertEqual(say("F#sus4", lang="zh"), "升F 掛四")

    def test_no_chord_is_silent(self):
        self.assertEqual(say("N", lang="zh"), "")
        self.assertEqual(say("", lang="zh"), "")


@unittest.skipUnless(NODE, "node not installed")
class TestDegrees(unittest.TestCase):
    def test_pop_progression_in_c(self):
        # I – V – vi – IV: the reason degree mode exists (same words in any key).
        words = [say(c, lang="zh", mode="degree", key="C") for c in ("C", "G", "Am", "F")]
        self.assertEqual(words, ["一級", "五級", "六級小", "四級"])

    def test_transposed_song_says_same_degrees(self):
        words = [say(c, lang="zh", mode="degree", key="D") for c in ("D", "A", "Bm", "G")]
        self.assertEqual(words, ["一級", "五級", "六級小", "四級"])

    def test_minor_key_counts_from_relative_major_like_jianpu(self):
        # 首調 jianpu numbers A minor from C, so Am is 6 on the cards; the
        # spoken degree must match the card.
        self.assertEqual(say("Am", lang="zh", mode="degree", key="Am"), "六級小")
        self.assertEqual(say("E7", lang="zh", mode="degree", key="Am"), "三級七")

    def test_borrowed_chord_and_slash_bass_degree(self):
        self.assertEqual(say("Bb", lang="zh", mode="degree", key="C"), "降七級")
        self.assertEqual(say("C/E", lang="zh", mode="degree", key="C"), "一級，低音三")
        self.assertEqual(say("G7", lang="en", mode="degree", key="C"), "five seven")


if __name__ == "__main__":
    unittest.main()
