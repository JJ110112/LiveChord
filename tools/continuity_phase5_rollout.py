#!/usr/bin/env python3
"""Phase 5 rollout helper for note-duration continuity.

Default mode is dry-run. It selects likely high-value songs, reports which
current-engine accompaniment caches are present or missing, audits readable
duration/schema metrics, and writes JSON + Markdown reports. Use ``--execute``
only when intentionally prewarming a small public/production set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


DEFAULT_STYLES = ["Arpeggio", "Block"]
DEFAULT_LEVELS = ["L1"]
DEFAULT_INSTRUMENTS = ["piano"]


def stable_song_hash(path: str) -> str:
    value = str(path or "").replace("\\", "/")
    value = re.sub(r"^@\d+/", "", value)
    return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def chord_path_for_hash(data_dir: Path, song_hash: str) -> Path:
    sharded = data_dir / "chords" / song_hash[:2] / f"{song_hash}.json"
    if sharded.is_file():
        return sharded
    demo = data_dir / "demo" / "chords" / f"{song_hash}.json"
    if demo.is_file():
        return demo
    return sharded


def iter_chord_paths(data_dir: Path) -> Iterable[Path]:
    chords_dir = data_dir / "chords"
    if not chords_dir.is_dir():
        return
    for bucket in sorted(chords_dir.iterdir()):
        if not bucket.is_dir() or len(bucket.name) != 2:
            continue
        for path in sorted(bucket.glob("*.json")):
            if path.suffix == ".json":
                yield path


@dataclass
class Candidate:
    song_hash: str
    score: float = 0.0
    sources: Dict[str, float] = field(default_factory=dict)
    path: str = ""
    title: str = ""
    bpm: Optional[float] = None
    time_signature: str = "unknown"
    chord_count: int = 0
    has_chords: bool = False
    has_melody: bool = False
    current_cache_count: int = 0

    def add(self, source: str, points: float) -> None:
        self.sources[source] = self.sources.get(source, 0.0) + points
        self.score += points

    def to_report(self) -> Dict[str, Any]:
        return {
            "hash": self.song_hash,
            "score": round(self.score, 3),
            "sources": dict(sorted(self.sources.items())),
            "path": self.path,
            "title": self.title,
            "bpm": self.bpm,
            "time_signature": self.time_signature,
            "chord_count": self.chord_count,
            "has_chords": self.has_chords,
            "has_melody": self.has_melody,
            "current_cache_count": self.current_cache_count,
        }


def _candidate(candidates: Dict[str, Candidate], song_hash: str) -> Candidate:
    if song_hash not in candidates:
        candidates[song_hash] = Candidate(song_hash=song_hash)
    return candidates[song_hash]


def _flatten_ratings(value: Any) -> List[float]:
    out: List[float] = []
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for child in value.values():
            out.extend(_flatten_ratings(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_flatten_ratings(child))
    return [v for v in out if 1 <= v <= 5]


def _add_recent(candidates: Dict[str, Candidate], data_dir: Path) -> None:
    recent = load_json(data_dir / "recent.json", {}).get("recent", [])
    for idx, item in enumerate(recent if isinstance(recent, list) else []):
        path = item.get("path") if isinstance(item, dict) else None
        song_hash = item.get("hash") if isinstance(item, dict) else None
        if not song_hash and path:
            song_hash = stable_song_hash(path)
        if not song_hash:
            continue
        cand = _candidate(candidates, song_hash)
        cand.path = cand.path or str(path or "")
        cand.add("recent", max(1.0, 100.0 - idx))


def _add_favorites(candidates: Dict[str, Candidate], data_dir: Path) -> None:
    favorites = load_json(data_dir / "favorites.json", {}).get("favorites", [])
    for item in favorites if isinstance(favorites, list) else []:
        path = item.get("path") if isinstance(item, dict) else None
        song_hash = item.get("hash") if isinstance(item, dict) else None
        if not song_hash and path:
            song_hash = stable_song_hash(path)
        if not song_hash:
            continue
        cand = _candidate(candidates, song_hash)
        cand.path = cand.path or str(path or "")
        cand.add("favorite", 160.0)


def _add_json_ratings(candidates: Dict[str, Candidate], data_dir: Path) -> None:
    ratings = load_json(data_dir / "ratings.json", {})
    if not isinstance(ratings, dict):
        return
    for song_hash, value in ratings.items():
        vals = _flatten_ratings(value)
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        cand = _candidate(candidates, song_hash)
        cand.add("high_rating_json", avg * 45.0 + len(vals) * 12.0)


def _add_feedback_db(candidates: Dict[str, Candidate], data_dir: Path) -> None:
    db = data_dir / "feedback.db"
    if not db.is_file() or db.stat().st_size == 0:
        return
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            "select song_hash, avg(rating), count(*), max(song_title) "
            "from ratings where song_hash != '' group by song_hash"
        ).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            con.close()
        except Exception:
            pass
    for song_hash, avg, count, title in rows:
        cand = _candidate(candidates, song_hash)
        cand.title = cand.title or (title or "")
        cand.add("high_rating_db", float(avg or 0) * 45.0 + float(count or 0) * 12.0)


def _add_analytics(candidates: Dict[str, Candidate], data_dir: Path) -> None:
    db = data_dir / "analytics.db"
    if not db.is_file() or db.stat().st_size == 0:
        return
    counts: Dict[str, int] = {}
    try:
        con = sqlite3.connect(db)
        for (payload,) in con.execute("select payload from events"):
            try:
                data = json.loads(payload or "{}")
            except Exception:
                continue
            song_hash = data.get("song_hash") or data.get("hash")
            if song_hash:
                counts[str(song_hash)] = counts.get(str(song_hash), 0) + 1
    except Exception:
        counts = {}
    finally:
        try:
            con.close()
        except Exception:
            pass
    for song_hash, count in counts.items():
        _candidate(candidates, song_hash).add("analytics", min(120.0, count * 8.0))


def _add_chord_index_fallback(
    candidates: Dict[str, Candidate],
    data_dir: Path,
    fallback_scan: int,
) -> None:
    index = load_json(data_dir / "chord_index.json", {})
    if isinstance(index, dict) and index:
        items = sorted(
            index.items(),
            key=lambda kv: (
                float((kv[1] or {}).get("mtime") or 0),
                int((kv[1] or {}).get("chord_count") or 0),
            ),
            reverse=True,
        )
        for song_hash, meta in items[:fallback_scan]:
            cand = _candidate(candidates, song_hash)
            chord_count = int((meta or {}).get("chord_count") or 0)
            cand.add("chord_index_fallback", 1.0 + min(20.0, chord_count / 20.0))
        return

    for path in list(iter_chord_paths(data_dir))[:fallback_scan]:
        _candidate(candidates, path.stem).add("chord_scan_fallback", 1.0)


def current_cache_counts(data_dir: Path, engine_version: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    acc_dir = data_dir / "accompaniments"
    if not acc_dir.is_dir():
        return counts
    for path in acc_dir.glob(f"*_{engine_version}.json"):
        song_hash = path.name.split("_", 1)[0]
        if len(song_hash) == 12:
            counts[song_hash] = counts.get(song_hash, 0) + 1
    return counts


def enrich_candidate(
    candidate: Candidate,
    data_dir: Path,
    engine_version: str,
    cache_counts: Optional[Dict[str, int]] = None,
) -> Candidate:
    chord_path = chord_path_for_hash(data_dir, candidate.song_hash)
    candidate.has_chords = chord_path.is_file()
    if chord_path.is_file():
        data = load_json(chord_path, {})
        candidate.path = candidate.path or str(data.get("path") or "")
        candidate.title = candidate.title or str(data.get("title") or "")
        candidate.bpm = _safe_float(data.get("bpm"))
        candidate.time_signature = str(data.get("time_signature") or data.get("meter") or "unknown")
        candidate.chord_count = len(data.get("chords") or [])
    candidate.has_melody = (data_dir / "melodies" / f"{candidate.song_hash}.json").is_file()
    if cache_counts is None:
        candidate.current_cache_count = len(list((data_dir / "accompaniments").glob(f"{candidate.song_hash}_*_{engine_version}.json")))
    else:
        candidate.current_cache_count = cache_counts.get(candidate.song_hash, 0)
    return candidate


def collect_candidates(
    data_dir: Path,
    *,
    limit: int,
    engine_version: str,
    fallback_scan: int = 500,
) -> List[Candidate]:
    candidates: Dict[str, Candidate] = {}
    _add_recent(candidates, data_dir)
    _add_favorites(candidates, data_dir)
    _add_json_ratings(candidates, data_dir)
    _add_feedback_db(candidates, data_dir)
    _add_analytics(candidates, data_dir)
    _add_chord_index_fallback(candidates, data_dir, fallback_scan)

    cache_counts = current_cache_counts(data_dir, engine_version)
    enriched = [
        enrich_candidate(candidate, data_dir, engine_version, cache_counts)
        for candidate in candidates.values()
    ]
    eligible = [c for c in enriched if c.has_chords and c.chord_count > 0]
    eligible.sort(key=lambda c: (c.score, c.chord_count, c.song_hash), reverse=True)
    return eligible[: max(1, limit)]


def choose_representative(candidates: List[Candidate], limit: int = 10) -> List[Dict[str, Any]]:
    selected: List[Tuple[str, Candidate]] = []
    used: set[str] = set()

    slots = [
        ("high_rated", lambda c: any(k.startswith("high_rating") for k in c.sources)),
        ("favorite", lambda c: "favorite" in c.sources),
        ("recent", lambda c: "recent" in c.sources),
        ("meter_3_4", lambda c: c.time_signature == "3/4"),
        ("meter_6_8", lambda c: c.time_signature == "6/8"),
        ("meter_12_8", lambda c: c.time_signature == "12/8"),
        ("slow_or_ballad", lambda c: c.bpm is not None and c.bpm < 85),
        ("midtempo", lambda c: c.bpm is not None and 85 <= c.bpm < 125),
        ("fast_or_funk_candidate", lambda c: c.bpm is not None and c.bpm >= 125),
        ("with_melody", lambda c: c.has_melody),
        ("unknown_meter_fallback", lambda c: c.time_signature == "unknown"),
    ]

    for label, predicate in slots:
        if len(selected) >= limit:
            break
        for candidate in candidates:
            if candidate.song_hash not in used and predicate(candidate):
                selected.append((label, candidate))
                used.add(candidate.song_hash)
                break

    for candidate in candidates:
        if len(selected) >= limit:
            break
        if candidate.song_hash not in used:
            selected.append(("score_fallback", candidate))
            used.add(candidate.song_hash)

    return [
        {"slot": label, **candidate.to_report()}
        for label, candidate in selected[:limit]
    ]


def _safe_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def cache_name(song_hash: str, style: str, level: str, section: str,
               instrument: str, engine_version: str) -> str:
    return f"{song_hash}_{style}_{level}_{section}_{instrument}_{engine_version}.json"


def _load_melody(data_dir: Path, song_hash: str) -> List[Dict[str, Any]]:
    payload = load_json(data_dir / "melodies" / f"{song_hash}.json", [])
    if isinstance(payload, dict):
        data = payload.get("melody") or []
    elif isinstance(payload, list):
        data = payload
    else:
        data = []
    return data if isinstance(data, list) else []


def _with_backend_imports():
    from ai.accompaniment_generator import ACC_ENGINE_VERSION, generate_accompaniment
    from ai.dynamics_engine import generate_dynamics
    from ai.pedal_advisor import generate_pedal_suggestions
    from ai_api import _dedupe_hand_collisions

    return ACC_ENGINE_VERSION, generate_accompaniment, generate_dynamics, generate_pedal_suggestions, _dedupe_hand_collisions


def generate_cache(
    data_dir: Path,
    candidate: Candidate,
    *,
    style: str,
    level: str,
    section: str,
    instrument: str,
    engine_version: str,
    add_pedal: bool = True,
    add_dynamics: bool = True,
) -> Dict[str, Any]:
    chord_data = load_json(chord_path_for_hash(data_dir, candidate.song_hash), {})
    chords = chord_data.get("chords") or []
    if not chords:
        raise ValueError("no chords")
    melody = _load_melody(data_dir, candidate.song_hash)
    bpm = float(chord_data.get("bpm") or 120.0)
    genre = chord_data.get("genre") or ""
    tempo_curve = chord_data.get("tempo_curve") or None
    time_signature = chord_data.get("time_signature") or chord_data.get("meter") or "4/4"
    beat_version = chord_data.get("beat_version", 0)

    _, generate_accompaniment, generate_dynamics, generate_pedal_suggestions, dedupe = _with_backend_imports()
    result = generate_accompaniment(
        chords=chords,
        melody=melody,
        bpm=bpm,
        style=style,
        level=level,
        genre=genre,
        section_type=section,
        tempo_curve=tempo_curve,
        instrument=instrument,
        time_signature=time_signature,
    )
    result["path"] = chord_data.get("path") or candidate.path
    result["bpm"] = round(bpm, 1)
    result["genre"] = genre
    result["source_beat_version"] = beat_version

    if add_pedal:
        try:
            result["pedal"] = generate_pedal_suggestions(
                chords,
                melody=melody,
                bpm=bpm,
                style="rhythmic" if section == "chorus" else "legato",
            )
        except Exception:
            result["pedal"] = []

    if add_dynamics:
        try:
            generate_dynamics(result.get("left_hand", []), bpm=int(bpm), section_type=section)
            generate_dynamics(result.get("right_hand", []), bpm=int(bpm), section_type=section)
        except Exception:
            pass

    result["left_hand"] = dedupe(result.get("left_hand", []), result.get("right_hand", []), melody)

    out_path = data_dir / "accompaniments" / cache_name(
        candidate.song_hash, style, level, section, instrument, engine_version
    )
    atomic_write_json(out_path, result)
    return result


def audit_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(events or [])
    durations = []
    schema2 = 0
    gate_count = 0
    missing_duration = 0
    very_short = 0
    for event in events or []:
        if event.get("schema_version") == 2:
            schema2 += 1
        if "gate_ratio" in event:
            gate_count += 1
        dur = _safe_float(event.get("duration"))
        if dur is None:
            missing_duration += 1
            continue
        durations.append(dur)
        if dur <= 0.125:
            very_short += 1

    lane_gaps: Dict[str, int] = {}
    for lane, lane_events in _events_by_lane(events).items():
        small_gaps = 0
        ordered = sorted(lane_events, key=lambda e: float(e.get("time", e.get("start", 0)) or 0))
        for prev, nxt in zip(ordered, ordered[1:]):
            prev_start = float(prev.get("time", prev.get("start", 0)) or 0)
            prev_dur = float(prev.get("duration", 0) or 0)
            next_start = float(nxt.get("time", nxt.get("start", 0)) or 0)
            gap = next_start - (prev_start + prev_dur)
            if 0 < gap <= 0.25:
                small_gaps += 1
        if small_gaps:
            lane_gaps[lane] = small_gaps

    return {
        "event_count": total,
        "schema2_ratio": round(schema2 / total, 4) if total else 1.0,
        "gate_ratio_count": gate_count,
        "missing_duration": missing_duration,
        "very_short_duration_count": very_short,
        "very_short_duration_ratio": round(very_short / total, 4) if total else 0.0,
        "avg_duration": round(sum(durations) / len(durations), 4) if durations else 0.0,
        "small_gap_by_lane": lane_gaps,
    }


def _events_by_lane(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    lanes: Dict[str, List[Dict[str, Any]]] = {}
    for event in events or []:
        lane = str(event.get("voice_lane") or f"{event.get('hand', '')}:{event.get('role', '')}" or "unknown")
        lanes.setdefault(lane, []).append(event)
    return lanes


def audit_cache_file(path: Path) -> Dict[str, Any]:
    payload = load_json(path, {})
    left = payload.get("left_hand") if isinstance(payload, dict) else []
    right = payload.get("right_hand") if isinstance(payload, dict) else []
    observation = payload.get("continuity_observation") if isinstance(payload, dict) else None
    return {
        "file": path.name,
        "schema_version": payload.get("schema_version") if isinstance(payload, dict) else None,
        "continuity_mode": (observation or {}).get("mode") if isinstance(observation, dict) else None,
        "continuity_candidates": ((observation or {}).get("total") or {}).get("candidate_events")
        if isinstance(observation, dict) else None,
        "left": audit_events(left or []),
        "right": audit_events(right or []),
    }


def prewarm_plan(
    data_dir: Path,
    candidates: List[Candidate],
    *,
    styles: List[str],
    levels: List[str],
    instruments: List[str],
    section: str,
    engine_version: str,
    execute: bool,
    add_pedal: bool = True,
    add_dynamics: bool = True,
) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    summary = {"existing": 0, "planned": 0, "generated": 0, "errors": 0}
    for candidate in candidates:
        for style in styles:
            for level in levels:
                for instrument in instruments:
                    name = cache_name(candidate.song_hash, style, level, section, instrument, engine_version)
                    path = data_dir / "accompaniments" / name
                    record = {
                        "hash": candidate.song_hash,
                        "style": style,
                        "level": level,
                        "instrument": instrument,
                        "file": name,
                        "status": "existing" if path.is_file() else "planned",
                    }
                    if path.is_file():
                        summary["existing"] += 1
                    elif execute:
                        try:
                            started = time.time()
                            generate_cache(
                                data_dir,
                                candidate,
                                style=style,
                                level=level,
                                section=section,
                                instrument=instrument,
                                engine_version=engine_version,
                                add_pedal=add_pedal,
                                add_dynamics=add_dynamics,
                            )
                            record["status"] = "generated"
                            record["seconds"] = round(time.time() - started, 3)
                            record["audit"] = audit_cache_file(path)
                            summary["generated"] += 1
                        except Exception as exc:
                            record["status"] = "error"
                            record["error"] = f"{type(exc).__name__}: {exc}"
                            summary["errors"] += 1
                    else:
                        summary["planned"] += 1
                    records.append(record)
    return {"summary": summary, "records": records}


def audit_existing_caches(data_dir: Path, candidates: List[Candidate], engine_version: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    acc_dir = data_dir / "accompaniments"
    for candidate in candidates:
        files = sorted(acc_dir.glob(f"{candidate.song_hash}_*_{engine_version}.json"))
        for path in files:
            out.append({"hash": candidate.song_hash, **audit_cache_file(path)})
    return out


def build_markdown_report(report: Dict[str, Any]) -> str:
    lines = [
        "# Phase 5 Continuity Rollout Report",
        "",
        f"- Mode: {'execute' if report.get('execute') else 'dry-run'}",
        f"- Engine: `{report.get('engine_version')}`",
        f"- Generated at: {report.get('generated_at')}",
        f"- Selected candidates: {len(report.get('candidates', []))}",
        "",
        "## Prewarm Summary",
        "",
    ]
    summary = report.get("prewarm", {}).get("summary", {})
    lines.extend([
        f"- Existing: {summary.get('existing', 0)}",
        f"- Planned: {summary.get('planned', 0)}",
        f"- Generated: {summary.get('generated', 0)}",
        f"- Errors: {summary.get('errors', 0)}",
        "",
        "## Representative Set",
        "",
        "| Slot | Hash | BPM | Meter | Chords | Sources |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ])
    for item in report.get("representative", []):
        sources = ", ".join(item.get("sources", {}).keys())
        lines.append(
            f"| {item.get('slot')} | `{item.get('hash')}` | {item.get('bpm') or ''} | "
            f"{item.get('time_signature') or ''} | {item.get('chord_count') or 0} | {sources} |"
        )
    lines.extend(["", "## Cache Audit", ""])
    audits = report.get("cache_audit", [])
    if not audits:
        lines.append("No current-engine caches found for the selected candidates.")
    else:
        lines.extend([
            "| Hash | File | Mode | Candidates | LH schema2 | RH schema2 | LH short | RH short |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ])
        for item in audits:
            left = item.get("left", {})
            right = item.get("right", {})
            lines.append(
                f"| `{item.get('hash')}` | `{item.get('file')}` | {item.get('continuity_mode') or ''} | "
                f"{item.get('continuity_candidates') if item.get('continuity_candidates') is not None else ''} | "
                f"{left.get('schema2_ratio')} | {right.get('schema2_ratio')} | "
                f"{left.get('very_short_duration_ratio')} | {right.get('very_short_duration_ratio')} |"
            )
    lines.append("")
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> Tuple[Dict[str, Any], Path, Path]:
    data_dir = Path(args.data_dir).resolve()
    engine_version = args.engine_version or _current_engine_version()
    styles = _split_csv(args.styles, DEFAULT_STYLES)
    levels = _split_csv(args.levels, DEFAULT_LEVELS)
    instruments = _split_csv(args.instruments, DEFAULT_INSTRUMENTS)

    candidates = collect_candidates(
        data_dir,
        limit=args.limit,
        engine_version=engine_version,
        fallback_scan=args.fallback_scan,
    )
    representative = choose_representative(candidates, args.representative_limit)
    prewarm_candidates = candidates[: args.limit]
    prewarm = prewarm_plan(
        data_dir,
        prewarm_candidates,
        styles=styles,
        levels=levels,
        instruments=instruments,
        section=args.section,
        engine_version=engine_version,
        execute=args.execute,
        add_pedal=not args.no_pedal,
        add_dynamics=not args.no_dynamics,
    )
    audit = audit_existing_caches(data_dir, prewarm_candidates, engine_version)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "execute": bool(args.execute),
        "data_dir": str(data_dir),
        "engine_version": engine_version,
        "styles": styles,
        "levels": levels,
        "instruments": instruments,
        "section": args.section,
        "candidates": [candidate.to_report() for candidate in candidates],
        "representative": representative,
        "prewarm": prewarm,
        "cache_audit": audit,
    }

    report_dir = Path(args.report_dir).resolve()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = args.report_name or f"continuity_phase5_rollout_{stamp}"
    json_path = report_dir / f"{stem}.json"
    md_path = report_dir / f"{stem}.md"
    return report, json_path, md_path


def _current_engine_version() -> str:
    try:
        from ai.accompaniment_generator import ACC_ENGINE_VERSION

        return str(ACC_ENGINE_VERSION)
    except Exception:
        return "v10"


def _split_csv(value: str, default: List[str]) -> List[str]:
    values = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return values or list(default)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 5 continuity rollout helper")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument("--report-dir", default=str(REPO_ROOT / "data" / "logs"))
    parser.add_argument("--report-name", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--representative-limit", type=int, default=10)
    parser.add_argument("--fallback-scan", type=int, default=500)
    parser.add_argument("--styles", default=",".join(DEFAULT_STYLES))
    parser.add_argument("--levels", default=",".join(DEFAULT_LEVELS))
    parser.add_argument("--instruments", default=",".join(DEFAULT_INSTRUMENTS))
    parser.add_argument("--section", default="default")
    parser.add_argument("--engine-version", default="")
    parser.add_argument("--execute", action="store_true", help="Generate missing caches")
    parser.add_argument("--no-pedal", action="store_true")
    parser.add_argument("--no-dynamics", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    report, json_path, md_path = build_report(args)
    atomic_write_json(json_path, report)
    atomic_write_text(md_path, build_markdown_report(report))
    summary = report["prewarm"]["summary"]
    print(f"Phase 5 report: {json_path}")
    print(f"Markdown report: {md_path}")
    print(
        "Prewarm summary: "
        f"existing={summary['existing']} planned={summary['planned']} "
        f"generated={summary['generated']} errors={summary['errors']}"
    )
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
