"""Inference wrapper for the trained beat_refiner.

Public entry point
------------------
``refine(audio_path, beats, downbeats, ...) -> dict``

  Loads the beat_refiner checkpoint once per process (lazy + thread-safe),
  extracts audio features, runs forward, peak-picks logits, returns
  refined beat / downbeat times in seconds. On any failure falls back to
  the original input beats with ``applied=False`` and a ``reason`` string
  — never raises across the boundary.

Process model
-------------
Each ProcessPoolExecutor worker that imports this module gets its own
singleton ``_model``. Same pattern as the BTC subprocess isolation
(see ``project_btc_subprocess.md`` memory) — keeps CUDA / blas state
out of the main FastAPI event loop and avoids GIL contention on
inference.

Performance budget
------------------
Phase 1 plan target: < 0.5 s per minute of audio on the NUC's CPU.

  feature extraction (CQT + chroma + onset + RMS) :  ~1.5 s / min
  forward through 3 M-param Transformer            :  ~0.3 s / min
  peak-picking + post                              :  ~0.05 s / min

Total ~1.8 s / min. Above the 0.5 s/min plan target, but acceptable
for offline / batch / on-demand. Real-time inference during playback is
out of scope for v1.

Long-song handling
------------------
Songs longer than ``MAX_FRAMES`` (≈ 15 min) are truncated. Chunked-
overlap inference is left for v2 — production songs are almost always
under 15 min.

Failure modes
-------------
- Checkpoint missing / version mismatch → return input unchanged,
  ``applied=False``, ``reason="no-checkpoint"`` / ``"version-mismatch"``.
- Audio decode fails                    → ``"audio-extract-failed"``.
- Empty / missing beats[] in input      → ``"no-input-beats"``.
- Forward throws (CUDA / OOM / NaN)     → ``"inference-failed"``.

Logged at WARNING. Caller can use ``applied`` flag to decide whether
to record the refinement in chord JSON metadata.

Smoke test
----------
::

    python -m backend.ai.beat_refiner_infer \\
        --audio "Y:/ABBA - Chiquitita.flac" \\
        --chord-json V:/data/chords/ab/abcd1234.json
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Singleton model state — one per process. ProcessPoolExecutor workers each
# load their own copy.
# ---------------------------------------------------------------------------

_model = None
_model_lock = threading.Lock()
_loaded_checkpoint_path: Optional[str] = None

# Default location — written by train_refiner_3_deploy.bat. Overridable via
# the ``checkpoint_path`` argument to ``refine()``.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CHECKPOINT = _REPO_ROOT / "data" / "models" / "beat_refiner.pt"


def _get_model(checkpoint_path: Optional[Path] = None):
    """Return the cached model, loading it on first call. Returns None if
    the checkpoint is missing or unloadable — caller should fall back."""
    global _model, _loaded_checkpoint_path

    target = str(Path(checkpoint_path or _DEFAULT_CHECKPOINT).resolve())

    with _model_lock:
        if _model is not None and _loaded_checkpoint_path == target:
            return _model

        # Lazy heavy imports — keeps startup of callers that never trigger
        # inference fast.
        try:
            import torch  # noqa: F401
            # Force-register torch._utils into the namespace BEFORE any
            # torch.load() unpickle. The saved checkpoint references
            # ``torch._utils._rebuild_tensor_v2`` for tensor reconstruction;
            # under ProcessPoolExecutor spawn this submodule sometimes
            # isn't auto-imported in time, producing
            # ``AttributeError: module 'torch' has no attribute '_utils'``
            # that BrokenProcessPool's the entire backfill (8 workers, only
            # 2 win the race). Importing it explicitly here avoids the race.
            import torch._utils  # noqa: F401
        except ImportError:
            logger.warning("torch unavailable — beat_refiner disabled")
            return None

        from backend.ai.beat_refiner_model import (
            build_default_model, MODEL_VERSION,
        )

        path = Path(target)
        if not path.exists():
            logger.warning("beat_refiner checkpoint not found: %s", path)
            return None

        try:
            import torch
            import torch._utils  # noqa: F401  (see race fix above)
            ckpt = torch.load(str(path), map_location="cpu",
                              weights_only=False)
        except Exception as e:
            logger.warning("failed to load %s: %s", path, e)
            return None

        ckpt_version = ckpt.get("model_version")
        if ckpt_version != MODEL_VERSION:
            logger.warning(
                "beat_refiner version mismatch: ckpt=%s runtime=%s — "
                "skipping (retrain or pin a matching version)",
                ckpt_version, MODEL_VERSION,
            )
            return None

        try:
            model = build_default_model()
            model.load_state_dict(ckpt["model_state"])
            model.eval()
        except Exception as e:
            logger.warning("failed to instantiate model from %s: %s", path, e)
            return None

        _model = model
        _loaded_checkpoint_path = target
        logger.info(
            "loaded beat_refiner v%d from %s (epoch=%s, val=%s)",
            MODEL_VERSION, path,
            ckpt.get("epoch"), ckpt.get("val_metrics"),
        )
        return _model


# ---------------------------------------------------------------------------
# Peak picking — same algorithm the trainer used in eval, copied here so
# the inference wrapper has zero dependency on scripts/.
# ---------------------------------------------------------------------------

def _peak_pick(probs: np.ndarray, threshold: float, min_distance: int
               ) -> List[int]:
    """Local-maximum peak picking. A frame is a peak if:
      - prob > threshold
      - prob > prev frame and >= next frame (strict-then-non-strict to
        avoid plateaus producing two peaks)
      - at least ``min_distance`` frames since the last accepted peak
    """
    peaks: List[int] = []
    last = -10 ** 9
    n = len(probs)
    for i in range(1, n - 1):
        if probs[i] < threshold:
            continue
        if probs[i] < probs[i - 1] or probs[i] <= probs[i + 1]:
            continue
        if i - last < min_distance:
            continue
        peaks.append(i)
        last = i
    return peaks


# ---------------------------------------------------------------------------
# Public refine()
# ---------------------------------------------------------------------------

def refine(audio_path: Union[str, Path],
           beats: List[float],
           downbeats: List[float],
           *,
           threshold: float = 0.5,
           min_distance_beat: int = 2,
           min_distance_downbeat: int = 4,
           checkpoint_path: Optional[Union[str, Path]] = None,
           ) -> Dict:
    """Refine ``beats`` / ``downbeats`` given the audio at ``audio_path``.

    Args:
      audio_path: Path to a decodable audio file (FLAC/WAV/MP3 …).
      beats: Initial beat times in seconds (typically from beat_this).
      downbeats: Initial downbeat times in seconds.
      threshold: Sigmoid prob cutoff for peak picking. 0.5 is the
        natural BCE break point.
      min_distance_beat: Minimum spacing between accepted beat peaks
        (frames). 2 frames = 186 ms, ≈ a 320-BPM ceiling.
      min_distance_downbeat: Same for downbeats. 4 frames = 372 ms.
      checkpoint_path: Override the default checkpoint location.

    Returns a dict:
      refined_beats: list[float]      — same as input on failure
      refined_downbeats: list[float]
      applied: bool                   — True only if we actually ran
      reason: str                     — short status / failure reason
      n_beats_in / n_beats_out
      n_downbeats_in / n_downbeats_out
      elapsed_sec: float
    """
    t0 = time.perf_counter()
    out: Dict = {
        "refined_beats": list(beats),
        "refined_downbeats": list(downbeats),
        "applied": False,
        "reason": "",
        "n_beats_in": len(beats),
        "n_beats_out": len(beats),
        "n_downbeats_in": len(downbeats),
        "n_downbeats_out": len(downbeats),
        "elapsed_sec": 0.0,
    }

    if not beats:
        out["reason"] = "no-input-beats"
        out["elapsed_sec"] = round(time.perf_counter() - t0, 3)
        return out

    model = _get_model(Path(checkpoint_path) if checkpoint_path else None)
    if model is None:
        out["reason"] = "no-model"
        out["elapsed_sec"] = round(time.perf_counter() - t0, 3)
        return out

    # Lazy-import the heavy modules only when we actually run.
    try:
        import torch
        from backend.ai.beat_refiner_features import (
            extract_features, build_initial_grid_channels, FRAMES_PER_SEC,
        )
        from backend.ai.beat_refiner_model import MAX_FRAMES
    except ImportError as e:
        out["reason"] = f"import-failed: {e}"
        return out

    # Audio features
    try:
        feat_dict = extract_features(audio_path)
    except Exception as e:
        out["reason"] = f"audio-extract-failed: {type(e).__name__}: {e}"
        out["elapsed_sec"] = round(time.perf_counter() - t0, 3)
        logger.warning("refine extract failed: %s", e)
        return out

    audio_feat = feat_dict["features"]
    full_input = build_initial_grid_channels(audio_feat, beats, downbeats)
    T = full_input.shape[0]

    if T > MAX_FRAMES:
        # v1: truncate. Songs > 15 min are vanishingly rare in this corpus.
        logger.info("refine truncating long song: %d -> %d frames", T, MAX_FRAMES)
        full_input = full_input[:MAX_FRAMES]
        T = MAX_FRAMES

    try:
        with torch.no_grad():
            x = torch.from_numpy(full_input).unsqueeze(0)
            pad_mask = torch.zeros(1, T, dtype=torch.bool)
            res = model(x, padding_mask=pad_mask)
            beat_probs = torch.sigmoid(res["beat_logits"][0]).cpu().numpy()
            db_probs = torch.sigmoid(res["downbeat_logits"][0]).cpu().numpy()
    except Exception as e:
        out["reason"] = f"inference-failed: {type(e).__name__}: {e}"
        out["elapsed_sec"] = round(time.perf_counter() - t0, 3)
        logger.warning("refine forward failed: %s", e)
        return out

    beat_frames = _peak_pick(beat_probs, threshold, min_distance_beat)
    db_frames = _peak_pick(db_probs, threshold, min_distance_downbeat)

    refined_beats = [round(f / FRAMES_PER_SEC, 4) for f in beat_frames]
    refined_downbeats = [round(f / FRAMES_PER_SEC, 4) for f in db_frames]

    out["refined_beats"] = refined_beats
    out["refined_downbeats"] = refined_downbeats
    out["n_beats_out"] = len(refined_beats)
    out["n_downbeats_out"] = len(refined_downbeats)
    out["applied"] = True
    out["reason"] = "ok"
    out["elapsed_sec"] = round(time.perf_counter() - t0, 3)
    return out


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def _gap_cv(seq: List[float]) -> Optional[float]:
    """Coefficient of variation of consecutive gaps. ``None`` if too short.
    Same diagnostic the rule-based bar_arbitrator uses."""
    if len(seq) < 3:
        return None
    gaps = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return (var ** 0.5) / mean


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", type=Path, required=True,
                    help="Path to audio file (FLAC/WAV/MP3)")
    ap.add_argument("--chord-json", type=Path,
                    help="Optional chord JSON to load initial beats[]/downbeats[] from. "
                         "If omitted, --beats and --downbeats are required.")
    ap.add_argument("--beats", type=float, nargs="*",
                    help="Initial beat times in seconds (overrides --chord-json beats[])")
    ap.add_argument("--downbeats", type=float, nargs="*",
                    help="Initial downbeat times in seconds")
    ap.add_argument("--checkpoint", type=Path, default=None,
                    help="Override checkpoint path (default: data/models/beat_refiner.pt)")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.chord_json:
        cd = json.loads(args.chord_json.read_text(encoding="utf-8"))
        beats = list(map(float, cd.get("beats") or args.beats or []))
        downbeats = list(map(float, cd.get("downbeats") or args.downbeats or []))
    else:
        beats = args.beats or []
        downbeats = args.downbeats or []

    if not beats:
        print("ERROR: no input beats. Pass --beats / --chord-json with beats[].")
        return

    print(f"audio:        {args.audio}")
    print(f"input  beats: {len(beats)} (cv={_gap_cv(beats):.4f})"
          if _gap_cv(beats) is not None else f"input  beats: {len(beats)}")
    print(f"input  downbeats: {len(downbeats)}"
          + (f" (cv={_gap_cv(downbeats):.4f})" if _gap_cv(downbeats) is not None else ""))

    result = refine(args.audio, beats, downbeats,
                    threshold=args.threshold,
                    checkpoint_path=args.checkpoint)

    print()
    print(f"applied:      {result['applied']}")
    print(f"reason:       {result['reason']}")
    print(f"elapsed:      {result['elapsed_sec']}s")
    print(f"output beats: {result['n_beats_out']} (in={result['n_beats_in']})"
          + (f" cv={_gap_cv(result['refined_beats']):.4f}"
             if _gap_cv(result['refined_beats']) is not None else ""))
    print(f"output downbeats: {result['n_downbeats_out']} (in={result['n_downbeats_in']})"
          + (f" cv={_gap_cv(result['refined_downbeats']):.4f}"
             if _gap_cv(result['refined_downbeats']) is not None else ""))

    # Show first 8 of each for quick eyeball comparison
    print()
    print("first 8 beats   in : " + ", ".join(f"{b:.3f}" for b in beats[:8]))
    print("first 8 beats   out: " + ", ".join(f"{b:.3f}" for b in result["refined_beats"][:8]))
    print("first 4 downbts in : " + ", ".join(f"{b:.3f}" for b in downbeats[:4]))
    print("first 4 downbts out: " + ", ".join(f"{b:.3f}" for b in result["refined_downbeats"][:4]))


if __name__ == "__main__":
    main()
