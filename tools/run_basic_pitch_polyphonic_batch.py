"""Generate polyphonic note JSON for RH melody solo-piano A/B rows.

This fills the Stage-1 input expected by ``solo_piano_polyphonic``. It reads the
Phase 0.5 smoke queue, resolves audio paths, runs Basic Pitch in raw polyphonic
mode, and writes ``polyphonic_json`` sidecars already referenced by the queue.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


DEFAULT_QUEUE = Path(r"V:\data\melody_reviews\phase0_5_ab_smoke_queue.jsonl")


def read_solo_piano_rows(queue_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in queue_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        data = json.loads(line)
        if isinstance(data, dict) and data.get("group") == "solo_piano":
            rows.append(data)
    rows.sort(key=lambda row: int(row.get("sample_order") or 0))
    return rows


class BasicPitchPolyphonicTranscriber:
    def __init__(self) -> None:
        self._predict = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._predict is not None:
            return
        from basic_pitch import FilenameSuffix, build_icassp_2022_model_path
        from basic_pitch.inference import Model, predict

        self._predict = predict
        self._model = Model(str(build_icassp_2022_model_path(FilenameSuffix.onnx)))

    def transcribe(self, audio_path: str) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        _model_output, _midi_data, note_events = self._predict(
            audio_path,
            self._model,
            onset_threshold=0.5,
            frame_threshold=0.3,
            minimum_note_length=58,
            minimum_frequency=None,
            maximum_frequency=None,
            melodia_trick=True,
        )
        return basic_pitch_events_to_notes(note_events)


def basic_pitch_events_to_notes(note_events: Iterable[Any]) -> List[Dict[str, Any]]:
    notes: List[Dict[str, Any]] = []
    for event in note_events:
        try:
            start, end, pitch_midi, amplitude, _bends = event
            start_f = float(start)
            end_f = float(end)
            midi = int(round(float(pitch_midi)))
            amp = max(0.0, min(1.0, float(amplitude)))
        except Exception:
            continue
        notes.append({
            "start": round(start_f, 4),
            "end": round(end_f, 4),
            "midi": midi,
            "velocity": max(1, min(127, int(round(amp * 127)))),
            "confidence": round(amp, 4),
        })
    notes.sort(key=lambda item: (item["start"], -item["midi"], item["end"]))
    return notes


def write_polyphonic_json(
    output_path: Path,
    *,
    row: Dict[str, Any],
    audio_path: str,
    notes: List[Dict[str, Any]],
    force: bool = False,
) -> Path:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass --force to overwrite")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source": "basic_pitch_polyphonic",
        "song_hash": row.get("hash") or "",
        "path": row.get("path") or "",
        "audio_path": audio_path,
        "sample_order": row.get("sample_order"),
        "group": row.get("group") or "",
        "notes": notes,
    }
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output_path)
    return output_path


def run_batch(
    rows: List[Dict[str, Any]],
    *,
    resolve_path: Callable[[str], str],
    transcribe: Callable[[str], List[Dict[str, Any]]],
    force: bool = False,
    limit: int = 0,
) -> Dict[str, Any]:
    selected = rows[:limit] if limit and limit > 0 else rows
    summary: Dict[str, Any] = {"total": len(selected), "ok": 0, "failed": 0, "skipped": 0, "items": []}
    for row in selected:
        output_raw = str(row.get("polyphonic_json") or "").strip()
        out = Path(output_raw) if output_raw else None
        item = {
            "sample_order": row.get("sample_order"),
            "hash": row.get("hash") or "",
            "path": row.get("path") or "",
            "polyphonic_json": output_raw,
        }
        try:
            if not out:
                raise ValueError("polyphonic_json is required")
            if out.is_file() and not force:
                item.update({"ok": True, "status": "cached", "notes": _count_existing_notes(out)})
                summary["skipped"] += 1
                summary["items"].append(item)
                continue
            audio_path = resolve_path(str(row.get("path") or ""))
            if not audio_path or not Path(audio_path).is_file():
                raise FileNotFoundError(audio_path or row.get("path") or "")
            notes = transcribe(audio_path)
            write_polyphonic_json(out, row=row, audio_path=audio_path, notes=notes, force=True)
            item.update({"ok": True, "status": "generated", "notes": len(notes)})
            summary["ok"] += 1
        except Exception as exc:
            item.update({"ok": False, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
            summary["failed"] += 1
        summary["items"].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    return summary


def _count_existing_notes(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    notes = data.get("notes") if isinstance(data, dict) else data
    return len(notes) if isinstance(notes, list) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Basic Pitch polyphonic JSON for solo-piano smoke rows.")
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE), help="Smoke queue JSONL.")
    parser.add_argument("--limit", type=int, default=0, help="Process only first N solo-piano rows. 0 means all.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing polyphonic JSON outputs.")
    args = parser.parse_args()

    from config import resolve_path

    rows = read_solo_piano_rows(Path(args.queue))
    transcriber = BasicPitchPolyphonicTranscriber()
    summary = run_batch(
        rows,
        resolve_path=resolve_path,
        transcribe=transcriber.transcribe,
        force=args.force,
        limit=args.limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary.get("failed") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
