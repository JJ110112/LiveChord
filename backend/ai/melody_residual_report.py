"""Residual metric report for RH melody vocal candidates.

These metrics are intentionally lightweight machine proxies for the Phase 0.5
ship audit. They do not replace listening review; they make the two known
vocal-candidate residual risks repeatable:

* coverage gaps in windows where the full-mix baseline has activity
* phrase-tail pitch jumps near silence boundaries
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .melody_candidate import FULL_MIX_PYIN, VOCAL_STEM_CREPE, candidate_path


DEFAULT_WINDOW_S = 10.0
DEFAULT_MIN_BASELINE_ACTIVE_S = 0.5
DEFAULT_MIN_CANDIDATE_COVERAGE_RATIO = 0.30
DEFAULT_PHRASE_GAP_S = 0.75
DEFAULT_TAIL_WINDOW_S = 0.50
DEFAULT_TAIL_JUMP_SEMITONES = 7.0


def read_smoke_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            rows.append(data)
    return rows


def build_vocal_residual_report(
    smoke_rows: Sequence[Mapping[str, Any]],
    *,
    data_dir: Path,
    group: str = "vocal",
    window_s: float = DEFAULT_WINDOW_S,
    min_baseline_active_s: float = DEFAULT_MIN_BASELINE_ACTIVE_S,
    min_candidate_coverage_ratio: float = DEFAULT_MIN_CANDIDATE_COVERAGE_RATIO,
    phrase_gap_s: float = DEFAULT_PHRASE_GAP_S,
    tail_window_s: float = DEFAULT_TAIL_WINDOW_S,
    tail_jump_semitones: float = DEFAULT_TAIL_JUMP_SEMITONES,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for smoke_row in smoke_rows:
        if str(smoke_row.get("group") or "") != group:
            continue
        result = smoke_row.get("result") or {}
        requested = smoke_row.get("requested") or {}
        song_hash = str(result.get("song_hash") or requested.get("hash") or "")
        if not song_hash:
            skipped += 1
            continue
        baseline_file = candidate_path(data_dir, song_hash, FULL_MIX_PYIN)
        candidate_file = candidate_path(data_dir, song_hash, VOCAL_STEM_CREPE)
        if not baseline_file.is_file() or not candidate_file.is_file():
            skipped += 1
            continue
        baseline_payload = _read_json(baseline_file)
        candidate_payload = _read_json(candidate_file)
        baseline_events = _events(baseline_payload)
        candidate_events = _events(candidate_payload)
        duration_s = _song_duration(smoke_row, baseline_events, candidate_events)
        coverage = coverage_gap_metrics(
            baseline_events,
            candidate_events,
            duration_s=duration_s,
            window_s=window_s,
            min_baseline_active_s=min_baseline_active_s,
            min_candidate_coverage_ratio=min_candidate_coverage_ratio,
        )
        tail = phrase_tail_jump_metrics(
            candidate_events,
            phrase_gap_s=phrase_gap_s,
            tail_window_s=tail_window_s,
            tail_jump_semitones=tail_jump_semitones,
        )
        rows.append({
            "sample_order": smoke_row.get("sample_order"),
            "song_hash": song_hash,
            "group": group,
            "title": requested.get("title") or _title_from_path(str(result.get("path") or requested.get("path") or "")),
            "path": result.get("path") or requested.get("path") or "",
            "note": smoke_row.get("note") or requested.get("note") or "",
            "duration_s": round(duration_s, 3),
            "baseline_events": len(baseline_events),
            "candidate_events": len(candidate_events),
            "coverage": coverage,
            "phrase_tail_jumps": tail,
            "flags": _row_flags(coverage, tail),
        })
    rows.sort(key=lambda item: int(item.get("sample_order") or 0))
    return {
        "schema_version": 1,
        "group": group,
        "source": "rh_vocal_residual_report_v1",
        "parameters": {
            "window_s": window_s,
            "min_baseline_active_s": min_baseline_active_s,
            "min_candidate_coverage_ratio": min_candidate_coverage_ratio,
            "phrase_gap_s": phrase_gap_s,
            "tail_window_s": tail_window_s,
            "tail_jump_semitones": tail_jump_semitones,
        },
        "summary": _summary(rows, skipped=skipped),
        "rows": rows,
    }


def coverage_gap_metrics(
    baseline_events: Sequence[Mapping[str, Any]],
    candidate_events: Sequence[Mapping[str, Any]],
    *,
    duration_s: float,
    window_s: float = DEFAULT_WINDOW_S,
    min_baseline_active_s: float = DEFAULT_MIN_BASELINE_ACTIVE_S,
    min_candidate_coverage_ratio: float = DEFAULT_MIN_CANDIDATE_COVERAGE_RATIO,
) -> Dict[str, Any]:
    if duration_s <= 0 or window_s <= 0:
        return {
            "baseline_active_windows": 0,
            "candidate_missing_windows": 0,
            "missing_window_fraction": 0.0,
            "worst_window_ratio": None,
            "windows": [],
        }
    windows: List[Dict[str, Any]] = []
    index = 0
    start = 0.0
    while start < duration_s:
        end = min(duration_s, start + window_s)
        baseline_active = _active_overlap(baseline_events, start, end)
        candidate_active = _active_overlap(candidate_events, start, end)
        if baseline_active >= min_baseline_active_s:
            ratio = candidate_active / baseline_active if baseline_active > 0 else 0.0
            missing = ratio < min_candidate_coverage_ratio
            windows.append({
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "baseline_active_s": round(baseline_active, 3),
                "candidate_active_s": round(candidate_active, 3),
                "coverage_ratio": round(ratio, 4),
                "missing": missing,
            })
        start += window_s
        index += 1
    missing_count = sum(1 for item in windows if item["missing"])
    worst_ratio = min((float(item["coverage_ratio"]) for item in windows), default=None)
    return {
        "baseline_active_windows": len(windows),
        "candidate_missing_windows": missing_count,
        "missing_window_fraction": round(missing_count / len(windows), 4) if windows else 0.0,
        "worst_window_ratio": worst_ratio,
        "windows": windows,
    }


def phrase_tail_jump_metrics(
    events: Sequence[Mapping[str, Any]],
    *,
    phrase_gap_s: float = DEFAULT_PHRASE_GAP_S,
    tail_window_s: float = DEFAULT_TAIL_WINDOW_S,
    tail_jump_semitones: float = DEFAULT_TAIL_JUMP_SEMITONES,
) -> Dict[str, Any]:
    compacted = [event for event in (_compact_event(event) for event in events) if event is not None]
    ordered = sorted(compacted, key=lambda event: (event["start"], event["end"], event["pitch"]))
    phrase_tails: List[Dict[str, Any]] = []
    for index, event in enumerate(ordered):
        next_start = ordered[index + 1]["start"] if index + 1 < len(ordered) else None
        gap = (next_start - event["end"]) if next_start is not None else None
        if gap is not None and gap < phrase_gap_s:
            continue
        tail_start = event["end"] - tail_window_s
        tail_events = [
            item for item in ordered
            if item["end"] >= tail_start and item["start"] <= event["end"]
        ]
        max_jump = _max_adjacent_jump(tail_events)
        pitch_range = _pitch_range(tail_events)
        has_jump = max(max_jump, pitch_range) > tail_jump_semitones
        phrase_tails.append({
            "event_index": index,
            "tail_start": round(tail_start, 3),
            "tail_end": round(event["end"], 3),
            "next_gap_s": round(gap, 3) if gap is not None else None,
            "tail_event_count": len(tail_events),
            "max_adjacent_jump": round(max_jump, 3),
            "pitch_range": round(pitch_range, 3),
            "has_jump": has_jump,
        })
    jump_count = sum(1 for item in phrase_tails if item["has_jump"])
    return {
        "phrase_tail_count": len(phrase_tails),
        "jump_tail_count": jump_count,
        "jump_tail_fraction": round(jump_count / len(phrase_tails), 4) if phrase_tails else 0.0,
        "tails": phrase_tails,
    }


def write_residual_report(report: Mapping[str, Any], output: Path, *, force: bool = False) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, output)
    return output


def _events(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    events = payload.get("melody") if isinstance(payload, Mapping) else []
    return [dict(event) for event in events or [] if isinstance(event, Mapping)]


def _read_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _song_duration(
    smoke_row: Mapping[str, Any],
    baseline_events: Sequence[Mapping[str, Any]],
    candidate_events: Sequence[Mapping[str, Any]],
) -> float:
    requested = smoke_row.get("requested") or {}
    for source in (smoke_row, requested):
        value = _float_or_none(source.get("duration_s") if isinstance(source, Mapping) else None)
        if value and value > 0:
            return value
    return max(
        [_event_end(event) for event in baseline_events]
        + [_event_end(event) for event in candidate_events]
        + [0.0]
    )


def _active_overlap(events: Sequence[Mapping[str, Any]], window_start: float, window_end: float) -> float:
    total = 0.0
    for event in events:
        compact = _compact_event(event)
        if compact is None:
            continue
        start = compact["start"]
        end = compact["end"]
        if end <= window_start or start >= window_end:
            continue
        total += max(0.0, min(end, window_end) - max(start, window_start))
    return total


def _compact_event(event: Mapping[str, Any]) -> Dict[str, float] | None:
    start = _float_or_none(event.get("time", event.get("start")))
    end = _float_or_none(event.get("end"))
    duration = _float_or_none(event.get("duration"))
    pitch = _float_or_none(event.get("midi", event.get("pitch")))
    if start is None or pitch is None:
        return None
    if end is None:
        end = start + max(0.0, duration or 0.0)
    if end <= start:
        return None
    return {"start": start, "end": end, "pitch": pitch}


def _event_start(event: Mapping[str, Any]) -> float:
    return _float_or_none(event.get("time", event.get("start"))) or 0.0


def _event_end(event: Mapping[str, Any]) -> float:
    start = _event_start(event)
    end = _float_or_none(event.get("end"))
    if end is not None:
        return end
    return start + max(0.0, _float_or_none(event.get("duration")) or 0.0)


def _max_adjacent_jump(events: Sequence[Mapping[str, float]]) -> float:
    if len(events) < 2:
        return 0.0
    ordered = sorted(events, key=lambda event: (event["start"], event["end"]))
    return max(abs(ordered[index]["pitch"] - ordered[index - 1]["pitch"]) for index in range(1, len(ordered)))


def _pitch_range(events: Sequence[Mapping[str, float]]) -> float:
    pitches = [float(event["pitch"]) for event in events]
    return max(pitches) - min(pitches) if pitches else 0.0


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _row_flags(coverage: Mapping[str, Any], tail: Mapping[str, Any]) -> List[str]:
    flags: List[str] = []
    if float(coverage.get("missing_window_fraction") or 0.0) > 0.30:
        flags.append("coverage_gap_gt_30pct")
    if float(tail.get("jump_tail_fraction") or 0.0) > 0.30:
        flags.append("phrase_tail_jump_gt_30pct")
    return flags


def _summary(rows: Sequence[Mapping[str, Any]], *, skipped: int) -> Dict[str, Any]:
    total = len(rows)
    coverage_flagged = sum(1 for row in rows if "coverage_gap_gt_30pct" in (row.get("flags") or []))
    tail_flagged = sum(1 for row in rows if "phrase_tail_jump_gt_30pct" in (row.get("flags") or []))
    return {
        "total": total,
        "skipped": skipped,
        "coverage_gap_gt_30pct_songs": coverage_flagged,
        "coverage_gap_gt_30pct_fraction": round(coverage_flagged / total, 4) if total else 0.0,
        "phrase_tail_jump_gt_30pct_songs": tail_flagged,
        "phrase_tail_jump_gt_30pct_fraction": round(tail_flagged / total, 4) if total else 0.0,
        "passes_stage_b_residual_gate": (
            total > 0
            and coverage_flagged / total <= 0.30
            and tail_flagged / total <= 0.30
        ),
    }


def _title_from_path(path: str) -> str:
    name = Path(path).name
    return name.rsplit(".", 1)[0] if "." in name else name
