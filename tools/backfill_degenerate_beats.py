"""Backfill beat tracking for songs with degenerate beat data.

Targets the long tail of chord JSONs where the original ingest only ran
librosa-fallback (no usable beats[] / downbeats[]) — common for songs added
before madmom or beat_this came online. Re-runs madmom (or beat_this via
Modal if configured) on PC compute so the NUC isn't tied up, then writes
back atomically to V:\\data\\chords.

Why a separate script (not migrate_add_dynamic_beats.py)
--------------------------------------------------------
That existing script processes ALL chord JSONs and is idempotent on
beats_source == "madmom". This one is **targeted**: pre-scans to identify
the degenerate subset (much smaller, predictable set), gives live progress
+ ETA, writes errors to a separate log file, and is safe to run repeatedly
(re-scans on each run, only processes what's still degenerate).

Usage (run from project root)
-----------------------------
    # Dry-run: scan + report what would be processed, no writes
    python tools/backfill_degenerate_beats.py --dry-run

    # Process all degenerate songs sequentially (madmom, ~30s/song)
    python tools/backfill_degenerate_beats.py

    # Parallel (madmom uses ~500MB RAM per worker; 2-4 is safe on dev PC)
    python tools/backfill_degenerate_beats.py --workers 3

    # Process N songs (smoke test)
    python tools/backfill_degenerate_beats.py --limit 5

    # Process a specific song by hash (debug)
    python tools/backfill_degenerate_beats.py --hash ca4c475972c7

    # Override what counts as "degenerate" (default: empty beats OR empty
    # downbeats OR beats_source=='librosa-fallback' OR beat_version<=1)
    python tools/backfill_degenerate_beats.py --include-version-2

    # Beat tracker selection
    python tools/backfill_degenerate_beats.py --tracker beat_this  # via Modal

Output
------
- Live progress line: [N/total] hash status (rate/s, ETA Xm)
- Summary at end: counts per status
- Error log: tools/backfill_errors_<timestamp>.log — full traceback per failure
- Safe to Ctrl+C: per-file write is atomic; resume via re-running

Requirements
------------
- madmom (default): pip install via the gauntlet documented in CLAUDE.md
  (MSVC Build Tools + Cython/numpy + git master --no-build-isolation)
- beat_this (--tracker beat_this): requires `modal` package + credentials
- All chord JSONs accessible on V:\\data\\chords (or pass --chords-dir)
- Source audio accessible via the music_roots resolved by config.resolve_path
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add backend/ to sys.path so we can import the analysis utilities.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from beat_snap import analyze_and_snap_dynamic, BEAT_VERSION, HAS_MADMOM  # noqa: E402
from config import resolve_path  # noqa: E402

DATA_DIR = _REPO_ROOT / "data"
DEFAULT_CHORDS_DIR = DATA_DIR / "chords"


# ---------------------------------------------------------------------------
# Status constants — short, grep-friendly strings used in counts + log
# ---------------------------------------------------------------------------
S_OK = "OK"
S_DRY = "DRY_RUN"
S_SKIP_GOOD = "SKIP_HAS_BEATS"
S_SKIP_NO_PATH = "SKIP_NO_PATH"
S_SKIP_NO_AUDIO = "SKIP_AUDIO_MISSING"
S_SKIP_NO_CHORDS = "SKIP_NO_CHORDS"
S_FAIL_READ = "FAIL_READ"
S_FAIL_NO_BEATS = "FAIL_NO_BEATS_DETECTED"
S_FAIL_TRACKER = "FAIL_TRACKER_EXCEPTION"
S_FAIL_WRITE = "FAIL_WRITE"
S_FAIL_BEAT_THIS_UNAVAILABLE = "FAIL_BEAT_THIS_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Detection: does this chord JSON need backfill?
# ---------------------------------------------------------------------------
def is_degenerate(sheet: dict, *, include_old_version: bool = False,
                  include_version_2: bool = False) -> bool:
    """Return True if the chord sheet's beat data is missing or low-quality.

    Default criteria (conservative — only TRULY broken songs):
      - beats[] is empty
      - downbeats[] is empty
      - beats_source contains 'librosa' (covers 'librosa', 'librosa-fallback')

    With --include-old-version: ALSO flag beat_version <= 1 songs (older
    schema; pre-Phase-0 backfill). May include songs whose beats are fine
    but never got their version bumped — re-runs madmom over them too.

    With --include-version-2: ALSO flag beat_version == 2 songs (already
    upgraded once). For corpus-wide refinement after a tracker upgrade.
    """
    if not sheet.get("beats"):
        return True
    if not sheet.get("downbeats"):
        return True
    src = (sheet.get("beats_source") or "").lower()
    if "librosa" in src:
        return True
    bv = int(sheet.get("beat_version") or 0)
    if include_old_version and bv <= 1:
        return True
    if include_version_2 and bv == 2:
        return True
    return False


# ---------------------------------------------------------------------------
# Worker: process one chord JSON
# ---------------------------------------------------------------------------
def _process_one(json_path: Path, *, dry_run: bool, tracker: str,
                 backup_current: bool) -> tuple[str, str, Optional[str]]:
    """Process a single chord JSON. Returns (status_key, summary, error_trace).

    error_trace is None for non-error statuses; populated with traceback for
    FAIL_TRACKER_EXCEPTION to write to the error log.
    """
    try:
        sheet = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (S_FAIL_READ, f"{type(exc).__name__}: {exc}",
                traceback.format_exc())

    track_path = sheet.get("path")
    if not track_path:
        return (S_SKIP_NO_PATH, "no path field", None)

    audio_path = resolve_path(track_path)
    if not audio_path or not os.path.isfile(audio_path):
        return (S_SKIP_NO_AUDIO, f"missing: {audio_path or '(unresolved)'}", None)

    chords = sheet.get("chords") or []
    if not chords:
        return (S_SKIP_NO_CHORDS, "empty chords", None)

    # Run the chosen tracker on a deep copy of chords so a mid-run exception
    # doesn't corrupt the in-memory sheet before atomic write.
    chords_copy = [dict(c) for c in chords]
    try:
        if tracker == "beat_this":
            try:
                from modal_beat_this import detect_beats_via_modal  # type: ignore
            except Exception as exc:
                return (S_FAIL_BEAT_THIS_UNAVAILABLE,
                        f"modal package or modal_beat_this not importable: {exc}",
                        None)
            info = detect_beats_via_modal(audio_path)
            if info.get("beats"):
                # beat_this returns flat dict; snap chords to its grid (mirror
                # the beat_upgrade_queue.py:226 path).
                try:
                    import numpy as np
                    from beat_snap import _snap_to_grid
                    _snap_to_grid(chords_copy,
                                  np.sort(np.asarray(info["beats"], dtype=np.float64)))
                except Exception:
                    pass  # snap is best-effort; raw beats still usable
        else:
            info = analyze_and_snap_dynamic(audio_path, chords_copy,
                                            prefer_madmom=True)
    except Exception as exc:
        return (S_FAIL_TRACKER, f"{type(exc).__name__}: {exc}",
                traceback.format_exc())

    if not info.get("beats_source") or not info.get("beats"):
        return (S_FAIL_NO_BEATS,
                f"tracker returned no beats (src={info.get('beats_source')!r})",
                None)

    n_beats = len(info.get("beats", []))
    n_db = len(info.get("downbeats", []))
    src = info["beats_source"]
    bpm_val = info.get("bpm") or 0

    if dry_run:
        return (S_DRY,
                f"src={src} bpm={bpm_val} beats={n_beats} db={n_db}",
                None)

    # ---- Atomic write ----
    try:
        # Backup current state (likely the librosa-fallback or empty data) so
        # the user can switch back via /api/process/beats/switch?mode=librosa
        # or roll back manually if needed.
        if backup_current:
            prev_src = (sheet.get("beats_source") or "librosa").split("-")[0]
            bak_path = Path(str(json_path) + f".bak.{prev_src}")
            if not bak_path.exists():
                bak_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

        # Apply tracker output
        sheet["chords"] = chords_copy
        if bpm_val:
            sheet["bpm"] = round(float(bpm_val), 1)
        sheet["beats"] = info.get("beats", [])
        sheet["downbeats"] = info.get("downbeats", [])
        sheet["tempo_curve"] = info.get("tempo_curve", [])
        sheet["beats_source"] = src
        # Bump beat_version so the player's stale-acc cache invalidates and
        # the chord_api serve-time meter detector re-runs against fresh data.
        prev_v = int(sheet.get("beat_version") or 0)
        sheet["beat_version"] = max(prev_v + 1, BEAT_VERSION)
        if info.get("bpm_correction") is not None:
            sheet["bpm_correction"] = info["bpm_correction"]

        tmp = json_path.with_suffix(json_path.suffix + ".tmp")
        tmp.write_text(json.dumps(sheet, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, json_path)
    except Exception as exc:
        return (S_FAIL_WRITE, f"{type(exc).__name__}: {exc}",
                traceback.format_exc())

    return (S_OK,
            f"src={src} bpm={bpm_val} beats={n_beats} db={n_db}",
            None)


def _worker_entry(args_tuple):
    json_path, dry_run, tracker, backup_current = args_tuple
    status, summary, err = _process_one(json_path, dry_run=dry_run,
                                         tracker=tracker,
                                         backup_current=backup_current)
    return (json_path.name, status, summary, err)


# ---------------------------------------------------------------------------
# Pre-scan: walk chord JSONs, identify degenerate set
# ---------------------------------------------------------------------------
def _quick_degenerate_prefilter(text: str) -> bool:
    """Fast string-based check before full JSON parse.

    Returns True if the file is OBVIOUSLY non-degenerate (has substantive
    `beats` array AND `beats_source` is not librosa-fallback). Conservative:
    when in doubt, defers to the full parse.
    """
    # If the text contains "librosa-fallback" anywhere, treat as degenerate
    # candidate — needs full parse to confirm.
    if '"librosa-fallback"' in text or '"librosa"' in text[:2048]:
        return False
    # If the file claims beats_source madmom or beat_this AND has a
    # substantive beats array (>= 100 entries means at least the first 1KB
    # of the array is populated), it's almost certainly NOT degenerate.
    if ('"beats_source": "madmom"' in text or
        '"beats_source": "beat_this' in text):
        # Cheap proxy for "beats array is non-empty" — look for at least
        # 50 chars of beat numbers in the beats[] field.
        b_idx = text.find('"beats":')
        if b_idx >= 0:
            # Look at next ~500 chars after "beats": for non-empty array
            chunk = text[b_idx:b_idx + 500]
            if '"beats": []' not in chunk and '"beats":[]' not in chunk:
                return True
    return False


def scan_degenerate(chords_dir: Path, *, include_old_version: bool,
                    include_version_2: bool,
                    only_hash: Optional[str] = None,
                    only_substr: Optional[str] = None,
                    progress_every: int = 1000) -> list[Path]:
    """Walk chords_dir and return chord JSON paths that need backfill.

    Two-pass for speed on slow SMB filesystems:
      1. Read raw text and run a string pre-filter to skip obviously-good files
      2. JSON-parse only the candidates that survive the prefilter
    """
    files = sorted(chords_dir.glob("*/*.json"))
    if not files:
        files = sorted(chords_dir.glob("*.json"))
    if only_hash:
        files = [f for f in files if f.stem == only_hash]
    total = len(files)
    out: list[Path] = []
    skipped_fast = 0
    parsed = 0
    t_start = time.time()
    for i, f in enumerate(files, 1):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if _quick_degenerate_prefilter(text):
            skipped_fast += 1
        else:
            try:
                sheet = json.loads(text)
            except Exception:
                continue
            parsed += 1
            if only_substr:
                p = (sheet.get("path") or "").lower()
                if only_substr.lower() not in p:
                    continue
            if is_degenerate(sheet, include_old_version=include_old_version,
                             include_version_2=include_version_2):
                out.append(f)
        if i % progress_every == 0 or i == total:
            elapsed = time.time() - t_start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"  scan {i}/{total} ({rate:.0f}/s, ETA {eta:.0f}s) — "
                  f"degenerate={len(out)} fastpath={skipped_fast} parsed={parsed}",
                  flush=True)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Backfill madmom/beat_this beat tracking for degenerate chord JSONs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write back. Reports what would change.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N files (default: all).")
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel worker processes (default 1). madmom uses "
                         "~500MB RAM per worker.")
    ap.add_argument("--tracker", choices=["madmom", "beat_this"], default="madmom",
                    help="Which beat tracker to use. madmom (default, local CPU) "
                         "or beat_this (Modal cloud GPU; requires `modal` package).")
    ap.add_argument("--hash", type=str, default="",
                    help="Process only the chord JSON with this 12-char hash.")
    ap.add_argument("--only", type=str, default="",
                    help="Substring filter on the chord JSON's `path` field.")
    ap.add_argument("--include-old-version", action="store_true",
                    help="Also flag beat_version<=1 songs (older schema, may "
                         "have intact beats just not bumped). Expands the work "
                         "set substantially. Default: only flag truly broken.")
    ap.add_argument("--include-version-2", action="store_true",
                    help="Also re-process songs with beat_version==2 (already "
                         "upgraded once). Default: skip them.")
    ap.add_argument("--chords-dir", type=str, default="",
                    help="Override the chord JSON directory (default: data/chords). "
                         "Useful for staging on local SSD before syncing back to V:\\.")
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip writing .bak.<prev_source> sidecar before overwriting. "
                         "Default behavior keeps the backup so you can roll back.")
    ap.add_argument("--error-log", type=str, default="",
                    help="Path for the error trace log (default: tools/backfill_errors_<ts>.log).")
    args = ap.parse_args()

    if args.tracker == "madmom" and not HAS_MADMOM:
        print("ERROR: madmom is not installed on this Python.", file=sys.stderr)
        print("       See CLAUDE.md 'madmom install on Windows' for the install gauntlet.",
              file=sys.stderr)
        print("       Or use --tracker beat_this (requires `modal` package).",
              file=sys.stderr)
        sys.exit(1)

    chords_dir = Path(args.chords_dir).resolve() if args.chords_dir else DEFAULT_CHORDS_DIR
    if not chords_dir.is_dir():
        print(f"ERROR: chord JSON directory not found: {chords_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {chords_dir} for degenerate chord JSONs...", flush=True)
    t_scan = time.time()
    files = scan_degenerate(
        chords_dir,
        include_old_version=args.include_old_version,
        include_version_2=args.include_version_2,
        only_hash=args.hash or None,
        only_substr=args.only or None,
    )
    print(f"Scan done in {time.time() - t_scan:.1f}s — {len(files)} songs need backfill",
          flush=True)

    if args.limit > 0:
        files = files[:args.limit]
        print(f"--limit applied: processing {len(files)} files", flush=True)

    if not files:
        print("Nothing to process. Exiting.", flush=True)
        return

    if args.dry_run:
        print("DRY RUN — no writes will occur", flush=True)

    # Set up error log
    if args.error_log:
        error_log_path = Path(args.error_log)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        error_log_path = Path(__file__).parent / f"backfill_errors_{ts}.log"
    error_log_fp = open(error_log_path, "w", encoding="utf-8")
    error_log_fp.write(f"Backfill error log — started {datetime.now().isoformat()}\n")
    error_log_fp.write(f"Tracker: {args.tracker}\n")
    error_log_fp.write(f"Chords dir: {chords_dir}\n")
    error_log_fp.write(f"Files queued: {len(files)}\n")
    error_log_fp.write("=" * 78 + "\n\n")
    error_log_fp.flush()

    counts: dict[str, int] = {}
    t0 = time.time()
    total_n = len(files)
    backup = not args.no_backup
    payload = [(f, args.dry_run, args.tracker, backup) for f in files]

    def emit(idx: int, fname: str, status: str, summary: str, err: Optional[str]):
        counts[status] = counts.get(status, 0) + 1
        elapsed = time.time() - t0
        rate = idx / elapsed if elapsed > 0 else 0
        eta_s = (total_n - idx) / rate if rate > 0 else 0
        eta_label = (f"{eta_s / 60:.1f}m" if eta_s < 3600
                     else f"{eta_s / 3600:.1f}h")
        marker = "✓" if status in (S_OK, S_DRY) else ("·" if status.startswith("SKIP") else "✗")
        print(f"[{idx}/{total_n}] {marker} {fname[:14]} {status} — {summary}  "
              f"({rate:.2f}/s, ETA {eta_label})", flush=True)
        if err:
            error_log_fp.write(f"[{idx}/{total_n}] {fname}\n")
            error_log_fp.write(f"  status: {status}\n")
            error_log_fp.write(f"  summary: {summary}\n")
            error_log_fp.write(err)
            error_log_fp.write("\n" + "-" * 78 + "\n\n")
            error_log_fp.flush()

    if args.workers <= 1:
        for i, p in enumerate(payload, 1):
            fname, status, summary, err = _worker_entry(p)
            emit(i, fname, status, summary, err)
    else:
        print(f"Using {args.workers} worker processes "
              f"(~{args.workers * 500}MB RAM expected)", flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(_worker_entry, p) for p in payload]
            for i, fut in enumerate(as_completed(futures), 1):
                fname, status, summary, err = fut.result()
                emit(i, fname, status, summary, err)

    error_log_fp.write(f"\nFinished {datetime.now().isoformat()}\n")
    error_log_fp.write(f"Counts: {counts}\n")
    error_log_fp.close()

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
    print(f"  error log     : {error_log_path}")
    if fail_n > 0:
        print()
        print(f"  ⚠ {fail_n} failures — see error log for tracebacks")
        sys.exit(2)


if __name__ == "__main__":
    main()
