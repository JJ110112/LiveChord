"""Conservative vocal-route gate for RH melody resolver experiments."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


VOCAL_GATE_VERSION = "vocal_stem_ratio_gate_v1"
DEFAULT_VOCAL_RATIO_THRESHOLD = 0.06
DEFAULT_MIN_DURATION_S = 30.0
DEFAULT_THRESHOLD_SWEEP = (0.04, 0.05, 0.055, 0.06, 0.065, 0.07, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35)


def classify_vocal_gate(
    row: Mapping[str, Any],
    *,
    vocal_ratio_threshold: float = DEFAULT_VOCAL_RATIO_THRESHOLD,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
) -> Dict[str, Any]:
    """Return whether a row is safe to route to vocal_stem_crepe.

    This is intentionally a binary conservative gate, not a full song-type
    classifier. Low confidence or missing stems should fall back to full_mix_pyin.
    The 0.06 default is calibrated from the leakage-clean 100-song Phase 0.5
    validation set. It sits inside the observed gap between the highest
    not-vocal stem ratio (~0.054) and lowest vocal-led ratio (~0.065). The 30s
    floor avoids trusting Demucs ratios on very short clips.
    """

    stems = row.get("stems") if isinstance(row.get("stems"), Mapping) else {}
    ratio = _optional_float(stems.get("vocal_stem_energy_ratio"))
    duration_s = _optional_float(row.get("duration_s"))
    if duration_s is None:
        duration_s = _optional_float(row.get("mix", {}).get("analyzed_duration_s") if isinstance(row.get("mix"), Mapping) else None)
    if duration_s is not None and duration_s < min_duration_s:
        return _result(False, ratio, duration_s, "duration_below_min")
    if ratio is None:
        return _result(False, ratio, duration_s, "missing_vocal_stem_ratio")
    if ratio >= vocal_ratio_threshold:
        confidence = min(1.0, max(0.0, (ratio - vocal_ratio_threshold) / max(1e-6, 1.0 - vocal_ratio_threshold)))
        return _result(True, ratio, duration_s, "vocal_ratio_above_threshold", confidence=confidence)
    return _result(False, ratio, duration_s, "vocal_ratio_below_threshold")


def evaluate_vocal_gate(
    rows: Sequence[Mapping[str, Any]],
    *,
    vocal_ratio_threshold: float = DEFAULT_VOCAL_RATIO_THRESHOLD,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
) -> Dict[str, Any]:
    predictions = []
    for row in rows:
        gold = str(row.get("resolved_label") or row.get("human_label") or "")
        gate = classify_vocal_gate(
            row,
            vocal_ratio_threshold=vocal_ratio_threshold,
            min_duration_s=min_duration_s,
        )
        predictions.append({
            "song_hash": str(row.get("song_hash") or row.get("hash") or ""),
            "title": str(row.get("title") or ""),
            "artist": str(row.get("artist") or ""),
            "path": str(row.get("path") or ""),
            "human_label": gold,
            "predict_vocal": gate["predict_vocal"],
            "reason": gate["reason"],
            "confidence": gate["song_type_confidence"],
            "vocal_stem_energy_ratio": gate["vocal_stem_energy_ratio"],
            "duration_s": gate["duration_s"],
        })
    tp = sum(1 for row in predictions if row["predict_vocal"] and row["human_label"] == "vocal_led")
    fp = [row for row in predictions if row["predict_vocal"] and row["human_label"] != "vocal_led"]
    fn = [row for row in predictions if not row["predict_vocal"] and row["human_label"] == "vocal_led"]
    predicted = sum(1 for row in predictions if row["predict_vocal"])
    actual = sum(1 for row in predictions if row["human_label"] == "vocal_led")
    known_predictions = [row for row in predictions if row["human_label"] != "unknown"]
    lenient_predicted = sum(1 for row in known_predictions if row["predict_vocal"])
    lenient_actual = sum(1 for row in known_predictions if row["human_label"] == "vocal_led")
    lenient_tp = sum(1 for row in known_predictions if row["predict_vocal"] and row["human_label"] == "vocal_led")
    lenient_fn = [row for row in known_predictions if not row["predict_vocal"] and row["human_label"] == "vocal_led"]
    unknown_rows = [row for row in predictions if row["human_label"] == "unknown"]
    unknown_predicted_vocal = [row for row in unknown_rows if row["predict_vocal"]]
    return {
        "gate_version": VOCAL_GATE_VERSION,
        "metric_policy": {
            "strict": "unknown labels are counted as not-vocal for precision.",
            "lenient": "unknown labels are excluded from precision and recall denominators.",
        },
        "vocal_ratio_threshold": vocal_ratio_threshold,
        "min_duration_s": min_duration_s,
        "total": len(predictions),
        "known_total": len(known_predictions),
        "unknown_total": len(unknown_rows),
        "unknown_predicted_vocal": len(unknown_predicted_vocal),
        "predicted_vocal": predicted,
        "actual_vocal": actual,
        "true_positive": tp,
        "false_positive": len(fp),
        "false_negative": len(fn),
        "precision": (tp / predicted) if predicted else None,
        "recall": (tp / actual) if actual else None,
        "strict_precision": (tp / predicted) if predicted else None,
        "strict_recall": (tp / actual) if actual else None,
        "lenient_precision": (lenient_tp / lenient_predicted) if lenient_predicted else None,
        "lenient_recall": (lenient_tp / lenient_actual) if lenient_actual else None,
        "false_positives": fp,
        "false_negatives": fn,
        "lenient_false_negatives": lenient_fn,
        "unknown_rows": unknown_rows,
        "rows": predictions,
    }


def threshold_sweep(
    rows: Sequence[Mapping[str, Any]],
    *,
    thresholds: Sequence[float] = DEFAULT_THRESHOLD_SWEEP,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
) -> Dict[str, Dict[str, Any]]:
    return {
        f"{float(threshold):.3f}": {
            key: value
            for key, value in evaluate_vocal_gate(
                rows,
                vocal_ratio_threshold=float(threshold),
                min_duration_s=min_duration_s,
            ).items()
            if key not in {"rows", "false_positives", "false_negatives"}
        }
        for threshold in thresholds
    }


def write_vocal_gate_report(report: Mapping[str, Any], output: Path, *, force: bool = False) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass force=True to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output)
    return output


def _result(
    predict_vocal: bool,
    ratio: float | None,
    duration_s: float | None,
    reason: str,
    *,
    confidence: float = 0.0,
) -> Dict[str, Any]:
    return {
        "song_type": "vocal_led" if predict_vocal else "unknown",
        "song_type_confidence": confidence if predict_vocal else 0.0,
        "song_type_source": VOCAL_GATE_VERSION,
        "predict_vocal": predict_vocal,
        "reason": reason,
        "vocal_stem_energy_ratio": ratio,
        "duration_s": duration_s,
    }


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
