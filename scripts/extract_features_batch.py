"""Parallel batch feature extraction for the beat_refiner training corpus.

Walks ``backend/ai/splits/manifest.json``, extracts the 98-channel audio
feature stack for each song via ``backend.ai.beat_refiner_features``,
and persists per-song .npz files under ``--cache-root`` (sharded by
``hash[:2]``).

Why this is a separate offline step
-----------------------------------
Training runs the encoder over hundreds of mini-batches per epoch. If
each batch had to load + decode FLAC + run librosa CQT, the GPU would
sit idle ~99% of the time waiting on I/O. Extracting once to .npz
(uncompressed float32) makes per-batch feature loads cost ~5–15 ms.

  ~50,000 songs × ~1 MB per npz → ~50 GB on V:\\data\\features\\
  Single-threaded: ~25 hours
  Parallel (8 workers): ~3 hours

Idempotent — already-cached songs are skipped automatically by
``cache_features``. Safe to interrupt + resume.

Usage
-----
Smoke test (50 songs, single worker)::

    python scripts/extract_features_batch.py \\
        --splits-dir backend/ai/splits \\
        --cache-root V:/data/features \\
        --max-songs 50 --workers 1

Full extraction (8 workers in parallel)::

    python scripts/extract_features_batch.py \\
        --splits-dir backend/ai/splits \\
        --cache-root V:/data/features \\
        --workers 8

Re-extract a specific tier (e.g. only beat_gold, after a feature spec bump)::

    python scripts/extract_features_batch.py \\
        --splits-dir backend/ai/splits \\
        --cache-root V:/data/features \\
        --filter-beat gold --force

Dry-run (parse args, count work, exit)::

    python scripts/extract_features_batch.py --dry-run \\
        --splits-dir backend/ai/splits

Reads only from manifest + audio paths. Writes only to ``--cache-root``.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("extract_features_batch")


# ---------------------------------------------------------------------------
# Worker — runs in a forked subprocess. Imports heavy deps lazily so the
# parent doesn't pay the cost when --dry-run.
# ---------------------------------------------------------------------------

def _extract_one(args: Tuple[str, str, str, bool]) -> Dict:
    """Worker function. Runs in a child process.

    Returns a result dict (always — never raises across the process
    boundary, since stack traces don't pickle cleanly in some setups).

    Args tuple shape:
      (song_hash, audio_path, cache_root, force)
    """
    song_hash, audio_path, cache_root, force = args
    t0 = time.time()
    try:
        # Lazy import inside the worker — avoids the parent re-importing
        # librosa just to spawn workers, and keeps the parent's memory
        # footprint small.
        from backend.ai.beat_refiner_features import (
            cache_features, features_path_for, load_cached,
        )

        # Skip-fast path: already-cached + version-OK + not forced.
        out_path = features_path_for(song_hash, cache_root)
        if out_path.exists() and not force:
            cached = load_cached(out_path)
            if cached is not None:
                return {
                    "hash": song_hash, "status": "skip-cached",
                    "path": str(out_path), "duration": cached["duration_sec"],
                    "elapsed": time.time() - t0,
                }
            # Version mismatch — fall through and re-extract.

        cache_features(audio_path, song_hash, cache_root, force=force)
        # Re-stat for size — avoids loading the file again to get a stat.
        size_kb = out_path.stat().st_size / 1024
        return {
            "hash": song_hash, "status": "ok",
            "path": str(out_path), "size_kb": round(size_kb, 1),
            "elapsed": time.time() - t0,
        }
    except FileNotFoundError as e:
        return {"hash": song_hash, "status": "error-file-missing",
                "error": str(e), "elapsed": time.time() - t0}
    except Exception as e:
        # Capture trace for diagnosis but keep the worker alive.
        return {"hash": song_hash, "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "trace_tail": traceback.format_exc().splitlines()[-3:],
                "elapsed": time.time() - t0}


# ---------------------------------------------------------------------------
# Manifest loading + filtering
# ---------------------------------------------------------------------------

def load_manifest(splits_dir: Path) -> List[Dict]:
    """Load entries from manifest.json under ``splits_dir``."""
    p = splits_dir / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(
            f"manifest not found at {p}. Run scripts/build_training_corpus.py "
            f"--build first."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    entries = data.get("entries") or []
    logger.info("loaded %d manifest entries from %s", len(entries), p)
    return entries


def filter_entries(entries: List[Dict], filter_beat: Optional[str],
                   filter_chord: Optional[str], max_songs: Optional[int]
                   ) -> List[Dict]:
    """Apply tier filters + cap. Returns a new list."""
    out = entries
    if filter_beat:
        out = [e for e in out if e.get("beat_quality") == filter_beat]
        logger.info("after beat=%s filter: %d", filter_beat, len(out))
    if filter_chord:
        out = [e for e in out if e.get("chord_quality") == filter_chord]
        logger.info("after chord=%s filter: %d", filter_chord, len(out))
    if max_songs and len(out) > max_songs:
        out = out[:max_songs]
        logger.info("capped to first %d songs", max_songs)
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_batch(entries: List[Dict], cache_root: Path, workers: int,
              force: bool, log_every: int = 25) -> Dict:
    """Dispatch ``entries`` across ``workers`` subprocesses, return summary.

    Reports rolling throughput every ``log_every`` completions so a long
    run is observable without spamming.
    """
    cache_root.mkdir(parents=True, exist_ok=True)

    tasks = [
        (e["hash"], e["audio_path"], str(cache_root), force)
        for e in entries
        if e.get("audio_path")  # entries without audio shouldn't exist past build
    ]

    counts = {"ok": 0, "skip-cached": 0, "error": 0, "error-file-missing": 0}
    error_examples: List[Dict] = []
    total_size_kb = 0.0
    t_start = time.time()
    last_log = t_start
    last_done = 0

    if workers <= 1:
        # Single-process path — easier to debug, no pickling overhead
        for i, t in enumerate(tasks):
            r = _extract_one(t)
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            if r["status"] == "ok":
                total_size_kb += r.get("size_kb", 0.0)
            elif r["status"].startswith("error") and len(error_examples) < 20:
                error_examples.append(r)
            if (i + 1) % log_every == 0 or time.time() - last_log > 30:
                _log_progress(i + 1, len(tasks), counts, t_start, last_log, last_done)
                last_log = time.time()
                last_done = i + 1
    else:
        # Process-pool path — librosa releases GIL so this scales near-linearly
        # in CPU cores until disk I/O becomes the bottleneck.
        ctx = mp.get_context("spawn")  # spawn is safer than fork on Windows
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futures = [pool.submit(_extract_one, t) for t in tasks]
            for i, fut in enumerate(as_completed(futures)):
                r = fut.result()
                counts[r["status"]] = counts.get(r["status"], 0) + 1
                if r["status"] == "ok":
                    total_size_kb += r.get("size_kb", 0.0)
                elif r["status"].startswith("error") and len(error_examples) < 20:
                    error_examples.append(r)
                if (i + 1) % log_every == 0 or time.time() - last_log > 30:
                    _log_progress(i + 1, len(tasks), counts,
                                  t_start, last_log, last_done)
                    last_log = time.time()
                    last_done = i + 1

    elapsed = time.time() - t_start
    return {
        "elapsed_sec": round(elapsed, 1),
        "n_tasks": len(tasks),
        "counts": counts,
        "total_cache_size_mb": round(total_size_kb / 1024, 1),
        "error_examples_first_20": error_examples,
    }


def _log_progress(n_done: int, n_total: int, counts: Dict,
                  t_start: float, t_last: float, last_done: int):
    """Pretty-print a progress line with both global and rolling rate."""
    now = time.time()
    global_rate = n_done / max(1.0, now - t_start)
    rolling_rate = (n_done - last_done) / max(0.1, now - t_last)
    eta_sec = (n_total - n_done) / max(0.1, rolling_rate)
    logger.info(
        "  %d/%d (%.1f%%) | rate=%.1f/s (rolling %.1f/s) | "
        "ok=%d skip=%d err=%d miss=%d | ETA %.1f min",
        n_done, n_total, 100.0 * n_done / n_total,
        global_rate, rolling_rate,
        counts.get("ok", 0), counts.get("skip-cached", 0),
        counts.get("error", 0), counts.get("error-file-missing", 0),
        eta_sec / 60.0,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--splits-dir", type=Path,
                    default=_REPO / "backend" / "ai" / "splits",
                    help="Dir holding manifest.json (default: backend/ai/splits)")
    ap.add_argument("--cache-root", type=Path,
                    default=Path("V:/data/features"),
                    help="Where to write per-song .npz caches "
                         "(default: V:/data/features)")
    ap.add_argument("--workers", type=int,
                    default=max(1, (os.cpu_count() or 4) // 2),
                    help="Process pool size. Defaults to half of CPU count.")
    ap.add_argument("--filter-beat", choices=("gold", "ok", "skip"),
                    help="Only extract songs with this beat_quality")
    ap.add_argument("--filter-chord", choices=("gold", "ok", "skip"),
                    help="Only extract songs with this chord_quality")
    ap.add_argument("--max-songs", type=int, default=None,
                    help="Cap entries (smoke test)")
    ap.add_argument("--force", action="store_true",
                    help="Re-extract even if a valid cache exists")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse args + count tasks, no extraction")
    ap.add_argument("--out-report", type=Path, default=None,
                    help="If set, write the run summary JSON to this path")
    args = ap.parse_args()

    cfg = {
        "splits_dir": str(args.splits_dir),
        "cache_root": str(args.cache_root),
        "workers": args.workers,
        "filter_beat": args.filter_beat,
        "filter_chord": args.filter_chord,
        "max_songs": args.max_songs,
        "force": args.force,
    }
    logger.info("config: %s", json.dumps(cfg))

    entries = load_manifest(args.splits_dir)
    entries = filter_entries(entries, args.filter_beat, args.filter_chord,
                             args.max_songs)

    n_with_audio = sum(1 for e in entries if e.get("audio_path"))
    logger.info("tasks: %d entries, %d with audio_path", len(entries), n_with_audio)

    if args.dry_run:
        return

    summary = run_batch(entries, args.cache_root, args.workers,
                        args.force)
    summary["config"] = cfg
    summary["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.out_report:
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("wrote summary to %s", args.out_report)


if __name__ == "__main__":
    main()
