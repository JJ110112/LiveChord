"""One-shot migration: flatten chord JSONs into 2-char prefix buckets.

Before:
    data/chords/0000d0e0e8e4.json
    data/chords/0000d0e0e8e4.json.bak.beat_this
    data/chords/0000d0e0e8e4.json.bak.librosa
    ... (200k+ files in one dir)

After:
    data/chords/00/0000d0e0e8e4.json
    data/chords/00/0000d0e0e8e4.json.bak.beat_this
    data/chords/00/0000d0e0e8e4.json.bak.librosa
    ... (~256 buckets × ~300 files)

Properties:
- Idempotent: safe to re-run; files already in the right bucket are skipped
- Atomic per-file: uses ``os.replace`` (rename, not copy)
- Survives Ctrl+C: a partial run leaves valid filesystem; re-run completes
- Validates: name must start with 12 hex chars, else file is left alone

Usage::

    python migrate_shard_chords.py --chords-dir 'G:\\stage\\livechord\\data\\chords'
    python migrate_shard_chords.py --chords-dir 'V:\\data\\chords' --dry-run
    python migrate_shard_chords.py        # default: ../data/chords (dev box)

CRITICAL: do NOT run this against ``V:\\data\\chords`` while NUC 8800/8801
is up — concurrent writes from process_queue could land in the wrong layout.
Stop ``restart_dual.bat`` services first.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

# Hash format: 12 hex chars (md5 first 12 from chord_cache.song_hash).
_HASH_RE = re.compile(r"^([0-9a-f]{12})\.json(?:\.bak\.[a-z_]+)?$")


def _migrate_one(file: Path, dry_run: bool) -> str:
    """Move one file into its bucket. Returns short status string."""
    name = file.name
    m = _HASH_RE.match(name)
    if not m:
        return "SKIP_NOT_HASH"
    h = m.group(1)
    bucket = file.parent / h[:2]
    target = bucket / name
    if file.parent == bucket:
        return "SKIP_ALREADY_SHARDED"
    if target.exists():
        # Two copies of the same file — one already in the bucket. The bucket
        # version is canonical (was migrated earlier or written by new code).
        # Drop the legacy flat copy.
        if not dry_run:
            try:
                file.unlink()
            except Exception as e:
                return f"DUP_UNLINK_FAIL ({type(e).__name__})"
        return "DUP_DROP"
    if dry_run:
        return f"DRY_RUN -> {bucket.name}/"
    try:
        bucket.mkdir(parents=True, exist_ok=True)
        os.replace(str(file), str(target))
        return f"OK -> {bucket.name}/"
    except Exception as e:
        return f"FAIL ({type(e).__name__}: {e})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chords-dir", type=str, default="",
                    help="Chord JSON root (default: ../data/chords)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report only; don't move files.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N files (debug).")
    args = ap.parse_args()

    if args.chords_dir:
        chords_dir = Path(args.chords_dir).resolve()
    else:
        chords_dir = Path(__file__).resolve().parent.parent / "data" / "chords"

    if not chords_dir.is_dir():
        print(f"ERROR: {chords_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    print(f"Sharding chord files under: {chords_dir}")
    if args.dry_run:
        print("DRY RUN — nothing will move")

    # iterdir() is fast even on 200k flat files (single readdir); avoids
    # building a huge in-memory list before any work happens.
    counts: dict[str, int] = {}
    t0 = time.time()
    n_seen = 0
    n_acted = 0
    for entry in chords_dir.iterdir():
        if not entry.is_file():
            continue
        n_seen += 1
        st = _migrate_one(entry, args.dry_run)
        key = st.split(" ", 1)[0]
        counts[key] = counts.get(key, 0) + 1
        if key not in ("SKIP_ALREADY_SHARDED", "SKIP_NOT_HASH"):
            n_acted += 1
        if n_acted % 5000 == 0 and n_acted > 0:
            elapsed = time.time() - t0
            rate = n_seen / elapsed if elapsed > 0 else 0
            print(f"... {n_seen} scanned, {n_acted} migrated  ({rate:.0f}/s)")
        if args.limit > 0 and n_acted >= args.limit:
            break

    print()
    print("=== Summary ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"Scanned: {n_seen}  in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
