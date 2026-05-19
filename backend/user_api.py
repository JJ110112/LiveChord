"""使用者資料 API — 最愛、最近播放"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

from auth_api import get_current_user
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["user"])
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MAX_RECENT = 50
# Log when a list-fetch takes >500ms so we can spot contention regressions.
_SLOW_THRESHOLD_S = 0.5

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


class RecentItem(BaseModel):
    path: str
    # 當 path 以 "__hash/" 開頭（處理過的 YouTube / 上傳結果）時額外記錄的 metadata，
    # 讓首頁最近播放能直接顯示標題與封面而不必再拉一次 /api/chords/by-hash
    title: Optional[str] = ""
    cover_url: Optional[str] = ""
    youtube_url: Optional[str] = ""


from chord_cache import get_chord_summary as _get_chord_summary, chord_file_for

@router.get("/favorites")
async def get_favorites(username: str = Depends(get_current_user)):
    t0 = time.time()
    fav_file = _get_user_file(username, "favorites.json")
    data = _read_json(fav_file, {"favorites": []})
    # Import here to avoid top-of-module circular imports with process_queue.
    from process_queue import CHORDS_DIR as _CHORDS_DIR
    cleaned = []
    changed = False
    for f in data.get("favorites", []):
        p = str(f.get("path", ""))
        if p.startswith("__hash/"):
            # Self-heal: drop favorites whose processed-result chord file was purged.
            h = p[7:]
            if not chord_file_for(h).is_file():
                changed = True
                continue
        else:
            f.update(_get_chord_summary(p))
        cleaned.append(f)
    if changed:
        data["favorites"] = cleaned
        _write_json(fav_file, data)
    dt = time.time() - t0
    if dt > _SLOW_THRESHOLD_S:
        logger.warning(f"/api/favorites slow: {dt:.2f}s user={username} n={len(cleaned)}")
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
    t0 = time.time()
    recent_file = _get_user_file(username, "recent.json")
    data = _read_json(recent_file, {"recent": []})
    # Import here to avoid top-of-module circular imports with process_queue.
    from process_queue import CHORDS_DIR as _CHORDS_DIR
    cleaned = []
    changed = False
    for r in data.get("recent", []):
        p = str(r.get("path", ""))
        if p.startswith("__hash/"):
            # Self-heal: drop hash entries whose chord data was deleted by an admin
            # purge or failed write. Without this, the home page would render cards
            # that navigate to a 404 player.
            h = p[7:]
            if not chord_file_for(h).is_file():
                changed = True
                continue
        else:
            r.update(_get_chord_summary(p))
        cleaned.append(r)
    if changed:
        data["recent"] = cleaned
        _write_json(recent_file, data)
    dt = time.time() - t0
    if dt > _SLOW_THRESHOLD_S:
        logger.warning(f"/api/recent slow: {dt:.2f}s user={username} n={len(cleaned)}")
    return data


@router.post("/recent")
async def add_recent(item: RecentItem, username: str = Depends(get_current_user)):
    recent_file = _get_user_file(username, "recent.json")
    data = _read_json(recent_file, {"recent": []})
    data["recent"] = [r for r in data["recent"] if r.get("path") != item.path]
    entry: dict = {
        "path": item.path,
        "played_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # 只在是 hash 項目時保留 metadata（path-mode 的資訊仍從 library_cache 拉）
    if item.path.startswith("__hash/"):
        if item.title: entry["title"] = item.title
        if item.cover_url: entry["cover_url"] = item.cover_url
        if item.youtube_url: entry["youtube_url"] = item.youtube_url
    data["recent"].insert(0, entry)
    data["recent"] = data["recent"][:MAX_RECENT]
    _write_json(recent_file, data)
    return {"ok": True}


@router.delete("/recent")
def remove_recent(path: str = Query(...), username: str = Depends(get_current_user)):
    recent_file = _get_user_file(username, "recent.json")
    data = _read_json(recent_file, {"recent": []})
    before_recent = len(data.get("recent", []))
    data["recent"] = [r for r in data.get("recent", []) if r.get("path") != path]
    _write_json(recent_file, data)

    fav_file = _get_user_file(username, "favorites.json")
    fav_data = _read_json(fav_file, {"favorites": []})
    before_favorites = len(fav_data.get("favorites", []))
    fav_data["favorites"] = [
        f for f in fav_data.get("favorites", []) if f.get("path") != path
    ]
    _write_json(fav_file, fav_data)

    return {
        "ok": True,
        "removed_recent": before_recent - len(data["recent"]),
        "removed_favorites": before_favorites - len(fav_data["favorites"]),
    }

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
