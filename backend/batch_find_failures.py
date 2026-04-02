"""
找出批次處理失敗的檔案：掃描音樂庫，比對已有的 chord JSON。
用法: python batch_find_failures.py --root Y:\
"""
import os
import sys
import json
import hashlib
import argparse
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))

SKIP_GENRES = {
    "classics", "classical", "symphony",
    "sleep", "relax", "meditation",
    "electronic dance music", "edm", "techno",
    "other"
}
SKIP_ALBUM_KEYWORDS = {
    "drum track", "drum loop", "hip-hop & rap beat",
    "horror soundscape", "sound effect", "sfx",
}
SUPPORTED_EXT = {".flac", ".mp3", ".wav"}
MAX_FILE_SIZE_MB = 100
CHORDS_DIR = Path(__file__).parent.parent / "data" / "chords"

def _song_hash(rel_path: str) -> str:
    rel_path = rel_path.replace("\\", "/")
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12]

def _is_skipped_genre(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) > 1:
        genre = parts[0].lower()
        if genre in SKIP_GENRES:
            return True
        if len(parts) > 2:
            album = parts[1].lower()
            for kw in SKIP_ALBUM_KEYWORDS:
                if kw in album:
                    return True
    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=str, required=True)
    args = parser.parse_args()
    root_dir = os.path.abspath(args.root)

    missing = []
    empty = []
    total = 0

    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXT:
                continue
            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, root_dir)

            if _is_skipped_genre(rel_path):
                continue
            try:
                fsize = os.path.getsize(full_path) / (1024 * 1024)
                if fsize > MAX_FILE_SIZE_MB:
                    continue
            except OSError:
                continue

            total += 1
            h = _song_hash(rel_path)
            out_file = CHORDS_DIR / f"{h}.json"

            if not out_file.is_file():
                missing.append((rel_path, fsize, "NO_JSON"))
            else:
                try:
                    with open(out_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not data.get("chords"):
                        empty.append((rel_path, fsize, "EMPTY_CHORDS"))
                except Exception as e:
                    empty.append((rel_path, fsize, f"BAD_JSON: {e}"))

    print(f"Total eligible tracks: {total}")
    print(f"Missing JSON (no output): {len(missing)}")
    print(f"Empty/bad JSON: {len(empty)}")
    print()

    # Group by genre folder
    from collections import Counter
    genre_counter = Counter()

    all_failures = missing + empty
    for rel, fsize, reason in sorted(all_failures):
        genre = rel.replace("\\", "/").split("/")[0] if "/" in rel.replace("\\", "/") else "root"
        genre_counter[genre] += 1
        line = f"  [{reason}] ({fsize:.1f}MB) {rel}"
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode())

    if genre_counter:
        print(f"\n--- Failures by genre folder ---")
        for genre, cnt in genre_counter.most_common():
            print(f"  {genre}: {cnt}")

    # Save to file
    out_path = Path(__file__).parent / "batch_failures.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Total eligible: {total}\n")
        f.write(f"Missing: {len(missing)}\n")
        f.write(f"Empty/bad: {len(empty)}\n\n")
        for rel, fsize, reason in sorted(all_failures):
            f.write(f"[{reason}] ({fsize:.1f}MB) {rel}\n")
    print(f"\nSaved to {out_path}")

if __name__ == "__main__":
    main()
