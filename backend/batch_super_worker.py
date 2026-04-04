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
import hashlib
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 確保可以 import backend 下的模組
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chord_detect import detect_chords, detect_key, _load_model
from ai.melody_extractor import MelodyExtractor
import torch

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

# 資料庫位置
CHORDS_DIR = Path(__file__).parent.parent / "data" / "chords"
MELODIES_DIR = Path(__file__).parent.parent / "data" / "melodies"

# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _song_hash(rel_path: str) -> str:
    """與 auto_worker.py 一致的雜湊演算法"""
    rel_path = rel_path.replace("\\", "/")
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12]

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
# 核心任務處理
# ---------------------------------------------------------------------------
print_lock = threading.Lock()
_gpu_semaphore = threading.Semaphore(2)  # 最多 2 個同時用 GPU，避免 VRAM 爆
_melody_extractor = MelodyExtractor()    # 全域實例

def process_track(root_dir: str, rel_path: str):
    full_path = os.path.join(root_dir, rel_path)
    h = _song_hash(rel_path)
    chord_file = CHORDS_DIR / f"{h}.json"
    melody_file = MELODIES_DIR / f"{h}.json"

    # 檢查是否已存在
    chord_done = False
    if chord_file.is_file():
        try:
            with open(chord_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("chords"):
                chord_done = True
        except Exception:
            pass

    melody_done = False
    if melody_file.is_file():
        try:
            with open(melody_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "melody" in data:
                melody_done = True
            elif isinstance(data, list):
                melody_done = True
        except Exception:
            pass

    if chord_done and melody_done:
        return "SKIP"

    # 預檢檔案大小（RAM 保護）
    try:
        fsize_mb = os.path.getsize(full_path) / (1024 * 1024)
        if fsize_mb > MAX_FILE_SIZE_MB:
            return f"SKIP_BIG ({fsize_mb:.0f}MB)"
    except OSError:
        return "ERROR_FS (file access)"

    res_msgs = []

    # 1. 旋律擷取 (CPU 密集 - 無 GPU 鎖)
    if not melody_done:
        try:
            melody = _melody_extractor.extract_melody(full_path)
            res = {"path": rel_path.replace("\\", "/"), "melody": melody}
            with open(melody_file, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            res_msgs.append(f"Melody:OK({len(melody)}notes)")
        except Exception as e:
            res_msgs.append(f"Melody:ERR({e})")

    # 2. 和弦偵測 (GPU 密集 - 受 Semaphore 限制)
    if not chord_done:
        try:
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

    return " | ".join(res_msgs)

def main():
    parser = argparse.ArgumentParser(description="LiveChord 巨量 BTC + 旋律 雙引擎批次工作站")
    parser.add_argument("--root", type=str, required=True, help="音樂庫根目錄 (例如 Z:\ 或 Z:\Jam)")
    parser.add_argument("--workers", type=int, default=12, help="並發執行緒數量 (預設 12，因為旋律十分吃重 CPU)")
    args = parser.parse_args()

    # 初始化
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    MELODIES_DIR.mkdir(parents=True, exist_ok=True)
    root_dir = os.path.abspath(args.root)

    print(f"==================================================")
    print(f"🚀 LiveChord 雙引擎批次工作站 啟動")
    print(f"📂 掃描目錄: {root_dir}")
    print(f"⏩ 排除風格: {', '.join(SKIP_GENRES)}")
    print(f"🔥 並發執行緒: {args.workers} (CPU P-Cores 榨汁機準備就緒)")
    print(f"==================================================")

    # 暖機 GPU 模型
    print("⏳ 正在暖機 GPU 模型...")
    _load_model()
    from chord_detect import _device
    is_gpu = _device.type == "cuda"
    if is_gpu:
        print(f"✅ GPU 啟動成功: {torch.cuda.get_device_name(0)}")
    else:
        args.workers = min(args.workers, 4)
        print(f"⚠️ CPU 模式，為避免 OOM 並發自動降為 {args.workers}")

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
    task_count = 0
    skip_count = 0
    err_count = 0
    process_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_path = {executor.submit(process_track, root_dir, p): p for p in tasks}
        
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            task_count += 1
            try:
                result = future.result()
                if result == "SKIP" or result.startswith("SKIP_BIG"):
                    skip_count += 1
                elif "ERR" in result:
                    err_count += 1
                    with print_lock:
                        print(f"[{task_count}/{total_tasks}] ⚠️ {path} -> {result}")
                else:
                    process_count += 1
                    with print_lock:
                        print(f"[{task_count}/{total_tasks}] ✅ {path} -> {result}")
            except Exception as exc:
                err_count += 1
                with print_lock:
                    print(f"[{task_count}/{total_tasks}] 💥 嚴重錯誤 {path}: {exc}")
                    
            if task_count % 50 == 0:
                elapsed = time.time() - start_time
                tps = task_count / elapsed
                with print_lock:
                    print(f"--- 系統狀態報告: 已掃描 {task_count}/{total_tasks} ({task_count/total_tasks:.1%}) | 處理速度: {tps:.2f} 首/秒 ---")

    end_time = time.time()
    total_time = end_time - start_time
    
    print(f"\n==================================================")
    print(f"🎉 任務完成！")
    print(f"⏳ 總耗時: {total_time/3600:.2f} 小時 ({total_time:.1f} 秒)")
    print(f"📊 統計結果:")
    print(f"   - 總掃描數: {total_tasks}")
    print(f"   - ✅ 實際處理: {process_count}")
    print(f"   - ⏭️ 跳過 (已存在): {skip_count}")
    print(f"   - ⚠️ 失敗/部分錯誤: {err_count}")
    print(f"==================================================")

if __name__ == "__main__":
    main()
