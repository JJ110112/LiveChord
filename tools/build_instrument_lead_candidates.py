r"""Build `instrument_lead` RH-melody candidates for non-piano instrumentals.

Why: the resolver promotes a cached instrument_lead candidate when the vocal
gate refuses a song (LiveChord-a1lh / aq98), and the Phase 0.5 human A/B had
it winning over full-mix pYIN whenever a real lead line exists (Take Five,
Again And Again) — but only 6 candidates existed. Solo piano is covered by
build_solo_piano_candidates.py; this one handles the rest of the
instrumental folders.

Per song (mirrors build_rh_melody_candidates.py, stems are transient):
  Demucs -> temp dir -> stem-energy sidecar + vocal gate
    gate says vocal  -> vocal_stem_crepe candidate (stems are here anyway)
    gate refuses     -> torchcrepe on the "other" stem -> instrument_lead
  temp stems deleted; ~150 MB transient, ~100 KB persisted per song.

Selection (dry-run prints it; nothing is written):
  library_cache tracks with chords under --folders (top folder after the @N
  root, e.g. Jazz, Relax, Other/Soundtracks), minus __upload/ __midi/ paths,
  minus files > --max-audio-mb or longer than --max-duration-min, minus songs
  the batch logs already gate as vocal (ratio >= 0.15), minus songs that
  already have an instrument_lead or solo_piano_polyphonic candidate.

Examples (PC, RTX 5080):
  python tools/build_instrument_lead_candidates.py
  python tools/build_instrument_lead_candidates.py --folders Jazz --execute --limit 50
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chord_cache import song_hash as make_song_hash  # noqa: E402
from ai.melody_candidate import (  # noqa: E402
    INSTRUMENT_LEAD,
    SOLO_PIANO_POLYPHONIC,
    VOCAL_STEM_CREPE,
    candidate_path,
)
from ai.song_type_audio_features import (  # noqa: E402
    read_stem_energy_sidecar,
    stem_energy_features_from_paths,
    write_stem_energy_sidecar,
)
from ai.song_type_vocal_gate import (  # noqa: E402
    apply_vocal_gate_override,
    classify_vocal_gate,
    load_vocal_gate_overrides,
)
from tools.build_rh_melody_candidates import (  # noqa: E402
    _UNPROCESSABLE_PREFIXES,
    _melody_context,
    _resolve_audio,
    _tree_bytes,
)

DEFAULT_DATA_DIR = Path(r"V:\data")
DEFAULT_FOLDERS = "Jazz,Relax,Other/Soundtracks"
LOG_GLOBS = ("rh_melody_candidates_*.jsonl", "instrument_lead_candidates_*.jsonl")
EST_S_PER_SONG = 13.3  # measured avg_total_s of the 263-song Libera RH batch on the RTX 5080


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_log_ratios(data_dir: Path) -> Dict[str, float]:
    out: Dict[str, float] = {}
    files = [f for pattern in LOG_GLOBS for f in glob.glob(str(data_dir / "logs" / pattern))]
    for f in sorted(files):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    x = r.get("vocal_stem_energy_ratio")
                    if x is not None and r.get("song_hash"):
                        out[str(r["song_hash"])] = float(x)
        except OSError:
            continue
    return out


def _rel_parts(path: str) -> List[str]:
    parts = path.replace("\\", "/").split("/")
    if parts and parts[0].startswith("@"):
        parts = parts[1:]
    return parts


def top_folder(path: str, depth: int = 2) -> str:
    parts = _rel_parts(path)
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1])


def _in_folders(path: str, folders: List[str]) -> bool:
    rel = "/".join(_rel_parts(path))
    return any(rel.startswith(f.rstrip("/") + "/") for f in folders)


def select_songs(args: argparse.Namespace, data_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    folders = [f.strip() for f in args.folders.split(",") if f.strip()]
    lib = _load_json(data_dir / "library_cache.json", {}).get("tracks", []) or []
    ratios = load_log_ratios(data_dir)
    drops: Dict[str, int] = {
        "no_chords": 0, "outside_folders": 0, "unprocessable_path": 0, "too_long": 0,
        "vocal_by_gate": 0, "not_measured": 0, "has_instrument_lead": 0, "has_solo_piano": 0,
    }
    rows: List[Dict[str, Any]] = []
    for t in lib:
        path = str(t.get("path") or "")
        if not path or not t.get("has_chords"):
            drops["no_chords"] += 1
            continue
        if path.startswith(_UNPROCESSABLE_PREFIXES):
            drops["unprocessable_path"] += 1
            continue
        if not _in_folders(path, folders):
            drops["outside_folders"] += 1
            continue
        if float(t.get("duration") or 0.0) > args.max_duration_min * 60:
            drops["too_long"] += 1
            continue
        h = make_song_hash(path)
        ratio = ratios.get(h)
        if ratio is not None and ratio >= args.max_vocal_ratio:
            drops["vocal_by_gate"] += 1
            continue
        if args.only_measured and ratio is None:
            drops["not_measured"] += 1
            continue
        if not args.force and candidate_path(data_dir, h, INSTRUMENT_LEAD).is_file():
            drops["has_instrument_lead"] += 1
            continue
        if candidate_path(data_dir, h, SOLO_PIANO_POLYPHONIC).is_file():
            drops["has_solo_piano"] += 1
            continue
        rows.append({
            "song_hash": h,
            "path": path,
            "duration": float(t.get("duration") or 0.0),
            "folder": top_folder(path),
            "vocal_ratio": ratio,
        })
    # Songs the batch logs already gate as instrumental go first: they are the
    # sure wins. Unmeasured songs (never through Demucs) follow, by folder.
    rows.sort(key=lambda r: (r["vocal_ratio"] is None, r["folder"], r["path"]))
    if args.limit > 0:
        rows = rows[: args.limit]
    return rows, drops


def _size_filter(rows: List[Dict[str, Any]], max_audio_mb: float) -> Tuple[List[Dict[str, Any]], int]:
    """stat() each audio file; drop oversize ones (Demucs stalls on very long files)."""
    if max_audio_mb <= 0:
        return rows, 0
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for r in rows:
        try:
            size = Path(_resolve_audio(r["path"])).stat().st_size
        except OSError:
            size = 0
        if size > max_audio_mb * 1e6:
            dropped += 1
            continue
        r["audio_bytes"] = size
        kept.append(r)
    return kept, dropped


# --------------------------------------------------------------------------
# per-song pipeline
# --------------------------------------------------------------------------
def process_song(row: Dict[str, Any], *, data_dir: Path, tmp_root: Path, force: bool, crepe_model: str) -> Dict[str, Any]:
    from ai.stem_separator import StemSeparator
    from ai.vocal_melody_crepe import VocalStemCrepeExtractor

    h = row["song_hash"]
    rec: Dict[str, Any] = {
        "song_hash": h,
        "path": row["path"],
        "folder": row["folder"],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    audio = _resolve_audio(row["path"])
    if not Path(audio).is_file():
        rec["status"] = "audio_not_found"
        rec["audio_path"] = audio
        return rec
    rec["audio_bytes"] = Path(audio).stat().st_size

    work = Path(tempfile.mkdtemp(prefix=f"lc_stems_{h}_", dir=str(tmp_root)))
    try:
        t0 = time.perf_counter()
        stems = StemSeparator(output_dir=str(work)).separate(audio)
        rec["demucs_s"] = round(time.perf_counter() - t0, 2)
        if not stems or not all(stems.get(n) for n in ("vocals", "bass", "drums", "other")):
            rec["status"] = "demucs_failed"
            return rec
        rec["stems_bytes"] = _tree_bytes(work)

        feats = stem_energy_features_from_paths(stems)
        gate = classify_vocal_gate({"duration_s": feats.get("stem_analyzed_duration_s"), "stems": feats})
        gate = apply_vocal_gate_override(gate, h, load_vocal_gate_overrides(data_dir))
        rec["vocal_stem_energy_ratio"] = feats.get("vocal_stem_energy_ratio")
        rec["gate"] = {"predict_vocal": gate.get("predict_vocal"), "reason": gate.get("reason")}
        if force or read_stem_energy_sidecar(data_dir, h) is None:
            write_stem_energy_sidecar(
                data_dir, h, feats,
                extra={"path": row["path"], "separator": "htdemucs", "builder": "build_instrument_lead_candidates"},
            )

        ctx = _melody_context(data_dir, h)
        crepe = VocalStemCrepeExtractor(data_dir=data_dir)
        if gate.get("predict_vocal"):
            # Not an instrumental after all: give it the vocal candidate instead.
            rec["route"] = "vocal"
            if not force and candidate_path(data_dir, h, VOCAL_STEM_CREPE).is_file():
                rec["status"] = "vocal_candidate_exists"
                return rec
            stem_path, cand_id, label, algo, empty_flag, song_type = (
                stems["vocals"], VOCAL_STEM_CREPE, "vocals", "htdemucs.vocals+torchcrepe.full",
                "empty_vocal_stem", "vocal_led",
            )
        else:
            rec["route"] = "instrument_lead"
            stem_path, cand_id, label, algo, empty_flag, song_type = (
                stems["other"], INSTRUMENT_LEAD, "other", "htdemucs.other+torchcrepe.full",
                "empty_instrument_lead", "instrumental",
            )
        t0 = time.perf_counter()
        res = crepe.extract_stem_to_cache(
            song_hash=h, path=row["path"], stem_path=stem_path, candidate_id=cand_id,
            stem_label=label, algorithm=algo, empty_flag=empty_flag, song_type=song_type,
            bpm=float(ctx["bpm"]), tempo_curve=ctx["tempo_curve"], time_signature=ctx["time_signature"],
            model=crepe_model,
        )
        rec["crepe_s"] = round(time.perf_counter() - t0, 2)
        if not res.ok:
            rec["status"] = "crepe_failed"
            rec["error"] = res.error
            return rec
        cand = candidate_path(data_dir, h, cand_id)
        payload = _load_json(cand, {})
        rec["candidate_notes"] = len(payload.get("melody") or [])
        rec["candidate_bytes"] = cand.stat().st_size if cand.is_file() else 0
        rec["status"] = "ok" if rec["candidate_notes"] > 0 else "empty_candidate"
        rec["total_s"] = round(rec["demucs_s"] + rec["crepe_s"], 2)
        return rec
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt_h(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def print_dry_run(rows: List[Dict[str, Any]], drops: Dict[str, int], oversize: int) -> None:
    by_folder: Dict[str, int] = {}
    dur = 0.0
    for r in rows:
        by_folder[r["folder"]] = by_folder.get(r["folder"], 0) + 1
        dur += r["duration"]
    unknown = sum(1 for r in rows if r["vocal_ratio"] is None)
    print(f"selected {len(rows)} songs, {dur / 3600:.1f} h of audio ({unknown} without a vocal-gate measurement, gate decided after Demucs)")
    print("dropped:", ", ".join(f"{k}={v}" for k, v in drops.items()) + f", oversize_audio={oversize}")
    print("by folder:")
    for k, v in sorted(by_folder.items(), key=lambda kv: -kv[1])[:30]:
        print(f"  {v:5d}  {k}")
    if len(by_folder) > 30:
        print(f"  ... {len(by_folder) - 30} more folders")
    print("sample:")
    step = max(1, len(rows) // 20)
    for r in rows[::step][:20]:
        vr = f"{r['vocal_ratio']:.2f}" if r["vocal_ratio"] is not None else "  - "
        print(f"  {r['song_hash']} vr={vr} {r['path'][-80:]}")
    print(f"\nestimate at {EST_S_PER_SONG} s/song (Demucs + CREPE, RTX 5080): {_fmt_h(EST_S_PER_SONG * len(rows))}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--folders", default=DEFAULT_FOLDERS, help="Comma-separated top folders (after the @N root).")
    ap.add_argument("--max-vocal-ratio", type=float, default=0.15)
    ap.add_argument("--only-measured", action="store_true",
                    help="Only songs a previous batch already measured as instrumental (skip the Demucs-to-find-out half).")
    ap.add_argument("--max-duration-min", type=float, default=30.0)
    ap.add_argument("--max-audio-mb", type=float, default=300.0, help="0 disables the size check.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--crepe-model", default="full", choices=["full", "tiny"])
    ap.add_argument("--tmp-root", default="", help="Local dir for transient stems (default: system temp).")
    ap.add_argument("--execute", action="store_true", help="Run the batch; default is dry-run.")
    ap.add_argument("--list-out", default="")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    rows, drops = select_songs(args, data_dir)
    rows, oversize = _size_filter(rows, args.max_audio_mb)
    if not rows:
        print("no songs selected", file=sys.stderr)
        print("dropped:", drops, file=sys.stderr)
        return 1

    if not args.execute:
        print_dry_run(rows, drops, oversize)
        if args.list_out:
            Path(args.list_out).write_text("\n".join(r["song_hash"] for r in rows) + "\n", encoding="utf-8")
            print(f"hash list -> {args.list_out}")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_jsonl = data_dir / "logs" / f"instrument_lead_candidates_{ts}.jsonl"
    local_jsonl = Path(tempfile.gettempdir()) / out_jsonl.name
    tmp_root = Path(args.tmp_root) if args.tmp_root else Path(tempfile.gettempdir())
    tmp_root.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    with local_jsonl.open("a", encoding="utf-8") as fh:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['song_hash']} {row['path']}", flush=True)
            rec = process_song(row, data_dir=data_dir, tmp_root=tmp_root, force=args.force, crepe_model=args.crepe_model)
            records.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"    {rec.get('status')} route={rec.get('route')} demucs={rec.get('demucs_s')}s crepe={rec.get('crepe_s')}s "
                  f"vr={rec.get('vocal_stem_energy_ratio')} notes={rec.get('candidate_notes')} {rec.get('error', '')}", flush=True)

    by_status: Dict[str, int] = {}
    by_route: Dict[str, int] = {}
    for r in records:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
        by_route[str(r.get("route"))] = by_route.get(str(r.get("route")), 0) + 1
    ok = [r for r in records if r.get("status") == "ok"]
    summary = {
        "songs": len(records),
        "by_status": by_status,
        "by_route": by_route,
        "avg_total_s": round(sum(float(r.get("total_s") or 0) for r in ok) / len(ok), 2) if ok else None,
        "avg_candidate_notes": round(sum(int(r.get("candidate_notes") or 0) for r in ok) / len(ok), 1) if ok else None,
        "log": str(out_jsonl),
    }
    try:
        out_jsonl.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_jsonl, out_jsonl)
        out_jsonl.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        summary["log"] = str(local_jsonl)
        summary["log_copy_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
