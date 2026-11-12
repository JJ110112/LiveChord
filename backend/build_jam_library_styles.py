"""
Build data/jam_library_styles.json — library-wide Jam styles beyond the
style-stratified jam_tracks.json sample.

  * ``Christmas`` — supplements the existing Christmas bucket (which only
    covers the top-level ``Christmas/`` folder, sampled to ~250) with every
    analyzed Christmas piece in the library: classical / masses / oratorio,
    crossover, jazz, instrumental, pop.
  * ``器樂(全)`` and per-instrument ``器樂(鋼琴)`` / ``器樂(吉他)`` /
    ``器樂(弦樂)`` / ``器樂(管樂)`` / ``器樂(管弦樂)``.

Classification is metadata-only (folder / album / title keywords): the
library has no reliable vocal/instrumental flag (the Demucs vocal ratio
exists only for the few songs with stems). Only songs that already have a
chord JSON are included, so every track opens in the player ready to play.

Offline on purpose: the regex pass over ~90k tracks is ~10s of pure Python,
which would hold the server GIL if run at request time. jam_tracks_api
merges the output with mtime-aware caching. Re-run after a big library add:

    python build_jam_library_styles.py                # data/ next to backend/
    python build_jam_library_styles.py --data-dir V:\\data
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chord_cache import song_hash  # noqa: E402

OUTPUT_NAME = "jam_library_styles.json"

CHRISTMAS = "Christmas"
INSTRUMENTAL_ALL = "器樂(全)"

CHRISTMAS_RE = re.compile(
    r"christmas|x-?mas\b|(?<!\()\bno[eë]l\b|navidad|navideñ|weihnacht"
    r"|聖誕|圣诞|耶誕|耶诞|クリスマス|크리스마스"
    r"|jingle bells?|silent night|holy night|stille nacht|平安夜"
    r"|santa claus|santa baby|sleigh ride|let it snow|winter wonderland"
    r"|rudolph|frosty the|mistletoe and holly|under the mistletoe"
    r"|adeste fideles|o come,? all ye|joy to the world|hark!? the herald"
    r"|first no[eë]l|little drummer|deck the hall|god rest ye"
    r"|carol of the|carols\b|\b(?!by\b)[a-z]+'?s? carol\b(?!['’])"
    r"|nutcracker|胡桃鉗|messiah,? hwv|handel.{0,40}messiah|hallelujah.{0,30}messiah"
    r"|o tannenbaum|we wish you a merry|away in a manger|o little town of"
    r"|o holy night|angels we have heard|good king wenceslas|what child is this"
    r"|twelve days of|il est né|christmas oratorio|weihnachtsoratorium|bwv 248",
    re.I,
)

# Any of these means a sung lead → not an instrumental track.
VOCAL_RE = re.compile(
    r"vocal|feat\.|\bft\.|choir|chorus|合唱|cantata|kantate|\baria\b|soprano"
    r"|tenor\b|baritone|mezzo|opera|lieder|\blied\b|\bsings?\b|\bsung\b"
    r"|歌[手唱]|ボーカル|with lyrics|a cappella|cappella",
    re.I,
)

INSTRUMENT_STYLES = [
    ("器樂(鋼琴)", r"piano|pianist|鋼琴|钢琴|ピアノ|피아노"),
    ("器樂(吉他)", r"guitar|吉他|ギター|ukulele|烏克麗麗"),
    ("器樂(弦樂)", r"violin|viola\b|cello|string quartet|strings\b|小提琴|大提琴|バイオリン|erhu|二胡"),
    ("器樂(管樂)", r"sax(ophone)?\b|flute|clarinet|oboe|bassoon|trumpet|\bhorn\b|recorder\b|harmonica|長笛|薩克斯|口琴"),
    ("器樂(管弦樂)", r"orchestra|orchestral|symphon(?!y x)|philharmoni|concerto|管弦|交響"),
]
INSTRUMENT_STYLES = [(name, re.compile(rx, re.I)) for name, rx in INSTRUMENT_STYLES]

# Instrumental with no specific instrument named → only in 器樂(全).
GENERIC_INSTRUMENTAL_RE = re.compile(
    r"instrumental|\binst\.|\(inst\)|off vocal|純音樂|纯音乐|演奏|伴奏", re.I,
)

STYLE_ORDER = [CHRISTMAS, INSTRUMENTAL_ALL] + [n for n, _ in INSTRUMENT_STYLES]


def classify(track: dict) -> list:
    """Return the library styles a library_cache track belongs to."""
    path = (track.get("path") or "").replace("\\", "/")
    text = " ".join(str(track.get(k) or "") for k in ("title", "album", "path"))
    top = re.sub(r"^@\d+/", "", path).split("/", 1)[0]
    styles = []
    if top == CHRISTMAS or CHRISTMAS_RE.search(text):
        styles.append(CHRISTMAS)
    if not VOCAL_RE.search(text):
        instruments = [name for name, rx in INSTRUMENT_STYLES if rx.search(text)]
        if instruments or GENERIC_INSTRUMENTAL_RE.search(text):
            styles.append(INSTRUMENTAL_ALL)
            styles.extend(instruments)
    return styles


def analyzed_hashes(chords_dir: Path) -> set:
    """Hashes with a sharded chord JSON (<chords>/<hash[:2]>/<hash>.json)."""
    out = set()
    if not chords_dir.is_dir():
        return out
    with os.scandir(chords_dir) as buckets:
        for b in buckets:
            if not b.is_dir() or len(b.name) != 2:
                continue
            with os.scandir(b.path) as files:
                for f in files:
                    if f.name.endswith(".json"):
                        out.add(f.name[:-5])
    return out


def build(library_tracks: list, analyzed: set) -> dict:
    styles = {name: [] for name in STYLE_ORDER}
    for t in library_tracks:
        path = (t.get("path") or "").replace("\\", "/")
        if not path:
            continue
        tags = classify(t)
        if not tags:
            continue
        h = song_hash(path)
        if h not in analyzed:
            continue
        row = {
            "hash": h,
            "path": path,
            "title": t.get("title") or "",
            "artist": t.get("artist") or "",
            "album": t.get("album") or "",
            "duration": round(float(t.get("duration") or 0), 1),
            "source": "library",
            "score": 0,
        }
        for tag in tags:
            styles[tag].append(dict(row, style=tag))
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": {name: len(v) for name, v in styles.items()},
        "styles": styles,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"))
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    sys.stdout.reconfigure(encoding="utf-8")

    lib = json.loads((data_dir / "library_cache.json").read_text(encoding="utf-8"))
    tracks = lib.get("tracks", [])
    analyzed = analyzed_hashes(data_dir / "chords")
    print(f"library tracks {len(tracks)}, analyzed chord files {len(analyzed)}")

    result = build(tracks, analyzed)
    out = data_dir / OUTPUT_NAME
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)

    for name in STYLE_ORDER:
        rows = result["styles"][name]
        top = Counter("/".join(re.sub(r"^@\d+/", "", r["path"]).split("/")[:2]) for r in rows)
        print(f"  {name:12s} {len(rows):6d}   top folders: {top.most_common(3)}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
