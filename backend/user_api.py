"""使用者資料 API — 最愛、最近播放"""

import json
import time
from pathlib import Path

from auth_api import get_current_user
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["user"])

DATA_DIR = Path(__file__).parent.parent / "data"
MAX_RECENT = 50

def _get_user_file(username: str, filename: str) -> Path:
    p = DATA_DIR / "users" / username / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _read_json(path: Path, default: dict) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except:
            return default
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


from chord_cache import get_chord_summary as _get_chord_summary

@router.get("/favorites")
async def get_favorites(username: str = Depends(get_current_user)):
    fav_file = _get_user_file(username, "favorites.json")
    data = _read_json(fav_file, {"favorites": []})
    for f in data.get("favorites", []):
        f.update(_get_chord_summary(f["path"]))
    return data


@router.post("/favorites")
async def add_favorite(item: FavoriteItem, username: str = Depends(get_current_user)):
    fav_file = _get_user_file(username, "favorites.json")
    data = _read_json(fav_file, {"favorites": []})
    existing = {f["path"] for f in data["favorites"]}
    if item.path in existing:
        return {"ok": True, "message": "已存在"}
    data["favorites"].insert(0, {
        "path": item.path,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _write_json(fav_file, data)
    return {"ok": True}


@router.delete("/favorites")
async def remove_favorite(path: str = Query(...), username: str = Depends(get_current_user)):
    fav_file = _get_user_file(username, "favorites.json")
    data = _read_json(fav_file, {"favorites": []})
    data["favorites"] = [f for f in data["favorites"] if f["path"] != path]
    _write_json(fav_file, data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# recent
# ---------------------------------------------------------------------------

@router.get("/recent")
async def get_recent(username: str = Depends(get_current_user)):
    recent_file = _get_user_file(username, "recent.json")
    data = _read_json(recent_file, {"recent": []})
    for r in data.get("recent", []):
        r.update(_get_chord_summary(r["path"]))
    return data


@router.post("/recent")
async def add_recent(item: FavoriteItem, username: str = Depends(get_current_user)):
    recent_file = _get_user_file(username, "recent.json")
    data = _read_json(recent_file, {"recent": []})
    data["recent"] = [r for r in data["recent"] if r["path"] != item.path]
    data["recent"].insert(0, {
        "path": item.path,
        "played_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    data["recent"] = data["recent"][:MAX_RECENT]
    _write_json(recent_file, data)
    return {"ok": True}

from fastapi.responses import FileResponse
import shutil
import tempfile

@router.get("/export-data")
async def export_data(username: str = Depends(get_current_user)):
    user_dir = DATA_DIR / "users" / username
    if not user_dir.exists():
        raise HTTPException(status_code=404, detail="無使用者資料")
        
    temp_dir = Path(tempfile.mkdtemp())
    zip_path = temp_dir / f"{username}_livechord_backup"
    
    shutil.make_archive(str(zip_path), 'zip', str(user_dir))
    return FileResponse(f"{zip_path}.zip", media_type="application/zip", filename=f"{username}_livechord_backup.zip")
