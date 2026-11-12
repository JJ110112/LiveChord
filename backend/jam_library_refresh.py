"""Auto-refresh data/jam_library_styles.json after the library changes.

Triggers (call ``request_rebuild(reason)``):
  * a library scan that rewrote library_cache.json (music_api._scan_worker)
  * a chord batch / auto-worker cycle that analyzed new songs — the 器樂 and
    Christmas lists only include analyzed songs, so new chord files matter
    even when the scan itself saw nothing new

Requests are coalesced: the build runs QUIET_S after the last request (so a
20-song auto cycle or a big scan is one rebuild) and at most once per
MIN_GAP_S. The builder runs as a separate low-priority process — its ~10s
regex pass would otherwise hold the server GIL (CLAUDE.md: pure-Python loops
on daemon threads still starve request threads). It writes atomically and
jam_tracks_api picks the new file up by mtime, so there is no restart.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
BUILDER = BACKEND_DIR / "build_jam_library_styles.py"

QUIET_S = 60.0
MIN_GAP_S = 30 * 60.0
TIMEOUT_S = 900
_POLL_S = 15.0

_lock = threading.Lock()
_state = {
    "requested_at": 0.0,   # 0 = nothing pending
    "reason": "",
    "last_end": 0.0,
    "running": False,
    "thread": None,
    "last_result": None,
}


def _run_builder() -> dict:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(BUILDER)], cwd=str(BACKEND_DIR), env=env,
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT_S, creationflags=flags,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "seconds": round(time.time() - t0, 1),
        "tail": ((proc.stdout or "") + (proc.stderr or "")).strip()[-600:],
    }


def _loop():
    while True:
        with _lock:
            req = _state["requested_at"]
            if not req:
                _state["thread"] = None
                return
            due = max(req + QUIET_S, _state["last_end"] + MIN_GAP_S)
            now = time.time()
            if now >= due:
                reason = _state["reason"]
                _state["requested_at"] = 0.0
                _state["running"] = True
        if now < due:
            time.sleep(min(due - now, _POLL_S))
            continue
        try:
            result = _run_builder()
        except Exception as e:  # timeout / spawn failure — never kill the server
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        result["reason"] = reason
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with _lock:
            _state["running"] = False
            _state["last_end"] = time.time()
            _state["last_result"] = result
        if result.get("ok"):
            logger.info("jam library styles rebuilt (%s) in %ss", reason, result.get("seconds"))
        else:
            logger.warning("jam library styles rebuild failed (%s): %s", reason,
                           result.get("error") or result.get("tail"))


def request_rebuild(reason: str) -> None:
    """Ask for a coalesced background rebuild. Cheap; safe from any thread."""
    with _lock:
        _state["requested_at"] = time.time()
        _state["reason"] = reason
        t = _state["thread"]
        if t is None or not t.is_alive():
            t = threading.Thread(target=_loop, daemon=True, name="jam-library-refresh")
            _state["thread"] = t
            t.start()


def status() -> dict:
    with _lock:
        return {
            "pending": bool(_state["requested_at"]),
            "running": _state["running"],
            "reason": _state["reason"],
            "last_result": _state["last_result"],
        }
