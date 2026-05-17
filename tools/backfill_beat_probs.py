"""Backfill beat_refiner Sigmoid confidences (Phase 1, LiveChord-sfw).

Walks the chord JSON corpus and runs the trained beat_refiner against each
song's audio to extract per-peak `beat_probs[]` and `downbeat_probs[]`
parallel to the existing `beats[]` / `downbeats[]` arrays. Additive only:
NEVER replaces beats/downbeats — only the confidence arrays are written.

Why a separate script
---------------------
The Phase 1 ingest hook (`_run_beat_refiner_into_sheet` in process_queue.py)
only writes probs when `phase1_refine` actually applies (gate 4 passes).
This script does the same dispatch for ALL existing chord JSONs without
touching their grids — songs that were rejected at gate 4 just don't get
probs (Phase 1b will revisit). Songs that pass get their confidence arrays
filled in so chord_splitter can gate compound 6/8 bar-snap.

Length safety: we only persist probs whose length EXACTLY matches the
existing `beats[]` / `downbeats[]` in the JSON. If the refiner re-pickes
a different count of peaks, the JSON is left untouched and we log a SKIP.
This guarantees `beat_probs[i]` always describes `beats[i]` and so on.

Usage (from project root)
-------------------------
    # Dry-run: report what would be written, no writes
    python tools/backfill_beat_probs.py --dry-run

    # Full corpus, single CPU worker
    python tools/backfill_beat_probs.py

    # 4 CPU workers (the script defaults to forcing CPU — see OOM guard
    # below; ~3M-param Transformer is fine on CPU for offline batch)
    python tools/backfill_beat_probs.py --workers 4

    # Single song by hash (debug)
    python tools/backfill_beat_probs.py --hash 0000d0e0e8e4

    # Limit smoke test
    python tools/backfill_beat_probs.py --limit 5

    # Force re-process even if probs already populated and aligned
    python tools/backfill_beat_probs.py --force

OOM guard
---------
The beat_refiner model is a ~3M-param Compact Transformer. Multi-worker
runs that put N model copies on the same GPU hit CUDA OOM. This script
forces CPU by default (`CUDA_VISIBLE_DEVICES=""`) before any torch import
so workers spawn cleanly on multi-core CPU. Use `--gpu` to opt in to GPU
mode — recommended only with `--workers 1`. Workers are spawned with
`multiprocessing.set_start_method("spawn")` to avoid CUDA context
inheritance bugs on fork-based platforms.

Output
------
- Live progress line per file
- Summary counts at end
- Atomic per-file write (.tmp + os.replace) — Ctrl+C safe
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
# Both paths needed: backend/ for bare `ai.beat_refiner_infer` / `config`
# imports, repo root for the `backend.ai.X` namespaced imports that
# beat_refiner_infer uses internally to load the model module.
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DATA_DIR = _REPO_ROOT / "data"
DEFAULT_CHORDS_DIR = DATA_DIR / "chords"


S_OK = "OK"
S_DRY = "DRY_RUN"
S_SKIP_ALREADY_POPULATED = "SKIP_HAS_PROBS"
S_SKIP_PROTECTED_PEAK_PICK = "SKIP_PROTECTED_PEAK_PICK"
S_SKIP_NO_PATH = "SKIP_NO_PATH"
S_SKIP_NO_AUDIO = "SKIP_AUDIO_MISSING"
S_SKIP_NO_BEATS = "SKIP_NO_BEATS"
S_SKIP_REFINER_BAILED = "SKIP_REFINER_BAILED"
S_SKIP_LENGTH_MISMATCH = "SKIP_LENGTH_MISMATCH"
S_FAIL_READ = "FAIL_READ"
S_FAIL_REFINER = "FAIL_REFINER_EXCEPTION"
S_FAIL_WRITE = "FAIL_WRITE"

MODE_GRID = "grid"
MODE_PEAK_PICK = "peak_pick"


def _has_aligned_probs(sheet: dict) -> bool:
    beats = sheet.get("beats") or []
    downbeats = sheet.get("downbeats") or []
    beat_probs = sheet.get("beat_probs") or []
    downbeat_probs = sheet.get("downbeat_probs") or []
    return (
        bool(beats) and bool(downbeats)
        and len(beat_probs) == len(beats)
        and len(downbeat_probs) == len(downbeats)
    )


def _process_one(json_path: Path, *, dry_run: bool, force: bool,
                 mode: str, force_overwrite_peak_pick: bool,
                 ) -> Tuple[str, str, Optional[str]]:
    """Return (status_key, summary, error_trace)."""
    try:
        sheet = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (S_FAIL_READ, f"{type(exc).__name__}: {exc}",
                traceback.format_exc())

    if not force and _has_aligned_probs(sheet):
        return (S_SKIP_ALREADY_POPULATED,
                f"beats={len(sheet.get('beats', []))} "
                f"downbeats={len(sheet.get('downbeats', []))}",
                None)

    # Belt-and-braces: even with --force, refuse to downgrade Phase 1
    # peak_pick probs to sampled probs unless the operator explicitly opts
    # in via --force-overwrite-peak-pick. Peak-pick probs are sigmoid local
    # maxima above 0.5; sampled probs land wherever the beat_this grid puts
    # them and typically distribute lower. Replacing them blindly would be
    # a silent quality regression on the 75% of the corpus that Phase 1
    # already covered.
    if (force and mode == MODE_GRID
            and not force_overwrite_peak_pick
            and sheet.get("prob_source") == "peak_pick"):
        return (S_SKIP_PROTECTED_PEAK_PICK,
                "Phase 1 peak_pick probs present; pass "
                "--force-overwrite-peak-pick to replace",
                None)

    beats = sheet.get("beats") or []
    downbeats = sheet.get("downbeats") or []
    if not beats or not downbeats:
        return (S_SKIP_NO_BEATS,
                f"beats={len(beats)} downbeats={len(downbeats)}",
                None)

    track_path = sheet.get("path")
    if not track_path:
        return (S_SKIP_NO_PATH, "no path field", None)

    try:
        from config import resolve_path  # late import (sys.path injected above)
    except Exception as exc:
        return (S_FAIL_REFINER, f"config import: {exc}",
                traceback.format_exc())
    audio_path = resolve_path(track_path)
    if not audio_path or not os.path.isfile(audio_path):
        return (S_SKIP_NO_AUDIO,
                f"missing: {audio_path or '(unresolved)'}", None)

    try:
        from ai.beat_refiner_infer import (  # noqa: WPS433
            refine, sample_probs_at_grid,
        )
    except Exception as exc:
        return (S_FAIL_REFINER, f"refine import: {exc}",
                traceback.format_exc())

    try:
        if mode == MODE_GRID:
            res = sample_probs_at_grid(audio_path, beats, downbeats)
        else:
            res = refine(audio_path, beats, downbeats)
    except Exception as exc:
        return (S_FAIL_REFINER, f"{type(exc).__name__}: {exc}",
                traceback.format_exc())

    if not res.get("applied"):
        return (S_SKIP_REFINER_BAILED, str(res.get("reason", "?")), None)

    beat_probs = res.get("beat_probs") or []
    downbeat_probs = res.get("downbeat_probs") or []

    # CRITICAL length guarantee: only persist when prob counts exactly match
    # the existing beats/downbeats arrays. In MODE_GRID this is guaranteed
    # by construction (sample_probs_at_grid output lengths = input lengths);
    # if it ever fires we want the loud signal that something is wrong with
    # _sample(). In MODE_PEAK_PICK this is the original Phase 1 skip branch
    # that triggered the need for Phase 1b in the first place.
    if len(beat_probs) != len(beats) or len(downbeat_probs) != len(downbeats):
        return (S_SKIP_LENGTH_MISMATCH,
                f"refiner re-picked {len(beat_probs)}/{len(downbeat_probs)} "
                f"vs existing {len(beats)}/{len(downbeats)}",
                None)

    prob_source = ("sample_at_grid" if mode == MODE_GRID else "peak_pick")
    summary = (f"applied mode={mode} beats={len(beats)} "
               f"downbeats={len(downbeats)} "
               f"elapsed={res.get('elapsed_sec', 0):.2f}s")

    if dry_run:
        return (S_DRY, summary, None)

    try:
        sheet["beat_probs"] = list(beat_probs)
        sheet["downbeat_probs"] = list(downbeat_probs)
        sheet["prob_source"] = prob_source
        tmp = json_path.with_suffix(json_path.suffix + ".tmp")
        tmp.write_text(json.dumps(sheet, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, json_path)
    except Exception as exc:
        return (S_FAIL_WRITE, f"{type(exc).__name__}: {exc}",
                traceback.format_exc())

    return (S_OK, summary, None)


def _worker_entry(args_tuple):
    json_path, dry_run, force, mode, force_overwrite_peak_pick = args_tuple
    status, summary, err = _process_one(
        json_path, dry_run=dry_run, force=force, mode=mode,
        force_overwrite_peak_pick=force_overwrite_peak_pick,
    )
    return (json_path.name, status, summary, err)


def _walk_chord_jsons(chords_dir: Path,
                      only_hash: Optional[str] = None) -> list[Path]:
    files = sorted(chords_dir.glob("*/*.json"))
    if not files:
        files = sorted(chords_dir.glob("*.json"))
    if only_hash:
        files = [f for f in files if f.stem == only_hash]
    return files


def main():
    ap = argparse.ArgumentParser(
        description="Backfill beat_refiner Sigmoid confidences into chord JSONs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write back.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N files (default: all).")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel CPU worker processes (default 1).")
    ap.add_argument("--hash", type=str, default="",
                    help="Process only the chord JSON with this hash.")
    ap.add_argument("--chords-dir", type=str, default="",
                    help="Override the chord JSON directory.")
    ap.add_argument("--force", action="store_true",
                    help="Re-process even when probs are already populated "
                         "and aligned with beats/downbeats.")
    ap.add_argument("--mode", choices=[MODE_GRID, MODE_PEAK_PICK],
                    default=MODE_GRID,
                    help="grid (default, Phase 1b): sample sigmoid probs at "
                         "the existing beats/downbeats grid — length match "
                         "guaranteed by construction. peak_pick (Phase 1 "
                         "legacy): re-run peak picking; trips "
                         "SKIP_LENGTH_MISMATCH for ~25%% of the corpus.")
    ap.add_argument("--force-overwrite-peak-pick", action="store_true",
                    help="With --force --mode grid, also overwrite chord "
                         "JSONs whose prob_source is already 'peak_pick'. "
                         "Off by default: sampled probs typically distribute "
                         "lower than peak-picked probs, so replacing them "
                         "is a silent quality regression unless intentional.")
    ap.add_argument("--gpu", action="store_true",
                    help="Allow GPU usage. Default is CPU-only to keep "
                         "multi-worker runs safe from CUDA OOM. With --gpu, "
                         "stick to --workers 1.")
    args = ap.parse_args()

    # OOM guard: hide GPU BEFORE any torch import so model loads on CPU in
    # both the parent and spawned worker processes. Override via --gpu.
    if not args.gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    chords_dir = (
        Path(args.chords_dir).resolve() if args.chords_dir else DEFAULT_CHORDS_DIR
    )
    if not chords_dir.is_dir():
        print(f"ERROR: chord JSON directory not found: {chords_dir}",
              file=sys.stderr)
        sys.exit(1)

    print(f"Walking {chords_dir} ...", flush=True)
    files = _walk_chord_jsons(chords_dir, only_hash=args.hash or None)
    if not files:
        print("No chord JSONs found.", flush=True)
        return

    if args.limit > 0:
        files = files[:args.limit]
    print(f"Queued {len(files)} chord JSONs", flush=True)
    if args.dry_run:
        print("DRY RUN — no writes", flush=True)
    print(f"Mode: {args.mode}  GPU enabled: {args.gpu}  "
          f"Workers: {args.workers}", flush=True)
    if args.force and args.mode == MODE_GRID and not args.force_overwrite_peak_pick:
        print("(--force will skip prob_source='peak_pick' JSONs; pass "
              "--force-overwrite-peak-pick to override)", flush=True)

    counts: dict[str, int] = {}
    t0 = time.time()
    total_n = len(files)
    payload = [
        (f, args.dry_run, args.force, args.mode, args.force_overwrite_peak_pick)
        for f in files
    ]

    def emit(idx: int, fname: str, status: str, summary: str,
             err: Optional[str]):
        counts[status] = counts.get(status, 0) + 1
        elapsed = time.time() - t0
        rate = idx / elapsed if elapsed > 0 else 0
        eta_s = (total_n - idx) / rate if rate > 0 else 0
        eta_label = (f"{eta_s / 60:.1f}m" if eta_s < 3600
                     else f"{eta_s / 3600:.1f}h")
        marker = (
            "OK" if status in (S_OK, S_DRY)
            else ("·" if status.startswith("SKIP") else "X")
        )
        print(f"[{idx}/{total_n}] {marker} {fname[:14]} {status} — "
              f"{summary}  ({rate:.2f}/s, ETA {eta_label})", flush=True)
        if err:
            print(err, file=sys.stderr)

    if args.workers <= 1:
        for i, p in enumerate(payload, 1):
            fname, status, summary, err = _worker_entry(p)
            emit(i, fname, status, summary, err)
    else:
        # spawn start method avoids CUDA context inheritance on Linux fork
        # and matches Windows' default — safer + consistent across platforms.
        try:
            multiprocessing.set_start_method("spawn", force=False)
        except RuntimeError:
            pass  # already set
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_worker_entry, p) for p in payload]
            for i, fut in enumerate(as_completed(futures), 1):
                fname, status, summary, err = fut.result()
                emit(i, fname, status, summary, err)

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<32} {v}")
    print(f"  {'-' * 40}")
    ok_n = counts.get(S_OK, 0) + counts.get(S_DRY, 0)
    fail_n = sum(v for k, v in counts.items() if k.startswith("FAIL"))
    skip_n = sum(v for k, v in counts.items() if k.startswith("SKIP"))
    print(f"  total ok/dry  : {ok_n}")
    print(f"  total fail    : {fail_n}")
    print(f"  total skip    : {skip_n}")
    print(f"  elapsed       : {time.time() - t0:.1f}s")
    if fail_n > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
