"""
Build /data/jam_tracks.json — the ~3000-song style-stratified representative set.

Iterates all chord JSON files, filters to songs with matching melody data,
computes a per-song quality score, and selects a balanced sample across styles
using sqrt-proportional quotas.
"""

import os
import sys
import json
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import math

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
MELODIES_DIR = DATA_DIR / "melodies"
LIBRARY_CACHE = DATA_DIR / "library_cache.json"
STYLE_MAP = DATA_DIR / "style_map.json"
OUTPUT = DATA_DIR / "jam_tracks.json"

TOTAL_TARGET = 3000
MIN_PER_STYLE = 50
MAX_PER_STYLE = 550

SOURCE_PRIORITY = {
    "chordify": 1.00,
    "midi": 0.90,
    "btc": 0.55,
    "btc_batch": 0.55,
}

# Duration sweet spot: 90–360 seconds
DURATION_MIN = 90
DURATION_MAX = 360


def load_library_metadata() -> dict:
    """Return {song_hash: library_metadata_dict}.

    Hashes use the chord/melody filename convention: strip `@1/` prefix before
    hashing so lookups align with chord data filenames.
    """
    import hashlib

    def _hash(path: str) -> str:
        # Strip drive prefix to match chord filename hashing
        if path.startswith("@1/"):
            path = path[3:]
        return hashlib.md5(path.replace("\\", "/").encode("utf-8")).hexdigest()[:12]

    with open(LIBRARY_CACHE, "r", encoding="utf-8") as f:
        lib = json.load(f)
    out = {}
    for t in lib.get("tracks", []):
        p = t.get("path", "")
        if not p:
            continue
        out[_hash(p)] = {
            "path": p,
            "title": t.get("title", ""),
            "artist": t.get("artist", ""),
            "album": t.get("album", ""),
            "duration": t.get("duration", 0),
        }
    return out


def duration_score(duration: float) -> float:
    if duration <= 0:
        return 0.3
    if DURATION_MIN <= duration <= DURATION_MAX:
        return 1.0
    if duration < DURATION_MIN:
        return max(0.2, duration / DURATION_MIN)
    # Too long (instrumental ambient): soft penalty
    overshoot = duration - DURATION_MAX
    return max(0.4, 1.0 - overshoot / 600)


def quality_score(
    chord_count: int,
    unique_chords: int,
    source: str,
    duration: float,
) -> float:
    # Normalized chord count: 10-200 chords is healthy
    cc_norm = min(1.0, chord_count / 80.0)
    # Unique chord diversity: 5-15 is sweet spot
    uc_norm = min(1.0, unique_chords / 10.0)
    source_w = SOURCE_PRIORITY.get(source, 0.5)
    dur_w = duration_score(duration)

    return round(
        0.40 * cc_norm + 0.25 * source_w + 0.20 * dur_w + 0.15 * uc_norm,
        4,
    )


def _normalized_hash(path: str) -> str:
    """Hash with `@1/` prefix stripped — matches style_classifier convention."""
    import hashlib
    if path.startswith("@1/"):
        path = path[3:]
    return hashlib.md5(path.replace("\\", "/").encode("utf-8")).hexdigest()[:12]


def load_melody_hash_set() -> set:
    """Scan melody JSON files, read each path field, return set of normalized hashes.

    Melody filenames suffer from the same dual-convention problem as chord
    filenames (some written with `@1/` prefix, some without), so we can't
    rely on filename stems. Read the path field inside each file instead.
    """
    available = set()
    for f in MELODIES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            p = data.get("path", "")
            if p:
                available.add(_normalized_hash(p))
        except Exception:
            continue
    return available


def scan_candidates(library_meta: dict, style_map: dict, melody_hashes: set) -> list:
    """Iterate chord JSONs, compute scores, return list of candidates.

    Note on hashing: historical chord files used inconsistent conventions —
    some have `@1/` prefix in their stored path, others don't — so the
    filename stem can't be trusted. We read the `path` field from each JSON,
    normalize it (strip `@1/`), and recompute the hash. The normalized hash
    aligns with style_map and library_meta (both also normalized).
    """
    candidates = []
    scanned = 0
    skipped_no_style = 0
    skipped_no_melody = 0
    skipped_no_meta = 0
    skipped_empty = 0
    t0 = time.time()

    from chord_cache import iter_chord_files
    for f in iter_chord_files(CHORDS_DIR):
        scanned += 1

        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        path_in_file = data.get("path", "")
        if not path_in_file:
            skipped_empty += 1
            continue

        h = _normalized_hash(path_in_file)

        style = style_map.get(h)
        if not style:
            skipped_no_style += 1
            continue
        if h not in melody_hashes:
            skipped_no_melody += 1
            continue
        meta = library_meta.get(h)
        if not meta:
            skipped_no_meta += 1
            continue

        chords = data.get("chords", [])
        if len(chords) < 5:
            continue
        source = data.get("source", "btc")
        unique = len({c.get("chord", "N") for c in chords if c.get("chord") and c["chord"] != "N"})
        chord_count = len(chords)
        duration = meta.get("duration", 0)

        score = quality_score(chord_count, unique, source, duration)

        candidates.append({
            "hash": h,
            "path": meta["path"],
            "title": meta.get("title", ""),
            "artist": meta.get("artist", ""),
            "style": style,
            "source": source,
            "chord_count": chord_count,
            "unique_chords": unique,
            "duration": round(duration, 1),
            "has_melody": True,
            "score": score,
        })

        if scanned % 5000 == 0:
            print(f"  scanned {scanned}  candidates {len(candidates)}  elapsed {time.time()-t0:.0f}s")

    print(f"\nScan complete: {scanned} chord files, {len(candidates)} candidates")
    print(f"  skipped (empty path): {skipped_empty}")
    print(f"  skipped (no style_map entry / not in allowlist): {skipped_no_style}")
    print(f"  skipped (no melody data): {skipped_no_melody}")
    print(f"  skipped (no library metadata): {skipped_no_meta}")
    return candidates


