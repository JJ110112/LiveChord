"""Persistent HTDemucs stem cache for RH melody shadow candidates."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from .melody_candidate import stem_dir, stem_path
from .stem_separator import StemSeparator


STEM_NAMES = ("vocals", "bass", "drums", "other")
REQUIRED_STEMS = {"vocals", "other"}


@dataclass
class StemCacheResult:
    ok: bool
    stems: Dict[str, str]
    cache_dir: str
    reused: bool = False
    error: str = ""


class StemCache:
    """Cache Demucs stems at V:/data/stems/<hh>/<hash>/ for reuse."""

    def __init__(
        self,
        data_dir: str | Path = Path(r"V:\data"),
        separator_factory: Optional[Callable[[], StemSeparator]] = None,
    ):
        self.data_dir = Path(data_dir)
        self.separator_factory = separator_factory or (lambda: StemSeparator())

    def ensure_stems(self, *, song_hash: str, audio_path: str, force: bool = False) -> StemCacheResult:
        out_dir = stem_dir(self.data_dir, song_hash)
        cached = self._read_cached(song_hash)
        if REQUIRED_STEMS.issubset(cached) and not force:
            return StemCacheResult(ok=True, stems=cached, cache_dir=str(out_dir), reused=True)

        audio_file = Path(audio_path)
        if not audio_file.is_file():
            return StemCacheResult(ok=False, stems={}, cache_dir=str(out_dir), error="audio_not_found")

        separator = self.separator_factory()
        separated = separator.separate(str(audio_file))
        if not separated:
            return StemCacheResult(ok=False, stems={}, cache_dir=str(out_dir), error="demucs_failed")

        out_dir.mkdir(parents=True, exist_ok=True)
        copied: Dict[str, str] = {}
        for name in STEM_NAMES:
            src = separated.get(name)
            if not src:
                continue
            src_path = Path(src)
            if not src_path.is_file():
                continue
            dst = stem_path(self.data_dir, song_hash, name)
            if src_path.resolve() == dst.resolve():
                copied[name] = str(dst)
                continue
            shutil.move(str(src_path), str(dst))
            copied[name] = str(dst)

        self._cleanup_source_dir(separated)

        if not REQUIRED_STEMS.issubset(copied):
            return StemCacheResult(ok=False, stems=copied, cache_dir=str(out_dir), error="missing_required_stems")
        return StemCacheResult(ok=True, stems=copied, cache_dir=str(out_dir), reused=False)

    def _read_cached(self, song_hash: str) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for name in STEM_NAMES:
            path = stem_path(self.data_dir, song_hash, name)
            if path.is_file():
                found[name] = str(path)
        return found

    def _cleanup_source_dir(self, separated: Dict[str, str]) -> None:
        if not separated:
            return
        try:
            source_dir = Path(next(iter(separated.values()))).parent
            target_dir = source_dir.resolve()
            cache_root = (self.data_dir / "stems").resolve()
            if cache_root in target_dir.parents or target_dir == cache_root:
                return
            shutil.rmtree(source_dir, ignore_errors=True)
        except Exception:
            return
