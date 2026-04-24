"""
Build MIDI corpus index for the accompaniment RH renderer.

Reads:
  U:/MIDI-Library/midi_catalog.jsonl                 (identify_midi.py output)
  U:/MIDI-Library-Identified/Uncategorized/*.playable_rh.json  (batch_playable_rh.py output)

Writes:
  V:/data/playable_rh/<song_hash>.json               (copy, renamed to hash)
  V:/data/playable_rh/_alternates/<hash>__<n>.json   (duplicate matches, kept as backup)
  V:/data/playable_rh/_anomalies.tsv                 (sanity-check warnings)
  V:/data/midi_corpus_index.json                     (runtime index)

Usage:
  python scripts/build_midi_corpus_index.py                          # default paths
  python scripts/build_midi_corpus_index.py --force                  # overwrite V: copies
  python scripts/build_midi_corpus_index.py --dry-run                # list only, no copy
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path
from collections import defaultdict

DEFAULT_CATALOG = Path("U:/MIDI-Library/midi_catalog.jsonl")
DEFAULT_IDENTIFIED_DIR = Path("U:/MIDI-Library-Identified/Uncategorized")
DEFAULT_V_DATA = Path("V:/data")
DEFAULT_LIBRARY_CACHE = Path("V:/data/library_cache.json")

# Matches leading drive letter like "Z:/" or "Z:\" — identify_midi's manifest
# uses Z: for the music share; chord JSONs key by song_hash(path_without_drive).
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def backend_song_hash(matched_audio: str) -> str:
    """Replicate chord_cache.song_hash() for a matched_audio path.

    identify_midi emits paths like 'Z:/POP/J-POP\\Artist\\Title.flac' but the
    backend hashes the path after stripping drive letter and @N/ prefix (both
    normalize to the same form via chord_cache.song_hash). Strip the drive
    locally so the catalog's Z:/ paths map to the @1/ or bare-suffix keys used
    by data/chords/<hash>.json.
    """
    suffix = _DRIVE_RE.sub("", matched_audio)
    normalized = suffix.replace("\\", "/")
    # song_hash also strips @N/ prefix; our suffix has none, so skip that.
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]

_SAFE_CHARS = re.compile(r'[\\/:*?"<>|]+')
_WS = re.compile(r"\s+")


def _safe_name(s: str) -> str:
    s = _SAFE_CHARS.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s[:120] or "unknown"


def _expected_basename(artist: str, title: str) -> str:
    return f"{_safe_name(artist)} - {_safe_name(title)}.mid"


def _find_identified_midi(identified_dir: Path, artist: str, title: str) -> list[Path]:
    """Return all <artist> - <title>[ (N)].mid paths that exist in the Identified folder."""
    stem = os.path.splitext(_expected_basename(artist, title))[0]
    matches = []
    base = identified_dir / f"{stem}.mid"
    if base.is_file():
        matches.append(base)
    n = 2
    while True:
        cand = identified_dir / f"{stem} ({n}).mid"
        if cand.is_file():
            matches.append(cand)
            n += 1
        else:
            break
    return matches


def _midi_file_hash(path: Path, head: int = 64 * 1024) -> str:
    """MD5 of first 64KB + file size, first 16 hex chars.

    Mirrors tools/chroma_feat.file_hash so we can match entries in Identified/
    back to their original catalog rows (which store midi_hash keyed this way).
    """
    import hashlib as _hl
    h = _hl.md5()
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            h.update(f.read(head))
        h.update(str(size).encode())
        return h.hexdigest()[:16]
    except Exception:
        return ""


def _resolve_identified_for_midi_hash(
    identified_dir: Path, artist: str, title: str, target_hash: str,
    _cache: dict = {},
) -> tuple[Path | None, list[Path]]:
    """Locate the Identified folder's MIDI copy whose file_hash matches target_hash.

    Returns (chosen_path, other_candidates). When identify_midi dedupes two
    source MIDIs for the same (artist, title), one lands at "<name>.mid" and
    the other at "<name> (2).mid" — pure alphabetical sort picks the wrong one
    half the time, so we match by content hash instead.
    """
    cands = _find_identified_midi(identified_dir, artist, title)
    chosen = None
    others = []
    for c in cands:
        cache_key = str(c)
        h = _cache.get(cache_key)
        if h is None:
            h = _midi_file_hash(c)
            _cache[cache_key] = h
        if h == target_hash and chosen is None:
            chosen = c
        else:
            others.append(c)
    return chosen, others


def _playable_rh_sibling(midi_path: Path) -> Path:
    return midi_path.with_suffix("").with_suffix(".playable_rh.json")
    # Note: with_suffix twice because .mid has one suffix; we want .playable_rh.json
    # Actually midi_path has suffix .mid. midi_path.with_suffix('') gives "Artist - Title".
    # Then .with_suffix(".playable_rh.json") — but with_suffix keeps the stem.
    # Correct approach: string replace, since .playable_rh.json is a compound suffix.


def _playable_rh_path(midi_path: Path) -> Path:
    # .mid → .playable_rh.json (sibling in same folder)
    return midi_path.parent / (midi_path.stem + ".playable_rh.json")


def _sanity_check(clusters: list[dict]) -> list[str]:
    """Return list of anomaly tags for this corpus file. Empty = OK."""
    tags = []
    if not clusters:
        tags.append("empty")
        return tags
    all_pitches = [p for c in clusters for p in c.get("notes", [])]
    if not all_pitches:
        tags.append("no_notes")
        return tags
    total_dur = max(c.get("time", 0) + c.get("duration", 0) for c in clusters)
    if total_dur < 10.0:
        tags.append(f"short_{total_dur:.1f}s")
    # single-voice corpus: >90% clusters are 1 note
    single = sum(1 for c in clusters if len(c.get("notes", [])) <= 1)
    if single / len(clusters) > 0.90:
        tags.append("mono_voice")
    # stuck at floor: >80% pitches < 55 (G3) means pitch filter barely did anything
    low = sum(1 for p in all_pitches if p < 55)
    if low / len(all_pitches) > 0.80:
        tags.append("lowreg")
    return tags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--identified-dir", type=Path, default=DEFAULT_IDENTIFIED_DIR)
    ap.add_argument("--v-data", type=Path, default=DEFAULT_V_DATA)
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing V: copies even if mtime matches")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only report what would be done, no copy / no index write")
    ap.add_argument("--require-root-match", action=argparse.BooleanOptionalAction, default=True,
                    help="Drop catalog rows where root_match=False (the 12 classic "
                         "false-positive key-of-fifth mismatches). Default: on.")
    ap.add_argument("--min-cost", type=float, default=0.01,
                    help="Drop catalog rows with normalized_cost below this. Degenerate "
                         "MIDIs (thin chroma) can hit cost<0.01 on coincidentally similar "
                         "reference audio -- sounds nothing like the target. Default: 0.01")
    ap.add_argument("--ratio-lo", type=float, default=0.6,
                    help="Lower bound for MIDI-to-audio duration ratio. Below this means "
                         "the MIDI is much shorter than the song -- probably a partial loop.")
    ap.add_argument("--ratio-hi", type=float, default=1.4,
                    help="Upper bound for MIDI-to-audio duration ratio. identify_midi uses "
                         "subsequence DTW; when MIDI is much longer than audio, only the "
                         "matched window is reliable but our .playable_rh.json spans the "
                         "WHOLE MIDI. Cap at 1.4 to avoid serving 90s of mismatched notes.")
    ap.add_argument("--library-cache", type=Path, default=DEFAULT_LIBRARY_CACHE,
                    help="Path to library_cache.json for audio durations (required when "
                         "duration gate is active). Pre-built by music_api library scan.")
    args = ap.parse_args()

    if not args.catalog.is_file():
        print(f"[fatal] catalog not found: {args.catalog}", file=sys.stderr)
        return 1
    if not args.identified_dir.is_dir():
        print(f"[fatal] identified dir not found: {args.identified_dir}", file=sys.stderr)
        return 1

    # Load audio durations (backend song_hash -> seconds) from library_cache
    audio_dur: dict[str, float] = {}
    if args.library_cache.is_file():
        # song_hash(path) from chord_cache -- same form matched_audio resolves to
        # after drive-strip. Library cache paths are already relative/normalized.
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
            from chord_cache import song_hash as _sh  # type: ignore
            lib = json.loads(args.library_cache.read_text(encoding="utf-8"))
            for t in lib.get("tracks", []):
                p = t.get("path", "")
                d = t.get("duration", 0)
                if p and d:
                    audio_dur[_sh(p)] = float(d)
            print(f"       loaded {len(audio_dur)} audio durations from {args.library_cache.name}")
        except Exception as e:
            print(f"       [warn] failed to load library_cache, duration gate inactive: {e}", file=sys.stderr)
    else:
        print(f"       [warn] library_cache not found at {args.library_cache}; duration gate inactive", file=sys.stderr)

    out_rh_dir = args.v_data / "playable_rh"
    alt_dir = out_rh_dir / "_alternates"
    index_path = args.v_data / "midi_corpus_index.json"
    anom_path = out_rh_dir / "_anomalies.tsv"
    if not args.dry_run:
        out_rh_dir.mkdir(parents=True, exist_ok=True)
        alt_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 1: group matched catalog rows by backend song_hash, keep best by confidence ---
    # Catalog's "matched_hash" is a content hash (file_hash from chroma_feat),
    # NOT the path-based song_hash the backend uses for data/chords/<hash>.json.
    # We re-key here so the index lines up with how /api/ai/accompaniment looks
    # up chord data (via chord_cache.song_hash(path)).
    print(f"[1/4] reading catalog {args.catalog} ...", flush=True)
    print(f"       filters: require_root_match={args.require_root_match}  min_cost={args.min_cost}  "
          f"ratio=[{args.ratio_lo},{args.ratio_hi}]")
    by_hash: dict[str, list[dict]] = defaultdict(list)
    total_rows = 0
    matched_rows = 0
    drop_root = 0
    drop_cost = 0
    drop_ratio = 0
    drop_no_audio_dur = 0
    with args.catalog.open("r", encoding="utf-8") as f:
        for line in f:
            total_rows += 1
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("status") != "matched":
                continue
            matched_audio = r.get("matched_audio")
            if not matched_audio:
                continue
            if args.require_root_match and not r.get("root_match", False):
                drop_root += 1
                continue
            if float(r.get("normalized_cost") or 1.0) < args.min_cost:
                drop_cost += 1
                continue
            bh = backend_song_hash(matched_audio)
            # Duration ratio gate (catches subseq-DTW false positives where MIDI
            # is much longer than audio but one segment happens to match).
            ad = audio_dur.get(bh, 0.0)
            md = float(r.get("midi_end_s") or 0.0)
            if audio_dur and ad > 0 and md > 0:
                ratio = md / ad
                if ratio < args.ratio_lo or ratio > args.ratio_hi:
                    drop_ratio += 1
                    continue
                r["_log_abs_ratio"] = abs(math.log(ratio))
                r["_duration_ratio"] = ratio
            elif audio_dur:
                # We have cache but no duration for this song -- skip to be safe.
                drop_no_audio_dur += 1
                continue
            r["_backend_hash"] = bh
            matched_rows += 1
            by_hash[bh].append(r)
    print(f"       rows={total_rows}  matched={matched_rows}  unique_backend_hashes={len(by_hash)}")
    print(f"       dropped: root_mismatch={drop_root}  cost_below_min={drop_cost}  "
          f"ratio_out_of_range={drop_ratio}  no_audio_duration={drop_no_audio_dur}")

    # Pick best row per hash: closest duration-ratio-to-1 wins, then highest
    # confidence as tiebreaker. Pure-confidence ranking fools us in cases like
    # 五月天 風若吹 where the slightly-less-confident candidate (ratio 0.77)
    # was the right song and the higher-confidence one (ratio 2.06) was a
    # subsequence false-positive.
    for h, rows in by_hash.items():
        rows.sort(key=lambda r: (r.get("_log_abs_ratio", float("inf")),
                                  -float(r.get("confidence") or 0.0)))

    # --- Step 2: for each hash, locate .playable_rh.json sibling in Identified dir ---
    print(f"[2/4] locating .playable_rh.json in {args.identified_dir} ...", flush=True)
    entries = {}
    missing_midi = 0
    missing_rh = 0
    missing_hash_match = 0
    for h, rows in by_hash.items():
        best = rows[0]
        artist = best.get("artist", "")
        title = best.get("title", "")
        target_midi_hash = best.get("midi_hash", "")
        chosen_midi, other_cands = _resolve_identified_for_midi_hash(
            args.identified_dir, artist, title, target_midi_hash,
        )
        if chosen_midi is None:
            # Fallback: no content-hash match (file may have been renamed or
            # the midi_hash scheme changed). Skip — better than serving wrong
            # .playable_rh.json content.
            if not _find_identified_midi(args.identified_dir, artist, title):
                missing_midi += 1
            else:
                missing_hash_match += 1
            continue
        rh_path = _playable_rh_path(chosen_midi)
        if not rh_path.is_file():
            missing_rh += 1
            continue
        alternates = []
        for cand in other_cands:
            alt_rh = _playable_rh_path(cand)
            if alt_rh.is_file():
                alternates.append((cand, alt_rh))
        entries[h] = {
            "best": best,
            "midi": chosen_midi,
            "rh": rh_path,
            "alts": alternates,
        }
    print(f"       have_rh={len(entries)}  missing_midi_file={missing_midi}  "
          f"missing_rh_sibling={missing_rh}  hash_mismatch={missing_hash_match}")

    # --- Step 3: copy to V: + sanity check ---
    print(f"[3/4] copying to {out_rh_dir} (force={args.force}, dry_run={args.dry_run}) ...", flush=True)
    copied = 0
    skipped = 0
    anomalies: list[tuple[str, str, str, list[str]]] = []
    index: dict[str, dict] = {}

    for h, meta in entries.items():
        src = meta["rh"]
        dst = out_rh_dir / f"{h}.json"

        # copy (resume-safe)
        need_copy = True
        if dst.is_file() and not args.force:
            try:
                if abs(dst.stat().st_mtime - src.stat().st_mtime) < 1.0:
                    need_copy = False
            except Exception:
                pass
        if need_copy and not args.dry_run:
            try:
                shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                print(f"       [warn] copy failed {src} -> {dst}: {e}", file=sys.stderr)
                continue
        else:
            skipped += 1

        # sanity check on the copied (or source) file
        read_from = dst if dst.is_file() else src
        try:
            clusters = json.loads(read_from.read_text(encoding="utf-8"))
        except Exception as e:
            anomalies.append((h, str(src), "read_error", [str(e)[:60]]))
            continue
        tags = _sanity_check(clusters)
        if tags:
            anomalies.append((h, str(src.name), "", tags))

        # alternates → _alternates/
        for i, (alt_midi, alt_rh) in enumerate(meta["alts"], start=1):
            alt_dst = alt_dir / f"{h}__{i}.json"
            if not args.dry_run and (args.force or not alt_dst.is_file()):
                try:
                    shutil.copy2(alt_rh, alt_dst)
                except Exception:
                    pass

        best = meta["best"]
        index[h] = {
            "playable_rh_path": str(dst).replace("\\", "/"),
            "source_midi": str(meta["midi"]).replace("\\", "/"),
            "matched_audio": best.get("matched_audio", ""),
            "artist": best.get("artist", ""),
            "title": best.get("title", ""),
            "key_ref": best.get("key_ref", ""),
            "ref_bpm": best.get("ref_bpm"),
            "transposition": best.get("transposition", 0),
            "tempo_ratio": best.get("tempo_ratio"),
            "confidence": best.get("confidence", 0.0),
            "cluster_count": len(clusters) if isinstance(clusters, list) else 0,
            "anomalies": tags,
            "built_at": _dt.datetime.now().isoformat(timespec="seconds"),
        }

    print(f"       copied={copied}  skipped_existing={skipped}  total_in_index={len(index)}")

    # --- Step 4: write index + anomalies ---
    if args.dry_run:
        print(f"[4/4] dry-run: skipping index / anomaly writes")
        return 0
    index_blob = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True)
    index_path.write_text(index_blob, encoding="utf-8")
    print(f"[4/4] wrote index -> {index_path}  ({len(index)} entries)")

    # Mirror to dev repo so local QA on 8802 can load it without extra setup.
    # (The .playable_rh.json files live only on V:\; absolute V: paths in the
    # index work on both PC and NUC since V: is the same mounted share.)
    dev_index = Path(__file__).resolve().parent.parent / "data" / "midi_corpus_index.json"
    try:
        dev_index.parent.mkdir(parents=True, exist_ok=True)
        dev_index.write_text(index_blob, encoding="utf-8")
        print(f"       mirrored to dev repo -> {dev_index}")
    except Exception as e:
        print(f"       [warn] dev-repo mirror failed: {e}", file=sys.stderr)

    if anomalies:
        with anom_path.open("w", encoding="utf-8") as f:
            f.write("song_hash\tsource_name\terror\ttags\n")
            for h, name, err, tags in anomalies:
                f.write(f"{h}\t{name}\t{err}\t{','.join(tags)}\n")
        print(f"       wrote anomaly log -> {anom_path}  ({len(anomalies)} entries)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
