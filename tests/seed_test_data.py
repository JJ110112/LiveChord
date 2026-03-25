"""種子測試資料 — 為已知的測試歌曲建立和弦譜"""

import json
import hashlib
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CHORDS_DIR = DATA_DIR / "chords"


def song_hash(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


# 使用 Bob James - Beerbohm 作為測試曲目（已確認存在）
TEST_TRACK = "Jazz/Bob James/Bob James - Jazz Hands/Beerbohm.flac"

TEST_CHORD_SHEET = {
    "path": TEST_TRACK,
    "key": "Eb",
    "capo": 0,
    "chords": [
        {"time": 0.0, "end": 8.0, "chord": "Eb"},
        {"time": 8.0, "end": 16.0, "chord": "Gm"},
        {"time": 16.0, "end": 24.0, "chord": "Cm7"},
        {"time": 24.0, "end": 32.0, "chord": "Fm7"},
        {"time": 32.0, "end": 40.0, "chord": "Bb7"},
        {"time": 40.0, "end": 48.0, "chord": "Eb"},
        {"time": 48.0, "end": 56.0, "chord": "Abmaj7"},
        {"time": 56.0, "end": 64.0, "chord": "Gm"},
        {"time": 64.0, "end": 72.0, "chord": "Fm7"},
        {"time": 72.0, "end": 80.0, "chord": "Bb7"},
        {"time": 80.0, "end": 88.0, "chord": "Eb"},
        {"time": 88.0, "end": 96.0, "chord": "Cm7"},
    ],
}


def seed():
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    h = song_hash(TEST_TRACK)
    path = CHORDS_DIR / f"{h}.json"
    path.write_text(json.dumps(TEST_CHORD_SHEET, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"Seeded: {path} (hash={h})")
    print(f"Track: {TEST_TRACK}")
    print(f"Chords: {len(TEST_CHORD_SHEET['chords'])} entries")


if __name__ == "__main__":
    seed()
