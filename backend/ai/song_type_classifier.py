"""Small metadata classifier for RH melody song-type routing experiments."""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .song_type_label_queue import LABEL_OPTIONS


MODEL_VERSION = "metadata_nb_v3"
AUDIO_MODEL_VERSION = "metadata_audio_nb_v2"
TEXT_FIELDS = ("path", "title", "artist", "album", "genre")
STOP_TOKENS = {
    "a",
    "an",
    "and",
    "feat",
    "ft",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def prepare_labeled_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    prepared = []
    for row in rows:
        label = str(row.get("resolved_label") or row.get("human_label") or "").strip()
        if label in LABEL_OPTIONS:
            prepared.append(dict(row))
    return prepared


def train_metadata_nb(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    labeled = prepare_labeled_rows(rows)
    model_version = AUDIO_MODEL_VERSION if any(_row_has_audio_features(row) for row in labeled) else MODEL_VERSION
    label_doc_counts: Counter[str] = Counter()
    token_counts: Dict[str, Counter[str]] = {}
    total_tokens: Counter[str] = Counter()
    vocab: set[str] = set()
    for row in labeled:
        label = str(row.get("resolved_label") or row.get("human_label"))
        label_doc_counts[label] += 1
        token_counts.setdefault(label, Counter())
        tokens = _tokens_for_row(row)
        token_counts[label].update(tokens)
        total_tokens[label] += len(tokens)
        vocab.update(tokens)

    return {
        "model_version": model_version,
        "labels": list(LABEL_OPTIONS),
        "text_fields": list(TEXT_FIELDS),
        "audio_features": model_version == AUDIO_MODEL_VERSION,
        "documents": len(labeled),
        "label_doc_counts": dict(label_doc_counts),
        "token_counts": {label: dict(counts) for label, counts in token_counts.items()},
        "total_tokens": dict(total_tokens),
        "vocab": sorted(vocab),
    }


def predict_metadata_nb(row: Mapping[str, Any], model: Mapping[str, Any]) -> Dict[str, Any]:
    model_version = str(model.get("model_version") or MODEL_VERSION)
    label_doc_counts = {
        str(label): int(count)
        for label, count in (model.get("label_doc_counts") or {}).items()
    }
    labels = [label for label in LABEL_OPTIONS if label_doc_counts.get(label, 0) > 0]
    if not labels:
        return {"song_type": "unknown", "song_type_confidence": 0.0, "song_type_source": model_version}

    token_counts = model.get("token_counts") or {}
    total_tokens = {
        str(label): int(count)
        for label, count in (model.get("total_tokens") or {}).items()
    }
    vocab = set(str(token) for token in (model.get("vocab") or []))
    vocab_size = max(1, len(vocab))
    total_docs = max(1, sum(label_doc_counts.values()))
    tokens = _tokens_for_row(row)
    scores: Dict[str, float] = {}
    for label in labels:
        prior = math.log((label_doc_counts.get(label, 0) + 1) / (total_docs + len(labels)))
        denom = total_tokens.get(label, 0) + vocab_size
        counts = token_counts.get(label) if isinstance(token_counts.get(label), dict) else {}
        score = prior
        for token in tokens:
            if token not in vocab:
                continue
            score += math.log((int(counts.get(token, 0)) + 1) / denom)
        scores[label] = score

    best = max(scores, key=scores.get)
    confidence = _softmax_confidence(scores, best)
    return {
        "song_type": best,
        "song_type_confidence": confidence,
        "song_type_source": model_version,
        "scores": scores,
    }


def evaluate_leave_one_out(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    labeled = prepare_labeled_rows(rows)
    predictions: List[Dict[str, Any]] = []
    model_version = AUDIO_MODEL_VERSION if any(_row_has_audio_features(row) for row in labeled) else MODEL_VERSION
    for index, row in enumerate(labeled):
        train_rows = labeled[:index] + labeled[index + 1:]
        model = train_metadata_nb(train_rows)
        prediction = predict_metadata_nb(row, model)
        predictions.append({
            **dict(row),
            "prediction": prediction["song_type"],
            "prediction_confidence": prediction["song_type_confidence"],
        })
    return _classification_report(predictions, prediction_field="prediction", model_version=model_version)


def merge_audio_feature_rows(
    rows: Sequence[Mapping[str, Any]],
    feature_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach extracted audio features to labeled rows by survey/hash identity."""

    by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
    by_hash: Dict[str, Dict[str, Any]] = {}
    for feature in feature_rows:
        if str(feature.get("status") or "") != "ok":
            continue
        survey_id = str(feature.get("survey_id") or "").strip()
        song_hash = str(feature.get("song_hash") or feature.get("hash") or "").strip()
        if not song_hash:
            continue
        item = dict(feature)
        by_hash[song_hash] = item
        if survey_id:
            by_key[(survey_id, song_hash)] = item

    merged: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        survey_id = str(item.get("survey_id") or "").strip()
        song_hash = str(item.get("song_hash") or item.get("hash") or "").strip()
        features = by_key.get((survey_id, song_hash)) or by_hash.get(song_hash)
        if features:
            item["_audio_features"] = features
        merged.append(item)
    return merged


def write_model(model: Mapping[str, Any], output_path: Path, *, force: bool = False) -> Path:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass force=True to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output_path)
    return output_path


def _classification_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    prediction_field: str,
    model_version: str = MODEL_VERSION,
) -> Dict[str, Any]:
    confusion: Dict[str, Dict[str, int]] = {}
    for row in rows:
        pred = str(row.get(prediction_field) or "unknown")
        gold = str(row.get("resolved_label") or row.get("human_label") or "unknown")
        confusion.setdefault(pred, {})
        confusion[pred][gold] = confusion[pred].get(gold, 0) + 1
    precision_by_label: Dict[str, float | None] = {}
    recall_by_label: Dict[str, float | None] = {}
    for label in LABEL_OPTIONS:
        predicted = confusion.get(label, {})
        predicted_total = sum(predicted.values())
        precision_by_label[label] = predicted.get(label, 0) / predicted_total if predicted_total else None
        gold_total = sum(predicted_counts.get(label, 0) for predicted_counts in confusion.values())
        recall_by_label[label] = confusion.get(label, {}).get(label, 0) / gold_total if gold_total else None
    return {
        "model_version": model_version,
        "prediction_field": prediction_field,
        "total": len(rows),
        "confusion": confusion,
        "precision_by_label": precision_by_label,
        "recall_by_label": recall_by_label,
    }


def _tokens_for_row(row: Mapping[str, Any]) -> List[str]:
    text = " ".join(str(row.get(field) or "") for field in TEXT_FIELDS).lower()
    text = text.replace("\\", "/")
    raw_tokens = re.findall(r"[\w#]+", text, flags=re.UNICODE)
    tokens = [token for token in raw_tokens if len(token) >= 2 and token not in STOP_TOKENS]
    path = str(row.get("path") or "").lower().replace("\\", "/")
    for part in path.split("/"):
        clean = part.strip()
        if clean and "." not in clean:
            tokens.append(f"path:{clean}")
    tokens.extend(_audio_feature_tokens(row))
    return tokens or ["__empty__"]


def _audio_feature_tokens(row: Mapping[str, Any]) -> List[str]:
    features = row.get("_audio_features")
    if not isinstance(features, Mapping):
        features = row
    mix = features.get("mix") if isinstance(features.get("mix"), Mapping) else {}
    stems = features.get("stems") if isinstance(features.get("stems"), Mapping) else {}
    if not mix and not stems:
        return []

    tokens: List[str] = []
    for name, thresholds in {
        "hpss_harmonic_ratio": (0.50, 0.75, 0.85, 0.92),
        "onset_density_per_s": (2.0, 3.0, 4.0, 5.0),
        "spectral_flatness_mean": (0.002, 0.006, 0.015, 0.04),
        "spectral_centroid_mean": (900.0, 1300.0, 1700.0, 2200.0),
        "zero_crossing_rate_mean": (0.06, 0.09, 0.12, 0.16),
        "rms_cov": (0.35, 0.60, 0.90, 1.30),
    }.items():
        value = _optional_float(mix.get(name))
        if value is not None:
            tokens.append(f"audio:{name}:{_bucket(value, thresholds)}")

    stem_status = str(stems.get("stem_status") or "").strip()
    if stem_status:
        tokens.append(f"audio:stem_status:{stem_status}")
    for name, thresholds in {
        "vocal_stem_energy_ratio": (0.05, 0.15, 0.30, 0.50),
        "vocal_vs_other_energy_ratio": (0.10, 0.25, 0.45, 0.65),
    }.items():
        value = _optional_float(stems.get(name))
        if value is not None:
            tokens.append(f"audio:{name}:{_bucket(value, thresholds)}")
    return tokens


def _row_has_audio_features(row: Mapping[str, Any]) -> bool:
    features = row.get("_audio_features")
    return (
        isinstance(features, Mapping)
        or isinstance(row.get("mix"), Mapping)
        or isinstance(row.get("stems"), Mapping)
    )


def _bucket(value: float, thresholds: Sequence[float]) -> str:
    for index, threshold in enumerate(thresholds):
        if value < threshold:
            return f"b{index}"
    return f"b{len(thresholds)}"


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _softmax_confidence(scores: Mapping[str, float], best: str) -> float:
    max_score = max(scores.values())
    exps = {label: math.exp(score - max_score) for label, score in scores.items()}
    total = sum(exps.values()) or 1.0
    return float(exps[best] / total)
