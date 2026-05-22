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

from ai.song_type_classifier import (  # noqa: E402
    evaluate_leave_one_out,
    merge_audio_feature_rows,
    train_metadata_nb,
    write_model,
)
from ai.song_type_label_queue import merge_queue_with_latest_labels, read_label_rows  # noqa: E402


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")
DEFAULT_LABELS = Path(r"V:\data\melody_reviews\phase0_5_song_type_labels.jsonl")
DEFAULT_MODEL = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_nb.json")
DEFAULT_REPORT = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_nb_eval.json")
DEFAULT_AUDIO_FEATURES = Path(r"V:\data\melody_reviews\phase0_5_song_type_audio_features.jsonl")
DEFAULT_AUDIO_MODEL = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_audio_nb.json")
DEFAULT_AUDIO_REPORT = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_audio_nb_eval.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and leave-one-out evaluate RH song-type metadata classifier.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Held-out label queue JSONL.")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Human label JSONL.")
    parser.add_argument("--audio-features", default="", help=f"Optional audio feature JSONL. Example: {DEFAULT_AUDIO_FEATURES}")
    parser.add_argument("--model-out", default="", help="Model artifact JSON.")
    parser.add_argument("--report-out", default="", help="Evaluation report JSON.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite existing outputs.")
    args = parser.parse_args()

    queue_rows = read_label_rows(Path(args.queue))
    label_rows = read_label_rows(Path(args.labels))
    merged = merge_queue_with_latest_labels(queue_rows, label_rows)
    if args.audio_features:
        feature_rows = read_label_rows(Path(args.audio_features))
        merged = merge_audio_feature_rows(merged, feature_rows)
    labeled = [row for row in merged if row.get("resolved_label")]
    if len(labeled) < 3:
        print(f"Need at least 3 labeled rows; got {len(labeled)}", file=sys.stderr)
        return 1

    model_out = Path(args.model_out) if args.model_out else (DEFAULT_AUDIO_MODEL if args.audio_features else DEFAULT_MODEL)
    report_out = Path(args.report_out) if args.report_out else (DEFAULT_AUDIO_REPORT if args.audio_features else DEFAULT_REPORT)
    model = train_metadata_nb(labeled)
    report = evaluate_leave_one_out(labeled)
    report["audio_features"] = bool(args.audio_features)
    report["audio_feature_rows"] = sum(1 for row in labeled if row.get("_audio_features")) if args.audio_features else 0
    write_model(model, model_out, force=args.force_output)
    _write_json(report, report_out, force=args.force_output)
    vocal_precision = report["precision_by_label"].get("vocal_led")
    vocal_text = "n/a" if vocal_precision is None else f"{vocal_precision:.3f}"
    print(
        f"Trained on {len(labeled)} rows; LOO vocal_led precision={vocal_text}; "
        f"model={model_out}; report={report_out}"
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
