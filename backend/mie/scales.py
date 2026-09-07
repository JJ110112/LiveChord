"""Scale catalogue ported from frontend/js/scale-lab.js (31 scales, 5 categories).

`intervals` are semitones above the root.  Keep this table in sync with the JS
source; the ids are shared with the Scale Lab UI so a scene can name a scale.
"""

from __future__ import annotations

SCALES: dict[str, dict] = {
    "major":             {"cat": "basic",     "en": "Major (Ionian)",             "intervals": [0, 2, 4, 5, 7, 9, 11]},
    "minor":             {"cat": "basic",     "en": "Natural Minor (Aeolian)",    "intervals": [0, 2, 3, 5, 7, 8, 10]},
    "harmonic_minor":    {"cat": "basic",     "en": "Harmonic Minor",             "intervals": [0, 2, 3, 5, 7, 8, 11]},
    "melodic_minor":     {"cat": "basic",     "en": "Melodic Minor",              "intervals": [0, 2, 3, 5, 7, 9, 11]},
    "major_pentatonic":  {"cat": "basic",     "en": "Major Pentatonic",           "intervals": [0, 2, 4, 7, 9]},
    "minor_pentatonic":  {"cat": "basic",     "en": "Minor Pentatonic",           "intervals": [0, 3, 5, 7, 10]},
    "blues":             {"cat": "jazz",      "en": "Blues Scale",                "intervals": [0, 3, 5, 6, 7, 10]},
    "major_blues":       {"cat": "jazz",      "en": "Major Blues Scale",          "intervals": [0, 2, 3, 4, 7, 9]},
    "dorian":            {"cat": "modes",     "en": "Dorian",                     "intervals": [0, 2, 3, 5, 7, 9, 10]},
    "phrygian":          {"cat": "modes",     "en": "Phrygian",                   "intervals": [0, 1, 3, 5, 7, 8, 10]},
    "lydian":            {"cat": "modes",     "en": "Lydian",                     "intervals": [0, 2, 4, 6, 7, 9, 11]},
    "mixolydian":        {"cat": "modes",     "en": "Mixolydian",                 "intervals": [0, 2, 4, 5, 7, 9, 10]},
    "locrian":           {"cat": "modes",     "en": "Locrian",                    "intervals": [0, 1, 3, 5, 6, 8, 10]},
    "bebop_dominant":    {"cat": "jazz",      "en": "Bebop Dominant",             "intervals": [0, 2, 4, 5, 7, 9, 10, 11]},
    "bebop_major":       {"cat": "jazz",      "en": "Bebop Major",                "intervals": [0, 2, 4, 5, 7, 8, 9, 11]},
    "altered":           {"cat": "jazz",      "en": "Altered (Super Locrian)",    "intervals": [0, 1, 3, 4, 6, 8, 10]},
    "lydian_dominant":   {"cat": "jazz",      "en": "Lydian Dominant",            "intervals": [0, 2, 4, 6, 7, 9, 10]},
    "half_whole_dim":    {"cat": "symmetric", "en": "Half-Whole Diminished",      "intervals": [0, 1, 3, 4, 6, 7, 9, 10]},
    "whole_half_dim":    {"cat": "symmetric", "en": "Whole-Half Diminished",      "intervals": [0, 2, 3, 5, 6, 8, 9, 11]},
    "whole_tone":        {"cat": "symmetric", "en": "Whole Tone",                 "intervals": [0, 2, 4, 6, 8, 10]},
    "chromatic":         {"cat": "symmetric", "en": "Chromatic",                  "intervals": list(range(12))},
    "harmonic_major":    {"cat": "exotic",    "en": "Harmonic Major",             "intervals": [0, 2, 4, 5, 7, 8, 11]},
    "hungarian_minor":   {"cat": "exotic",    "en": "Hungarian Minor (Gypsy)",    "intervals": [0, 2, 3, 6, 7, 8, 11]},
    "phrygian_dominant": {"cat": "exotic",    "en": "Phrygian Dominant (Spanish)", "intervals": [0, 1, 4, 5, 7, 8, 10]},
    "double_harmonic":   {"cat": "exotic",    "en": "Double Harmonic (Byzantine)", "intervals": [0, 1, 4, 5, 7, 8, 11]},
    "neapolitan_minor":  {"cat": "exotic",    "en": "Neapolitan Minor",           "intervals": [0, 1, 3, 5, 7, 8, 11]},
    "persian":           {"cat": "exotic",    "en": "Persian",                    "intervals": [0, 1, 4, 5, 6, 8, 11]},
    "hirajoshi":         {"cat": "exotic",    "en": "Hirajoshi (Japanese)",       "intervals": [0, 2, 3, 7, 8]},
    "in_sen":            {"cat": "exotic",    "en": "In Sen (Japanese)",          "intervals": [0, 1, 5, 7, 10]},
    "egyptian":          {"cat": "exotic",    "en": "Egyptian (Suspended Pentatonic)", "intervals": [0, 2, 5, 7, 10]},
    "enigmatic":         {"cat": "exotic",    "en": "Enigmatic",                  "intervals": [0, 1, 4, 6, 8, 10, 11]},
}

# key mode name (player key badge / Krumhansl output) -> scale id
MODE_TO_SCALE = {
    "major": "major", "ionian": "major", "minor": "minor", "aeolian": "minor",
    "dorian": "dorian", "phrygian": "phrygian", "lydian": "lydian", "mixolydian": "mixolydian",
    "locrian": "locrian", "harmonic_minor": "harmonic_minor", "melodic_minor": "melodic_minor",
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def scale_pcs(tonic_pc: int, scale_id: str) -> frozenset[int]:
    """Pitch classes of `scale_id` built on `tonic_pc`. Unknown id -> chromatic."""
    sc = SCALES.get(scale_id) or SCALES["chromatic"]
    return frozenset((tonic_pc + iv) % 12 for iv in sc["intervals"])


def scale_for_mode(mode: str) -> str:
    return MODE_TO_SCALE.get((mode or "major").lower(), "major")


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"
