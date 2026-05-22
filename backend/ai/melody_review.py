"""Phase 0 RH melody review tagging and survey sampling helpers."""

from __future__ import annotations

import json
import os
import random
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .melody_schema import melody_review_taxonomy


REVIEW_SCHEMA_VERSION = 1
REVIEW_PHASE = "phase0"
REVIEW_DIR_NAME = "melody_reviews"
REVIEW_TAG_LOG_NAME = "phase0_tags.jsonl"
REVIEW_QUEUE_NAME = "phase0_survey_queue.jsonl"
REVIEW_SUMMARY_NAME = "phase0_survey_queue.summary.json"
REVIEW_REPORT_NAME = "phase0_survey_report.json"
PRODUCTION_DATA_DIR = Path(r"V:\data")
AUDIO_QUALITY_NOTES = {
    "none",
    "reverb_high",
    "clipped",
    "noisy",
    "low_bitrate",
    "live_room_bleed",
    "separation_artifact",
}


def validate_review_labels(
    failure_tag: str,
    secondary_flags: Optional[List[str]] = None,
    audio_quality_note: str = "none",
) -> Dict[str, Any]:
    """Validate Phase 0 labels and return taxonomy details for the tag."""

    taxonomy = melody_review_taxonomy()
    primary_tags = taxonomy["primary_tags"]
    allowed_secondary = set(taxonomy["secondary_flags"])
    tag = (failure_tag or "").strip()
    if tag not in primary_tags:
        allowed = ", ".join(sorted(primary_tags))
        raise ValueError(f"failure_tag must be one of: {allowed}")

    clean_secondary = []
    seen = set()
    for flag in secondary_flags or []:
        clean = str(flag or "").strip()
        if not clean:
            continue
        if clean not in allowed_secondary:
            allowed = ", ".join(sorted(allowed_secondary))
            raise ValueError(f"secondary flag '{clean}' must be one of: {allowed}")
        if clean not in seen:
            clean_secondary.append(clean)
            seen.add(clean)

    note = (audio_quality_note or "none").strip()
    if note not in AUDIO_QUALITY_NOTES:
        allowed = ", ".join(sorted(AUDIO_QUALITY_NOTES))
        raise ValueError(f"audio_quality_note must be one of: {allowed}")

    return {
        "failure_tag": tag,
        "tag_info": primary_tags[tag],
        "secondary_flags": clean_secondary,
        "audio_quality_note": note,
    }


