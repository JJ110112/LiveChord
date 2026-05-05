"""
Shared chroma-CQT feature extraction used by both the audio index builder
(build_chroma_index.py) and the MIDI reverse identifier (identify_midi.py).

Keeping this in one module guarantees the query-side chroma (MIDI synth)
and reference-side chroma (audio files in V:/Database/Chroma_Index) are
computed with the exact same parameters — otherwise DTW distances are
meaningless.
"""
from __future__ import annotations

import hashlib
import os
from typing import Tuple

import numpy as np

SR = 22050
HOP = 512
TIME_TARGET_HZ = 2.0
FRAMES_PER_SEC_CQT = SR / HOP
DECIMATE = max(1, int(FRAMES_PER_SEC_CQT / TIME_TARGET_HZ))  # = 21


_UNIFORM_12 = np.full((12,), 1.0 / np.sqrt(12.0), dtype=np.float32)


def sanitize_chroma(chroma_n: np.ndarray) -> np.ndarray:
    """Replace silence/zero-magnitude frames with a uniform unit chroma.

    The L2-normalize step in extract_chroma uses a clamp=1e-6 fallback, but
    an all-zero frame (silence between MIDI notes, or pitch-tracker
    dropout) stays all-zero after the divide — and cosine distance on a
    zero vector is NaN, which librosa.sequence.dtw rejects with
    'DTW cost matrix C has NaN values'. Substituting uniform 1/sqrt(12)
    keeps the frame unit-norm and contributes a neutral cost instead.
    """
    norms = np.linalg.norm(chroma_n, axis=0)
    bad = norms < 1e-3
    if bad.any():
        chroma_n = chroma_n.copy()
        chroma_n[:, bad] = _UNIFORM_12[:, None]
    return chroma_n


def extract_chroma(y: np.ndarray, sr: int = SR) -> Tuple[np.ndarray, np.ndarray]:
    """Compute normalized chroma-CQT + 24-dim statistical signature.

    Returns:
        chroma_n: (12, F) float32, L2-normalized per frame, decimated to ~2 fps.
                  Zero-magnitude frames substituted with uniform chroma.
        sig:      (24,) float32, concat of per-pitch-class mean and std
    """
    import librosa
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
    if chroma.shape[1] > DECIMATE * 2:
        trim = (chroma.shape[1] // DECIMATE) * DECIMATE
        chroma = chroma[:, :trim].reshape(12, -1, DECIMATE).mean(axis=2)
    chroma = chroma.astype(np.float32)
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    chroma_n = chroma / np.clip(norms, 1e-6, None)
    chroma_n = sanitize_chroma(chroma_n)
    sig = np.concatenate([chroma_n.mean(axis=1), chroma_n.std(axis=1)]).astype(np.float32)
    return chroma_n, sig


def file_hash(path: str, head_bytes: int = 64 * 1024) -> str:
    """MD5 of first 64KB concatenated with file size → 16-char hex.

    Same scheme as build_chroma_index.audio_hash; used for both .flac and .mid
    so the identifier's resume logic keys stably across renames.
    """
    h = hashlib.md5()
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        h.update(f.read(head_bytes))
    h.update(str(size).encode())
    return h.hexdigest()[:16]
