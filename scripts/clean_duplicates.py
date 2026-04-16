import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = DATA_DIR / "library_cache.json"

def clean_duplicates():
    if not CACHE_FILE.is_file():
        print("No library cache found.")
        return

    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Failed to read cache: {e}")
        return

    tracks = cache.get("tracks", [])
    original_count = len(tracks)

    # Use a dictionary to deduplicate by path
    # (Assuming path is unique. If duplication is caused by identical paths)
    unique_tracks = {}
    duplicates_removed = 0

    for track in tracks:
        path = track.get("path")
        if path:
            if path in unique_tracks:
                duplicates_removed += 1
            else:
                unique_tracks[path] = track

    if duplicates_removed > 0:
        cache["tracks"] = list(unique_tracks.values())
        cache["total_tracks"] = len(cache["tracks"])
        
        # Save back to cache
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"Successfully removed {duplicates_removed} duplicate entries from library cache.")
        print(f"New total tracks: {cache['total_tracks']}")
    else:
        print("No duplicates found in library cache.")

if __name__ == "__main__":
    clean_duplicates()