def allocate_quotas(bucket_sizes: dict, target: int, min_n: int, max_n: int) -> dict:
    """Sqrt-proportional allocation, clamped to [min_n, max_n]."""
    sqrt_sum = sum(math.sqrt(n) for n in bucket_sizes.values())
    raw = {s: (math.sqrt(n) / sqrt_sum) * target for s, n in bucket_sizes.items()}
    clamped = {s: max(min_n, min(max_n, int(round(v)))) for s, v in raw.items()}
    # Clamp further: never exceed bucket availability
    clamped = {s: min(v, bucket_sizes[s]) for s, v in clamped.items()}
    return clamped


def select_balanced(candidates: list, target: int = TOTAL_TARGET) -> dict:
    by_style: dict = defaultdict(list)
    for c in candidates:
        by_style[c["style"]].append(c)
    for style in by_style:
        by_style[style].sort(key=lambda x: -x["score"])

    bucket_sizes = {s: len(cs) for s, cs in by_style.items()}
    quotas = allocate_quotas(bucket_sizes, target, MIN_PER_STYLE, MAX_PER_STYLE)

    selected = []
    per_style_counts = {}
    for style, cands in by_style.items():
        q = quotas.get(style, 0)
        take = cands[:q]
        selected.extend(take)
        per_style_counts[style] = len(take)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": target,
        "total": len(selected),
        "params": {
            "min_per_style": MIN_PER_STYLE,
            "max_per_style": MAX_PER_STYLE,
            "source_priority": SOURCE_PRIORITY,
            "duration_window": [DURATION_MIN, DURATION_MAX],
        },
        "by_style": dict(sorted(per_style_counts.items(), key=lambda x: -x[1])),
        "bucket_sizes": dict(sorted(bucket_sizes.items(), key=lambda x: -x[1])),
        "tracks": selected,
    }


def main():
    t_start = time.time()
    print("Loading library_cache.json...")
    library_meta = load_library_metadata()
    print(f"  {len(library_meta)} tracks in library")

    print("Loading style_map.json...")
    sm = json.loads(STYLE_MAP.read_text(encoding="utf-8"))
    style_map = sm.get("tracks", {})
    allowed = set(sm.get("allowed_top_levels", []))
    if not allowed:
        raise RuntimeError(
            "style_map.json missing `allowed_top_levels` — regenerate with the "
            "allowlist-aware style_classifier.py first."
        )
    print(f"  {len(style_map)} tracks with style labels")
    print(f"  allowed top-level styles: {sorted(allowed)}")

    # Assertion: every style in style_map must be in (or derive from) allowlist
    for s in sm.get("styles", {}):
        top = s.split("/", 1)[0]
        assert top in allowed, (
            f"style_map contains style '{s}' whose top-level '{top}' is not in "
            f"allowed_top_levels {allowed}; regenerate style_map.json"
        )

    print("Scanning melody files...")
    melody_hashes = load_melody_hash_set()
    print(f"  {len(melody_hashes)} melody files indexed")

    print("\nScanning chord files...")
    candidates = scan_candidates(library_meta, style_map, melody_hashes)

    print("\nAllocating quotas and selecting...")
    result = select_balanced(candidates, TOTAL_TARGET)

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== Result ===")
    print(f"Target: {result['target']}")
    print(f"Selected: {result['total']}")
    print("\nPer-style selection:")
    for s, n in result["by_style"].items():
        total = result["bucket_sizes"].get(s, 0)
        print(f"  {s:30s} {n:4d} / {total:6d}")
    print(f"\nOutput: {OUTPUT}")
    print(f"Elapsed: {time.time() - t_start:.1f}s")


if __name__ == "__main__":
    main()
