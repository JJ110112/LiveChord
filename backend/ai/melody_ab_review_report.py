"""Markdown review report helpers for RH melody A/B smoke results."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import quote


DEFAULT_BASE_URL = "http://192.168.50.6:8800"
DEFAULT_REQUIRED_CANDIDATES = ("full_mix_pyin", "vocal_stem_crepe")


def read_smoke_result_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, dict):
            rows.append(data)
    return rows


def build_review_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    group: str = "vocal",
    required_candidates: Sequence[str] = DEFAULT_REQUIRED_CANDIDATES,
    base_url: str = DEFAULT_BASE_URL,
) -> List[Dict[str, Any]]:
    review_rows: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("group") or "") != group:
            continue
        result = row.get("result") or {}
        candidate_results = {
            item.get("candidate_id"): item
            for item in result.get("results") or []
            if isinstance(item, dict)
        }
        if not all(candidate_results.get(candidate_id, {}).get("ok") for candidate_id in required_candidates):
            continue
        requested = row.get("requested") or {}
        song_hash = str(result.get("song_hash") or requested.get("hash") or "")
        path = str(result.get("path") or requested.get("path") or "")
        title = str(requested.get("title") or _title_from_path(path))
        artist = str(requested.get("artist") or "")
        admin_url = f"{base_url.rstrip('/')}/admin?melody={quote(song_hash)}&melodyCandidates=1"
        player_url = f"{base_url.rstrip('/')}/player?path={quote(path, safe='')}&autoplay=1"
        api_url = f"{base_url.rstrip('/')}/api/ai/melody/debug/candidates?hash={quote(song_hash)}"
        review_rows.append({
            "sample_order": row.get("sample_order"),
            "hash": song_hash,
            "path": path,
            "title": title,
            "artist": artist,
            "note": row.get("note") or requested.get("note") or "",
            "admin_url": admin_url,
            "player_url": player_url,
            "api_url": api_url,
            "candidate_status": {
                candidate_id: str(candidate_results.get(candidate_id, {}).get("status") or "")
                for candidate_id in required_candidates
            },
        })
    review_rows.sort(key=lambda item: int(item.get("sample_order") or 0))
    return review_rows


def render_review_markdown(
    review_rows: Sequence[Dict[str, Any]],
    *,
    title: str = "RH Melody Phase 0.5 Vocal A/B Review",
) -> str:
    lines = [
        f"# {title}",
        "",
        "Rubric:",
        "",
        "- Octave displacement: is the candidate in the right octave more often than full-mix pYIN?",
        "- Sustained-note continuity: are long notes less fragmented across chord changes?",
        "- Phrase boundaries: are endings cleaner with less instrumental bleed?",
        "",
        "Decision rule:",
        "",
        "- 8+/12 clearly better: scale up vocal route.",
        "- 4-6/12 clearly better: add song-type/confidence gate before scaling.",
        "- <4/12 clearly better: do not scale vocal CREPE; focus Phase 0.5 on solo piano.",
        "",
        f"Review rows: {len(review_rows)}",
        "",
        "| # | Song | Hash | Admin | Player | Notes | Octave | Sustain | Boundary | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in review_rows:
        song = _md_cell(" - ".join(part for part in [str(row.get("artist") or ""), str(row.get("title") or "")] if part))
        note = _md_cell(str(row.get("note") or ""))
        lines.append(
            "| {order} | {song} | `{hash}` | [Candidates]({admin}) | [Player]({player}) | {note} |  |  |  |  |".format(
                order=row.get("sample_order") or "",
                song=song,
                hash=row.get("hash") or "",
                admin=row.get("admin_url") or "",
                player=row.get("player_url") or "",
                note=note,
            )
        )
    return "\n".join(lines) + "\n"


def write_review_markdown(markdown: str, output: Path, *, force: bool = False) -> Path:
    if output.exists() and not force:
        raise FileExistsError(f"{output} already exists; pass --force-output to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    os.replace(tmp, output)
    return output


def _title_from_path(path: str) -> str:
    name = Path(path).name
    return name.rsplit(".", 1)[0] if "." in name else name


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
