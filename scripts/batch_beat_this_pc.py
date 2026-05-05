"""PC-side batch beat_this runner: scans V:\\data\\chords for songs lacking
``beats_source == "beat_this"`` and runs beat_this on them in parallel.

Why this exists
---------------
The NUC has no CUDA, so beat_this can't run there. New chord JSONs that
land on V:\\ from process_queue (uploads / YouTube ingests) initially have
``beats_source = "librosa-fallback"`` because the NUC's ingest path
intentionally skips madmom for ingest speed. This script is the offline
PC-side sweep that promotes those entries to beat_this on the GPU.

Idempotent: pre-filters the file list to JSONs where
``beats_source != "beat_this"``, so re-runs are cheap (one stat + one
small JSON parse per file) and only new songs get processed.

Re-uses the per-song logic from
``backend/migrate_add_beats_via_beat_this.py`` (atomic write, bak rotation,
BPM-sanity halving, tempo_curve schema). This script is the parallel +
filtered driver around that.

GPU sharing note
----------------
Each ProcessPool worker loads its own ``File2Beats`` instance, so VRAM
scales linearly with ``--workers``. On RTX 5080 (16 GB), 3 workers ≈ 9 GB
peak. CUDA serializes kernel launches across processes, so 3 workers is
typically only ~1.3-1.5x faster than 1; the win is hiding I/O latency
(audio decode, JSON read) behind a parallel ahead-of-time fetch. Set
``--workers 1`` if you'd rather minimize GPU contention with another GPU
job (training, etc).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _needs_processing(json_path: Path) -> bool:
    """Pre-filter: True iff this JSON needs beat_this run.

    Cheap — one read, one .get(). Avoids paying ProcessPool worker startup
    cost (model load = several seconds) for files we'd skip anyway.
    """
    try:
        sheet = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if sheet.get("beats_source") == "beat_this":
        return False
    if not sheet.get("path"):
        return False
    if not sheet.get("chords"):
        return False
    return True


def _worker_task(json_path_str: str, device: str) -> tuple[str, str]:
    """Run inside ProcessPool worker. Returns (file_name, status_string).

    Imports happen here so the parent process doesn't pay the cost; each
    worker pays it once on first task and re-uses the loaded predictor
    via the module-global caching inside migrate_add_beats_via_beat_this.
    """
    try:
        from migrate_add_beats_via_beat_this import _process_one  # type: ignore[import-not-found]
    except Exception as e:
        return (Path(json_path_str).name, f"WORKER_IMPORT_FAIL ({type(e).__name__}: {e})")
    p = Path(json_path_str)
    try:
        st = _process_one(p, force=False, dry_run=False, device=device)
    except Exception as e:
        st = f"WORKER_RAISED ({type(e).__name__}: {str(e)[:100]})"
    return (p.name, st)


def _write_progress(progress_path: Path, payload: dict) -> None:
    try:
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = progress_path.with_suffix(progress_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, progress_path)
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--chords-dir", type=str, default=r"V:\data\chords",
                    help="Sharded chord JSON root (default: V:\\data\\chords).")
    ap.add_argument("--workers", type=int, default=3,
                    help="ProcessPool workers (default 3, max recommended 3 on RTX 5080).")
    ap.add_argument("--device", type=str, default="cuda")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N candidates (default: all).")
    ap.add_argument("--progress-file", type=str, default="",
                    help="Optional JSON progress file. Defaults to <repo>/data/logs/batch_beat_this_progress.json.")
    args = ap.parse_args()

    if args.workers < 1 or args.workers > 4:
        print(f"ERROR: --workers must be 1..4 (got {args.workers}). "
              f"Multiple workers share one GPU; >4 risks VRAM exhaustion.",
              file=sys.stderr)
        return 2

    chords_dir = Path(args.chords_dir).resolve()
    if not chords_dir.is_dir():
        print(f"ERROR: {chords_dir} does not exist or is not a directory", file=sys.stderr)
        return 1

    progress_path = (Path(args.progress_file).resolve() if args.progress_file
                     else _REPO_ROOT / "data" / "logs" / "batch_beat_this_progress.json")

    # Sharded layout: <chords_dir>/<bucket>/<hash>.json (recurse 1 level)
    # Exclude backup snapshots (.bak.librosa / .bak.madmom / .bak.beat_this / .bak.beat_refiner.json
    # etc) which are also *.json — running beat_this on a stale backup would
    # silently overwrite the canonical sheet with old data.
    print(f"[scan] {chords_dir} ...", flush=True)
    t0 = time.time()
    raw = sorted(chords_dir.glob("*/*.json"))
    if not raw:
        raw = sorted(chords_dir.glob("*.json"))
    all_files = [p for p in raw if ".bak." not in p.name]
    bak_dropped = len(raw) - len(all_files)
    print(f"[scan] {len(all_files):,} canonical chord JSONs ({bak_dropped:,} bak files filtered) "
          f"({time.time() - t0:.1f}s)", flush=True)

    # Pre-filter to candidates only — much faster than letting workers skip
    # (worker model-load is ~3-5s; one JSON read is ~1ms locally, ~10-50ms
    # over SMB). Use a thread pool because the work is I/O-bound (SMB
    # latency dominates), so 16 threads ≈ 16x speedup with no GIL fight.
    print(f"[filter] pre-filtering for beats_source != 'beat_this' ...", flush=True)
    t0 = time.time()
    candidates: list[Path] = []
    last_log = t0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(_needs_processing, p): p for p in all_files}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                if fut.result():
                    candidates.append(futs[fut])
            except Exception:
                pass
            now = time.time()
            if now - last_log >= 5.0:
                pct = 100.0 * done / max(1, len(all_files))
                print(f"[filter] {done:,}/{len(all_files):,} ({pct:.0f}%) · "
                      f"{len(candidates):,} candidates so far", flush=True)
                last_log = now
    candidates.sort()
    skipped = len(all_files) - len(candidates)
    print(f"[filter] {len(candidates):,} need beat_this · {skipped:,} skip "
          f"(already beat_this / no path / no chords) ({time.time() - t0:.1f}s)", flush=True)

    if args.limit > 0:
        candidates = candidates[: args.limit]
        print(f"[filter] --limit applied: processing {len(candidates):,} files", flush=True)

    if not candidates:
        print("[done] nothing to do.")
        _write_progress(progress_path, {
            "status": "idle", "total": 0, "processed": 0, "ok": 0, "skip": 0, "fail": 0,
            "started": time.time(), "ended": time.time(), "workers": args.workers,
        })
        return 0

    counts: dict[str, int] = {}
    n = len(candidates)
    started = time.time()
    print(f"[run] {args.workers} worker(s), device={args.device}, n={n}", flush=True)
    _write_progress(progress_path, {
        "status": "running", "total": n, "processed": 0, "ok": 0, "skip": 0, "fail": 0,
        "started": started, "workers": args.workers, "chords_dir": str(chords_dir),
    })

    ok = skip = fail = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_worker_task, str(p), args.device): p for p in candidates}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                name, status = fut.result()
            except Exception as e:
                p = futures[fut]
                name, status = (p.name, f"FUTURE_RAISED ({type(e).__name__}: {e})")

            key = status.split(" ", 1)[0]
            counts[key] = counts.get(key, 0) + 1
            if key == "OK":
                ok += 1
            elif key.startswith("SKIP"):
                skip += 1
            else:
                fail += 1

            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            eta_min = (n - i) / rate / 60 if rate > 0 else 0
            print(f"[{i}/{n}] {name}: {status}  ({rate:.2f}/s, ETA {eta_min:.0f}m)", flush=True)

            if i % 25 == 0 or i == n:
                _write_progress(progress_path, {
                    "status": "running", "total": n, "processed": i,
                    "ok": ok, "skip": skip, "fail": fail,
                    "started": started, "now": time.time(),
                    "rate_per_s": round(rate, 2), "eta_min": round(eta_min, 1),
                    "workers": args.workers, "chords_dir": str(chords_dir),
                })

    ended = time.time()
    print("\n=== Summary ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"Elapsed: {ended - started:.1f}s · OK={ok} SKIP={skip} FAIL={fail}")

    _write_progress(progress_path, {
        "status": "done", "total": n, "processed": n,
        "ok": ok, "skip": skip, "fail": fail,
        "started": started, "ended": ended,
        "workers": args.workers, "chords_dir": str(chords_dir),
        "counts": counts,
    })
    return 0 if fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
