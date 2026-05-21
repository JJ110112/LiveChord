"""One-song RH melody shadow candidate generation."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .melody_candidate import (
    FULL_MIX_PYIN,
    SOLO_PIANO_POLYPHONIC,
    VOCAL_STEM_CREPE,
    build_candidate_payload,
    candidate_dir,
    candidate_path,
    read_candidate_cache,
    write_candidate_cache,
)
from .melody_extractor import MelodyExtractor
from .piano_rh_selector import select_right_hand_melody
from .stem_separation import StemCache, StemCacheResult
from .vocal_melody_crepe import VocalStemCrepeExtractor


DEFAULT_DATA_DIR = Path(r"V:\data")


@dataclass
class ShadowCandidateResult:
    candidate_id: str
    ok: bool
    status: str
    cache_file: str = ""
    error: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ShadowGenerationResult:
    ok: bool
    song_hash: str
    path: str
    audio_path: str
    results: List[ShadowCandidateResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "song_hash": self.song_hash,
            "path": self.path,
            "audio_path": self.audio_path,
            "results": [
                {
                    "candidate_id": r.candidate_id,
                    "ok": r.ok,
                    "status": r.status,
                    "cache_file": r.cache_file,
                    "error": r.error,
                    "details": r.details,
                }
                for r in self.results
            ],
        }


def generate_shadow_candidates(
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    song_hash: str = "",
    path: str = "",
    audio_path: str = "",
    candidates: Sequence[str] = (FULL_MIX_PYIN,),
    polyphonic_json: str = "",
    polyphonic_midi: str = "",
    key: Optional[str] = None,
    force: bool = False,
    resolve_audio_path: Optional[Callable[[str], str]] = None,
    melody_extractor_factory: Optional[Callable[[], MelodyExtractor]] = None,
    stem_cache: Optional[StemCache] = None,
    vocal_extractor: Optional[VocalStemCrepeExtractor] = None,
) -> ShadowGenerationResult:
    """Generate selected shadow candidates for a single song.

    This function never writes the legacy `melodies/` cache or the selected
    `melodies_rh_v2/` cache. It only writes shadow candidate files.
    """

    root = Path(data_dir)
    resolved_hash, resolved_path = _resolve_identity(root, song_hash=song_hash, path=path)
    resolved_audio = audio_path or _resolve_audio(resolved_path, resolve_audio_path)
    context = _melody_context(root, resolved_hash)
    results: List[ShadowCandidateResult] = []
    requested = list(candidates or (FULL_MIX_PYIN,))
    for candidate_id in requested:
        if candidate_id == FULL_MIX_PYIN:
            results.append(_generate_full_mix_pyin(
                data_dir=root,
                song_hash=resolved_hash,
                path=resolved_path,
                audio_path=resolved_audio,
                context=context,
                force=force,
                melody_extractor_factory=melody_extractor_factory,
            ))
        elif candidate_id == VOCAL_STEM_CREPE:
            results.append(_generate_vocal_stem_crepe(
                data_dir=root,
                song_hash=resolved_hash,
                path=resolved_path,
                audio_path=resolved_audio,
                context=context,
                force=force,
                stem_cache=stem_cache,
                vocal_extractor=vocal_extractor,
            ))
        elif candidate_id == SOLO_PIANO_POLYPHONIC:
            results.append(_generate_solo_piano_polyphonic(
                data_dir=root,
                song_hash=resolved_hash,
                path=resolved_path,
                context=context,
                key=key,
                polyphonic_json=polyphonic_json,
                polyphonic_midi=polyphonic_midi,
                force=force,
            ))
        else:
            results.append(ShadowCandidateResult(
                candidate_id=str(candidate_id),
                ok=False,
                status="unsupported_candidate",
                error="unsupported_candidate",
            ))
    return ShadowGenerationResult(
        ok=all(r.ok for r in results) if results else False,
        song_hash=resolved_hash,
        path=resolved_path,
        audio_path=resolved_audio,
        results=results,
    )


def _generate_full_mix_pyin(
    *,
    data_dir: Path,
    song_hash: str,
    path: str,
    audio_path: str,
    context: Dict[str, Any],
    force: bool,
    melody_extractor_factory: Optional[Callable[[], MelodyExtractor]],
) -> ShadowCandidateResult:
    out = candidate_path(data_dir, song_hash, FULL_MIX_PYIN)
    if out.is_file() and not force:
        return ShadowCandidateResult(FULL_MIX_PYIN, True, "cached", str(out))

    melody = _read_legacy_melody(data_dir, song_hash)
    source = "legacy_cache"
    if melody is None:
        audio = Path(audio_path)
        if not audio.is_file():
            return ShadowCandidateResult(FULL_MIX_PYIN, False, "failed", error="audio_not_found")
        extractor = melody_extractor_factory() if melody_extractor_factory else MelodyExtractor()
        try:
            melody = extractor.extract_melody(
                str(audio),
                bpm=float(context.get("bpm") or 120.0),
                tempo_curve=context.get("tempo_curve"),
                time_signature=str(context.get("time_signature") or "4/4"),
            )
            source = "fresh_extraction"
        except Exception as exc:
            return ShadowCandidateResult(
                FULL_MIX_PYIN,
                False,
                "failed",
                error=f"extraction_failed:{type(exc).__name__}:{exc}",
            )

    payload = build_candidate_payload(
        song_hash=song_hash,
        path=path,
        candidate_id=FULL_MIX_PYIN,
        melody=melody or [],
        stem="full_mix",
        algorithm="librosa.pyin",
        quality_flags=["shadow_candidate"],
        bpm=float(context.get("bpm") or 120.0),
        tempo_curve=context.get("tempo_curve"),
        time_signature=str(context.get("time_signature") or "4/4"),
        song_type="unknown",
        build_info={"source": source},
    )
    cache_file = write_candidate_cache(data_dir, song_hash, FULL_MIX_PYIN, payload)
    return ShadowCandidateResult(FULL_MIX_PYIN, True, "generated", str(cache_file), details={"source": source})


def _generate_vocal_stem_crepe(
    *,
    data_dir: Path,
    song_hash: str,
    path: str,
    audio_path: str,
    context: Dict[str, Any],
    force: bool,
    stem_cache: Optional[StemCache],
    vocal_extractor: Optional[VocalStemCrepeExtractor],
) -> ShadowCandidateResult:
    out = candidate_path(data_dir, song_hash, VOCAL_STEM_CREPE)
    if out.is_file() and not force:
        return ShadowCandidateResult(VOCAL_STEM_CREPE, True, "cached", str(out))

    cache = stem_cache or StemCache(data_dir)
    stems = cache.ensure_stems(song_hash=song_hash, audio_path=audio_path, force=force)
    if not stems.ok:
        return ShadowCandidateResult(
            VOCAL_STEM_CREPE,
            False,
            "failed",
            error=f"stem_cache:{stems.error}",
            details={"stems": stems.stems, "cache_dir": stems.cache_dir, "reused": stems.reused},
        )
    vocals = stems.stems.get("vocals", "")
    extractor = vocal_extractor or VocalStemCrepeExtractor(data_dir=data_dir)
    result = extractor.extract_to_cache(
        song_hash=song_hash,
        path=path,
        vocal_stem_path=vocals,
        bpm=float(context.get("bpm") or 120.0),
        tempo_curve=context.get("tempo_curve"),
        time_signature=str(context.get("time_signature") or "4/4"),
    )
    if not result.ok:
        return ShadowCandidateResult(
            VOCAL_STEM_CREPE,
            False,
            "failed",
            error=result.error,
            details={"stems": stems.stems, "cache_dir": stems.cache_dir, "reused": stems.reused},
        )
    return ShadowCandidateResult(
        VOCAL_STEM_CREPE,
        True,
        "generated",
        result.cache_file,
        details={"stems": stems.stems, "cache_dir": stems.cache_dir, "reused": stems.reused},
    )


def _generate_solo_piano_polyphonic(
    *,
    data_dir: Path,
    song_hash: str,
    path: str,
    context: Dict[str, Any],
    key: Optional[str],
    polyphonic_json: str,
    polyphonic_midi: str,
    force: bool,
) -> ShadowCandidateResult:
    out = candidate_path(data_dir, song_hash, SOLO_PIANO_POLYPHONIC)
    if out.is_file() and not force:
        return ShadowCandidateResult(SOLO_PIANO_POLYPHONIC, True, "cached", str(out))
    try:
        notes = _load_polyphonic_notes(polyphonic_json=polyphonic_json, polyphonic_midi=polyphonic_midi)
    except Exception as exc:
        return ShadowCandidateResult(
            SOLO_PIANO_POLYPHONIC,
            False,
            "polyphonic_load_failed",
            error=f"polyphonic_load_failed:{type(exc).__name__}:{exc}",
        )
    resolved_key = key if key else str(context.get("key") or "C")
    selected = select_right_hand_melody(
        notes,
        key=resolved_key,
        bpm=float(context.get("bpm") or 120.0),
        tempo_curve=context.get("tempo_curve"),
        time_signature=str(context.get("time_signature") or "4/4"),
    )
    if polyphonic_midi:
        debug_mid = candidate_dir(data_dir, song_hash) / "solo_piano_polyphonic_full.mid"
        debug_mid.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(polyphonic_midi, debug_mid)
    payload = build_candidate_payload(
        song_hash=song_hash,
        path=path,
        candidate_id=SOLO_PIANO_POLYPHONIC,
        melody=selected,
        stem="polyphonic_midi",
        algorithm="magenta.onsets_frames+skyline_temperley",
        quality_flags=["shadow_candidate"] + ([] if selected else ["empty_solo_piano_polyphonic"]),
        bpm=float(context.get("bpm") or 120.0),
        tempo_curve=context.get("tempo_curve"),
        time_signature=str(context.get("time_signature") or "4/4"),
        song_type="solo_piano",
        build_info={
            "polyphonic_json": polyphonic_json or "",
            "polyphonic_midi": polyphonic_midi or "",
            "key": resolved_key,
            "input_notes": len(notes),
        },
    )
    cache_file = write_candidate_cache(data_dir, song_hash, SOLO_PIANO_POLYPHONIC, payload)
    return ShadowCandidateResult(
        SOLO_PIANO_POLYPHONIC,
        True,
        "generated",
        str(cache_file),
        details={"input_notes": len(notes), "selected_notes": len(selected)},
    )


def _resolve_identity(data_dir: Path, *, song_hash: str, path: str) -> tuple[str, str]:
    if path and not song_hash:
        from chord_cache import song_hash as make_song_hash
        return make_song_hash(path), path
    if song_hash and path:
        return song_hash, path
    if song_hash:
        chord_file = _chord_file_for(data_dir, song_hash)
        if chord_file.is_file():
            try:
                data = json.loads(chord_file.read_text(encoding="utf-8"))
                chord_path = str(data.get("path") or data.get("rel_path") or "")
                return song_hash, chord_path
            except Exception:
                pass
        return song_hash, ""
    raise ValueError("song_hash or path is required")


def _resolve_audio(path: str, resolver: Optional[Callable[[str], str]]) -> str:
    if not path:
        return ""
    if resolver:
        return resolver(path)
    from .melody_review import _default_resolve_audio_path
    return _default_resolve_audio_path(path)


def _melody_context(data_dir: Path, song_hash: str) -> Dict[str, Any]:
    context: Dict[str, Any] = {"bpm": 120.0, "tempo_curve": None, "time_signature": "4/4", "key": "C"}
    chord_file = _chord_file_for(data_dir, song_hash)
    if not chord_file.is_file():
        return context
    try:
        data = json.loads(chord_file.read_text(encoding="utf-8"))
    except Exception:
        return context
    context["bpm"] = _float_or(data.get("bpm"), 120.0)
    context["tempo_curve"] = data.get("tempo_curve") or None
    context["time_signature"] = data.get("time_signature") or data.get("meter") or "4/4"
    context["key"] = data.get("key") or data.get("chord_key") or "C"
    return context


def _read_legacy_melody(data_dir: Path, song_hash: str) -> Optional[List[Dict[str, Any]]]:
    cache_file = data_dir / "melodies" / f"{song_hash}.json"
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        melody = data.get("melody")
    else:
        melody = data
    return melody if isinstance(melody, list) else None


def _load_polyphonic_notes(*, polyphonic_json: str, polyphonic_midi: str) -> List[Dict[str, Any]]:
    if polyphonic_json:
        data = json.loads(Path(polyphonic_json).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            notes = data.get("notes") or data.get("melody") or data.get("events")
        else:
            notes = data
        if not isinstance(notes, list):
            raise ValueError("polyphonic_json must contain a list or a notes/events list")
        return [n for n in notes if isinstance(n, dict)]
    if polyphonic_midi:
        return _load_pretty_midi_notes(polyphonic_midi)
    raise ValueError("polyphonic_json or polyphonic_midi is required for solo_piano_polyphonic")


def _load_pretty_midi_notes(midi_path: str) -> List[Dict[str, Any]]:
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(midi_path)
    notes: List[Dict[str, Any]] = []
    for instrument in pm.instruments:
        if instrument.is_drum:
            continue
        for note in instrument.notes:
            notes.append({
                "start": float(note.start),
                "end": float(note.end),
                "midi": int(note.pitch),
                "velocity": int(note.velocity),
            })
    notes.sort(key=lambda n: (n["start"], -n["midi"]))
    return notes


def _chord_file_for(data_dir: Path, song_hash: str) -> Path:
    try:
        from chord_cache import chord_file_for
        return chord_file_for(song_hash, root=data_dir / "chords")
    except Exception:
        return data_dir / "chords" / song_hash[:2] / f"{song_hash}.json"


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
