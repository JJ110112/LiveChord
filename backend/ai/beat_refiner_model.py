"""Compact Transformer encoder for the beat_refiner — predicts per-frame
beat / downbeat / chord-boundary logits given audio features + the
existing tracker's beat grid.

Architecture (Phase 1 baseline)
-------------------------------
Input  (B, T, 100)            ← N_MODEL_CHANNELS from beat_refiner_features
  └─ Linear(100, 256)         input projection
  └─ + sinusoidal pos enc     additive, computed on the fly (any T)
  └─ TransformerEncoderLayer × 6
        d_model=256, nhead=4, ffn=512, dropout=0.1, GELU, pre-LN
  └─ three Linear(256, 1) heads (sharing the encoder trunk):
        beat_logits           per-frame BCE target = beat_label
        downbeat_logits       per-frame BCE target = downbeat_label
        chord_boundary_logits per-frame BCE target = chord_boundary_label

≈ 3.0 M parameters. Trains in a few hours on an RTX 5080 with chunked
60-second segments.

Why these choices
-----------------
- **Three sigmoid heads, no softmax**: a frame can be both a beat AND a
  downbeat AND a chord change. They're not mutually exclusive labels.
- **Shared trunk**: lets the chord_boundary head use percussive cues the
  beat head learns and vice versa — the whole point of multi-task.
- **No causal mask**: refinement is offline / batch, so we have full
  bidirectional context.
- **Sinusoidal pos enc, not learned**: songs vary in length up to 9,693
  frames (15 min at 92.9 ms / frame). Learned positional embeddings
  would need fixed max length and don't generalize to longer test songs.
- **GELU + pre-LN**: standard for stable training of small Transformers
  on long sequences. Mirrors the existing ``bar_arbitrator`` style
  (``activation="gelu"`` in scripts/train_bar_arbitrator.py).
- **batch_first=True**: cleaner indexing in trainer/exporter; required
  for clean ONNX export with dynamic axes.

Padding mask convention
-----------------------
``padding_mask`` is shape ``(B, T)`` with ``True`` at PADDING positions
(matches PyTorch's ``src_key_padding_mask`` convention). The trainer
constructs it when batching variable-length songs.

ONNX export friendliness
------------------------
- All ops are stock ``torch.nn`` modules; no custom autograd.
- Sinusoidal pos enc precomputed up to ``MAX_FRAMES`` and indexed —
  ONNX dynamic shapes only need to cover length, not pos-enc generation.
- Outputs are three named tensors so the inference wrapper can read
  them by name.

Why not Conformer (deferred)
----------------------------
A Conformer block (MHSA + conv + FFN) gives stronger local inductive
bias for percussive transients. We get away without it for v1 because:
  - The input already includes onset_strength + RMS, which carry
    transient info explicitly.
  - 6-layer attention can learn local kernels approximately when given
    enough data (we have 13,017 beat-trainable songs).
  - Conformer adds ~50% to train/inference cost and complicates ONNX
    export. Worth it only if v1 hits a quality ceiling traceable to
    weak local feature integration.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import torch
import torch.nn as nn

from backend.ai.beat_refiner_features import N_MODEL_CHANNELS

# ---------------------------------------------------------------------------
# Hyperparameters — frozen here so trainer + exporter + inference all
# agree on the architecture exactly. Bumping any of these requires a
# new MODEL_VERSION + retraining.
# ---------------------------------------------------------------------------

MODEL_VERSION = 1

D_MODEL = 256
N_HEADS = 4
N_LAYERS = 6
FFN_DIM = 512
DROPOUT = 0.1

# Pos-enc table size — covers MAX_AUDIO_SECONDS (900s) at 10.766 fps.
# Tiny memory cost (256 floats × 9700 ≈ 9.5 MB), avoids generating
# pos-enc on the fly inside the forward (which complicates ONNX).
MAX_FRAMES = 9728  # next multiple of 64 above 9700 ≈ 9.5 GB train-batch headroom


# ---------------------------------------------------------------------------
# Sinusoidal positional encoding
# ---------------------------------------------------------------------------

def _build_sinusoidal_pos_enc(max_len: int, d_model: int) -> torch.Tensor:
    """Standard ``Attention Is All You Need`` sinusoidal table.

    Returns a ``(max_len, d_model)`` tensor; caller indexes by length.
    Computed once at module-import time and registered as a buffer.
    """
    pe = torch.zeros(max_len, d_model)
    position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
    div_term = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32)
                         * (-math.log(10000.0) / d_model))
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class BeatRefiner(nn.Module):
    """Bidirectional Transformer encoder with three per-frame BCE heads.

    Input
    -----
    x              float32 (B, T, N_MODEL_CHANNELS)  audio + initial-grid features
    padding_mask   bool    (B, T)                    True at padding positions

    Output
    ------
    dict with three logits tensors of shape (B, T):
      beat_logits
      downbeat_logits
      chord_boundary_logits

    Apply ``sigmoid`` outside the model — keeping logits in the output
    lets the trainer use ``binary_cross_entropy_with_logits`` (numerically
    stabler than separate sigmoid + BCE).
    """

    def __init__(self,
                 n_input_channels: int = N_MODEL_CHANNELS,
                 d_model: int = D_MODEL,
                 n_heads: int = N_HEADS,
                 n_layers: int = N_LAYERS,
                 ffn_dim: int = FFN_DIM,
                 dropout: float = DROPOUT,
                 max_frames: int = MAX_FRAMES):
        super().__init__()
        self.d_model = d_model
        self.max_frames = max_frames

        self.input_proj = nn.Linear(n_input_channels, d_model)

        # Sinusoidal pos enc as a fixed (non-learned) buffer
        pe = _build_sinusoidal_pos_enc(max_frames, d_model)
        self.register_buffer("pos_enc", pe, persistent=False)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=ffn_dim,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,  # pre-LN — more stable for small models
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.encoder_norm = nn.LayerNorm(d_model)

        # Three independent linear heads on the shared trunk.
        self.beat_head = nn.Linear(d_model, 1)
        self.downbeat_head = nn.Linear(d_model, 1)
        self.chord_boundary_head = nn.Linear(d_model, 1)

        self._init_weights()

    def _init_weights(self):
        """Xavier for projections, zero biases. Keeps the initial logits
        near 0 → sigmoid ≈ 0.5 so BCE doesn't immediately collapse."""
        for m in (self.input_proj, self.beat_head, self.downbeat_head,
                  self.chord_boundary_head):
            nn.init.xavier_uniform_(m.weight)
            nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor,
                padding_mask: Optional[torch.Tensor] = None
                ) -> Dict[str, torch.Tensor]:
        B, T, C = x.shape
        if T > self.max_frames:
            raise ValueError(
                f"Input has {T} frames; max_frames={self.max_frames}. "
                f"Trainer must chunk longer songs."
            )

        # Project to d_model and add positional encoding.
        h = self.input_proj(x)                     # (B, T, d_model)
        h = h + self.pos_enc[:T].unsqueeze(0)      # broadcast over batch

        # Encoder. ``src_key_padding_mask`` expects True at padded positions.
        h = self.encoder(h, src_key_padding_mask=padding_mask)
        h = self.encoder_norm(h)

        # Heads — squeeze the trailing 1-dim for clean (B, T) logits.
        return {
            "beat_logits": self.beat_head(h).squeeze(-1),
            "downbeat_logits": self.downbeat_head(h).squeeze(-1),
            "chord_boundary_logits": self.chord_boundary_head(h).squeeze(-1),
        }


