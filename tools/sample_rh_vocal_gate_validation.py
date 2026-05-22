"""Sample a leakage-clean RH vocal-gate validation queue.

The queue is for binary human review: label each row as vocal_led or not-vocal.
It intentionally excludes prior Phase 0.5 queues/reports by hash.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.song_type_label_queue import DEFAULT_BASE_URL, infer_song_type_hint, load_excluded_hashes, read_library_tracks  # noqa: E402


DEFAULT_LIBRARY_CACHE = Path(r"V:\data\library_cache.json")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_vocal_gate_validation_queue.jsonl")
DEFAULT_EXCLUDES = [
    Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl"),
    Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_queue.jsonl"),
    Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_results.jsonl"),
]
VOCAL_GATE_VALIDATION_PHASE = "phase0_5_vocal_gate_validation"
VOCAL_GATE_LABEL_OPTIONS = ("vocal_led", "not_vocal", "unknown")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample RH vocal-gate validation rows from library_cache.json.")
    parser.add_argument("--library-cache", default=str(DEFAULT_LIBRARY_CACHE), help="library_cache.json path.")
    parser.add_argument("--exclude-jsonl", action="append", default=[], help="JSONL queue/results to exclude by hash. Can repeat.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Output queue JSONL.")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260523)
    parser.add_argument("--survey-id", default="phase0_5_vocal_gate_validation_seed_20260523")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--force", action="store_true", help="Overwrite output and summary.")
    args = parser.parse_args()

    exclude_paths = [Path(path) for path in args.exclude_jsonl] or DEFAULT_EXCLUDES
    tracks = read_library_tracks(Path(args.library_cache))
    excluded = load_excluded_hashes(exclude_paths)
    candidates, stats = build_vocal_gate_candidates(
        tracks,
        exclude_hashes=excluded,
        base_url=args.base_url,
    )
    sample = sample_vocal_gate_queue(candidates, sample_size=args.sample_size, seed=args.seed)
    summary = write_vocal_gate_queue(
        Path(args.out),
        sample,
        survey_id=args.survey_id,
        seed=args.seed,
        candidate_stats={
            **stats,
            "library_cache": str(args.library_cache),
            "excluded_hashes": len(excluded),
            "exclude_jsonl": [str(path) for path in exclude_paths if path.is_file()],
        },
        force=args.force,
    )
    print(f"Wrote {summary['sample_size']} validation rows to {summary['output']}")
    print(f"By hint: {summary['by_hint']}")
    return 0 if summary["sample_size"] else 1


def build_vocal_gate_candidates(
    tracks: Iterable[Mapping[str, Any]],
    *,
    exclude_hashes: Iterable[str] = (),
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from chord_cache import song_hash

    excluded = set(exclude_hashes)
    candidates: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "checked": 0,
        "missing_path": 0,
        "excluded": 0,
        "candidates": 0,
        "by_hint": {},
    }
    seen: set[str] = set()
    for track in tracks:
        stats["checked"] += 1
        path = str(track.get("path") or "").strip()
        if not path:
            stats["missing_path"] += 1
            continue
        h = str(track.get("hash") or "").strip() or song_hash(path)
        if h in excluded or h in seen:
            stats["excluded"] += 1
            continue
        seen.add(h)
        hint, reason = infer_song_type_hint(track)
        stats["by_hint"][hint] = stats["by_hint"].get(hint, 0) + 1
        candidates.append({
            "hash": h,
            "song_hash": h,
            "path": path,
            "title": str(track.get("title") or _title_from_path(path)),
            "artist": str(track.get("artist") or ""),
            "album": str(track.get("album") or ""),
            "genre": str(track.get("genre") or ""),
            "duration_s": _optional_float(track.get("duration_s") or track.get("duration")),
            "candidate_hint": hint,
            "hint_reason": reason,
            "player_url": f"{base_url.rstrip('/')}/player?path={quote(path, safe='')}&autoplay=1",
        })
    stats["candidates"] = len(candidates)
    return candidates, stats


def sample_vocal_gate_queue(
    candidates: Sequence[Mapping[str, Any]],
    *,
    sample_size: int,
    seed: int,
) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    items = [dict(item) for item in candidates]
    rng.shuffle(items)
    sample = items[: max(0, int(sample_size))]
    for order, item in enumerate(sample, start=1):
        item["sample_order"] = order
    return sample


def write_vocal_gate_queue(
    output: Path,
    sample: Sequence[Mapping[str, Any]],
    *,
    survey_id: str,
    seed: int,
    candidate_stats: Mapping[str, Any],
    force: bool = False,
) -> Dict[str, Any]:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for item in sample:
            row = {
                "schema_version": 1,
                "phase": VOCAL_GATE_VALIDATION_PHASE,
                "survey_id": survey_id,
                "status": "pending",
                "human_label": "pending",
                "label_options": list(VOCAL_GATE_LABEL_OPTIONS),
                **dict(item),
            }
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, output)

    summary = {
        "schema_version": 1,
        "phase": VOCAL_GATE_VALIDATION_PHASE,
        "survey_id": survey_id,
        "seed": seed,
        "sample_size": len(sample),
        "candidate_stats": dict(candidate_stats),
        "by_hint": _count_by(sample, "candidate_hint"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "output": str(output),
    }
    summary_path = output.with_suffix(".summary.json")
    summary_tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    summary_tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(summary_tmp, summary_path)
    return summary


def _count_by(rows: Iterable[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _title_from_path(path: str) -> str:
    name = Path(path).name
    return name.rsplit(".", 1)[0] if "." in name else name


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
