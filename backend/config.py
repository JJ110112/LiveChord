import os
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

def _load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_music_root() -> str:
    """音樂庫根目錄（預設 Y:/）"""
    settings = _load_settings()
    if settings.get("music_root"):
        return os.path.normpath(settings["music_root"])
    return os.path.normpath(os.environ.get("LIVECHORD_MUSIC_ROOT", "Y:/"))


def get_midi_root() -> str:
    """MIDI 檔案根目錄（預設 X:/）"""
    settings = _load_settings()
    if settings.get("midi_root"):
        return os.path.normpath(settings["midi_root"])
    return os.path.normpath(os.environ.get("LIVECHORD_MIDI_ROOT", "X:/"))

def set_music_root(new_path: str):
    _save_setting("music_root", os.path.normpath(new_path))


def set_midi_root(new_path: str):
    _save_setting("midi_root", os.path.normpath(new_path))


def _save_setting(key: str, value):
    settings = _load_settings()
    settings[key] = value
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
