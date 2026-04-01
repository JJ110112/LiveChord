"""
LiveChord 巨量音訊批次和弦轉換工作站 (RTX 5080 全速版)
專門設計給 i9-13900KF + RTX 5080 環境使用。
利用多執行緒平行處理 CPU 音訊載入 (librosa)，並送交 GPU (PyTorch) 進行高速 BTC 推論。
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
import torch

# ---------------------------------------------------------------------------
# 設定區
# ---------------------------------------------------------------------------

# 必須排除的資料夾清單 (Blacklist)
SKIP_GENRES = {
    "classics", "classical", "symphony",
    "sleep", "relax", "meditation",
    "electronic dance music", "edm", "techno",
    "other"
}

# 支援的音檔格式
SUPPORTED_EXT = {".flac", ".mp3", ".wav"}

# 資料庫位置
CHORDS_DIR = Path(__file__).parent.parent / "data" / "chords"

# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _song_hash(rel_path: str) -> str:
    """與 auto_worker.py 一致的雜湊演算法"""
    # 確保路徑斜線一致 (Windows \ 轉為 /)
    rel_path = rel_path.replace("\\", "/")
    return hashlib.md5(rel_path.encode("utf-8")).hexdigest()[:12]

def _is_skipped_genre(rel_path: str) -> bool:
    """判斷該路徑是否屬於黑名單曲風"""
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) > 1:
        # 假設第一層資料夾是曲風 (Genre)
        genre = parts[0].lower()
        if genre in SKIP_GENRES:
            return True
    return False

# ---------------------------------------------------------------------------
# 核心任務處理
# ---------------------------------------------------------------------------
# 使用 threading.Lock 確保寫入 JSON 和 Print 時不會打架
print_lock = threading.Lock()

def process_track(root_dir: str, rel_path: str):
    full_path = os.path.join(root_dir, rel_path)
    # 產出 JSON 的目標位置
    h = _song_hash(rel_path)
    out_file = CHORDS_DIR / f"{h}.json"

    # 如果已經有和弦譜，檢查來源是否為 BTC 或 MIDI
    if out_file.is_file():
        try:
            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 只有當沒有來源或是 chordify 時才考慮覆寫（這裡為求速度只要檔案存在就跳過）
            if data.get("chords"):
                return "SKIP"
        except Exception:
            pass  # JSON 壞掉就重做

    try:
        # librosa.load (CPU) + model inference (GPU)
        chords = detect_chords(full_path)
        
        if not chords:
            return "NO_CHORDS"
            
        # 從剛取得的和弦立刻推導 key，避免做兩次 GPU 推論！
        from chord_detect import _key_from_chords
        key = _key_from_chords(chords)

        # 封裝結果
        sheet = {
            "path": rel_path.replace("\\", "/"),
            "key": key,
            "capo": 0,
            "source": "btc_batch",
            "chords": chords
        }
        
        # 覆寫/新建 JSON
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(sheet, f, ensure_ascii=False, indent=2)

        return f"OK ({key}, {len(chords)} chords)"

    except Exception as e:
        return f"ERROR ({e})"

def main():
    parser = argparse.ArgumentParser(description="LiveChord 巨量 BTC 批次工作站")
    parser.add_argument("--root", type=str, required=True, help="音樂庫根目錄 (例如 Z:\ 或 Z:\Jam)")
    parser.add_argument("--workers", type=int, default=8, help="並發執行緒數量 (預設 8，配合 i9 處理器)")
    args = parser.parse_args()

    # 初始化
    CHORDS_DIR.mkdir(parents=True, exist_ok=True)
    root_dir = os.path.abspath(args.root)

    print(f"==================================================")
    print(f"🚀 LiveChord RTX 5080 批次工作站 啟動")
    print(f"📂 掃描目錄: {root_dir}")
    print(f"⏩ 排除風格: {', '.join(SKIP_GENRES)}")
    print(f"🔥 並發執行緒: {args.workers}")
    print(f"==================================================")

    # 先進行一次假推論，強制載入 BTC 模型進 GPU VRAM，防止多執行緒同時競爭 Load Weights
    print("⏳ 正在暖機 GPU 模型...")
    _load_model()
    if torch.cuda.is_available():
        print(f"✅ GPU 啟動成功: {torch.cuda.get_device_name(0)}")
    else:
        print(f"⚠️ 警告：未偵測到 GPU，將使用純 CPU 算力！這可能需要數個月。")

    # 掃描檔案清單
    print("⏳ 正在建立播放清單，請稍候...")
    tasks = []
    
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXT:
                full_path = os.path.join(dirpath, fname)
                # 取得相對路徑
                rel_path = os.path.relpath(full_path, root_dir)
                
                # 檢查排除清單
                if _is_skipped_genre(rel_path):
                    continue
                    
                tasks.append(rel_path)

    total_tasks = len(tasks)
    print(f"🎵 共找到 {total_tasks} 首有效曲目準備進行壓榨！")
    
    if total_tasks == 0:
        print("沒有任務需要執行，結束程式。")
        return

    # 開始平行處理
    start_time = time.time()
    task_count = 0
    skip_count = 0
    err_count = 0
    ok_count = 0

    # ThreadPoolExecutor 非常適合 I/O 與 CPU 混合的工作 (i9 解碼音樂 -> 送入 GPU)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        # submit tasks
        future_to_path = {executor.submit(process_track, root_dir, p): p for p in tasks}
        
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            task_count += 1
            try:
                result = future.result()
                if result == "SKIP":
                    skip_count += 1
                elif result.startswith("ERROR"):
                    err_count += 1
                    with print_lock:
                        print(f"[{task_count}/{total_tasks}] ❌ {path} -> {result}")
                else:
                    ok_count += 1
                    with print_lock:
                        print(f"[{task_count}/{total_tasks}] ✅ {path} -> {result}")
            except Exception as exc:
                err_count += 1
                with print_lock:
                    print(f"[{task_count}/{total_tasks}] 💥 嚴重錯誤 {path}: {exc}")
                    
            # 簡單進度回報
            if task_count % 100 == 0:
                elapsed = time.time() - start_time
                tps = task_count / elapsed
                with print_lock:
                    print(f"--- 系統狀態報告: 已處理 {task_count}/{total_tasks} ({task_count/total_tasks:.1%}) | 速度: {tps:.2f} 首/秒 ---")

    end_time = time.time()
    total_time = end_time - start_time
    
    print(f"\n==================================================")
    print(f"🎉 任務完成！")
    print(f"⏳ 總耗時: {total_time/3600:.2f} 小時 ({total_time:.1f} 秒)")
    print(f"📊 統計結果:")
    print(f"   - 總任務數: {total_tasks}")
    print(f"   - ✅ 成功偵測: {ok_count}")
    print(f"   - ⏭️ 跳過 (已存在): {skip_count}")
    print(f"   - ❌ 失敗錯誤: {err_count}")
    print(f"==================================================")

if __name__ == "__main__":
    main()
