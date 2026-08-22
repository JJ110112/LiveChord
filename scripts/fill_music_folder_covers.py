#!/usr/bin/env python3
"""Fill missing folder covers by copying a child folder cover or extracting one from FLAC files.

Examples:
  python scripts/fill_music_folder_covers.py Z:\Classics
  python scripts/fill_music_folder_covers.py Z:\Classics --dry-run
  python scripts/fill_music_folder_covers.py Z:\Classics --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.oggvorbis import OggVorbis

COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "Cover.jpg")
AUDIO_EXTS = {".flac", ".mp3", ".ogg", ".wav"}
IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "dist",
    "build",
    "target",
    "@eaDir",
    "System Volume Information",
    "$RECYCLE.BIN",
}


def _is_cover_file(path: Path) -> bool:
    return path.is_file() and path.name.lower() in {n.lower() for n in COVER_NAMES}


def _extract_embedded_cover(audio_path: Path) -> Optional[Tuple[bytes, str]]:
    """Return (bytes, mime_type) from the first embedded cover found in a supported audio file."""
    suffix = audio_path.suffix.lower()
    try:
        if suffix == ".flac":
            tags = FLAC(str(audio_path))
            if tags and getattr(tags, "pictures", None):
                pic = tags.pictures[0]
                return pic.data, (pic.mime or "image/jpeg")

        if suffix == ".mp3":
            tags = ID3(str(audio_path))
            for pic in tags.getall("APIC"):
                return pic.data, (pic.mime or "image/jpeg")

        if suffix == ".ogg":
            tags = OggVorbis(str(audio_path))
            # some Ogg files store metadata_block_picture as base64 payload
            b64_values = tags.get("metadata_block_picture", [])
            if b64_values:
                import base64
                from mutagen.flac import Picture

                pic = Picture(base64.b64decode(b64_values[0]))
                return pic.data, (pic.mime or "image/jpeg")

        if suffix == ".wav":
            audio = MutagenFile(str(audio_path), easy=True)
            if audio is not None:
                for key in ("covr", "coverart"):
                    if key in audio:
                        val = audio.get(key)
                        if val:
                            return bytes(val[0]), "image/jpeg"
    except Exception:
        pass

    return None


def _write_cover_bytes(target_dir: Path, data: bytes, mime_type: str) -> Path:
    """Write a folder cover as JPEG where possible, while preserving PNG if conversion is not available."""
    target = target_dir / "cover.jpg"
    mime = (mime_type or "").lower()

    if "png" in mime or data.startswith(b"\x89PNG"):
        if Image is not None:
            try:
                image = Image.open(io.BytesIO(data))
                if image.mode in {"RGBA", "LA", "P"}:
                    image = image.convert("RGB")
                target = target_dir / "cover.jpg"
                image.save(target, format="JPEG", quality=90)
                return target
            except Exception:
                pass
        target = target_dir / "cover.png"
        target.write_bytes(data)
        return target

    target = target_dir / "cover.jpg"
    target.write_bytes(data)
    return target


def _generate_placeholder_cover(directory: Path, label: str) -> Path:
    """Create a deterministic placeholder cover when a real image is unavailable."""
    if Image is None:
        raise RuntimeError("Pillow is required to generate placeholder covers")

    width, height = 1200, 1200
    hue = int(hashlib.sha1(label.encode("utf-8")).hexdigest(), 16) % 360
    bg1 = (hue, 70, 42)
    bg2 = ((hue + 35) % 360, 60, 30)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        from PIL import ImageColor
        c1 = ImageColor.getrgb(f"hsl({bg1[0]}, {bg1[1]}%, {bg1[2]}%)")
        c2 = ImageColor.getrgb(f"hsl({bg2[0]}, {bg2[1]}%, {bg2[2]}%)")
    except Exception:
        c1 = (48, 63, 160)
        c2 = (126, 87, 194)

    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(c1[0] * (1 - ratio) + c2[0] * ratio)
        g = int(c1[1] * (1 - ratio) + c2[1] * ratio)
        b = int(c1[2] * (1 - ratio) + c2[2] * ratio)
        draw.line((0, y, width, y), fill=(r, g, b))

    text = (label or directory.name or "Library")
    # clamp label to a readable length for the generated cover
    if len(text) > 24:
        text = text[:21] + "..."

    padding = 80
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 90)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", 90)
        except Exception:
            font = None

    bbox = draw.textbbox((0, 0), text, font=font) if font else (0, 0, 600, 120)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (width - text_w) / 2
    y = (height - text_h) / 2

    draw.rounded_rectangle((padding, padding, width - padding, height - padding), radius=60, outline=(255, 255, 255, 180), width=8)
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    target = directory / "cover.jpg"
    image.save(target, format="JPEG", quality=90)
    return target


def _find_cover_in_dir(directory: Path) -> Optional[Path]:
    for candidate in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if _is_cover_file(candidate):
            return candidate
    return None


def _find_cover_in_child_dir(directory: Path) -> Optional[Path]:
    for child in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        cover = _find_cover_in_dir(child)
        if cover is not None:
            return cover
        deeper = _find_cover_in_child_dir(child)
        if deeper is not None:
            return deeper
    return None


def _ensure_folder_cover(directory: Path, dry_run: bool = False, verbose: bool = False) -> bool:
    directory = directory.resolve()
    if not directory.is_dir():
        return False

    existing = _find_cover_in_dir(directory)
    if existing is not None:
        return False

    child_cover = _find_cover_in_child_dir(directory)
    if child_cover is not None:
        target = directory / "cover.jpg"
        if verbose:
            print(f"[copy] {child_cover} -> {target}")
        if not dry_run:
            try:
                shutil.copy2(child_cover, target)
            except Exception:
                if child_cover.suffix.lower() == ".png":
                    payload = child_cover.read_bytes()
                    _write_cover_bytes(directory, payload, "image/png")
                else:
                    raise
        return True

    for audio_file in sorted(directory.rglob("*"), key=lambda p: str(p).lower()):
        if not audio_file.is_file() or audio_file.suffix.lower() not in AUDIO_EXTS:
            continue
        result = _extract_embedded_cover(audio_file)
        if result is None:
            continue
        data, mime = result
        if verbose:
            print(f"[extract] {audio_file} -> {directory / 'cover.jpg'}")
        if not dry_run:
            _write_cover_bytes(directory, data, mime)
        return True

    if not dry_run:
        label = directory.name or "Library"
        if verbose:
            print(f"[placeholder] {directory} -> {directory / 'cover.jpg'}")
        _generate_placeholder_cover(directory, label)
        return True

    return True


def scan_root(root: Path, dry_run: bool = False, verbose: bool = False) -> int:
    root = root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    updated = 0
    for directory, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in sorted(dirnames) if d not in IGNORED_DIR_NAMES]
        current = Path(directory)
        if current.name in IGNORED_DIR_NAMES:
            continue
        if _ensure_folder_cover(current, dry_run=dry_run, verbose=verbose):
            updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill missing folder covers from child folder covers or embedded FLAC cover art.")
    parser.add_argument("paths", nargs="+", help="One or more root folders to scan, e.g. Z:\\Classics")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing files")
    parser.add_argument("--verbose", action="store_true", help="Print each applied cover change")
    args = parser.parse_args()

    total = 0
    for raw in args.paths:
        root = Path(raw).expanduser()
        count = scan_root(root, dry_run=args.dry_run, verbose=args.verbose)
        total += count
        print(f"{root}: {count} folder cover(s) {'would be created' if args.dry_run else 'created'}")

    print(f"Total: {total} folder cover(s) processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
