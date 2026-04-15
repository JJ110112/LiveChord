"""
LiveChord 巨量音訊批次和弦與旋律雙引擎工作站 (RTX 5080 + i9 全速版)
專門設計給高效能 PC 環境使用。
利用多執行緒平行處理 CPU 音訊載入與 pYIN 旋律擷取 (librosa)，
並送交 GPU (PyTorch) 進行高速 BTC 推論，徹底榨乾系統效能！
"""

import os
import sys
import json
import time
import argparse
import threading
import warnings
import collections
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 靜音 librosa / audioread 各類無害警告
warnings.filterwarnings("ignore", message="n_fft=.*is too large")
warnings.filterwarnings("ignore", message="PySoundFile failed")
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="librosa")
warnings.filterwarnings("ignore", category=UserWarning, module="ai.melody_extractor")

# 確保可以 import backend 下的模組
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chord_cache import song_hash

# 延遲 import: melody-only 模式不需要 GPU 相關套件
# chord_detect, torch 在 chord/both 模式才載入
# MelodyExtractor 在 melody/both 模式才載入
detect_chords = None
_load_model = None
torch = None
MelodyExtractor = None

def _lazy_import_chord():
    global detect_chords, _load_model, torch
    if detect_chords is None:
        from chord_detect import detect_chords as _dc, _load_model as _lm
        import torch as _torch
        detect_chords = _dc
        _load_model = _lm
        torch = _torch

def _lazy_import_melody():
    global MelodyExtractor
    if MelodyExtractor is None:
        from ai.melody_extractor import MelodyExtractor as _ME
        MelodyExtractor = _ME

def _format_duration(seconds):
    """將秒數格式化為 X天 X時 X分"""
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    parts = []
    if d > 0: parts.append(f"{d}天")
    if h > 0: parts.append(f"{h}時")
    parts.append(f"{m}分")
    return " ".join(parts)

# ---------------------------------------------------------------------------
# 設定區
# ---------------------------------------------------------------------------

# 必須排除的資料夾清單 (Blacklist) — 第一層 Genre
SKIP_GENRES = {
    "classics", "classical", "symphony",
    "sleep", "relax", "meditation",
    "electronic dance music", "edm", "techno",
    "other"
}

# 專輯名稱關鍵字黑名單 — 無和弦內容（純鼓/節拍/音效）
SKIP_ALBUM_KEYWORDS = {
    "drum track", "drum loop", "hip-hop & rap beat",
    "horror soundscape", "sound effect", "sfx",
}

# 支援的音檔格式
SUPPORTED_EXT = {".flac", ".mp3", ".wav"}

# 跳過超過此大小的檔案（MB），避免 OOM
MAX_FILE_SIZE_MB = 100

# 資料庫位置 — 固定寫到 NAS，讓 LiveChord Server 即時存取
CHORDS_DIR = Path("V:/data/chords")
MELODIES_DIR = Path("V:/data/melodies")

# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------


def _is_skipped_genre(rel_path: str) -> bool:
    """判斷該路徑是否屬於黑名單曲風或無和弦內容"""
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) > 1:
        genre = parts[0].lower()
        if genre in SKIP_GENRES:
            return True
        if len(parts) > 2:
            album = parts[1].lower()
            for kw in SKIP_ALBUM_KEYWORDS:
                if kw in album:
                    return True
    return False

# ---------------------------------------------------------------------------
# 進度報告 & 停止機制
# ---------------------------------------------------------------------------
_recent_logs = collections.deque(maxlen=100)
_progress_file = None
_stop_file = None

def _write_progress(data: dict):
    """Atomically write progress JSON"""
    if not _progress_file:
        return
    try:
        tmp = _progress_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(_progress_file)
    except Exception:
        pass

def _should_stop() -> bool:
    return _stop_file is not None and _stop_file.exists()

def _add_log(level: str, msg: str):
    _recent_logs.append({"time": datetime.now().strftime("%H:%M:%S"), "level": level, "msg": msg})

