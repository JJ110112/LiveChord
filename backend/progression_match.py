"""Transposition-invariant chord-progression matching.

Used by the Progression Library "matching songs" feature: given a roman-numeral
progression (a list of (degree_semitone, is_minor) tokens), find library songs
whose detected chord sequence contains that progression in ANY key.

Matching is done on the **root-interval + major/minor pattern**, NOT on the
detected key. This deliberately ignores the (noisy) `key` field — a song that
plays C-G-Am-F matches the I-V-vi-IV query whether its key was detected as C,
Am, or anything else. We slide all 12 transpositions of the query over the
song's chord sequence and look for a contiguous run.

Each chord is packed into a single character `chr(48 + pc*2 + minor)` (values
'0'..'G', all JSON-safe) so a song's progression is a short string and matching
reduces to a handful of fast substring searches.
"""
from __future__ import annotations

_NOTE_PC = {
    "C": 0, "B#": 0,
    "C#": 1, "DB": 1,
    "D": 2,
    "D#": 3, "EB": 3,
    "E": 4, "FB": 4,
    "F": 5, "E#": 5,
    "F#": 6, "GB": 6,
    "G": 7,
    "G#": 8, "AB": 8,
    "A": 9,
    "A#": 10, "BB": 10,
    "B": 11, "CB": 11,
}

_PACK_BASE = 48  # '0'


def chord_to_token(name: str):
    """Parse a chord name into (root_pc, is_minor), or None for no-chord.

    is_minor is True for minor / diminished / half-diminished qualities; major,
    dominant, augmented, and suspended chords map to major (False). Slash basses
    (``C/E``) use the chord root, not the bass.
    """
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    if not s or s.upper() in ("N", "NC", "N.C.", "X"):
        return None
    # Root letter + optional accidental. A 'b' or '#' immediately after the root
    # letter is always an accidental (chord qualities never start with one).
    letter = s[0].upper()
    if letter not in "ABCDEFG":
        return None
    i = 1
    lookup = letter
    if i < len(s) and s[i] in "#b":
        lookup = letter + ("#" if s[i] == "#" else "B")
        i += 1
    pc = _NOTE_PC.get(lookup)
    if pc is None:
        return None
    return pc, _quality_is_minor(s[i:])


def _quality_is_minor(q: str) -> bool:
    if not q:
        return False
    ql = q.lower()
    # Diminished / half-diminished are treated as minor-family.
    if ql.startswith("dim") or ql.startswith("°") or q.startswith("o") or ql.startswith("ø"):
        return True
    if "m7b5" in ql or "m7-5" in ql:
        return True
    # Minor: starts with 'm' but not 'maj'/'M' major-seventh notation.
    if ql.startswith("maj") or ql.startswith("ma7") or ql.startswith("Δ") or ql.startswith("^"):
        return False
    if ql.startswith("m") or ql.startswith("min") or ql.startswith("-"):
        return True
    return False


def pack_token(pc: int, is_minor: bool) -> str:
    return chr(_PACK_BASE + (pc % 12) * 2 + (1 if is_minor else 0))


def chords_to_packed(chord_names) -> str:
    """Pack an ordered chord-name list into a deduped (consecutive) token string."""
    out = []
    prev = None
    for name in chord_names:
        tok = chord_to_token(name)
        if tok is None:
            continue
        ch = pack_token(tok[0], tok[1])
        if ch != prev:
            out.append(ch)
            prev = ch
    return "".join(out)


def query_variants(query_tokens) -> list[str]:
    """All 12 transpositions of a query as packed strings.

    query_tokens: list of (degree_semitone, is_minor). Consecutive duplicates are
    removed first so progressions with repeated bars (e.g. 12-bar blues
    I7-I7-I7-I7-IV7...) match against the consecutive-deduped song sequences.
    """
    deduped = []
    prev = None
    for deg, minor in query_tokens:
        cur = (deg % 12, bool(minor))
        if cur != prev:
            deduped.append(cur)
            prev = cur
    variants = []
    for t in range(12):
        variants.append("".join(pack_token(deg + t, minor) for (deg, minor) in deduped))
    return variants


def song_matches(packed_song: str, variants: list[str]) -> bool:
    for v in variants:
        if v and v in packed_song:
            return True
    return False
