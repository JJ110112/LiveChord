"""Extract audio features for RH melody song-type classifier experiments.

Example:
  python tools/extract_rh_song_type_audio_features.py --force-output
"""

from __future__ import annotations

import argparse
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

from ai.song_type_audio_features import (  # noqa: E402
    AUDIO_FEATURE_SCHEMA_VERSION,
    AUDIO_FEATURE_SOURCE,
    DEFAULT_MAX_SECONDS,
    DEFAULT_SAMPLE_RATE,
    build_audio_feature_row,
)
from ai.song_type_label_queue import merge_queue_with_latest_labels, read_label_rows  # noqa: E402


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")
DEFAULT_LABELS = Path(r"V:\data\melody_reviews\phase0_5_song_type_labels.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_song_type_audio_features.jsonl")
DEFAULT_DATA_DIR = Path(r"V:\data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract RH song-type audio features from the held-out label queue.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Held-out label queue JSONL.")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Human label JSONL.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Feature output JSONL.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="LiveChord data root containing cached stems.")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit for smoke runs.")
    parser.add_argument("--no-cached-stems", action="store_true", help="Skip cached stem energy ratios.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing output.")
    args = parser.parse_args()

    queue_rows = read_label_rows(Path(args.queue))
    label_rows = read_label_rows(Path(args.labels))
    merged = merge_queue_with_latest_labels(queue_rows, label_rows)
    labeled = [row for row in merged if row.get("resolved_label")]
    if args.limit and args.limit > 0:
        labeled = labeled[: args.limit]

    rows = extract_feature_rows(
        labeled,
        data_dir=Path(args.data_dir),
        sample_rate=args.sample_rate,
        max_seconds=args.max_seconds,
        include_cached_stems=not args.no_cached_stems,
    )
    output = write_feature_rows(rows, Path(args.out), force=args.force_output)
    summary = write_summary(rows, output, args=args)
    print(
        f"Wrote {output}; rows={summary['rows']}; ok={summary['ok']}; "
        f"failed={summary['failed']}; cached_stems={summary['stem_status'].get('cached_stems', 0)}"
    )
    return 0 if summary["ok"] else 1


def extract_feature_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    data_dir: Path,
    sample_rate: int,
    max_seconds: float,
    include_cached_stems: bool,
) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    for row in rows:
        song_hash = str(row.get("song_hash") or row.get("hash") or "").strip()
        try:
            audio_path = _resolve_audio_path(str(row.get("path") or ""))
            feature_row = build_audio_feature_row(
                row,
                audio_path=audio_path,
                data_dir=data_dir,
                sample_rate=sample_rate,
                max_seconds=max_seconds,
                include_cached_stems=include_cached_stems,
            )
        except Exception as exc:
            feature_row = {
                "schema_version": AUDIO_FEATURE_SCHEMA_VERSION,
                "feature_source": AUDIO_FEATURE_SOURCE,
                "song_hash": song_hash,
                "hash": song_hash,
                "survey_id": str(row.get("survey_id") or ""),
                "path": str(row.get("path") or ""),
                "title": str(row.get("title") or ""),
                "artist": str(row.get("artist") or ""),
                "candidate_hint": str(row.get("candidate_hint") or ""),
                "resolved_label": str(row.get("resolved_label") or ""),
                "status": "failed",
                "error": f"{type(exc).__name__}:{exc}",
            }
        extracted.append(feature_row)
    return extracted


def write_feature_rows(rows: Sequence[Mapping[str, Any]], output: Path, *, force: bool) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, output)
    return output


def write_summary(rows: Sequence[Mapping[str, Any]], output: Path, *, args: argparse.Namespace) -> Dict[str, Any]:
    summary = {
        "schema_version": AUDIO_FEATURE_SCHEMA_VERSION,
        "feature_source": AUDIO_FEATURE_SOURCE,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "output": str(output),
        "queue": str(args.queue),
        "labels": str(args.labels),
        "data_dir": str(args.data_dir),
        "sample_rate": int(args.sample_rate),
        "max_seconds": float(args.max_seconds),
        "include_cached_stems": not bool(args.no_cached_stems),
        "rows": len(rows),
        "ok": sum(1 for row in rows if row.get("status") == "ok"),
        "failed": sum(1 for row in rows if row.get("status") != "ok"),
        "by_label": _count_by(rows, "resolved_label"),
        "stem_status": _stem_status_counts(rows),
    }
    summary_path = output.with_suffix(".summary.json")
    tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, summary_path)
    return summary


def _resolve_audio_path(path: str) -> str:
    try:
        from config import resolve_path

        return resolve_path(path)
    except Exception as exc:
        _warn_once("resolve_path", f"[song-type-audio] resolve_path fallback: {type(exc).__name__}: {exc}\n")
        return path


def _count_by(rows: Iterable[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _stem_status_counts(rows: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        stems = row.get("stems") if isinstance(row.get("stems"), dict) else {}
        status = str(stems.get("stem_status") or "not_available")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _warn_once(key: str, message: str) -> None:
    seen = getattr(_warn_once, "_seen", set())
    if key in seen:
        return
    seen.add(key)
    setattr(_warn_once, "_seen", seen)
    sys.stderr.write(message)


if __name__ == "__main__":
    raise SystemExit(main())
