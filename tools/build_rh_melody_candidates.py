"""PC batch worker: build RH melody candidates without persisting Demucs stems.

Per song:
  1. HTDemucs 4-stem separation into a LOCAL temp dir (never V:\\)
  2. stem energy ratios -> V:\\data\\melody_candidates\\<hh>\\<hash>\\stem_energy.json
  3. torchcrepe on the vocals stem -> .../vocal_stem_crepe.json
  4. delete the temp stems

The resolver (backend/ai/melody_resolver.py) reads the sidecar via
cached_stem_energy_features(), so no stem WAV is ever needed at serve time.

Default is a dry-run plan. Pass --execute to run. Per-song timing / disk
numbers go to <data-dir>/logs/rh_melody_candidates_<ts>.jsonl (+ .summary.json).

Examples:
  python tools/build_rh_melody_candidates.py --recent --favorites --rated --analytics
  python tools/build_rh_melody_candidates.py --recent --favorites --random 20 --limit 50 --execute
  python tools/build_rh_melody_candidates.py --hash 9a399f94b9e7 --execute --force
"""

from __future__ import annotations

import argparse
import json
import os
import random
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

from chord_cache import song_hash as make_song_hash  # noqa: E402
from ai.melody_candidate import VOCAL_STEM_CREPE, candidate_path  # noqa: E402
from ai.song_type_audio_features import (  # noqa: E402
    read_stem_energy_sidecar,
    stem_energy_features_from_paths,
    write_stem_energy_sidecar,
)
from ai.song_type_vocal_gate import classify_vocal_gate  # noqa: E402

DEFAULT_DATA_DIR = Path(r"V:\data")


# --------------------------------------------------------------------------
# song selection
# --------------------------------------------------------------------------
def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _add(rows: Dict[str, Dict[str, Any]], path: str, source: str, song_hash: str = "") -> None:
    path = str(path or "").strip()
    if not path and not song_hash:
        return
    h = song_hash or make_song_hash(path)
    row = rows.setdefault(h, {"song_hash": h, "path": path, "sources": []})
    row["path"] = row["path"] or path
    if source not in row["sources"]:
        row["sources"].append(source)


def _chord_json_path(data_dir: Path, song_hash: str) -> Path:
    return data_dir / "chords" / song_hash[:2] / f"{song_hash}.json"


def _path_from_chord_json(data_dir: Path, song_hash: str) -> str:
    data = _load_json(_chord_json_path(data_dir, song_hash), {})
    return str(data.get("path") or "") if isinstance(data, dict) else ""


