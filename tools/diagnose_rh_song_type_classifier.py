"""Diagnose leave-one-out RH song-type classifier errors.

Example:
  python tools/diagnose_rh_song_type_classifier.py --audio-features V:\data\melody_reviews\phase0_5_song_type_audio_features.jsonl --force-output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.song_type_classifier import (  # noqa: E402
    merge_audio_feature_rows,
    predict_metadata_nb,
    prepare_labeled_rows,
    train_metadata_nb,
)
from ai.song_type_label_queue import LABEL_OPTIONS, merge_queue_with_latest_labels, read_label_rows  # noqa: E402


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_song_type_label_queue.jsonl")
DEFAULT_LABELS = Path(r"V:\data\melody_reviews\phase0_5_song_type_labels.jsonl")
DEFAULT_AUDIO_FEATURES = Path(r"V:\data\melody_reviews\phase0_5_song_type_audio_features.jsonl")
DEFAULT_OUTPUT = Path(r"V:\data\melody_reviews\phase0_5_song_type_metadata_audio_nb_diagnostics.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Write per-row LOO diagnostics for the RH song-type classifier.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Held-out label queue JSONL.")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS), help="Human label JSONL.")
    parser.add_argument("--audio-features", default="", help=f"Optional audio feature JSONL. Example: {DEFAULT_AUDIO_FEATURES}")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Diagnostic report JSON.")
    parser.add_argument("--focus-gold", default="", help="Optional gold label filter, e.g. vocal_led.")
    parser.add_argument("--focus-prediction", default="", help="Optional prediction filter, e.g. instrumental_lead.")
    parser.add_argument("--force-output", action="store_true", help="Overwrite output.")
    args = parser.parse_args()

    queue_rows = read_label_rows(Path(args.queue))
    label_rows = read_label_rows(Path(args.labels))
    merged = merge_queue_with_latest_labels(queue_rows, label_rows)
    if args.audio_features:
        feature_rows = read_label_rows(Path(args.audio_features))
        merged = merge_audio_feature_rows(merged, feature_rows)
    labeled = prepare_labeled_rows(merged)
    if len(labeled) < 3:
        print(f"Need at least 3 labeled rows; got {len(labeled)}", file=sys.stderr)
        return 1

    report = build_diagnostics(
        labeled,
        focus_gold=args.focus_gold.strip(),
        focus_prediction=args.focus_prediction.strip(),
        audio_features=bool(args.audio_features),
    )
    output = write_diagnostics(report, Path(args.out), force=args.force_output)
    print(
        f"Wrote {output}; rows={report['total']}; errors={report['errors']}; "
        f"focus={len(report['focus_rows'])}"
    )
    return 0


def build_diagnostics(
    rows: Sequence[Mapping[str, Any]],
    *,
    focus_gold: str = "",
    focus_prediction: str = "",
    audio_features: bool = False,
) -> Dict[str, Any]:
    labeled = prepare_labeled_rows(rows)
    prediction_rows: List[Dict[str, Any]] = []
    for index, row in enumerate(labeled):
        train_rows = labeled[:index] + labeled[index + 1:]
        model = train_metadata_nb(train_rows)
        prediction = predict_metadata_nb(row, model)
        gold = str(row.get("resolved_label") or row.get("human_label") or "unknown")
        pred = str(prediction.get("song_type") or "unknown")
        prediction_rows.append({
            "song_hash": str(row.get("song_hash") or row.get("hash") or ""),
            "survey_id": str(row.get("survey_id") or ""),
            "title": str(row.get("title") or ""),
            "artist": str(row.get("artist") or ""),
            "path": str(row.get("path") or ""),
            "duration_s": _optional_float(row.get("duration_s")),
            "candidate_hint": str(row.get("candidate_hint") or ""),
            "human_label": gold,
            "prediction": pred,
            "prediction_confidence": prediction.get("song_type_confidence"),
            "correct": pred == gold,
            "scores": prediction.get("scores") or {},
            "audio_features": _compact_audio_features(row.get("_audio_features")),
            "review_note": _review_note(row),
        })

    focus_rows = [
        row for row in prediction_rows
        if (not focus_gold or row["human_label"] == focus_gold)
        and (not focus_prediction or row["prediction"] == focus_prediction)
    ]
    errors_by_pair: Dict[str, int] = {}
    for row in prediction_rows:
        if row["correct"]:
            continue
        key = f"{row['human_label']}->{row['prediction']}"
        errors_by_pair[key] = errors_by_pair.get(key, 0) + 1
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "audio_features": bool(audio_features),
        "total": len(prediction_rows),
        "correct": sum(1 for row in prediction_rows if row["correct"]),
        "errors": sum(1 for row in prediction_rows if not row["correct"]),
        "errors_by_pair": errors_by_pair,
        "focus_gold": focus_gold,
        "focus_prediction": focus_prediction,
        "labels": list(LABEL_OPTIONS),
        "rows": prediction_rows,
        "focus_rows": focus_rows,
    }


def write_diagnostics(report: Mapping[str, Any], output: Path, *, force: bool) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output)
    return output


def _compact_audio_features(features: Any) -> Dict[str, Any]:
    if not isinstance(features, Mapping):
        return {}
    mix = features.get("mix") if isinstance(features.get("mix"), Mapping) else {}
    stems = features.get("stems") if isinstance(features.get("stems"), Mapping) else {}
    return {
        "stem_status": str(stems.get("stem_status") or ""),
        "vocal_stem_energy_ratio": stems.get("vocal_stem_energy_ratio"),
        "vocal_vs_other_energy_ratio": stems.get("vocal_vs_other_energy_ratio"),
        "hpss_harmonic_ratio": mix.get("hpss_harmonic_ratio"),
        "onset_density_per_s": mix.get("onset_density_per_s"),
        "spectral_flatness_mean": mix.get("spectral_flatness_mean"),
        "spectral_centroid_mean": mix.get("spectral_centroid_mean"),
        "zero_crossing_rate_mean": mix.get("zero_crossing_rate_mean"),
        "rms_cov": mix.get("rms_cov"),
    }


def _review_note(row: Mapping[str, Any]) -> str:
    latest = row.get("latest_label")
    if isinstance(latest, Mapping):
        return str(latest.get("review_note") or "")
    return ""


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
