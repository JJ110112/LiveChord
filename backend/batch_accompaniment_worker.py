"""
LiveChord 離線伴奏與指法生成批次工廠
會將 data/chords/ 的樂譜與 data/melodies/ 的旋律合併，送入 AI 引擎演算。
並存入 data/accompaniments/<hash>_<level>_<style>.json 做快取。
"""

import os
import sys
import json
import time
import argparse
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 確保可以 import backend 下的模組
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai.accompaniment_generator import generate_accompaniment

CHORDS_DIR = Path(__file__).parent.parent / "data" / "chords"
MELODIES_DIR = Path(__file__).parent.parent / "data" / "melodies"
ACCOMP_DIR = Path(__file__).parent.parent / "data" / "accompaniments"

print_lock = threading.Lock()

def process_track(song_hash: str, levels: list, styles: list):
    chord_file = CHORDS_DIR / f"{song_hash}.json"
    melody_file = MELODIES_DIR / f"{song_hash}.json"
    
    # 確保兩者皆存在
    if not chord_file.is_file() or not melody_file.is_file():
        return f"SKIP_MISSING_DATA"
        
    try:
        with open(chord_file, "r", encoding="utf-8") as f:
            sheet_data = json.load(f)
        
        with open(melody_file, "r", encoding="utf-8") as f:
            melody_data = json.load(f)
            
        chords = sheet_data.get("chords", [])
        if not chords:
            return "SKIP_EMPTY_CHORDS"
            
        melody_list = melody_data.get("melody", []) if isinstance(melody_data, dict) else melody_data
        
        # Generator params
        key = sheet_data.get("key", "C")
        genre = sheet_data.get("genre", "pop")
        bpm = sheet_data.get("bpm", 120)
        
        results = []
        for level in levels:
            for style in styles:
                out_name = f"{song_hash}_{style}_{level}.json"
                out_path = ACCOMP_DIR / out_name
                
                if out_path.exists():
                    # 已經有了就跳過
                    results.append("S")
                    continue
                    
                # 開始生成
                acc_result = generate_accompaniment(
                    chords=chords,
                    genre=genre,
                    bpm=bpm,
                    style=style,
                    level=level,
                    melody=melody_list
                )
                
                with open(out_path, "w", encoding="utf-8") as fout:
                    json.dump(acc_result, fout, ensure_ascii=False)
                
                results.append("G")
                
        return "".join(results)
    except Exception as e:
        return f"ERR({str(e)})"


def main():
    parser = argparse.ArgumentParser(description="LiveChord 離線伴奏工廠")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--levels", type=str, default="L1,L2", help="Comma separated e.g. L1,L2,L3")
    parser.add_argument("--styles", type=str, default="Arpeggio,Block", help="Comma separated")
    
    args = parser.parse_args()
    
    ACCOMP_DIR.mkdir(parents=True, exist_ok=True)
    
    levels = [x.strip() for x in args.levels.split(",") if x.strip()]
    styles = [x.strip() for x in args.styles.split(",") if x.strip()]
    
    print("==================================================")
    print("🎹 LiveChord 離線伴奏與指法生成批次工廠")
    print(f"等級設定: {levels}")
    print(f"風格設定: {styles}")
    print(f"執行緒數: {args.workers}")
    print("==================================================")
    
    # 找尋所有可用的 hash
    hashes = []
    if CHORDS_DIR.exists():
        for f in os.listdir(CHORDS_DIR):
            if f.endswith(".json"):
                hashes.append(f.replace(".json", ""))
            
    total_tasks = len(hashes)
    print(f"找到 {total_tasks} 首曲目資料。準備開始...")
    
    if total_tasks == 0:
        return
        
    start_time = time.time()
    task_count = 0
    err_count = 0
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_hash = {executor.submit(process_track, h, levels, styles): h for h in hashes}
        
        for future in as_completed(future_to_hash):
            h = future_to_hash[future]
            task_count += 1
            try:
                res = future.result()
                if "ERR" in res:
                    err_count += 1
                with print_lock:
                    print(f"[{task_count}/{total_tasks}] {h} -> {res}")
            except Exception as e:
                err_count += 1
                with print_lock:
                    print(f"[{task_count}/{total_tasks}] {h} -> FATAL EXCEPTION: {e}")
                    
    print("\n完成！")
    print(f"總耗時: {time.time() - start_time:.1f} 秒")
    print(f"錯誤數: {err_count}")

if __name__ == "__main__":
    main()
