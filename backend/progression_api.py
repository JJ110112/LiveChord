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


def _load_index():
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
    return _cache["songs"]


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
