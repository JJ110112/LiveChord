"""
Modal serverless GPU dispatch for beat_this beat tracking.

Mirrors backend/modal_btc.py:

1. **Modal app definition** — `modal deploy backend/modal_beat_this.py`
   registers the `livechord-beat-this` app and provisions a T4 GPU container
   serving `detect_beats_modal` over the network.

2. **Local-side dispatcher** — `detect_beats_via_modal(audio_path)` is called
   from the upgrade queue when `LIVECHORD_USE_MODAL_BEAT_THIS=1`, uploads
   audio bytes to the deployed Modal function, returns a beat-info dict
   shaped like `analyze_and_snap_dynamic` output (bpm, beats, downbeats,
   tempo_curve, beats_source, bpm_correction).

Why this exists: the NUC has no CUDA + no beat_this installed, and the public
VPS won't either. Without Modal dispatch, `process_api.switch_beats` returns
503 for `mode=beat_this` and tells the user to run a PC bulk batch — which
public users obviously can't do. With Modal, switching is on-demand for any
host that sets the env flag.

The `import modal` is wrapped in try/except so personal-mode NUC instances
without `modal` installed keep working — the dispatcher just raises and the
caller falls back to the existing 503.
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

if _MODAL_AVAILABLE:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .apt_install("ffmpeg", "libsndfile1", "git")
        .pip_install(
            "torch==2.4.0",
            # Pin torchaudio to match — pip's resolver otherwise grabs the
            # latest (2.11.x, CUDA 13) which mismatches torch 2.4.0's CUDA 12
            # libs and torchaudio fails to load (`libcudart.so.13 not found`).
            "torchaudio==2.4.0",
            "numpy<2.0",
            "librosa>=0.10.1",
            "soundfile>=0.12.1",
            "scipy>=1.10",
            "numba>=0.59",
            # beat_this is a git-only install — its lab repo isn't on PyPI.
            # Pulls lightning + einops + rotary-embedding-torch transitively.
            "git+https://github.com/CPJKU/beat_this.git",
        )
        # Pre-warm: instantiate File2Beats once at image build time so the
        # checkpoint download happens during `modal deploy` (cached into the
        # image layer) rather than on the first user-facing cold start.
        # device="cpu" keeps build hosts GPU-free; the runtime will reload to
        # CUDA on first call regardless. `|| true` so a transient HF Hub blip
        # during build doesn't kill the whole deploy — runtime re-downloads
        # in that case (slower cold start, still works).
        # Single-line invocation — `run_commands` becomes a Dockerfile RUN and
        # embedded \n inside the bash string break the Dockerfile parser.
        .run_commands(
            "python -c \"from beat_this.inference import File2Beats; "
            "File2Beats(checkpoint_path='final0', device='cpu', float16=False); "
            "print('beat_this checkpoint primed')\" || echo 'WARN: prime skipped'"
        )
        # Bundle backend/ so the deployed function can import bpm_sanity
        # (ballad_halving_check + apply_halving_to_beat_info). Excludes data/,
        # tests, and pyc to keep the image lean.
        .add_local_dir(
            str(_BACKEND_DIR),
            remote_path="/app/backend",
            ignore=["__pycache__", "*.pyc", "data", "tests"],
        )
    )

    app = modal.App("livechord-beat-this")

    @app.function(
        image=image,
        gpu="T4",
        # min_containers=0 → scales fully to zero, zero idle cost. First call
        # of the day pays a ~10-15 s cold start (model load — checkpoint is
        # already in the image so no download). Subsequent calls within
        # scaledown_window reuse the warm container.
        min_containers=0,
        # Stay warm 5 min after each call so a user switching tracker on a few
        # consecutive songs all hit warm.
        scaledown_window=300,
        timeout=600,
    )
    def detect_beats_modal(
        audio_bytes: bytes,
        audio_filename: str = "input.wav",
        run_bpm_sanity: bool = True,
    ) -> dict:
        """Run beat_this on a single audio blob.

        Returns a dict shaped like the local
        `migrate_add_beats_via_beat_this._process_one` writes back into the
        chord JSON — the caller atomically applies these fields.

        Schema:
        {
          "bpm": float,
          "beats": [float, ...],
          "downbeats": [float, ...],
          "tempo_curve": [{"t": float, "bpm": float}, ...],
          "beats_source": "beat_this",
          "bpm_correction": {applied, reason, onset_density, rms_cov, original}
                            | None,
        }
        """
        import sys
        import tempfile
        import numpy as np

        # Make `from bpm_sanity import ...` work inside the container.
        sys.path.insert(0, "/app/backend")

        suffix = os.path.splitext(audio_filename)[1] or ".wav"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name

            from beat_this.inference import File2Beats  # type: ignore

            # Lazy module-level cache so successive calls in a warm container
            # reuse the loaded model (~3 GB GPU mem; ~5 s to load fresh).
            # Stash on the module dict to dodge the `global` UnboundLocalError
            # gotcha (assignment-then-read makes the name function-local).
            mod = sys.modules[__name__]
            predictor = getattr(mod, "_predictor", None)
            if predictor is None:
                predictor = File2Beats(
                    checkpoint_path="final0", device="cuda", float16=True
                )
                setattr(mod, "_predictor", predictor)

            beats_arr, downbeats_arr = predictor(tmp_path)

            # ----- Same cleanup & derivation as migrate_add_beats_via_beat_this -----
            beats_raw = [float(x) for x in beats_arr]
            downbeats = [float(x) for x in downbeats_arr]

            # Drop spurious sub-50ms intervals (occasional adjacent doubled
            # detections that would otherwise spike tempo to 600+ BPM).
            if len(beats_raw) >= 2:
                arr = np.asarray(beats_raw, dtype=float)
                diffs = np.diff(arr)
                valid_idx = np.concatenate(([True], diffs > 0.05))
                beats = arr[valid_idx].tolist()
            else:
                beats = beats_raw

            if not beats:
                return {
                    "bpm": 0.0,
                    "beats": [],
                    "downbeats": downbeats,
                    "tempo_curve": [],
                    "beats_source": "beat_this",
                    "bpm_correction": None,
                    "error": "no beats detected",
                }

            # Median-period BPM
            arr = np.asarray(beats, dtype=float)
            diffs = np.diff(arr)
            diffs = diffs[diffs > 0.05]
            bpm = float(60.0 / float(np.median(diffs))) if len(diffs) > 0 else 0.0

            # Smoothed per-beat tempo curve (window=4)
            tempo_curve = []
            if len(diffs) > 0:
                half = max(1, 4 // 2)
                n = len(diffs)
                for i in range(n):
                    lo = max(0, i - half)
                    hi = min(n, i + half + 1)
                    med = float(np.median(diffs[lo:hi]))
                    if med > 0.05:
                        tempo_curve.append(
                            {"t": float(arr[i]), "bpm": round(60.0 / med, 2)}
                        )

            bpm_correction_record = None
            if run_bpm_sanity:
                from bpm_sanity import (  # type: ignore
                    ballad_halving_check,
                    apply_halving_to_beat_info,
                )

                bpm_corrected, bpm_meta = ballad_halving_check(
                    bpm, tmp_path, bpm_range=(130.0, 220.0)
                )
                if bpm_meta.get("applied"):
                    beat_info = {"tempo_curve": tempo_curve, "downbeats": downbeats}
                    apply_halving_to_beat_info(beat_info)
                    tempo_curve = beat_info["tempo_curve"]
                    downbeats = beat_info["downbeats"]
                    bpm = bpm_corrected
                    bpm_correction_record = {
                        "applied": True,
                        "reason": bpm_meta["reason"],
                        "onset_density": bpm_meta["onset_density"],
                        "rms_cov": bpm_meta["rms_cov"],
                        "original": bpm_meta["original"],
                    }

            return {
                "bpm": round(bpm, 1),
                "beats": beats,
                "downbeats": downbeats,
                "tempo_curve": tempo_curve,
                "beats_source": "beat_this",
                "bpm_correction": bpm_correction_record,
            }
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Local-side dispatcher
# ---------------------------------------------------------------------------


def modal_beat_this_enabled() -> bool:
    """Opt-in flag. Set LIVECHORD_USE_MODAL_BEAT_THIS=1 on a host without a
    local CUDA + beat_this install to route on-demand beat_this switches to
    the deployed Modal function. See doc/MODAL_DEPLOY.md."""
    val = (os.environ.get("LIVECHORD_USE_MODAL_BEAT_THIS") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def detect_beats_via_modal(audio_path: str) -> dict:
    """Upload audio bytes to the deployed Modal function, return the beat-info
    dict ready to merge into a chord JSON. Raises if Modal isn't installed
    or the deployed app can't be reached — caller catches and surfaces.
    """
    if not _MODAL_AVAILABLE:
        raise RuntimeError(
            "modal package is not installed; pip install modal to enable "
            "LIVECHORD_USE_MODAL_BEAT_THIS."
        )

    func = modal.Function.from_name(
        "livechord-beat-this", "detect_beats_modal"
    )

    audio_filename = os.path.basename(audio_path)
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    return func.remote(
        audio_bytes=audio_bytes,
        audio_filename=audio_filename,
    )
