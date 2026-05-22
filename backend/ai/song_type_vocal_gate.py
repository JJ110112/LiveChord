"""Conservative vocal-route gate for RH melody resolver experiments."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


VOCAL_GATE_VERSION = "vocal_stem_ratio_gate_v1"
DEFAULT_VOCAL_RATIO_THRESHOLD = 0.30
DEFAULT_MIN_DURATION_S = 30.0
DEFAULT_THRESHOLD_SWEEP = (0.25, 0.30, 0.35)


def classify_vocal_gate(
    row: Mapping[str, Any],
    *,
    vocal_ratio_threshold: float = DEFAULT_VOCAL_RATIO_THRESHOLD,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
) -> Dict[str, Any]:
    """Return whether a row is safe to route to vocal_stem_crepe.

    This is intentionally a binary conservative gate, not a full song-type
    classifier. Low confidence or missing stems should fall back to full_mix_pyin.
    The 0.30 default is an empirical Phase 0.5 candidate chosen from the 48-song
    held-out gap between non-vocal and vocal stem energy ratios; it must be
    revalidated on a leakage-clean set before resolver promotion. The 30s floor
    avoids trusting Demucs ratios on very short clips.
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
    return {
        "gate_version": VOCAL_GATE_VERSION,
        "vocal_ratio_threshold": vocal_ratio_threshold,
        "min_duration_s": min_duration_s,
        "total": len(predictions),
        "predicted_vocal": predicted,
        "actual_vocal": actual,
        "true_positive": tp,
        "false_positive": len(fp),
        "false_negative": len(fn),
        "precision": (tp / predicted) if predicted else None,
        "recall": (tp / actual) if actual else None,
        "false_positives": fp,
        "false_negatives": fn,
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
