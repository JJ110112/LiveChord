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


def get_music_roots() -> list[str]:
    """所有音樂庫根目錄（支援多資料夾）"""
    settings = _load_settings()
    # 新格式：music_roots 列表
    if settings.get("music_roots"):
        return [os.path.normpath(p) for p in settings["music_roots"] if p]
    # 舊格式遷移：music_root 字串 → 單元素列表
    if settings.get("music_root"):
        return [os.path.normpath(settings["music_root"])]
    env = os.environ.get("LIVECHORD_MUSIC_ROOT", "Y:/")
    return [os.path.normpath(env)]


def get_music_root() -> str:
    """主要音樂庫根目錄（向下相容，回傳第一個）"""
    return get_music_roots()[0]


def set_music_roots(roots: list[str]):
    """儲存多音樂庫路徑"""
    normed = [os.path.normpath(p) for p in roots if p and p.strip()]
    if not normed:
        raise ValueError("至少需要一個音樂庫路徑")
    settings = _load_settings()
    settings["music_roots"] = normed
    settings.pop("music_root", None)  # 移除舊格式
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def set_music_root(new_path: str):
    """向下相容：設定單一音樂庫（寫入 music_roots 列表）"""
    current = get_music_roots()
    current[0] = os.path.normpath(new_path)
    set_music_roots(current)


def resolve_path(track_path: str) -> str:
    """將曲目相對路徑解析為絕對路徑。

    路徑格式：
      - 無前綴 → music_roots[0] 下的相對路徑（向下相容）
      - @N/... → music_roots[N] 下的相對路徑
    """
    roots = get_music_roots()
    if track_path.startswith("@") and "/" in track_path:
        prefix, rest = track_path.split("/", 1)
        try:
            idx = int(prefix[1:])
        except ValueError:
            idx = 0
        if 0 <= idx < len(roots):
            return os.path.normpath(os.path.join(roots[idx], rest))
    return os.path.normpath(os.path.join(roots[0], track_path))


def get_midi_root() -> str:
    """MIDI 檔案根目錄（預設 X:/）"""
    settings = _load_settings()
    if settings.get("midi_root"):
        return os.path.normpath(settings["midi_root"])
    return os.path.normpath(os.environ.get("LIVECHORD_MIDI_ROOT", "X:/"))


def set_midi_root(new_path: str):
    _save_setting("midi_root", os.path.normpath(new_path))


def _save_setting(key: str, value):
    settings = _load_settings()
    settings[key] = value
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
