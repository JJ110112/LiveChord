"""
Jam Tracks API — style-stratified representative song set for playing along.

Serves /api/jam_tracks/* endpoints backed by /data/jam_tracks.json and the
style-stratified groove dictionary at /data/models/groove.json.

Note: uses synchronous `def` (not `async def`) so FastAPI runs handlers in a
thread pool. File I/O in async handlers blocks the event loop.
"""

import json
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api", tags=["jam-tracks"])

DATA_DIR = Path(__file__).parent.parent / "data"
JAM_TRACKS_FILE = DATA_DIR / "jam_tracks.json"

_cache: dict = {"mtime": 0, "data": None}


def _load_jam_tracks() -> dict:
    """Load jam_tracks.json with mtime-aware caching."""
    if not JAM_TRACKS_FILE.is_file():
        raise HTTPException(
            status_code=503,
            detail="jam_tracks.json not built yet; run build_jam_tracks.py",
        )
    mtime = os.path.getmtime(JAM_TRACKS_FILE)
    if _cache["data"] is None or _cache["mtime"] != mtime:
        _cache["data"] = json.loads(JAM_TRACKS_FILE.read_text(encoding="utf-8"))
        _cache["mtime"] = mtime
    return _cache["data"]


@router.get("/jam_tracks/styles")
def jam_tracks_styles():
    """Return the list of available jam track styles with counts."""
    data = _load_jam_tracks()
    by_style = data.get("by_style", {})
    return {
        "total": data.get("total", 0),
        "target": data.get("target", 0),
        "generated_at": data.get("generated_at", ""),
        "styles": [
            {"name": name, "count": n}
            for name, n in sorted(by_style.items(), key=lambda x: -x[1])
        ],
    }


@router.get("/jam_tracks")
def jam_tracks_list(
    style: Optional[str] = Query(default=None, description="Style filter (e.g. Jazz, POP/J-POP)"),
    limit: int = Query(default=50, ge=1, le=600),
    offset: int = Query(default=0, ge=0),
):
    """Paginated jam track list, optionally filtered by style."""
    data = _load_jam_tracks()
    tracks = data.get("tracks", [])
    if style:
        tracks = [t for t in tracks if t.get("style") == style]
    total = len(tracks)
    page = tracks[offset:offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "style": style,
        "tracks": page,
    }


@router.get("/jam_tracks/groove")
def jam_tracks_groove(
    style: Optional[str] = Query(default=None, description="Style bucket for groove lookup"),
    context: str = Query(default="", description="Comma-separated degrees (e.g. I,vi,IV,V)"),
    top_k: int = Query(default=5, ge=1, le=20),
):
    """Style-aware groove pattern suggestion.

    With `style` set, queries the per-style sub-dictionary; otherwise falls
    back to the global groove dictionary. Context is a comma-separated list
    of chord degrees (Roman numerals as produced by pattern_extractor).
    """
    from ai.groove_dict import get_groove_dict

    gd = get_groove_dict()

    if style and style not in gd.available_styles():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown style '{style}'. Available: {gd.available_styles()}",
        )

    ctx: List[str] = [c.strip() for c in context.split(",") if c.strip()] if context else []

    if ctx:
        patterns = gd.suggest_pattern(ctx, top_k=top_k, style=style)
        return {"style": style, "context": ctx, "patterns": patterns}
    else:
        return {
            "style": style,
            "patterns_4": gd.top_patterns(4, top_k, style=style),
            "patterns_8": gd.top_patterns(8, top_k, style=style),
        }
