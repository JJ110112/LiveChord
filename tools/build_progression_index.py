"""Build the chord-progression search index for the Progression Library feature.

Walks the sharded chord corpus (data/chords/<bucket>/<hash>.json), packs each
song's ordered chord sequence into a deduped token string (see
backend/progression_match.py), and writes a compact index that the
/api/progression/match endpoint scans in memory.

Output: data/progression_index.json
  { "version": 1, "count": N, "songs": [[hash, title, key, packed_seq], ...] }

Run from the dev repo (fast local SSD), then deploy the resulting JSON to
V:\data\progression_index.json. This is a dev/offline tool, NOT runtime — but
it imports backend/progression_match.py (which IS runtime) so the packing logic
is shared and can't drift.

Usage:
  python tools/build_progression_index.py [--chords-dir PATH] [--out PATH] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
import progression_match as pm  # noqa: E402

# Songs need at least this many distinct chords to be worth indexing — a
# one-chord drone can't match any multi-chord progression and just bloats scans.
_MIN_TOKENS = 2


def _title_from_path(path: str) -> str:
    if not path:
        return "Untitled"
    base = path.replace("\\", "/").rstrip("/").split("/")[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem.strip() or "Untitled"


def build(chords_dir: Path, out_path: Path, limit: int = 0) -> dict:
    songs = []
    scanned = 0
    skipped = 0
    t0 = time.time()
    if not chords_dir.is_dir():
        raise SystemExit(f"chords dir not found: {chords_dir}")

    buckets = sorted(p for p in chords_dir.iterdir() if p.is_dir() and len(p.name) == 2)
    for b in buckets:
        for entry in os.scandir(b):
            if not entry.name.endswith(".json") or ".json.bak." in entry.name:
                continue
            scanned += 1
            try:
                data = json.loads(Path(entry.path).read_text(encoding="utf-8"))
            except Exception:
                skipped += 1
                continue
            chords = data.get("chords") or []
            names = [c.get("chord") for c in chords if isinstance(c, dict)]
            packed = pm.chords_to_packed(names)
            if len(packed) < _MIN_TOKENS:
                skipped += 1
                continue
            h = entry.name[:-5]
            title = _title_from_path(data.get("path", ""))
            key = data.get("key", "") or ""
            songs.append([h, title, key, packed])
            if limit and len(songs) >= limit:
                break
        if scanned % 5000 == 0:
            print(f"  ... scanned {scanned}, indexed {len(songs)} ({time.time()-t0:.0f}s)")
        if limit and len(songs) >= limit:
            break

    payload = {"version": 1, "count": len(songs), "songs": songs}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, out_path)
    elapsed = time.time() - t0
    print(f"Indexed {len(songs)} songs (scanned {scanned}, skipped {skipped}) in {elapsed:.0f}s")
    print(f"Wrote {out_path} ({out_path.stat().st_size/1e6:.1f} MB)")
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chords-dir", default=str(ROOT / "data" / "chords"))
    ap.add_argument("--out", default=str(ROOT / "data" / "progression_index.json"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    build(Path(args.chords_dir), Path(args.out), args.limit)


if __name__ == "__main__":
    main()
