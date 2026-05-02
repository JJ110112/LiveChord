"""
Modal serverless GPU dispatch for BTC chord detection.

Two roles in one file:

1. **Modal app definition** — when `modal deploy backend/modal_btc.py` is run
   from the project root, Modal imports this module, registers the
   `livechord-btc` app, and provisions a T4 GPU container that can serve
   `detect_chords_and_key_modal` over the network. See `doc/MODAL_DEPLOY.md`.

2. **Local-side dispatcher** — when `chord_detect.detect_chords_and_key_isolated`
   sees `LIVECHORD_USE_MODAL_BTC=1`, it calls `detect_via_modal(audio_path)`
   from this module, which uploads the audio bytes to the deployed Modal
   function and returns the same `(chords, key)` tuple as the local path.

The `import modal` is wrapped in a try/except so the rest of LiveChord still
runs in personal/beta mode without `modal` installed (modal is in
requirements.txt but pinned to extras for VPS-only deployment).
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Modal app definition (only imported when Modal CLI deploys this file)
# ---------------------------------------------------------------------------

try:
    import modal  # type: ignore

    _MODAL_AVAILABLE = True
except ImportError:
    modal = None  # type: ignore
    _MODAL_AVAILABLE = False


_BACKEND_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent

# Image: Debian slim + ffmpeg + the exact Python deps `chord_detect` pulls in.
# Pinning torch CPU? No — we want CUDA on the T4. Modal's torch wheel auto-
# matches the GPU driver in the container.
if _MODAL_AVAILABLE:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("ffmpeg", "libsndfile1")
        .pip_install(
            "torch==2.4.0",
            "numpy<2.0",
            "librosa>=0.10.1",
            "soundfile>=0.12.1",
            "PyYAML>=6.0",
            "scipy>=1.10",
            "numba>=0.59",
        )
        # Bundle the entire backend/ tree (chord_detect.py + btc/ submodule
        # + utils/) so the deployed function can `from chord_detect import
        # detect_chords_and_key`. Excludes data/, tests, and pyc to keep the
        # image lean. The model weight ships separately via Modal Volume.
        .add_local_dir(
            str(_BACKEND_DIR),
            remote_path="/app/backend",
            ignore=["__pycache__", "*.pyc", "data", "tests"],
        )
    )

    # Persistent named volume — admin pre-loads the BTC checkpoint here once
    # via `modal volume put livechord-btc-model <local-path> /btc.pt`.
    volume = modal.Volume.from_name("livechord-btc-model", create_if_missing=True)

    app = modal.App("livechord-btc")

    @app.function(
        image=image,
        gpu="T4",
        volumes={"/btc_model": volume},
        # min_containers=0 → scales fully to zero, zero idle cost. The first
        # analysis of the day pays a ~15-20 s cold start; subsequent calls
        # within scaledown_window reuse the warm container. Right trade for
        # the LiveChord traffic profile (handful of analyses per hour) plus
        # the Starter $1/mo workspace cap. Bump to 1 once payment is added
        # and you want guaranteed warm response on every first uploader.
        min_containers=0,
        # Stay warm for 5 min after a call so consecutive uploads (a user
        # analyzing 3-4 songs in a session) all hit warm. Outside that
        # window scales to zero.
        scaledown_window=300,
        timeout=600,
    )
    def detect_chords_and_key_modal(
        audio_bytes: bytes,
        audio_filename: str = "input.mp3",
        min_dur: float = 0.5,
    ) -> dict:
        """
        Run BTC chord detection on a single audio blob.

        The container imports our existing `chord_detect.detect_chords_and_key`
        — no logic divergence between local and remote paths. Model weight
        comes from the mounted Modal Volume; we symlink it into BTC_DIR so
        the existing `_load_model()` code finds it without modification.
        """
        import sys
        import shutil
        import tempfile

        # Make `from chord_detect import …` work inside the container.
        sys.path.insert(0, "/app/backend")

        # Symlink the Volume-resident model weight into where chord_detect
        # expects it. The Volume layout is /btc_model/btc.pt → we want it at
        # /app/backend/btc/btc_model_large_voca.pt to match BTC_DIR.
        src_weight = "/btc_model/btc.pt"
        dst_weight = "/app/backend/btc/btc_model_large_voca.pt"
        if not os.path.exists(dst_weight):
            os.makedirs(os.path.dirname(dst_weight), exist_ok=True)
            if os.path.exists(src_weight):
                os.symlink(src_weight, dst_weight)
            else:
                raise RuntimeError(
                    "BTC model weight not found at /btc_model/btc.pt — "
                    "deploy with `modal volume put livechord-btc-model "
                    "<local-path>/btc_model_large_voca.pt /btc.pt` first."
                )

        suffix = os.path.splitext(audio_filename)[1] or ".mp3"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            from chord_detect import detect_chords_and_key  # type: ignore
            chords, key = detect_chords_and_key(tmp_path, min_dur=min_dur)
            return {"chords": chords, "key": key}
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Local-side dispatcher (called from chord_detect.detect_chords_and_key_isolated)
# ---------------------------------------------------------------------------


def detect_via_modal(audio_path: str, min_dur: float = 0.5) -> tuple:
    """
    Upload audio bytes to the deployed Modal function and return
    (chords, key). Raises if Modal isn't installed or the deployed app
    can't be reached — caller (chord_detect) catches and falls back.
    """
    if not _MODAL_AVAILABLE:
        raise RuntimeError(
            "modal package is not installed; pip install modal to enable "
            "LIVECHORD_USE_MODAL_BTC."
        )

    # Lazy lookup — avoids a network call at module import time.
    func = modal.Function.from_name("livechord-btc", "detect_chords_and_key_modal")

    audio_filename = os.path.basename(audio_path)
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    result = func.remote(
        audio_bytes=audio_bytes,
        audio_filename=audio_filename,
        min_dur=min_dur,
    )
    return result["chords"], result["key"]