# ---------------------------------------------------------------------------
# 核心任務處理
# ---------------------------------------------------------------------------
print_lock = threading.Lock()
_gpu_semaphore = threading.Semaphore(6)  # RTX 5080 16GB VRAM — 放寬至 6 並發
_task_history = collections.deque(maxlen=2000) # 用於動態預測最近 2000 筆的跳過率
# Phase 11: fast_mode 批次高速模式
# - 關閉 adaptive_range (省去全頻段粗掃 pYIN)
# - 關閉 onset detection (省去 onset_strength 計算)
# - 保留 vibrato filter (成本低但品質提升明顯)
# 預估速度提升: 0.08→0.13 首/秒 (+60%)
_melody_extractor = None  # 延遲初始化，melody 模式才載入
_run_mode = "both"  # "both" | "melody" | "chord"

def _ensure_melody_extractor():
    global _melody_extractor
    if _melody_extractor is None:
        _lazy_import_melody()
        _melody_extractor = MelodyExtractor(fast_mode=True, fmin="C3", fmax="C6")
    return _melody_extractor

def process_track(root_dir: str, rel_path: str):
    full_path = os.path.join(root_dir, rel_path)
    h = song_hash(rel_path)
    chord_file = CHORDS_DIR / f"{h}.json"
    melody_file = MELODIES_DIR / f"{h}.json"

    do_melody = _run_mode in ("both", "melody")
    do_chord = _run_mode in ("both", "chord")

    # 檢查是否已存在
    chord_done = True
    if do_chord and chord_file.is_file():
        try:
            with open(chord_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("chords"):
                chord_done = True
            else:
                chord_done = False
        except Exception:
            chord_done = False
    elif do_chord:
        chord_done = False

    melody_done = True
    if do_melody and melody_file.is_file():
        try:
            with open(melody_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "melody" in data:
                melody_done = True
            elif isinstance(data, list):
                melody_done = True
            else:
                melody_done = False
        except Exception:
            melody_done = False
    elif do_melody:
        melody_done = False

    if chord_done and melody_done:
        return "SKIP"

    # 預檢檔案大小（RAM 保護）
    try:
        fsize_mb = os.path.getsize(full_path) / (1024 * 1024)
        if fsize_mb > MAX_FILE_SIZE_MB:
            return f"SKIP_BIG ({fsize_mb:.0f}MB)"
    except OSError:
        return "ERROR_FS (file access)"

    # --- 跨進程/機器鎖機制 (Atomic Lock) ---
    lock_file = CHORDS_DIR / f"{h}.lock"
    try:
        # 使用 os.O_CREAT | os.O_EXCL 達成原子操作 (Atomic)建立鎖檔案
        # 若已有其他機器建立，會觸發 FileExistsError
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        # 發現鎖，檢查是否為陳舊鎖 (超過 2 小時沒更新，當作上一個工作站已經當機崩潰)
        try:
            mtime = os.path.getmtime(lock_file)
            if time.time() - mtime > 7200:
                Path(lock_file).touch()  # 強行接管，更新修改時間
            else:
                return "SKIP_LOCK"
        except Exception:
            return "SKIP_LOCK"
    except Exception:
        # 忽略其他網路磁碟報錯，繼續嘗試
        pass

    res_msgs = []
    try:
        # 1. 旋律擷取 (CPU 密集 - 無 GPU 鎖)
        if do_melody and not melody_done:
            try:
                extractor = _ensure_melody_extractor()
                melody = extractor.extract_melody(full_path)
                res = {"path": rel_path.replace("\\", "/"), "melody": melody}
                with open(melody_file, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                res_msgs.append(f"Melody:OK({len(melody)}notes)")
            except Exception as e:
                res_msgs.append(f"Melody:ERR({e})")

        # 2. 和弦偵測 (GPU 密集 - 受 Semaphore 限制)
        if do_chord and not chord_done:
            try:
                _lazy_import_chord()
                with _gpu_semaphore:
                    chords = detect_chords(full_path)

                if not chords:
                    res_msgs.append("Chord:NO_CHORDS")
                else:
                    from chord_detect import _key_from_chords
                    key = _key_from_chords(chords)
                    sheet = {
                        "path": rel_path.replace("\\", "/"),
                        "key": key,
                        "capo": 0,
                        "source": "btc_batch",
                        "chords": chords
                    }
                    with open(chord_file, "w", encoding="utf-8") as f:
                        json.dump(sheet, f, ensure_ascii=False, indent=2)
                    res_msgs.append(f"Chord:OK({key})")
            except Exception as e:
                res_msgs.append(f"Chord:ERR({e})")

    finally:
        # 結束後解除清理鎖機制
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass

    return " | ".join(res_msgs)

def main():
    global _gpu_semaphore, _progress_file, _stop_file, _run_mode

    parser = argparse.ArgumentParser(description="LiveChord 巨量 BTC + 旋律 雙引擎批次工作站")
    parser.add_argument("--root", type=str, required=True, help="音樂庫根目錄 (例如 Z:\\ 或 Z:\\Jam)")
    parser.add_argument("--workers", type=int, default=12, help="CPU 並發執行緒 (預設 12)")
    parser.add_argument("--gpu-concurrent", type=int, default=6, help="GPU 並發數 (預設 6, RTX 5080 可到 8)")
    parser.add_argument("--mode", type=str, default="both", choices=["both", "melody", "chord"],
                        help="執行模式: both=全部, melody=僅旋律(CPU), chord=僅和弦(GPU)")
    parser.add_argument("--progress-file", type=str, default=None, help="寫入進度的 JSON 檔案路徑")
    parser.add_argument("--stop-file", type=str, default=None, help="停止信號檔案路徑")
    args = parser.parse_args()

    _run_mode = args.mode

    if args.progress_file:
        _progress_file = Path(args.progress_file)
    if args.stop_file:
        _stop_file = Path(args.stop_file)

    _gpu_semaphore = threading.Semaphore(args.gpu_concurrent)

    # 初始化
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    MELODIES_DIR.mkdir(parents=True, exist_ok=True)
    root_dir = os.path.abspath(args.root)

    mode_labels = {"both": "和弦+旋律", "melody": "僅旋律 (CPU)", "chord": "僅和弦 (GPU)"}
    print(f"==================================================")
    print(f"🚀 LiveChord 批次工作站 啟動")
    print(f"📂 掃描目錄: {root_dir}")
    print(f"🎯 模式: {mode_labels[args.mode]}")
    print(f"⏩ 排除風格: {', '.join(SKIP_GENRES)}")
    print(f"🔥 CPU 執行緒: {args.workers} | GPU 並發: {args.gpu_concurrent}")
    print(f"==================================================")

    # 暖機 GPU 模型（melody-only 模式不需要）
    if args.mode in ("both", "chord"):
        _lazy_import_chord()
        print("⏳ 正在暖機 GPU 模型...")
        _load_model()
        from chord_detect import _device
        is_gpu = _device.type == "cuda"
        if is_gpu:
            print(f"✅ GPU 啟動成功: {torch.cuda.get_device_name(0)}")
        else:
            args.workers = min(args.workers, 4)
            print(f"⚠️ CPU 模式，為避免 OOM 並發自動降為 {args.workers}")
    else:
        print("⏩ 旋律模式，跳過 GPU 暖機")

    # 掃描檔案清單
    print("⏳ 正在建立播放清單，請稍候...")
    tasks = []

    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXT:
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_dir)

                if _is_skipped_genre(rel_path):
                    continue

                try:
                    fsize = os.path.getsize(full_path) / (1024 * 1024)
                    if fsize > MAX_FILE_SIZE_MB:
                        continue
                except OSError:
                    continue

                tasks.append(rel_path)

    total_tasks = len(tasks)
    print(f"🎵 共找到 {total_tasks} 首有效曲目準備進行雙重壓榨！")

    if total_tasks == 0:
        print("沒有任務需要執行，結束程式。")
        return

    # 開始平行處理
    start_time = time.time()
    started_at = datetime.now().isoformat()
    task_count = 0
    skip_count = 0
    err_count = 0
    process_count = 0
    current_file = ""
    stopped = False

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_path = {executor.submit(process_track, root_dir, p): p for p in tasks}

        for future in as_completed(future_to_path):
            # 檢查停止信號
            if _should_stop():
                stopped = True
                executor.shutdown(wait=False, cancel_futures=True)
                print("⛔ 收到停止信號，正在結束...")
                break

            path = future_to_path[future]
            current_file = path
            task_count += 1
            try:
                result = future.result()
                if result == "SKIP":
                    skip_count += 1
                elif result.startswith("SKIP_BIG") or result == "SKIP_LOCK":
                    skip_count += 1
                    _task_history.append(0) # 0 表示非實際耗時工作 (前線跳過)
                elif "ERR" in result:
                    err_count += 1
                    _task_history.append(1) # 1 表示實際有運算 (失敗也是算力)
                    log_msg = f"{path} -> {result}"
                    _add_log("ERR", log_msg)
                    with print_lock:
                        print(f"[{task_count}/{total_tasks}] ⚠️ {log_msg}")
                else:
                    process_count += 1
                    _task_history.append(1) # 1 表示實際有運算
                    log_msg = f"{path} -> {result}"
                    _add_log("OK", log_msg)
                    with print_lock:
                        print(f"[{task_count}/{total_tasks}] ✅ {log_msg}")
            except Exception as exc:
                err_count += 1
                _task_history.append(1)
                log_msg = f"嚴重錯誤 {path}: {exc}"
                _add_log("ERR", log_msg)
                with print_lock:
                    print(f"[{task_count}/{total_tasks}] 💥 {log_msg}")

            if task_count % 50 == 0:
                elapsed = time.time() - start_time
                actual = process_count + err_count  # 實際有處理的（排除 skip）
                if actual > 0:
                    real_tps = actual / elapsed
                    
                    # 取最近 2000 首做為預測根據，避免歷史包袱稀釋跳過率
                    if len(_task_history) > 0:
                        recent_work_ratio = sum(_task_history) / len(_task_history)
                    else:
                        recent_work_ratio = 1.0
                    
                    # 如果近期幾乎全是舊檔案，保守估計接下來還是有些微處女地，下限設 0.02
                    recent_work_ratio = max(recent_work_ratio, 0.02)
                    
                    remaining_all = total_tasks - task_count
                    remaining_process = remaining_all * recent_work_ratio
                    eta_sec = remaining_process / real_tps

                else:
                    scan_tps = task_count / elapsed if elapsed > 0 else 0
                    eta_sec = (total_tasks - task_count) / scan_tps if scan_tps > 0 else 0
                    real_tps = scan_tps
                eta_str = _format_duration(eta_sec)
                _ft = time.localtime(time.time() + eta_sec)
                finish_time = f"{_ft.tm_year}/{_ft.tm_mon:02d}/{_ft.tm_mday:02d} {_ft.tm_hour:02d}:{_ft.tm_min:02d}"
                with print_lock:
                    print(f"--- 系統狀態報告: 已掃描 {task_count}/{total_tasks} ({task_count/total_tasks:.1%}) | 實際處理: {actual} | 跳過: {skip_count} | 處理速度: {real_tps:.4f} 首/秒 | 剩餘: {eta_str} | 預計完成: {finish_time} ---")

                # 寫入進度檔
                _write_progress({
                    "job_type": f"super_{_run_mode}",
                    "status": "running",
                    "pid": os.getpid(),
                    "started_at": started_at,
                    "root_dir": root_dir,
                    "workers": args.workers,
                    "gpu_concurrent": args.gpu_concurrent,
                    "total": total_tasks,
                    "done": task_count,
                    "processed": process_count,
                    "skipped": skip_count,
                    "errors": err_count,
                    "current_file": current_file,
                    "real_tps": round(real_tps, 4),
                    "eta_seconds": int(eta_sec),
                    "estimated_finish": finish_time,
                    "recent_logs": list(_recent_logs),
                })

    end_time = time.time()
    total_time = end_time - start_time

    final_status = "stopped" if stopped else "completed"
    print(f"\n==================================================")
    print(f"🎉 任務{'已停止' if stopped else '完成'}！")
    print(f"⏳ 總耗時: {_format_duration(total_time)} ({total_time:.1f} 秒)")
    print(f"📊 統計結果:")
    print(f"   - 總掃描數: {total_tasks}")
    print(f"   - ✅ 實際處理: {process_count}")
    print(f"   - ⏭️ 跳過 (已存在): {skip_count}")
    print(f"   - ⚠️ 失敗/部分錯誤: {err_count}")
    print(f"==================================================")

    # 寫入最終進度
    _write_progress({
        "job_type": f"super_{_run_mode}",
        "status": final_status,
        "pid": os.getpid(),
        "started_at": started_at,
        "root_dir": root_dir,
        "workers": args.workers,
        "gpu_concurrent": args.gpu_concurrent,
        "total": total_tasks,
        "done": task_count,
        "processed": process_count,
        "skipped": skip_count,
        "errors": err_count,
        "current_file": "",
        "real_tps": 0,
        "eta_seconds": 0,
        "estimated_finish": "",
        "recent_logs": list(_recent_logs),
    })

if __name__ == "__main__":
    main()
