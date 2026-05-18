"""Scan chord JSON corpus for orphan entries — chord JSONs whose audio
file path no longer resolves to a real file.

Walks ``data/chords/<shard>/<hash>.json`` (excluding ``.bak.*`` sidecars),
reads each ``path`` field, runs ``config.resolve_path``, and reports
chord JSONs whose audio is missing. Optionally produces a delete plan
(JSON listing every artifact that would be removed when an orphan is
deleted: chord JSON, .bak sidecars, melody cache, accompaniment caches,
human_feedback overlays).

Dry-run by default. Pass --execute to actually delete (still requires
the operator to have authorized).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

DATA_DIR = _REPO_ROOT / "data"
DEFAULT_CHORDS_DIR = DATA_DIR / "chords"


def _related_artifacts(chord_json: Path, sheet: dict, data_dir: Path) -> list[Path]:
    """Files that should be removed alongside a deleted chord JSON.

    Conservative — only lists artifacts keyed by the song hash:
      - the chord JSON itself
      - sibling .bak.<source>.json sidecars in the same shard dir
      - melody cache at data/melodies/<hash>.json
      - accompaniment caches at data/accompaniments/<hash>_*.json
      - human_feedback overlays at data/human_feedback/<hash>.json
    """
    h = chord_json.stem
    shard = chord_json.parent

    artifacts: list[Path] = [chord_json]
    # Sibling .bak files in the shard dir
    for sib in shard.iterdir():
        if sib.name.startswith(h + ".json.bak."):
            artifacts.append(sib)
    # Melody cache
    mel = data_dir / "melodies" / f"{h}.json"
    if mel.is_file():
        artifacts.append(mel)
    # Accompaniment caches
    acc_dir = data_dir / "accompaniments"
    if acc_dir.is_dir():
        for f in acc_dir.glob(f"{h}_*.json"):
            artifacts.append(f)
    # Human feedback overlays
    hf = data_dir / "human_feedback" / f"{h}.json"
    if hf.is_file():
        artifacts.append(hf)
    return artifacts


def scan_orphans(chords_dir: Path, *, data_dir: Path,
                 limit: Optional[int] = None,
                 progress_every: int = 2000) -> dict:
    from config import resolve_path

    files = sorted(chords_dir.glob("*/*.json"))
    if not files:
        files = sorted(chords_dir.glob("*.json"))
    files = [f for f in files if ".bak." not in f.name]
    if limit:
        files = files[:limit]

    total = len(files)
    orphans: list[dict] = []
    no_path = 0
    valid = 0
    read_fail = 0
    artifact_total = 0
    t0 = time.time()

    for i, f in enumerate(files, 1):
        try:
            sheet = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            read_fail += 1
            continue
        track_path = sheet.get("path") or ""
        if not track_path:
            no_path += 1
            continue
        try:
            resolved = resolve_path(track_path)
        except Exception:
            resolved = ""
        if resolved and os.path.isfile(resolved):
            valid += 1
            continue
        arts = _related_artifacts(f, sheet, data_dir)
        artifact_total += len(arts)
        orphans.append({
            "hash": f.stem,
            "chord_json": str(f),
            "path": track_path,
            "resolved": resolved or "(unresolved)",
            "artifacts": [str(a) for a in arts],
            "n_chords": len(sheet.get("chords") or []),
            "n_beats": len(sheet.get("beats") or []),
            "source": sheet.get("source", ""),
            "beats_source": sheet.get("beats_source", ""),
        })
        if i % progress_every == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"  scan {i}/{total} ({rate:.0f}/s, ETA {eta:.0f}s) — "
                  f"orphans={len(orphans)} valid={valid} no_path={no_path} "
                  f"read_fail={read_fail}", flush=True)

    return {
        "total_scanned": total,
        "n_orphans": len(orphans),
        "n_valid": valid,
        "n_no_path": no_path,
        "n_read_fail": read_fail,
        "n_artifacts_total": artifact_total,
        "orphans": orphans,
        "elapsed_sec": round(time.time() - t0, 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chords-dir", default="",
                    help="Override chord JSON directory (default: data/chords).")
    ap.add_argument("--data-dir", default="",
                    help="Override data dir for related-artifact lookups "
                         "(default: <chords-dir>/..).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Scan first N chord JSONs only.")
    ap.add_argument("--out", default="",
                    help="Write the full orphan list to this JSON file. "
                         "Without this, only counts + a sample print.")
    ap.add_argument("--sample", type=int, default=10,
                    help="How many orphans to print as a sample (default 10).")
    ap.add_argument("--execute", action="store_true",
                    help="DELETE every artifact listed. Without this it's a "
                         "dry-run report.")
    args = ap.parse_args()

    chords_dir = Path(args.chords_dir).resolve() if args.chords_dir else DEFAULT_CHORDS_DIR
    data_dir = Path(args.data_dir).resolve() if args.data_dir else chords_dir.parent

    if not chords_dir.is_dir():
        print(f"ERROR: chord JSON directory not found: {chords_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {chords_dir} (data_dir={data_dir}) ...", flush=True)
    result = scan_orphans(
        chords_dir,
        data_dir=data_dir,
        limit=args.limit or None,
    )

    print()
    print("=" * 60)
    print("Orphan scan summary")
    print("=" * 60)
    print(f"  total chord JSONs scanned : {result['total_scanned']:,}")
    print(f"  orphans (audio missing)   : {result['n_orphans']:,}")
    print(f"  valid (audio present)     : {result['n_valid']:,}")
    print(f"  no 'path' field           : {result['n_no_path']:,}")
    print(f"  read failures             : {result['n_read_fail']:,}")
    print(f"  total artifacts to delete : {result['n_artifacts_total']:,}")
    print(f"  elapsed                   : {result['elapsed_sec']}s")

    if args.sample > 0 and result["orphans"]:
        print()
        print(f"Sample (first {min(args.sample, len(result['orphans']))} orphans):")
        for o in result["orphans"][:args.sample]:
            print(f"  {o['hash']}  src={o['source']!r}  nc={o['n_chords']} "
                  f"nb={o['n_beats']}  path={o['path']!r}  "
                  f"({len(o['artifacts'])} artifact(s))")

    if args.out:
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote orphan list → {args.out}")

    if not args.execute:
        print()
        print("DRY RUN — no files deleted. Pass --execute to actually remove.")
        return

    # ---- Execute deletion ----
    print()
    print("=" * 60)
    print(f"EXECUTING DELETE of {result['n_artifacts_total']:,} artifacts "
          f"across {result['n_orphans']:,} orphan songs ...")
    print("=" * 60)
    deleted = 0
    missing = 0
    errors: list[tuple[str, str]] = []
    for o in result["orphans"]:
        for art in o["artifacts"]:
            p = Path(art)
            try:
                if p.is_file():
                    p.unlink()
                    deleted += 1
                else:
                    missing += 1
            except Exception as e:
                errors.append((art, f"{type(e).__name__}: {e}"))
    print(f"  deleted        : {deleted:,}")
    print(f"  missing (skip) : {missing:,}")
    print(f"  errors         : {len(errors):,}")
    for art, err in errors[:20]:
        print(f"    ! {art} :: {err}")
    if len(errors) > 20:
        print(f"    ... ({len(errors) - 20} more)")


if __name__ == "__main__":
    main()
