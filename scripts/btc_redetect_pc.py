"""PC-side BTC re-detection for tracks missing chord JSON.

After the source=midi quarantine (LiveChord-a7c) ~10,000 library tracks
have no chord JSON on disk. The NUC auto_worker would re-run BTC on
them eventually, but the user wants to drive the GPU on their PC
instead while the NUC 8800 service is paused. Stopping NUC lets this
script have exclusive write access to ``V:\\data\\chords\\``.

Output is byte-identical to what backend/auto_worker.py writes:
  * source = "btc"
  * BTC chords + key
  * beat_snap (madmom / librosa) BPM + beats + downbeats + tempo_curve
  * Optional phase1 beat_refiner (gated on `beat_refiner_enabled`
    setting, same as auto_worker)
  * chord_index.json incrementally updated via cache_update_entry

Usage:
    python scripts/btc_redetect_pc.py            # process every missing track
    python scripts/btc_redetect_pc.py --limit 50 # smoke-test first
    python scripts/btc_redetect_pc.py --skip 1000 --limit 500
    python scripts/btc_redetect_pc.py --from-manifest V:/data/chords_midi_quarantine/_manifest_1778760866.json

Prerequisites:
  * NUC 8800 stopped (else two processes will race on chord_index.json)
  * V:\\ + Z:\\ (NAS) mounted and reachable
  * CUDA-enabled torch + the backend BTC environment importable
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

# Mount the backend module surface so we use the same code paths as
# auto_worker — chord JSON shape stays identical.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _patch_data_root(data_root: Path) -> None:
    """Backend modules hard-code ``DATA_DIR = repo/data``. Override that
    so PC scripts can write to the NUC's V:\\data\\ directory.

    Must run BEFORE any backend module is imported, otherwise the
    constants captured at import time stick to the original path."""
    import config as _config
    import data_cache as _dc
    _config.DATA_DIR = data_root
    _dc.DATA_DIR = data_root
    # Re-bind derived constants in data_cache.
    _dc.CACHE_FILE = data_root / "library_cache.json"
    _dc.CHORDS_DIR = data_root / "chords"
    # chord_cache imports DATA_DIR at module load — patch its copy too.
    import chord_cache as _cc
    _cc.DATA_DIR = data_root
    _cc.INDEX_FILE = data_root / "chord_index.json"
    _cc.CHORDS_DIR = data_root / "chords"


def _load_settings() -> dict:
    """Settings drive beat_refiner_enabled (and any future toggles)."""
    try:
        from auto_worker import load_settings
        return load_settings()
    except Exception:
        try:
            with open(REPO_ROOT.parent / "data" / "settings_personal.json", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def _detect_one(track_path: str, settings: dict) -> dict:
    """Replicate auto_worker._auto_chord_detect_loop body for one track."""
    from chord_detect import detect_chords_and_key_isolated
    from chord_cache import song_hash, chord_file_for, ensure_chord_bucket, update_entry_from_file
    from config import resolve_path

    full = resolve_path(track_path)
    if not full or not os.path.isfile(full):
        return {"ok": False, "reason": "audio missing", "path": track_path}

    chords, key = detect_chords_and_key_isolated(full)

    beat_info: dict = {}
    try:
        from beat_snap import analyze_and_snap_dynamic
        beat_info = analyze_and_snap_dynamic(full, chords)
    except Exception as e:
        # Non-fatal — raw BTC still gets saved.
        beat_info = {"_snap_error": f"{type(e).__name__}: {e}"}

    sheet: dict = {
        "path": track_path, "key": key, "capo": 0,
        "source": "btc", "chords": chords,
    }
    bpm_val = beat_info.get("bpm")
    if bpm_val:
        sheet["bpm"] = round(bpm_val, 1)
    if beat_info.get("beats_source"):
        sheet["beats"] = beat_info.get("beats", [])
        sheet["downbeats"] = beat_info.get("downbeats", [])
        sheet["tempo_curve"] = beat_info.get("tempo_curve", [])
        sheet["beats_source"] = beat_info["beats_source"]
        sheet["beat_version"] = beat_info.get("beat_version", 0)

    if settings.get("beat_refiner_enabled", False) and sheet.get("beats"):
        try:
            from ai.bar_arbitrator import phase1_refine
            res = phase1_refine(sheet, full)
            sheet["beat_refiner"] = {
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
                sheet["beats"] = res["beats_after"]
                sheet["downbeats"] = res["downbeats_after"]
        except Exception as e:
            sheet["beat_refiner"] = {
                "applied": False,
                "reason": f"error:{type(e).__name__}",
            }

    h = song_hash(track_path)
    ensure_chord_bucket(h)
    out = chord_file_for(h)
    out.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    update_entry_from_file(track_path)

    return {
        "ok": True,
        "path": track_path,
        "key": key,
        "n_chords": len(chords),
        "bpm": sheet.get("bpm"),
        "snap_error": beat_info.get("_snap_error"),
    }


def _missing_track_paths(from_manifest: str | None) -> list[str]:
    """Resolve the work list — every library track missing a chord JSON,
    minus any persistent quarantine entries (so tracks that fail every
    chunk don't loop forever)."""
    from chord_cache import song_hash
    from data_cache import get_library_tracks, get_chord_hash_set
    from auto_worker import _load_quarantine

    library = get_library_tracks()
    have = get_chord_hash_set()
    quarantined = set(_load_quarantine().keys())

    if from_manifest:
        manifest = json.loads(Path(from_manifest).read_text(encoding="utf-8"))
        target_hashes = {row["hash"] for row in manifest.get("rows", []) if row.get("hash")}
        out = []
        for t in library:
            p = t.get("path")
            if not p:
                continue
            if p in quarantined:
                continue
            h = song_hash(p)
            if h in target_hashes and h not in have:
                out.append(p)
        return out

    out = []
    for t in library:
        p = t.get("path")
        if not p:
            continue
        if p in quarantined:
            continue
        h = song_hash(p)
        if h not in have:
            out.append(p)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="max tracks to process (0 = all)")
    parser.add_argument("--skip", type=int, default=0,
                        help="skip first N tracks (for resuming)")
    parser.add_argument("--from-manifest", default="",
                        help="path to V:/data/chords_midi_quarantine/_manifest_<ts>.json"
                             " (restricts to that quarantine's set)")
    parser.add_argument("--progress-file", default="",
                        help="optional path to write per-track JSON results")
    parser.add_argument("--data-root", default="V:/data",
                        help="DATA_DIR override (default: V:/data — the NUC's live data tree)")
    args = parser.parse_args()

    _patch_data_root(Path(args.data_root))
    print(f"data_root: {args.data_root}")

    settings = _load_settings()
    print(f"beat_refiner_enabled = {bool(settings.get('beat_refiner_enabled', False))}")

    work = _missing_track_paths(args.from_manifest or None)
    print(f"Missing chord JSONs: {len(work)}")
    if args.skip > 0:
        work = work[args.skip:]
        print(f"After --skip {args.skip}: {len(work)} tracks remain")
    if args.limit > 0:
        work = work[: args.limit]
        print(f"Limited to {len(work)} tracks")
    if not work:
        print("Nothing to do.")
        return 0

    progress_fp = None
    if args.progress_file:
        progress_fp = open(args.progress_file, "w", encoding="utf-8")

    t0 = time.time()
    ok = 0
    failed = 0
    quarantined: list[str] = []

    try:
        for i, path in enumerate(work, start=1):
            t_one = time.time()
            try:
                r = _detect_one(path, settings)
            except Exception as e:
                r = {"ok": False, "reason": f"{type(e).__name__}: {e}", "path": path}

            elapsed_one = time.time() - t_one
            if r["ok"]:
                ok += 1
                tag = f" BPM {r['bpm']}" if r.get("bpm") else ""
                snap_warn = " (no beat_snap)" if r.get("snap_error") else ""
                print(f"[{i:>5}/{len(work)}] {elapsed_one:5.1f}s  OK "
                      f"{r['n_chords']:>3} chords  key={r['key']:<3}{tag}{snap_warn}  {path}",
                      flush=True)
            else:
                failed += 1
                reason = r.get("reason", "")
                print(f"[{i:>5}/{len(work)}] {elapsed_one:5.1f}s  FAIL {reason}  {path}",
                      flush=True)
                # Persistent quarantine for known "this file will never
                # decode" classes — otherwise the same broken track is the
                # first item of every wrapper-loop chunk forever.
                low = reason.lower()
                if ("too short" in low and ("cqt" in low or "audio" in low)) \
                   or "0 bytes" in low or "no audio frames" in low \
                   or "lost sync" in low or "format not recognised" in low \
                   or "format not recognized" in low \
                   or "libsndfileerror" in low or "flac decoder" in low \
                   or "file does not contain" in low:
                    try:
                        from auto_worker import _add_to_quarantine, _SHORT_AUDIO_REASON
                        _add_to_quarantine(path, reason or _SHORT_AUDIO_REASON)
                        quarantined.append(path)
                    except Exception as qe:
                        print(f"        (quarantine write failed: {qe})", flush=True)

            if progress_fp:
                progress_fp.write(json.dumps(r, ensure_ascii=False) + "\n")
                progress_fp.flush()

            # In-chunk RAM hygiene. librosa / numpy / madmom accumulate
            # internal buffers in the main process; without periodic GC
            # we hit MemoryError on the host (not GPU) around ~2k tracks.
            # gc.collect() alone is not always enough — the wrapper
            # loop's --limit-based restart is the bulletproof safety net.
            if i % 25 == 0:
                gc.collect()

            if i % 25 == 0 or i == len(work):
                elapsed = time.time() - t0
                rate = i / elapsed
                eta_s = (len(work) - i) / rate if rate > 0 else 0
                print(f"--- {i}/{len(work)}  ok={ok} fail={failed}  "
                      f"{elapsed/60:.1f}min elapsed  {rate:.2f}/s  "
                      f"ETA {eta_s/60:.1f}min ---", flush=True)
            # Coarse milestone every 250 tracks for external monitors / chat
            # notifications. Use a distinct prefix so a grep filter on the
            # log can pick these out without picking up per-25 lines.
            if i % 250 == 0 or i == len(work):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta_s = (len(work) - i) / rate if rate > 0 else 0
                print(f"=== MILESTONE {i}/{len(work)} ok={ok} fail={failed} "
                      f"elapsed={elapsed/60:.1f}min rate={rate:.2f}/s "
                      f"eta={eta_s/60:.0f}min ===", flush=True)
    finally:
        if progress_fp:
            progress_fp.close()

    # Persist the chord_index changes — update_entry_from_file only
    # touches the in-memory cache and relies on a final ensure_synced
    # call to flush the dirty entries to disk. Without this, all
    # accumulated index updates get discarded at interpreter exit
    # (the atexit hook only saves when _save_state.dirty is True, and
    # update_entry_from_file deliberately does not set that flag).
    try:
        import chord_cache as _cc
        _cc.ensure_synced(force=True)
        _cc._save_chord_index(force=True)
        print(f"chord_index.json flushed ({len(_cc._chord_index_cache)} entries)",
              flush=True)
    except Exception as e:
        print(f"WARN: chord_index flush failed: {type(e).__name__}: {e}",
              flush=True)

    elapsed = time.time() - t0
    print()
    print(f"Done in {elapsed/60:.1f}min. ok={ok} failed={failed}")
    if quarantined:
        print(f"Tracks that looked short/corrupt ({len(quarantined)}):")
        for p in quarantined[:20]:
            print(f"  {p}")
        if len(quarantined) > 20:
            print(f"  ... and {len(quarantined) - 20} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
