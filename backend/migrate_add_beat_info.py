"""Retrofit existing chord JSONs with BPM + beat-snapped chord boundaries.

Before this script, BTC-detected chord JSONs in ``data/chords/*.json`` did not
carry a ``bpm`` field and their ``time``/``end`` were raw frame-level outputs,
not aligned to a beat grid. That produced fractional dot counts on the player
ribbon and inconsistent display between player and editor.

This script walks every official chord JSON, resolves the source audio, runs
``beat_snap.analyze_and_snap``, and writes the updated JSON back. Chord NAMES
are never changed — only ``time``, ``end``, and the new ``bpm`` field.

Usage (from inside the ``backend/`` directory):

    python migrate_add_beat_info.py --dry-run           # report-only
    python migrate_add_beat_info.py --limit 10          # process first 10
    python migrate_add_beat_info.py --workers 8         # 8 parallel workers
    python migrate_add_beat_info.py                     # do everything
    python migrate_add_beat_info.py --force             # re-run on JSONs
                                                        #  that already have bpm

Resuming after a crash / Ctrl+C
-------------------------------
**Just re-run the same command.** The script skips any JSON that already
has a ``bpm`` field (status ``SKIP_HAS_BPM``), and the per-file write is
atomic (``.tmp`` + ``os.replace``), so a successfully-processed file is
either fully written or untouched — never partial. No checkpoint file,
no ``--start-from``. On the re-run you'll see ``SKIP_HAS_BPM`` zip past
the already-done files, then it picks up where it left off.

(``--start-from`` exists for debugging — processing only a specific range
of filenames — not for resume. Most users should ignore it.)

Design choices
--------------
- **Operates on the OFFICIAL chord dir only** (``data/chords/``). User
  per-account files under ``data/users/*/chords/`` are their own edits —
  leave them alone. A user can trigger per-song calibration if they want.
- **Skips files that already have ``bpm``** unless ``--force`` is passed.
  Rerunning is idempotent but adds CPU cost.
- **Preserves all other fields** (key, capo, source, chords[].chord, etc.).
- **Writes via atomic replace** (``.tmp`` + rename) so a crash mid-write
  doesn't leave a truncated file.
- **Never deletes anything.** If the audio file can't be found or the beat
  tracker fails, the JSON is left untouched.
- **CPU only.** Beat tracking is a CPU op; doesn't touch the GPU so it can
  run alongside the NUC's BTC worker without contention.

Performance
-----------
Per song: ~1-2 seconds (dominated by ``librosa.load``). ~4500 songs therefore
takes roughly 1.5-2.5 hours serial. Can be parallelized with
``concurrent.futures.ProcessPoolExecutor`` later if needed — not done here
to keep the first pass simple and easy to checkpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Add this file's directory to sys.path so imports work when run directly
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from beat_snap import analyze_and_snap  # noqa: E402
from config import resolve_path  # noqa: E402

DATA_DIR = _THIS_DIR.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"


def _process_one_worker(args_tuple):
    """Worker wrapper so ProcessPoolExecutor can pickle args."""
    json_path, force, dry_run = args_tuple
    return (json_path.name, _process_one(json_path, force, dry_run))


def _process_one(json_path: Path, force: bool, dry_run: bool) -> str:
    """Process a single chord JSON. Returns a short status string."""
    try:
        sheet = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"READ_FAIL ({type(e).__name__})"

    if sheet.get("bpm") and not force:
        return "SKIP_HAS_BPM"

    track_path = sheet.get("path")
    if not track_path:
        return "SKIP_NO_PATH"

    audio_path = resolve_path(track_path)
    if not audio_path or not os.path.isfile(audio_path):
        return "SKIP_AUDIO_MISSING"

    chords = sheet.get("chords") or []
    if not chords:
        return "SKIP_NO_CHORDS"

    # Work on a copy so a mid-run exception doesn't corrupt in-memory state
    chords_copy = [dict(c) for c in chords]
    bpm, n_snapped = analyze_and_snap(audio_path, chords_copy)

    if bpm is None:
        return "FAIL_NO_BEATS"

    if dry_run:
        return f"DRY_RUN bpm={bpm:.1f} snap={n_snapped}"

    sheet["chords"] = chords_copy
    sheet["bpm"] = round(float(bpm), 1)

    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, json_path)
    return f"OK bpm={bpm:.1f} snap={n_snapped}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write back. Reports what would change.")
    ap.add_argument("--force", action="store_true",
                    help="Process files that already have bpm (default: skip them).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N files (default: all).")
    ap.add_argument("--start-from", type=str, default="",
                    help="Debug-only: skip files whose name sorts before this "
                         "(for processing a specific filename range). You do "
                         "NOT need this for crash recovery — just re-run the "
                         "same command and SKIP_HAS_BPM will fast-forward.")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel worker processes (default 1 = serial). Each "
                         "worker loads librosa and an audio file, so memory "
                         "scales linearly; 4-8 is usually a sweet spot.")
    args = ap.parse_args()

    if not CHORDS_DIR.is_dir():
        print(f"ERROR: {CHORDS_DIR} does not exist", file=sys.stderr)
        sys.exit(1)

    # Sharded layout: <CHORDS_DIR>/<bucket>/<hash>.json (recurse 1 level)
    files = sorted(CHORDS_DIR.glob("*/*.json"))
    if not files:
        files = sorted(CHORDS_DIR.glob("*.json"))
    total = len(files)
    print(f"Found {total} chord JSON files in {CHORDS_DIR}")
    if args.dry_run:
        print("DRY RUN — nothing will be written")

    if args.start_from:
        files = [f for f in files if f.name >= args.start_from]
        print(f"After --start-from={args.start_from}: {len(files)} files")

    if args.limit > 0:
        files = files[:args.limit]
        print(f"--limit applied: processing {len(files)} files")

    counts = {}
    t0 = time.time()
    total_n = len(files)

    if args.workers <= 1:
        for i, f in enumerate(files, 1):
            status = _process_one(f, force=args.force, dry_run=args.dry_run)
            key = status.split(" ", 1)[0]
            counts[key] = counts.get(key, 0) + 1
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total_n - i) / rate if rate > 0 else 0
            print(f"[{i}/{total_n}] {f.name}: {status}  ({rate:.1f}/s, ETA {eta / 60:.0f}m)")
    else:
        print(f"Using {args.workers} worker processes")
        payload = [(f, args.force, args.dry_run) for f in files]
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_process_one_worker, p) for p in payload]
            for i, fut in enumerate(as_completed(futures), 1):
                fname, status = fut.result()
                key = status.split(" ", 1)[0]
                counts[key] = counts.get(key, 0) + 1
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total_n - i) / rate if rate > 0 else 0
                print(f"[{i}/{total_n}] {fname}: {status}  ({rate:.1f}/s, ETA {eta / 60:.0f}m)")

    print("\n=== Summary ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