# ---------------------------------------------------------------------------
# Convenience constructors / inspection helpers
# ---------------------------------------------------------------------------

def build_default_model() -> BeatRefiner:
    """Construct the canonical v1 architecture. Trainer + exporter both
    call this so they cannot drift."""
    return BeatRefiner()


def count_parameters(model: nn.Module) -> int:
    """Total trainable parameter count — handy for spec-sheet logging."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


# ---------------------------------------------------------------------------
# Smoke test — ``python -m backend.ai.beat_refiner_model``
# ---------------------------------------------------------------------------

def _smoke_test():
    """Forward + backward on a small synthetic batch, print param count
    and output shapes. No real data needed; verifies the module is wired."""
    torch.manual_seed(0)
    model = build_default_model()
    n = count_parameters(model)
    print(f"BeatRefiner v{MODEL_VERSION}")
    print(f"  d_model={D_MODEL}, n_heads={N_HEADS}, n_layers={N_LAYERS}, ffn={FFN_DIM}")
    print(f"  parameters: {n:,}  ({n / 1e6:.2f} M)")
    print(f"  input channels: {N_MODEL_CHANNELS}")
    print(f"  max_frames: {MAX_FRAMES}")

    # Tiny batch: B=2, T=200, varying lengths via padding mask.
    B, T = 2, 200
    x = torch.randn(B, T, N_MODEL_CHANNELS)
    pad = torch.zeros(B, T, dtype=torch.bool)
    pad[1, 150:] = True  # second sample is only 150 frames long

    model.eval()
    with torch.no_grad():
        out = model(x, padding_mask=pad)
    print(f"  forward ok | beat_logits {tuple(out['beat_logits'].shape)} "
          f"downbeat_logits {tuple(out['downbeat_logits'].shape)} "
          f"chord_boundary_logits {tuple(out['chord_boundary_logits'].shape)}")

    # Gradient sanity — backward must not raise on padded inputs.
    model.train()
    out = model(x, padding_mask=pad)
    loss = (out["beat_logits"].mean()
            + out["downbeat_logits"].mean()
            + out["chord_boundary_logits"].mean())
    loss.backward()
    has_grad = any(p.grad is not None and p.grad.abs().sum() > 0
                   for p in model.parameters())
    print(f"  backward ok | gradients flow: {has_grad}")


if __name__ == "__main__":
    _smoke_test()
