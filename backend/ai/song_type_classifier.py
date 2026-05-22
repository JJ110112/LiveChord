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


MODEL_VERSION = "metadata_nb_v1"
TEXT_FIELDS = ("path", "title", "artist", "album", "genre")


def prepare_labeled_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    prepared = []
    for row in rows:
        label = str(row.get("resolved_label") or row.get("human_label") or "").strip()
        if label in LABEL_OPTIONS:
            prepared.append(dict(row))
    return prepared


def train_metadata_nb(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    labeled = prepare_labeled_rows(rows)
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
        "model_version": MODEL_VERSION,
        "labels": list(LABEL_OPTIONS),
        "text_fields": list(TEXT_FIELDS),
        "documents": len(labeled),
        "label_doc_counts": dict(label_doc_counts),
        "token_counts": {label: dict(counts) for label, counts in token_counts.items()},
        "total_tokens": dict(total_tokens),
        "vocab": sorted(vocab),
    }


def predict_metadata_nb(row: Mapping[str, Any], model: Mapping[str, Any]) -> Dict[str, Any]:
    label_doc_counts = {
        str(label): int(count)
        for label, count in (model.get("label_doc_counts") or {}).items()
    }
    labels = [label for label in LABEL_OPTIONS if label_doc_counts.get(label, 0) > 0]
    if not labels:
        return {"song_type": "unknown", "song_type_confidence": 0.0, "song_type_source": MODEL_VERSION}

    token_counts = model.get("token_counts") or {}
    total_tokens = {
        str(label): int(count)
        for label, count in (model.get("total_tokens") or {}).items()
    }
    vocab_size = max(1, len(model.get("vocab") or []))
    total_docs = max(1, sum(label_doc_counts.values()))
    tokens = _tokens_for_row(row)
    scores: Dict[str, float] = {}
    for label in labels:
        prior = math.log((label_doc_counts.get(label, 0) + 1) / (total_docs + len(labels)))
        denom = total_tokens.get(label, 0) + vocab_size
        counts = token_counts.get(label) if isinstance(token_counts.get(label), dict) else {}
        score = prior
        for token in tokens:
            score += math.log((int(counts.get(token, 0)) + 1) / denom)
        scores[label] = score

    best = max(scores, key=scores.get)
    confidence = _softmax_confidence(scores, best)
    return {
        "song_type": best,
        "song_type_confidence": confidence,
        "song_type_source": MODEL_VERSION,
        "scores": scores,
    }


def evaluate_leave_one_out(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    labeled = prepare_labeled_rows(rows)
    predictions: List[Dict[str, Any]] = []
    for index, row in enumerate(labeled):
        train_rows = labeled[:index] + labeled[index + 1:]
        model = train_metadata_nb(train_rows)
        prediction = predict_metadata_nb(row, model)
        predictions.append({
            **dict(row),
            "prediction": prediction["song_type"],
            "prediction_confidence": prediction["song_type_confidence"],
        })
    return _classification_report(predictions, prediction_field="prediction")


def write_model(model: Mapping[str, Any], output_path: Path, *, force: bool = False) -> Path:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass force=True to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output_path)
    return output_path


def _classification_report(rows: Sequence[Mapping[str, Any]], *, prediction_field: str) -> Dict[str, Any]:
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
        gold_total = sum(row.get(label, 0) for row in confusion.values())
        recall_by_label[label] = confusion.get(label, {}).get(label, 0) / gold_total if gold_total else None
    return {
        "model_version": MODEL_VERSION,
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
    tokens = [token for token in raw_tokens if len(token) >= 2]
    path = str(row.get("path") or "").lower().replace("\\", "/")
    for part in path.split("/"):
        clean = part.strip()
        if clean and "." not in clean:
            tokens.append(f"path:{clean}")
    return tokens or ["__empty__"]


def _softmax_confidence(scores: Mapping[str, float], best: str) -> float:
    max_score = max(scores.values())
    exps = {label: math.exp(score - max_score) for label, score in scores.items()}
    total = sum(exps.values()) or 1.0
    return float(exps[best] / total)
