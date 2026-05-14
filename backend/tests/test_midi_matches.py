"""Tests for ``_midi_matches`` hardening.

History: the original substring-based matcher matched
``"Vem"`` against ``"Beethoven - Moonlight Sonata (1st Movement).mid"``
because ``"vem"`` sits inside ``"movement"``. 10k chord JSONs got
auto-tagged source=midi this way and were quarantined under
LiveChord-a7c. These tests pin the new (stricter) behaviour against
the exact false-positive set we found in the user's library.
"""

import unittest

from backend.midi_match import midi_matches as _midi_matches


class TestMidiMatchesFalsePositives(unittest.TestCase):
    """Every case here MUST stay rejected — these were real bad matches in prod."""

    def test_vem_does_not_match_moonlight_movement(self):
        # "vem" ⊂ "movement" was the canonical bug.
        self.assertFalse(_midi_matches(
            "Vem", "Beethoven - Moonlight Sonata (1st Movement).mid"))

    def test_move_does_not_match_moonlight_movement(self):
        # "\bmove$" inside "movement" must not fire either.
        self.assertFalse(_midi_matches(
            "Move", "Beethoven - Moonlight Sonata (1st Movement).mid"))

    def test_you_does_not_match_random_song_about_you(self):
        # "You" is generic; original matcher returned 50 candidates.
        self.assertFalse(_midi_matches(
            "You", "Air Supply - Here I Am (Just When I Thought I Was Over You).mid"))

    def test_for_you_does_not_match_for_your_eyes_only(self):
        self.assertFalse(_midi_matches(
            "For You", "For Your Eyes Only.mid"))

    def test_be_with_me_does_not_match_killing_me_softly(self):
        # All-stopword title rejected on keyword path.
        self.assertFalse(_midi_matches(
            "Be With Me",
            "Fugees - Killing Me Softly With His Song (Official Video).mid"))

    def test_smoke_gets_in_your_eyes_does_not_match_for_your_eyes_only(self):
        self.assertFalse(_midi_matches(
            "Smoke Gets In Your Eyes", "For Your Eyes Only.mid"))

    def test_silence_does_not_match_sound_of_silence(self):
        # Single-word ASCII no longer wins via trailing-word substring.
        self.assertFalse(_midi_matches(
            "Silence",
            "Disturbed - The Sound Of Silence (Official Music Video) [4K UPGRADE].mid"))

    def test_summer_does_not_match_one_summers_day(self):
        self.assertFalse(_midi_matches(
            "Summer", "Joe Hisaishi - One Summer's Day.mid"))

    def test_celebrate_does_not_match_tonight_i_celebrate_my_love(self):
        self.assertFalse(_midi_matches(
            "Celebrate",
            "Roberta Flack & Peabo Bryson - Tonight I Celebrate My Love (Official Music Video).mid"))

    def test_what_is_hip_does_not_match_what_love_is(self):
        # Only "what" and "is" overlap, both stopwords now.
        self.assertFalse(_midi_matches(
            "What Is Hip", "Foreigner - I Want To Know What Love Is (Official Music Video).mid"))

    def test_dont_blame_me_does_not_match_dont_cry_for_me_argentina(self):
        self.assertFalse(_midi_matches(
            "Don't Blame Me",
            "Richard Clayderman - Don't Cry for Me Argentina (Official Audio).mid"))

    def test_i_love_you_because_does_not_match_and_i_love_you_so(self):
        # Word-boundary substring path: the full title is not a contiguous
        # substring of the MIDI name, so substring fails. Keyword overlap
        # has only "love" / "love" — 1 word, below the new ≥3 threshold.
        self.assertFalse(_midi_matches(
            "I Love You Because", "ELVIS - And I Love You So.mid"))


class TestMidiMatchesTruePositives(unittest.TestCase):
    """Legitimate matches still pass."""

    def test_exact_filename(self):
        self.assertTrue(_midi_matches("Spain", "Spain.mid"))
        self.assertTrue(_midi_matches("Autumn Leaves", "Autumn Leaves.mid"))

    def test_artist_dash_track_pattern(self):
        # The user's MIDI library mostly follows this convention.
        self.assertTrue(_midi_matches(
            "The Winner Takes It All", "ABBA - The Winner Takes It All.mid"))
        self.assertTrue(_midi_matches(
            "Autumn Leaves", "Nat King Cole - Autumn Leaves.mid"))

    def test_multi_word_with_remaster_suffix(self):
        self.assertTrue(_midi_matches(
            "This Masquerade", "This Masquerade (2000 Remaster).mid"))

    def test_cjk_substring(self):
        # CJK characters are inherent word boundaries.
        self.assertTrue(_midi_matches(
            "残酷な天使のテーゼ", "残酷な天使のテーゼ - 高橋洋子.mid"))
        self.assertTrue(_midi_matches("月亮代表我的心", "月亮代表我的心.mid"))


class TestMidiMatchesEdgeCases(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertFalse(_midi_matches("", "anything.mid"))
        self.assertFalse(_midi_matches("Spain", ""))
        self.assertFalse(_midi_matches("", ""))

    def test_punctuation_only_after_normalize_still_works(self):
        # "!!! ???" normalizes to empty → must not match.
        self.assertFalse(_midi_matches("!!! ???", "Spain.mid"))

    def test_single_word_ascii_requires_exact_equality(self):
        # "Spain" → "Chick Corea Spain" is rejected (false-negative we
        # accept on purpose) because we cannot disambiguate single
        # short words without artist context.
        self.assertFalse(_midi_matches("Spain", "Chick Corea Spain.mid"))


if __name__ == "__main__":
    unittest.main()
