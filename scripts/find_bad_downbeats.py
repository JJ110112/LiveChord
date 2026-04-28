"""Find chord JSONs whose backfill made downbeats sub-bar.

A 4/4 song should have ~4 beats per bar. If beats_per_bar drops below 3
(typically to ~2), the model misread 4/4 as 2/4 and halved the bar
duration. chord_splitter then over-splits, breaking player rendering.

Output: list of bad-hash JSON paths (suitable for selective restore).
"""
import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path("V:/data/chords")
THRESH_MIN = 3.0   # beats_per_bar must be >= this to be plausible
THRESH_MAX = 5.0   # and <= this (catches weird 6+/bar too)


def scan_one(p: Path):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    br = d.get("beat_refiner") or {}
    if not br.get("applied"):
        return None
    beats = d.get("beats") or []
    dbs = d.get("downbeats") or []
    if len(beats) < 5 or len(dbs) < 3:
        return None
    bgaps = sorted([beats[i+1]-beats[i] for i in range(len(beats)-1)])
    dgaps = sorted([dbs[i+1]-dbs[i] for i in range(len(dbs)-1)])
    bmed = bgaps[len(bgaps)//2]
    dmed = dgaps[len(dgaps)//2]
    if bmed <= 0:
        return None
    bpb = dmed / bmed
    if THRESH_MIN <= bpb <= THRESH_MAX:
        return None  # plausible
    return {
        "json": str(p),
        "path": d.get("path", ""),
        "bpb": bpb,
        "n_beats": len(beats),
        "n_db": len(dbs),
        "bmed": bmed,
        "dmed": dmed,
        "before": br.get("score_before", 0),
        "after": br.get("score_after", 0),
    }


def main():
    paths = list(ROOT.glob("*/*.json"))
    print(f"scanning {len(paths):,} JSONs...", file=sys.stderr)
    bad = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(scan_one, p) for p in paths]):
            r = f.result()
            if r:
                bad.append(r)
    bad.sort(key=lambda r: r["bpb"])
    print(f"\nfound {len(bad):,} suspect songs (beats_per_bar outside [{THRESH_MIN},{THRESH_MAX}])", file=sys.stderr)
    # Write full list FIRST so a downstream print error can't lose data.
    out = Path("G:/livechord/bad_downbeat_hashes.txt")
    out.write_text("\n".join(r["json"] for r in bad), encoding="utf-8")
    print(f"wrote {len(bad)} json paths to {out}", file=sys.stderr)

    print(f"\n{'bpb':>6}  {'n_beats':>7}  {'n_db':>5}  {'gain':>7}  path")
    print("-" * 110)
    for r in bad[:30]:
        gain = r["after"] - r["before"]
        print(f"{r['bpb']:>6.2f}  {r['n_beats']:>7}  {r['n_db']:>5}  {gain:>+7.4f}  {r['path']}")
    if len(bad) > 30:
        print(f"... and {len(bad) - 30:,} more")


if __name__ == "__main__":
    main()
