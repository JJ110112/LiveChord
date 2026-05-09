"""Evaluate the trained beat_refiner on the held-out test split.

Mirrors the trainer's in-loop eval (``scripts/train_beat_refiner.py``
``_eval_song`` / ``_f1_at_tolerance``) but runs it across the full
``backend/ai/splits/test.json`` (n=7390) instead of the tiny in-loop
val sample (n=6).

Output
------
A JSON report keyed by:

  ``overall``        : macro-F1 over all evaluated songs.
  ``by_beat_quality``: ``{gold: {...}, ok: {...}, ...}``
  ``by_source``      : ``{midi: {...}, btc_batch: {...}, ...}``
  ``per_song``       : optional, ``--include-per-song`` flag.

Each bucket contains: ``n_songs``, ``beat_f1``, ``downbeat_f1``,
``cb_f1``, ``beat_p``, ``beat_r``, ``downbeat_p``, ``downbeat_r``,
``elapsed_sec_total``.

The numbers go straight into the HF Hub model card README under the
"Metrics" section.

Usage
-----
::

    python scripts/eval_beat_refiner_holdout.py \\
        --splits-dir backend/ai/splits \\
        --features-cache G:/livechord/features \\
        --checkpoint data/models/beat_refiner.pt \\
        --out G:/livechord/eval_holdout.json

For a quick smoke test, add ``--limit 50`` to only evaluate the first
50 entries.

Note on "ground truth"
----------------------
The training corpus uses the chord-JSON's ``beats[]`` (originally from
beat_this) AS the gold label for songs whose beat_quality is "gold"
(low CV + high chord-change alignment). So the F1 reported here is
**self-consistency vs the prior tracker on filtered-good songs** — a
measure of how well the refiner preserves correct beats while the
model also adds chord-boundary supervision. It is NOT a direct
"refiner > beat_this" claim. That would require an independent ground
truth (MIREX-style human-annotated beats) which is out of scope for v1.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("eval_beat_refiner_holdout")


# Same tolerance as trainer (=±1 frame ≈92.88 ms, mir_eval standard is 70 ms).
_EVAL_TOL_FRAMES = 1


# ---------------------------------------------------------------------------
# Metric helpers (transplanted verbatim from train_beat_refiner.py so this
# script stands alone — no import cycle into the trainer module).
# ---------------------------------------------------------------------------

def _peak_pick(probs: np.ndarray, threshold: float = 0.5,
               min_distance: int = 2) -> List[int]:
    peaks: List[int] = []
    last = -10 ** 9
    for i in range(1, len(probs) - 1):
        if probs[i] < threshold:
            continue
        if probs[i] < probs[i - 1] or probs[i] <= probs[i + 1]:
            continue
        if i - last < min_distance:
            continue
        peaks.append(i)
        last = i
    return peaks


def _f1_at_tolerance(pred: List[int], gt: List[int], tol: int) -> Tuple[float, float, float]:
    """F1 with ±tol-frame matching window. Greedy sweep."""
    if not gt:
        return (1.0 if not pred else 0.0, 1.0, 1.0 if not pred else 0.0)
    if not pred:
        return 0.0, 0.0, 0.0
    gt_sorted = sorted(gt)
    pred_sorted = sorted(pred)
    matched = [False] * len(gt_sorted)
    tp = 0
    j = 0
    for p in pred_sorted:
        while j < len(gt_sorted) and gt_sorted[j] < p - tol:
            j += 1
        k = j
        while k < len(gt_sorted) and gt_sorted[k] <= p + tol:
            if not matched[k]:
                matched[k] = True
                tp += 1
                break
            k += 1
    fp = len(pred_sorted) - tp
    fn = len(gt_sorted) - tp
    p_ = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p_ * r / max(1e-9, p_ + r)
    return f1, p_, r


def _gt_frames(label: np.ndarray, T: int) -> List[int]:
    return [int(i) for i, v in enumerate(label[:T]) if v > 0.5]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class Bucket:
    n_songs: int = 0
    beat_f1_sum: float = 0.0
    beat_p_sum: float = 0.0
    beat_r_sum: float = 0.0
    downbeat_f1_sum: float = 0.0
    downbeat_p_sum: float = 0.0
    downbeat_r_sum: float = 0.0
    cb_f1_sum: float = 0.0
    raw_beat_f1_sum: float = 0.0       # F1(initial_grid, gt_beats) — sanity / round-trip baseline
    raw_downbeat_f1_sum: float = 0.0   # F1(initial_grid, gt_downbeats)

    def add(self, m: Dict):
        self.n_songs += 1
        self.beat_f1_sum += m["beat_f1"]
        self.beat_p_sum += m["beat_p"]
        self.beat_r_sum += m["beat_r"]
        self.downbeat_f1_sum += m["downbeat_f1"]
        self.downbeat_p_sum += m["downbeat_p"]
        self.downbeat_r_sum += m["downbeat_r"]
        self.cb_f1_sum += m["cb_f1"]
        self.raw_beat_f1_sum += m["raw_beat_f1"]
        self.raw_downbeat_f1_sum += m["raw_downbeat_f1"]

    def mean(self) -> Dict:
        n = max(1, self.n_songs)
        return {
            "n_songs": self.n_songs,
            "beat_f1": self.beat_f1_sum / n,
            "beat_p": self.beat_p_sum / n,
            "beat_r": self.beat_r_sum / n,
            "downbeat_f1": self.downbeat_f1_sum / n,
            "downbeat_p": self.downbeat_p_sum / n,
            "downbeat_r": self.downbeat_r_sum / n,
            "cb_f1": self.cb_f1_sum / n,
            "raw_beat_f1": self.raw_beat_f1_sum / n,
            "raw_downbeat_f1": self.raw_downbeat_f1_sum / n,
        }


# ---------------------------------------------------------------------------
# Per-song eval
# ---------------------------------------------------------------------------

def _eval_one(model, song: Dict, device, max_frames: int) -> Optional[Dict]:
    import torch
    from backend.ai.beat_refiner_model import MAX_FRAMES

    feat = song["features"]
    T = feat.shape[0]
    cap = min(max_frames, MAX_FRAMES)
    if T > cap:
        feat = feat[:cap]
        T = cap

    x = torch.from_numpy(feat).unsqueeze(0).to(device)
    pad = torch.zeros(1, T, dtype=torch.bool, device=device)
    t0 = time.perf_counter()
    with torch.no_grad():
        out = model(x, padding_mask=pad)
    p_beat = torch.sigmoid(out["beat_logits"][0]).cpu().numpy()
    p_db = torch.sigmoid(out["downbeat_logits"][0]).cpu().numpy()
    p_cb = torch.sigmoid(out["chord_boundary_logits"][0]).cpu().numpy()
    elapsed = time.perf_counter() - t0

    gt_beat = _gt_frames(song["beat_label"], T)
    gt_db = _gt_frames(song["downbeat_label"], T)
    gt_cb = _gt_frames(song["cb_label"], T)

    pred_beat = _peak_pick(p_beat, 0.5, 2)
    pred_db = _peak_pick(p_db, 0.5, 4)
    pred_cb = _peak_pick(p_cb, 0.5, 2)

    # Raw baseline = the initial beat grid that was fed in via the trailing
    # input channels. Round-trips the prior tracker's output through the
    # same F1 metric. Should be very close to 1.0 (it IS the label here).
    raw_beat = _gt_frames(feat[:, 98], T)
    raw_db = _gt_frames(feat[:, 99], T)

    bf, bp, br = _f1_at_tolerance(pred_beat, gt_beat, _EVAL_TOL_FRAMES)
    df, dp, dr = _f1_at_tolerance(pred_db, gt_db, _EVAL_TOL_FRAMES)
    cf, _, _ = _f1_at_tolerance(pred_cb, gt_cb, _EVAL_TOL_FRAMES)
    rbf, _, _ = _f1_at_tolerance(raw_beat, gt_beat, _EVAL_TOL_FRAMES)
    rdf, _, _ = _f1_at_tolerance(raw_db, gt_db, _EVAL_TOL_FRAMES)

    return {
        "beat_f1": bf, "beat_p": bp, "beat_r": br,
        "downbeat_f1": df, "downbeat_p": dp, "downbeat_r": dr,
        "cb_f1": cf,
        "raw_beat_f1": rbf, "raw_downbeat_f1": rdf,
        "T": T, "elapsed_sec": elapsed,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_test(splits_dir: Path) -> List[Dict]:
    p = splits_dir / "test.json"
    with p.open("r", encoding="utf-8") as f:
        d = json.load(f)
    return d["entries"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--splits-dir", type=Path,
                    default=Path("backend/ai/splits"))
    ap.add_argument("--features-cache", type=Path,
                    default=Path("G:/livechord/features"))
    ap.add_argument("--checkpoint", type=Path,
                    default=Path("data/models/beat_refiner.pt"))
    ap.add_argument("--out", type=Path,
                    default=Path("G:/livechord/eval_holdout.json"))
    ap.add_argument("--limit", type=int, default=None,
                    help="Only evaluate first N entries (smoke test)")
    ap.add_argument("--device", type=str, default=None,
                    help='"cuda" or "cpu" (default: auto)')
    ap.add_argument("--max-frames", type=int, default=12000,
                    help="Truncate songs longer than this many frames")
    ap.add_argument("--include-per-song", action="store_true",
                    help="Include per-song breakdown in output JSON")
    ap.add_argument("--quality-filter", type=str, default="all",
                    choices=["all", "gold", "gold_or_ok"],
                    help="Filter by beat_quality (default: all)")
    args = ap.parse_args()

    # Repo-root sys.path so `backend.*` imports resolve when this script is
    # run from anywhere.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    import torch
    from backend.ai.beat_refiner_model import build_default_model, MODEL_VERSION
    from backend.ai.beat_refiner_features import (
        load_cached, build_initial_grid_channels, compute_labels,
        features_path_for,
    )

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("device: %s", device)

    # Load checkpoint
    ckpt = torch.load(str(args.checkpoint), map_location=device,
                      weights_only=False)
    if ckpt.get("model_version") != MODEL_VERSION:
        raise SystemExit(
            f"checkpoint model_version={ckpt.get('model_version')} "
            f"!= runtime MODEL_VERSION={MODEL_VERSION}"
        )
    model = build_default_model().to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    logger.info("loaded ckpt epoch=%s val_metrics=%s",
                ckpt.get("epoch"), ckpt.get("val_metrics"))

    entries = _load_test(args.splits_dir)
    if args.quality_filter == "gold":
        entries = [e for e in entries if e.get("beat_quality") == "gold"]
    elif args.quality_filter == "gold_or_ok":
        entries = [e for e in entries
                   if e.get("beat_quality") in ("gold", "ok")]
    logger.info("test entries (after filter=%s): %d",
                args.quality_filter, len(entries))
    if args.limit:
        entries = entries[: args.limit]
        logger.info("limited to first %d entries", len(entries))

    overall = Bucket()
    by_beat_q: Dict[str, Bucket] = {}
    by_chord_q: Dict[str, Bucket] = {}
    by_source: Dict[str, Bucket] = {}
    by_beats_source: Dict[str, Bucket] = {}
    per_song: List[Dict] = []

    n_skip_no_cache = 0
    n_skip_no_label = 0
    n_skip_no_beats = 0
    n_skip_error = 0
    t_total = time.perf_counter()

    for i, e in enumerate(entries):
        h = e["hash"]
        cache_path = features_path_for(h, args.features_cache)
        cached = load_cached(cache_path)
        if cached is None:
            n_skip_no_cache += 1
            continue

        try:
            chord_data = json.loads(
                Path(e["label_path"]).read_text(encoding="utf-8")
            )
        except Exception as ex:
            logger.debug("label load failed for %s: %s", h, ex)
            n_skip_no_label += 1
            continue

        beats = list(map(float, chord_data.get("beats") or []))
        downbeats = list(map(float, chord_data.get("downbeats") or []))
        if not beats:
            n_skip_no_beats += 1
            continue

        try:
            feat = build_initial_grid_channels(cached["features"], beats, downbeats)
            labels = compute_labels(chord_data, n_frames=feat.shape[0])
            song = {
                "features": feat,
                "beat_label": labels["beat_label"],
                "downbeat_label": labels["downbeat_label"],
                "cb_label": labels["chord_boundary_label"],
            }
            m = _eval_one(model, song, device, args.max_frames)
        except Exception as ex:
            logger.warning("eval failed for %s: %s", h, ex)
            n_skip_error += 1
            continue

        if m is None:
            n_skip_error += 1
            continue

        overall.add(m)
        by_beat_q.setdefault(e.get("beat_quality", "?"), Bucket()).add(m)
        by_chord_q.setdefault(e.get("chord_quality", "?"), Bucket()).add(m)
        src = (e.get("metrics") or {}).get("source", "?")
        bs = (e.get("metrics") or {}).get("beats_source", "?")
        by_source.setdefault(src, Bucket()).add(m)
        by_beats_source.setdefault(bs, Bucket()).add(m)

        if args.include_per_song:
            per_song.append({
                "hash": h, "beat_quality": e.get("beat_quality"),
                "chord_quality": e.get("chord_quality"),
                "source": src, "beats_source": bs,
                **{k: round(v, 4) for k, v in m.items()
                   if k not in ("T",)},
            })

        if (i + 1) % 100 == 0:
            elapsed = time.perf_counter() - t_total
            logger.info(
                "  [%d/%d] overall beat_f1=%.3f downbeat_f1=%.3f cb_f1=%.3f "
                "(%.1f songs/s, eta %.1fmin)",
                i + 1, len(entries),
                overall.beat_f1_sum / max(1, overall.n_songs),
                overall.downbeat_f1_sum / max(1, overall.n_songs),
                overall.cb_f1_sum / max(1, overall.n_songs),
                (i + 1) / elapsed,
                (len(entries) - i - 1) / max(0.001, (i + 1) / elapsed) / 60,
            )

    elapsed_total = time.perf_counter() - t_total
    logger.info(
        "done in %.1fs — evaluated %d songs (skip: no_cache=%d no_label=%d "
        "no_beats=%d error=%d)",
        elapsed_total, overall.n_songs,
        n_skip_no_cache, n_skip_no_label, n_skip_no_beats, n_skip_error,
    )

    report = {
        "checkpoint": str(args.checkpoint),
        "ckpt_epoch": ckpt.get("epoch"),
        "ckpt_val_metrics": ckpt.get("val_metrics"),
        "device": device,
        "tolerance_frames": _EVAL_TOL_FRAMES,
        "tolerance_ms": round(_EVAL_TOL_FRAMES * 92.88, 1),
        "test_entries_total": len(entries),
        "n_evaluated": overall.n_songs,
        "n_skip_no_cache": n_skip_no_cache,
        "n_skip_no_label": n_skip_no_label,
        "n_skip_no_beats": n_skip_no_beats,
        "n_skip_error": n_skip_error,
        "elapsed_sec_total": round(elapsed_total, 1),
        "overall": overall.mean(),
        "by_beat_quality": {str(k): v.mean() for k, v in sorted(by_beat_q.items(), key=lambda kv: str(kv[0]))},
        "by_chord_quality": {str(k): v.mean() for k, v in sorted(by_chord_q.items(), key=lambda kv: str(kv[0]))},
        "by_source": {str(k): v.mean() for k, v in sorted(by_source.items(), key=lambda kv: str(kv[0]))},
        "by_beats_source": {str(k): v.mean() for k, v in sorted(by_beats_source.items(), key=lambda kv: str(kv[0]))},
    }
    if args.include_per_song:
        report["per_song"] = per_song

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    logger.info("wrote report -> %s", args.out)

    # Pretty print summary
    print()
    print(f"=== beat_refiner v{MODEL_VERSION} eval @ ±{_EVAL_TOL_FRAMES} frame tolerance "
          f"({_EVAL_TOL_FRAMES * 92.88:.0f} ms) ===")
    print(f"  evaluated:    {overall.n_songs}/{len(entries)} songs")
    o = overall.mean()
    print(f"  overall:      beat_f1={o['beat_f1']:.4f}  "
          f"downbeat_f1={o['downbeat_f1']:.4f}  cb_f1={o['cb_f1']:.4f}")
    print(f"  raw baseline: beat_f1={o['raw_beat_f1']:.4f}  "
          f"downbeat_f1={o['raw_downbeat_f1']:.4f}  "
          f"(round-trip; identity-bound)")
    print()
    for label, bucket in (("beat_quality", by_beat_q),
                          ("source", by_source),
                          ("beats_source", by_beats_source)):
        print(f"  by {label}:")
        for k, v in sorted(bucket.items(), key=lambda kv: str(kv[0])):
            mv = v.mean()
            print(f"    {str(k):<14} n={mv['n_songs']:<5} "
                  f"beat_f1={mv['beat_f1']:.4f} "
                  f"downbeat_f1={mv['downbeat_f1']:.4f} "
                  f"cb_f1={mv['cb_f1']:.4f}")
        print()


if __name__ == "__main__":
    main()
