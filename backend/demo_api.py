"""Demo songs API — serves the manifest of pre-analyzed royalty-free tracks
shipped with the repo so first-time visitors can hit Play without uploading.

Audio + covers live under ``data/demo/`` and are served via a static mount
in main.py (``/static/demo/...``). Chord JSONs live at
``data/demo/chords/<hash>.json`` and are served via the existing
``/api/chords/by-hash`` endpoint — chord_cache.chord_file_for() transparently
falls back to the demo dir when the sharded path is missing.

Unauthenticated by design: anonymous visitors are the primary audience.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data"
DEMO_DIR = DATA_DIR / "demo"
MANIFEST_FILE = DEMO_DIR / "manifest.json"


@router.get("/api/demo/list")
def list_demos():
    if not MANIFEST_FILE.is_file():
        return []
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"demo manifest unreadable: {e}")
