"""Train the bar_arbitrator Transformer.

End-to-end pipeline:
  1. Walk a chord JSON corpus (sharded ``<hash[:2]>/<hash>.json`` layout).
  2. Filter to "clean" / "mild_drift" songs (raw downbeat CV ≤ max-cv).
  3. Build per-beat features via backend.ai.bar_arbitrator.build_beat_features.
  4. Generate per-beat downbeat labels from raw downbeats[].
  5. Train tiny Transformer encoder (~100k params, BCE on downbeat head +
     CE on bpb head).
  6. Export to ONNX at data/models/bar_arbitrator_v1.onnx.

Usage
-----
Dry-run (no torch needed)::

    python scripts/train_bar_arbitrator.py --dry-run --no-torch

Stats only (count what would be yielded, no training)::

    python scripts/train_bar_arbitrator.py --stats --root V:/data/chords

Real training::

    python scripts/train_bar_arbitrator.py --train \\
        --root V:/data/chords \\
        --epochs 20 \\
        --batch-size 16 \\
        --max-samples 5000 \\
        --out-onnx data/models/bar_arbitrator_v1.onnx

Read-only on the chord corpus — won't touch any chord JSON. Reads V:\\
across 78k+ files; first epoch takes a while as files load. We materialize
features into an in-memory cache after first pass to keep subsequent
epochs fast (don't enable --max-samples=all without RAM headroom).
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_bar_arb")


# ---------------------------------------------------------------------------
# Feature spec — frozen here so the model architecture and the inference
# code in bar_arbitrator agree on exactly what goes into the encoder.
# Importing FEATURE_DIM from the runtime module keeps them in sync.
# ---------------------------------------------------------------------------

from backend.ai.bar_arbitrator import (  # noqa: E402
    FEATURE_DIM,
    build_beat_features,
    _gap_stats,
)

BPB_CLASSES = (3, 4, 6)


# Per-beat label tolerance: how close a beat-time must be to a raw
# downbeat to receive label=1. Slightly wider than the inference tolerance
# (_GRID_MATCH_TOL_BEAT=0.12) so the model learns soft windows.
_LABEL_TOL_SEC = 0.15


# ---------------------------------------------------------------------------
# Torch lazy import
# ---------------------------------------------------------------------------

_torch = None


def _try_import_torch():
    global _torch
    if _torch is not None:
        return _torch
    try:
        import torch  # noqa
        _torch = torch
        return torch
    except ImportError as e:
        print(f"torch unavailable: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Dataset — corpus filtering + label building
# ---------------------------------------------------------------------------

def _bpb_label(downbeats, beats):
    """Snap (median db gap / median beat gap) to nearest of {3, 4, 6}."""
    if len(downbeats) < 4 or len(beats) < 4:
        return None
    db_gaps = sorted(downbeats[i + 1] - downbeats[i] for i in range(len(downbeats) - 1))
    bt_gaps = sorted(beats[i + 1] - beats[i] for i in range(len(beats) - 1))
    db_med = db_gaps[len(db_gaps) // 2]
    bt_med = bt_gaps[len(bt_gaps) // 2]
    if bt_med <= 0:
        return None
    ratio = db_med / bt_med
    return min(BPB_CLASSES, key=lambda k: abs(ratio - k))


def _build_labels(beats, downbeats, tol=_LABEL_TOL_SEC):
    """Per-beat binary downbeat labels from raw downbeats[]."""
    import numpy as np
    if not beats or not downbeats:
        return None
    y = np.zeros(len(beats), dtype=np.float32)
    db_sorted = sorted(downbeats)
    j = 0
    for i, t in enumerate(beats):
        while j < len(db_sorted) and db_sorted[j] < t - tol:
            j += 1
        if j < len(db_sorted) and db_sorted[j] <= t + tol:
            y[i] = 1.0
    return y


def _is_trainable(cd, max_cv, min_beats=32, min_downbeats=8, min_chords=8):
    """Returns (ok, reason)."""
    beats = cd.get("beats") or []
    downbeats = cd.get("downbeats") or []
    chords = cd.get("chords") or []
    if not cd.get("beats_source"):
        return False, "no-beats-source"
    if len(beats) < min_beats:
        return False, "too-few-beats"
    if len(downbeats) < min_downbeats:
        return False, "too-few-downbeats"
    if len(chords) < min_chords:
        return False, "too-few-chords"
    _, cv = _gap_stats(downbeats)
    if cv is None or cv > max_cv:
        return False, "raw-cv-too-high"
    return True, "ok"


def collect_samples(root: Path, max_cv: float, skip_recent_sec: float,
                    max_samples: int | None, seed: int = 42):
    """Walk the corpus, filter, and materialize all training samples in memory.

    Returns: list of dicts {"X": ndarray(T, F), "y_db": ndarray(T,), "y_bpb": int, "hash": str}.
    """
    import numpy as np
    rng = random.Random(seed)

    counts = Counter()
    samples = []
    now = time.time()

    # Two-pass: first list all candidate paths so we can shuffle and apply
    # max_samples uniformly across the corpus (instead of biasing toward
    # whichever shard rglob hits first).
    paths = []
    for p in root.rglob("*.json"):
        if ".bak." in p.name:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if now - mt < skip_recent_sec:
            counts["skip_recent"] += 1
            continue
        paths.append(p)

    rng.shuffle(paths)
    logger.info("collect_samples: %d candidate files after recent-skip", len(paths))

    last_log = time.time()
    for i, p in enumerate(paths):
        if max_samples and len(samples) >= max_samples:
            break
        try:
            cd = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            counts["unparseable"] += 1
            continue

        ok, reason = _is_trainable(cd, max_cv=max_cv)
        if not ok:
            counts[f"skip_{reason}"] += 1
            continue

        feat = build_beat_features(cd)
        if feat is None or feat.shape[1] != FEATURE_DIM or feat.shape[0] < 32:
            counts["skip_features"] += 1
            continue

        y_db = _build_labels(cd["beats"], cd["downbeats"])
        if y_db is None or len(y_db) != feat.shape[0]:
            counts["skip_labels"] += 1
            continue

        y_bpb = _bpb_label(cd["downbeats"], cd["beats"])
        if y_bpb is None:
            counts["skip_no_bpb"] += 1
            continue

        samples.append({"X": feat, "y_db": y_db, "y_bpb": y_bpb, "hash": p.stem})
        counts["trainable"] += 1

        if time.time() - last_log > 10:
            logger.info("  scanned %d / %d, kept %d", i + 1, len(paths), len(samples))
            last_log = time.time()

    return samples, dict(counts)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def build_model(n_layers=3, d_model=64, n_heads=4, dropout=0.1):
    torch = _try_import_torch()
    if torch is None:
        raise RuntimeError("torch required to build model")
    import torch.nn as nn

    class BarArbitrator(nn.Module):
        def __init__(self):
            super().__init__()
            self.input_proj = nn.Linear(FEATURE_DIM, d_model)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=n_heads,
                dim_feedforward=d_model * 2, dropout=dropout,
                batch_first=True, activation="gelu",
            )
            self.encoder = nn.TransformerEncoder(enc_layer, num_layers=n_layers)
            self.downbeat_head = nn.Linear(d_model, 1)
            self.bpb_head = nn.Linear(d_model, len(BPB_CLASSES))

        def forward(self, x, mask=None):
            h = self.input_proj(x)
            h = self.encoder(h, src_key_padding_mask=mask)
            db_logits = self.downbeat_head(h).squeeze(-1)
            if mask is not None:
                m = (~mask).float().unsqueeze(-1)
                pooled = (h * m).sum(dim=1) / m.sum(dim=1).clamp(min=1)
            else:
                pooled = h.mean(dim=1)
            bpb_logits = self.bpb_head(pooled)
            return db_logits, bpb_logits

    return BarArbitrator()


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def _pad_batch(samples, max_t):
    """Pad a list of samples to (B, max_t, F). Returns (X, y_db, y_bpb, mask)."""
    import numpy as np
    import torch
    B = len(samples)
    X = np.zeros((B, max_t, FEATURE_DIM), dtype=np.float32)
    y_db = np.zeros((B, max_t), dtype=np.float32)
    mask = np.ones((B, max_t), dtype=bool)  # True = padding
    y_bpb = np.zeros(B, dtype=np.int64)
    for i, s in enumerate(samples):
        T = s["X"].shape[0]
        X[i, :T] = s["X"]
        y_db[i, :T] = s["y_db"]
        mask[i, :T] = False
        y_bpb[i] = BPB_CLASSES.index(s["y_bpb"])
    return (torch.from_numpy(X), torch.from_numpy(y_db),
            torch.from_numpy(y_bpb), torch.from_numpy(mask))


def _iter_batches(samples, batch_size, shuffle=True, seed=0):
    """Yield batches sorted by length within each shuffle bucket — keeps
    padding overhead small without losing too much randomness."""
    rng = random.Random(seed)
    idx = list(range(len(samples)))
    if shuffle:
        rng.shuffle(idx)
    for start in range(0, len(idx), batch_size):
        batch = [samples[i] for i in idx[start:start + batch_size]]
        max_t = max(s["X"].shape[0] for s in batch)
        yield _pad_batch(batch, max_t)


def _eval_metrics(model, samples, batch_size, device):
    """Per-beat downbeat F1 + bpb accuracy on a sample list."""
    import torch
    from torch.nn.functional import binary_cross_entropy_with_logits as bce
    model.eval()
    tp = fp = fn = 0
    bpb_correct = 0
    bpb_total = 0
    total_loss = 0.0
    n_beats = 0
    with torch.no_grad():
        for X, y_db, y_bpb, mask in _iter_batches(samples, batch_size, shuffle=False):
            X, y_db, y_bpb, mask = X.to(device), y_db.to(device), y_bpb.to(device), mask.to(device)
            db_logits, bpb_logits = model(X, mask=mask)
            valid = ~mask
            loss = bce(db_logits[valid], y_db[valid], reduction="sum")
            total_loss += float(loss.item())
            n_beats += int(valid.sum().item())
            pred = (db_logits.sigmoid() > 0.5) & valid
            true = (y_db > 0.5) & valid
            tp += int((pred & true).sum().item())
            fp += int((pred & ~true).sum().item())
            fn += int((~pred & true).sum().item())
            bpb_pred = bpb_logits.argmax(dim=-1)
            bpb_correct += int((bpb_pred == y_bpb).sum().item())
            bpb_total += int(y_bpb.shape[0])
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f1 = 2 * p * r / max(1e-9, p + r)
    return {
        "loss_per_beat": total_loss / max(1, n_beats),
        "downbeat_p": p,
        "downbeat_r": r,
        "downbeat_f1": f1,
        "bpb_acc": bpb_correct / max(1, bpb_total),
    }


def train_model(samples, *, epochs, batch_size, lr, val_frac, device, log_every=20):
    torch = _try_import_torch()
    import torch.nn.functional as F
    from torch.nn.functional import binary_cross_entropy_with_logits as bce

    rng = random.Random(0)
    rng.shuffle(samples)
    n_val = max(1, int(len(samples) * val_frac))
    val = samples[:n_val]
    train = samples[n_val:]
    logger.info("train=%d val=%d", len(train), len(val))

    # Class imbalance: in 4/4 songs ~25% of beats are downbeats; in 3/4 ~33%.
    # Pop dataset is heavily 4/4 so positives ~25% — train with pos_weight=3
    # so BCE rewards recall enough that the model doesn't collapse to "no
    # downbeat anywhere" (which would give an okay-looking BCE but f1 = 0).
    pos_weight = torch.tensor([3.0], device=device)

    model = build_model().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    logger.info("model params: %d", n_params)

    optim = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    best_f1 = -1.0
    best_state = None

    for ep in range(1, epochs + 1):
        model.train()
        ep_loss = 0.0
        ep_beats = 0
        t0 = time.time()
        for step, (X, y_db, y_bpb, mask) in enumerate(_iter_batches(train, batch_size, seed=ep)):
            X, y_db, y_bpb, mask = X.to(device), y_db.to(device), y_bpb.to(device), mask.to(device)
            db_logits, bpb_logits = model(X, mask=mask)
            valid = ~mask
            loss_db = bce(db_logits[valid], y_db[valid], pos_weight=pos_weight)
            loss_bpb = F.cross_entropy(bpb_logits, y_bpb)
            loss = loss_db + 0.3 * loss_bpb
            optim.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optim.step()
            ep_loss += float(loss_db.item()) * int(valid.sum().item())
            ep_beats += int(valid.sum().item())
            if (step + 1) % log_every == 0:
                logger.info("  ep%d step%d loss_db=%.4f loss_bpb=%.4f", ep, step + 1,
                            float(loss_db.item()), float(loss_bpb.item()))
        train_loss = ep_loss / max(1, ep_beats)
        m = _eval_metrics(model, val, batch_size, device)
        dt = time.time() - t0
        logger.info(
            "ep%d done in %.1fs | train_loss=%.4f | val: loss=%.4f f1=%.3f p=%.3f r=%.3f bpb_acc=%.3f",
            ep, dt, train_loss, m["loss_per_beat"], m["downbeat_f1"],
            m["downbeat_p"], m["downbeat_r"], m["bpb_acc"],
        )
        if m["downbeat_f1"] > best_f1:
            best_f1 = m["downbeat_f1"]
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            logger.info("  new best f1=%.3f, kept", best_f1)

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, best_f1, val


# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------

def export_onnx(model, out_path: Path):
    torch = _try_import_torch()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    model.eval()
    # Dummy input — variable T via dynamic shapes. Use seq_len=200 (and B=2)
    # rather than 64 so dynamic-shape inference doesn't get confused with
    # d_model (which is also 64).
    dummy_x = torch.zeros((2, 200, FEATURE_DIM), dtype=torch.float32)
    dummy_mask = torch.zeros((2, 200), dtype=torch.bool)

    class _Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x, mask):
            db, bpb = self.m(x, mask=mask)
            return db, bpb

    # Reconfigure stdout to UTF-8 so the dynamo exporter's "[torch.onnx] ✅"
    # success print doesn't crash on Windows cp1252. The legacy
    # TorchScript-based exporter is also broken for nn.TransformerEncoder
    # — it bakes the dummy seq_len into MultiheadAttention.Reshape ops, so
    # inference fails on any other T. dynamo (torch.export-based) handles
    # dynamic shapes correctly.
    import io
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    from torch.export import Dim
    T = Dim("time", min=8, max=4096)
    B = Dim("batch", min=1, max=64)
    dynamic_shapes = ({0: B, 1: T}, {0: B, 1: T})

    onnx_program = torch.onnx.export(
        _Wrap(model),
        (dummy_x, dummy_mask),
        input_names=["features", "padding_mask"],
        output_names=["downbeat_logits", "bpb_logits"],
        dynamic_shapes=dynamic_shapes,
        opset_version=18,
        dynamo=True,
    )
    onnx_program.save(str(out_path))
    logger.info("ONNX exported to %s (%d bytes)", out_path, out_path.stat().st_size)


# ---------------------------------------------------------------------------
# Dry-run + stats commands
# ---------------------------------------------------------------------------

def cmd_dry_run(no_torch: bool):
    fixtures = _REPO / "backend" / "tests" / "fixtures" / "bar_arbitrator"
    files = sorted(fixtures.glob("*.json"))
    print(f"Found {len(files)} fixtures.")

    if no_torch:
        for f in files:
            d = json.loads(f.read_text(encoding="utf-8"))
            feat = build_beat_features(d)
            shape = feat.shape if feat is not None else None
            print(f"  {f.stem}: feat_shape={shape} bpm={d.get('bpm')}")
        return 0

    torch = _try_import_torch()
    if torch is None:
        return 2
    model = build_model()
    print(f"Model: {sum(p.numel() for p in model.parameters()):,} params")
    model.eval()
    with torch.no_grad():
        for f in files:
            d = json.loads(f.read_text(encoding="utf-8"))
            feat = build_beat_features(d)
            if feat is None:
                print(f"  {f.stem}: no features")
                continue
            x = torch.from_numpy(feat).unsqueeze(0)
            db_logits, bpb_logits = model(x)
            print(f"  {f.stem}: T={feat.shape[0]} db_out={tuple(db_logits.shape)} "
                  f"bpb_pred={BPB_CLASSES[int(bpb_logits.argmax(dim=-1).item())]}")
    return 0


def cmd_stats(root: Path, max_cv: float, skip_recent_sec: float, sample_limit: int | None):
    """Quick stats sweep — count what would be yielded without building features."""
    counts = Counter()
    bpb_dist = Counter()
    now = time.time()
    paths = []
    for p in root.rglob("*.json"):
        if ".bak." in p.name:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if now - mt < skip_recent_sec:
            counts["skip_recent"] += 1
            continue
        paths.append(p)

    if sample_limit:
        random.Random(42).shuffle(paths)
        paths = paths[:sample_limit]

    logger.info("scanning %d files", len(paths))
    for i, p in enumerate(paths):
        try:
            cd = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            counts["unparseable"] += 1
            continue
        ok, reason = _is_trainable(cd, max_cv=max_cv)
        if not ok:
            counts[f"skip_{reason}"] += 1
            continue
        bpb = _bpb_label(cd["downbeats"], cd["beats"])
        if bpb is None:
            counts["skip_no_bpb"] += 1
            continue
        counts["trainable"] += 1
        bpb_dist[bpb] += 1
        if (i + 1) % 5000 == 0:
            logger.info("  scanned %d, trainable so far %d", i + 1, counts["trainable"])

    print(json.dumps({
        "n_scanned": len(paths),
        "counts": dict(counts),
        "bpb_dist": dict(bpb_dist),
    }, indent=2))


def cmd_export_only(args):
    """Re-export an existing .pt checkpoint to ONNX. Used when training
    succeeded but ONNX export crashed (e.g. encoding bug, opset mismatch)."""
    torch = _try_import_torch()
    if torch is None:
        return 2
    ckpt_path = Path(args.out_onnx).with_suffix(".pt")
    if not ckpt_path.exists():
        logger.error("checkpoint not found: %s", ckpt_path)
        return 3
    logger.info("loading checkpoint %s", ckpt_path)
    ckpt = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    model = build_model()
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    logger.info("loaded — best_f1=%s", ckpt.get("best_f1"))
    export_onnx(model, Path(args.out_onnx))
    return 0


def cmd_train(args):
    torch = _try_import_torch()
    if torch is None:
        return 2

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("device=%s torch=%s", device, torch.__version__)

    logger.info("collecting samples from %s (max_cv=%s, max_samples=%s)",
                args.root, args.max_cv, args.max_samples)
    samples, counts = collect_samples(
        args.root, args.max_cv, args.skip_recent_sec, args.max_samples
    )
    logger.info("counts: %s", counts)
    logger.info("collected %d trainable samples", len(samples))

    if len(samples) < 100:
        logger.error("too few samples (%d) — aborting", len(samples))
        return 3

    model, best_f1, val = train_model(
        samples, epochs=args.epochs, batch_size=args.batch_size,
        lr=args.lr, val_frac=args.val_frac, device=device,
    )
    logger.info("training done. best val f1 = %.3f", best_f1)

    # Always save the PyTorch checkpoint first — ONNX export can fail on
    # missing onnxscript or opset issues; we don't want to lose 30s+ of
    # training on a packaging glitch.
    if args.out_onnx:
        out_onnx = Path(args.out_onnx)
        out_onnx.parent.mkdir(parents=True, exist_ok=True)
        ckpt_path = out_onnx.with_suffix(".pt")
        torch.save(
            {"state_dict": model.state_dict(), "best_f1": best_f1,
             "feature_dim": FEATURE_DIM, "bpb_classes": list(BPB_CLASSES)},
            str(ckpt_path),
        )
        logger.info("checkpoint saved to %s", ckpt_path)
        try:
            export_onnx(model.cpu(), out_onnx)
        except Exception as e:
            logger.error("ONNX export failed: %s", e)
            logger.error("PyTorch checkpoint at %s is still usable for re-export.", ckpt_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")

    # legacy --dry-run / --train flags via top-level for compat
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-torch", action="store_true")
    ap.add_argument("--stats", action="store_true",
                    help="Stats-only: count what would be yielded, no training")
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--export-only", action="store_true",
                    help="Re-export ONNX from existing .pt checkpoint (no training)")

    ap.add_argument("--root", type=Path, default=_REPO / "data" / "chords")
    ap.add_argument("--max-cv", type=float, default=0.15)
    ap.add_argument("--skip-recent-sec", type=float, default=600.0)
    ap.add_argument("--max-samples", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--out-onnx", type=Path,
                    default=_REPO / "data" / "models" / "bar_arbitrator_v1.onnx")
    args = ap.parse_args()

    if args.dry_run:
        return cmd_dry_run(no_torch=args.no_torch)
    if args.stats:
        return cmd_stats(args.root, args.max_cv, args.skip_recent_sec, args.max_samples)
    if args.train:
        return cmd_train(args)
    if args.export_only:
        return cmd_export_only(args)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
