"""Retrofit existing chord JSONs with dynamic beats[]/downbeats[]/tempo_curve.

Phase 4 of the rubato BPM upgrade. Where ``migrate_add_beat_info.py`` retrofitted
a single scalar ``bpm`` field via librosa, this script runs the new madmom RNN+DBN
tracker (when available) to capture rubato structure for live and old recordings.

Idempotent: skips JSONs that already have ``beats_source == "madmom"``.
Per-file write is atomic (``.tmp`` + ``os.replace``) so Ctrl+C is safe — just
re-run the same command and it picks up where it left off.

Usage (from inside the ``backend/`` directory):

    python migrate_add_dynamic_beats.py --dry-run         # report-only
    python migrate_add_dynamic_beats.py --limit 5         # process first 5
    python migrate_add_dynamic_beats.py --only "<substr>" # filter by path
    python migrate_add_dynamic_beats.py --workers 2       # parallel madmom
    python migrate_add_dynamic_beats.py                   # do everything
    python migrate_add_dynamic_beats.py --force           # re-run on madmom
                                                          #  files too
    python migrate_add_dynamic_beats.py --regen-acc       # also delete stale
                                                          #  accompaniment cache
                                                          #  JSONs for processed
                                                          #  songs (forces regen
                                                          #  on next request)

Workers note
------------
madmom is ~30s/song (vs ~1-2s for librosa). With 2 workers and ~4500 songs
the full migration is ~10-15 hours. Personal-mode safe: this is a CPU op,
no GPU contention with BTC.

--only is great for the per-song experiments described in Phase 4:

    python migrate_add_dynamic_beats.py --only "Besame Mucho"
    python migrate_add_dynamic_beats.py --only "Lettre"

Cache invalidation
------------------
Bumps ``beat_version`` on every successful write. The player's stale-acc
detector then shows a toast informing the user that their cached
accompaniment was generated against the old beats. Use ``--regen-acc`` to
also delete matching ``data/accompaniments/<hash>_*.json`` files so the next
playback request triggers a fresh generation against the new tempo_curve.
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

from beat_snap import analyze_and_snap_dynamic, BEAT_VERSION, HAS_MADMOM  # noqa: E402
from config import resolve_path  # noqa: E402

DATA_DIR = _THIS_DIR.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"
ACCOMP_DIR = DATA_DIR / "accompaniments"


def _process_one_worker(args_tuple):
    json_path, force, dry_run, regen_acc = args_tuple
    return (json_path.name, _process_one(json_path, force, dry_run, regen_acc))


def _process_one(json_path: Path, force: bool, dry_run: bool, regen_acc: bool) -> str:
    """Process a single chord JSON. Returns a short status string."""
    try:
        sheet = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"READ_FAIL ({type(e).__name__})"

    # Idempotent: skip if already migrated to madmom dynamic beats
    if sheet.get("beats_source") == "madmom" and not force:
        return "SKIP_HAS_MADMOM"

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
    # Migration is the slow-but-accurate path — explicitly opt into madmom
    # (analyze_and_snap_dynamic defaults to prefer_madmom=False so the ingest
    # paths stay fast).
    info = analyze_and_snap_dynamic(audio_path, chords_copy, prefer_madmom=True)

    if not info.get("beats_source"):
        return "FAIL_NO_BEATS"

    if dry_run:
        n_beats = len(info.get("beats", []))
        n_db = len(info.get("downbeats", []))
        tc = info.get("tempo_curve") or []
        rng = (max(p["bpm"] for p in tc) - min(p["bpm"] for p in tc)) if tc else 0
        return (f"DRY_RUN src={info['beats_source']} bpm={info.get('bpm')} "
                f"beats={n_beats} db={n_db} range={rng:.1f}")

    # Write back: keep all existing fields (path/key/capo/source/title/etc),
    # update bpm + chords[] (snapped), add the new dynamic-beat block.
    sheet["chords"] = chords_copy
    bpm_val = info.get("bpm")
    if bpm_val:
        sheet["bpm"] = round(float(bpm_val), 1)
    sheet["beats"] = info.get("beats", [])
    sheet["downbeats"] = info.get("downbeats", [])
    sheet["tempo_curve"] = info.get("tempo_curve", [])
    sheet["beats_source"] = info["beats_source"]
    # Bump version so the player's stale-acc gate fires for cached
    # accompaniments that were generated under the previous beats[].
    prev_v = int(sheet.get("beat_version", 0))
    if force and prev_v >= BEAT_VERSION:
        # Re-run on already-migrated file → bump past current to invalidate
        sheet["beat_version"] = prev_v + 1
    else:
        sheet["beat_version"] = max(prev_v, BEAT_VERSION)

    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, json_path)

    n_purged = 0
    if regen_acc and ACCOMP_DIR.is_dir():
        # Hash-prefixed cache filenames: <hash>_<style>_<level>_<section>_<vers>.json
        hash_prefix = json_path.stem + "_"
        for f in ACCOMP_DIR.glob(f"{hash_prefix}*.json"):
            try:
                f.unlink()
                n_purged += 1
            except Exception:
                pass

    tc = info.get("tempo_curve") or []
    rng = (max(p["bpm"] for p in tc) - min(p["bpm"] for p in tc)) if tc else 0
    purged_tag = f" purge={n_purged}" if n_purged else ""
    return (f"OK src={info['beats_source']} bpm={info.get('bpm')} "
            f"snap={info.get('n_snapped')} range={rng:.1f}{purged_tag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write back. Reports what would change.")
    ap.add_argument("--force", action="store_true",
                    help="Process files that already have madmom beats (default: skip).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N files (default: all).")
    ap.add_argument("--only", type=str, default="",
                    help="Substring filter on the chord JSON's `path` field. "
                         "Useful for per-song experiments. "
                         "Example: --only 'Besame Mucho'")
    ap.add_argument("--exclude", type=str, default="",
                    help="Comma-separated case-insensitive substring blacklist on the "
                         "chord JSON's `path` field. Skip files whose path contains ANY "
                         "listed substring. Example: --exclude 'Classics,Sleep'")
    ap.add_argument("--start-from", type=str, default="",
                    help="Debug: skip files whose name sorts before this.")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel worker processes (default 1). madmom uses "
                         "~500MB RAM per worker.")
    ap.add_argument("--regen-acc", action="store_true",
                    help="Also delete cached accompaniment JSONs for processed "
                         "songs so the next request regenerates them against "
                         "the new tempo_curve.")
    ap.add_argument("--chords-dir", type=str, default="",
                    help="Override the chord JSON directory (default: ../data/chords). "
                         "Use this to run madmom against a local SSD copy of V:\\data\\chords "
                         "(staging workflow) — much faster than reading/writing over SMB. "
                         "Example: --chords-dir 'G:\\stage\\livechord\\data\\chords'")
    args = ap.parse_args()

    if not HAS_MADMOM:
        print("WARNING: madmom is not installed. Will fall back to librosa "
              "(no dynamic beats). See backend/requirements.txt for install steps.")

    chords_dir = Path(args.chords_dir).resolve() if args.chords_dir else CHORDS_DIR
    if not chords_dir.is_dir():
        print(f"ERROR: {chords_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # Sharded layout: <chords_dir>/<bucket>/<hash>.json (recurse 1 level)
    files = sorted(chords_dir.glob("*/*.json"))
    if not files:
        # Legacy flat fallback (pre-sharding migration)
        files = sorted(chords_dir.glob("*.json"))
    total = len(files)
    print(f"Found {total} chord JSON files in {chords_dir}")
    if args.dry_run:
        print("DRY RUN — nothing will be written")

    if args.only or args.exclude:
        only_needle = args.only.lower() if args.only else ""
        exclude_needles = [s.strip().lower() for s in args.exclude.split(",")
                           if s.strip()] if args.exclude else []
        kept = []
        for f in files:
            try:
                p = json.loads(f.read_text(encoding="utf-8")).get("path", "").lower()
            except Exception:
                continue
            if only_needle and only_needle not in p:
                continue
            if exclude_needles and any(n in p for n in exclude_needles):
                continue
            kept.append(f)
        files = kept
        parts = []
        if args.only:
            parts.append(f"--only={args.only!r}")
        if args.exclude:
            parts.append(f"--exclude={args.exclude!r}")
        print(f"After {' '.join(parts)}: {len(files)} files")

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
            status = _process_one(f, args.force, args.dry_run, args.regen_acc)
            key = status.split(" ", 1)[0]
            counts[key] = counts.get(key, 0) + 1
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total_n - i) / rate if rate > 0 else 0
            print(f"[{i}/{total_n}] {f.name}: {status}  ({rate:.2f}/s, ETA {eta / 60:.0f}m)")
    else:
        print(f"Using {args.workers} worker processes")
        payload = [(f, args.force, args.dry_run, args.regen_acc) for f in files]
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_process_one_worker, p) for p in payload]
            for i, fut in enumerate(as_completed(futures), 1):
                fname, status = fut.result()
                key = status.split(" ", 1)[0]
                counts[key] = counts.get(key, 0) + 1
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total_n - i) / rate if rate > 0 else 0
                print(f"[{i}/{total_n}] {fname}: {status}  ({rate:.2f}/s, ETA {eta / 60:.0f}m)")

    print("\n=== Summary ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
