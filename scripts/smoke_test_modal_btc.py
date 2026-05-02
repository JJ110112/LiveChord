"""Smoke test for the deployed Modal BTC function.

Usage:
    python scripts/smoke_test_modal_btc.py <path/to/test.mp3>

Forces LIVECHORD_USE_MODAL_BTC=1 for this call only, so we hit the
remote Modal function and not the local subprocess. First call after
idle pays ~15-20 s cold start; subsequent calls within 5 min are warm.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parent.parent

    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_test_modal_btc.py <audio.mp3>")
        return 1

    audio_path = sys.argv[1]
    if not Path(audio_path).is_file():
        print(f"❌ File not found: {audio_path}")
        return 1

    # Force Modal route for this test
    os.environ["LIVECHORD_USE_MODAL_BTC"] = "1"
    sys.path.insert(0, str(repo / "backend"))

    print(f"🎵 Audio: {audio_path}")
    print(f"📡 Routing through Modal (LIVECHORD_USE_MODAL_BTC=1)…")
    print(f"   First call after idle: ~15-20 s cold start")
    print()

    t0 = time.time()
    from chord_detect import detect_chords_and_key_isolated

    chords, key = detect_chords_and_key_isolated(audio_path)
    elapsed = time.time() - t0

    print(f"✅ Got {len(chords)} chords in {elapsed:.1f} s")
    print(f"   Key: {key}")
    print()
    print(f"First 5 chords:")
    for c in chords[:5]:
        print(f"   {c['time']:6.2f}s → {c['end']:6.2f}s   {c['chord']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
