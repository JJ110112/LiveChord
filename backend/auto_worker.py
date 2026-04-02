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
from config import get_music_root

DATA_DIR = Path(__file__).parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "chord_queue.json"
CACHE_FILE = DATA_DIR / "library_cache.json"
CHORDS_DIR = DATA_DIR / "chords"
LOG_FILE = DATA_DIR / "activity.log"

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

    # 測試歌曲的特殊識別
    test_songs = ["dancing queen", "abba", "test", "benchmark"]
    
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    test_tracks = []  # 優先處理的測試歌曲
    
    for t in tracks:
        # 跳過指定 genre
        genre = t.get("genre", "").split("/")[0].lower()
        if genre in skip_genres:
            continue
            
        track_path = t.get("path", "")
        if not track_path:
            continue
            
        # 檢查和弦檔案：沒有 → 需偵測，有但來源是 btc → 可被 MIDI 升級
        track_hash = _song_hash(track_path)
        chord_file = CHORDS_DIR / f"{track_hash}.json"

        needs_detect = False
        if not chord_file.is_file():
            needs_detect = True
        else:
            try:
                existing = json.loads(chord_file.read_text(encoding="utf-8"))
                src = existing.get("source", "") or ""
                if src not in ("chordify", "midi"):
                    needs_detect = True  # btc 或無來源 → 可升級
            except Exception:
                needs_detect = True

        if needs_detect:
            track_name = (t.get("name") or t.get("title", "")).lower()
            track_artist = t.get("artist", "").lower()
            search_str = f"{track_name} {track_artist}"
            is_test_song = any(keyword in search_str for keyword in test_songs)
            if is_test_song:
                test_tracks.append(track_path)
            else:
                result.append(track_path)
    
    # 測試歌曲排在最前面
    return test_tracks + result


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

            # ---- AI 模型漸進式學習 ----
            _retrain_ai_models()

            # ---- 等待下次循環 ----
            interval = max(settings.get("auto_scan_interval_minutes", 30), 1)
            next_time = datetime.now().timestamp() + interval * 60
            _worker_state["status"] = "waiting"
            _worker_state["next_scan_at"] = datetime.fromtimestamp(next_time).strftime("%H:%M:%S")
            _worker_state["current_task"] = ""

            # 可中斷的等待（stop 或 trigger 都會中斷）
            _trigger_event.clear()
            _trigger_event.wait(timeout=interval * 60)
            if _stop_event.is_set():
                break

        except Exception as e:
            _worker_state["error"] = str(e)
            add_log("ERROR", f"工作器錯誤: {e}")
            _stop_event.wait(timeout=60)

    _worker_state["running"] = False
    _worker_state["status"] = "stopped"
    add_log("INFO", "自動工作器已停止")


_last_chord_count = -1  # -1 = 尚未初始化


