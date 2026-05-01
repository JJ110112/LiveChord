"""One-shot: scan chord JSONs, rank by beat_refiner align gain, print top N."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path("V:/data/chords")
TOP_N = 20  # request top 10, but show 20 for headroom


def scan_one(p: Path):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    br = d.get("beat_refiner") or {}
    if not br.get("applied"):
        return None
    sb = br.get("score_before") or 0
    sa = br.get("score_after") or 0
    return {
        "gain": sa - sb,
        "before": sb,
        "after": sa,
        "n_db_before": br.get("n_downbeats_before", 0),
        "n_db_after": br.get("n_downbeats_after", 0),
        "path": d.get("path", ""),
        "json": str(p),
    }


def main():
    t0 = time.time()
    paths = list(ROOT.glob("*/*.json"))
    print(f"scanning {len(paths):,} JSONs...", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(scan_one, p) for p in paths]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r:
                results.append(r)
            if i % 5000 == 0:
                print(f"  {i:,}/{len(paths):,}", file=sys.stderr)

    results.sort(key=lambda r: r["gain"], reverse=True)
    elapsed = time.time() - t0
    print(f"\nscanned in {elapsed:.1f}s · {len(results):,} applied entries\n", file=sys.stderr)

    print(f"{'#':>3}  {'gain':>7}  {'before':>7}  {'after':>7}  {'n_db':>11}  path")
    print("-" * 110)
    for i, r in enumerate(results[:TOP_N], 1):
        n_db = f"{r['n_db_before']}->{r['n_db_after']}"
        print(f"{i:>3}  {r['gain']:>+7.4f}  {r['before']:>7.4f}  {r['after']:>7.4f}  {n_db:>11}  {r['path']}")


if __name__ == "__main__":
    main()
