"""Generate RH melody shadow candidates for one song.

Examples:
  python tools/generate_rh_melody_shadow.py --hash abcdef123456 --candidate full_mix_pyin
  python tools/generate_rh_melody_shadow.py --path "POP/song.flac" --candidate vocal_stem_crepe
  python tools/generate_rh_melody_shadow.py --hash abcdef123456 --candidate solo_piano_polyphonic --polyphonic-midi C:\tmp\song.mid
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from ai.melody_candidate import FULL_MIX_PYIN, SOLO_PIANO_POLYPHONIC, VOCAL_STEM_CREPE  # noqa: E402
from ai.melody_shadow_generator import DEFAULT_DATA_DIR, generate_shadow_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RH melody shadow candidates for one song.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--hash", default="", help="Song hash. If omitted, --path is hashed.")
    parser.add_argument("--path", default="", help="Library-relative song path.")
    parser.add_argument("--audio-path", default="", help="Absolute audio path override.")
    parser.add_argument(
        "--candidate",
        action="append",
        choices=[FULL_MIX_PYIN, VOCAL_STEM_CREPE, SOLO_PIANO_POLYPHONIC],
        help="Candidate to generate. May be repeated. Default: full_mix_pyin.",
    )
    parser.add_argument("--polyphonic-json", default="", help="Polyphonic note JSON for solo_piano_polyphonic.")
    parser.add_argument("--polyphonic-midi", default="", help="Polyphonic MIDI for solo_piano_polyphonic.")
    parser.add_argument(
        "--key",
        default=None,
        help="Key hint for solo_piano_polyphonic. Defaults to chord-cache detected key.",
    )
    parser.add_argument("--force", action="store_true", help="Regenerate even if candidate cache exists.")
    args = parser.parse_args()

    result = generate_shadow_candidates(
        data_dir=Path(args.data_dir),
        song_hash=args.hash,
        path=args.path,
        audio_path=args.audio_path,
        candidates=args.candidate or [FULL_MIX_PYIN],
        polyphonic_json=args.polyphonic_json,
        polyphonic_midi=args.polyphonic_midi,
        key=args.key,
        force=args.force,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
