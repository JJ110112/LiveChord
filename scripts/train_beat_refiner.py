"""Train the beat_refiner Compact Transformer.

End-to-end pipeline:
  1. Load splits from ``backend/ai/splits/{train,val,test}.json``
     (built by ``scripts/build_training_corpus.py --build``).
  2. For each song: lazy-load cached audio features (built by
     ``scripts/extract_features_batch.py``), build initial-grid channels
     from beats[]/downbeats[], compute per-frame labels.
  3. Sample 60-second random segments per training step.
  4. Forward through ``BeatRefiner`` (3 heads), confidence-weighted BCE
     across heads, apply gradient clip, AdamW + cosine schedule.
  5. Validate every epoch on the gold|gold subset (57 songs), tracking
     beat F1 / downbeat F1 / chord_boundary F1 at 70 ms tolerance.
  6. Save best checkpoint to ``--out`` keyed by val downbeat_f1.

Confidence-weighted loss
------------------------
Each song carries (beat_quality, chord_quality) in {gold, ok, skip}^2.

  beat_loss / downbeat_loss are masked OUT for songs with beat_quality=skip
    (the labels would be self-distillation noise — see plan doc).
  chord_boundary_loss is computed on every song (chord labels are usable
    even when beats are noisy).

Per-head per-song weight:
  beat / downbeat    gold->1.0, ok->0.5, skip->0.0
  chord_boundary     gold->1.0, ok->0.5, skip->0.0

Final loss = mean over batch of weighted sum across the three heads,
each normalized by the count of valid frames in the segment.

Why segment sampling
--------------------
Songs vary 60s–900s (~650–9700 frames). Full-song self-attention at
T=9700 is O(T²) = 94 M ops per layer per attention head — fits the GPU
but tanks throughput. 60-second segments (~646 frames) keep the
attention matrix at 416 K — comfortable for B=32 on a 16 GB GPU.

Songs shorter than the segment are loaded whole and padded; a padding
mask is built so the encoder ignores those positions.

Eval is full-song (chunked if longer than ``MAX_FRAMES``) so metrics
reflect production behavior, not segment-level performance.

Usage
-----
Smoke test (CPU, tiny config)::

    python scripts/train_beat_refiner.py --train \\
        --splits-dir backend/ai/splits \\
        --cache-root V:/data/features \\
        --epochs 1 --batch-size 4 --max-train-songs 100 --max-val-songs 20 \\
        --device cpu

Full training (PC, RTX 5080)::

    python scripts/train_beat_refiner.py --train \\
        --splits-dir backend/ai/splits \\
        --cache-root V:/data/features \\
        --epochs 30 --batch-size 32 --segment-frames 646 \\
        --device cuda

Eval-only on a saved checkpoint::

    python scripts/train_beat_refiner.py --eval \\
        --splits-dir backend/ai/splits \\
        --cache-root V:/data/features \\
        --checkpoint data/models/beat_refiner.pt
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_beat_refiner")


# ---------------------------------------------------------------------------
# Confidence-weight schedule — bumping these is a training hyperparameter,
# kept top-of-file for visibility. Changing the structure (e.g. adding
# new tier names) requires a coordinated update in build_training_corpus.
# ---------------------------------------------------------------------------

_BEAT_HEAD_WEIGHT = {"gold": 1.0, "ok": 0.5, "skip": 0.0}
_CHORD_HEAD_WEIGHT = {"gold": 1.0, "ok": 0.5, "skip": 0.0}

# Beat / downbeat positives are sparse (~5–20% of frames). Without
# pos_weight, BCE collapses to "predict 0 everywhere" early in training.
# 5.0 gives recall a ~5x reward; tuned empirically against
# train_bar_arbitrator.py's pos_weight=3.0.
_BEAT_POS_WEIGHT = 5.0
_DOWNBEAT_POS_WEIGHT = 8.0  # downbeats are 4x sparser than beats
_CHORD_BOUNDARY_POS_WEIGHT = 5.0

# Eval tolerance. Standard mir_eval beat tolerance is 70 ms; we round to
# the nearest frame at 92.9 ms / frame.
_EVAL_TOL_FRAMES = 1


# ---------------------------------------------------------------------------
# Lazy import of torch
# ---------------------------------------------------------------------------

def _import_torch():
    import torch  # noqa: F401
    return torch


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

@dataclass
class SongRecord:
    """One row in the training corpus — pre-resolved paths + tier tags."""
    hash: str
    label_path: Path
    audio_path: Optional[Path]  # for re-extraction if cache misses
    cache_path: Path
    beat_quality: str
    chord_quality: str


def _build_records(entries: List[Dict], cache_root: Path) -> List[SongRecord]:
    """Resolve each manifest entry to a SongRecord (no I/O yet)."""
    from backend.ai.beat_refiner_features import features_path_for
    out = []
    for e in entries:
        h = e["hash"]
        out.append(SongRecord(
            hash=h,
            label_path=Path(e["label_path"]),
            audio_path=Path(e["audio_path"]) if e.get("audio_path") else None,
            cache_path=features_path_for(h, cache_root),
            beat_quality=e.get("beat_quality", "skip"),
            chord_quality=e.get("chord_quality", "skip"),
        ))
    return out


def _load_song(rec: SongRecord) -> Optional[Dict]:
    """Load features + labels for one song. Returns None if cache missing.

    Returns dict with:
      features         np.ndarray (T, N_MODEL_CHANNELS) — audio + initial grid
      beat_label       np.ndarray (T,)
      downbeat_label   np.ndarray (T,)
      cb_label         np.ndarray (T,) — chord-boundary
      beat_quality / chord_quality  str
      hash             str
    """
    from backend.ai.beat_refiner_features import (
        load_cached, build_initial_grid_channels, compute_labels,
    )
    cached = load_cached(rec.cache_path)
    if cached is None:
        return None
    audio_feat = cached["features"]

    try:
        chord_data = json.loads(rec.label_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("failed to load %s: %s", rec.label_path, e)
        return None

    beats = list(map(float, chord_data.get("beats") or []))
    downbeats = list(map(float, chord_data.get("downbeats") or []))
    feat = build_initial_grid_channels(audio_feat, beats, downbeats)

    labels = compute_labels(chord_data, n_frames=feat.shape[0])
    return {
        "features": feat,
        "beat_label": labels["beat_label"],
        "downbeat_label": labels["downbeat_label"],
        "cb_label": labels["chord_boundary_label"],
        "beat_quality": rec.beat_quality,
        "chord_quality": rec.chord_quality,
        "hash": rec.hash,
    }


# ---------------------------------------------------------------------------
# Segment sampling + batching
# ---------------------------------------------------------------------------

# Channel offsets for the initial beat / downbeat grids in the model input.
# Mirrors backend.ai.beat_refiner_features.N_AUDIO_CHANNELS = 98 — the two
# trailing channels at indices 98 and 99 are the per-frame initial grids.
_BEAT_GRID_CH = 98
_DOWNBEAT_GRID_CH = 99


def _augment_initial_grid(features: np.ndarray, rng: random.Random) -> np.ndarray:
    """Perturb the initial beat/downbeat grid channels (98, 99) to simulate
    noisy beat-tracker output.

    WHY THIS EXISTS — read carefully before disabling
    -------------------------------------------------
    Channel 98 of the model input is rasterized ``beats[]`` from the chord
    JSON. The training label ``beat_label`` is *also* rasterized from the
    same ``beats[]``. So input channel 98 is *literally identical* to the
    beat label. Without augmentation, the model trivially learns
    ``beat_logits ≈ const * (input ch 98)`` — a 1-layer identity passthrough
    — and gets ~0 BCE loss while learning nothing about audio↔beat.

    At inference time on real songs, the input grid is whatever beat_this
    produced (often jittery, occasionally bar-doubled). A pure-identity
    model just copies the noise to the output → no refinement happens.

    By corrupting the input grid during training (jitter / drop / insert /
    bar doubling/halving) while keeping labels clean, we force the model
    to lean on CQT + chroma + onset + RMS to recover the truth. This is
    the residual-learning premise the whole refiner depends on.

    Augmentation operations (each independent coin flip per call) — v2
    ------------------------------------------------------------------
      phase_shift (p=0.15): all peaks shifted by same +/- {2,4,6,8} frames
                            — simulates tracker locked on wrong phase, the
                            single most common production failure mode and
                            the ROOT CAUSE of the 3+1 problem
      jitter      (p=0.7) : shift each beat by Gaussian(0, 1.5) frames
      drop        (p=0.3) : drop 10-30% of beats uniformly
      insert      (p=0.3) : insert spurious beats (5-20% of original count)
      halve       (p=0.15): bar-halving — keep every other peak (tracker
                            locked on halftime; ~3x more common in v2 than v1)
      double      (p=0.15): bar-doubling — insert peak between consecutive
                            pairs (tracker locked on doubletime)

    v1 -> v2 changes
    ----------------
    The v1 model peaked at val downbeat_f1=0.82 but barely improved real-
    world noisy songs (alignment +0.03). Diagnosis: v1 augmentation only
    simulated *random* noise (jitter/drop/insert), but real beat trackers
    fail in *structured* ways — phase shift, halftime lock, doubletime
    lock. v2 raises those probabilities so the model sees the production
    distribution during training.
    """
    out = features.copy()
    T = out.shape[0]

    for ch in (_BEAT_GRID_CH, _DOWNBEAT_GRID_CH):
        peaks = [int(i) for i in np.where(out[:, ch] > 0.5)[0]]
        if not peaks:
            continue

        new_peaks: List[int] = list(peaks)

        # Phase shift FIRST so jitter applies on top of the shifted grid
        # (matches production: real bar tracker emits a phase-shifted grid
        # then individual beats are perturbed further by detection noise).
        if rng.random() < 0.15:
            shift = rng.choice([-8, -6, -4, -2, 2, 4, 6, 8])
            new_peaks = [p + shift for p in new_peaks]

        if rng.random() < 0.7:
            new_peaks = [int(round(p + rng.gauss(0.0, 1.5))) for p in new_peaks]

        if rng.random() < 0.3:
            drop_p = rng.uniform(0.1, 0.3)
            new_peaks = [p for p in new_peaks if rng.random() > drop_p]

        if rng.random() < 0.3 and peaks:
            n_insert = max(1, int(len(peaks) * rng.uniform(0.05, 0.2)))
            new_peaks.extend(rng.randint(0, T - 1) for _ in range(n_insert))

        if rng.random() < 0.15 and len(new_peaks) >= 2:
            sorted_peaks = sorted(new_peaks)
            extras = []
            for a, b in zip(sorted_peaks, sorted_peaks[1:]):
                if b - a >= 4:  # only worth doubling if the gap > 4 frames
                    extras.append((a + b) // 2)
            new_peaks = sorted_peaks + extras

        if rng.random() < 0.15 and len(new_peaks) >= 2:
            new_peaks = sorted(new_peaks)[::2]

        # Re-rasterize: zero the channel, set 1.0 at each (clipped, deduped) peak
        out[:, ch] = 0.0
        for p in set(new_peaks):
            if 0 <= p < T:
                out[p, ch] = 1.0

    return out


def _sample_segment(song: Dict, seg_frames: int, rng: random.Random,
                    augment: bool = False) -> Dict:
    """Crop a random ``seg_frames``-long window from the song. If shorter,
    pad to seg_frames and emit a padding mask.

    When ``augment=True`` (training only), perturb the initial beat /
    downbeat grids per ``_augment_initial_grid``.
    """
    T = song["features"].shape[0]
    if T <= seg_frames:
        n_pad = seg_frames - T
        feat = np.pad(song["features"], ((0, n_pad), (0, 0)),
                      mode="constant", constant_values=0.0)
        beat = np.pad(song["beat_label"], (0, n_pad))
        db = np.pad(song["downbeat_label"], (0, n_pad))
        cb = np.pad(song["cb_label"], (0, n_pad))
        pad_mask = np.zeros(seg_frames, dtype=bool)
        pad_mask[T:] = True
        valid = np.zeros(seg_frames, dtype=np.float32)
        valid[:T] = 1.0
    else:
        start = rng.randint(0, T - seg_frames)
        end = start + seg_frames
        feat = song["features"][start:end]
        beat = song["beat_label"][start:end]
        db = song["downbeat_label"][start:end]
        cb = song["cb_label"][start:end]
        pad_mask = np.zeros(seg_frames, dtype=bool)
        valid = np.ones(seg_frames, dtype=np.float32)

    if augment:
        # Apply augmentation only AFTER cropping — we don't want a beat near
        # a chunk boundary getting jittered into the padding region or vice
        # versa. The augmentation operates on whatever grid frames lie inside
        # the segment.
        feat = _augment_initial_grid(feat, rng)

    return {
        "features": feat, "beat": beat, "downbeat": db, "cb": cb,
        "pad_mask": pad_mask, "valid": valid,
        "beat_quality": song["beat_quality"],
        "chord_quality": song["chord_quality"],
    }


def _collate(segments: List[Dict]):
    """Stack segments into tensors. All segments have the same seg_frames
    so this is trivially a stack."""
    torch = _import_torch()
    feat = np.stack([s["features"] for s in segments])
    beat = np.stack([s["beat"] for s in segments])
    db = np.stack([s["downbeat"] for s in segments])
    cb = np.stack([s["cb"] for s in segments])
    pad_mask = np.stack([s["pad_mask"] for s in segments])
    valid = np.stack([s["valid"] for s in segments])
    bw = np.array([_BEAT_HEAD_WEIGHT[s["beat_quality"]] for s in segments],
                  dtype=np.float32)
    cw = np.array([_CHORD_HEAD_WEIGHT[s["chord_quality"]] for s in segments],
                  dtype=np.float32)
    return {
        "features": torch.from_numpy(feat),
        "beat": torch.from_numpy(beat),
        "downbeat": torch.from_numpy(db),
        "cb": torch.from_numpy(cb),
        "pad_mask": torch.from_numpy(pad_mask),
        "valid": torch.from_numpy(valid),
        "beat_weight": torch.from_numpy(bw),
        "chord_weight": torch.from_numpy(cw),
    }


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------

def _multitask_loss(logits: Dict, batch: Dict, device) -> Tuple:
    """Sum of three per-head BCE losses, masked by per-song head weights
    and per-frame valid mask. Returns (total_loss, components_dict)."""
    torch = _import_torch()
    from torch.nn.functional import binary_cross_entropy_with_logits as bce

    valid = batch["valid"].to(device)
    bw = batch["beat_weight"].to(device).unsqueeze(1)   # (B, 1)
    cw = batch["chord_weight"].to(device).unsqueeze(1)

    def _head_loss(logit, label, head_weight, pos_weight):
        # Per-frame BCE; multiplied by per-frame valid mask + per-song head_weight
        loss = bce(
            logit, label.to(device),
            pos_weight=torch.tensor([pos_weight], device=device),
            reduction="none",
        )
        weighted = loss * valid * head_weight
        # Normalize by # of valid frames per song to avoid long-song bias
        denom = (valid * head_weight).sum().clamp(min=1.0)
        return weighted.sum() / denom

    l_beat = _head_loss(logits["beat_logits"], batch["beat"], bw, _BEAT_POS_WEIGHT)
    l_db = _head_loss(logits["downbeat_logits"], batch["downbeat"], bw, _DOWNBEAT_POS_WEIGHT)
    l_cb = _head_loss(logits["chord_boundary_logits"], batch["cb"], cw, _CHORD_BOUNDARY_POS_WEIGHT)

    # v2: cb head got dominated by beat/downbeat in v1 (cb_f1 collapsed
    # 0.345 -> 0.038 by ep30). Up-weighting cb forces the optimizer to
    # spend gradient on it. 3x is conservative; can push to 5x in v3 if
    # cb still under-trains.
    total = l_beat + l_db + 3.0 * l_cb
    return total, {"beat": float(l_beat.item()),
                   "downbeat": float(l_db.item()),
                   "cb": float(l_cb.item())}


# ---------------------------------------------------------------------------
# Eval — peak-pick logits, compute F1 at frame tolerance
# ---------------------------------------------------------------------------

def _peak_pick(probs: np.ndarray, threshold: float = 0.5,
               min_distance: int = 2) -> List[int]:
    """Local-maximum peak picking: a frame is a peak if it's > threshold,
    higher than both neighbors, and ≥min_distance from the previous peak."""
    peaks = []
    last = -10**9
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
    """F1 with a ±tol-frame matching window. Each gt event matches at most
    one pred (greedy, sweep-style)."""
    if not gt:
        return (1.0 if not pred else 0.0, 1.0, 1.0 if not pred else 0.0)
    if not pred:
        return 0.0, 0.0, 0.0
    gt_sorted = sorted(gt)
    pred_sorted = sorted(pred)
    matched_gt = [False] * len(gt_sorted)
    tp = 0
    j = 0
    for p in pred_sorted:
        while j < len(gt_sorted) and gt_sorted[j] < p - tol:
            j += 1
        # Find first unmatched gt within tol
        k = j
        while k < len(gt_sorted) and gt_sorted[k] <= p + tol:
            if not matched_gt[k]:
                matched_gt[k] = True
                tp += 1
                break
            k += 1
    fp = len(pred_sorted) - tp
    fn = len(gt_sorted) - tp
    p_ = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p_ * r / max(1e-9, p_ + r)
    return f1, p_, r


def _eval_song(model, song: Dict, device, max_frames: int) -> Dict:
    """Run the model on one song (chunked if too long) and compute per-head F1."""
    torch = _import_torch()
    from backend.ai.beat_refiner_model import MAX_FRAMES

    feat = song["features"]
    T = feat.shape[0]
    cap = min(max_frames, MAX_FRAMES)
    if T > cap:
        # Truncate for v1 simplicity. Chunked-overlap inference can come later.
        feat = feat[:cap]
        T = cap

    x = torch.from_numpy(feat).unsqueeze(0).to(device)
    pad = torch.zeros(1, T, dtype=torch.bool, device=device)
    with torch.no_grad():
        out = model(x, padding_mask=pad)
    p_beat = torch.sigmoid(out["beat_logits"][0]).cpu().numpy()
    p_db = torch.sigmoid(out["downbeat_logits"][0]).cpu().numpy()
    p_cb = torch.sigmoid(out["chord_boundary_logits"][0]).cpu().numpy()

    # Ground truth as frame indices
    def _gt_frames(label):
        return [int(i) for i, v in enumerate(label[:T]) if v > 0.5]

    gt_beat = _gt_frames(song["beat_label"])
    gt_db = _gt_frames(song["downbeat_label"])
    gt_cb = _gt_frames(song["cb_label"])

    pred_beat = _peak_pick(p_beat, threshold=0.5, min_distance=2)
    pred_db = _peak_pick(p_db, threshold=0.5, min_distance=4)
    pred_cb = _peak_pick(p_cb, threshold=0.5, min_distance=2)

    return {
        "beat": _f1_at_tolerance(pred_beat, gt_beat, _EVAL_TOL_FRAMES),
        "downbeat": _f1_at_tolerance(pred_db, gt_db, _EVAL_TOL_FRAMES),
        "cb": _f1_at_tolerance(pred_cb, gt_cb, _EVAL_TOL_FRAMES),
    }


def evaluate(model, val_records: List[SongRecord], device, max_frames: int,
             gold_only: bool = True) -> Dict:
    """Evaluate model on val. Returns macro F1 averaged over songs."""
    if gold_only:
        records = [r for r in val_records
                   if r.beat_quality == "gold" and r.chord_quality == "gold"]
        if not records:
            # Fall back to b_gold|c_ok if g|g empty
            records = [r for r in val_records if r.beat_quality == "gold"]
    else:
        records = val_records

    n = 0
    sums = {"beat_f1": 0.0, "downbeat_f1": 0.0, "cb_f1": 0.0}
    model.eval()
    for r in records:
        song = _load_song(r)
        if song is None:
            continue
        m = _eval_song(model, song, device, max_frames)
        sums["beat_f1"] += m["beat"][0]
        sums["downbeat_f1"] += m["downbeat"][0]
        sums["cb_f1"] += m["cb"][0]
        n += 1
    if n == 0:
        return {"n_songs": 0, "beat_f1": 0.0, "downbeat_f1": 0.0, "cb_f1": 0.0}
    return {
        "n_songs": n,
        "beat_f1": sums["beat_f1"] / n,
        "downbeat_f1": sums["downbeat_f1"] / n,
        "cb_f1": sums["cb_f1"] / n,
    }


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def _iter_train_batches(records: List[SongRecord], batch_size: int,
                        seg_frames: int, rng: random.Random,
                        augment: bool = True):
    """Infinite stream of training batches.

    Each step samples ``batch_size`` songs uniformly, loads each, and
    crops one random segment per song. Cache misses are skipped (next
    song picked) so a partial cache build still trains.

    ``augment=True`` perturbs the initial beat / downbeat grid channels
    so the model learns refinement, not identity passthrough — see
    ``_augment_initial_grid`` for the rationale. Disable only for
    debugging "is the model learning the trivial copy" sanity checks.
    """
    while True:
        # Pick batch_size song records — accept cache misses by skipping.
        batch = []
        attempts = 0
        max_attempts = batch_size * 5
        while len(batch) < batch_size and attempts < max_attempts:
            r = rng.choice(records)
            song = _load_song(r)
            attempts += 1
            if song is None:
                continue
            seg = _sample_segment(song, seg_frames, rng, augment=augment)
            batch.append(seg)
        if not batch:
            # All cache misses — degenerate, return partial-empty batch
            # to give the trainer a chance to log + bail.
            yield None
            return
        yield _collate(batch)


def train(args):
    torch = _import_torch()
    from backend.ai.beat_refiner_model import build_default_model, count_parameters

    device = torch.device(args.device)
    logger.info("device=%s", device)

    splits = json.loads((args.splits_dir / "train.json").read_text(encoding="utf-8"))
    val_split = json.loads((args.splits_dir / "val.json").read_text(encoding="utf-8"))

    train_records = _build_records(splits["entries"], args.cache_root)
    val_records = _build_records(val_split["entries"], args.cache_root)
    if args.max_train_songs:
        train_records = train_records[:args.max_train_songs]
    if args.max_val_songs:
        val_records = val_records[:args.max_val_songs]
    logger.info("train_records=%d val_records=%d", len(train_records), len(val_records))

    model = build_default_model().to(device)
    logger.info("model parameters: %d (%.2f M)",
                count_parameters(model), count_parameters(model) / 1e6)

    optim = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    steps_per_epoch = max(1, len(train_records) // args.batch_size)
    total_steps = args.epochs * steps_per_epoch

    # Warmup + cosine decay — small Transformer-friendly schedule.
    def _lr_lambda(step):
        warmup = max(1, args.warmup_steps)
        if step < warmup:
            return step / warmup
        progress = (step - warmup) / max(1, total_steps - warmup)
        return 0.5 * (1 + math.cos(math.pi * progress))
    scheduler = torch.optim.lr_scheduler.LambdaLR(optim, _lr_lambda)

    rng = random.Random(args.seed)
    best_f1 = -1.0
    best_state = None
    history = []
    no_improve = 0  # epochs since best_f1 last improved (early-stop counter)

    args.out.mkdir(parents=True, exist_ok=True)

    global_step = 0
    for epoch in range(1, args.epochs + 1):
        model.train()
        ep_t0 = time.time()
        ep_losses = {"beat": 0.0, "downbeat": 0.0, "cb": 0.0, "total": 0.0}
        n_batches = 0

        batch_iter = _iter_train_batches(
            train_records, args.batch_size, args.segment_frames, rng,
            augment=not args.no_augment,
        )
        for step in range(steps_per_epoch):
            batch = next(batch_iter)
            if batch is None:
                logger.error("all cache misses — extract features first?")
                return
            batch = {k: v.to(device) if hasattr(v, "to") else v
                     for k, v in batch.items()}
            logits = model(batch["features"], padding_mask=batch["pad_mask"])
            loss, components = _multitask_loss(logits, batch, device)

            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optim.step()
            scheduler.step()
            global_step += 1

            ep_losses["beat"] += components["beat"]
            ep_losses["downbeat"] += components["downbeat"]
            ep_losses["cb"] += components["cb"]
            ep_losses["total"] += float(loss.item())
            n_batches += 1

            if (step + 1) % args.log_every == 0:
                avg = {k: v / max(1, n_batches) for k, v in ep_losses.items()}
                cur_lr = optim.param_groups[0]["lr"]
                logger.info(
                    "  ep%d step%d/%d | total=%.4f beat=%.4f db=%.4f cb=%.4f | lr=%.6f",
                    epoch, step + 1, steps_per_epoch,
                    avg["total"], avg["beat"], avg["downbeat"], avg["cb"], cur_lr,
                )

        # Epoch-end eval
        ep_dt = time.time() - ep_t0
        logger.info("ep%d done in %.1fs — running val…", epoch, ep_dt)
        v = evaluate(model, val_records, device, args.eval_max_frames,
                     gold_only=True)
        logger.info(
            "  val (gold|gold n=%d): beat_f1=%.3f downbeat_f1=%.3f cb_f1=%.3f",
            v["n_songs"], v["beat_f1"], v["downbeat_f1"], v["cb_f1"],
        )
        history.append({
            "epoch": epoch, "step": global_step,
            "train_loss": ep_losses["total"] / max(1, n_batches),
            **{f"val_{k}": vv for k, vv in v.items()},
        })

        if v["downbeat_f1"] > best_f1:
            best_f1 = v["downbeat_f1"]
            no_improve = 0
            best_state = {k: vv.detach().cpu().clone()
                          for k, vv in model.state_dict().items()}
            torch.save({
                "model_state": best_state,
                "model_version": 1,
                "epoch": epoch, "global_step": global_step,
                "val_metrics": v,
                "config": vars(args),
            }, args.out / "beat_refiner.pt")
            logger.info("  new best downbeat_f1=%.3f - saved checkpoint", best_f1)
        else:
            no_improve += 1
            logger.info("  no improvement for %d epoch(s) (best=%.3f)",
                        no_improve, best_f1)
            if no_improve >= args.early_stop_patience:
                logger.info(
                    "early stop: %d consecutive epochs without improvement "
                    "(best downbeat_f1=%.3f at ep<=%d)",
                    no_improve, best_f1, epoch - no_improve,
                )
                break

    (args.out / "history.json").write_text(
        json.dumps(history, indent=2), encoding="utf-8"
    )
    logger.info("training done. best downbeat_f1=%.3f", best_f1)


def eval_only(args):
    torch = _import_torch()
    from backend.ai.beat_refiner_model import build_default_model

    device = torch.device(args.device)
    val_split = json.loads((args.splits_dir / "val.json").read_text(encoding="utf-8"))
    val_records = _build_records(val_split["entries"], args.cache_root)
    if args.max_val_songs:
        val_records = val_records[:args.max_val_songs]

    ckpt = torch.load(args.checkpoint, map_location=device)
    model = build_default_model().to(device)
    model.load_state_dict(ckpt["model_state"])

    v_gold = evaluate(model, val_records, device, args.eval_max_frames,
                      gold_only=True)
    v_all = evaluate(model, val_records, device, args.eval_max_frames,
                     gold_only=False)
    print(json.dumps({"val_gold_only": v_gold, "val_all": v_all},
                     indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--train", action="store_true")
    mode.add_argument("--eval", action="store_true")

    ap.add_argument("--splits-dir", type=Path,
                    default=_REPO / "backend" / "ai" / "splits")
    ap.add_argument("--cache-root", type=Path, default=Path("V:/data/features"))
    ap.add_argument("--out", type=Path, default=_REPO / "data" / "models")
    ap.add_argument("--checkpoint", type=Path, default=None,
                    help="Path to .pt for --eval mode")

    ap.add_argument("--device", type=str,
                    default="cuda" if _try_cuda() else "cpu")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--segment-frames", type=int, default=646,
                    help="60s @ 92.9 ms/frame (default 646)")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--warmup-steps", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--log-every", type=int, default=20)
    ap.add_argument("--eval-max-frames", type=int, default=4096,
                    help="Truncate eval songs to this many frames")
    ap.add_argument("--max-train-songs", type=int, default=None)
    ap.add_argument("--max-val-songs", type=int, default=None)
    ap.add_argument("--no-augment", action="store_true",
                    help="Disable initial-grid augmentation. ONLY for "
                         "debugging the trivial-identity sanity check - "
                         "with this flag the beat/downbeat heads will "
                         "collapse to ~0 loss and learn nothing useful.")
    ap.add_argument("--early-stop-patience", type=int, default=5,
                    help="Stop training if val downbeat_f1 doesn't improve "
                         "for this many consecutive epochs. v1 saw the peak "
                         "at ep6 with later epochs degrading cb_f1; default "
                         "5 keeps a safety margin while bounding wall time.")
    args = ap.parse_args()

    logger.info("config: %s", json.dumps(
        {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        ensure_ascii=False,
    ))

    if args.train:
        train(args)
    else:
        if args.checkpoint is None:
            ap.error("--eval requires --checkpoint")
        eval_only(args)


def _try_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    main()
