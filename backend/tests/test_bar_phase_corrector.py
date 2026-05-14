import unittest

from backend.bar_phase_corrector import (
    correct_phase,
    fragmentation_risk,
    search_best_phase,
)


class TestFragmentationRisk(unittest.TestCase):
    def test_shifted_4_4_grid_flags_one_three_fragments(self):
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "C"},
            {"time": 2.0, "end": 4.0, "chord": "F"},
            {"time": 4.0, "end": 6.0, "chord": "G"},
        ]
        shifted_downbeats = [0.5, 2.5, 4.5]
        risk = fragmentation_risk(chords, shifted_downbeats, bpm=120.0, bpb=4)
        self.assertGreaterEqual(risk["bad_fragments"], 3)
        self.assertIn("1+3", risk["patterns"])
        self.assertGreaterEqual(risk["penalty"], 0.18)

    def test_clean_4_4_grid_has_no_fragment_penalty(self):
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "C"},
            {"time": 2.0, "end": 4.0, "chord": "F"},
            {"time": 4.0, "end": 6.0, "chord": "G"},
        ]
        clean_downbeats = [0.0, 2.0, 4.0, 6.0]
        risk = fragmentation_risk(chords, clean_downbeats, bpm=120.0, bpb=4)
        self.assertEqual(risk["bad_fragments"], 0)
        self.assertEqual(risk["penalty"], 0.0)


class TestPhaseCorrectorFragmentGuard(unittest.TestCase):
    def test_search_prefers_clean_phase_over_fragmented_phase(self):
        bpm = 120.0
        beats = [i * 0.5 for i in range(17)]
        chords = [
            {"time": 0.0, "end": 2.0, "chord": "C"},
            {"time": 2.0, "end": 4.0, "chord": "F"},
            {"time": 4.0, "end": 6.0, "chord": "G"},
            {"time": 6.0, "end": 8.0, "chord": "C"},
        ]
        chord_changes = [c["time"] for c in chords[1:]]
        bpb, phase, align, frag = search_best_phase(chord_changes, beats, bpm, chords)
        self.assertEqual(bpb, 4)
        self.assertEqual(phase, 0)
        self.assertGreater(align, 0.5)
        self.assertEqual(frag["bad_fragments"], 0)

    def test_correct_phase_replaces_clean_but_shifted_downbeats(self):
        bpm = 120.0
        beats = [i * 0.5 for i in range(49)]
        chords = [
            {"time": i * 2.0, "end": (i + 1) * 2.0, "chord": "C" if i % 2 == 0 else "F"}
            for i in range(12)
        ]
        data = {
            "chords": chords,
            "beats": beats,
            "downbeats": [0.5 + i * 2.0 for i in range(12)],
            "bpm": bpm,
        }
        res = correct_phase(data)
        self.assertTrue(res["applied"], res["reason"])
        self.assertEqual(res["bpb_after"], 4)
        self.assertEqual(res["phase_after"], 0)
        self.assertEqual(res["bad_fragments"], 0)

    def test_correct_phase_accepts_large_relative_fragment_improvement(self):
        bpm = 120.0
        beats = [i * 0.5 for i in range(65)]
        chords = []
        for i in range(12):
            start = i * 2.0
            # A handful of real one-beat pickups remain even on the best grid.
            # The corrector should still accept the phase when it removes the
            # much larger wrong-grid fragment pattern.
            end = start + (2.5 if i in {1, 3, 5, 7, 9} else 2.0)
            chords.append({"time": start, "end": end, "chord": "C" if i % 2 == 0 else "F"})
        data = {
            "chords": chords,
            "beats": beats,
            "downbeats": [0.5 + i * 2.0 for i in range(12)],
            "bpm": bpm,
        }

        res = correct_phase(data)

        self.assertTrue(res["applied"], res["reason"])
        self.assertIn("relative-fragment-fix", res["reason"])
        self.assertEqual(res["phase_after"], 0)
        self.assertGreater(res["bad_fragments_before"], res["bad_fragments"])

    def test_preserves_stable_current_four_four_against_three_beat_candidate(self):
        bpm = 130.4
        beats = [round(0.24 + i * 0.46, 3) for i in range(192)]
        downbeats = [round(0.24 + i * 1.84, 3) for i in range(48)]
        chords = [
            {"time": 1.207, "end": 3.042, "chord": "G"},
            {"time": 3.042, "end": 4.876, "chord": "C"},
            {"time": 4.876, "end": 7.639, "chord": "Am"},
            {"time": 7.639, "end": 9.520, "chord": "G"},
            {"time": 9.520, "end": 10.400, "chord": "C"},
            {"time": 10.400, "end": 11.817, "chord": "D"},
            {"time": 11.817, "end": 13.189, "chord": "Gm"},
            {"time": 13.189, "end": 15.511, "chord": "Cm"},
            {"time": 15.511, "end": 17.345, "chord": "G"},
            {"time": 17.345, "end": 20.571, "chord": "Cm"},
            {"time": 20.571, "end": 21.965, "chord": "Gm"},
            {"time": 21.965, "end": 22.894, "chord": "Dm"},
            {"time": 22.894, "end": 32.109, "chord": "Gm"},
        ]
        data = {"chords": chords, "beats": beats, "downbeats": downbeats, "bpm": bpm}

        res = correct_phase(data)

        self.assertFalse(res["applied"])
        self.assertIn("preserve-stable-4/4", res["reason"])
        self.assertEqual(res["bpb_after"], 3)


    def test_rejects_marginal_3beat_candidate_for_noisy_4_4(self):
        # Mirrors a Beegie Adair / Diana Krall failure case from the
        # 2026-05-15 jazz quality gate. The current downbeat track
        # has median 4-beat gaps (so the song is really in 4/4), but
        # beat_this noise drove cv above the old stable_4_4 cv<0.08
        # threshold. The search would find a 3-beat candidate that
        # gains only a fraction over the current alignment — the
        # cross-meter gate must require ≥0.12 gain, so anything below
        # that should NOT flip the song to 3/4.
        # Verified against the actual production chord JSON for
        # "Why Don't You Do Right" (hash 7a63a04e280e):
        #   current_bpb_est=4.04, candidate bpb=3, align +0.055.
        # That song must stay 4/4.
        bpm = 130.4
        spb = 60.0 / bpm
        bar4 = 4 * spb
        beats = [round(i * spb, 3) for i in range(256)]
        # Build downbeats whose median gap is solidly 4-beat (1.84s),
        # but with several intra-bar entries that bump cv above 0.08
        # AND lower the current alignment to ~0.23 (matching prod).
        clean = [round(i * bar4, 3) for i in range(56)]
        # Inject mostly-near-bar drift so search finds a 3-beat phase
        # that aligns marginally better but not by much.
        noise = [round(c + 0.32, 3) for c in clean[8:16]]
        downbeats = sorted(set(clean + noise))
        # Chords drift just enough to make 3-beat phase tempting but
        # not overwhelming.
        chords = []
        for i, t in enumerate(clean):
            chords.append({
                "time": t,
                "end": round(t + bar4, 3),
                "chord": ["Dm", "G7", "C", "A7"][i % 4],
            })
        data = {"chords": chords, "beats": beats, "downbeats": downbeats, "bpm": bpm}

        res = correct_phase(data)

        if res.get("bpb_after") == 3 and res.get("applied"):
            self.fail(
                f"3-beat candidate adopted for what should be a 4/4 grid: {res['reason']}"
            )


if __name__ == "__main__":
    unittest.main()
