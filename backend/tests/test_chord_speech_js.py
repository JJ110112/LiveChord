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
        self.assertEqual(say("Bbm7", lang="zh"), "降 B 小七")
        self.assertEqual(say("Bbm7", lang="en"), "B flat minor seven")

    def test_major_seven_is_not_dominant_seven(self):
        # maj7 and 7 are different chords; "m" must not swallow "maj".
        self.assertEqual(say("Cmaj7", lang="zh"), "C 大七")
        self.assertEqual(say("C7", lang="zh"), "C 七")
        # A bare 小 was too short to catch over the music; triads say 小三.
        self.assertEqual(say("Cm", lang="zh"), "C 小三")
        self.assertEqual(say("C", lang="zh"), "C")

    def test_slash_bass_is_announced(self):
        # The bass note decides what the left hand plays.
        self.assertEqual(say("G/B", lang="zh"), "G，低音B")
        self.assertEqual(say("G/B", lang="en"), "G over B")

    def test_tensions_and_half_diminished(self):
        self.assertEqual(say("Bm7b5", lang="zh"), "B 半減七")
        self.assertEqual(say("G7b9", lang="en"), "G seven flat nine")
        self.assertEqual(say("F#sus4", lang="zh"), "升 F 掛四")

    def test_full_style_says_one_word_per_symbol(self):
        # 中文對照 matches the chart symbol by symbol (m7b5 = 小七降五, 7 = 屬七)
        # for players who read the chart; 精簡 keeps it short for fast changes.
        self.assertEqual(say("Gm7b5", lang="zh"), "G 半減七")
        self.assertEqual(say("Gm7b5", lang="zh", style="full"), "G 小七降五")
        self.assertEqual(say("C7", lang="zh", style="full"), "C 屬七")
        self.assertEqual(say("Cmaj7", lang="zh", style="full"), "C 大七")
        self.assertEqual(say("Gm7b5", lang="en", style="full"), "G minor seven flat five")

    def test_no_chord_is_silent(self):
        self.assertEqual(say("N", lang="zh"), "")
        self.assertEqual(say("", lang="zh"), "")


@unittest.skipUnless(NODE, "node not installed")
class TestDegrees(unittest.TestCase):
    def test_pop_progression_in_c(self):
        # I – V – vi – IV: the reason degree mode exists (same words in any key).
        words = [say(c, lang="zh", mode="degree", key="C") for c in ("C", "G", "Am", "F")]
        self.assertEqual(words, ["一級", "五級", "六級小三", "四級"])

    def test_transposed_song_says_same_degrees(self):
        words = [say(c, lang="zh", mode="degree", key="D") for c in ("D", "A", "Bm", "G")]
        self.assertEqual(words, ["一級", "五級", "六級小三", "四級"])

    def test_minor_key_counts_from_relative_major_like_jianpu(self):
        # 首調 jianpu numbers A minor from C, so Am is 6 on the cards; the
        # spoken degree must match the card.
        self.assertEqual(say("Am", lang="zh", mode="degree", key="Am"), "六級小三")
        self.assertEqual(say("E7", lang="zh", mode="degree", key="Am"), "三級七")

    def test_borrowed_chord_and_slash_bass_degree(self):
        self.assertEqual(say("Bb", lang="zh", mode="degree", key="C"), "降七級")
        self.assertEqual(say("C/E", lang="zh", mode="degree", key="C"), "一級，低音三")
        self.assertEqual(say("G7", lang="en", mode="degree", key="C"), "five seven")


def plan(chord, dur, **kw):
    o = {"chord": chord, "lang": "en", "style": "short", "mode": "name", "key": "C",
         "durSec": dur, "prevDurSec": 2.0, "baseRate": 1.1, "baseLeadSec": 0.4,
         "auto": True, "shorten": True}
    o.update(kw)
    script = (
        f"const S = require({json.dumps(str(JS))});"
        f"process.stdout.write(JSON.stringify(S.planCallout({json.dumps(o)})));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node not installed")
class TestPlanByTempo(unittest.TestCase):
    """A callout still talking when the next chord starts tells the player
    the wrong chord, so quick chords must get shorter words / faster speech,
    while slow songs keep the user's own settings."""

    def test_slow_chord_keeps_full_words_and_user_rate(self):
        p = plan("Gm7b5", 4.0)
        self.assertEqual(p["text"], "G half diminished")
        self.assertAlmostEqual(p["rate"], 1.1)

    def test_quick_chord_is_abbreviated_when_option_on(self):
        self.assertEqual(plan("Gm7b5", 0.9)["text"], "G half dim")
        self.assertEqual(plan("Bdim", 0.9)["text"], "B dim")
        self.assertEqual(plan("Gm7b5", 0.9, shorten=False)["text"], "G half diminished")

    def test_zh_full_falls_back_to_short_wording_on_quick_chords(self):
        p = plan("Gm7b5", 1.05, lang="zh", style="full")
        self.assertEqual(p["text"], "G 半減七")
        self.assertEqual(plan("Gm7b5", 4.0, lang="zh", style="full")["text"], "G 小七降五")

    def test_auto_speeds_up_only_when_needed_and_never_past_135(self):
        # Above ~1.35x a voice over music smears flat / sharp / minor.
        self.assertAlmostEqual(plan("C", 0.9)["rate"], 1.1)          # short word fits as is
        self.assertGreater(plan("C/E", 0.9)["rate"], 1.1)            # slightly long: a bit faster
        self.assertLessEqual(plan("Bbm7", 0.6, shorten=False)["rate"], 1.35 + 1e-9)

    def test_steps_down_to_root_only_instead_of_being_cut_off(self):
        # Fast ii-V: hearing the root motion clearly beats a smeared
        # "D minor seven" that the next callout cuts in half.
        self.assertEqual(plan("Bbm7", 0.9)["text"], "B flat")
        self.assertEqual(plan("Dm7", 0.8)["text"], "D")
        self.assertEqual(plan("Dm7", 0.8, lang="zh", mode="degree", key="C")["text"], "二級")

    def test_skips_when_even_the_root_does_not_fit(self):
        self.assertEqual(plan("Bbm7", 0.3)["text"], "")

    def test_long_chord_in_fast_song_is_spoken_in_full_at_user_rate(self):
        # Tempo alone never speeds the voice up: a 2-bar chord in a fast song
        # gets the full words at the user's own rate (easier to hear).
        p = plan("Bbm7", 3.0)
        self.assertEqual(p["text"], "B flat minor seven")
        self.assertAlmostEqual(p["rate"], 1.1)

    def test_auto_lead_never_reaches_back_past_half_the_previous_chord(self):
        p = plan("Bbm7", 0.9, prevDurSec=0.6)
        self.assertLessEqual(p["leadSec"], 0.3 + 1e-9)

    def test_auto_off_uses_the_sliders_exactly(self):
        p = plan("Bbm7", 0.9, auto=False)
        self.assertAlmostEqual(p["rate"], 1.1)
        self.assertAlmostEqual(p["leadSec"], 0.4)


if __name__ == "__main__":
    unittest.main()
