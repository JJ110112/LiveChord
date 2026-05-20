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
MELODY_CACHE_VERSION = "rhmelody-phase0"
MELODY_FAILURE_TAXONOMY = {
    "pyin_fine": {
        "description": "Current pYIN is musically usable; resolver should not switch away.",
        "post_filter_fixable": False,
    },
    "wrong_octave": {
        "description": "Correct contour but octave is displaced or jumps against the running median.",
        "post_filter_fixable": True,
    },
    "bass_leakage": {
        "description": "pYIN follows bass or left-hand material below the intended lead line.",
        "post_filter_fixable": True,
    },
    "wrong_line_backing_vocal": {
        "description": "pYIN follows harmony or backing vocal above/beside the lead.",
        "post_filter_fixable": False,
    },
    "wrong_line_accompaniment": {
        "description": "pYIN follows piano RH, guitar riff, pad, or accompaniment texture.",
        "post_filter_fixable": None,
    },
    "sparse_missing": {
        "description": "A lead exists but pYIN misses too many notes or phrases.",
        "post_filter_fixable": None,
    },
    "no_lead_present": {
        "description": "The audio genuinely has no stable RH lead line.",
        "post_filter_fixable": False,
    },
    "audio_quality": {
        "description": "Recording quality dominates the failure: reverb, clipping, noise, low bitrate, room bleed, or separation artifact.",
        "post_filter_fixable": False,
    },
    "no_issue_audible": {
        "description": "JSON or metrics look suspicious but playback is acceptable.",
        "post_filter_fixable": False,
    },
}
MELODY_SECONDARY_FLAGS = [
    "audio_quality",
    "quantization_jitter",
    "mixed_section_single_source",
    "source_intro_missing",
    "needs_ab_replay",
]


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
    result["melody_source"] = _default_melody_source(result.get("melody_source"))
    result["quality_flags"] = _default_quality_flags(
        result.get("quality_flags"),
        result["melody"],
        result["melody_source"],
    )
    result["melody_stats"] = _melody_stats(result["melody"])
    return result


def melody_review_taxonomy() -> Dict[str, Any]:
    """Return the frozen Phase 0 review taxonomy for admin/debug tools."""

    return {
        "primary_tags": copy.deepcopy(MELODY_FAILURE_TAXONOMY),
        "secondary_flags": list(MELODY_SECONDARY_FLAGS),
        "review_rule": (
            "Assign exactly one primary tag per reviewed song/segment; "
            "secondary flags are optional."
        ),
    }


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


def _default_melody_source(existing: Any) -> Dict[str, Any]:
    if isinstance(existing, dict):
        source = copy.deepcopy(existing)
    else:
        source = {}
    source.setdefault("id", "full_mix_pyin")
    source.setdefault("stem", "full_mix")
    source.setdefault("algorithm", "librosa.pyin")
    source.setdefault("song_type", "unknown")
    source.setdefault("song_type_confidence", None)
    source.setdefault("selected_by", "legacy_primary")
    source.setdefault("candidate_score", None)
    source.setdefault("margin_over_fallback", 0.0)
    source.setdefault("cache_version", MELODY_CACHE_VERSION)
    return source


def _default_quality_flags(
    existing: Any,
    events: List[Dict[str, Any]],
    melody_source: Dict[str, Any],
) -> List[str]:
    flags: List[str] = []
    if isinstance(existing, list):
        flags.extend(str(flag) for flag in existing if flag)
    elif isinstance(existing, str) and existing:
        flags.append(existing)
    if melody_source.get("id") == "full_mix_pyin" and "fallback_full_mix" not in flags:
        flags.append("fallback_full_mix")
    if not events and "empty_melody_cache" not in flags:
        flags.append("empty_melody_cache")
    return flags


def _melody_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    note_count = len(events or [])
    if note_count == 0:
        return {
            "note_count": 0,
            "duration_s": 0.0,
            "density_notes_per_s": 0.0,
            "midi_min": None,
            "midi_max": None,
            "midi_median": None,
            "confidence_avg": None,
        }

    start = min(_as_float(event.get("time", event.get("start")), 0.0) for event in events)
    end = max(_as_float(event.get("end"), _as_float(event.get("time", event.get("start")), 0.0)) for event in events)
    duration = max(0.0, end - start)
    midis = sorted(_as_int(event.get("midi", event.get("pitch")), 0) or 0 for event in events)
    confs = []
    for event in events:
        if event.get("confidence") is None:
            continue
        conf = _as_float(event.get("confidence"), None)
        if conf is not None:
            confs.append(conf)
    median_idx = note_count // 2
    if note_count % 2:
        midi_median = float(midis[median_idx])
    else:
        midi_median = (midis[median_idx - 1] + midis[median_idx]) / 2.0
    return {
        "note_count": note_count,
        "duration_s": round(duration, 3),
        "density_notes_per_s": round(note_count / duration, 4) if duration > 0 else 0.0,
        "midi_min": midis[0],
        "midi_max": midis[-1],
        "midi_median": round(midi_median, 2),
        "confidence_avg": round(sum(confs) / len(confs), 4) if confs else None,
    }


def _clamp_gate_ratio(value: Any, default: float = 1.0) -> float:
    gate = _as_float(value, default)
    return round(max(0.05, min(1.0, gate)), 4)


def _as_float(value: Any, default: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: Optional[int]) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default
