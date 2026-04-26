import os
import sys
import math
import shutil
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    import mido
except ImportError:
    print("Please install mido: pip install mido")
    sys.exit(1)

def score_midi_file(file_path: str) -> dict:
    """
    Score a MIDI file out of 100 based on its quality for arrangement training.
    """
    try:
        mid = mido.MidiFile(file_path, clip=True)
    except Exception as e:
        return {"file": file_path, "score": 0, "reason": f"Parse Error: {str(e)}"}

    channels_used = set()
    velocities = []
    total_notes = 0
    duration_ticks = 0
    has_drums = False

    for track in mid.tracks:
        for msg in track:
            duration_ticks += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                channels_used.add(msg.channel)
                velocities.append(msg.velocity)
                total_notes += 1
                if msg.channel == 9: # MIDI channel 10 is index 9 (Drums)
                    has_drums = True

    # 1. Duration check (Estimate)
    # Ticks to seconds depends on tempo, but we use a rough heuristic:
    # If a song has too few notes or is incredibly short/long, it's bad.
    score = 0
    reasons = []

    if total_notes < 50:
        return {"file": file_path, "score": 0, "reason": "Too few notes (<50)"}
    if total_notes > 50000:
        return {"file": file_path, "score": 0, "reason": "Black MIDI / Too dense (>50k notes)"}

    # 2. Track Diversity (Max 35 pts)
    # Want multiple channels. Single channel is bad for multi-track training.
    if len(channels_used) >= 4:
        score += 35
    elif len(channels_used) == 3:
        score += 25
    elif len(channels_used) == 2:
        score += 15
    else:
        reasons.append("Single channel")
        score += 0
        
    # 3. Has Drums (Max 15 pts)
    if has_drums:
        score += 15
    else:
        reasons.append("No drums")

    # 4. Velocity Dynamics (Max 30 pts)
    # Calculate standard deviation of velocities
    if len(velocities) > 0:
        mean_vel = sum(velocities) / len(velocities)
        variance = sum((v - mean_vel) ** 2 for v in velocities) / len(velocities)
        std_dev = math.sqrt(variance)
        
        if std_dev > 15:
            score += 30
        elif std_dev > 5:
            score += 15
        else:
            reasons.append("Robotic velocity (No dynamics)")
            score += 0
            
    # 5. Length/Density Bonus (Max 20 pts)
    # A healthy MIDI usually has between 500 and 10000 notes.
    if 500 <= total_notes <= 15000:
        score += 20
    else:
        score += 10
        
    return {
        "file": file_path,
        "score": score,
        "reason": ", ".join(reasons) if reasons else "Good"
    }

def process_one(file_path: str, source_dir: str, trash_dir: str, threshold: int) -> str:
    res = score_midi_file(file_path)
    score = res["score"]
    
    if score < threshold:
        # Move to trash
        try:
            rel_path = os.path.relpath(file_path, source_dir)
            trash_path = os.path.join(trash_dir, rel_path)
            
            # Create subdirectories in trash if needed
            os.makedirs(os.path.dirname(trash_path), exist_ok=True)
            
            # Handle duplicate names in trash safely
            if os.path.exists(trash_path):
                base, ext = os.path.splitext(trash_path)
                trash_path = f"{base}_{hash(file_path)}{ext}"
                
            shutil.move(file_path, trash_path)
            
            # Also move the accompanied .beat.json and .tmp.wav if they exist
            base_path = Path(file_path)
            for extra_ext in [".beat.json", ".tmp.wav"]:
                extra_file = base_path.with_suffix(extra_ext)
                if extra_file.exists():
                    extra_trash = str(Path(trash_path).with_suffix(extra_ext))
                    shutil.move(str(extra_file), extra_trash)
                    
            return f"TRASHED ({score} pts) - {res['reason']} -> {os.path.basename(file_path)}"
        except Exception as e:
            return f"ERROR moving {file_path}: {e}"
    else:
        return f"KEPT ({score} pts) - {os.path.basename(file_path)}"

def main():
    parser = argparse.ArgumentParser(description="MIDI Quality Scorer and Filter")
    parser.add_argument("--dir", type=str, default=r"F:\MIDI-Library", help="Source directory")
    parser.add_argument("--trash", type=str, default=r"F:\MIDI-Trash", help="Trash directory")
    parser.add_argument("--threshold", type=int, default=60, help="Minimum score to keep (0-100)")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel workers")
    
    args = parser.parse_args()
    
    source_dir = Path(args.dir)
    trash_dir = Path(args.trash)
    
    if not source_dir.exists():
        print(f"Error: Directory {source_dir} does not exist.")
        sys.exit(1)
        
    os.makedirs(trash_dir, exist_ok=True)
    
    print(f"Scanning {source_dir} for .mid files...")
    files = []
    for f in source_dir.rglob("*.mid"):
        if f.is_file():
            files.append(str(f))
            
    for f in source_dir.rglob("*.midi"):
        if f.is_file():
            files.append(str(f))
            
    total_files = len(files)
    print(f"Found {total_files} MIDI files to score.")
    print(f"Threshold: {args.threshold} points. Files below this will be moved to {trash_dir}")
    print(f"Using {args.workers} workers...")
    
    kept = 0
    trashed = 0
    errors = 0
    
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_one, f, str(source_dir), str(trash_dir), args.threshold): f for f in files}
        
        for i, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result()
                if result.startswith("TRASHED"):
                    trashed += 1
                elif result.startswith("KEPT"):
                    kept += 1
                else:
                    errors += 1
                    
                # Print progress every 100 files to avoid terminal spam
                if i % 100 == 0 or i == total_files:
                    sys.stdout.write(f"\rProgress: [{i}/{total_files}] Kept: {kept} | Trashed: {trashed} | Errors: {errors}")
                    sys.stdout.flush()
            except Exception as e:
                errors += 1
                print(f"\nWorker error: {e}")
                
    print("\n\n=== Quality Scoring Complete ===")
    print(f"Total processed: {total_files}")
    print(f"High Quality (Kept): {kept}")
    print(f"Low Quality (Trashed): {trashed}")
    print(f"Errors: {errors}")
    
if __name__ == "__main__":
    main()
