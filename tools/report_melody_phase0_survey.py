"""Aggregate Phase 0 RH melody survey tags into a JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_review import (  # noqa: E402
    REVIEW_DIR_NAME,
    REVIEW_REPORT_NAME,
    build_survey_report,
    read_latest_review_tags,
    read_survey_queue,
    resolve_review_data_dir,
    write_survey_report,
)


DEFAULT_DATA_DIR = Path(r"V:\data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write RH melody Phase 0 survey report JSON.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="LiveChord data root.")
    parser.add_argument("--survey-id", default="", help="Survey id filter; defaults to queue summary survey_id.")
    parser.add_argument("--out", default="", help="Report JSON output path.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing report output.")
    args = parser.parse_args()

    data_dir = resolve_review_data_dir(Path(args.data_dir))
    rows, queue_summary, _queue_file = read_survey_queue(data_dir)
    active_survey_id = args.survey_id or str(queue_summary.get("survey_id") or "")
    latest, _tag_file = read_latest_review_tags(data_dir, active_survey_id)
    report = build_survey_report(
        rows,
        latest,
        queue_summary=queue_summary,
        survey_id=active_survey_id,
    )
    output = Path(args.out) if args.out else data_dir / REVIEW_DIR_NAME / REVIEW_REPORT_NAME
    write_survey_report(report, output, force=args.force_output)
    print(json.dumps({
        "output": str(output),
        "survey_id": report["survey_id"],
        "total": report["total"],
        "completed": report["completed"],
        "pending": report["pending"],
        "post_filter_fixable_ratio": report["post_filter_fixable"]["ratio"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
