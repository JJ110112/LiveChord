"""曲目群組抽象 — 將 library_cache 依「root 第一層資料夾」分組

支援兩種音樂庫結構：
- 扁平庫（如 Y:\）：根目錄下的檔案歸為「未分類」，子資料夾名稱即群組
- 階層庫（如 Z:\，路徑前綴 @N/）：取 @N/ 之後的第一層資料夾名

群組統計從現有 library_cache.json 與 chord_cache 計算，不重掃磁碟。
"""

import json
import os
from pathlib import Path

from chord_cache import song_hash
from config import get_music_roots

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "library_cache.json"
CHORDS_DIR = DATA_DIR / "chords"

UNCATEGORIZED = "未分類"


def _split_group(rel_path: str) -> tuple[int, str, str]:
    """從相對路徑解析出 (root_idx, label, path_prefix)

    例：
      "POP/song.flac"          -> (0, "POP", "POP/")
      "song.flac"              -> (0, "未分類", "")  # 根目錄檔案
      "@1/Jazz/Miles/x.flac"   -> (1, "Jazz", "@1/Jazz/")
      "@1/x.flac"              -> (1, "未分類", "@1/")
    """
    p = rel_path.replace("\\", "/")
    root_idx = 0
    prefix = ""
    if p.startswith("@"):
        slash = p.find("/")
        if slash > 0:
            try:
                root_idx = int(p[1:slash])
            except ValueError:
                root_idx = 0
            prefix = p[: slash + 1]
            p = p[slash + 1 :]
    parts = p.split("/", 1)
    if len(parts) < 2:
        return root_idx, UNCATEGORIZED, prefix
    label = parts[0] or UNCATEGORIZED
    return root_idx, label, prefix + label + "/"


def _root_label(root_idx: int) -> str:
    roots = get_music_roots()
    if 0 <= root_idx < len(roots):
        return roots[root_idx].rstrip("/\\") or f"@{root_idx}"
    return f"@{root_idx}"


def _chord_hash_set() -> set:
    if not CHORDS_DIR.is_dir():
        return set()
    try:
        return {n[:-5] for n in os.listdir(CHORDS_DIR) if n.endswith(".json")}
    except OSError:
        return set()


def list_groups() -> list[dict]:
    """從 library_cache 計算所有群組與覆蓋率"""
    if not CACHE_FILE.is_file():
        return []

    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []

    chord_hashes = _chord_hash_set()
    groups: dict[tuple[int, str], dict] = {}

    for t in cache.get("tracks", []):
        path = t.get("path", "")
        if not path:
            continue
        root_idx, label, prefix = _split_group(path)
        key = (root_idx, label)
        g = groups.get(key)
        if g is None:
            g = {
                "group_id": f"@{root_idx}/{label}",
                "root_idx": root_idx,
                "root_label": _root_label(root_idx),
                "label": label,
                "path_prefix": prefix,
                "track_count": 0,
                "chords_done": 0,
            }
            groups[key] = g
        g["track_count"] += 1
        if song_hash(path) in chord_hashes:
            g["chords_done"] += 1

    result = list(groups.values())
    for g in result:
        g["chords_pending"] = g["track_count"] - g["chords_done"]
        g["coverage"] = round(g["chords_done"] / g["track_count"] * 100, 1) if g["track_count"] else 0
    result.sort(key=lambda x: (x["root_idx"], x["label"]))
    return result


def filter_tracks_by_group(tracks: list, group_id: str) -> list:
    """從一個 tracks 陣列中過濾屬於指定 group 的曲目

    group_id 格式：@<root_idx>/<label>
    """
    if not group_id:
        return tracks
    if not group_id.startswith("@") or "/" not in group_id:
        return []
    try:
        root_part, label = group_id[1:].split("/", 1)
        target_root = int(root_part)
    except (ValueError, IndexError):
        return []

    matched = []
    for t in tracks:
        path = t.get("path", "") if isinstance(t, dict) else t
        if not path:
            continue
        root_idx, t_label, _ = _split_group(path)
        if root_idx == target_root and t_label == label:
            matched.append(t)
    return matched
