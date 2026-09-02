"""Chord-progression song search — powers the Progression Library "matching
songs" feature.

Given a roman-numeral progression (degree + major/minor tokens), scans the
prebuilt progression index (data/progression_index.json, see
tools/build_progression_index.py) and returns library songs that contain the
progression in any key. Matching is transposition-invariant and key-field
independent (see progression_match.py).

Read-only. On deployments without an index file (e.g. the public VPS, which has
no NAS library) every query simply returns zero matches.
"""
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

import progression_match as pm

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_FILE = DATA_DIR / "progression_index.json"

# Lazy in-memory cache of the index, invalidated by file mtime so a rebuilt
# index is picked up without a restart.
_cache = {"mtime": -1.0, "songs": None}

# Short-lived cache for /trends responses, keyed by the deduped seq. Cleared
# whenever the underlying index mtime changes (see _load_index).
_trends_cache: dict[str, dict] = {}
_trends_cache_mtime = -1.0

# Roman-numeral labels by relative degree (degree 0 = the start tonic).
# Lowercased by the caller when the chord is minor-family.
_ROMAN_BASE = {
    0: "I", 1: "♭II", 2: "II", 3: "♭III", 4: "III", 5: "IV",
    6: "♭V", 7: "V", 8: "♭VI", 9: "VI", 10: "♭VII", 11: "VII",
}


def _load_index():
    global _trends_cache_mtime
    try:
        mt = INDEX_FILE.stat().st_mtime
    except OSError:
        _cache["songs"] = []
        _cache["mtime"] = -1.0
        return _cache["songs"]
    if _cache["songs"] is not None and _cache["mtime"] == mt:
        return _cache["songs"]
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        _cache["songs"] = data.get("songs", [])
        _cache["mtime"] = mt
    except (OSError, json.JSONDecodeError):
        _cache["songs"] = []
        _cache["mtime"] = -1.0
    # The index changed (or first load) — drop any stale trends responses.
    if _trends_cache_mtime != _cache["mtime"]:
        _trends_cache.clear()
        _trends_cache_mtime = _cache["mtime"]
    return _cache["songs"]


def _dedupe_consecutive(tokens):
    """Drop consecutive duplicate (deg, minor) tokens, matching the packing in
    progression_match.chords_to_packed so a query lines up with the song data."""
    out = []
    prev = None
    for deg, minor in tokens:
        cur = (deg % 12, bool(minor))
        if cur != prev:
            out.append(cur)
            prev = cur
    return out


def _parse_seq(seq: str):
    """"0M-7M-9m-5M" -> [(0, False), (7, False), (9, True), (5, False)]."""
    tokens = []
    for part in seq.split("-"):
        part = part.strip()
        if len(part) < 2:
            continue
        q = part[-1]
        try:
            deg = int(part[:-1])
        except ValueError:
            continue
        # Lowercase 'm' = minor, uppercase 'M' = major. Do NOT lowercase here.
        tokens.append((deg % 12, q == "m"))
    return tokens


@router.get("/api/progression/match")
def match_progression(
    seq: str = Query(..., description="degree+M/m tokens, e.g. 0M-7M-9m-5M"),
    limit: int = Query(24, ge=1, le=60),
    offset: int = Query(0, ge=0, description="page offset into the match list"),
):
    tokens = _parse_seq(seq)
    if len(tokens) < 2:
        raise HTTPException(status_code=400, detail="progression must have at least 2 chords")
    variants = pm.query_variants(tokens)
    songs = _load_index()
    t0 = time.time()
    matches = [s for s in songs if pm.song_matches(s[3], variants)]
    page = matches[offset:offset + limit]
    out = [
        {"hash": s[0], "title": s[1], "key": s[2], "path": s[4] if len(s) > 4 else ""}
        for s in page
    ]
    return {
        "count": len(matches),
        "offset": offset,
        "limit": limit,
        "indexed": len(songs),
        "took_ms": round((time.time() - t0) * 1000, 1),
        "songs": out,
    }


# Cap on how many next-chord candidates we return — beyond ~12 the long tail is
# noise no one drills into.
_TRENDS_TOP_N = 12


