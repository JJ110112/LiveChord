"""
和弦摘要快取模組
負責計算並快取歌曲的和弦摘要 (unique_chords, chord_key, chord_list)
大幅減少多次從硬碟讀取與解析大型 JSON 的 I/O 延遲。
"""

import json
import os
import hashlib
from pathlib import Path

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
INDEX_FILE = DATA_DIR / "chord_index.json"

_chord_index_cache = None

def song_hash(path: str) -> str:
    """產生穩定的 song hash（統一將反斜線轉為正斜線，避免 Windows 路徑不一致）"""
    path = path.replace("\\", "/")
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]

def _load_chord_index():
    """載入或初始化全域快取"""
    global _chord_index_cache
    if _chord_index_cache is None:
        if INDEX_FILE.is_file():
            try:
                _chord_index_cache = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            except Exception:
                _chord_index_cache = {}
        else:
            _chord_index_cache = {}

def _save_chord_index():
    """將快取寫回硬碟"""
    if _chord_index_cache is not None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_FILE.write_text(json.dumps(_chord_index_cache, ensure_ascii=False), encoding="utf-8")

def get_chord_summary(path: str) -> dict:
    """取得和弦摘要：unique count, key, chord list。內建 mtime 驗證的快取機制"""
    _load_chord_index()
    h = song_hash(path)
    chords_file = CHORDS_DIR / f"{h}.json"

    if not chords_file.is_file():
        return {"unique_chords": 0, "chord_key": "", "chord_list": []}

    try:
        mtime = os.path.getmtime(chords_file)
    except OSError:
        mtime = 0

    # Check cache match
    cached = _chord_index_cache.get(h)
    if cached and cached.get("mtime") == mtime:
        return {
            "unique_chords": cached.get("unique_chords", 0),
            "chord_key": cached.get("chord_key", ""),
            "chord_list": cached.get("chord_list", []),
        }

    # Cache miss or expired: Recompute from file
    try:
        cdata = json.loads(chords_file.read_text(encoding="utf-8"))
        unique = sorted(set(c["chord"] for c in cdata.get("chords", []) if c.get("chord") and c["chord"] != "N"))

        summary = {
            "unique_chords": len(unique),
            "chord_key": cdata.get("key", ""),
            "chord_list": unique,
            "mtime": mtime,
        }

        # Update cache & persistence
        _chord_index_cache[h] = summary
        _save_chord_index()
        
        return {
            "unique_chords": summary["unique_chords"],
            "chord_key": summary["chord_key"],
            "chord_list": summary["chord_list"]
        }
    except Exception:
        pass
        
    return {"unique_chords": 0, "chord_key": "", "chord_list": []}
