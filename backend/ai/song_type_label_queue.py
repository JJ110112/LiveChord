"""Held-out song-type label queue helpers for RH melody resolver work."""

from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple
from urllib.parse import quote


LABEL_QUEUE_SCHEMA_VERSION = 1
LABEL_QUEUE_PHASE = "phase0_5_song_type"
LABEL_OPTIONS = ("vocal_led", "solo_piano", "instrumental_lead", "no_clear_lead", "unknown")
DEFAULT_LABEL_QUOTAS = {
    "vocal_led": 18,
    "solo_piano": 12,
    "instrumental_lead": 12,
    "no_clear_lead": 6,
}
DEFAULT_BASE_URL = "http://192.168.50.6:8800"


def read_library_tracks(library_cache: Path) -> List[Dict[str, Any]]:
    data = json.loads(library_cache.read_text(encoding="utf-8-sig"))
    tracks = data.get("tracks") if isinstance(data, dict) else data
    return [track for track in tracks if isinstance(track, dict)] if isinstance(tracks, list) else []


def load_excluded_hashes(paths: Sequence[Path]) -> set[str]:
    hashes: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        for row in _read_jsonl(path):
            song_hash = str(row.get("hash") or row.get("song_hash") or "").strip()
            result = row.get("result") if isinstance(row.get("result"), dict) else {}
            if not song_hash and result:
                song_hash = str(result.get("song_hash") or "").strip()
            if song_hash:
                hashes.add(song_hash)
    return hashes


def build_label_candidates(
    tracks: Iterable[Dict[str, Any]],
    *,
    exclude_hashes: Iterable[str] = (),
    base_url: str = DEFAULT_BASE_URL,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from chord_cache import song_hash

    excluded = set(exclude_hashes)
    candidates: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "checked": 0,
        "missing_path": 0,
        "excluded": 0,
        "by_hint": {label: 0 for label in DEFAULT_LABEL_QUOTAS},
    }
    seen: set[str] = set()
    for track in tracks:
        stats["checked"] += 1
        path = str(track.get("path") or "").strip()
        if not path:
            stats["missing_path"] += 1
            continue
        h = str(track.get("hash") or "").strip() or song_hash(path)
        if h in excluded or h in seen:
            stats["excluded"] += 1
            continue
        seen.add(h)
        hint, reason = infer_song_type_hint(track)
        stats["by_hint"][hint] = stats["by_hint"].get(hint, 0) + 1
        candidates.append({
            "hash": h,
            "path": path,
            "title": str(track.get("title") or _title_from_path(path)),
            "artist": str(track.get("artist") or ""),
            "album": str(track.get("album") or ""),
            "genre": str(track.get("genre") or ""),
            "duration_s": _first_float(track.get("duration_s"), track.get("duration")),
            "candidate_hint": hint,
            "hint_reason": reason,
            "player_url": f"{base_url.rstrip('/')}/player?path={quote(path, safe='')}&autoplay=1",
        })
    stats["candidates"] = len(candidates)
    return candidates, stats


def infer_song_type_hint(track: Mapping[str, Any]) -> Tuple[str, str]:
    fields = [
        str(track.get("path") or ""),
        str(track.get("title") or ""),
        str(track.get("artist") or ""),
        str(track.get("album") or ""),
        str(track.get("genre") or ""),
    ]
    text = " ".join(fields).lower()
    normalized = re.sub(r"[_\-]+", " ", text)
    path_text = str(track.get("path") or "").lower().replace("\\", "/")
    genre_text = str(track.get("genre") or "").lower()

    no_lead_terms = (
        "jam track", "backing track", "karaoke", "minus one", "ambient", "meditation",
        "rain sound", "white noise", "no vocal", "zen garden", "lullaby",
    )
    if (
        any(term in normalized for term in no_lead_terms)
        or "/sleep/" in path_text
        or genre_text in {"sleep", "ambient"}
    ):
        return "no_clear_lead", "metadata_no_clear_lead"

    solo_piano_terms = (
        "solo piano", "piano solo", "piano sonata", "nocturne", "clair de lune",
        "moonlight sonata", "chopin", "debussy", "liszt", "rachmaninoff", "satie",
        "alexis ffrench", "richard clayderman",
    )
    if any(term in normalized for term in solo_piano_terms):
        return "solo_piano", "metadata_solo_piano"

    instrumental_terms = (
        "instrumental", "fusion", "jazz", "sax", "saxophone", "t-square", "casiopea",
        "dave brubeck", "santana", "guitar instrumental", "smooth jazz",
    )
    if any(term in normalized for term in instrumental_terms):
        return "instrumental_lead", "metadata_instrumental"

    vocal_terms = (
        "official music video", "vevo", "pop", "love song", "vocal", "karaoke version",
        "remastered", "lyrics",
    )
    if any(term in normalized for term in vocal_terms):
        return "vocal_led", "metadata_vocal"

    return "vocal_led", "default_vocal_prior"