def select_songs(args: argparse.Namespace, data_dir: Path) -> List[Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for h in args.hash:
        _add(rows, _path_from_chord_json(data_dir, h), "hash", song_hash=h)
    for p in args.path:
        _add(rows, p, "path")
    if args.hashes_file:
        for line in Path(args.hashes_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                _add(rows, _path_from_chord_json(data_dir, line), "file", song_hash=line)
    if args.recent:
        for item in _load_json(data_dir / "recent.json", {}).get("recent", []) or []:
            if isinstance(item, dict):
                _add(rows, item.get("path", ""), "recent", song_hash=str(item.get("hash") or ""))
    if args.favorites:
        for item in _load_json(data_dir / "favorites.json", {}).get("favorites", []) or []:
            if isinstance(item, dict):
                _add(rows, item.get("path", ""), "favorite", song_hash=str(item.get("hash") or ""))
    if args.rated:
        ratings = _load_json(data_dir / "ratings.json", {})
        for h in (ratings.keys() if isinstance(ratings, dict) else []):
            _add(rows, _path_from_chord_json(data_dir, h), "rated", song_hash=h)
        try:
            import sqlite3

            con = sqlite3.connect(data_dir / "feedback.db", timeout=10)
            for (h,) in con.execute("select distinct song_hash from ratings where song_hash != ''"):
                _add(rows, _path_from_chord_json(data_dir, h), "rated_db", song_hash=str(h))
            con.close()
        except Exception:
            pass
    if args.analytics:
        try:
            import sqlite3

            con = sqlite3.connect(data_dir / "analytics.db", timeout=10)
            for (payload,) in con.execute("select payload from events"):
                try:
                    d = json.loads(payload or "{}")
                except Exception:
                    continue
                raw = str(d.get("song_hash") or d.get("hash") or "").strip()
                if not raw:
                    continue
                if raw.startswith("__hash/"):
                    raw = raw[len("__hash/"):]
                if len(raw) == 12 and all(c in "0123456789abcdef" for c in raw):
                    _add(rows, _path_from_chord_json(data_dir, raw), "analytics", song_hash=raw)
                else:
                    _add(rows, raw, "analytics")  # payload stored a library path
            con.close()
        except Exception:
            pass
    if args.random > 0:
        chords_root = data_dir / "chords"
        shards = [d for d in chords_root.iterdir() if d.is_dir() and len(d.name) == 2]
        rng = random.Random(args.seed)
        picked = 0
        attempts = 0
        while picked < args.random and attempts < args.random * 20 and shards:
            attempts += 1
            shard = rng.choice(shards)
            files = [f for f in shard.glob("*.json") if len(f.stem) == 12 and f.suffix == ".json"]
            if not files:
                continue
            f = rng.choice(files)
            h = f.stem
            if h in rows:
                continue
            p = _path_from_chord_json(data_dir, h)
            if not p:
                continue
            _add(rows, p, "random", song_hash=h)
            picked += 1
    out, dropped = _prefilter(list(rows.values()), max_audio_mb=args.max_audio_mb)
    if dropped:
        by_reason: Dict[str, int] = {}
        for d in dropped:
            by_reason[d["skip_reason"]] = by_reason.get(d["skip_reason"], 0) + 1
        print(
            "pre-flight skipped "
            + ", ".join(f"{n} {reason}" for reason, n in sorted(by_reason.items())),
            file=sys.stderr,
        )
        for d in dropped:
            print(f"  skip({d['skip_reason']}) {d['song_hash']} {d['path']}", file=sys.stderr)
    if args.limit > 0:
        out = out[: args.limit]
    return out


# Chord-JSON paths that never resolve to an audio file on the NAS: uploads
# have their tmp audio deleted right after ingest, MIDI imports never had one.
_UNPROCESSABLE_PREFIXES = ("__upload/", "__midi/")


def _prefilter(rows: List[Dict[str, Any]], *, max_audio_mb: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Drop rows that would only burn a Demucs pass: empty / upload / midi
    paths and audio above ``max_audio_mb`` (0 disables the size check)."""
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for row in rows:
        path = str(row.get("path") or "").strip()
        reason = ""
        if not path:
            reason = "empty_path"
        elif path.startswith(_UNPROCESSABLE_PREFIXES):
            reason = path.split("/", 1)[0]
        elif max_audio_mb > 0:
            try:
                size = Path(_resolve_audio(path)).stat().st_size
            except OSError:
                size = 0  # missing file is reported as audio_not_found at run time
            if size > max_audio_mb * 1e6:
                reason = "oversize"
                row["audio_bytes"] = size
        if reason:
            row["skip_reason"] = reason
            dropped.append(row)
        else:
            kept.append(row)
    return kept, dropped


# --------------------------------------------------------------------------
# per-song pipeline
# --------------------------------------------------------------------------
def _resolve_audio(path: str) -> str:
    try:
        from config import resolve_path

        return resolve_path(path)
    except Exception:
        return path


def _tree_bytes(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _melody_context(data_dir: Path, song_hash: str) -> Dict[str, Any]:
    ctx = {"bpm": 120.0, "tempo_curve": None, "time_signature": "4/4"}
    data = _load_json(_chord_json_path(data_dir, song_hash), {})
    if isinstance(data, dict):
        try:
            ctx["bpm"] = float(data.get("bpm") or 120.0)
        except (TypeError, ValueError):
            pass
        ctx["tempo_curve"] = data.get("tempo_curve") or None
        ctx["time_signature"] = str(data.get("time_signature") or data.get("meter") or "4/4")
    return ctx


def process_song(
    row: Dict[str, Any],
    *,
    data_dir: Path,
    tmp_root: Path,
    force: bool,
    crepe_model: str,
    skip_crepe_below_gate: bool,
) -> Dict[str, Any]:
    from ai.stem_separator import StemSeparator
    from ai.vocal_melody_crepe import VocalStemCrepeExtractor

    song_hash = row["song_hash"]
    rec: Dict[str, Any] = {
        "song_hash": song_hash,
        "path": row["path"],
        "sources": row.get("sources", []),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    sidecar_file = data_dir / "melody_candidates" / song_hash[:2] / song_hash / "stem_energy.json"
    cand_file = candidate_path(data_dir, song_hash, VOCAL_STEM_CREPE)
    if not force and sidecar_file.is_file() and cand_file.is_file():
        rec["status"] = "skipped_existing"
        return rec

    audio = _resolve_audio(row["path"])
    if not Path(audio).is_file():
        rec["status"] = "audio_not_found"
        rec["audio_path"] = audio
        return rec
    rec["audio_path"] = audio
    rec["audio_bytes"] = Path(audio).stat().st_size

    work = Path(tempfile.mkdtemp(prefix=f"lc_stems_{song_hash}_", dir=str(tmp_root)))
    try:
        t0 = time.perf_counter()
        stems = StemSeparator(output_dir=str(work)).separate(audio)
        rec["demucs_s"] = round(time.perf_counter() - t0, 2)
        if not stems or not all(stems.get(n) for n in ("vocals", "bass", "drums", "other")):
            rec["status"] = "demucs_failed"
            rec["stems"] = stems or {}
            return rec
        rec["stems_bytes"] = _tree_bytes(work)

        t0 = time.perf_counter()
        feats = stem_energy_features_from_paths(stems)
        rec["energy_s"] = round(time.perf_counter() - t0, 2)
        gate = classify_vocal_gate({"duration_s": feats.get("stem_analyzed_duration_s"), "stems": feats})
        rec["vocal_stem_energy_ratio"] = feats.get("vocal_stem_energy_ratio")
        rec["stem_energy_ratio"] = feats.get("stem_energy_ratio")
        rec["gate"] = {"predict_vocal": gate.get("predict_vocal"), "reason": gate.get("reason")}
        out = write_stem_energy_sidecar(
            data_dir,
            song_hash,
            feats,
            extra={"path": row["path"], "separator": "htdemucs", "builder": "build_rh_melody_candidates"},
        )
        rec["sidecar_bytes"] = out.stat().st_size

        if skip_crepe_below_gate and not gate.get("predict_vocal"):
            rec["status"] = "sidecar_only_gate_failed"
            return rec

        ctx = _melody_context(data_dir, song_hash)
        t0 = time.perf_counter()
        res = VocalStemCrepeExtractor(data_dir=data_dir).extract_to_cache(
            song_hash=song_hash,
            path=row["path"],
            vocal_stem_path=stems["vocals"],
            bpm=ctx["bpm"],
            tempo_curve=ctx["tempo_curve"],
            time_signature=ctx["time_signature"],
            model=crepe_model,
        )
        rec["crepe_s"] = round(time.perf_counter() - t0, 2)
        if not res.ok:
            rec["status"] = "crepe_failed"
            rec["error"] = res.error
            return rec
        rec["candidate_file"] = res.cache_file
        rec["candidate_bytes"] = Path(res.cache_file).stat().st_size
        stats = (res.payload or {}).get("melody_stats") or {}
        rec["candidate_notes"] = len((res.payload or {}).get("melody") or [])
        rec["candidate_active_duration_s"] = stats.get("active_duration_s")
        rec["status"] = "ok"
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec
    finally:
        shutil.rmtree(work, ignore_errors=True)
        rec["finished_at"] = datetime.now(timezone.utc).isoformat()
        rec["total_s"] = round(
            sum(float(rec.get(k) or 0.0) for k in ("demucs_s", "energy_s", "crepe_s")), 2
        )


# --------------------------------------------------------------------------
def _summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in records if r.get("status") in ("ok", "sidecar_only_gate_failed")]
    by_status: Dict[str, int] = {}
    for r in records:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1

    def _avg(key: str) -> Optional[float]:
        vals = [float(r[key]) for r in ok if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def _sum(key: str) -> int:
        return int(sum(int(r.get(key) or 0) for r in ok))

    n_vocal = sum(1 for r in ok if (r.get("gate") or {}).get("predict_vocal"))
    return {
        "songs": len(records),
        "by_status": by_status,
        "avg_demucs_s": _avg("demucs_s"),
        "avg_energy_s": _avg("energy_s"),
        "avg_crepe_s": _avg("crepe_s"),
        "avg_total_s": _avg("total_s"),
        "avg_stems_bytes_transient": _avg("stems_bytes"),
        "persisted_bytes_total": _sum("sidecar_bytes") + _sum("candidate_bytes"),
        "avg_persisted_bytes": round((_sum("sidecar_bytes") + _sum("candidate_bytes")) / len(ok), 1) if ok else None,
        "vocal_gate_pass": n_vocal,
        "vocal_gate_fail": len(ok) - n_vocal,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--hash", action="append", default=[])
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("--hashes-file", default="")
    ap.add_argument("--recent", action="store_true")
    ap.add_argument("--favorites", action="store_true")
    ap.add_argument("--rated", action="store_true", help="ratings.json + feedback.db rated songs.")
    ap.add_argument("--analytics", action="store_true", help="Songs with play events in analytics.db.")
    ap.add_argument("--random", type=int, default=0, help="Random sample from chords index.")
    ap.add_argument("--seed", type=int, default=20260902)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--max-audio-mb",
        type=float,
        default=300.0,
        help="Pre-flight skip audio larger than this (Demucs stalls on very long files). 0 disables.",
    )
    ap.add_argument("--execute", action="store_true", help="Run Demucs + CREPE; default is dry-run plan.")
    ap.add_argument("--force", action="store_true", help="Rebuild even if sidecar + candidate exist.")
    ap.add_argument("--crepe-model", default="full", choices=["full", "tiny"])
    ap.add_argument(
        "--skip-crepe-below-gate",
        action="store_true",
        help="Only run CREPE when the vocal gate passes (production mode). Default runs CREPE always for measurement.",
    )
    ap.add_argument("--tmp-root", default="", help="Local dir for transient stems (default: system temp).")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    rows = select_songs(args, data_dir)
    if not rows:
        print("no songs selected", file=sys.stderr)
        return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = log_dir / f"rh_melody_candidates_{ts}.jsonl"
    # Write the per-song log locally: an SMB hiccup on V:\ mid-run must not
    # abort the batch. The file is copied to <data-dir>/logs at the end.
    local_jsonl = Path(tempfile.gettempdir()) / out_jsonl.name

    if not args.execute:
        planned = 0
        for r in rows:
            h = r["song_hash"]
            have = read_stem_energy_sidecar(data_dir, h) is not None and candidate_path(data_dir, h, VOCAL_STEM_CREPE).is_file()
            state = "skip(existing)" if have and not args.force else "plan"
            planned += state == "plan"
            print(f"{state:15s} {h} [{','.join(r['sources'])}] {r['path']}")
        print(f"\n{planned}/{len(rows)} songs would be processed. Add --execute to run.")
        return 0

    tmp_root = Path(args.tmp_root) if args.tmp_root else Path(tempfile.gettempdir())
    tmp_root.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    with local_jsonl.open("a", encoding="utf-8") as fh:
        for i, row in enumerate(rows, 1):
            print(f"[{i}/{len(rows)}] {row['song_hash']} {row['path']}", flush=True)
            rec = process_song(
                row,
                data_dir=data_dir,
                tmp_root=tmp_root,
                force=args.force,
                crepe_model=args.crepe_model,
                skip_crepe_below_gate=args.skip_crepe_below_gate,
            )
            records.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(
                f"    {rec.get('status')} demucs={rec.get('demucs_s')}s crepe={rec.get('crepe_s')}s "
                f"vocal_ratio={rec.get('vocal_stem_energy_ratio')} gate={(rec.get('gate') or {}).get('predict_vocal')} "
                f"notes={rec.get('candidate_notes')}",
                flush=True,
            )

    summary = _summarize(records)
    summary["log"] = str(out_jsonl)
    try:
        shutil.copy2(local_jsonl, out_jsonl)
        (out_jsonl.with_suffix(".summary.json")).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        summary["log"] = str(local_jsonl)
        summary["log_copy_error"] = f"{type(exc).__name__}: {exc}"
        local_jsonl.with_suffix(".summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
