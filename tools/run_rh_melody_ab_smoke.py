"""Run a small Phase 0.5 RH melody A/B smoke queue.

Queue JSONL rows:
  {"hash":"abcdef123456","group":"vocal","candidates":["full_mix_pyin","vocal_stem_crepe"]}
  {"path":"Classics/piano.flac","group":"solo_piano","polyphonic_json":"C:\\tmp\\piano-notes.json"}

The runner writes shadow candidate caches through generate_shadow_candidates();
the Admin Candidates panel remains read-only and only displays those caches.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_shadow_generator import DEFAULT_DATA_DIR  # noqa: E402
from ai.melody_shadow_smoke import read_smoke_queue, run_smoke_queue, write_smoke_report  # noqa: E402


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_queue.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_results.jsonl")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 0.5 RH melody A/B smoke candidate generation.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="JSONL queue of 20-30 skewed smoke songs.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="LiveChord data dir; default V:\\data.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="JSONL result report path.")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N rows. 0 means all rows.")
    parser.add_argument("--force", action="store_true", help="Regenerate candidate caches even when present.")
    parser.add_argument("--dry-run", action="store_true", help="Validate queue and write planned rows without generating caches.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing report output.")
    args = parser.parse_args()

    queue_path = Path(args.queue)
    items = read_smoke_queue(queue_path)
    rows, summary = run_smoke_queue(
        items,
        data_dir=Path(args.data_dir),
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
    )
    summary = write_smoke_report(rows, summary, Path(args.out), force=args.force_output)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("failed") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
