"""Backfill phase1_refine results onto already-detected chord JSONs.

For every chord JSON under ``--root`` that doesn't yet carry a
``beat_refiner`` metadata block, runs ``ai.bar_arbitrator.phase1_refine``
and writes the result back in-place.

Why
---
The 78k chord JSONs in ``V:\\data\\chords`` were built before the
beat_refiner existed. Roughly 10-25% of them carry the 3+1 splitting
problem driven by misaligned ``downbeats[]``. Re-running BTC + beat_snap
on all of them would take ~30h on RTX 5080 and overwrite chord names
the user may have manually corrected. Backfilling phase1_refine instead
only touches ``beats[]`` / ``downbeats[]`` (and adds metadata); chord
names + key + tempo_curve are preserved.

Idempotent + safe
-----------------
- ``beat_refiner`` metadata already present? Skip the song (unless
  ``--force``).
- About to mutate ``beats[]``/``downbeats[]``? Snapshot the original
  chord JSON to ``<hash>.bak.beat_refiner.json`` first (one-time;
  re-runs preserve the very-first snapshot, mirroring the existing
  ``.bak.beat_this`` pattern).
- Atomic file write: tmp file + ``os.replace`` so a Ctrl-C never leaves
  a half-written JSON on disk.
- audio path missing on this machine? Skip with ``no-audio`` reason.

Throughput
----------
At ~0.84s/song mean (PC i9 CPU benchmark), 73k songs single-threaded
≈ 17h. ProcessPoolExecutor with ``--workers 4`` typically halves wall
time but disk I/O on the NAS (audio source) becomes the bottleneck past
4-6 workers.

Restoration
-----------
To undo:

    python scripts/backfill_beat_refiner.py --restore --root V:/data/chords

This walks every ``*.bak.beat_refiner.json``, swaps the content back into
the live JSON. Safe to run multiple times (idempotent on missing backups).

Usage
-----
Stats only::

    python scripts/backfill_beat_refiner.py --stats --root V:/data/chords

Backfill, 4 workers::

    python scripts/backfill_beat_refiner.py --build --root V:/data/chords \\
        --music-roots Y:/ Z:/ --workers 4

Restore everything::

    python scripts/backfill_beat_refiner.py --restore --root V:/data/chords
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import shutil
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_beat_refiner")


# ---------------------------------------------------------------------------
# Helpers shared with build_training_corpus — duplicated here to keep this
# script standalone (avoids importing the much-heavier corpus builder).
# ---------------------------------------------------------------------------

import re

_AT_PREFIX_RE = re.compile(r"^@\d+[/\\]")
_BAK_SUFFIX = ".bak.beat_refiner"


def resolve_audio_path(rel: str, music_roots: List[Path]) -> Optional[Path]:
    """Same algorithm as build_training_corpus — strip ``@N/`` and try each
    music root in order. Returns first existing path or None."""
    if not rel:
        return None
    stripped = _AT_PREFIX_RE.sub("", rel.replace("\\", "/"))
    for root in music_roots:
        candidate = root / stripped
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def _walk_chord_dir(root: Path):
    """Yield chord JSON paths under ``root``. Skips ``.bak.*`` backup files
    and the script's own snapshot files."""
    if not root.exists():
        return
    for p in root.rglob("*.json"):
        if ".bak." in p.name:
            continue
        try:
            p.stat()
        except OSError:
            continue
        yield p


# ---------------------------------------------------------------------------
# Worker — runs phase1_refine on one chord JSON. Lazy heavy imports inside
# so the parent process doesn't pay the cost on --dry-run / --stats.
# ---------------------------------------------------------------------------

