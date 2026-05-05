import os
import sys
import glob
import json
import pretty_midi
import multiprocessing
from tqdm import tqdm

def simplify_to_playable_rh(midi_path, out_path, window_s=0.25, max_voices_below=3):
    try:
        pm = pretty_midi.PrettyMIDI(midi_path)
    except Exception as e:
        return False, f"Load Error: {str(e)}"
    
    all_notes = []
    for inst in pm.instruments:
        if not inst.is_drum:
            for note in inst.notes:
                all_notes.append(note)
                
    if not all_notes:
        return False, "No notes found in MIDI"
        
    # 群組化：將時間離散化為 window_s 的區塊
    bins = {}
    for note in all_notes:
        if note.pitch < 48: # 過濾 C3 以下的低音，只保留右手可能的音域
            continue
        bin_idx = int(note.start / window_s)
        if bin_idx not in bins:
            bins[bin_idx] = []
        bins[bin_idx].append(note)
        
    simplified_clusters = []
    
    for bin_idx in sorted(bins.keys()):
        cluster = bins[bin_idx]
        
        # 依音高降冪排序，取得該時窗內的最高音與伴奏音
        cluster.sort(key=lambda n: n.pitch, reverse=True)
        
        top_note = cluster[0]
        chord_notes = [top_note]
        
        for n in cluster[1:]:
            if n.pitch not in [cn.pitch for cn in chord_notes]:
                chord_notes.append(n)
                if len(chord_notes) >= 1 + max_voices_below:
                    break
                    
        # 計算此和弦/打擊點的平均開始時間與最大結束時間
        avg_start = sum(n.start for n in chord_notes) / len(chord_notes)
        max_end = max(n.end for n in chord_notes)
        
        event = {
            "time": float(round(avg_start, 3)),
            "duration": float(round(max(0.1, max_end - avg_start), 3)),
            "notes": [n.pitch for n in chord_notes]
        }
        simplified_clusters.append(event)
        
    # 儲存為 JSON
    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(simplified_clusters, f, ensure_ascii=False, separators=(',', ':'))
        return True, "Success"
    except Exception as e:
        return False, f"Write Error: {str(e)}"

def process_file(file_path):
    out_path = file_path.replace('.mid', '.playable_rh.json')
    # 避免重複執行
    if os.path.exists(out_path):
        # Optional: return True, "Already processed"
        pass 
    success, msg = simplify_to_playable_rh(file_path, out_path, window_s=0.25, max_voices_below=3)
    return success, msg

def main():
    target_dir = 'U:/MIDI-Library-Identified/Uncategorized/'
    if not os.path.exists(target_dir):
        print(f"Directory not found: {target_dir}")
        return
        
    midi_files = glob.glob(os.path.join(target_dir, '*.mid'))
    print(f"Found {len(midi_files)} MIDI files in {target_dir}.")
    
    if len(midi_files) == 0:
        return
        
    pool = multiprocessing.Pool(processes=max(1, multiprocessing.cpu_count() - 2))
    
    results = []
    for res in tqdm(pool.imap_unordered(process_file, midi_files), total=len(midi_files)):
        results.append(res)
        
    pool.close()
    pool.join()
    
    success_count = sum(1 for r, msg in results if r)
    print(f"Completed! Success: {success_count}/{len(midi_files)}")
    
    if success_count < len(midi_files):
        errors = [msg for r, msg in results if not r]
        print(f"Sample errors: {errors[:5]}")

if __name__ == '__main__':
    main()
