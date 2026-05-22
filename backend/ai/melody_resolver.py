"""Resolver v0 for selecting RH melody candidates without generating them."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, Mapping

from .melody_candidate import FULL_MIX_PYIN, VOCAL_STEM_CREPE, read_candidate_cache, selected_path
from .melody_schema import finalize_melody_payload
from .song_type_audio_features import cached_stem_energy_features
from .song_type_vocal_gate import VOCAL_GATE_VERSION, classify_vocal_gate


RESOLVER_VERSION = "rhmelody-resolver-v0"
RETREAT_LOW_COVERAGE_FLAG = "vocal_candidate_retreat_low_coverage"
RESOLVER_FALLBACK_FLAG = "resolver_fallback_full_mix"
DEFAULT_MIN_COVERAGE_RATIO = 0.30


def resolver_enabled() -> bool:
    """Return whether the source-aware RH melody resolver may affect output."""

    raw = os.environ.get("LIVECHORD_RH_MELODY_RESOLVER", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        from config import is_public_mode

        if is_public_mode():
            return False
    except Exception:
        pass
    return True


class MelodyResolver:
    """Read-only candidate selector with a conservative full-mix fallback."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        min_coverage_ratio: float = DEFAULT_MIN_COVERAGE_RATIO,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.min_coverage_ratio = float(min_coverage_ratio)

    def resolve(
        self,
        baseline_payload: Mapping[str, Any],
        *,
        song_hash: str,
        path: str = "",
    ) -> Dict[str, Any]:
        """Return the selected melody payload.

        v0 only promotes `vocal_stem_crepe` when all prerequisites are already
        cached and the vocal gate passes. It never runs Demucs, CREPE, or pYIN.
        """

        baseline = copy.deepcopy(dict(baseline_payload or {}))
        if not song_hash:
            return baseline

        candidate = read_candidate_cache(self.data_dir, song_hash, VOCAL_STEM_CREPE)
        if not candidate:
            selected = self._fallback(
                baseline,
                gate={
                    "predict_vocal": False,
                    "reason": "vocal_candidate_missing",
                    "song_type_source": VOCAL_GATE_VERSION,
                },
                reason="vocal_candidate_missing",
            )
            self._write_selected_cache(song_hash, selected)
            return selected

        gate = self._vocal_gate(song_hash)
        if gate.get("predict_vocal"):
            selected = self._maybe_select_vocal(
                baseline,
                candidate,
                song_hash=song_hash,
                path=path,
                gate=gate,
            )
        else:
            reason = str(gate.get("reason") or "vocal_gate_failed")
            selected = self._fallback(
                baseline,
                gate=gate,
                reason=reason,
            )
        self._write_selected_cache(song_hash, selected)
        return selected

    def _vocal_gate(self, song_hash: str) -> Dict[str, Any]:
        stem_features = cached_stem_energy_features(self.data_dir, song_hash)
        row = {
            "duration_s": stem_features.get("stem_analyzed_duration_s"),
            "stems": stem_features,
        }
        gate = classify_vocal_gate(row)
        gate["stem_status"] = stem_features.get("stem_status")
        gate["missing_stems"] = stem_features.get("missing_stems", [])
        return gate

    def _maybe_select_vocal(
        self,
        baseline: Dict[str, Any],
        candidate: Mapping[str, Any],
        *,
        song_hash: str,
        path: str,
        gate: Mapping[str, Any],
    ) -> Dict[str, Any]:
        baseline_stats = baseline.get("melody_stats") if isinstance(baseline.get("melody_stats"), dict) else {}
        candidate_stats = candidate.get("melody_stats") if isinstance(candidate.get("melody_stats"), dict) else {}
        if self._low_coverage(candidate_stats, baseline_stats):
            return self._fallback(
                baseline,
                gate=gate,
                reason=RETREAT_LOW_COVERAGE_FLAG,
                extra_flags=[RETREAT_LOW_COVERAGE_FLAG],
            )

        selected = copy.deepcopy(dict(candidate))
        source = selected.get("melody_source") if isinstance(selected.get("melody_source"), dict) else {}
        source = dict(source)
        source.update({
            "id": VOCAL_STEM_CREPE,
            "song_type": "vocal_led",
            "song_type_confidence": gate.get("song_type_confidence"),
            "song_type_source": gate.get("song_type_source") or VOCAL_GATE_VERSION,
            "selected_by": RESOLVER_VERSION,
            "resolver_version": RESOLVER_VERSION,
            "resolver_gate": self._gate_metadata(gate),
        })
        selected["melody_source"] = source
        flags = self._flags(selected.get("quality_flags"))
        if "resolver_selected_vocal_stem_crepe" not in flags:
            flags.append("resolver_selected_vocal_stem_crepe")
        selected["quality_flags"] = flags
        return self._finalize(selected, path=path or str(selected.get("path") or ""), song_hash=song_hash)

    def _fallback(
        self,
        baseline: Dict[str, Any],
        *,
        gate: Mapping[str, Any],
        reason: str,
        extra_flags: list[str] | None = None,
    ) -> Dict[str, Any]:
        selected = copy.deepcopy(baseline)
        source = selected.get("melody_source") if isinstance(selected.get("melody_source"), dict) else {}
        source = dict(source)
        source.update({
            "id": FULL_MIX_PYIN,
            "selected_by": "fallback",
            "resolver_version": RESOLVER_VERSION,
            "fallback_reason": reason,
            "song_type_source": gate.get("song_type_source") or VOCAL_GATE_VERSION,
            "resolver_gate": self._gate_metadata(gate),
        })
        selected["melody_source"] = source
        flags = self._flags(selected.get("quality_flags"))
        for flag in [RESOLVER_FALLBACK_FLAG, *(extra_flags or [])]:
            if flag not in flags:
                flags.append(flag)
        selected["quality_flags"] = flags
        return selected

    def _low_coverage(self, candidate_stats: Mapping[str, Any], baseline_stats: Mapping[str, Any]) -> bool:
        for key in ("active_duration_s", "density_when_active_per_s"):
            baseline_value = _optional_float(baseline_stats.get(key))
            candidate_value = _optional_float(candidate_stats.get(key))
            if baseline_value is None or candidate_value is None or baseline_value <= 0:
                continue
            if candidate_value < baseline_value * self.min_coverage_ratio:
                return True
        return False

    def _write_selected_cache(self, song_hash: str, payload: Mapping[str, Any]) -> None:
        try:
            from .melody_schema import atomic_write_json

            out = selected_path(self.data_dir, song_hash)
            out.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(out, dict(payload))
        except Exception:
            pass

    def _finalize(self, payload: Mapping[str, Any], *, path: str, song_hash: str) -> Dict[str, Any]:
        context = self._context(song_hash)
        return finalize_melody_payload(
            dict(payload),
            path=path,
            bpm=context["bpm"],
            tempo_curve=context["tempo_curve"],
            time_signature=context["time_signature"],
        )

    def _context(self, song_hash: str) -> Dict[str, Any]:
        try:
            from .melody_schema import melody_context_from_chord_cache

            return melody_context_from_chord_cache(song_hash)
        except Exception:
            return {"bpm": 120.0, "tempo_curve": None, "time_signature": "4/4"}

    def _gate_metadata(self, gate: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "version": gate.get("song_type_source") or VOCAL_GATE_VERSION,
            "predict_vocal": bool(gate.get("predict_vocal")),
            "reason": str(gate.get("reason") or ""),
            "vocal_stem_energy_ratio": gate.get("vocal_stem_energy_ratio"),
            "duration_s": gate.get("duration_s"),
            "stem_status": gate.get("stem_status"),
            "missing_stems": list(gate.get("missing_stems") or []),
        }

    def _flags(self, existing: Any) -> list[str]:
        if isinstance(existing, list):
            return [str(flag) for flag in existing if flag]
        if isinstance(existing, str) and existing:
            return [existing]
        return []


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
