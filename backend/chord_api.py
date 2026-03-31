"""和弦 API — 和弦資訊、和弦圖、和弦譜 CRUD、自動偵測、批次偵測"""

import json
import hashlib
import os
import time
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from chord_table import get_chord_info, get_chord_jianpu
from chord_diagrams import get_chord_diagram

from config import get_music_root

router = APIRouter(prefix="/api", tags=["chord"])

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
CACHE_FILE = DATA_DIR / "library_cache.json"

# 批次偵測狀態
_batch_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_track": "",
    "succeeded": 0,
    "failed": 0,
    "skipped": 0,
    "errors": [],
    "started_at": "",
    "finished_at": "",
}


def _song_hash(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# chord info / diagram
# ---------------------------------------------------------------------------

@router.get("/chord/info/{name:path}")
async def chord_info(name: str):
    """取得和弦資訊（組成音、簡譜）"""
    info = get_chord_info(name)
    if not info or not info.get("notes"):
        raise HTTPException(status_code=404, detail=f"未知和弦: {name}")
    return info


@router.get("/chord/diagram/{instrument}/{name:path}")
async def chord_diagram(instrument: str, name: str):
    """取得和弦圖（吉他/烏克麗麗）"""
    if instrument not in ("guitar", "ukulele"):
        raise HTTPException(status_code=400, detail="instrument 須為 guitar 或 ukulele")
    diagram = get_chord_diagram(name, instrument=instrument)
    if not diagram:
        raise HTTPException(status_code=404, detail=f"無 {instrument} 和弦圖: {name}")
    return diagram


# ---------------------------------------------------------------------------
# chord sheet CRUD
# ---------------------------------------------------------------------------

class ChordSheet(BaseModel):
    path: str
    key: str = ""
    capo: int = 0
    chords: list = []


@router.get("/chords")
async def get_chords(path: str = Query(...)):
    """取得某首歌的和弦譜"""
    chords_file = CHORDS_DIR / f"{_song_hash(path)}.json"
    if not chords_file.is_file():
        return {"path": path, "key": "", "capo": 0, "chords": [], "exists": False}

    data = json.loads(chords_file.read_text(encoding="utf-8"))
    data["exists"] = True
    return data


@router.post("/chords")
async def save_chords(sheet: ChordSheet):
    """儲存和弦譜"""
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{_song_hash(sheet.path)}.json"
    chords_file.write_text(
        json.dumps(sheet.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"ok": True, "path": sheet.path}


# ---------------------------------------------------------------------------
# auto-detect (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/chords/detect")
async def detect_chords_api(path: str = Query(...)):
    """自動偵測音訊中的和弦，偵測完成後自動儲存"""
    root = get_music_root()
    full = os.path.normpath(os.path.join(root, path))
    if not full.lower().startswith(root.lower()):
        raise HTTPException(status_code=403, detail="路徑不允許")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="檔案不存在")

    # 如果已有 chordify 來源的和弦，不要用 BTC 覆蓋
    chords_file = CHORDS_DIR / f"{_song_hash(path)}.json"
    if chords_file.is_file():
        existing = json.loads(chords_file.read_text(encoding="utf-8"))
        if existing.get("source") == "chordify":
            return {
                "ok": True, "path": path,
                "key": existing.get("key", ""),
                "chord_count": len(existing.get("chords", [])),
                "chords": existing.get("chords", []),
                "source": "chordify (已有高品質和弦，跳過 BTC 偵測)",
            }

    try:
        from chord_detect import detect_chords, detect_key
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"缺少依賴: {e}")

    try:
        key = detect_key(full)
        chords = detect_chords(full)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"偵測失敗: {e}")

    # 自動儲存
    sheet = {
        "path": path,
        "key": key,
        "capo": 0,
        "chords": chords,
    }
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{_song_hash(path)}.json"
    chords_file.write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "path": path,
        "key": key,
        "chord_count": len(chords),
        "chords": chords,
    }


# ---------------------------------------------------------------------------
# MIDI 匯入
# ---------------------------------------------------------------------------

@router.get("/chords/midi-search")
async def midi_search(path: str = Query(...)):
    """搜尋 X:\\ 中與此曲目名稱相符的 MIDI 檔案"""
    from config import get_midi_root
    midi_root = get_midi_root()

    # 從音樂路徑提取歌名
    song_name = os.path.splitext(os.path.basename(path))[0].lower()

    results = []
    if os.path.isdir(midi_root):
        for dirpath, _, filenames in os.walk(midi_root):
            for fname in filenames:
                if not fname.lower().endswith(('.mid', '.midi')):
                    continue
                if song_name in fname.lower() or fname.lower().replace('.mid', '').replace('.midi', '') in song_name:
                    rel = os.path.relpath(os.path.join(dirpath, fname), midi_root).replace("\\", "/")
                    results.append({"name": fname, "path": rel})

    return {"song": song_name, "midi_root": midi_root, "results": results}