def _process_one(task: Tuple[str, Optional[str], bool]) -> Dict:
    """Workhorse. Args tuple: (chord_json_path, audio_path, force)."""
    json_path, audio_path, force = task
    t0 = time.time()
    try:
        cd = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "error",
                "path": json_path,
                "error": f"{type(e).__name__}: {e}",
                "elapsed": time.time() - t0}

    if cd.get("beat_refiner") and not force:
        return {"status": "skip-already-done",
                "path": json_path, "elapsed": time.time() - t0}

    if not audio_path or not os.path.isfile(audio_path):
        # Mark with a metadata stub so we don't re-attempt unless --force
        cd["beat_refiner"] = {"applied": False, "reason": "no-audio"}
        _atomic_write_json(json_path, cd)
        return {"status": "skip-no-audio",
                "path": json_path, "elapsed": time.time() - t0}

    try:
        # Lazy import — torch + librosa take ~10s on first call per worker
        from backend.ai.bar_arbitrator import phase1_refine
        res = phase1_refine(cd, audio_path)
    except Exception as e:
        return {"status": "error",
                "path": json_path,
                "error": f"phase1_refine: {type(e).__name__}: {e}",
                "trace_tail": traceback.format_exc().splitlines()[-3:],
                "elapsed": time.time() - t0}

    cd["beat_refiner"] = {
        "applied": res["applied"],
        "reason": res["reason"],
        "model_version": res.get("model_version", "phase1_v1"),
        "score_before": res.get("score_before", 0.0),
        "score_after": res.get("score_after", 0.0),
        "n_beats_before": len(res.get("beats_before") or []),
        "n_beats_after": len(res.get("beats_after") or []),
        "n_downbeats_before": len(res.get("downbeats_before") or []),
        "n_downbeats_after": len(res.get("downbeats_after") or []),
    }

    if res["applied"]:
        # First-time apply on this song: snapshot the chord JSON before we
        # overwrite ``beats`` / ``downbeats``. Subsequent apply→restore→apply
        # cycles preserve the very-first snapshot — never overwritten.
        bak = json_path + _BAK_SUFFIX + ".json"
        if not os.path.exists(bak):
            try:
                shutil.copy2(json_path, bak)
            except OSError as e:
                # If we can't backup, abort the mutation so user can rollback
                return {"status": "error",
                        "path": json_path,
                        "error": f"backup-failed: {e}",
                        "elapsed": time.time() - t0}
        cd["beats"] = res["beats_after"]
        cd["downbeats"] = res["downbeats_after"]

    _atomic_write_json(json_path, cd)

    return {
        "status": "applied" if res["applied"] else "no-op",
        "path": json_path,
        "applied": res["applied"],
        "score_before": res.get("score_before", 0.0),
        "score_after": res.get("score_after", 0.0),
        "n_db_before": len(res.get("downbeats_before") or []),
        "n_db_after": len(res.get("downbeats_after") or []),
        "elapsed": time.time() - t0,
    }


def _atomic_write_json(path: str, data: Dict):
    """Write JSON via tmp file + os.replace so partial writes never replace
    the live file on Ctrl-C. Mirrors how chord_api saves chord JSONs."""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Restore — re-instate the .bak.beat_refiner.json snapshots
# ---------------------------------------------------------------------------

