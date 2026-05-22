"""Evaluate the conservative RH vocal-route gate on extracted feature rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.song_type_label_queue import read_label_rows  # noqa: E402
from ai.song_type_vocal_gate import (  # noqa: E402
    DEFAULT_MIN_DURATION_S,
    DEFAULT_VOCAL_RATIO_THRESHOLD,
    evaluate_vocal_gate,
    write_vocal_gate_report,
)


DEFAULT_FEATURES = Path(r"V:\data\melody_reviews\phase0_5_song_type_audio_features.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_vocal_gate_eval.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RH vocal-route gate from audio feature JSONL.")
    parser.add_argument("--features", default=str(DEFAULT_FEATURES), help="Audio feature JSONL.")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Evaluation report JSON.")
    parser.add_argument("--vocal-ratio-threshold", type=float, default=DEFAULT_VOCAL_RATIO_THRESHOLD)
    parser.add_argument("--min-duration-s", type=float, default=DEFAULT_MIN_DURATION_S)
    parser.add_argument("--force-output", action="store_true", help="Overwrite output.")
    args = parser.parse_args()

    rows = read_label_rows(Path(args.features))
    report = evaluate_vocal_gate(
        rows,
        vocal_ratio_threshold=args.vocal_ratio_threshold,
        min_duration_s=args.min_duration_s,
    )
    output = write_vocal_gate_report(report, Path(args.out), force=args.force_output)
    precision = report["precision"]
    recall = report["recall"]
    precision_text = "n/a" if precision is None else f"{precision:.3f}"
    recall_text = "n/a" if recall is None else f"{recall:.3f}"
    print(
        f"Wrote {output}; predicted_vocal={report['predicted_vocal']}; "
        f"precision={precision_text}; recall={recall_text}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
