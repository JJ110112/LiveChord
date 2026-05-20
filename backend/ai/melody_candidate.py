"""Shared cache and payload helpers for RH melody shadow candidates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .melody_schema import finalize_melody_payload


MELODY_CANDIDATE_CACHE_VERSION = "rhmelody-candidate-v1"
MELODY_CANDIDATE_PHASE = "phase0_5_shadow"
MELODY_CANDIDATE_DIR_NAME = "melody_candidates"
MELODY_SELECTED_DIR_NAME = "melodies_rh_v2"
STEM_CACHE_DIR_NAME = "stems"

FULL_MIX_PYIN = "full_mix_pyin"
VOCAL_STEM_CREPE = "vocal_stem_crepe"
VOCAL_FULL_MIX_FTANET = "vocal_full_mix_ftanet"
SOLO_PIANO_POLYPHONIC = "solo_piano_polyphonic"
INSTRUMENT_LEAD = "instrument_lead"
MIDI_ALIGNED = "midi_aligned"

VALID_CANDIDATE_IDS = {
    FULL_MIX_PYIN,
    VOCAL_STEM_CREPE,
    VOCAL_FULL_MIX_FTANET,
    SOLO_PIANO_POLYPHONIC,
    INSTRUMENT_LEAD,
    MIDI_ALIGNED,
}


def shard_for_hash(song_hash: str) -> str:
    clean = _clean_hash(song_hash)
    return clean[:2] if len(clean) >= 2 else "__"


def candidate_dir(data_dir: Path, song_hash: str) -> Path:
    clean = _clean_hash(song_hash)
    return Path(data_dir) / MELODY_CANDIDATE_DIR_NAME / shard_for_hash(clean) / clean


def candidate_path(data_dir: Path, song_hash: str, candidate_id: str) -> Path:
    clean_id = _clean_candidate_id(candidate_id)
    return candidate_dir(data_dir, song_hash) / f"{clean_id}.json"


def selected_path(data_dir: Path, song_hash: str) -> Path:
    """Path reserved for the future resolver-selected cache.

    Phase 0.5 writes shadow candidates only. The Phase 5 resolver promotion will
    be the first writer for this path.
    """

    clean = _clean_hash(song_hash)
    return Path(data_dir) / MELODY_SELECTED_DIR_NAME / shard_for_hash(clean) / f"{clean}.json"


def stem_dir(data_dir: Path, song_hash: str) -> Path:
    clean = _clean_hash(song_hash)
    return Path(data_dir) / STEM_CACHE_DIR_NAME / shard_for_hash(clean) / clean


def stem_path(data_dir: Path, song_hash: str, stem_name: str) -> Path:
    return stem_dir(data_dir, song_hash) / f"{_clean_stem_name(stem_name)}.wav"


def build_candidate_payload(
    *,
    song_hash: str,
    path: str,
    candidate_id: str,
    melody: Iterable[Dict[str, Any]],
    stem: str,
    algorithm: str,
    quality_flags: Optional[List[str]] = None,
    bpm: float = 120.0,
    tempo_curve: Optional[List[Dict[str, Any]]] = None,
    time_signature: str = "4/4",
    song_type: str = "unknown",
    song_type_confidence: Optional[float] = None,
    candidate_score: Optional[float] = None,
    build_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a legacy-compatible melody payload stamped as a shadow candidate."""

    clean_id = _clean_candidate_id(candidate_id)
    payload = {
        "song_hash": _clean_hash(song_hash),
        "path": path or "",
        "melody": list(melody or []),
        "melody_source": {
            "id": clean_id,
            "stem": stem,
            "algorithm": algorithm,
            "song_type": song_type,
            "song_type_confidence": song_type_confidence,
            "selected_by": "shadow_candidate",
            "candidate_score": candidate_score,
            "margin_over_fallback": None,
            "cache_version": MELODY_CANDIDATE_CACHE_VERSION,
            "phase": MELODY_CANDIDATE_PHASE,
        },
        "quality_flags": list(quality_flags or []),
        "candidate": {
            "id": clean_id,
            "schema_version": 1,
            "build_info": build_info or {},
        },
    }
    return finalize_melody_payload(
        payload,
        path=path or "",
        bpm=bpm,
        tempo_curve=tempo_curve,
        time_signature=time_signature,
    )


def write_candidate_cache(
    data_dir: Path,
    song_hash: str,
    candidate_id: str,
    payload: Dict[str, Any],
) -> Path:
    out = candidate_path(data_dir, song_hash, candidate_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, out)
    return out


def read_candidate_cache(data_dir: Path, song_hash: str, candidate_id: str) -> Optional[Dict[str, Any]]:
    path = candidate_path(data_dir, song_hash, candidate_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _clean_hash(song_hash: str) -> str:
    clean = str(song_hash or "").strip()
    if not clean:
        raise ValueError("song_hash is required")
    return clean


def _clean_candidate_id(candidate_id: str) -> str:
    clean = str(candidate_id or "").strip()
    if clean not in VALID_CANDIDATE_IDS:
        allowed = ", ".join(sorted(VALID_CANDIDATE_IDS))
        raise ValueError(f"candidate_id must be one of: {allowed}")
    return clean


def _clean_stem_name(stem_name: str) -> str:
    clean = str(stem_name or "").strip().lower()
    if clean not in {"vocals", "bass", "drums", "other"}:
        raise ValueError("stem_name must be one of: vocals, bass, drums, other")
    return clean
