"""Build an equal-probability Phase 0 RH melody review queue.

Example:
  python tools/sample_melody_phase0_survey.py --sample-size 200 --seed 20260520 --force
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_review import (  # noqa: E402
    collect_survey_candidates,
    sample_survey_candidates,
    write_survey_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample a Phase 0 RH melody survey queue from chord cache."
    )
    parser.add_argument("--chords-root", default=str(REPO_ROOT / "data" / "chords"))
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "melody_reviews" / "phase0_survey_queue.jsonl"))
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260520)
    parser.add_argument("--survey-id", default="")
    parser.add_argument(
        "--include-missing-audio",
        action="store_true",
        help="Include chord records even when resolve_path(path) does not exist on disk.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files.")
    args = parser.parse_args()

    survey_id = args.survey_id or f"phase0_random_{args.sample_size}_seed_{args.seed}"
    candidates, stats = collect_survey_candidates(
        Path(args.chords_root),
        require_audio=not args.include_missing_audio,
    )
    sample = sample_survey_candidates(
        candidates,
        sample_size=max(0, args.sample_size),
        seed=args.seed,
    )
    summary = write_survey_queue(
        Path(args.out),
        sample,
        survey_id=survey_id,
        seed=args.seed,
        candidate_stats={
            **stats,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "require_audio": not args.include_missing_audio,
        },
        force=args.force,
    )
    print(f"Wrote {summary['sample_size']} survey rows to {summary['output']}")
    print(f"Candidate stats: {summary['candidate_stats']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
