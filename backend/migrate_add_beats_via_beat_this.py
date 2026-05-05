"""Experimental beat tracker: beat_this (CPJKU 2024) on GPU.

A drop-in alternative to migrate_add_dynamic_beats.py that swaps madmom
for beat_this — same lab, modern PyTorch transformer, runs on CUDA.
Per-song inference is ~5-10x faster vs madmom's CPU RNN.

This is an EXPERIMENT. Quality on non-Western pop / Asian content is
unknown vs madmom. Run on a small sample first (--limit 30), spot-check
results in the player, then decide whether to roll wider.

Idempotent: skips files where ``beats_source == "beat_this"``. Atomic
write via ``.tmp`` + ``os.replace``. Original chord JSON is backed up
to ``<hash>.json.bak.pre_beat_this`` on first overwrite, so you can
manually restore if the experiment fails::

    cd <chords_dir>
    for f in *.bak.pre_beat_this; do mv "$f" "${f%.bak.pre_beat_this}"; done

Sets ``beats_source = "beat_this"`` and bumps ``beat_version`` so
downstream caches (accompaniment) see the change.

Install (one-time, on PC):

    pip install git+https://github.com/CPJKU/beat_this.git

Usage::

    # Smoke test on 30 songs first (experiment)
    python migrate_add_beats_via_beat_this.py --limit 30 \\
        --chords-dir G:\\stage\\livechord\\data\\chords

    # Full run, exclude Classics+Sleep
    python migrate_add_beats_via_beat_this.py \\
        --chords-dir G:\\stage\\livechord\\data\\chords \\
        --exclude "Classics,Sleep"

    # Re-run even if already beat_this (force overwrite)
    python migrate_add_beats_via_beat_this.py --force ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from config import resolve_path  # noqa: E402

DATA_DIR = _THIS_DIR.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"

# Lazy-loaded GPU predictor (single instance per process)
_predictor = None
_predictor_device = None


def _get_predictor(device: str):
    """Load beat_this once; reuse across songs to amortize model-load cost."""
    global _predictor, _predictor_device
    if _predictor is not None and _predictor_device == device:
        return _predictor
    try:
        from beat_this.inference import File2Beats
    except ImportError as e:
        raise RuntimeError(
            f"beat_this not installed: {e}\n"
            "Install on PC:  pip install git+https://github.com/CPJKU/beat_this.git"
        )
    _predictor = File2Beats(checkpoint_path="final0", device=device, float16=True)
    _predictor_device = device
    return _predictor


def _clean_beats(beats: list[float], threshold: float = 0.05) -> list[float]:
    import numpy as np
    if len(beats) < 2:
        return beats
    arr = np.asarray(beats, dtype=float)
    diffs = np.diff(arr)
    valid_idx = np.concatenate(([True], diffs > threshold))
    return arr[valid_idx].tolist()


def _compute_bpm(beats: list[float]) -> float:
    import numpy as np
    if len(beats) < 2:
        return 0.0
    diffs = np.diff(np.asarray(beats, dtype=float))
    diffs = diffs[diffs > 0.05]  # drop spurious sub-50ms intervals
    if len(diffs) == 0:
        return 0.0
    return float(60.0 / float(np.median(diffs)))


def _compute_tempo_curve(beats: list[float], window: int = 4) -> list[dict]:
    """Per-beat smoothed tempo, matching madmom's tempo_curve schema.

    Naive instantaneous tempo = 60 / (beats[i+1] - beats[i]) is too noisy:
    beat_this occasionally emits adjacent beats <0.1s apart (likely off-beat
    doubled detection), which spikes a single point to 600+ BPM. Smooth by
    taking the rolling median of beat periods over a `window`-wide region.
    """
    import numpy as np
    if len(beats) < 2:
        return []
    arr = np.asarray(beats, dtype=float)
    diffs = np.diff(arr)
    # Drop spurious sub-50ms intervals (and the corresponding beats)
    valid = diffs > 0.05
    if not valid.all():
        keep = np.concatenate([[True], valid])
        arr = arr[keep]
        if len(arr) < 2:
            return []
        diffs = np.diff(arr)
    out = []
    half = max(1, window // 2)
    n = len(diffs)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        med = float(np.median(diffs[lo:hi]))
        if med > 0.05:
            # Schema: {"t": time_sec, "bpm": tempo} matches madmom path
            # (apply_halving_to_beat_info reads p["t"], not p["time"])
            out.append({"t": float(arr[i]), "bpm": round(60.0 / med, 2)})
    return out


def _process_one(json_path: Path, force: bool, dry_run: bool, device: str) -> str:
    try:
        sheet = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"READ_FAIL ({type(e).__name__})"

    if sheet.get("beats_source") == "beat_this" and not force:
        return "SKIP_HAS_BEAT_THIS"

    track_path = sheet.get("path")
    if not track_path:
        return "SKIP_NO_PATH"

    audio_path = resolve_path(track_path)
    if not audio_path or not os.path.isfile(audio_path):
        return "SKIP_AUDIO_MISSING"

    chords = sheet.get("chords") or []
    if not chords:
        return "SKIP_NO_CHORDS"

    # Run inference (also runs in dry-run so user can see actual output)
    pred = _get_predictor(device)
    try:
        beats_arr, downbeats_arr = pred(audio_path)
    except Exception as e:
        return f"INFER_FAIL ({type(e).__name__}: {str(e)[:80]})"

    # 1. Clean spurious beats first so they don't get saved to JSON
    beats = _clean_beats([float(x) for x in beats_arr])
    downbeats = [float(x) for x in downbeats_arr]
    if not beats:
        return "FAIL_NO_BEATS"

    bpm = _compute_bpm(beats)
    tempo_curve = _compute_tempo_curve(beats)

    # 2. Revert to conservative bpm_sanity logic
    # The Harmonic Rhythm (beats_per_chord) heuristic caused too many False Positives
    # because real fast songs (e.g. 200 BPM) can also have slow harmonic rhythm (e.g. 8 beats/chord).
    from bpm_sanity import ballad_halving_check, apply_halving_to_beat_info
    bpm_corrected, bpm_meta = ballad_halving_check(
        bpm, audio_path, bpm_range=(130.0, 220.0)
    )
    bpm_correction_record = None
    if bpm_meta.get("applied"):
        beat_info = {"tempo_curve": tempo_curve, "downbeats": downbeats}
        apply_halving_to_beat_info(beat_info)
        tempo_curve = beat_info["tempo_curve"]
        downbeats = beat_info["downbeats"]
        bpm = bpm_corrected
        bpm_correction_record = {
            "applied": True,
            "reason": bpm_meta["reason"],
            "onset_density": bpm_meta["onset_density"],
            "rms_cov": bpm_meta["rms_cov"],
            "original": bpm_meta["original"],
        }

    if dry_run:
        rng = 0.0
        if tempo_curve:
            bpms = [p["bpm"] for p in tempo_curve]
            rng = max(bpms) - min(bpms)
        halve_tag = ""
        if bpm_correction_record:
            halve_tag = f" HALVED({bpm_correction_record['original']:.1f}->{bpm:.1f})"
        return (f"DRY_RUN src=beat_this bpm={bpm:.1f} beats={len(beats)} "
                f"db={len(downbeats)} range={rng:.1f}{halve_tag}")

    # Backup original chord JSON on first overwrite (full snapshot)
    # Backup the CURRENT (pre-beat_this) state under its tracker's bak file,
    # following the convention used by beat_upgrade_queue.py:
    #   .bak.librosa  — librosa-fallback snapshot (pre-upgrade default)
    #   .bak.madmom   — madmom snapshot (post-madmom upgrade)
    #   .bak.beat_this — beat_this snapshot (post-beat_this upgrade)
    # The 3-way switch endpoint reads these to atomically swap between
    # tracker outputs without re-running inference.
    current_src = str(sheet.get("beats_source") or "").lower()
    if "madmom" in current_src:
        cur_bak = json_path.with_suffix(json_path.suffix + ".bak.madmom")
    elif "beat_this" in current_src:
        # Already beat_this (re-running with --force) — overwrite the existing
        # .bak.beat_this below; no pre-state worth preserving.
        cur_bak = None
    else:
        cur_bak = json_path.with_suffix(json_path.suffix + ".bak.librosa")
    if cur_bak is not None and not cur_bak.exists():
        try:
            cur_bak.write_text(json.dumps(sheet, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        except Exception as e:
            return f"BAK_FAIL ({type(e).__name__}: {e})"

    sheet["bpm"] = round(bpm, 1)
    sheet["beats"] = beats
    sheet["downbeats"] = downbeats
    sheet["tempo_curve"] = tempo_curve
    sheet["beats_source"] = "beat_this"
    sheet["beat_version"] = int(sheet.get("beat_version", 0)) + 1
    if bpm_correction_record is not None:
        sheet["bpm_correction"] = bpm_correction_record
    elif "bpm_correction" in sheet:
        # Wipe stale halving record if previous run halved but this run didn't
        sheet.pop("bpm_correction", None)

    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(sheet, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, json_path)
    except Exception as e:
        return f"WRITE_FAIL ({type(e).__name__}: {e})"

    # Cache the new beat_this state for fast switch via the 3-way endpoint.
    bt_bak = json_path.with_suffix(json_path.suffix + ".bak.beat_this")
    try:
        bt_bak.write_text(json.dumps(sheet, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except Exception as e:
        # Non-fatal — main JSON already written; just log via return tag
        pass

    rng = 0.0
    if tempo_curve:
        bpms = [p["bpm"] for p in tempo_curve]
        rng = max(bpms) - min(bpms)
    halve_tag = ""
    if bpm_correction_record:
        halve_tag = f" HALVED({bpm_correction_record['original']:.1f}->{bpm:.1f})"
    return (f"OK bpm={bpm:.1f} beats={len(beats)} db={len(downbeats)} "
            f"range={rng:.1f}{halve_tag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Run inference but don't write back. Reports beats/bpm.")
    ap.add_argument("--force", action="store_true",
                    help="Re-process files already on beat_this (default: skip).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N files (default: all).")
    ap.add_argument("--only", type=str, default="",
                    help="Substring filter on chord JSON's `path` field.")
    ap.add_argument("--exclude", type=str, default="",
                    help="Comma-separated case-insensitive substring blacklist.")
    ap.add_argument("--start-from", type=str, default="",
                    help="Skip files whose name sorts before this.")
    ap.add_argument("--device", type=str, default="cuda",
                    help="cuda / cpu (default cuda).")
    ap.add_argument("--chords-dir", type=str, default="",
                    help="Override chord JSON directory (default: ../data/chords). "
                         "Use this for staging workflow on local SSD.")
    args = ap.parse_args()

    chords_dir = Path(args.chords_dir).resolve() if args.chords_dir else CHORDS_DIR
    if not chords_dir.is_dir():
        print(f"ERROR: {chords_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # Sharded layout: <chords_dir>/<bucket>/<hash>.json (recurse 1 level)
    files = sorted(chords_dir.glob("*/*.json"))
    if not files:
        # Legacy flat fallback (pre-sharding migration)
        files = sorted(chords_dir.glob("*.json"))
    print(f"Found {len(files)} chord JSON files in {chords_dir}")
    if args.dry_run:
        print("DRY RUN — inference runs but JSON not written back")

    if args.only or args.exclude:
        only_n = args.only.lower() if args.only else ""
        ex_n = ([s.strip().lower() for s in args.exclude.split(",") if s.strip()]
                if args.exclude else [])
        kept = []
        for f in files:
            try:
                p = json.loads(f.read_text(encoding="utf-8")).get("path", "").lower()
            except Exception:
                continue
            if only_n and only_n not in p:
                continue
            if ex_n and any(n in p for n in ex_n):
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

    counts: dict[str, int] = {}
    t0 = time.time()
    n = len(files)
    for i, f in enumerate(files, 1):
        st = _process_one(f, args.force, args.dry_run, args.device)
        key = st.split(" ", 1)[0]
        counts[key] = counts.get(key, 0) + 1
        elapsed = time.time() - t0
        rate = i / elapsed if elapsed > 0 else 0
        eta = (n - i) / rate / 60 if rate > 0 else 0
        print(f"[{i}/{n}] {f.name}: {st}  ({rate:.2f}/s, ETA {eta:.0f}m)")
        
        # Prevent PyTorch VRAM fragmentation over thousands of files
        if i % 50 == 0 and args.device == "cuda":
            import torch
            torch.cuda.empty_cache()

    print("\n=== Summary ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