def restore_all(root: Path) -> Dict:
    """Walk root, restore every .bak.beat_refiner.json back to the live file."""
    counts: Counter = Counter()
    errors: List[str] = []
    for bak in root.rglob(f"*{_BAK_SUFFIX}.json"):
        live = Path(str(bak).replace(_BAK_SUFFIX + ".json", ".json"))
        try:
            shutil.copy2(bak, live)
            counts["restored"] += 1
        except Exception as e:
            counts["restore-failed"] += 1
            if len(errors) < 20:
                errors.append(f"{bak.name}: {type(e).__name__}: {e}")
    return {"counts": dict(counts), "errors_first_20": errors}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def collect_tasks(root: Path, music_roots: List[Path],
                  filter_noisy: bool, force: bool, max_songs: Optional[int]
                  ) -> Tuple[List[Tuple], Dict]:
    """Walk root, build the worker task list. Returns (tasks, pre_stats)."""
    counts: Counter = Counter()
    tasks: List[Tuple[str, Optional[str], bool]] = []

    for json_path in _walk_chord_dir(root):
        counts["seen"] += 1
        try:
            cd = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            counts["unparseable"] += 1
            continue

        # Idempotent skip
        if cd.get("beat_refiner") and not force:
            counts["already-done"] += 1
            continue

        # Optional pre-filter: only process songs likely to need refinement.
        # Useful for accelerated runs that target only the worst offenders.
        if filter_noisy:
            from backend.ai.bar_arbitrator import _gap_stats
            db = cd.get("downbeats") or []
            _, cv = _gap_stats(db)
            if cv is None or cv < 0.10:
                counts["skip-clean"] += 1
                continue

        rel = cd.get("path") or ""
        audio_path = resolve_audio_path(rel, music_roots) if rel else None
        if audio_path is None:
            counts["no-audio-resolved"] += 1
            # Still queue it — worker writes a stub metadata so we know we
            # tried (won't re-attempt next run).

        tasks.append((str(json_path),
                      str(audio_path) if audio_path else None,
                      force))
        if max_songs and len(tasks) >= max_songs:
            break

    return tasks, dict(counts)


def run_backfill(tasks: List[Tuple], workers: int, log_every: int = 50
                 ) -> Dict:
    """Dispatch tasks across worker processes. Returns summary."""
    counts: Counter = Counter()
    examples: Dict[str, List[Dict]] = {"applied": [], "no-op": [], "error": []}
    align_gains: List[float] = []
    elapsed_per_task: List[float] = []
    t_start = time.time()
    last_log = t_start
    last_done = 0

    if workers <= 1:
        for i, t in enumerate(tasks):
            r = _process_one(t)
            _record(r, counts, examples, align_gains, elapsed_per_task)
            if (i + 1) % log_every == 0 or time.time() - last_log > 30:
                _log_progress(i + 1, len(tasks), counts,
                              t_start, last_log, last_done)
                last_log = time.time()
                last_done = i + 1
    else:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futures = [pool.submit(_process_one, t) for t in tasks]
            for i, fut in enumerate(as_completed(futures)):
                r = fut.result()
                _record(r, counts, examples, align_gains, elapsed_per_task)
                if (i + 1) % log_every == 0 or time.time() - last_log > 30:
                    _log_progress(i + 1, len(tasks), counts,
                                  t_start, last_log, last_done)
                    last_log = time.time()
                    last_done = i + 1

    elapsed = time.time() - t_start
    align_summary = _summary_stats(align_gains) if align_gains else None
    elapsed_summary = _summary_stats(elapsed_per_task) if elapsed_per_task else None
    return {
        "elapsed_sec": round(elapsed, 1),
        "n_tasks": len(tasks),
        "counts": dict(counts),
        "applied_align_gain": align_summary,
        "per_task_elapsed_sec": elapsed_summary,
        "examples": examples,
    }


def _record(r: Dict, counts: Counter, examples: Dict,
            align_gains: List[float], elapsed_per_task: List[float]):
    counts[r["status"]] += 1
    elapsed_per_task.append(r.get("elapsed", 0.0))
    if r["status"] == "applied":
        gain = r.get("score_after", 0) - r.get("score_before", 0)
        align_gains.append(gain)
        if len(examples["applied"]) < 10:
            examples["applied"].append({
                "path": r["path"], "score_before": r["score_before"],
                "score_after": r["score_after"], "n_db_before": r["n_db_before"],
                "n_db_after": r["n_db_after"],
            })
    elif r["status"] == "error":
        if len(examples["error"]) < 10:
            examples["error"].append({"path": r["path"],
                                       "error": r.get("error", "?")})
    elif r["status"] == "no-op" and len(examples["no-op"]) < 5:
        examples["no-op"].append({"path": r["path"],
                                   "score_before": r.get("score_before"),
                                   "score_after": r.get("score_after")})


