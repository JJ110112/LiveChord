"""Quarantine chord JSONs tagged ``source: midi`` and prune the index.

Background — ``_midi_matches`` in ``backend/chord_api.py`` uses a bare
substring check without word boundaries. Short audio track names match
unrelated MIDI files in the library (``"Vem" ⊂ "movement"``,
``"You"`` matches 50 candidates, etc.), and historically the auto-worker
wrote those false matches back to disk as ``source: midi`` chord JSONs.

Auto MIDI import has been off since 2026-05-11 (commit d479832), but
~10k legacy MIDI-sourced chord JSONs remain on disk and serve the wrong
chord progressions to the player. The user no longer relies on
MIDI-import/upload, so the cleanest fix is:

  1. **Quarantine** every ``source: midi`` chord JSON under
     ``data/chords_midi_quarantine/<bucket>/<hash>.json`` so the move is
     reversible if any of them turn out to be correct hand-imports.
  2. **Prune** the matching entries from ``data/chord_index.json``.
  3. Restart auto-worker — the library scan will re-queue those tracks
     for BTC because their hash no longer appears in the chord index.

Run with ``--dry-run`` to print stats only. ``--commit`` performs the
quarantine + index prune. ``--restore`` reverses an earlier quarantine
(move quarantined files back, re-add their index entries by re-reading
them).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def _resolve_data_root() -> Path:
    # Allow override via env var so this script runs on any host whose
    # V: drive is mounted at a non-default location.
    env = os.environ.get("LIVECHORD_DATA_ROOT")
    if env:
        return Path(env)
    return Path("V:/data")


def _load_index(index_file: Path) -> dict:
    return json.loads(index_file.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _chord_file(chords_root: Path, hash_id: str) -> Path:
    return chords_root / hash_id[:2] / f"{hash_id}.json"


def _quarantine_file(quarantine_root: Path, hash_id: str) -> Path:
    return quarantine_root / hash_id[:2] / f"{hash_id}.json"


def cmd_quarantine(data_root: Path, commit: bool) -> int:
    index_file = data_root / "chord_index.json"
    chords_root = data_root / "chords"
    quarantine_root = data_root / "chords_midi_quarantine"

    if not index_file.is_file():
        print(f"ERR: missing {index_file}")
        return 2

    index = _load_index(index_file)
    midi_hashes = sorted(
        h for h, v in index.items()
        if isinstance(v, dict) and (v.get("source") or "").lower() == "midi"
    )
    print(f"index has {len(index)} entries, {len(midi_hashes)} with source=midi")

    on_disk = 0
    missing = 0
    for h in midi_hashes:
        if _chord_file(chords_root, h).is_file():
            on_disk += 1
        else:
            missing += 1
    print(f"  on disk: {on_disk}")
    print(f"  index says midi but file missing: {missing}")

    if not commit:
        print("\n(dry-run; pass --commit to move files + prune index)")
        return 0

    quarantine_root.mkdir(parents=True, exist_ok=True)
    manifest_file = quarantine_root / f"_manifest_{int(time.time())}.json"

    moved = 0
    failures: list[dict] = []
    manifest_rows: list[dict] = []
    t0 = time.time()
    # Phase 1: move files only. The chord-index prune happens in a separate
    # pass after re-reading the index so any entries the NUC's auto-worker
    # writes during the (multi-minute) move phase are not clobbered.
    for i, h in enumerate(midi_hashes, start=1):
        src = _chord_file(chords_root, h)
        dst = _quarantine_file(quarantine_root, h)
        moved_this = False
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved += 1
                moved_this = True
            except Exception as e:
                failures.append({"hash": h, "stage": "move", "error": str(e)})
        manifest_rows.append({"hash": h, "moved": moved_this})
        if i % 500 == 0:
            print(f"  ... {i}/{len(midi_hashes)} ({time.time()-t0:.1f}s)")

    # Phase 2: prune index. Re-read fresh — any new entries the worker
    # added in between are preserved; we only delete the midi-marked
    # hashes we just quarantined.
    index_fresh = _load_index(index_file)
    pruned = 0
    midi_hash_set = set(midi_hashes)
    for h in list(index_fresh.keys()):
        if h in midi_hash_set:
            del index_fresh[h]
            pruned += 1
    _atomic_write_json(index_file, index_fresh)
    manifest_file.write_text(
        json.dumps({"moved": moved, "pruned": pruned, "failures": failures,
                    "rows": manifest_rows}, ensure_ascii=False),
        encoding="utf-8",
    )

    print()
    print(f"moved to quarantine: {moved}")
    print(f"pruned from chord_index.json: {pruned}")
    print(f"failures: {len(failures)}")
    print(f"manifest: {manifest_file}")
    print(f"elapsed: {time.time()-t0:.1f}s")
    return 0


def cmd_restore(data_root: Path, manifest_path: Path) -> int:
    """Reverse a prior quarantine run using its manifest."""
    index_file = data_root / "chord_index.json"
    chords_root = data_root / "chords"
    quarantine_root = data_root / "chords_midi_quarantine"

    if not manifest_path.is_file():
        print(f"ERR: missing manifest {manifest_path}")
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest.get("rows", [])
    print(f"manifest covers {len(rows)} hashes")

    index = _load_index(index_file)
    restored_files = 0
    restored_index = 0
    failures: list[dict] = []

    for row in rows:
        h = row.get("hash")
        if not h:
            continue
        src = _quarantine_file(quarantine_root, h)
        dst = _chord_file(chords_root, h)
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                restored_files += 1
            except Exception as e:
                failures.append({"hash": h, "stage": "move", "error": str(e)})
                continue
        if dst.is_file() and h not in index:
            try:
                data = json.loads(dst.read_text(encoding="utf-8"))
                entry = {
                    "source": data.get("source") or "midi",
                    "chord_count": len(data.get("chords", []) or []),
                    "chord_key": data.get("key", "") or "",
                    "mtime": dst.stat().st_mtime,
                }
                # Best-effort chord_list / unique_chords (not strictly needed).
                names = [c.get("chord") for c in (data.get("chords") or [])
                         if isinstance(c, dict) and c.get("chord")]
                if names:
                    uniq = sorted({n for n in names})
                    entry["chord_list"] = uniq
                    entry["unique_chords"] = len(uniq)
                index[h] = entry
                restored_index += 1
            except Exception as e:
                failures.append({"hash": h, "stage": "reindex", "error": str(e)})

    _atomic_write_json(index_file, index)
    print(f"restored files: {restored_files}")
    print(f"restored index entries: {restored_index}")
    print(f"failures: {len(failures)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_q = sub.add_parser("quarantine", help="move source=midi chord JSONs to quarantine + prune index")
    p_q.add_argument("--commit", action="store_true",
                     help="actually move files; without this it's a dry-run")
    p_r = sub.add_parser("restore", help="reverse an earlier quarantine using its manifest")
    p_r.add_argument("--manifest", required=True, help="path to _manifest_<ts>.json")

    args = parser.parse_args()
    data_root = _resolve_data_root()
    print(f"data_root: {data_root}")

    if args.cmd == "quarantine":
        return cmd_quarantine(data_root, commit=args.commit)
    if args.cmd == "restore":
        return cmd_restore(data_root, Path(args.manifest))
    return 1


if __name__ == "__main__":
    sys.exit(main())
