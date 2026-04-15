"""LiveChord 自動工作器 — 類似 Roon Core 的背景自動掃描與和弦分析"""

import os
import sys
import json
import time
import threading
import logging
import ctypes
from pathlib import Path
from datetime import datetime

log = logging.getLogger("livechord.auto")
from config import resolve_path
from chord_cache import song_hash, update_entry_from_file as cache_update_entry, ensure_synced as cache_ensure_synced
from data_cache import invalidate_chord_hash_set
from task_lock import get_task_lock

DATA_DIR = Path(__file__).parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "chord_queue.json"
CACHE_FILE = DATA_DIR / "library_cache.json"
CHORDS_DIR = DATA_DIR / "chords"
LOG_FILE = DATA_DIR / "activity.log"
QUEUE_CURSOR_FILE = DATA_DIR / ".queue_cursor.json"

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "auto_start_on_boot": False,        # 服務啟動時是否自動開啟 Core（預設關，使用者手動啟動）
    "auto_scan_enabled": False,
    "auto_scan_interval_minutes": 30,
    "auto_chord_enabled": False,
    "auto_chord_max_per_cycle": 20,     # 每輪最多偵測幾首
    "auto_chord_delay_seconds": 1.0,    # 每首之間的延遲（秒），避免 CPU 飢餓導致前端無回應
    "auto_chord_skip_genres": [],       # 跳過的 genre（如 Classics）
    "auto_chord_active_groups": [],     # 啟用的曲目群組（空 = 不過濾，附 1 用）
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


def _build_midi_index() -> list:
    """單次掃描 midi_root，回傳 [(full_path, fname), ...]。
    避免每首歌都對 midi_root 走一次 os.walk（過去的瓶頸）。"""
    from config import get_midi_root
    midi_root = get_midi_root()
    if not midi_root or not os.path.isdir(midi_root):
        return []
    result = []
    for dirpath, dirnames, filenames in os.walk(midi_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(('#', '.', '@'))]
        for fname in filenames:
            if fname.lower().endswith(('.mid', '.midi')):
                result.append((os.path.join(dirpath, fname), fname))
                # 每 500 個 MIDI 回報一次，避免大型 SMB MIDI 庫長時間靜默
                if len(result) % 500 == 0:
                    _worker_state["current_task"] = f"掃描 MIDI 庫 ({len(result)} 個)"
    return result


def _lookup_midi(track_name: str, midi_index: list) -> str:
    """從預建 MIDI index 查找匹配的 .mid 路徑，回傳 None 表示無匹配。"""
    if not track_name or not midi_index:
        return None
    from chord_api import _midi_matches
    for path, fname in midi_index:
        if _midi_matches(track_name, fname):
            return path
    return None


# ---------------------------------------------------------------------------
# 佇列 cursor — 跨輪記住「上次掃完發現 queue 是空」的狀態，避免重覆全量比對
# ---------------------------------------------------------------------------

def _compute_settings_hash(settings: dict) -> str:
    """穩定 hash 設定中影響 queue 內容的欄位。任一變動 → cursor 失效。"""
    import hashlib
    relevant = {
        "skip_genres": sorted(settings.get("auto_chord_skip_genres", []) or []),
        "active_groups": sorted(settings.get("auto_chord_active_groups", []) or []),
    }
    blob = json.dumps(relevant, sort_keys=True).encode("utf-8")
    return hashlib.md5(blob).hexdigest()[:12]


def _library_cache_mtime() -> float:
    try:
        return CACHE_FILE.stat().st_mtime
    except OSError:
        return 0.0


def _midi_root_mtime() -> float:
    """MIDI root 目錄本身的 mtime（單次 stat，不走遞迴）。
    新增 / 移除根層 .mid 會改變 mtime，足以觸發 cursor 失效。"""
    try:
        from config import get_midi_root
        p = get_midi_root()
        if not p or not os.path.isdir(p):
            return 0.0
        return os.stat(p).st_mtime
    except Exception:
        return 0.0


