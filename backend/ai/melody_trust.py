"""Is a served melody plausibly the lead line? Shared by the accompaniment
endpoint (RH vocal-avoidance / LH dedupe / pedal) and the melody endpoint
(melody_quality flag + bass-leak note drop). Pure functions, no I/O."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .melody_candidate import INSTRUMENT_LEAD, SOLO_PIANO_POLYPHONIC, VOCAL_STEM_CREPE

# Resolver-selected non-vocal candidates: the vocal gate refusing the song is
# expected (that is why they were chosen), so the gate verdict is ignored;
# the content checks (notes / coverage / bass leak) still apply.
NON_VOCAL_CANDIDATES = (SOLO_PIANO_POLYPHONIC, INSTRUMENT_LEAD)

# Accompaniment planning trusts a melody only when it is plausibly the sung /
# lead line. full_mix_pyin on instrumentals or bass-heavy mixes returns the LH
# bass line (LiveChord-gyjt: Unchained Melody had 107/206 notes below MIDI 55),
# and Auto's RH vocal-avoidance + the LH collision dedupe then hollow out the
# accompaniment around notes nobody sings.
MELODY_TRUST_LOW_MIDI = 55
MELODY_TRUST_MAX_LOW_FRACTION = 0.30
MELODY_TRUST_MIN_COVERAGE = 0.40
MELODY_TRUST_MIN_NOTES = 20


def melody_trust(events: List[Dict[str, Any]], *, source_id: str = "",
                 song_duration_s: float = 0.0,
                 gate_predict_vocal: Optional[bool] = None) -> Dict[str, Any]:
    """Decide whether accompaniment generation may plan around ``events``.

    vocal_stem_crepe (resolver-selected, gate passed) is always trusted. Any
    other source is trusted only when the vocal gate did not refuse the song,
    the melody has enough notes, its active time covers >= 40 % of the song,
    and <= 30 % of its notes sit below MIDI 55 (bass leak signature).
    """

    notes = [e for e in events or [] if isinstance(e, dict)]
    n = len(notes)
    meta: Dict[str, Any] = {"source": source_id or "unknown", "notes": n}
    if not n:
        meta.update(trusted=False, reason="no_melody")
        return meta

    def _st(e):
        try:
            return float(e.get("start", e.get("time", 0.0)) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _en(e):
        try:
            end = e.get("end")
            if end is None:
                end = _st(e) + float(e.get("duration", 0.0) or 0.0)
            return float(end)
        except (TypeError, ValueError):
            return _st(e)

    def _pitch(e):
        try:
            return float(e.get("midi", e.get("pitch", 60)) or 60)
        except (TypeError, ValueError):
            return 60.0

    active = sum(max(0.0, _en(e) - _st(e)) for e in notes)
    span = max([song_duration_s] + [_en(e) for e in notes])
    coverage = round(active / span, 4) if span > 0 else 0.0
    low_fraction = round(sum(1 for e in notes if _pitch(e) < MELODY_TRUST_LOW_MIDI) / n, 4)
    meta.update(coverage=coverage, low_fraction=low_fraction)

    if source_id == VOCAL_STEM_CREPE:
        meta.update(trusted=True, reason="vocal_stem_crepe")
        return meta
    if source_id in NON_VOCAL_CANDIDATES:
        gate_predict_vocal = None
    # Content verdicts first: they tell the player WHAT is wrong with the
    # notes; the gate verdict only says the song is not vocal-led.
    if n < MELODY_TRUST_MIN_NOTES:
        meta.update(trusted=False, reason="too_few_notes")
        return meta
    if low_fraction > MELODY_TRUST_MAX_LOW_FRACTION:
        meta.update(trusted=False, reason="bass_leak")
        return meta
    if coverage < MELODY_TRUST_MIN_COVERAGE:
        meta.update(trusted=False, reason="low_coverage")
        return meta
    if gate_predict_vocal is False:
        meta.update(trusted=False, reason="vocal_gate_refused")
        return meta
    meta.update(trusted=True, reason="full_mix_ok")
    return meta




BASS_LEAK_FOLD_SEMITONES = 12


def drop_bass_leak_notes(events: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    """Remove notes more than an octave below the median pitch.

    Same rule melody_extractor_v2 applies to Basic Pitch output. The player
    used to octave-fold these up into the melody register, which is exactly
    the "右手旋律夾雜左手低音" complaint on Chopin / Greensleeves. Returns
    (kept_events, dropped_count).
    """

    notes = [e for e in events or [] if isinstance(e, dict)]
    if len(notes) < 2:
        return notes, 0
    pitches = sorted(_pitch(e) for e in notes)
    median = pitches[len(pitches) // 2]
    floor = median - BASS_LEAK_FOLD_SEMITONES
    kept = [e for e in notes if _pitch(e) >= floor]
    return kept, len(notes) - len(kept)


def _pitch(e: Dict[str, Any]) -> float:
    try:
        return float(e.get("midi", e.get("pitch", 60)) or 60)
    except (TypeError, ValueError):
        return 60.0
