"""Delete chord JSONs (and their related artifacts) for songs whose
audio is missing on the NAS.

Takes a newline-separated list of song hashes (e.g. extracted from a
g1k SKIP_AUDIO_MISSING run via ``grep "SKIP_AUDIO_MISSING" g1k.log |
grep -oE "[a-f0-9]{12}" | sort -u``) and removes:
  - data/chords/<shard>/<hash>.json
  - data/chords/<shard>/<hash>.json.bak.*  (every sidecar)
  - data/melodies/<hash>.json
  - data/accompaniments/<hash>_*.json
  - data/human_feedback/<hash>.json

Dry-run by default. Pass --execute to actually delete.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = _REPO_ROOT / "data"


def _artifacts_for_hash(h: str, data_dir: Path) -> list[Path]:
    shard = data_dir / "chords" / h[:2]
    arts: list[Path] = []
    # Chord JSON + sidecars (every file beginning with "<hash>.json")
    if shard.is_dir():
        for f in shard.iterdir():
            if f.name == f"{h}.json" or f.name.startswith(f"{h}.json."):
                arts.append(f)
    # Melody cache
    mel = data_dir / "melodies" / f"{h}.json"
    if mel.is_file():
        arts.append(mel)
    # Accompaniment caches
    acc_dir = data_dir / "accompaniments"
    if acc_dir.is_dir():
        for f in acc_dir.glob(f"{h}_*.json"):
            arts.append(f)
    # Human feedback overlay (if file)
    hf = data_dir / "human_feedback" / f"{h}.json"
    if hf.is_file():
        arts.append(hf)
    return arts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hashes", required=True,
                    help="Path to file with one song hash per line.")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                    help="Data dir root (default: <repo>/data).")
    ap.add_argument("--execute", action="store_true",
                    help="Actually delete files. Without this it's a dry-run.")
    ap.add_argument("--sample", type=int, default=8,
                    help="Number of orphan hashes to show as a sample.")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        sys.exit(1)

    hashes = [h.strip() for h in Path(args.hashes).read_text(encoding="utf-8").splitlines()
              if h.strip()]
    print(f"Loaded {len(hashes):,} song hashes from {args.hashes}", flush=True)
    print(f"Data dir: {data_dir}", flush=True)

    by_kind = {"chord_json": 0, "chord_bak": 0, "melody": 0, "accomp": 0, "human_feedback": 0}
    plan: dict[str, list[Path]] = {}
    total_size = 0

    for i, h in enumerate(hashes, 1):
        arts = _artifacts_for_hash(h, data_dir)
        plan[h] = arts
        for a in arts:
            try:
                total_size += a.stat().st_size
            except Exception:
                pass
            name = a.name
            if name == f"{h}.json":
                by_kind["chord_json"] += 1
            elif name.startswith(f"{h}.json."):
                by_kind["chord_bak"] += 1
            elif a.parent.name == "melodies":
                by_kind["melody"] += 1
            elif a.parent.name == "accompaniments":
                by_kind["accomp"] += 1
            elif a.parent.name == "human_feedback":
                by_kind["human_feedback"] += 1
        if i % 500 == 0 or i == len(hashes):
            print(f"  plan {i}/{len(hashes)} — artifacts so far: "
                  f"{sum(by_kind.values()):,}", flush=True)

    print()
    print("=" * 60)
    print("Delete plan")
    print("=" * 60)
    total = sum(by_kind.values())
    print(f"  unique hashes        : {len(hashes):,}")
    print(f"  total artifacts      : {total:,}")
    print(f"    chord JSONs        : {by_kind['chord_json']:,}")
    print(f"    .bak sidecars      : {by_kind['chord_bak']:,}")
    print(f"    melody caches      : {by_kind['melody']:,}")
    print(f"    accompaniment cache: {by_kind['accomp']:,}")
    print(f"    human_feedback     : {by_kind['human_feedback']:,}")
    print(f"  total size           : {total_size / (1024*1024):.1f} MiB")
    # Hashes with NO surviving artifacts (already cleaned up?)
    missing_n = sum(1 for arts in plan.values() if not arts)
    print(f"  hashes w/ no artifacts (already cleaned): {missing_n:,}")

    if args.sample > 0:
        print()
        print(f"Sample (first {min(args.sample, len(hashes))} hashes):")
        for h in hashes[:args.sample]:
            arts = plan[h]
            names = [a.name for a in arts]
            print(f"  {h}  ({len(arts)} artifact(s)): {names}")

    if not args.execute:
        print()
        print("DRY RUN — no files deleted. Pass --execute to actually remove.")
        return

    print()
    print("=" * 60)
    print(f"EXECUTING DELETE of {total:,} artifacts ...")
    print("=" * 60)
    deleted = 0
    errors: list[tuple[str, str]] = []
    for h, arts in plan.items():
        for a in arts:
            try:
                a.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
            except Exception as e:
                errors.append((str(a), f"{type(e).__name__}: {e}"))
    print(f"  deleted     : {deleted:,}")
    print(f"  errors      : {len(errors):,}")
    for art, err in errors[:20]:
        print(f"    ! {art} :: {err}")
    if len(errors) > 20:
        print(f"    ... ({len(errors) - 20} more)")


if __name__ == "__main__":
    main()