def _retrain_ai_models():
    """當和弦資料有變更時，重新訓練 AI 模型

    若已有離線訓練快取（data/models/），首次啟動只載入快取不重訓。
    只有當和弦數量實際增長時才觸發重訓。
    """
    global _last_chord_count
    try:
        chord_files = list(CHORDS_DIR.glob("*.json")) if CHORDS_DIR.is_dir() else []
        current_count = len(chord_files)

        # 首次啟動：記錄目前數量，若有快取就跳過重訓
        if _last_chord_count == -1:
            _last_chord_count = current_count
            models_dir = DATA_DIR / "models"
            if (models_dir / "markov.json").is_file():
                add_log("INFO", f"AI 模型已有離線快取，載入中")
                # 確保 singletons 載入快取
                from ai.markov import get_predictor
                from ai.hmm import get_emission
                from ai.groove_dict import get_groove_dict
                get_predictor(str(CHORDS_DIR))
                get_emission(str(CHORDS_DIR))
                get_groove_dict(str(CHORDS_DIR))
                add_log("OK", f"AI 模型快取載入完成")
                return
            # 無快取，fall through 執行完整訓練
            _last_chord_count = 0

        if current_count == _last_chord_count:
            return  # 沒有變動，跳過

        _worker_state["status"] = "detecting"
        _worker_state["current_task"] = "AI 模型學習中"
        add_log("INFO", f"AI 模型重新訓練（{_last_chord_count}→{current_count} 首）")

        from ai.markov import retrain as markov_retrain
        import ai.chord2vec as c2v_mod
        import ai.groove_dict as gd_mod

        markov_retrain(str(CHORDS_DIR))

        c2v_mod._model = None
        c2v_mod.get_chord2vec(str(CHORDS_DIR))

        gd_mod._dict = None
        # 刪除舊快取強制從資料重建
        if gd_mod._CACHE_FILE.is_file():
            gd_mod._CACHE_FILE.unlink()
        gd = gd_mod.get_groove_dict(str(CHORDS_DIR))
        gd_mod._MODELS_DIR.mkdir(parents=True, exist_ok=True)
        gd.save(str(gd_mod._CACHE_FILE))

        _last_chord_count = current_count
        add_log("OK", f"AI 模型訓練完成（{current_count} 首和弦資料）")

    except Exception as e:
        add_log("ERROR", f"AI 訓練失敗: {e}")


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


def _find_midi_for_track(track_name: str) -> str:
    """在 MIDI 目錄中搜尋與歌名匹配的 .mid 檔案"""
    from config import get_midi_root
    from chord_api import _midi_matches
    midi_root = get_midi_root()
    if not os.path.isdir(midi_root):
        return None

    for dirpath, dirnames, filenames in os.walk(midi_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(('#', '.', '@'))]
        for fname in filenames:
            if not fname.lower().endswith(('.mid', '.midi')):
                continue
            if _midi_matches(track_name, fname):
                return os.path.join(dirpath, fname)
    return None


def _do_auto_chord_detect(settings: dict):
    """偵測佇列中的曲目和弦（優先用 MIDI，fallback BTC）"""
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

        # 優先用 MIDI
        midi_path = _find_midi_for_track(name)
        if midi_path:
            try:
                import sys
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
                from midi_to_lab import midi_to_lab
                entries = midi_to_lab(midi_path, verbose=False)
                if entries:
                    from collections import Counter
                    roots = [e["chord"][0] if len(e["chord"]) < 2 or e["chord"][1] not in '#b'
                             else e["chord"][:2] for e in entries if e["chord"][0] in 'ABCDEFG']
                    key = Counter(roots).most_common(1)[0][0] if roots else ""
                    sheet = {"path": track_path, "key": key, "capo": 0,
                             "source": "midi", "chords": entries}
                    chords_file = CHORDS_DIR / f"{_song_hash(track_path)}.json"
                    chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
                    _worker_state["detect_count"] += 1
                    add_log("OK", f"MIDI 匯入: {name} (Key: {key}, {len(entries)} chords)")
                    continue
            except Exception as e:
                add_log("WARN", f"MIDI 匯入失敗: {name} — {e}，改用 BTC")

        # fallback: BTC 自動偵測
        full = os.path.normpath(os.path.join(get_music_root(), track_path))
        if not os.path.isfile(full):
            continue

        try:
            from chord_detect import detect_chords, detect_key
            key = detect_key(full)
            chords = detect_chords(full)
            sheet = {"path": track_path, "key": key, "capo": 0,
                     "source": "btc", "chords": chords}
            chords_file = CHORDS_DIR / f"{_song_hash(track_path)}.json"
            chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
            _worker_state["detect_count"] += 1
            add_log("OK", f"BTC 偵測: {name} (Key: {key}, {len(chords)} chords)")
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


_trigger_event = threading.Event()


def trigger_now():
    """立即觸發一輪（中斷等待，不停止工作器）"""
    if _worker_state["running"] and _worker_state["status"] == "waiting":
        _trigger_event.set()
        return True
    return False
