"""LiveChord 自動工作器 — 類似 Roon Core 的背景自動掃描與和弦分析"""

import os
import json
import time
import hashlib
import threading
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger("livechord.auto")

DATA_DIR = Path(__file__).parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "chord_queue.json"
CACHE_FILE = DATA_DIR / "library_cache.json"
CHORDS_DIR = DATA_DIR / "chords"
LOG_FILE = DATA_DIR / "activity.log"

MUSIC_ROOT = os.path.normpath(os.environ.get("LIVECHORD_MUSIC_ROOT", "Z:/"))

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "auto_scan_enabled": False,
    "auto_scan_interval_minutes": 30,
    "auto_chord_enabled": False,
    "auto_chord_max_per_cycle": 20,     # 每輪最多偵測幾首
    "auto_chord_skip_genres": [],        # 跳過的 genre（如 Classics）
}


def load_settings() -> dict:
    if SETTINGS_FILE.is_file():
        try:
            return {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Activity Log
# ---------------------------------------------------------------------------

_activity_log = []  # 最近 100 筆
MAX_LOG = 100


def add_log(level: str, msg: str):
    entry = {"time": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg}
    _activity_log.append(entry)
    if len(_activity_log) > MAX_LOG:
        _activity_log.pop(0)
    log.info(f"[{level}] {msg}")


def get_log(limit: int = 50) -> list:
    return _activity_log[-limit:]


# ---------------------------------------------------------------------------
# 和弦偵測佇列
# ---------------------------------------------------------------------------

def _song_hash(path: str) -> str:
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:12]


def _get_unanalyzed_tracks(settings: dict) -> list:
    """取得尚未偵測和弦的曲目列表"""
    if not CACHE_FILE.is_file():
        return []

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    tracks = cache.get("tracks", [])
    skip_genres = set(g.lower() for g in settings.get("auto_chord_skip_genres", []))

    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for t in tracks:
        # 跳過指定 genre
        genre = t.get("genre", "").split("/")[0].lower()
        if genre in skip_genres:
            continue
        # 檢查是否已有和弦譜
        chords_file = CHORDS_DIR / f"{_song_hash(t['path'])}.json"
        if not chords_file.is_file():
            result.append(t["path"])

    return result


# ---------------------------------------------------------------------------
# 自動工作器狀態
# ---------------------------------------------------------------------------

_worker_state = {
    "running": False,
    "status": "stopped",      # stopped | scanning | detecting | idle | waiting
    "current_task": "",
    "next_scan_at": "",
    "scan_count": 0,          # 已完成幾輪掃描
    "detect_count": 0,        # 已偵測幾首
    "detect_queue_size": 0,   # 佇列剩餘
    "last_scan_time": "",
    "last_detect_time": "",
    "error": "",
}


def get_worker_state() -> dict:
    return dict(_worker_state)


# ---------------------------------------------------------------------------
# 自動工作器主迴圈
# ---------------------------------------------------------------------------

_worker_thread = None
_stop_event = threading.Event()


def _worker_loop():
    """背景工作器主迴圈"""
    global _worker_state
    _worker_state["running"] = True
    _worker_state["status"] = "idle"
    _worker_state["error"] = ""
    add_log("INFO", "自動工作器已啟動")

    while not _stop_event.is_set():
        try:
            settings = load_settings()

            # ---- 自動掃描音樂庫 ----
            if settings.get("auto_scan_enabled", True):
                _do_auto_scan(settings)

            # ---- 自動和弦偵測 ----
            if settings.get("auto_chord_enabled", True):
                _do_auto_chord_detect(settings)

            # ---- 等待下次循環 ----
            interval = max(settings.get("auto_scan_interval_minutes", 30), 1)
            next_time = datetime.now().timestamp() + interval * 60
            _worker_state["status"] = "waiting"
            _worker_state["next_scan_at"] = datetime.fromtimestamp(next_time).strftime("%H:%M:%S")
            _worker_state["current_task"] = ""

            # 可中斷的等待
            _stop_event.wait(timeout=interval * 60)

        except Exception as e:
            _worker_state["error"] = str(e)
            add_log("ERROR", f"工作器錯誤: {e}")
            _stop_event.wait(timeout=60)

    _worker_state["running"] = False
    _worker_state["status"] = "stopped"
    add_log("INFO", "自動工作器已停止")


