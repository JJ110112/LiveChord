"""Symbolic solo-piano RH melody selector.

This is the Stage-2 baseline from the RH melody plan: a Skyline candidate set
followed by a small Temperley-style Viterbi pass over range, interval, and key
priors. Stage 1 polyphonic transcription remains pluggable; this module only
needs note events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .melody_schema import finalize_melody_events


KEY_MAJOR_PCS = {
    "C": 0, "G": 7, "D": 2, "A": 9, "E": 4, "B": 11,
    "F#": 6, "Db": 1, "Ab": 8, "Eb": 3, "Bb": 10, "F": 5,
}
MINOR_RELATIVE_MAJOR = {
    "A": "C", "E": "G", "B": "D", "F#": "A", "C#": "E", "G#": "B",
    "D#": "F#", "A#": "Db", "D": "F", "G": "Bb", "C": "Eb", "F": "Ab",
}


@dataclass(frozen=True)
class PianoNote:
    start: float
    end: float
    midi: int
    velocity: float = 64.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def select_right_hand_melody(
    notes: Iterable[Dict[str, Any]],
    *,
    key: str = "C",
    bpm: float = 120.0,
    tempo_curve: Optional[List[Dict[str, Any]]] = None,
    time_signature: str = "4/4",
    onset_cluster_s: float = 0.08,
    max_candidates_per_cluster: int = 4,
) -> List[Dict[str, Any]]:
    """Select a monophonic RH melody from polyphonic note events."""

    normalized = _normalize_notes(notes)
    if not normalized:
        return []
    clusters = _cluster_by_onset(normalized, onset_cluster_s)
    candidate_sets = [
        _skyline_candidates(cluster, max_candidates_per_cluster)
        for cluster in clusters
    ]
    candidate_sets = [c for c in candidate_sets if c]
    if not candidate_sets:
        return []

    path = _viterbi(candidate_sets, key=key)
    events = _post_merge([
        {
            "start": round(note.start, 6),
            "end": round(note.end, 6),
            "midi": note.midi,
            "confidence": round(_local_score(note, key), 4),
        }
        for note in path
    ])
    return finalize_melody_events(
        events,
        bpm=bpm,
        tempo_curve=tempo_curve,
        time_signature=time_signature,
    )


def _normalize_notes(notes: Iterable[Dict[str, Any]]) -> List[PianoNote]:
    out: List[PianoNote] = []
    for raw in notes or []:
        if not isinstance(raw, dict):
            continue
        start = _first_float(raw.get("start"), raw.get("time"), raw.get("onset"))
        end = _first_float(raw.get("end"), None)
        duration = _first_float(raw.get("duration"), None)
        midi = _first_int(raw.get("midi"), raw.get("pitch"), raw.get("note"))
        if start is None or midi is None:
            continue
        if end is None and duration is not None:
            end = start + duration
        if end is None or end <= start:
            continue
        velocity = _first_float(raw.get("velocity"), 64.0)
        out.append(PianoNote(start=float(start), end=float(end), midi=int(midi), velocity=float(velocity or 64.0)))
    out.sort(key=lambda n: (n.start, -n.midi, -n.velocity))
    return out


def _cluster_by_onset(notes: List[PianoNote], window: float) -> List[List[PianoNote]]:
    clusters: List[List[PianoNote]] = []
    current: List[PianoNote] = []
    anchor: Optional[float] = None
    for note in notes:
        if anchor is None or note.start - anchor <= window:
            current.append(note)
            if anchor is None:
                anchor = note.start
            continue
        clusters.append(current)
        current = [note]
        anchor = note.start
    if current:
        clusters.append(current)
    return clusters


def _skyline_candidates(cluster: List[PianoNote], limit: int) -> List[PianoNote]:
    # Keep the top pitch plus a few high/strong alternatives; LH/bass material is
    # allowed only when it is all the transcription gives us.
    ranked = sorted(
        cluster,
        key=lambda n: (
            n.midi,
            min(127.0, max(1.0, n.velocity)),
            n.duration,
        ),
        reverse=True,
    )
    high_enough = [n for n in ranked if n.midi >= 52]
    pool = high_enough or ranked
    dedup: List[PianoNote] = []
    seen = set()
    for note in pool:
        key = (round(note.start, 2), note.midi)
        if key in seen:
            continue
        dedup.append(note)
        seen.add(key)
        if len(dedup) >= limit:
            break
    return dedup


def _viterbi(candidate_sets: List[List[PianoNote]], *, key: str) -> List[PianoNote]:
    scores: List[Dict[int, float]] = []
    back: List[Dict[int, int]] = []

    first_scores = {i: _local_score(note, key) for i, note in enumerate(candidate_sets[0])}
    scores.append(first_scores)
    back.append({})

    for idx in range(1, len(candidate_sets)):
        layer: Dict[int, float] = {}
        layer_back: Dict[int, int] = {}
        prev_candidates = candidate_sets[idx - 1]
        for j, note in enumerate(candidate_sets[idx]):
            best_score = None
            best_prev = 0
            local = _local_score(note, key)
            for i, prev in enumerate(prev_candidates):
                transition = _transition_score(prev, note)
                score = scores[idx - 1][i] + local + transition
                if best_score is None or score > best_score:
                    best_score = score
                    best_prev = i
            layer[j] = float(best_score if best_score is not None else local)
            layer_back[j] = best_prev
        scores.append(layer)
        back.append(layer_back)

    last_index = max(scores[-1], key=lambda i: scores[-1][i])
    indexes = [last_index]
    for idx in range(len(candidate_sets) - 1, 0, -1):
        last_index = back[idx].get(last_index, 0)
        indexes.append(last_index)
    indexes.reverse()
    return [candidate_sets[i][j] for i, j in enumerate(indexes)]


def _local_score(note: PianoNote, key: str) -> float:
    # Range prior: solo-piano melody usually sits in C4-C6, with soft shoulders.
    if 60 <= note.midi <= 84:
        range_score = 1.0
    elif 52 <= note.midi < 60:
        range_score = 0.35 + (note.midi - 52) * 0.06
    elif 84 < note.midi <= 96:
        range_score = 0.9 - (note.midi - 84) * 0.04
    else:
        range_score = -0.35

    velocity_score = min(1.0, max(0.0, note.velocity / 127.0)) * 0.35
    duration_score = min(0.4, note.duration * 0.5)
    key_score = 0.16 if _in_major_key(note.midi, key) else -0.08
    return range_score + velocity_score + duration_score + key_score


def _transition_score(prev: PianoNote, note: PianoNote) -> float:
    interval = abs(note.midi - prev.midi)
    if interval <= 2:
        interval_score = 0.35
    elif interval <= 7:
        interval_score = 0.2 - interval * 0.02
    elif interval <= 12:
        interval_score = -0.15
    else:
        interval_score = -0.45 - min(1.0, (interval - 12) / 24.0)

    gap = max(0.0, note.start - prev.end)
    overlap = max(0.0, prev.end - note.start)
    continuity = -min(0.4, gap * 0.15) - min(0.25, overlap * 0.2)
    return interval_score + continuity


def _post_merge(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not events:
        return []
    merged: List[Dict[str, Any]] = [dict(events[0])]
    for event in events[1:]:
        prev = merged[-1]
        gap = float(event["start"]) - float(prev["end"])
        if event["midi"] == prev["midi"] and 0 <= gap <= 0.12:
            prev["end"] = event["end"]
            prev["confidence"] = round((float(prev.get("confidence") or 0) + float(event.get("confidence") or 0)) / 2, 4)
        else:
            merged.append(dict(event))
    return merged


def _in_major_key(midi: int, key: str) -> bool:
    tonic = KEY_MAJOR_PCS.get(_normalize_major_key(key), 0)
    return ((midi % 12) - tonic) % 12 in {0, 2, 4, 5, 7, 9, 11}


def _normalize_major_key(key: str) -> str:
    raw = str(key or "C").strip()
    if not raw:
        return "C"
    cleaned = raw.replace("major", "").replace("Major", "").strip()
    lower = cleaned.lower()
    if lower.endswith("minor"):
        tonic = cleaned[:-5].strip()
        return MINOR_RELATIVE_MAJOR.get(_normalize_tonic(tonic), "C")
    if lower.endswith("min"):
        tonic = cleaned[:-3].strip()
        return MINOR_RELATIVE_MAJOR.get(_normalize_tonic(tonic), "C")
    if len(cleaned) >= 2 and cleaned[-1] == "m":
        return MINOR_RELATIVE_MAJOR.get(_normalize_tonic(cleaned[:-1]), "C")
    return _normalize_tonic(cleaned)


def _normalize_tonic(value: str) -> str:
    token = str(value or "C").strip()
    if not token:
        return "C"
    first = token[0].upper()
    suffix = token[1:].replace("♭", "b").replace("♯", "#")
    if suffix.startswith("#"):
        return first + "#"
    if suffix.startswith("b"):
        return first + "b"
    return first


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_int(*values: Any) -> Optional[int]:
    value = _first_float(*values)
    return int(round(value)) if value is not None else None
