"""Interactive learning (互動學習) progress store.

Backs the /learn page ear-training quizzes. Personal use: one JSON file
(data/learn_progress.json, tier1 backup) with per-module / per-level totals plus
a rolling window of recent attempts so the page can show streaks and weak spots.
"""
import json
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()

DATA_DIR = Path(__file__).parent.parent / "data"
PROGRESS_FILE = DATA_DIR / "learn_progress.json"
_lock = threading.Lock()
_RECENT_MAX = 500


class LearnResult(BaseModel):
    module: str = Field(..., min_length=1, max_length=32)
    level: int = Field(..., ge=1, le=3)
    expected: str = Field(..., min_length=1, max_length=64)
    answer: str = Field(..., min_length=1, max_length=64)
    correct: bool


def _read() -> dict:
    try:
        data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("totals", {})
            data.setdefault("recent", [])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "totals": {}, "recent": []}


def _write(data: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, PROGRESS_FILE)


@router.get("/api/learn/stats")
def learn_stats():
    data = _read()
    return {"totals": data["totals"], "recent": data["recent"][-50:]}


@router.post("/api/learn/result")
def learn_result(body: LearnResult):
    with _lock:
        data = _read()
        bucket = data["totals"].setdefault(body.module, {}).setdefault(str(body.level), {"correct": 0, "total": 0})
        bucket["total"] += 1
        if body.correct:
            bucket["correct"] += 1
        # Per-answer confusion counts so the UI can point at weak spots.
        conf = data["totals"][body.module].setdefault("by_item", {}).setdefault(body.expected, {"correct": 0, "total": 0})
        conf["total"] += 1
        if body.correct:
            conf["correct"] += 1
        data["recent"].append({
            "t": int(time.time()), "module": body.module, "level": body.level,
            "expected": body.expected, "answer": body.answer, "correct": body.correct,
        })
        data["recent"] = data["recent"][-_RECENT_MAX:]
        _write(data)
        return {"ok": True, "level": bucket}
