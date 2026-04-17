"""共享記憶體快取：library_cache.json 與 CHORDS_DIR 檔案集合。

目的：避免 admin 頁面與 auto_worker 在每個 endpoint 重複從 SMB
讀取 50MB JSON 與對 45k+ 檔案做 listdir，這是冷啟動 ~10 秒的主因。

策略：
- library_cache：mtime 為快取金鑰，scan 寫入新檔自動失效
- chord_hash_set：30 秒 TTL，避免每次 admin 輪詢都打 SMB；
  關鍵事件（批次偵測完成）可呼叫 invalidate 主動失效
"""

import os
import time
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "library_cache.json"
CHORDS_DIR = DATA_DIR / "chords"

_lib_cache = {"data": None, "mtime": 0.0}
_chord_hash_cache = {"data": None, "ts": 0.0}
_CHORD_HASH_TTL = 30.0  # 秒


def get_library_cache(force: bool = False) -> dict:
    """回傳 library_cache.json 內容（mtime 失效）。"""
    if not CACHE_FILE.is_file():
        return {}
    try:
        mtime = CACHE_FILE.stat().st_mtime
    except OSError:
        return {}
    if not force and _lib_cache["data"] is not None and _lib_cache["mtime"] == mtime:
        return _lib_cache["data"]
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    _lib_cache["data"] = data
    _lib_cache["mtime"] = mtime
    return data


def get_library_tracks() -> list:
    """方便函式：直接回傳 tracks 清單。"""
    return get_library_cache().get("tracks", [])


def invalidate_library_cache():
    """強制下一次 get_library_cache 重讀。"""
    _lib_cache["data"] = None
    _lib_cache["mtime"] = 0.0


def get_chord_hash_set(force: bool = False) -> set:
    """CHORDS_DIR 下所有 .json hash 集合（30s TTL）。"""
    now = time.time()
    if not force and _chord_hash_cache["data"] is not None and (now - _chord_hash_cache["ts"]) < _CHORD_HASH_TTL:
        return _chord_hash_cache["data"]
    if not CHORDS_DIR.is_dir():
        result = set()
    else:
        try:
            result = {n[:-5] for n in os.listdir(CHORDS_DIR) if n.endswith(".json")}
        except OSError:
            result = set()
    _chord_hash_cache["data"] = result
    _chord_hash_cache["ts"] = now
    return result


def invalidate_chord_hash_set():
    """強制下一次 get_chord_hash_set 重掃。
    順手清掉 chord_batch 與 library_groups 的快取，讓 admin 統計立即反映變化。"""
    _chord_hash_cache["data"] = None
    _chord_hash_cache["ts"] = 0.0
    try:
        from chord_batch import _stats_cache
        _stats_cache["data"] = None
        _stats_cache["ts"] = 0
    except Exception:
        pass
    try:
        from library_groups import invalidate_groups_cache
        invalidate_groups_cache()
    except Exception:
        pass
