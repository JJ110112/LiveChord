"""Summarize held-out RH melody song-type labels.

Example:
  python tools/report_rh_song_type_labels.py --force-output
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
    build_label_report,
    read_label_rows,
    write_label_report,
)


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")
DEFAULT_LABELS = Path(r"V:\data\melody_reviews\phase0_5_song_type_labels.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_report.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Report RH melody song-type label progress and baseline confusion.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Held-out label queue JSONL.")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Human label JSONL.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Report output (.json or .md).")
    parser.add_argument("--prediction-field", default="candidate_hint", help="Queue field to treat as prediction.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing report output.")
    args = parser.parse_args()

    queue_rows = read_label_rows(Path(args.queue))
    label_rows = read_label_rows(Path(args.labels))
    report = build_label_report(queue_rows, label_rows, prediction_field=args.prediction_field)
    output = write_label_report(report, Path(args.out), force=args.force_output)
    vocal_precision = report["precision_by_label"].get("vocal_led")
    vocal_text = "n/a" if vocal_precision is None else f"{vocal_precision:.3f}"
    print(
        f"Wrote {output}; labeled {report['labeled']}/{report['total']}; "
        f"vocal_led precision={vocal_text}"
    )
    return 0 if report["labeled"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