def _load_queue_cursor() -> dict:
    if not QUEUE_CURSOR_FILE.is_file():
        return None
    try:
        return json.loads(QUEUE_CURSOR_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_queue_cursor(data: dict):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        QUEUE_CURSOR_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _get_unanalyzed_tracks(settings: dict, midi_index: list = None) -> tuple:
    """取得尚未偵測和弦的曲目列表。

    回傳 (queue, stats)：
      - queue: list[str]，待偵測曲目路徑（測試歌排前）
      - stats: {"new": N, "upgrade": M}，分類計數

    BTC 既有檔案只有在 MIDI 索引中找得到匹配時才會被排入升級佇列，
    避免每輪都把所有 BTC 檔重排回去並做 BTC 重跑。
    """
    from data_cache import get_library_tracks
    tracks = get_library_tracks()
    if not tracks:
        return [], {"new": 0, "upgrade": 0}

    # ---- 快路徑：佇列 cursor 命中 ----
    # 若 (a) library_cache 沒重寫 (b) midi_root 沒變動 (c) 設定沒變動
    # 而且上輪 cursor 紀錄佇列為空 → 直接跳過全量比對。
    cur_lib_mtime = _library_cache_mtime()
    cur_midi_mtime = _midi_root_mtime()
    cur_settings_hash = _compute_settings_hash(settings)
    cursor = _load_queue_cursor()
    if (cursor
        and cursor.get("library_cache_mtime") == cur_lib_mtime
        and cursor.get("midi_root_mtime") == cur_midi_mtime
        and cursor.get("settings_hash") == cur_settings_hash
        and cursor.get("queue_was_empty")):
        _worker_state["current_task"] = "佇列 cursor 命中（無變動，跳過比對）"
        add_log("INFO", "佇列 cursor 命中：library / MIDI / 設定皆無變動，跳過全量比對")
        return [], {"new": 0, "upgrade": 0}

    skip_genres = [g.lower().strip() for g in settings.get("auto_chord_skip_genres", []) if g.strip()]
    active_groups = settings.get("auto_chord_active_groups", []) or []

    # 群組過濾準備（active_groups 為空表示不過濾）
    split_group = None
    if active_groups:
        from library_groups import _split_group as _sg
        split_group = _sg

    # 測試歌曲的特殊識別
    test_songs = ["dancing queen", "abba", "test", "benchmark"]

    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    if midi_index is None:
        midi_index = _build_midi_index()

    # ---- Pass 1：預過濾 ----
    # 先把 (a) skip_genres 與 (b) active_groups 排除掉，留下「真正候選」清單。
    # 這樣主迴圈的進度分母就會反映使用者實際選的範圍，而不是整個 library_cache。
    _worker_state["current_task"] = f"過濾候選 0/{len(tracks)}"
    relevant = []
    for i, t in enumerate(tracks):
        if i % 5000 == 0 and i > 0:
            _worker_state["current_task"] = f"過濾候選 {i}/{len(tracks)}"
        genre = t.get("genre", "").split("/")[0].lower().strip()
        if any(genre.startswith(sg) for sg in skip_genres):
            continue
        track_path = t.get("path", "")
        if not track_path:
            continue
        if split_group:
            try:
                root_idx, label, _ = split_group(track_path)
                group_id = f"@{root_idx}/{label}"
                if group_id not in active_groups:
                    continue
            except Exception:
                pass
        relevant.append(t)

    # ---- Pass 2：分類為 new / upgrade / skip ----
    # 用 chord_hash_set + chord_index dict 取代「每首讀 JSON 檔」的舊作法：
    #   - 不在 hash set → new
    #   - 在 hash set + source 是 midi/chordify → 已完成，跳過（不做 MIDI lookup）
    #   - 在 hash set + source 是 btc/空 → 才去 _lookup_midi 看能不能升級
    # 結果：對 94% 已偵測的 track 完全不打 MIDI lookup（O(M) → 0），
    # 只對剩下的 BTC 子集做 fuzzy MIDI 比對。
    from data_cache import get_chord_hash_set
    from chord_cache import _load_chord_index, _chord_index_cache
    chord_hashes = get_chord_hash_set()
    _load_chord_index()  # 確保 _chord_index_cache 已載入

    result = []
    test_tracks = []  # 優先處理的測試歌曲
    stats = {"new": 0, "upgrade": 0}
    relevant_total = len(relevant)
    _worker_state["current_task"] = f"建立佇列 0/{relevant_total}"

    for i, t in enumerate(relevant):
        if i % 1000 == 0 and i > 0:
            _worker_state["current_task"] = f"建立佇列 {i}/{relevant_total}"

        track_path = t.get("path", "")  # 已在 Pass 1 驗證
        track_hash = song_hash(track_path)

        kind = None  # "new" | "upgrade" | None
        if track_hash not in chord_hashes:
            kind = "new"
        else:
            entry = _chord_index_cache.get(track_hash) if _chord_index_cache else None
            src = (entry.get("source") if entry else "") or ""
            if src not in ("chordify", "midi"):
                # btc 或無來源 → 只有 MIDI 索引中找得到匹配時才升級
                track_name = t.get("name") or t.get("title", "")
                if _lookup_midi(track_name, midi_index):
                    kind = "upgrade"

        if kind:
            stats[kind] += 1
            track_name_l = (t.get("name") or t.get("title", "")).lower()
            track_artist = t.get("artist", "").lower()
            search_str = f"{track_name_l} {track_artist}"
            is_test_song = any(keyword in search_str for keyword in test_songs)
            if is_test_song:
                test_tracks.append(track_path)
            else:
                result.append(track_path)

    # 儲存 cursor — 下輪同樣狀態下可走快路徑
    _save_queue_cursor({
        "library_cache_mtime": cur_lib_mtime,
        "midi_root_mtime": cur_midi_mtime,
        "settings_hash": cur_settings_hash,
        "queue_was_empty": (stats["new"] == 0 and stats["upgrade"] == 0),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    })

    return test_tracks + result, stats


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


def _set_low_priority():
    """降低當前執行緒優先級（Windows），讓 event loop 優先回應前端請求"""
    if sys.platform == "win32":
        try:
            THREAD_PRIORITY_BELOW_NORMAL = -1
            handle = ctypes.windll.kernel32.GetCurrentThread()
            ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_BELOW_NORMAL)
        except Exception:
            pass


