"""Compute Phase 0.5 residual metrics for vocal RH melody candidates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_residual_report import (  # noqa: E402
    DEFAULT_MIN_BASELINE_ACTIVE_S,
    DEFAULT_MIN_CANDIDATE_COVERAGE_RATIO,
    DEFAULT_PHRASE_GAP_S,
    DEFAULT_TAIL_JUMP_SEMITONES,
    DEFAULT_TAIL_WINDOW_S,
    DEFAULT_WINDOW_S,
    build_vocal_residual_report,
    read_smoke_rows,
    write_residual_report,
)


DEFAULT_INPUT = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_results.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_vocal_residual_report.json")
DEFAULT_DATA_DIR = Path(r"V:\data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute RH melody vocal residual metrics.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Phase 0.5 smoke results JSONL.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Residual report JSON output.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="LiveChord data root.")
    parser.add_argument("--group", default="vocal", help="Smoke group to include.")
    parser.add_argument("--window-s", type=float, default=DEFAULT_WINDOW_S)
    parser.add_argument("--min-baseline-active-s", type=float, default=DEFAULT_MIN_BASELINE_ACTIVE_S)
    parser.add_argument("--min-candidate-coverage-ratio", type=float, default=DEFAULT_MIN_CANDIDATE_COVERAGE_RATIO)
    parser.add_argument("--phrase-gap-s", type=float, default=DEFAULT_PHRASE_GAP_S)
    parser.add_argument("--tail-window-s", type=float, default=DEFAULT_TAIL_WINDOW_S)
    parser.add_argument("--tail-jump-semitones", type=float, default=DEFAULT_TAIL_JUMP_SEMITONES)
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing report output.")
    args = parser.parse_args()

    rows = read_smoke_rows(Path(args.input))
    report = build_vocal_residual_report(
        rows,
        data_dir=Path(args.data_dir),
        group=args.group,
        window_s=args.window_s,
        min_baseline_active_s=args.min_baseline_active_s,
        min_candidate_coverage_ratio=args.min_candidate_coverage_ratio,
        phrase_gap_s=args.phrase_gap_s,
        tail_window_s=args.tail_window_s,
        tail_jump_semitones=args.tail_jump_semitones,
    )
    output = write_residual_report(report, Path(args.out), force=args.force_output)
    summary = report["summary"]
    print(
        "Wrote {total} rows to {output}; coverage_flags={coverage}; "
        "tail_flags={tail}; pass={passed}".format(
            total=summary["total"],
            output=output,
            coverage=summary["coverage_gap_gt_30pct_songs"],
            tail=summary["phrase_tail_jump_gt_30pct_songs"],
            passed=summary["passes_stage_b_residual_gate"],
        )
    )
    return 0 if summary["passes_stage_b_residual_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
