"""Shadow Mode V2 melody extraction.

V1 pYIN stays primary (serves the user). After V1 finishes, this module
fires V2 (basic-pitch via venv_ai, Python 3.11) as a background subprocess
on a copy of the audio file. Result lands in `data/melodies_v2/` and a
JSONL timing log in `data/shadow_v2.log`.

Fail-silently by contract: any exception is swallowed and logged, never
propagates back to process_queue / the user. V1 behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _REPO_ROOT / "data"
_MELODIES_V2_DIR = _DATA_DIR / "melodies_v2"
_SHADOW_TMP_DIR = _DATA_DIR / "tmp" / "shadow"
_SHADOW_LOG = _DATA_DIR / "shadow_v2.log"
_VENV_PY = _REPO_ROOT / "venv_ai" / "Scripts" / "python.exe"

_SUBPROCESS_TIMEOUT_S = 180  # shadow is background — generous timeout

# One-shot warning latch so we don't flood logs if venv_ai is missing
_venv_missing_warned = False


def shadow_available() -> bool:
    """True if venv_ai is set up and V2 can be invoked."""
    return _VENV_PY.is_file()


def _shadow_enabled() -> bool:
    """Controlled by config.SHADOW_V2_ENABLED + venv presence."""
    try:
        # process_queue imports work off sys.path including backend/ — match that
        import config as _cfg  # type: ignore
    except ImportError:
        try:
            from backend import config as _cfg  # type: ignore
        except ImportError:
            return False
    try:
        if not getattr(_cfg, "SHADOW_V2_ENABLED", False):
            return False
    except Exception:
        return False
    return shadow_available()


def run_shadow_async(
    audio_path: str,
    song_hash: str,
    v1_time_s: Optional[float] = None,
    v1_notes: Optional[int] = None,
) -> None:
    """Fire V2 in background. Returns immediately. Never raises."""
    global _venv_missing_warned
    try:
        if not _shadow_enabled():
            if not shadow_available() and not _venv_missing_warned:
                logger.info("Shadow V2 skipped: venv_ai missing (%s)", _VENV_PY)
                _venv_missing_warned = True
            return
        if not audio_path or not os.path.isfile(audio_path):
            return

        _SHADOW_TMP_DIR.mkdir(parents=True, exist_ok=True)
        _MELODIES_V2_DIR.mkdir(parents=True, exist_ok=True)

        # Copy audio so main melody worker can delete the original freely.
        shadow_audio = _SHADOW_TMP_DIR / f"{song_hash}_{int(time.time()*1000)}{Path(audio_path).suffix}"
        shutil.copy2(audio_path, shadow_audio)

        t = threading.Thread(
            target=_run_shadow_work,
            args=(str(shadow_audio), song_hash, v1_time_s, v1_notes),
            daemon=True,
            name=f"shadow-v2-{song_hash[:8]}",
        )
        t.start()
    except Exception as e:
        logger.warning("Shadow V2 launch failed for %s: %s", song_hash, e)


def _run_shadow_work(
    shadow_audio: str,
    song_hash: str,
    v1_time_s: Optional[float],
    v1_notes: Optional[int],
) -> None:
    out_json = _MELODIES_V2_DIR / f"{song_hash}.json"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "hash": song_hash,
        "v1_time_s": v1_time_s,
        "v1_notes": v1_notes,
        "status": "unknown",
    }
    t0 = time.time()
    try:
        result = subprocess.run(
            [
                str(_VENV_PY),
                "-m",
                "backend.ai.melody_extractor_v2",
                shadow_audio,
                "--json-out",
                str(out_json),
                "--quiet",
            ],
            cwd=str(_REPO_ROOT),
            timeout=_SUBPROCESS_TIMEOUT_S,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        entry["v2_time_s"] = round(time.time() - t0, 2)
        entry["return_code"] = result.returncode
        if result.returncode == 0 and out_json.is_file():
            try:
                data = json.loads(out_json.read_text(encoding="utf-8"))
                notes = data if isinstance(data, list) else data.get("melody", [])
                entry["v2_notes"] = len(notes)
                entry["status"] = "ok"
            except Exception as e:
                entry["status"] = "ok_but_parse_failed"
                entry["error"] = f"{type(e).__name__}: {e}"
        else:
            entry["status"] = "error"
            stderr_tail = (result.stderr or "").strip().splitlines()[-3:]
            entry["error"] = " | ".join(stderr_tail) if stderr_tail else f"rc={result.returncode}"
    except subprocess.TimeoutExpired:
        entry["status"] = "timeout"
        entry["v2_time_s"] = round(time.time() - t0, 2)
    except Exception as e:
        entry["status"] = "exception"
        entry["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            os.remove(shadow_audio)
        except OSError:
            pass
        _append_log(entry)


def _append_log(entry: dict) -> None:
    try:
        _SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Shadow log append failed: %s", e)
