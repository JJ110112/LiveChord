"""Melody note-event schema v2 normalization.

The melody pipeline historically exposed ``start``/``end`` events only. Phase 3
keeps those legacy fields for the player, but also stamps the canonical note
schema used by accompaniment, score rendering, and MIDI export.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .note_continuity import repair_note_continuity


MELODY_EVENT_SCHEMA_VERSION = 2
MELODY_VOICE_LANE = "rh_melody"


def finalize_melody_events(
    events: List[Dict[str, Any]],
    *,
    bpm: float = 120.0,
    tempo_curve: Optional[List[Dict[str, Any]]] = None,
    time_signature: str = "4/4",
    apply_continuity: bool = True,
) -> List[Dict[str, Any]]:
    """Return melody events stamped with schema v2 and canonical durations.

    ``start``/``end`` are preserved for legacy frontend consumers. ``time`` and
    ``duration`` are the canonical schema-v2 fields; after continuity repair,
    ``end`` is synchronized to ``time + duration``. ``pitch`` and ``midi`` must
    stay equal for melody events; call this finalizer again after any pitch edit.
    """

    normalized = [_normalize_event(event) for event in events or []]
    normalized = [event for event in normalized if event is not None]
    normalized.sort(key=lambda event: (event["time"], event["pitch"]))

    if apply_continuity:
        normalized = repair_note_continuity(
            normalized,
            bpm=bpm,
            tempo_curve=tempo_curve,
            time_signature=time_signature,
            hand="right",
            role="melody",
            chord_boundaries=[],
        )

    for event in normalized:
        _sync_legacy_bounds(event)
    return normalized


def finalize_melody_payload(
    payload: Any,
    *,
    path: str = "",
    bpm: float = 120.0,
    tempo_curve: Optional[List[Dict[str, Any]]] = None,
    time_signature: str = "4/4",
) -> Dict[str, Any]:
    """Normalize a cached or freshly extracted melody payload."""

    if isinstance(payload, dict):
        events = payload.get("melody", [])
        result = copy.deepcopy(payload)
    elif isinstance(payload, list):
        events = payload
        result = {}
    else:
        events = []
        result = {}

    if path and not result.get("path"):
        result["path"] = path
    result["schema_version"] = MELODY_EVENT_SCHEMA_VERSION
    result["melody"] = finalize_melody_events(
        events,
        bpm=bpm,
        tempo_curve=tempo_curve,
        time_signature=time_signature,
    )
    return result


def melody_context_from_chord_cache(song_hash: str) -> Dict[str, Any]:
    """Best-effort BPM / tempo-curve / meter context from chord cache."""

    context = {"bpm": 120.0, "tempo_curve": None, "time_signature": "4/4"}
    if not song_hash:
        return context
    try:
        try:
            from chord_cache import chord_file_for
        except ImportError:
            from backend.chord_cache import chord_file_for  # type: ignore
        chords_file = chord_file_for(song_hash)
        if not chords_file.is_file():
            return context
        chord_data = json.loads(chords_file.read_text(encoding="utf-8"))
        context["bpm"] = float(chord_data.get("bpm") or 120.0)
        context["tempo_curve"] = chord_data.get("tempo_curve") or None
        context["time_signature"] = (
            chord_data.get("time_signature")
            or chord_data.get("meter")
            or "4/4"
        )
    except Exception:
        return context
    return context


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write a JSON cache file without exposing partial content to readers."""

    content = json.dumps(payload, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _normalize_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(event, dict):
        return None

    start = _as_float(event.get("time", event.get("start", 0.0)), 0.0)
    duration_value = event.get("duration")
    if duration_value is None:
        end_value = _as_float(event.get("end"), start)
        duration = max(0.0, end_value - start)
    else:
        duration = max(0.0, _as_float(duration_value, 0.0))
    if duration <= 0:
        return None

    midi = _as_int(event.get("midi", event.get("pitch")), None)
    if midi is None:
        return None

    normalized = copy.deepcopy(event)
    normalized["schema_version"] = MELODY_EVENT_SCHEMA_VERSION
    normalized["voice_lane"] = MELODY_VOICE_LANE
    normalized["time"] = round(start, 6)
    normalized["duration"] = round(duration, 6)
    normalized["start"] = round(start, 6)
    normalized["end"] = round(start + duration, 6)
    normalized["pitch"] = midi
    normalized["midi"] = midi
    normalized["gate_ratio"] = _clamp_gate_ratio(event.get("gate_ratio", 1.0))
    return normalized


def _sync_legacy_bounds(event: Dict[str, Any]) -> None:
    start = _as_float(event.get("time", event.get("start", 0.0)), 0.0)
    duration = max(0.0, _as_float(event.get("duration"), 0.0))
    event["time"] = round(start, 6)
    event["duration"] = round(duration, 6)
    event["start"] = round(start, 6)
    event["end"] = round(start + duration, 6)
    event["pitch"] = _as_int(event.get("pitch", event.get("midi")), 0)
    event["midi"] = _as_int(event.get("midi", event.get("pitch")), 0)
    event["schema_version"] = MELODY_EVENT_SCHEMA_VERSION
    event["voice_lane"] = MELODY_VOICE_LANE
    event["gate_ratio"] = _clamp_gate_ratio(event.get("gate_ratio", 1.0))


def _clamp_gate_ratio(value: Any, default: float = 1.0) -> float:
    gate = _as_float(value, default)
    return round(max(0.05, min(1.0, gate)), 4)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: Optional[int]) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default
