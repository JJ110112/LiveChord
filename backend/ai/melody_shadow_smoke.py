"""Batch runner helpers for Phase 0.5 RH melody shadow A/B smoke tests."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .melody_candidate import FULL_MIX_PYIN, SOLO_PIANO_POLYPHONIC, VOCAL_STEM_CREPE
from .melody_shadow_generator import DEFAULT_DATA_DIR, ShadowGenerationResult, generate_shadow_candidates


SMOKE_SURVEY_ID = "phase0_5_ab_smoke"
IMPLEMENTED_SMOKE_CANDIDATES = {FULL_MIX_PYIN, VOCAL_STEM_CREPE, SOLO_PIANO_POLYPHONIC}
DEFAULT_CANDIDATES_BY_GROUP = {
    "vocal": [FULL_MIX_PYIN, VOCAL_STEM_CREPE],
    "vocal_led": [FULL_MIX_PYIN, VOCAL_STEM_CREPE],
    "solo_piano": [FULL_MIX_PYIN, SOLO_PIANO_POLYPHONIC],
    "piano": [FULL_MIX_PYIN, SOLO_PIANO_POLYPHONIC],
    "instrumental": [FULL_MIX_PYIN],
    "mixed": [FULL_MIX_PYIN, VOCAL_STEM_CREPE],
    "unknown": [FULL_MIX_PYIN],
}


@dataclass
class SmokeQueueItem:
    hash: str = ""
    path: str = ""
    group: str = "unknown"
    candidates: List[str] = field(default_factory=list)
    audio_path: str = ""
    polyphonic_json: str = ""
    polyphonic_midi: str = ""
    key: Optional[str] = None
    note: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SmokeQueueItem":
        candidates = data.get("candidates") or []
        if isinstance(candidates, str):
            candidates = [part.strip() for part in candidates.split(",") if part.strip()]
        if not isinstance(candidates, list):
            candidates = []
        return cls(
            hash=str(data.get("hash") or data.get("song_hash") or ""),
            path=str(data.get("path") or ""),
            group=_clean_group(data.get("group") or data.get("category") or "unknown"),
            candidates=[str(item) for item in candidates],
            audio_path=str(data.get("audio_path") or ""),
            polyphonic_json=str(data.get("polyphonic_json") or ""),
            polyphonic_midi=str(data.get("polyphonic_midi") or ""),
            key=str(data["key"]) if data.get("key") else None,
            note=str(data.get("note") or ""),
        )

    def resolved_candidates(self) -> List[str]:
        requested = self.candidates or DEFAULT_CANDIDATES_BY_GROUP.get(self.group, DEFAULT_CANDIDATES_BY_GROUP["unknown"])
        invalid = [item for item in requested if item not in IMPLEMENTED_SMOKE_CANDIDATES]
        if invalid:
            allowed = ", ".join(sorted(IMPLEMENTED_SMOKE_CANDIDATES))
            raise ValueError(f"unsupported smoke candidate(s): {', '.join(invalid)}; allowed: {allowed}")
        return list(dict.fromkeys(requested))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hash": self.hash,
            "path": self.path,
            "group": self.group,
            "candidates": self.candidates,
            "audio_path": self.audio_path,
            "polyphonic_json": self.polyphonic_json,
            "polyphonic_midi": self.polyphonic_midi,
            "key": self.key or "",
            "note": self.note,
        }

    def warnings(self) -> List[str]:
        if self.candidates:
            return []
        if self.group not in DEFAULT_CANDIDATES_BY_GROUP:
            return [f"unknown_group:{self.group};defaulting_to_unknown"]
        return []


def read_smoke_queue(path: Path) -> List[SmokeQueueItem]:
    items: List[SmokeQueueItem] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except Exception as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path}:{line_no}: queue row must be a JSON object")
        item = SmokeQueueItem.from_dict(data)
        if not item.hash and not item.path:
            raise ValueError(f"{path}:{line_no}: hash or path is required")
        items.append(item)
    return items


def run_smoke_queue(
    items: Sequence[SmokeQueueItem],
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    limit: int = 0,
    force: bool = False,
    dry_run: bool = False,
    generator: Optional[Callable[..., ShadowGenerationResult]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    root = Path(data_dir)
    selected = list(items[:limit] if limit and limit > 0 else items)
    generate = generator or generate_shadow_candidates
    rows: List[Dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()

    for index, item in enumerate(selected, start=1):
        warnings = item.warnings()
        try:
            candidates = item.resolved_candidates()
            if dry_run:
                planned_results = [
                    _dry_run_candidate_result(candidate_id, item)
                    for candidate_id in candidates
                ]
                result_dict: Dict[str, Any] = {
                    "ok": all(result["ok"] for result in planned_results),
                    "song_hash": item.hash,
                    "path": item.path,
                    "audio_path": item.audio_path,
                    "results": planned_results,
                }
            else:
                result = generate(
                    data_dir=root,
                    song_hash=item.hash,
                    path=item.path,
                    audio_path=item.audio_path,
                    candidates=candidates,
                    polyphonic_json=item.polyphonic_json,
                    polyphonic_midi=item.polyphonic_midi,
                    key=item.key,
                    force=force,
                )
                result_dict = result.to_dict()
        except Exception as exc:
            candidates = []
            warnings.append("candidate_resolution_failed")
            result_dict = {
                "ok": False,
                "song_hash": item.hash,
                "path": item.path,
                "audio_path": item.audio_path,
                "results": [],
                "error": f"{type(exc).__name__}:{exc}",
            }
        rows.append({
            "survey_id": SMOKE_SURVEY_ID,
            "sample_order": index,
            "group": item.group,
            "note": item.note,
            "requested": item.to_dict(),
            "resolved_candidates": candidates,
            "warnings": warnings,
            "result": result_dict,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    summary = _summarize_rows(rows, started_at=started_at, data_dir=root, dry_run=dry_run, force=force)
    return rows, summary


def write_smoke_report(
    rows: Iterable[Dict[str, Any]],
    summary: Dict[str, Any],
    output: Path,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(content + ("\n" if content else ""), encoding="utf-8")
    os.replace(tmp, output)
    summary = dict(summary)
    summary["output"] = str(output)
    summary_path = output.with_suffix(output.suffix + ".summary.json")
    summary_tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    summary_tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(summary_tmp, summary_path)
    summary["summary_output"] = str(summary_path)
    return summary


def _summarize_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    started_at: str,
    data_dir: Path,
    dry_run: bool,
    force: bool,
) -> Dict[str, Any]:
    by_group: Dict[str, Dict[str, int]] = {}
    by_candidate: Dict[str, Dict[str, Any]] = {}
    warnings: Dict[str, int] = {}
    ok_rows = 0
    for row in rows:
        group = str(row.get("group") or "unknown")
        result = row.get("result") or {}
        for warning in row.get("warnings") or []:
            key = str(warning or "unknown_warning")
            warnings[key] = warnings.get(key, 0) + 1
        if result.get("ok"):
            ok_rows += 1
        group_counts = by_group.setdefault(group, {"total": 0, "ok": 0, "failed": 0})
        group_counts["total"] += 1
        group_counts["ok" if result.get("ok") else "failed"] += 1
        for candidate_result in result.get("results") or []:
            candidate_id = str(candidate_result.get("candidate_id") or "")
            status = str(candidate_result.get("status") or "unknown")
            if not candidate_id:
                continue
            counts = by_candidate.setdefault(candidate_id, {"total": 0, "ok": 0, "failed": 0, "by_status": {}})
            counts["total"] += 1
            counts["ok" if candidate_result.get("ok") else "failed"] += 1
            counts["by_status"][status] = counts["by_status"].get(status, 0) + 1
    return {
        "survey_id": SMOKE_SURVEY_ID,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "dry_run": dry_run,
        "force": force,
        "total": len(rows),
        "ok": ok_rows,
        "failed": max(0, len(rows) - ok_rows),
        "by_group": by_group,
        "by_candidate": by_candidate,
        "warnings": warnings,
    }


def _dry_run_candidate_result(candidate_id: str, item: SmokeQueueItem) -> Dict[str, Any]:
    if candidate_id == SOLO_PIANO_POLYPHONIC:
        if item.polyphonic_json:
            if not Path(item.polyphonic_json).is_file():
                return _planned_failure(candidate_id, "polyphonic_json_missing", item.polyphonic_json)
        elif item.polyphonic_midi:
            if not Path(item.polyphonic_midi).is_file():
                return _planned_failure(candidate_id, "polyphonic_midi_missing", item.polyphonic_midi)
        else:
            return _planned_failure(candidate_id, "polyphonic_input_missing", "")
    return {"candidate_id": candidate_id, "ok": True, "status": "planned", "cache_file": "", "error": "", "details": {}}


def _planned_failure(candidate_id: str, status: str, path: str) -> Dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "ok": False,
        "status": status,
        "cache_file": "",
        "error": status,
        "details": {"path": path},
    }


def _clean_group(value: Any) -> str:
    clean = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    return clean or "unknown"