@router.post("/chords/midi-import")
async def midi_import(path: str = Query(...), midi_path: str = Query(...)):
    """從 MIDI 檔案匯入和弦，儲存到 chords/{hash}.json"""
    from config import get_midi_root
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
    from midi_to_lab import midi_to_lab

    midi_root = get_midi_root()
    full_midi = os.path.normpath(os.path.join(midi_root, midi_path))

    if not os.path.isfile(full_midi):
        raise HTTPException(status_code=404, detail=f"MIDI 檔案不存在: {midi_path}")

    try:
        entries = midi_to_lab(full_midi, verbose=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MIDI 解析失敗: {e}")

    if not entries:
        raise HTTPException(status_code=400, detail="MIDI 無法解析出和弦")

    # 推導 key
    from collections import Counter
    roots = []
    for e in entries:
        c = e["chord"]
        if c and c[0] in "ABCDEFG":
            root = c[0]
            if len(c) > 1 and c[1] in '#b':
                root += c[1]
            roots.append(root)
    key = Counter(roots).most_common(1)[0][0] if roots else ""

    # 儲存
    sheet = {
        "path": path,
        "key": key,
        "capo": 0,
        "source": "midi",
        "chords": entries,
    }
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    chords_file = CHORDS_DIR / f"{_song_hash(path)}.json"
    chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True, "path": path, "key": key,
        "chord_count": len(entries),
        "source": "midi",
        "midi_file": midi_path,
    }


# ---------------------------------------------------------------------------
# 批次和弦偵測（背景執行緒）
# ---------------------------------------------------------------------------

def _batch_detect_worker(tracks: list, skip_existing: bool):
    """背景批次偵測所有曲目的和弦"""
    global _batch_state
    from chord_detect import detect_chords, detect_key

    _batch_state.update({
        "running": True, "total": len(tracks), "done": 0,
        "current_track": "", "succeeded": 0, "failed": 0,
        "skipped": 0, "errors": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": "",
    })

    CHORDS_DIR.mkdir(parents=True, exist_ok=True)

    for i, track_path in enumerate(tracks):
        _batch_state["done"] = i
        _batch_state["current_track"] = track_path

        # 檢查是否已有和弦譜
        chords_file = CHORDS_DIR / f"{_song_hash(track_path)}.json"
        if skip_existing and chords_file.is_file():
            _batch_state["skipped"] += 1
            continue

        full = os.path.normpath(os.path.join(get_music_root(), track_path))
        if not os.path.isfile(full):
            _batch_state["skipped"] += 1
            continue

        try:
            key = detect_key(full)
            chords = detect_chords(full)
            sheet = {"path": track_path, "key": key, "capo": 0, "chords": chords}
            chords_file.write_text(
                json.dumps(sheet, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _batch_state["succeeded"] += 1
        except Exception as e:
            _batch_state["failed"] += 1
            _batch_state["errors"].append({"path": track_path, "error": str(e)})
            # 只保留最後 20 筆錯誤
            if len(_batch_state["errors"]) > 20:
                _batch_state["errors"] = _batch_state["errors"][-20:]

    _batch_state["done"] = len(tracks)
    _batch_state["current_track"] = ""
    _batch_state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _batch_state["running"] = False


@router.post("/chords/batch-detect")
async def batch_detect(
    skip_existing: bool = Query(default=True),
    genre: str = Query(default=""),
):
    """啟動批次和弦偵測（背景非同步）"""
    if _batch_state["running"]:
        return {"ok": False, "message": "批次偵測進行中", "done": _batch_state["done"], "total": _batch_state["total"]}

    # 從 library_cache 取得曲目列表
    if not CACHE_FILE.is_file():
        raise HTTPException(status_code=400, detail="請先執行音樂庫掃描")

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    tracks = [t["path"] for t in cache.get("tracks", [])]

    # 依 genre 過濾
    if genre:
        tracks = [p for p in tracks if p.lower().startswith(genre.lower())]

    if not tracks:
        return {"ok": False, "message": "找不到曲目"}

    t = threading.Thread(target=_batch_detect_worker, args=(tracks, skip_existing), daemon=True)
    t.start()

    return {"ok": True, "message": f"批次偵測已啟動（{len(tracks)} 首）", "total": len(tracks)}


@router.get("/chords/batch-detect/status")
async def batch_detect_status():
    """查詢批次偵測進度"""
    return {
        "running": _batch_state["running"],
        "total": _batch_state["total"],
        "done": _batch_state["done"],
        "current_track": _batch_state["current_track"],
        "succeeded": _batch_state["succeeded"],
        "failed": _batch_state["failed"],
        "skipped": _batch_state["skipped"],
        "errors": _batch_state["errors"][-5:],
        "started_at": _batch_state["started_at"],
        "finished_at": _batch_state["finished_at"],
    }


@router.get("/chords/stats")
async def chords_stats():
    """和弦譜統計"""
    total_chords = 0
    if CHORDS_DIR.is_dir():
        total_chords = len(list(CHORDS_DIR.glob("*.json")))

    total_tracks = 0
    if CACHE_FILE.is_file():
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        total_tracks = cache.get("total_tracks", 0)

    return {
        "total_tracks": total_tracks,
        "tracks_with_chords": total_chords,
        "coverage": round(total_chords / total_tracks * 100, 1) if total_tracks else 0,
        "batch_running": _batch_state["running"],
    }