def build_review_entry(
    *,
    debug_info: Dict[str, Any],
    reviewer: str,
    failure_tag: str,
    secondary_flags: Optional[List[str]] = None,
    audio_quality_note: str = "none",
    review_note: str = "",
    survey_id: str = "",
    segment: Optional[Dict[str, Any]] = None,
    machine_proxies: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a normalized JSONL review entry from debug metadata."""

    labels = validate_review_labels(failure_tag, secondary_flags, audio_quality_note)
    tag_info = labels["tag_info"]
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "phase": REVIEW_PHASE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": _clean_string(reviewer, 120),
        "survey_id": _clean_string(survey_id, 120),
        "query": debug_info.get("query", {}),
        "query_hash": debug_info.get("query_hash", ""),
        "song_hash": debug_info.get("song_hash", ""),
        "hash_recomputed": bool(debug_info.get("hash_recomputed")),
        "path": debug_info.get("path", ""),
        "lookup": debug_info.get("lookup", ""),
        "cache": debug_info.get("cache", {}),
        "melody_source": debug_info.get("melody_source", {}),
        "quality_flags": debug_info.get("quality_flags", []),
        "melody_stats": debug_info.get("melody_stats", {}),
        "failure_tag": labels["failure_tag"],
        "post_filter_fixable": bool(tag_info.get("post_filter_fixable")),
        "secondary_flags": labels["secondary_flags"],
        "audio_quality_note": labels["audio_quality_note"],
        "segment": _clean_segment(segment),
        "machine_proxies": machine_proxies or {},
        "review_note": _clean_string(review_note, 2000),
    }


def append_review_entry(data_dir: Path, entry: Dict[str, Any]) -> Path:
    """Append one review entry to the Phase 0 JSONL log."""

    review_dir = data_dir / REVIEW_DIR_NAME
    review_dir.mkdir(parents=True, exist_ok=True)
    log_file = review_dir / REVIEW_TAG_LOG_NAME
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with log_file.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
    return log_file


def resolve_review_data_dir(data_dir: Path) -> Path:
    """Return the data root that owns the Phase 0 review queue/log."""

    local_review_dir = data_dir / REVIEW_DIR_NAME
    if local_review_dir.exists():
        return data_dir
    production_review_dir = PRODUCTION_DATA_DIR / REVIEW_DIR_NAME
    if production_review_dir.exists():
        return PRODUCTION_DATA_DIR
    return data_dir


def read_survey_queue(data_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Path]:
    """Read the Phase 0 survey queue and summary sidecar."""

    review_dir = data_dir / REVIEW_DIR_NAME
    queue_file = review_dir / REVIEW_QUEUE_NAME
    summary_file = review_dir / REVIEW_SUMMARY_NAME
    rows = _read_jsonl(queue_file)
    summary: Dict[str, Any] = {}
    if summary_file.is_file():
        try:
            loaded = json.loads(summary_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary = loaded
        except Exception:
            summary = {}
    return rows, summary, queue_file


def read_latest_review_tags(data_dir: Path, survey_id: str = "") -> Tuple[Dict[str, Dict[str, Any]], Path]:
    """Read latest tag per (survey_id, song_hash/path) for admin display."""

    log_file = data_dir / REVIEW_DIR_NAME / REVIEW_TAG_LOG_NAME
    latest: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(log_file):
        if survey_id and row.get("survey_id") != survey_id:
            continue
        key = _review_key(row)
        if not key:
            continue
        previous = latest.get(key)
        if not previous or str(row.get("created_at") or "") >= str(previous.get("created_at") or ""):
            latest[key] = row
    return latest, log_file


def build_survey_report(
    queue_rows: List[Dict[str, Any]],
    latest_tags: Dict[str, Dict[str, Any]],
    *,
    queue_summary: Optional[Dict[str, Any]] = None,
    survey_id: str = "",
) -> Dict[str, Any]:
    """Aggregate Phase 0 survey completion and failure-mode distribution."""

    taxonomy = melody_review_taxonomy()
    primary_tags = taxonomy["primary_tags"]
    active_survey_id = survey_id or str((queue_summary or {}).get("survey_id") or "")
    rows = [
        dict(row)
        for row in queue_rows
        if not active_survey_id or row.get("survey_id") == active_survey_id
    ]

    tag_counts = Counter({tag: 0 for tag in primary_tags})
    secondary_counts: Counter[str] = Counter()
    audio_quality_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    fixable_counts = Counter({"true": 0, "false": 0})
    reviewed_items: List[Dict[str, Any]] = []

    for row in rows:
        key = _review_key(row)
        tag = latest_tags.get(key)
        if not tag:
            continue
        failure_tag = str(tag.get("failure_tag") or "")
        if failure_tag:
            tag_counts[failure_tag] += 1
        fixable_key = "true" if bool(tag.get("post_filter_fixable")) else "false"
        fixable_counts[fixable_key] += 1
        for flag in tag.get("secondary_flags") or []:
            secondary_counts[str(flag)] += 1
        audio_quality_counts[str(tag.get("audio_quality_note") or "none")] += 1
        source = tag.get("melody_source") if isinstance(tag.get("melody_source"), dict) else {}
        source_counts[str(source.get("id") or "unknown")] += 1
        reviewed_items.append({
            "sample_order": row.get("sample_order"),
            "song_hash": row.get("hash") or row.get("song_hash") or tag.get("song_hash") or "",
            "path": row.get("path") or tag.get("path") or "",
            "failure_tag": failure_tag,
            "post_filter_fixable": bool(tag.get("post_filter_fixable")),
            "secondary_flags": list(tag.get("secondary_flags") or []),
            "audio_quality_note": tag.get("audio_quality_note") or "none",
            "created_at": tag.get("created_at") or "",
        })

    reviewed = len(reviewed_items)
    total = len(rows)
    fixable_true = int(fixable_counts["true"])
    fixable_ratio = round(fixable_true / reviewed, 4) if reviewed else None
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "phase": REVIEW_PHASE,
        "source": "rh_melody_phase0_survey_report_v1",
        "survey_id": active_survey_id,
        "queue_summary": queue_summary or {},
        "total": total,
        "completed": reviewed,
        "pending": max(0, total - reviewed),
        "completion_ratio": round(reviewed / total, 4) if total else 0.0,
        "primary_tag_counts": dict(sorted(tag_counts.items())),
        "post_filter_fixable": {
            "true": fixable_true,
            "false": int(fixable_counts["false"]),
            "ratio": fixable_ratio,
            "decision_rule": "> 0.50 means Phase 1 post-filters are likely the next best investment.",
            "passes_phase1_threshold": (fixable_ratio is not None and fixable_ratio > 0.50),
        },
        "secondary_flag_counts": dict(sorted(secondary_counts.items())),
        "audio_quality_note_counts": dict(sorted(audio_quality_counts.items())),
        "melody_source_counts": dict(sorted(source_counts.items())),
        "reviewed_items": sorted(reviewed_items, key=lambda item: int(item.get("sample_order") or 0)),
    }


def write_survey_report(report: Dict[str, Any], output_path: Path, *, force: bool = False) -> Path:
    """Write a Phase 0 survey report JSON atomically."""

    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass force=True to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output_path)
    return output_path


def collect_survey_candidates(
    chord_root: Path,
    *,
    require_audio: bool = True,
    resolve_audio_path: Optional[Callable[[str], str]] = None,
    existing_audio_paths: Optional[set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Collect reviewable chord-cache records for Phase 0 random sampling."""

    from chord_cache import iter_chord_files

    candidates: List[Dict[str, Any]] = []
    stats = {
        "checked": 0,
        "missing_path": 0,
        "missing_audio": 0,
        "invalid_json": 0,
    }
    resolver = resolve_audio_path or _default_resolve_audio_path
    for chord_file in iter_chord_files(chord_root):
        stats["checked"] += 1
        try:
            data = json.loads(chord_file.read_text(encoding="utf-8"))
        except Exception:
            stats["invalid_json"] += 1
            continue
        path = str(data.get("path") or data.get("rel_path") or "").strip()
        if not path:
            stats["missing_path"] += 1
            continue
        if existing_audio_paths is not None:
            audio_path = path
            audio_exists = _normalize_library_path(path) in existing_audio_paths
        else:
            audio_path = resolver(path)
            audio_exists = bool(audio_path and os.path.isfile(audio_path))
        if require_audio and not audio_exists:
            stats["missing_audio"] += 1
            continue
        candidates.append({
            "hash": chord_file.stem,
            "path": path,
            "chord_file": str(chord_file),
            "audio_path": audio_path,
            "audio_exists": audio_exists,
            "source": data.get("source") or "",
            "beats_source": data.get("beats_source") or "",
            "chord_count": len(data.get("chords") or []),
            "duration_s": _first_float(
                data.get("duration"),
                data.get("duration_s"),
                data.get("audio_duration"),
            ),
        })
    stats["candidates"] = len(candidates)
    return candidates, stats


def collect_library_cache_candidates(cache_file: Path) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Collect playable survey candidates directly from library_cache tracks."""

    from chord_cache import song_hash

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    tracks = data.get("tracks") if isinstance(data, dict) else data
    stats = {
        "checked": 0,
        "missing_path": 0,
        "candidates": 0,
    }
    candidates: List[Dict[str, Any]] = []
    if not isinstance(tracks, list):
        return candidates, stats
    for track in tracks:
        stats["checked"] += 1
        if not isinstance(track, dict):
            stats["missing_path"] += 1
            continue
        path = str(track.get("path") or "").strip()
        if not path:
            stats["missing_path"] += 1
            continue
        candidates.append({
            "hash": song_hash(path),
            "path": path,
            "chord_file": "",
            "audio_path": path,
            "audio_exists": True,
            "source": "",
            "beats_source": "",
            "chord_count": 0,
            "duration_s": _first_float(track.get("duration"), track.get("duration_s")),
            "title": track.get("title") or "",
            "artist": track.get("artist") or "",
            "album": track.get("album") or "",
            "genre": track.get("genre") or "",
            "selection_source": "library_cache",
        })
    stats["candidates"] = len(candidates)
    return candidates, stats


def load_library_cache_paths(cache_file: Path) -> set[str]:
    """Return normalized track paths from a LiveChord library cache."""

    data = json.loads(cache_file.read_text(encoding="utf-8"))
    tracks = data.get("tracks") if isinstance(data, dict) else data
    paths = set()
    if not isinstance(tracks, list):
        return paths
    for track in tracks:
        if not isinstance(track, dict):
            continue
        path = str(track.get("path") or "").strip()
        if path:
            paths.add(_normalize_library_path(path))
    return paths


def sample_survey_candidates(
    candidates: Iterable[Dict[str, Any]],
    *,
    sample_size: int,
    seed: int,
) -> List[Dict[str, Any]]:
    """Return an equal-probability deterministic random sample."""

    rng = random.Random(seed)
    selected: List[Dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if len(selected) < sample_size:
            selected.append(dict(item))
            continue
        replace_at = rng.randint(0, index)
        if replace_at < sample_size:
            selected[replace_at] = dict(item)
    for order, item in enumerate(selected, start=1):
        item["sample_order"] = order
    return selected


def write_survey_queue(
    output_path: Path,
    sample: List[Dict[str, Any]],
    *,
    survey_id: str,
    seed: int,
    candidate_stats: Dict[str, Any],
    force: bool = False,
) -> Dict[str, Any]:
    """Write survey queue JSONL plus a small summary sidecar."""

    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass force=True to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in sample:
            row = {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "phase": REVIEW_PHASE,
                "survey_id": survey_id,
                "status": "pending",
                **item,
            }
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "phase": REVIEW_PHASE,
        "survey_id": survey_id,
        "seed": seed,
        "sample_size": len(sample),
        "candidate_stats": candidate_stats,
        "output": str(output_path),
    }
    summary_path = output_path.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _clean_string(value: Any, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if isinstance(data, dict):
                rows.append(data)
    return rows


def _review_key(row: Dict[str, Any]) -> str:
    survey_id = str(row.get("survey_id") or "")
    song_hash = str(row.get("song_hash") or row.get("hash") or "")
    path = str(row.get("path") or "")
    target = song_hash or path
    return f"{survey_id}|{target}" if target else ""


def _clean_segment(segment: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    if not isinstance(segment, dict):
        return {"start": None, "end": None}
    start = _first_float(segment.get("start"), segment.get("start_s"))
    end = _first_float(segment.get("end"), segment.get("end_s"))
    return {"start": start, "end": end}


def _first_float(*values: Any) -> Optional[float]:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _default_resolve_audio_path(path: str) -> str:
    try:
        from config import resolve_path
        return resolve_path(path)
    except Exception as exc:
        _warn_once(
            "resolve_path_fallback",
            f"[melody_review] WARNING: config.resolve_path failed; using raw path for audio lookup: {exc}",
        )
        return path


def _warn_once(key: str, message: str) -> None:
    seen = getattr(_warn_once, "_seen", set())
    if key in seen:
        return
    print(message, file=sys.stderr)
    seen.add(key)
    setattr(_warn_once, "_seen", seen)


def _normalize_library_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    normalized = re.sub(r"^@\d+/", "", normalized)
    return normalized.lower()