def _worker_loop():
    """背景工作器主迴圈"""
    global _worker_state
    _set_low_priority()
    _worker_state["running"] = True
    _worker_state["status"] = "idle"
    _worker_state["error"] = ""
    add_log("INFO", "自動工作器已啟動")

    while not _stop_event.is_set():
        try:
            settings = load_settings()

            # ---- 自動掃描音樂庫 ----
            if settings.get("auto_scan_enabled", True):
                _worker_state["status"] = "scanning"
                _worker_state["current_task"] = "掃描音樂庫"
                _do_auto_scan(settings)
                _worker_state["current_task"] = ""
            if _stop_event.is_set():
                break

            # ---- 自動和弦偵測 ----
            if settings.get("auto_chord_enabled", True):
                _worker_state["status"] = "detecting"
                _worker_state["current_task"] = "準備偵測佇列"
                _do_auto_chord_detect(settings)
                _worker_state["current_task"] = ""
            if _stop_event.is_set():
                break

            # ---- AI 模型漸進式學習 ----
            _worker_state["status"] = "training"
            _worker_state["current_task"] = "檢查 AI 模型"
            _retrain_ai_models()
            _worker_state["current_task"] = ""
            if _stop_event.is_set():
                break

            # ---- 等待下次循環 ----
            interval = max(settings.get("auto_scan_interval_minutes", 30), 1)
            next_time = datetime.now().timestamp() + interval * 60
            _worker_state["status"] = "waiting"
            _worker_state["next_scan_at"] = datetime.fromtimestamp(next_time).strftime("%H:%M:%S")
            _worker_state["current_task"] = f"下輪 {_worker_state['next_scan_at']}"

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
    """執行一輪增量掃描（受 task_lock 保護）"""
    from music_api import _scan_worker, _scan_state

    lock = get_task_lock()
    if not lock.try_acquire("SCAN", "AUTO"):
        other = lock.current_kind() or "其他任務"
        add_log("INFO", f"延後自動掃描：{other} 進行中，下個週期再試")
        return

    try:
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
    finally:
        lock.release()


def _do_auto_chord_detect(settings: dict):
    """偵測佇列中的曲目和弦（優先用 MIDI，fallback BTC，受 task_lock 保護）"""
    _worker_state["current_task"] = "掃描 MIDI 庫"
    add_log("INFO", "建立 MIDI 索引中…")
    midi_index = _build_midi_index()
    add_log("INFO", f"MIDI 索引完成：{len(midi_index)} 個檔案")

    _worker_state["current_task"] = "建立偵測佇列"
    add_log("INFO", "開始建立偵測佇列…")
    unanalyzed, stats = _get_unanalyzed_tracks(settings, midi_index)
    add_log("INFO", f"佇列建立完成：{len(unanalyzed)} 首待偵測（新檔 {stats['new']} / BTC 可升級 {stats['upgrade']}）")
    max_per_cycle = settings.get("auto_chord_max_per_cycle", 20)
    batch = unanalyzed[:max_per_cycle]

    _worker_state["detect_queue_size"] = len(unanalyzed)

    if not batch:
        add_log("INFO", "所有曲目已有和弦譜")
        return

    lock = get_task_lock()
    if not lock.try_acquire("CHORD_BATCH", "AUTO"):
        other = lock.current_kind() or "其他任務"
        add_log("INFO", f"延後和弦偵測：{other} 進行中，下個週期再試")
        return

    try:
        _worker_state["status"] = "detecting"
        add_log("INFO",
                f"開始自動偵測和弦: {len(batch)} 首"
                f"（佇列剩餘 {len(unanalyzed)}: 新檔 {stats['new']} / BTC 可升級 {stats['upgrade']}）")
        lock.update_progress(total=len(batch))

        CHORDS_DIR.mkdir(parents=True, exist_ok=True)

        _auto_chord_detect_loop(settings, batch, unanalyzed, lock, midi_index)
    finally:
        lock.release()


