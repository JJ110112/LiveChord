"""Smoke test for the deployed Modal beat_this function.

Usage:
    python scripts/smoke_test_modal_beat_this.py <path/to/test.wav>

Forces LIVECHORD_USE_MODAL_BEAT_THIS=1 for this call only, so we hit the
remote Modal function. First call after idle pays ~10-15 s cold start;
subsequent calls within 5 min are warm.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parent.parent

    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_test_modal_beat_this.py <audio.wav>")
        return 1

    audio_path = sys.argv[1]
    if not Path(audio_path).is_file():
        print(f"❌ File not found: {audio_path}")
        return 1

    os.environ["LIVECHORD_USE_MODAL_BEAT_THIS"] = "1"
    sys.path.insert(0, str(repo / "backend"))

    print(f"🥁 Audio: {audio_path}")
    print(f"📡 Routing through Modal (LIVECHORD_USE_MODAL_BEAT_THIS=1)…")
    print(f"   First call after idle: ~10-15 s cold start")
    print()

    t0 = time.time()
    from modal_beat_this import detect_beats_via_modal

    info = detect_beats_via_modal(audio_path)
    elapsed = time.time() - t0

    print(f"✅ Returned in {elapsed:.1f} s")
    print(f"   beats_source: {info.get('beats_source')}")
    print(f"   bpm:          {info.get('bpm')}")
    print(f"   beats:        {len(info.get('beats') or [])}")
    print(f"   downbeats:    {len(info.get('downbeats') or [])}")
    tc = info.get("tempo_curve") or []
    if tc:
        bpms = [p["bpm"] for p in tc]
        print(f"   tempo range:  {min(bpms):.1f} – {max(bpms):.1f}")
    bc = info.get("bpm_correction")
    if bc and bc.get("applied"):
        print(f"   bpm halved:   {bc['original']:.1f} → {info['bpm']} ({bc['reason']})")
    if info.get("error"):
        print(f"   ⚠️  error: {info['error']}")
    print()
    print("First 5 beats:")
    for b in (info.get("beats") or [])[:5]:
        print(f"   {b:6.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
