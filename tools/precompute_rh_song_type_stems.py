"""Precompute cached Demucs stems for RH song-type classifier experiments.

Default mode is a dry-run. Pass --execute to run Demucs and populate
V:/data/stems/<hh>/<hash>/.

Example:
  python tools/precompute_rh_song_type_stems.py --hash 9a399f94b9e7 --execute --force-output
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_candidate import stem_path  # noqa: E402
from ai.song_type_label_queue import merge_queue_with_latest_labels, read_label_rows  # noqa: E402
from ai.stem_separation import REQUIRED_STEMS, STEM_NAMES, StemCache, StemCacheResult  # noqa: E402


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")
DEFAULT_LABELS = Path(r"V:\data\melody_reviews\phase0_5_song_type_labels.jsonl")
DEFAULT_DATA_DIR = Path(r"V:\data")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_song_type_stem_precompute.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(description="Precompute cached stems for RH song-type classifier rows.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Held-out label queue JSONL.")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Human label JSONL.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="LiveChord data root.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Precompute report JSONL.")
    parser.add_argument("--label", action="append", default=[], help="Only include resolved labels. Can repeat.")
    parser.add_argument("--hash", action="append", default=[], help="Only include song hashes. Can repeat.")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit after filters.")
    parser.add_argument("--execute", action="store_true", help="Actually run Demucs; default is dry-run planning.")
    parser.add_argument("--force", action="store_true", help="Regenerate stems even when cached.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing output.")
    args = parser.parse_args()

    queue_rows = read_label_rows(Path(args.queue))
    label_rows = read_label_rows(Path(args.labels))
    rows = merge_queue_with_latest_labels(queue_rows, label_rows)
    selected = select_rows(rows, labels=args.label, hashes=args.hash, limit=args.limit)
    report_rows, summary = run_precompute(
        selected,
        data_dir=Path(args.data_dir),
        execute=args.execute,
        force=args.force,
    )
    summary = write_report(report_rows, summary, Path(args.out), force=args.force_output)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["failed"] == 0 else 1


def select_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    labels: Sequence[str] = (),
    hashes: Sequence[str] = (),
    limit: int = 0,
) -> List[Dict[str, Any]]:
    label_filter = {str(label).strip() for label in labels if str(label).strip()}
    hash_filter = {str(song_hash).strip() for song_hash in hashes if str(song_hash).strip()}
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        song_hash = str(row.get("song_hash") or row.get("hash") or "").strip()
        if not song_hash or song_hash in seen:
            continue
        label = str(row.get("resolved_label") or row.get("human_label") or "").strip()
        if label_filter and label not in label_filter:
            continue
        if hash_filter and song_hash not in hash_filter:
            continue
        seen.add(song_hash)
        selected.append(dict(row))
        if limit and limit > 0 and len(selected) >= limit:
            break
    return selected


def run_precompute(
    rows: Sequence[Mapping[str, Any]],
    *,
    data_dir: Path,
    execute: bool,
    force: bool = False,
    stem_cache: StemCache | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    cache = stem_cache or StemCache(data_dir)
    started_at = datetime.now(timezone.utc).isoformat()
    demucs_available = _module_available("demucs")
    report_rows: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        song_hash = str(row.get("song_hash") or row.get("hash") or "").strip()
        label = str(row.get("resolved_label") or row.get("human_label") or "")
        path = str(row.get("path") or "")
        audio_path = _resolve_audio_path(path)
        cached = cached_stem_status(data_dir, song_hash)
        base = {
            "schema_version": 1,
            "sample_order": index,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "execute": execute,
            "force": force,
            "song_hash": song_hash,
            "hash": song_hash,
            "survey_id": str(row.get("survey_id") or ""),
            "resolved_label": label,
            "candidate_hint": str(row.get("candidate_hint") or ""),
            "title": str(row.get("title") or ""),
            "artist": str(row.get("artist") or ""),
            "path": path,
            "audio_path": audio_path,
            "cache_dir": cached["cache_dir"],
            "cached_stems": cached["stems"],
            "cached_complete": cached["complete"],
        }
        if cached["complete"] and not force:
            report_rows.append({**base, "ok": True, "status": "cached", "error": ""})
            continue
        if not execute:
            status = "planned" if Path(audio_path).is_file() else "audio_not_found"
            report_rows.append({**base, "ok": status == "planned", "status": status, "error": "" if status == "planned" else "audio_not_found"})
            continue
        if not demucs_available:
            report_rows.append({**base, "ok": False, "status": "dependency_missing", "error": "demucs"})
            continue
        result = cache.ensure_stems(song_hash=song_hash, audio_path=audio_path, force=force)
        report_rows.append({**base, **_result_fields(result)})
    summary = summarize_rows(report_rows, started_at=started_at, data_dir=data_dir, execute=execute, force=force)
    return report_rows, summary


def cached_stem_status(data_dir: Path, song_hash: str) -> Dict[str, Any]:
    stems = {
        name: str(stem_path(data_dir, song_hash, name))
        for name in STEM_NAMES
        if stem_path(data_dir, song_hash, name).is_file()
    }
    return {
        "cache_dir": str(Path(data_dir) / "stems" / song_hash[:2] / song_hash) if song_hash else "",
        "stems": stems,
        "complete": REQUIRED_STEMS.issubset(stems),
        "missing": [name for name in STEM_NAMES if name not in stems],
    }


def write_report(
    rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    output: Path,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, output)
    final_summary = dict(summary)
    final_summary["output"] = str(output)
    summary_path = output.with_suffix(".summary.json")
    summary_tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    summary_tmp.write_text(json.dumps(final_summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(summary_tmp, summary_path)
    final_summary["summary_output"] = str(summary_path)
    return final_summary


def summarize_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    started_at: str,
    data_dir: Path,
    execute: bool,
    force: bool,
) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_label: Dict[str, Dict[str, int]] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        label = str(row.get("resolved_label") or "")
        by_status[status] = by_status.get(status, 0) + 1
        label_counts = by_label.setdefault(label, {"total": 0, "ok": 0, "failed": 0})
        label_counts["total"] += 1
        label_counts["ok" if row.get("ok") else "failed"] += 1
    return {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "execute": execute,
        "force": force,
        "total": len(rows),
        "ok": sum(1 for row in rows if row.get("ok")),
        "failed": sum(1 for row in rows if not row.get("ok")),
        "by_status": by_status,
        "by_label": by_label,
    }


def _result_fields(result: StemCacheResult) -> Dict[str, Any]:
    status = "generated" if result.ok and not result.reused else "cached"
    if not result.ok:
        status = result.error or "failed"
    return {
        "ok": bool(result.ok),
        "status": status,
        "error": result.error,
        "cache_dir": result.cache_dir,
        "stems": result.stems,
    }


def _resolve_audio_path(path: str) -> str:
    try:
        from config import resolve_path

        return resolve_path(path)
    except Exception as exc:
        _warn_once("resolve_path", f"[song-type-stems] resolve_path fallback: {type(exc).__name__}: {exc}\n")
        return path


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _warn_once(key: str, message: str) -> None:
    seen = getattr(_warn_once, "_seen", set())
    if key in seen:
        return
    seen.add(key)
    setattr(_warn_once, "_seen", seen)
    sys.stderr.write(message)


if __name__ == "__main__":
    raise SystemExit(main())
