"""Train/evaluate the RH melody metadata song-type classifier."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.song_type_classifier import evaluate_leave_one_out, train_metadata_nb, write_model  # noqa: E402
from ai.song_type_label_queue import merge_queue_with_latest_labels, read_label_rows  # noqa: E402


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")
DEFAULT_LABELS = Path(r"V:\data\melody_reviews\phase0_5_song_type_labels.jsonl")
DEFAULT_MODEL = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_nb.json")
DEFAULT_REPORT = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_nb_eval.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and leave-one-out evaluate RH song-type metadata classifier.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Held-out label queue JSONL.")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Human label JSONL.")
    parser.add_argument("--model-out", default=str(DEFAULT_MODEL), help="Model artifact JSON.")
    parser.add_argument("--report-out", default=str(DEFAULT_REPORT), help="Evaluation report JSON.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing outputs.")
    args = parser.parse_args()

    queue_rows = read_label_rows(Path(args.queue))
    label_rows = read_label_rows(Path(args.labels))
    merged = merge_queue_with_latest_labels(queue_rows, label_rows)
    labeled = [row for row in merged if row.get("resolved_label")]
    if len(labeled) < 3:
        print(f"Need at least 3 labeled rows; got {len(labeled)}", file=sys.stderr)
        return 1

    model = train_metadata_nb(labeled)
    report = evaluate_leave_one_out(labeled)
    write_model(model, Path(args.model_out), force=args.force_output)
    _write_json(report, Path(args.report_out), force=args.force_output)
    vocal_precision = report["precision_by_label"].get("vocal_led")
    vocal_text = "n/a" if vocal_precision is None else f"{vocal_precision:.3f}"
    print(
        f"Trained on {len(labeled)} rows; LOO vocal_led precision={vocal_text}; "
        f"model={args.model_out}; report={args.report_out}"
    )
    return 0


def _write_json(data, output: Path, *, force: bool) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(output)
    return output


if __name__ == "__main__":
    raise SystemExit(main())
