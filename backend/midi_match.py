"""Strict-ish track-name ↔ MIDI-filename matcher.

The original loose substring matcher caused 10k false-positive
auto-imports across the library (LiveChord-a7c). Auto MIDI ingest has
been off since 2026-05-11 (d479832) but the helper is still called by
``/api/chords/midi-search`` and would be re-activated if the auto path
flips back on, so the matcher itself is hardened here. See
``_midi_matches`` for the rule set.

This module is dependency-free (no FastAPI / sqlite / etc) so the
matcher can be unit-tested without standing up the full backend.
"""

from __future__ import annotations

import re


# Functional words that carry no song-identity information. A keyword
# overlap powered only by these is meaningless — adding them to the
# stoplist stopped jazz standards collapsing onto unrelated MIDIs.
_MIDI_KEYWORD_STOPWORDS = frozenset({
    # Articles, prepositions, conjunctions
    "the", "a", "an", "of", "in", "at", "on", "and", "or", "for", "to",
    "from", "by", "as", "but", "with", "without",
    # Pronouns / determiners — "Be With Me" collided with anything
    # containing "me" or "with" until these landed here.
    "me", "my", "mine", "you", "your", "yours", "he", "his", "she", "her",
    "it", "its", "we", "our", "ours", "us", "they", "them", "their",
    "this", "that", "these", "those",
    # Auxiliaries / common functional words
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must",
    "just", "only", "all", "any", "no", "not",
    "what", "who", "whom", "why", "when", "where", "how", "which",
    # Release / format noise
    "live", "official", "video", "music", "lyric", "lyrics",
    "remaster", "remastered", "version", "studio", "mtv", "time", "aligned",
    "bpm", "chordify", "audio", "song", "single", "ep", "album", "track",
    "feat", "ft", "edit", "mix", "radio", "instrumental",
})


def normalize_name(name: str) -> str:
    """Lower-case, replace _/- with space, strip punctuation, keep CJK."""
    name = name.lower()
    name = re.sub(r"[_\-]", " ", name)
    name = re.sub(r"[^a-z0-9\s一-鿿぀-ヿ가-힯]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _has_cjk(text: str) -> bool:
    """True iff ``text`` has any CJK / Japanese kana / Hangul characters."""
    return any(
        "一" <= c <= "鿿"   # CJK unified ideographs
        or "぀" <= c <= "ヿ"  # hiragana + katakana
        or "가" <= c <= "힯"  # hangul syllables
        for c in text
    )


def extract_keywords(name: str) -> set:
    """Return meaningful keywords (no stopwords, no digits, len ≥ 2)."""
    words = set(normalize_name(name).split())
    return {
        w for w in words
        if len(w) > 1 and w not in _MIDI_KEYWORD_STOPWORDS and not w.isdigit()
    }


def midi_matches(song_name: str, midi_fname: str) -> bool:
    """Decide whether ``midi_fname`` plausibly carries chords for ``song_name``.

    History: the bare-substring predecessor matched ``"Vem"`` against
    ``"Beethoven - Moonlight Sonata (1st Movement).mid"`` because
    ``"vem"`` sits inside ``"movement"`` — 10k false-positive ingests
    across the library (LiveChord-a7c). Hardening:

    * Word-boundary check on the ASCII substring path (``\\bX\\b``)
    * Single-word ASCII names only match on strict equality after
      normalization — too generic to disambiguate ("Silence" vs
      "Sound of Silence", "You" vs anything ending in "you")
    * Expanded functional-word stoplist
    * Require ≥3 keyword overlap (was ≥2) on the fallback path

    CJK names keep plain substring matching since each CJK character is
    itself a word boundary and the CJK MIDI library is small enough
    that collisions are unlikely.
    """
    sn = normalize_name(song_name)
    mn = normalize_name(midi_fname.replace(".mid", "").replace(".midi", ""))
    if not sn or not mn:
        return False

    # Strict equality always trusted — handles "Spain" → "Spain.mid".
    if sn == mn:
        return True

    if _has_cjk(sn) or _has_cjk(mn):
        shorter, longer = (sn, mn) if len(sn) <= len(mn) else (mn, sn)
        # ≥2 chars on the shorter side; single CJK chars are too common.
        if len(shorter) >= 2 and shorter in longer:
            return True
    else:
        shorter, longer = (sn, mn) if len(sn) <= len(mn) else (mn, sn)
        words = shorter.split()
        if len(words) >= 2:
            # Multi-word: word-boundary substring is safe enough.
            if re.search(r"\b" + re.escape(shorter) + r"\b", longer):
                return True
        # Single-word ASCII shorter side: only the strict-equal path
        # above is allowed. Letting it through here matches "Silence" to
        # any MIDI ending in "...silence" and "Move" to "...movement".

    sk = extract_keywords(song_name)
    mk = extract_keywords(midi_fname)
    if not sk or not mk:
        return False
    overlap = len(sk & mk)
    min_len = min(len(sk), len(mk))
    return overlap >= max(3, min_len * 0.6)


# Backwards-compatible aliases used by chord_api.
_normalize_name = normalize_name
_extract_keywords = extract_keywords
_midi_matches = midi_matches
