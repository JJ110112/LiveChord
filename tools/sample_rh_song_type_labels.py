"""Build a held-out RH melody song-type labeling queue.

Example:
  python tools/sample_rh_song_type_labels.py --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.song_type_label_queue import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_LABEL_QUOTAS,
    build_label_candidates,
    load_excluded_hashes,
    parse_quotas,
    read_library_tracks,
    sample_label_queue,
    write_label_queue,
)


DEFAULT_LIBRARY_CACHE = Path(r"V:\data\library_cache.json")
DEFAULT_SMOKE_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_queue.jsonl")
DEFAULT_SMOKE_RESULTS = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_results.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample a held-out RH melody song-type label queue.")
    parser.add_argument("--library-cache", default=str(DEFAULT_LIBRARY_CACHE), help="library_cache.json path.")
    parser.add_argument("--exclude-jsonl", action="append", default=[], help="JSONL queue/results to exclude by hash. Can repeat.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Output JSONL path.")
    parser.add_argument("--survey-id", default="phase0_5_song_type_heldout_seed_20260522")
    parser.add_argument("--seed", type=int, default=20260522)
    parser.add_argument(
        "--quotas",
        default=",".join(f"{key}={value}" for key, value in DEFAULT_LABEL_QUOTAS.items()),
        help="Comma quotas, e.g. vocal_led=18,solo_piano=12,instrumental_lead=12,no_clear_lead=6.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Player URL base.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output and summary.")
    args = parser.parse_args()

    library_cache = Path(args.library_cache)
    exclude_paths = [Path(p) for p in args.exclude_jsonl]
    if not exclude_paths:
        exclude_paths = [DEFAULT_SMOKE_QUEUE, DEFAULT_SMOKE_RESULTS]
    quotas = parse_quotas(args.quotas)

    tracks = read_library_tracks(library_cache)
    excluded = load_excluded_hashes(exclude_paths)
    candidates, stats = build_label_candidates(tracks, exclude_hashes=excluded, base_url=args.base_url)
    sample = sample_label_queue(candidates, quotas=quotas, seed=args.seed)
    summary = write_label_queue(
        Path(args.out),
        sample,
        survey_id=args.survey_id,
        seed=args.seed,
        candidate_stats={
            **stats,
            "library_cache": str(library_cache),
            "excluded_hashes": len(excluded),
            "exclude_jsonl": [str(path) for path in exclude_paths if path.is_file()],
        },
        quotas=quotas,
        force=args.force,
    )
    print(f"Wrote {summary['sample_size']} label rows to {summary['output']}")
    print(f"By hint: {summary['by_hint']}")
    return 0 if summary["sample_size"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