def _auto_chord_detect_loop(settings: dict, batch: list, unanalyzed: list, lock, midi_index: list):
    for i, track_path in enumerate(batch):
        if _stop_event.is_set():
            break

        name = track_path.split("/")[-1].replace(".flac", "")
        _worker_state["current_task"] = f"偵測和弦 ({i+1}/{len(batch)}): {name}"
        lock.update_progress(current_item=track_path, done=i + 1)

        # 判斷此 track 是「新檔」還是「BTC 升級」
        chords_file = CHORDS_DIR / f"{song_hash(track_path)}.json"
        is_btc_upgrade = False
        if chords_file.is_file():
            try:
                existing = json.loads(chords_file.read_text(encoding="utf-8"))
                if existing.get("source", "") not in ("chordify", "midi"):
                    is_btc_upgrade = True
            except Exception:
                pass

        # 優先用 MIDI（從預建 index 查表，O(M)）
        midi_path = _lookup_midi(name, midi_index)
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
                    chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
                    cache_update_entry(track_path)
                    _worker_state["detect_count"] += 1
                    verb = "升級" if is_btc_upgrade else "匯入"
                    add_log("OK", f"MIDI {verb}: {name} (Key: {key}, {len(entries)} chords)")
                    continue
            except Exception as e:
                add_log("WARN", f"MIDI 匯入失敗: {name} — {type(e).__name__}: {e or '<空訊息>'}")
                # 既有 BTC 檔案 MIDI 升級失敗 → 不要覆寫，跳過
                if is_btc_upgrade:
                    continue

        # 既有 BTC 檔但 MIDI 不可用 → 不重跑 BTC（避免覆寫已有結果造成 GPU 空轉）
        if is_btc_upgrade:
            continue

        # fallback: BTC 自動偵測（僅對全新檔案）
        full = resolve_path(track_path)
        if not os.path.isfile(full):
            continue

        try:
            from chord_detect import detect_chords_and_key_isolated
            chords, key = detect_chords_and_key_isolated(full)
            sheet = {"path": track_path, "key": key, "capo": 0,
                     "source": "btc", "chords": chords}
            chords_file.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
            cache_update_entry(track_path)
            _worker_state["detect_count"] += 1
            add_log("OK", f"BTC 偵測: {name} (Key: {key}, {len(chords)} chords)")
        except Exception as e:
            add_log("ERROR", f"偵測失敗: {name} — {type(e).__name__}: {e or '<空訊息>'}")

        # 每首之間暫停，讓 event loop 有時間回應前端請求
        delay = settings.get("auto_chord_delay_seconds", 1.0)
        if delay > 0 and not _stop_event.is_set():
            _stop_event.wait(timeout=delay)

    remaining = len(unanalyzed) - len(batch)
    _worker_state["detect_queue_size"] = remaining
    _worker_state["last_detect_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache_ensure_synced(force=True)
    invalidate_chord_hash_set()  # 讓 admin 群組覆蓋率立即反映新檔
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
    _worker_state["status"] = "stopping"
    # 同時取消正在跑的掃描，避免 worker 卡在 _scan_worker 內無法即時退出
    try:
        from music_api import cancel_scan
        cancel_scan()
    except Exception:
        pass
    # 釋放等待中的 trigger_event，讓 worker_loop 立即往前走
    _trigger_event.set()
    return True


_trigger_event = threading.Event()


def trigger_now():
    """立即觸發一輪（中斷等待，不停止工作器）"""
    if _worker_state["running"] and _worker_state["status"] == "waiting":
        _trigger_event.set()
        return True
    return False
