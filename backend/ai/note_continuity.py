"""Canonical note-duration continuity repair.

This module is intentionally pure: callers pass note events in, receive a new
list out, and can use dry-run metadata for shadow comparison before wiring the
repair into accompaniment or melody pipelines.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .beat_helpers import beat_duration_at


_EPSILON = 1e-6
_ONSET_GROUP_TOLERANCE = 0.02


@dataclass
class _NoteGroup:
    indices: List[int]
    start: float
    end: float
    strum_id: Optional[str]


def repair_note_continuity(
    events: List[Dict[str, Any]],
    *,
    bpm: float,
    tempo_curve: Optional[List[Dict[str, Any]]] = None,
    time_signature: str = "4/4",
    hand: str = "",
    role: str = "accompaniment",
    chord_boundaries: Optional[List[float]] = None,
    max_gap_beats: float = 0.5,
    preserve_articulations: bool = True,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Return note events with small same-lane rests filled.

    ``duration`` is treated as the canonical musical duration. Short playback
    touch is represented by ``gate_ratio``/``articulation`` and is preserved.
    The input list and nested metadata are never mutated.
    """

    del time_signature  # Kept in the public API for meter-aware callers.
    del preserve_articulations  # Articulation fields are preserved by design.

    if not events:
        return []

    repaired = copy.deepcopy(events)
    fallback_bpm = _safe_bpm(bpm)
    max_gap_beats = max(0.0, float(max_gap_beats))
    lane_to_indices: Dict[str, List[int]] = {}
    for idx, event in enumerate(repaired):
        lane_to_indices.setdefault(_voice_lane(event, hand=hand, role=role), []).append(idx)

    boundaries = _normalize_boundaries(chord_boundaries)
    may_cross_chord = _role_allows_chord_crossing(role)

    for indices in lane_to_indices.values():
        ordered = sorted(indices, key=lambda i: (_event_time(repaired[i]), _event_pitch(repaired[i]), i))
        groups = _build_groups(repaired, ordered)
        for pos, group in enumerate(groups[:-1]):
            next_group = groups[pos + 1]
            target_end = next_group.start

            if not may_cross_chord:
                boundary = _first_boundary_between(boundaries, group.end, target_end)
                if boundary is not None:
                    target_end = boundary

            if target_end <= group.end + _EPSILON:
                continue

            gap = target_end - group.end
            max_gap = beat_duration_at(tempo_curve, group.start, fallback_bpm=fallback_bpm) * max_gap_beats
            if gap > max_gap + _EPSILON:
                continue

            _extend_group(
                repaired,
                group,
                target_end,
                reason=_extension_reason(repaired, group, next_group),
                dry_run=dry_run,
            )

    return repaired


def _event_time(event: Dict[str, Any]) -> float:
    return float(event.get("time", event.get("start", 0.0)) or 0.0)


def _safe_bpm(bpm: float) -> float:
    bpm = float(bpm or 0.0)
    return bpm if bpm > 0 else 120.0


def _event_duration(event: Dict[str, Any]) -> float:
    return max(0.0, float(event.get("duration", 0.0) or 0.0))


def _event_end(event: Dict[str, Any]) -> float:
    return _event_time(event) + _event_duration(event)


def _event_pitch(event: Dict[str, Any]) -> int:
    return int(event.get("pitch", event.get("note", 0)) or 0)


def _voice_lane(event: Dict[str, Any], *, hand: str, role: str) -> str:
    lane = event.get("voice_lane")
    if lane:
        return str(lane)

    event_hand = event.get("hand") or hand or "unknown"
    event_role = event.get("role") or role or "notes"
    return f"{event_hand}:{event_role}"


def _strum_id(event: Dict[str, Any]) -> Optional[str]:
    value = event.get("strum_id")
    if value is None or value == "":
        return None
    return str(value)


def _normalize_boundaries(chord_boundaries: Optional[Iterable[float]]) -> List[float]:
    if not chord_boundaries:
        return []
    return sorted(float(boundary) for boundary in chord_boundaries)


def _role_allows_chord_crossing(role: str) -> bool:
    return "melody" in (role or "").lower()


def _first_boundary_between(boundaries: List[float], start: float, end: float) -> Optional[float]:
    for boundary in boundaries:
        if start + _EPSILON < boundary < end - _EPSILON:
            return boundary
    return None


def _build_groups(events: List[Dict[str, Any]], ordered_indices: List[int]) -> List[_NoteGroup]:
    groups: List[_NoteGroup] = []
    current: List[int] = []

    for idx in ordered_indices:
        if not current:
            current = [idx]
            continue

        if _belongs_to_group(events, current, idx):
            current.append(idx)
            continue

        groups.append(_make_group(events, current))
        current = [idx]

    if current:
        groups.append(_make_group(events, current))

    return groups


def _belongs_to_group(events: List[Dict[str, Any]], current: List[int], candidate_idx: int) -> bool:
    candidate = events[candidate_idx]
    current_strum = _strum_id(events[current[0]])
    candidate_strum = _strum_id(candidate)

    if current_strum is not None or candidate_strum is not None:
        return current_strum is not None and current_strum == candidate_strum

    first_start = _event_time(events[current[0]])
    return abs(_event_time(candidate) - first_start) <= _ONSET_GROUP_TOLERANCE


def _make_group(events: List[Dict[str, Any]], indices: List[int]) -> _NoteGroup:
    starts = [_event_time(events[idx]) for idx in indices]
    ends = [_event_end(events[idx]) for idx in indices]
    return _NoteGroup(
        indices=list(indices),
        start=min(starts),
        end=max(ends),
        strum_id=_strum_id(events[indices[0]]),
    )


def _extend_group(
    events: List[Dict[str, Any]],
    group: _NoteGroup,
    target_end: float,
    *,
    reason: str,
    dry_run: bool,
) -> None:
    target_end = round(float(target_end), 6)
    for idx in group.indices:
        event = events[idx]
        start = _event_time(event)
        source_duration = _event_duration(event)
        target_duration = max(0.0, round(target_end - start, 6))
        if target_duration <= source_duration + _EPSILON:
            continue

        meta = dict(event.get("continuity_meta") or {})
        if dry_run:
            meta.update(
                {
                    "would_extend_to": target_end,
                    "would_duration": target_duration,
                    "would_extend_by": round(target_duration - source_duration, 6),
                    "reason": reason,
                    "dry_run": True,
                }
            )
        else:
            event["duration"] = target_duration
            meta.update(
                {
                    "source_duration": round(source_duration, 6),
                    "extended_to": target_end,
                    "extended_by": round(target_duration - source_duration, 6),
                    "reason": reason,
                }
            )
        event["continuity_meta"] = meta


def _extension_reason(
    events: List[Dict[str, Any]],
    group: _NoteGroup,
    next_group: _NoteGroup,
) -> str:
    if group.strum_id is not None:
        return "small_gap_strum_group"

    group_pitches = _pitch_set(events, group.indices)
    next_pitches = _pitch_set(events, next_group.indices)
    if group_pitches == next_pitches:
        return "small_gap_same_pitch"
    if len(group.indices) > 1:
        return "small_gap_chord_group"
    return "small_gap_same_voice"


def _pitch_set(events: List[Dict[str, Any]], indices: List[int]) -> Tuple[int, ...]:
    return tuple(sorted({_event_pitch(events[idx]) for idx in indices}))
