"""使用者資料 API — 最愛、最近播放"""

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["user"])

DATA_DIR = Path(__file__).parent.parent / "data"
FAVORITES_FILE = DATA_DIR / "favorites.json"
RECENT_FILE = DATA_DIR / "recent.json"
MAX_RECENT = 50


def _read_json(path: Path, default: dict) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# favorites
# ---------------------------------------------------------------------------

class FavoriteItem(BaseModel):
    path: str


import hashlib
from chord_cache import get_chord_summary as _get_chord_summary

def _song_hash(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]

@router.get("/favorites")
async def get_favorites():
    data = _read_json(FAVORITES_FILE, {"favorites": []})
    for f in data.get("favorites", []):
        f.update(_get_chord_summary(f["path"]))
    return data


@router.post("/favorites")
async def add_favorite(item: FavoriteItem):
    data = _read_json(FAVORITES_FILE, {"favorites": []})
    # 避免重複
    existing = {f["path"] for f in data["favorites"]}
    if item.path in existing:
        return {"ok": True, "message": "已存在"}
    data["favorites"].insert(0, {
        "path": item.path,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _write_json(FAVORITES_FILE, data)
    return {"ok": True}


@router.delete("/favorites")
async def remove_favorite(path: str = Query(...)):
    data = _read_json(FAVORITES_FILE, {"favorites": []})
    data["favorites"] = [f for f in data["favorites"] if f["path"] != path]
    _write_json(FAVORITES_FILE, data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# recent
# ---------------------------------------------------------------------------

@router.get("/recent")
async def get_recent():
    data = _read_json(RECENT_FILE, {"recent": []})
    for r in data.get("recent", []):
        r.update(_get_chord_summary(r["path"]))
    return data


@router.post("/recent")
async def add_recent(item: FavoriteItem):
    data = _read_json(RECENT_FILE, {"recent": []})
    # 移除舊的相同項目
    data["recent"] = [r for r in data["recent"] if r["path"] != item.path]
    # 加到最前面
    data["recent"].insert(0, {
        "path": item.path,
        "played_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    # 限制數量
    data["recent"] = data["recent"][:MAX_RECENT]
    _write_json(RECENT_FILE, data)
    return {"ok": True}
