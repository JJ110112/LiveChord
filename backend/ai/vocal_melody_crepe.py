"""HTDemucs vocal-stem -> CREPE shadow melody candidate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import librosa
import numpy as np
from scipy.ndimage import median_filter

from .melody_candidate import (
    VOCAL_STEM_CREPE,
    build_candidate_payload,
    write_candidate_cache,
)


@dataclass
class VocalCrepeResult:
    ok: bool
    payload: Optional[Dict[str, Any]] = None
    cache_file: str = ""
    error: str = ""


class VocalStemCrepeExtractor:
    """Build `vocal_stem_crepe` from a precomputed vocals.wav stem."""

    def __init__(
        self,
        data_dir: str | Path = Path(r"V:\data"),
        sr: int = 16000,
        hop_length: int = 160,
        confidence_threshold: float = 0.5,
        min_note_dur: float = 0.08,
        gap_merge: float = 0.12,
    ):
        self.data_dir = Path(data_dir)
        self.sr = sr
        self.hop_length = hop_length
        self.confidence_threshold = confidence_threshold
        self.min_note_dur = min_note_dur
        self.gap_merge = gap_merge

    def extract_to_cache(
        self,
        *,
        song_hash: str,
        path: str,
        vocal_stem_path: str,
        bpm: float = 120.0,
        tempo_curve: Optional[List[Dict[str, Any]]] = None,
        time_signature: str = "4/4",
        model: str = "full",
    ) -> VocalCrepeResult:
        stem = Path(vocal_stem_path)
        if not stem.is_file():
            return VocalCrepeResult(ok=False, error="vocal_stem_not_found")
        try:
            events, build_info = self._extract_events(stem, model=model)
        except ImportError as exc:
            return VocalCrepeResult(ok=False, error=f"missing_dependency:{exc.name}")
        except Exception as exc:
            return VocalCrepeResult(ok=False, error=f"extraction_failed:{type(exc).__name__}:{exc}")

        flags = []
        if not events:
            flags.append("empty_vocal_stem_crepe")
        payload = build_candidate_payload(
            song_hash=song_hash,
            path=path,
            candidate_id=VOCAL_STEM_CREPE,
            melody=events,
            stem="vocals",
            algorithm=f"htdemucs.vocals+torchcrepe.{model}",
            quality_flags=flags,
            bpm=bpm,
            tempo_curve=tempo_curve,
            time_signature=time_signature,
            song_type="vocal_pop",
            build_info=build_info,
        )
        cache_file = write_candidate_cache(self.data_dir, song_hash, VOCAL_STEM_CREPE, payload)
        return VocalCrepeResult(ok=True, payload=payload, cache_file=str(cache_file))

    def _extract_events(self, stem: Path, *, model: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        import torch
        import torchcrepe

        y, _ = librosa.load(str(stem), sr=self.sr, mono=True)
        if y.size == 0:
            return [], {"sample_rate": self.sr, "hop_length": self.hop_length, "reason": "empty_audio"}

        audio = torch.tensor(y, dtype=torch.float32).unsqueeze(0)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        batch_size = 1024 if device == "cuda" else 32
        audio = audio.to(device)
        frequency, periodicity = torchcrepe.predict(
            audio,
            self.sr,
            self.hop_length,
            50.0,
            1100.0,
            model,
            batch_size=batch_size,
            device=device,
            return_periodicity=True,
        )
        f0 = frequency.squeeze(0).detach().cpu().numpy()
        confidence = periodicity.squeeze(0).detach().cpu().numpy()
        times = np.arange(len(f0), dtype=float) * (self.hop_length / float(self.sr))
        voiced = confidence >= self.confidence_threshold
        stable_f0 = self._median_vibrato_filter(f0, voiced)
        events = self._segment(times, stable_f0, voiced, confidence)
        return events, {
            "sample_rate": self.sr,
            "hop_length": self.hop_length,
            "confidence_threshold": self.confidence_threshold,
            "model": model,
            "device": device,
            "batch_size": batch_size,
            "frames": int(len(f0)),
        }

    def _median_vibrato_filter(self, f0: np.ndarray, voiced: np.ndarray) -> np.ndarray:
        out = f0.copy()
        mask = voiced & np.isfinite(out) & (out > 0)
        if int(mask.sum()) < 5:
            return out
        midi = np.full_like(out, np.nan, dtype=float)
        midi[mask] = librosa.hz_to_midi(out[mask])
        filled = midi.copy()
        for i in range(1, len(filled)):
            if np.isnan(filled[i]) and not np.isnan(filled[i - 1]):
                filled[i] = filled[i - 1]
        for i in range(len(filled) - 2, -1, -1):
            if np.isnan(filled[i]) and not np.isnan(filled[i + 1]):
                filled[i] = filled[i + 1]
        if not np.all(np.isnan(filled)):
            filtered = median_filter(filled, size=5)
            out[mask] = librosa.midi_to_hz(filtered[mask])
        return out

    def _segment(
        self,
        times: np.ndarray,
        f0: np.ndarray,
        voiced: np.ndarray,
        confidence: np.ndarray,
    ) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        current_midi = None
        start = 0.0
        confs: List[float] = []
        for idx, (t, freq, is_voiced) in enumerate(zip(times, f0, voiced)):
            if is_voiced and np.isfinite(freq) and freq > 0:
                midi = int(round(float(librosa.hz_to_midi(freq))))
                if current_midi is None:
                    current_midi = midi
                    start = float(t)
                    confs = [float(confidence[idx])]
                elif midi != current_midi:
                    self._append_event(events, start, float(t), current_midi, confs)
                    current_midi = midi
                    start = float(t)
                    confs = [float(confidence[idx])]
                else:
                    confs.append(float(confidence[idx]))
            elif current_midi is not None:
                self._append_event(events, start, float(t), current_midi, confs)
                current_midi = None
                confs = []
        if current_midi is not None:
            end = float(times[-1]) + self.hop_length / float(self.sr)
            self._append_event(events, start, end, current_midi, confs)
        return self._merge_gaps(events)

    def _append_event(self, events: List[Dict[str, Any]], start: float, end: float, midi: int, confs: List[float]) -> None:
        if end - start < self.min_note_dur:
            return
        events.append({
            "start": round(start, 6),
            "end": round(end, 6),
            "midi": midi,
            "confidence": round(float(np.mean(confs)) if confs else 0.0, 4),
        })

    def _merge_gaps(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for event in events:
            if merged and event["midi"] == merged[-1]["midi"] and event["start"] - merged[-1]["end"] <= self.gap_merge:
                merged[-1]["end"] = event["end"]
                merged[-1]["confidence"] = round((merged[-1]["confidence"] + event["confidence"]) / 2.0, 4)
            else:
                merged.append(dict(event))
        return merged
