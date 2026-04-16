"""
Style classifier for LiveChord library.

Maps `@1/STYLE/...` storage paths (Z:\ drive) to canonical style labels,
producing /data/style_map.json for use by groove_dict and jam_tracks.

Y:\ drive tracks (no `@1/` prefix) are excluded — they are ~192 flat-layout
Pop songs without a style folder hierarchy.

Path normalization: chord/melody JSON files hash paths WITHOUT the `@1/` drive
prefix (for example `POP/J-POP/.../song.flac`), while library_cache.json stores
them WITH the prefix (`@1/POP/J-POP/.../song.flac`). To keep this module's
output hashes aligned with chord/melody filenames, we strip the `@1/` prefix
before hashing.
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime
from collections import Counter

DATA_DIR = Path(__file__).parent.parent.parent / "data"
LIBRARY_CACHE = DATA_DIR / "library_cache.json"
STYLE_MAP_PATH = DATA_DIR / "style_map.json"

POP_SUB_THRESHOLD = 100

# Only these top-level folders have chord/melody extraction data.
# Classics / Relax / Sleep / Other / Electronic Dance Music were deliberately
# excluded from batch extraction to keep POP/Jazz chord detection accurate.
# Adding a folder here requires running batch extractors on it first.
ALLOWED_TOP_LEVELS = {"POP", "Jazz", "Christmas", "Jam"}

# Jam sub-classification: Jam is not organized into sub-folders like POP is,
# so we derive sub-genres from keywords in the album/artist segment
# (the second path segment after `@1/Jam/`). Order matters — first match wins,
# so list the more specific genres first.
JAM_SUB_KEYWORDS = [
    ("Jam/Lo-Fi", ["lo-fi", "lofi", "lo fi", "chillhop"]),
    ("Jam/Funk",  ["funk"]),
    ("Jam/Blues", ["blues"]),
    ("Jam/Jazz",  ["jazz", "swing", "bebop"]),
    ("Jam/Rock",  ["rock", "metal", "hard rock", "prog"]),
    ("Jam/Country", ["country", "bluegrass"]),
    ("Jam/Reggae",  ["reggae", "ska"]),
]


def _normalize_for_hash(path: str) -> str:
    """Strip `@1/` drive prefix so the hash matches chord/melody filename hashes."""
    if path.startswith("@1/"):
        return path[3:]
    return path


def _song_hash(path: str) -> str:
    norm = _normalize_for_hash(path).replace("\\", "/")
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:12]


def _classify_jam_sub(album_segment: str) -> str:
    """Match Jam album name against JAM_SUB_KEYWORDS; returns sub-label or 'Jam/Other'."""
    lowered = album_segment.lower()
    for label, keywords in JAM_SUB_KEYWORDS:
        for kw in keywords:
            if kw in lowered:
                return label
    return "Jam/Other"


def path_to_style(path: str, pop_subs: set | None = None) -> str | None:
    """Classify a library path into a canonical style label.

    Returns None for paths that are:
    - not on the Z:\\ drive (missing `@1/` prefix),
    - or on a top-level folder outside ALLOWED_TOP_LEVELS.

    POP: sub-folder expansion via `pop_subs` (folder-based)
    Jam: sub-genre derived from keywords in the album/artist segment
    Others: use the top folder name verbatim
    """
    if not path.startswith("@1/"):
        return None
    parts = path[3:].split("/")
    if not parts or not parts[0]:
        return None
    top = parts[0]
    if top not in ALLOWED_TOP_LEVELS:
        return None
    if top == "POP" and len(parts) >= 2 and pop_subs and parts[1] in pop_subs:
        return f"POP/{parts[1]}"
    if top == "Jam" and len(parts) >= 2:
        return _classify_jam_sub(parts[1])
    return top


def _collect_pop_subs(tracks: list, threshold: int) -> set:
    counts = Counter()
    for t in tracks:
        p = t.get("path", "")
        if not p.startswith("@1/POP/"):
            continue
        parts = p[3:].split("/")
        if len(parts) >= 2:
            counts[parts[1]] += 1
    return {sub for sub, n in counts.items() if n >= threshold}


def build_style_map(
    library_cache_path: Path = LIBRARY_CACHE,
    output_path: Path = STYLE_MAP_PATH,
    pop_threshold: int = POP_SUB_THRESHOLD,
) -> dict:
    with open(library_cache_path, "r", encoding="utf-8") as f:
        library = json.load(f)
    tracks = library.get("tracks", [])

    pop_subs = _collect_pop_subs(tracks, pop_threshold)

    style_tracks: dict = {}
    style_counts = Counter()
    excluded = {
        "non_z_drive": 0,
        "not_in_allowlist": 0,
        "duplicate_hash": 0,
    }

    for t in tracks:
        p = t.get("path", "")
        style = path_to_style(p, pop_subs=pop_subs)
        if style is None:
            if not p.startswith("@1/"):
                excluded["non_z_drive"] += 1
            else:
                excluded["not_in_allowlist"] += 1
            continue
        h = _song_hash(p)
        if h in style_tracks:
            excluded["duplicate_hash"] += 1
            continue
        style_tracks[h] = style
        style_counts[style] += 1

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "allowed_top_levels": sorted(ALLOWED_TOP_LEVELS),
        "pop_expansion_threshold": pop_threshold,
        "expanded_pop_subs": sorted(pop_subs),
        "styles": dict(sorted(style_counts.items(), key=lambda x: -x[1])),
        "excluded": excluded,
        "tracks": style_tracks,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    result = build_style_map()
    print(f"Generated: {result['generated_at']}")
    print(f"Excluded: {result['excluded']}")
    print(f"POP sub-folders expanded (>= {result['pop_expansion_threshold']}): "
          f"{result['expanded_pop_subs']}")
    print()
    print("Style distribution:")
    for style, n in result["styles"].items():
        print(f"  {style:40s} {n:7d}")
    print()
    print(f"Total classified: {sum(result['styles'].values())}")
    print(f"Output: {STYLE_MAP_PATH}")
