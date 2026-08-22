import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import music_api

spec = importlib.util.spec_from_file_location(
    "fill_music_folder_covers",
    ROOT / "scripts" / "fill_music_folder_covers.py",
)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)
_generate_placeholder_cover = module._generate_placeholder_cover


def test_find_cover_recurses_into_child_dir(tmp_path):
    folder = tmp_path / "parent-folder"
    child = folder / "first-child"
    child.mkdir(parents=True)
    cover = child / "cover.jpg"
    cover.write_bytes(b"fake-cover")

    assert music_api._find_cover_in_tree(str(folder)) == str(cover)
    assert music_api._directory_has_cover(str(folder)) is True


def test_find_cover_returns_none_when_absent(tmp_path):
    folder = tmp_path / "empty-folder"
    folder.mkdir()

    assert music_api._find_cover_in_tree(str(folder)) is None
    assert music_api._directory_has_cover(str(folder)) is False


def test_placeholder_cover_created_when_no_child_or_embedded_art(tmp_path):
    folder = tmp_path / "POP"
    folder.mkdir()

    cover = _generate_placeholder_cover(folder, "POP")

    assert cover.exists()
    assert cover.name == "cover.jpg"
    assert cover.stat().st_size > 0