@router.get("/api/progression/trends")
def progression_trends(
    seq: str = Query(..., description="relative degrees from the start tonic, e.g. 0M-7M-9m"),
):
    """Next-chord probability distribution for a progression path.

    The path is a sequence of degrees RELATIVE to the chord you started from
    (degree 0 = the tonic 'I'). For every library song we slide all 12
    transpositions of the path over the song's key-free packed sequence; each
    physical occurrence is counted once (it matches exactly one transposition),
    and the chord that immediately follows it is tallied as a relative degree.
    The result is a transposition-invariant Markov step pooled over the corpus —
    same key-free philosophy as /api/progression/match.
    """
    tokens = _dedupe_consecutive(_parse_seq(seq))
    if not tokens:
        raise HTTPException(status_code=400, detail="progression must have at least 1 chord")

    songs = _load_index()
    cache_key = "-".join(f"{d}{'m' if m else 'M'}" for d, m in tokens)
    cached = _trends_cache.get(cache_key)
    if cached is not None:
        return cached

    variants = pm.query_variants(tokens)  # 12 transposed packed strings of the full path
    plen = len(variants[0]) if variants else 0
    counts: dict[tuple, int] = {}
    total = 0
    t0 = time.time()

    if plen:
        for s in songs:
            packed = s[3]
            slen = len(packed)
            for t, v in enumerate(variants):
                start = 0
                find = packed.find
                while True:
                    i = find(v, start)
                    if i < 0:
                        break
                    j = i + plen  # index of the chord following the matched run
                    if j < slen:
                        nv = ord(packed[j]) - 48
                        key = ((nv >> 1) - t) % 12, nv & 1
                        counts[key] = counts.get(key, 0) + 1
                        total += 1
                    # Advance by 1 so overlapping occurrences of a repeating path
                    # (e.g. I-V-I-V) are each counted as a distinct transition.
                    start = i + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TRENDS_TOP_N]
    nxt = [
        {
            "deg": deg,
            "minor": bool(minor),
            "roman": _ROMAN_BASE[deg].lower() if minor else _ROMAN_BASE[deg],
            "count": c,
            "prob": round(c / total, 4) if total else 0.0,
        }
        for (deg, minor), c in ranked
    ]
    result = {
        "indexed": len(songs),
        "path_len": len(tokens),
        "total": total,
        "next": nxt,
        "took_ms": round((time.time() - t0) * 1000, 1),
    }
    _trends_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Custom progressions — user-collected progressions (personal use). Stored in
# data/custom_progressions.json (tier1 backup). Chord specs mirror the frontend
# LIBRARY shape: [degree 0-11, quality, optional bass degree].
# ---------------------------------------------------------------------------
import os
import re
import threading
import uuid

from pydantic import BaseModel, Field

CUSTOM_FILE = DATA_DIR / "custom_progressions.json"
_custom_lock = threading.Lock()

_CUSTOM_QUALITIES = {
    "maj", "m", "dim", "aug", "maj7", "m7", "7", "m7b5", "dim7", "maj9", "m9",
    "9", "7sus4", "6", "m6", "add9", "6/9", "11", "m11", "13", "sus2", "sus4",
}
_CUSTOM_MAX = 200


class CustomProgressionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    chords: list = Field(..., min_length=2, max_length=32)
    desc: str = Field("", max_length=500)
    source_url: str = Field("", max_length=500)
    input_text: str = Field("", max_length=300)


def _read_custom() -> list:
    try:
        data = json.loads(CUSTOM_FILE.read_text(encoding="utf-8"))
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_custom(items: list) -> None:
    CUSTOM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CUSTOM_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, CUSTOM_FILE)


def _normalize_chords(chords) -> list:
    out = []
    for spec in chords:
        if not isinstance(spec, (list, tuple)) or len(spec) < 2:
            raise HTTPException(status_code=400, detail="each chord must be [degree, quality]")
        try:
            deg = int(spec[0]) % 12
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="chord degree must be an integer")
        q = str(spec[1])
        if q not in _CUSTOM_QUALITIES:
            raise HTTPException(status_code=400, detail=f"unsupported chord quality: {q}")
        item = [deg, q]
        if len(spec) > 2 and spec[2] is not None:
            try:
                item.append(int(spec[2]) % 12)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="bass degree must be an integer")
        out.append(item)
    return out


def _validate_custom(body: CustomProgressionIn) -> dict:
    url = body.source_url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        raise HTTPException(status_code=400, detail="source_url must start with http:// or https://")
    return {
        "name": body.name.strip(),
        "chords": _normalize_chords(body.chords),
        "desc": body.desc.strip(),
        "source_url": url,
        "input_text": body.input_text.strip(),
    }


@router.get("/api/progression/custom")
def list_custom_progressions():
    return {"items": _read_custom()}


@router.post("/api/progression/custom")
def create_custom_progression(body: CustomProgressionIn):
    fields = _validate_custom(body)
    with _custom_lock:
        items = _read_custom()
        if len(items) >= _CUSTOM_MAX:
            raise HTTPException(status_code=400, detail=f"limit of {_CUSTOM_MAX} custom progressions reached")
        item = {"id": f"c_{uuid.uuid4().hex[:10]}", "created_at": int(time.time()), **fields}
        items.append(item)
        _write_custom(items)
    return item


@router.put("/api/progression/custom/{item_id}")
def update_custom_progression(item_id: str, body: CustomProgressionIn):
    fields = _validate_custom(body)
    with _custom_lock:
        items = _read_custom()
        for i, it in enumerate(items):
            if it.get("id") == item_id:
                items[i] = {**it, **fields, "updated_at": int(time.time())}
                _write_custom(items)
                return items[i]
    raise HTTPException(status_code=404, detail="custom progression not found")


@router.delete("/api/progression/custom/{item_id}")
def delete_custom_progression(item_id: str):
    with _custom_lock:
        items = _read_custom()
        kept = [it for it in items if it.get("id") != item_id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail="custom progression not found")
        _write_custom(kept)
    return {"ok": True, "count": len(kept)}
