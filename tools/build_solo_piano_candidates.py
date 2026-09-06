r"""Build `solo_piano_polyphonic` RH-melody candidates for piano-solo songs.

Why: the resolver now promotes a cached solo_piano_polyphonic candidate when
the vocal gate refuses a song (LiveChord-a1lh), and the Phase 0.5 human A/B
had it winning 7/9 over full-mix pYIN on solo piano — but only 9 such
candidates existed. This batch fills the gap for the piano-solo part of the
library without Demucs: Basic Pitch (polyphonic, ONNX) -> skyline RH
selection (`select_right_hand_melody`) -> candidate file, exactly the path the
smoke rows used.

Selection (dry-run prints it; nothing is written):
  1. library_cache tracks with chords whose path / title / album / artist /
     genre matches --tokens (piano family + nocturne / etude / ballade)
  2. minus songs the vocal gate calls vocal (batch-log ratio >= --max-vocal-ratio)
     — a singer over piano is a vocal song, CREPE handles it
  3. minus songs that already have a solo_piano_polyphonic candidate
  4. --classifier adds songs the metadata song-type model calls solo_piano
     with confidence >= --min-solo-prob (weak model: 6 labelled examples)

Dry-run (default) shows counts per top folder, a sample, and — with
--probe N — transcribes N songs to measure seconds/song for the estimate.
--execute runs the batch; per-song JSONL goes to %TEMP% first and is copied
to <data-dir>/logs/solo_piano_candidates_<ts>.jsonl at the end (SMB-safe).

Examples (PC):
  python tools/build_solo_piano_candidates.py --probe 2
  python tools/build_solo_piano_candidates.py --execute --limit 200
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
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
from ai.melody_candidate import SOLO_PIANO_POLYPHONIC, candidate_dir, candidate_path  # noqa: E402

DEFAULT_DATA_DIR = Path(r"V:\data")
DEFAULT_TOKENS = r"piano|鋼琴|钢琴|klavier|pianoforte|nocturne|etude|étude|ballade|arabesque|gymnop|gnossienne"
# Piano in the title but not a piano solo: concertos, chamber works "for
# violin and piano", arrangements for other instruments, songs with a singer.
# "solo piano" in the text overrides the exclusion.
DEFAULT_EXCLUDE = r"concerto|orchestr|symphon|ensemble|\bband\b|violin|violon|viola|cello|violoncell|\bbass\b|contrabass|flute|flûte|clarinet|oboe|bassoon|\bhorn\b|trumpet|trombone|saxophon|\bsax\b|guitar|guitare|harp\b|harpsichord|organ\b|accordion|quartet|quintet|sextet|\btrio\b|\bduo\b|requiem|mass\b|choir|choral|chorus|\blied|feat\.|\bft\.|vocal|\bvoice|karaoke|\bopera\b|without keyboard"
LOG_GLOB = "rh_melody_candidates_*.jsonl"
POLY_INPUT_NAME = "solo_piano_polyphonic_input.json"


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
    for f in sorted(glob.glob(str(data_dir / "logs" / LOG_GLOB))):
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


def top_folder(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if parts and parts[0].startswith("@"):
        parts = parts[1:]
    return "/".join(parts[:2]) if len(parts) > 2 else (parts[0] if parts else "")


def select_songs(args: argparse.Namespace, data_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    tokens = re.compile(args.tokens, re.I)
    exclude = re.compile(args.exclude, re.I) if args.exclude else None
    solo_override = re.compile(r"solo\s*piano|piano\s*solo|鋼琴獨奏|钢琴独奏", re.I)
    lib = _load_json(data_dir / "library_cache.json", {}).get("tracks", []) or []
    ratios = load_log_ratios(data_dir)
    model = None
    if args.classifier:
        from ai.song_type_classifier import predict_metadata_nb

        model = _load_json(Path(args.classifier), {})
        if not model:
            print(f"classifier model unreadable: {args.classifier}", file=sys.stderr)
            model = None

    drops: Dict[str, int] = {"no_chords": 0, "no_token": 0, "excluded_ensemble": 0, "vocal_by_gate": 0, "has_candidate": 0, "classifier_only": 0}
    rows: List[Dict[str, Any]] = []
    for t in lib:
        path = str(t.get("path") or "")
        if not path or not t.get("has_chords"):
            drops["no_chords"] += 1
            continue
        text = " ".join(str(t.get(k) or "") for k in ("path", "title", "album", "artist", "genre"))
        hit_token = bool(tokens.search(text))
        hit_model = False
        prob = None
        if model is not None:
            pred = predict_metadata_nb(t, model)
            if pred.get("song_type") == "solo_piano":
                prob = float(pred.get("song_type_confidence") or 0.0)
                hit_model = prob >= args.min_solo_prob
        if not hit_token and not hit_model:
            drops["no_token"] += 1
            continue
        if exclude is not None and exclude.search(text) and not solo_override.search(text):
            drops["excluded_ensemble"] += 1
            continue
        h = make_song_hash(path)
        ratio = ratios.get(h)
        if ratio is not None and ratio >= args.max_vocal_ratio:
            drops["vocal_by_gate"] += 1
            continue
        if candidate_path(data_dir, h, SOLO_PIANO_POLYPHONIC).is_file() and not args.force:
            drops["has_candidate"] += 1
            continue
        if hit_model and not hit_token:
            drops["classifier_only"] += 1
        rows.append({
            "song_hash": h,
            "path": path,
            "duration": float(t.get("duration") or 0.0),
            "genre": str(t.get("genre") or ""),
            "folder": top_folder(path),
            "vocal_ratio": ratio,
            "why": ("token" if hit_token else "") + ("+model" if hit_model else ""),
            "solo_prob": prob,
        })
    rows.sort(key=lambda r: (r["folder"], r["path"]))
    if args.limit > 0:
        rows = rows[: args.limit]
    return rows, drops


# --------------------------------------------------------------------------
# per-song pipeline
# --------------------------------------------------------------------------
def _resolve_audio(path: str) -> str:
    try:
        from config import resolve_path

        return resolve_path(path)
    except Exception:
        return path


class _Transcriber:
    def __init__(self) -> None:
        from tools.run_basic_pitch_polyphonic_batch import BasicPitchPolyphonicTranscriber

        self.inner = BasicPitchPolyphonicTranscriber()

    def __call__(self, audio_path: str) -> List[Dict[str, Any]]:
        return self.inner.transcribe(audio_path)


def process_song(row: Dict[str, Any], *, data_dir: Path, transcribe, force: bool) -> Dict[str, Any]:
    from ai.melody_shadow_generator import generate_shadow_candidates

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
    poly = candidate_dir(data_dir, h) / POLY_INPUT_NAME
    try:
        t0 = time.perf_counter()
        if poly.is_file() and not force:
            rec["poly_status"] = "cached"
        else:
            notes = transcribe(audio)
            poly.parent.mkdir(parents=True, exist_ok=True)
            tmp = poly.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "schema_version": 1,
                "source": "basic_pitch_polyphonic",
                "song_hash": h,
                "path": row["path"],
                "audio_path": audio,
                "notes": notes,
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, poly)
            rec["poly_status"] = "generated"
            rec["poly_notes"] = len(notes)
        rec["transcribe_s"] = round(time.perf_counter() - t0, 2)

        t0 = time.perf_counter()
        res = generate_shadow_candidates(
            data_dir=data_dir,
            song_hash=h,
            path=row["path"],
            audio_path=audio,
            candidates=[SOLO_PIANO_POLYPHONIC],
            polyphonic_json=str(poly),
            force=force,
        )
        rec["select_s"] = round(time.perf_counter() - t0, 2)
        r0 = res.results[0] if res.results else None
        if r0 is None or not r0.ok:
            rec["status"] = "candidate_failed"
            rec["error"] = getattr(r0, "error", "") or getattr(r0, "status", "")
            return rec
        rec["candidate_status"] = r0.status
        rec["candidate_notes"] = (r0.details or {}).get("selected_notes")
        cand = candidate_path(data_dir, h, SOLO_PIANO_POLYPHONIC)
        rec["candidate_bytes"] = cand.stat().st_size if cand.is_file() else 0
        rec["status"] = "ok" if (rec["candidate_notes"] or 0) > 0 else "empty_candidate"
        rec["total_s"] = round((rec.get("transcribe_s") or 0) + rec["select_s"], 2)
        return rec
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt_h(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def print_dry_run(rows: List[Dict[str, Any]], drops: Dict[str, int], *, probe_s: Optional[float]) -> None:
    by_folder: Dict[str, int] = {}
    dur = 0.0
    for r in rows:
        by_folder[r["folder"]] = by_folder.get(r["folder"], 0) + 1
        dur += r["duration"]
    unknown_vr = sum(1 for r in rows if r["vocal_ratio"] is None)
    print(f"selected {len(rows)} songs, {dur / 3600:.1f} h of audio ({unknown_vr} without a vocal-gate measurement)")
    print("dropped:", ", ".join(f"{k}={v}" for k, v in drops.items()))
    print("by folder:")
    for k, v in sorted(by_folder.items(), key=lambda kv: -kv[1])[:30]:
        print(f"  {v:5d}  {k}")
    if len(by_folder) > 30:
        print(f"  ... {len(by_folder) - 30} more folders")
    print("sample:")
    step = max(1, len(rows) // 25)
    for r in rows[::step][:25]:
        vr = f"{r['vocal_ratio']:.2f}" if r["vocal_ratio"] is not None else "  - "
        print(f"  {r['song_hash']} vr={vr} {r['why']:11s} {r['path'][-80:]}")
    if probe_s is not None:
        est = probe_s * len(rows)
        print(f"\nprobe: {probe_s:.1f} s/song -> estimated {_fmt_h(est)} for {len(rows)} songs")
    else:
        print("\n(no --probe: add --probe 2 to time Basic Pitch on this machine)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--tokens", default=DEFAULT_TOKENS, help="Regex over path/title/album/artist/genre.")
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE, help="Regex that drops ensemble / vocal titles ('' to disable).")
    ap.add_argument("--max-vocal-ratio", type=float, default=0.15, help="Skip songs the vocal gate calls vocal.")
    ap.add_argument("--classifier", default="", help="metadata NB model JSON to add solo_piano predictions.")
    ap.add_argument("--min-solo-prob", type=float, default=0.8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="Rebuild even if a candidate exists.")
    ap.add_argument("--probe", type=int, default=0, help="Dry-run: transcribe N songs to time them (writes their inputs).")
    ap.add_argument("--execute", action="store_true", help="Run the batch; default is dry-run.")
    ap.add_argument("--list-out", default="", help="Dry-run: write selected hashes here (one per line).")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    rows, drops = select_songs(args, data_dir)
    if not rows:
        print("no songs selected", file=sys.stderr)
        print("dropped:", drops, file=sys.stderr)
        return 1

    if not args.execute:
        probe_s = None
        if args.probe > 0:
            transcribe = _Transcriber()
            times: List[float] = []
            for row in rows[: args.probe]:
                rec = process_song(row, data_dir=data_dir, transcribe=transcribe, force=True)
                print(f"  probe {rec.get('status')} transcribe={rec.get('transcribe_s')}s select={rec.get('select_s')}s "
                      f"poly_notes={rec.get('poly_notes')} candidate_notes={rec.get('candidate_notes')} {row['path'][-60:]}", flush=True)
                if rec.get("total_s"):
                    times.append(float(rec["total_s"]))
            probe_s = sum(times) / len(times) if times else None
        print_dry_run(rows, drops, probe_s=probe_s)
        if args.list_out:
            Path(args.list_out).write_text("\n".join(r["song_hash"] for r in rows) + "\n", encoding="utf-8")
            print(f"hash list -> {args.list_out}")
        return 0

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_jsonl = data_dir / "logs" / f"solo_piano_candidates_{ts}.jsonl"
    local_jsonl = Path(tempfile.gettempdir()) / out_jsonl.name
    transcribe = _Transcriber()
    records: List[Dict[str, Any]] = []
    with local_jsonl.open("a", encoding="utf-8") as fh:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['song_hash']} {row['path']}", flush=True)
            rec = process_song(row, data_dir=data_dir, transcribe=transcribe, force=args.force)
            records.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"    {rec.get('status')} transcribe={rec.get('transcribe_s')}s poly_notes={rec.get('poly_notes')} "
                  f"candidate_notes={rec.get('candidate_notes')} {rec.get('error', '')}", flush=True)

    by_status: Dict[str, int] = {}
    for r in records:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
    ok = [r for r in records if r.get("status") == "ok"]
    summary = {
        "songs": len(records),
        "by_status": by_status,
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