def sample_label_queue(
    candidates: Sequence[Dict[str, Any]],
    *,
    quotas: Mapping[str, int] | None = None,
    seed: int = 20260522,
) -> List[Dict[str, Any]]:
    quotas = dict(quotas or DEFAULT_LABEL_QUOTAS)
    rng = random.Random(seed)
    by_hint: Dict[str, List[Dict[str, Any]]] = {}
    for item in candidates:
        by_hint.setdefault(str(item.get("candidate_hint") or "unknown"), []).append(dict(item))
    for items in by_hint.values():
        rng.shuffle(items)

    selected: List[Dict[str, Any]] = []
    selected_hashes: set[str] = set()
    for hint, quota in quotas.items():
        for item in by_hint.get(hint, [])[:max(0, int(quota))]:
            selected.append(item)
            selected_hashes.add(str(item.get("hash") or ""))

    target_size = sum(max(0, int(value)) for value in quotas.values())
    if len(selected) < target_size:
        leftovers = [dict(item) for item in candidates if str(item.get("hash") or "") not in selected_hashes]
        rng.shuffle(leftovers)
        selected.extend(leftovers[:target_size - len(selected)])

    selected.sort(key=lambda item: (
        list(quotas).index(str(item.get("candidate_hint"))) if str(item.get("candidate_hint")) in quotas else 99,
        str(item.get("artist") or ""),
        str(item.get("title") or ""),
    ))
    for order, item in enumerate(selected, start=1):
        item["sample_order"] = order
    return selected


def write_label_queue(
    output_path: Path,
    sample: Sequence[Dict[str, Any]],
    *,
    survey_id: str,
    seed: int,
    candidate_stats: Dict[str, Any],
    quotas: Mapping[str, int],
    force: bool = False,
) -> Dict[str, Any]:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass force=True to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in sample:
            row = {
                "schema_version": LABEL_QUEUE_SCHEMA_VERSION,
                "phase": LABEL_QUEUE_PHASE,
                "survey_id": survey_id,
                "status": "pending",
                "human_label": "pending",
                "label_options": list(LABEL_OPTIONS),
                **dict(item),
            }
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "schema_version": LABEL_QUEUE_SCHEMA_VERSION,
        "phase": LABEL_QUEUE_PHASE,
        "survey_id": survey_id,
        "seed": seed,
        "sample_size": len(sample),
        "quotas": dict(quotas),
        "candidate_stats": candidate_stats,
        "by_hint": _count_by(sample, "candidate_hint"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "output": str(output_path),
    }
    summary_path = output_path.with_suffix(".summary.json")
    tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, summary_path)
    return summary


def parse_quotas(raw: str) -> Dict[str, int]:
    if not raw.strip():
        return dict(DEFAULT_LABEL_QUOTAS)
    quotas: Dict[str, int] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"Invalid quota {part!r}; expected label=count")
        label = key.strip()
        if label not in LABEL_OPTIONS:
            raise ValueError(f"Invalid label {label!r}; expected one of {', '.join(LABEL_OPTIONS)}")
        quotas[label] = int(value.strip())
    return quotas


def _count_by(rows: Iterable[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _title_from_path(path: str) -> str:
    name = Path(path).name
    return name.rsplit(".", 1)[0] if "." in name else name


def _first_float(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
