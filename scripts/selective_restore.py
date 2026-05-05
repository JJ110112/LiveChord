"""Restore only specific chord JSONs from their .bak.beat_refiner.json
snapshots — for the bad-bpb songs identified by find_bad_downbeats.py.

Reads a list of live JSON paths (one per line) from --hashes-file and
copies the matching .bak.beat_refiner.json over each, leaving all other
applied songs alone.

Idempotent: if .bak doesn't exist, just logs and continues.
"""
import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

_BAK_SUFFIX = ".bak.beat_refiner"


def restore(live_path: Path) -> str:
    # Snapshot is stored as <live_path>.bak.beat_refiner.json (appended,
    # NOT a replacement of .json). Match the backfill script's convention:
    #   bak = json_path + _BAK_SUFFIX + ".json"
    bak = Path(str(live_path) + _BAK_SUFFIX + ".json")
    if not bak.exists():
        return "no-bak"
    try:
        shutil.copy2(bak, live_path)
        return "restored"
    except Exception as e:
        return f"error:{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hashes-file", required=True, type=Path,
                    help="One live JSON path per line.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    paths = [Path(line.strip()) for line in args.hashes_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"target: {len(paths):,} songs")
    if args.dry_run:
        print("(dry run — no changes)")
        for p in paths[:5]:
            bak = Path(str(p) + _BAK_SUFFIX + ".json")
            print(f"  {p.name}  bak_exists={bak.exists()}")
        return

    counts: Counter = Counter()
    for i, p in enumerate(paths, 1):
        counts[restore(p)] += 1
        if i % 200 == 0:
            print(f"  {i:,}/{len(paths):,} {dict(counts)}", file=sys.stderr)
    print(f"\ndone: {dict(counts)}")


if __name__ == "__main__":
    main()