def _do_auto_scan(settings: dict):
    """執行一輪增量掃描"""
    from music_api import _scan_worker, _scan_state

    # 如果已有掃描在跑，跳過
    if _scan_state["running"]:
        return

    _worker_state["status"] = "scanning"
    _worker_state["current_task"] = "掃描音樂庫"
    add_log("INFO", "開始自動增量掃描")

    _scan_worker("incremental")

    new = _scan_state.get("new_tracks", 0)
    updated = _scan_state.get("updated_tracks", 0)
    _worker_state["scan_count"] += 1
    _worker_state["last_scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if new > 0 or updated > 0:
        add_log("INFO", f"掃描完成: 新增 {new}, 更新 {updated}")
    else:
        add_log("INFO", "掃描完成: 無變動")


def _do_auto_chord_detect(settings: dict):
    """偵測佇列中的曲目和弦"""
    try:
        from chord_detect import detect_chords, detect_key
    except ImportError:
        add_log("ERROR", "chord_detect 模組載入失敗")
        return

    unanalyzed = _get_unanalyzed_tracks(settings)
    max_per_cycle = settings.get("auto_chord_max_per_cycle", 20)
    batch = unanalyzed[:max_per_cycle]

    _worker_state["detect_queue_size"] = len(unanalyzed)

    if not batch:
        add_log("INFO", "所有曲目已有和弦譜")
        return

    _worker_state["status"] = "detecting"
    add_log("INFO", f"開始自動偵測和弦: {len(batch)} 首（佇列剩餘 {len(unanalyzed)}）")

    CHORDS_DIR.mkdir(parents=True, exist_ok=True)

    for i, track_path in enumerate(batch):
        if _stop_event.is_set():
            break

        name = track_path.split("/")[-1].replace(".flac", "")
        _worker_state["current_task"] = f"偵測和弦 ({i+1}/{len(batch)}): {name}"

        full = os.path.normpath(os.path.join(MUSIC_ROOT, track_path))
        if not os.path.isfile(full):
            continue

        try:
            key = detect_key(full)
            chords = detect_chords(full)
            sheet = {"path": track_path, "key": key, "capo": 0, "chords": chords}
            chords_file = CHORDS_DIR / f"{_song_hash(track_path)}.json"
            chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
            _worker_state["detect_count"] += 1
            add_log("OK", f"和弦偵測完成: {name} (Key: {key}, {len(chords)} chords)")
        except Exception as e:
            add_log("ERROR", f"偵測失敗: {name} — {e}")

    remaining = len(unanalyzed) - len(batch)
    _worker_state["detect_queue_size"] = remaining
    _worker_state["last_detect_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    add_log("INFO", f"本輪偵測完成，佇列剩餘 {remaining} 首")


# ---------------------------------------------------------------------------
# 控制 API（供 main.py 呼叫）
# ---------------------------------------------------------------------------

def start_worker():
    global _worker_thread, _stop_event
    if _worker_state["running"]:
        return False
    _stop_event = threading.Event()
    _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
    _worker_thread.start()
    return True


def stop_worker():
    global _stop_event
    if not _worker_state["running"]:
        return False
    _stop_event.set()
    return True


def trigger_now():
    """立即觸發一輪（中斷等待）"""
    if _worker_state["running"] and _worker_state["status"] == "waiting":
        _stop_event.set()
        # 重啟
        time.sleep(1)
        start_worker()
        return True
    return False