def _summary_stats(values: List[float]) -> Dict:
    s = sorted(values)
    n = len(s)
    return {
        "n": n,
        "min": round(s[0], 4),
        "q25": round(s[n // 4], 4),
        "median": round(s[n // 2], 4),
        "q75": round(s[(3 * n) // 4], 4),
        "max": round(s[-1], 4),
        "mean": round(sum(values) / n, 4),
    }


def _log_progress(n_done: int, n_total: int, counts: Dict,
                  t_start: float, t_last: float, last_done: int):
    now = time.time()
    global_rate = n_done / max(1.0, now - t_start)
    rolling = (n_done - last_done) / max(0.1, now - t_last)
    eta = (n_total - n_done) / max(0.1, rolling)
    logger.info(
        "  %d/%d (%.1f%%) | rate=%.1f/s (rolling %.1f/s) | "
        "applied=%d no-op=%d skip-done=%d skip-clean=%d no-audio=%d err=%d "
        "| ETA %.1f min",
        n_done, n_total, 100.0 * n_done / n_total,
        global_rate, rolling,
        counts.get("applied", 0), counts.get("no-op", 0),
        counts.get("skip-already-done", 0), counts.get("skip-clean", 0),
        counts.get("skip-no-audio", 0) + counts.get("error", 0),
        counts.get("error", 0),
        eta / 60.0,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stats", action="store_true",
                      help="Walk + count what would be done; no writes")
    mode.add_argument("--build", action="store_true",
                      help="Run the backfill (writes to chord JSONs in place)")
    mode.add_argument("--restore", action="store_true",
                      help="Restore all .bak.beat_refiner.json snapshots back")

    ap.add_argument("--root", type=Path, default=Path("V:/data/chords"))
    ap.add_argument("--music-roots", type=Path, nargs="+",
                    default=[Path("Y:/"), Path("Z:/")])
    ap.add_argument("--workers", type=int,
                    default=max(1, (os.cpu_count() or 4) // 2))
    ap.add_argument("--max-songs", type=int, default=None,
                    help="Cap entries (smoke test)")
    ap.add_argument("--filter-noisy", action="store_true",
                    help="Skip songs whose downbeats are already regular "
                         "(cv < 0.10). Targets the 3+1 problem cases only.")
    ap.add_argument("--force", action="store_true",
                    help="Re-process songs even if they already have a "
                         "beat_refiner metadata block")
    ap.add_argument("--out-report", type=Path, default=None,
                    help="Path to write the run summary JSON. Default: stdout only.")
    args = ap.parse_args()

    cfg = {
        "mode": ("stats" if args.stats else
                 "build" if args.build else "restore"),
        "root": str(args.root),
        "music_roots": [str(p) for p in args.music_roots],
        "workers": args.workers,
        "max_songs": args.max_songs,
        "filter_noisy": args.filter_noisy,
        "force": args.force,
    }
    logger.info("config: %s", json.dumps(cfg))

    if not args.root.exists():
        logger.error("chord root does not exist: %s", args.root)
        sys.exit(2)

    if args.restore:
        summary = restore_all(args.root)
        summary["config"] = cfg
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        if args.out_report:
            args.out_report.parent.mkdir(parents=True, exist_ok=True)
            args.out_report.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return

    tasks, pre_stats = collect_tasks(
        args.root, args.music_roots, args.filter_noisy,
        args.force, args.max_songs,
    )
    logger.info("collect: %d tasks queued | pre_stats=%s",
                len(tasks), json.dumps(pre_stats))

    if args.stats:
        out = {"config": cfg, "n_tasks_would_run": len(tasks),
               "pre_stats": pre_stats}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    if not tasks:
        logger.info("no tasks to run — already up to date.")
        return

    summary = run_backfill(tasks, args.workers)
    summary["config"] = cfg
    summary["pre_stats"] = pre_stats
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
