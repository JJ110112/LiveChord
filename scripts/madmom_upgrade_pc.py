"""PC-side madmom beat upgrade for songs whose chord JSON lost beats[].

After the BTC re-detect (LiveChord-a7c), ~73 slow jazz piano tracks
(Beegie Adair, Diana Krall solo, etc.) ended up with
``beats_source: librosa-fallback`` and ``n_beats=0``. librosa's beat
tracker simply can't lock onto the soft transients of solo piano;
madmom's RNN+DBN model handles them well. This script reads the
chord JSON, replays ``analyze_and_snap_dynamic(prefer_madmom=True)``,
and writes back the upgraded beats/downbeats/tempo_curve.

The chord progression itself is NOT touched — only the rhythmic
metadata is rewritten and the chord boundaries are re-snapped to the
new beat grid (which is what ``analyze_and_snap_dynamic`` does
internally).

Defaults pick targets from the most recent jazz quality-gate report;
override via ``--from-report`` or ``--paths-file``.
"""

from __future__ import annotations

import argparse
import copy
import csv
import gc
import json
import os
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _patch_data_root(data_root: Path) -> None:
    import config as _config
    import data_cache as _dc
    import chord_cache as _cc
    _config.DATA_DIR = data_root
    _dc.DATA_DIR = data_root
    _dc.CACHE_FILE = data_root / "library_cache.json"
    _dc.CHORDS_DIR = data_root / "chords"
    _cc.DATA_DIR = data_root
    _cc.INDEX_FILE = data_root / "chord_index.json"
    _cc.CHORDS_DIR = data_root / "chords"


def _load_targets(report_csv: Path | None,
                  paths_file: Path | None,
                  filter_missing_beats: bool) -> list[str]:
    """Return library-relative paths (sans ``@1/`` prefix) that need madmom."""
    if paths_file:
        return [
            line.strip().lstrip("@1/").lstrip("@")
            for line in paths_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if report_csv:
        with report_csv.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if filter_missing_beats:
            rows = [r for r in rows if "missing_beats" in (r.get("issue_types") or "")]
        else:
            # weak_beat_source covers librosa-fallback too — broader sweep
            rows = [
                r for r in rows
                if "missing_beats" in (r.get("issue_types") or "")
                or "weak_beat_source" in (r.get("issue_types") or "")
                or (r.get("beats_source") or "") == "librosa-fallback"
            ]
        return [r["path"] for r in rows]
    return []


def _upgrade_one(rel_path: str) -> dict:
    """Replay beat_snap with prefer_madmom=True and write upgraded JSON."""
    from chord_cache import song_hash, chord_file_for
    from config import resolve_path
    from beat_snap import analyze_and_snap_dynamic, HAS_MADMOM

    if not HAS_MADMOM:
        return {"ok": False, "reason": "madmom not importable", "path": rel_path}

    library_path = "@1/" + rel_path
    full = resolve_path(library_path)
    if not full or not os.path.isfile(full):
        return {"ok": False, "reason": f"audio missing: {full}", "path": rel_path}

    chord_file = chord_file_for(song_hash(library_path))
    if not chord_file.is_file():
        return {"ok": False, "reason": "chord JSON missing", "path": rel_path}

    sheet = json.loads(chord_file.read_text(encoding="utf-8"))
    raw_chords = sheet.get("chords") or []
    if not raw_chords:
        return {"ok": False, "reason": "empty chord list", "path": rel_path}

    chords_copy = copy.deepcopy(raw_chords)
    try:
        info = analyze_and_snap_dynamic(full, chords_copy, prefer_madmom=True)
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "path": rel_path}

    bsource = info.get("beats_source") or ""
    if bsource != "madmom":
        # madmom failed for this file (e.g. signal too short or atypical) —
        # leave the chord JSON alone rather than overwriting good data with
        # another fallback.
        return {"ok": False, "reason": f"madmom did not fire (beats_source={bsource!r})",
                "path": rel_path}

    # The snap function mutates chord boundaries in-place. Write back the
    # updated chord list AND the beat fields. Beat refiner (if previously
    # applied) is stamped as stale because it was scored against the old
    # beats; drop it so it does not mislead downstream logic.
    sheet["chords"] = chords_copy
    sheet["bpm"] = round(float(info.get("bpm") or sheet.get("bpm") or 0.0), 1)
    sheet["beats"] = info.get("beats", [])
    sheet["downbeats"] = info.get("downbeats", [])
    sheet["tempo_curve"] = info.get("tempo_curve", [])
    sheet["beats_source"] = "madmom"
    sheet["beat_version"] = info.get("beat_version", sheet.get("beat_version", 0))
    sheet.pop("beat_refiner", None)

    tmp = chord_file.with_suffix(chord_file.suffix + ".tmp")
    tmp.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chord_file)

    return {
        "ok": True,
        "path": rel_path,
        "bpm": sheet["bpm"],
        "n_beats": len(sheet["beats"]),
        "n_downbeats": len(sheet["downbeats"]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    default_report = (REPO_ROOT
                      / "reports"
                      / "quality_gate_jazz_after_phase_fix_20260515"
                      / "quality_failures.csv")
    ap.add_argument("--from-report", default=str(default_report),
                    help="quality_failures.csv to pull missing_beats songs from")
    ap.add_argument("--paths-file", default="",
                    help="newline-separated list of library-relative paths")
    ap.add_argument("--data-root", default="V:/data")
    ap.add_argument("--missing-only", action="store_true",
                    help="restrict to rows tagged missing_beats (default also includes weak_beat_source)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--progress-file", default="")
    args = ap.parse_args()

    _patch_data_root(Path(args.data_root))
    print(f"data_root: {args.data_root}")

    targets = _load_targets(
        Path(args.from_report) if args.from_report else None,
        Path(args.paths_file) if args.paths_file else None,
        args.missing_only,
    )
    # Dedup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for p in targets:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    targets = deduped
    print(f"Targets: {len(targets)} song(s)")
    if args.skip:
        targets = targets[args.skip:]
        print(f"After --skip {args.skip}: {len(targets)}")
    if args.limit:
        targets = targets[: args.limit]
        print(f"Limited to {len(targets)}")
    if not targets:
        print("Nothing to do.")
        return 0

    progress_fp = open(args.progress_file, "w", encoding="utf-8") if args.progress_file else None
    t0 = time.time()
    ok = 0
    failed = 0

    try:
        for i, rel in enumerate(targets, start=1):
            t_one = time.time()
            r = _upgrade_one(rel)
            dt = time.time() - t_one
            if r["ok"]:
                ok += 1
                print(f"[{i:>4}/{len(targets)}] {dt:5.1f}s  OK madmom "
                      f"BPM {r['bpm']}  beats={r['n_beats']} db={r['n_downbeats']}  {rel}",
                      flush=True)
            else:
                failed += 1
                print(f"[{i:>4}/{len(targets)}] {dt:5.1f}s  SKIP {r['reason']}  {rel}",
                      flush=True)
            if progress_fp:
                progress_fp.write(json.dumps(r, ensure_ascii=False) + "\n")
                progress_fp.flush()

            # madmom is heavy; periodic gc keeps the steady-state RSS in check.
            if i % 10 == 0:
                gc.collect()

            if i % 25 == 0 or i == len(targets):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(targets) - i) / rate if rate else 0
                print(f"=== MILESTONE {i}/{len(targets)} ok={ok} fail={failed} "
                      f"elapsed={elapsed/60:.1f}min rate={rate:.2f}/s "
                      f"eta={eta/60:.0f}min ===", flush=True)
    finally:
        if progress_fp:
            progress_fp.close()
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
