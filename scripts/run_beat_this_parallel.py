import os
import sys
import json
import time
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

_worker_predictor = None

def worker_init(device: str):
    global _worker_predictor
    try:
        from beat_this.inference import File2Beats
        import torch
        _worker_predictor = File2Beats(checkpoint_path="final0", device=device, float16=True)
    except Exception as e:
        print(f"Worker initialization failed: {e}")

def _process_one(file_path: str, force: bool) -> str:
    global _worker_predictor
    path = Path(file_path)
    output_json = path.with_suffix(".beat.json")
    
    if output_json.exists() and not force:
        return f"SKIP - {output_json.name} exists"

    if _worker_predictor is None:
        return "ERROR - predictor not loaded in worker"

    try:
        # Load the model directly in the worker process
        # so each worker has its own instance
        predictor = _worker_predictor
        import torch
        import subprocess
        
        # NOTE: if the input is MIDI, we might need to synthesize audio first
        # since beat_this expects audio. If F:\MIDI-Library has accompanying .mp3/.wav
        # we process that instead. For now we assume the input path is valid for beat_this.
        # Let's assume it can read the file directly, or we can use fluidsynth if it's .mid
        
        # If it's a .mid file, we need to render it to a temporary .wav first
        audio_to_process = file_path
        tmp_wav = None
        if path.suffix.lower() in [".mid", ".midi"]:
            tmp_wav = path.with_suffix(".tmp.wav")
            sf2_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "soundfonts", "FluidR3_GM_GS.sf2")
            
            try:
                # Use subprocess with a 60-second timeout to prevent malformed MIDIs from exhausting RAM
                cmd = ["fluidsynth", "-ni", "-r", "44100", "-F", str(tmp_wav), sf2_path, file_path]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            except subprocess.TimeoutExpired:
                if os.path.exists(tmp_wav): os.remove(tmp_wav)
                return f"SKIP - FluidSynth timeout (possibly malformed MIDI): {file_path}"
            except subprocess.CalledProcessError:
                if os.path.exists(tmp_wav): os.remove(tmp_wav)
                return f"ERROR - FluidSynth failed (Memory allocation or other error): {file_path}"
            
            audio_to_process = str(tmp_wav)
            if not os.path.exists(audio_to_process):
                return f"ERROR - failed to synthesize MIDI to audio: {file_path}"

        beats_arr, downbeats_arr = predictor(audio_to_process)
        torch.cuda.empty_cache() # Clear VRAM after each prediction
        beats = [float(x) for x in beats_arr]
        downbeats = [float(x) for x in downbeats_arr]
        
        res = {
            "source_file": file_path,
            "beats": beats,
            "downbeats": downbeats,
            "beats_source": "beat_this",
            "timestamp": time.time()
        }
        
        tmp_out = output_json.with_suffix(".tmp.json")
        with open(tmp_out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
            
        os.replace(tmp_out, output_json)
        
        if tmp_wav and os.path.exists(tmp_wav):
            os.remove(tmp_wav)
            
        return f"OK - {len(beats)} beats, {len(downbeats)} downbeats"
        
    except Exception as e:
        return f"ERROR - {type(e).__name__}: {str(e)}"

def main():
    parser = argparse.ArgumentParser(description="Parallel processing of beat_this on F:\\MIDI-Library")
    parser.add_argument("--dir", type=str, default=r"F:\MIDI-Library", help="Target directory")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--device", type=str, default="cuda", help="cuda / cpu")
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs")
    parser.add_argument("--limit", type=int, default=0, help="Max files to process (for testing)")
    
    args = parser.parse_args()
    target_dir = Path(args.dir)
    
    if not target_dir.exists():
        print(f"Error: Directory {target_dir} does not exist.")
        sys.exit(1)
        
    print(f"Scanning {target_dir} for audio/MIDI files...")
    # Supporting standard audio and midi extensions
    exts = [".mp3", ".wav", ".flac", ".m4a", ".mid", ".midi"]
    files = []
    for f in target_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in exts:
            if f.name.endswith(".tmp.wav"):
                continue
            files.append(str(f))
            
    print(f"Found {len(files)} files.")
    
    if args.limit > 0:
        files = files[:args.limit]
        print(f"Limiting to {len(files)} files.")

    print(f"Starting {args.workers} workers using device={args.device}...")
    start_time = time.time()
    
    success = 0
    skipped = 0
    errors = 0
    
    # EWMA variables for ETA
    v_last = None
    alpha = 0.1
    last_update_t = start_time
    last_update_i = 0
    warmup_items = 50

    # ProcessPoolExecutor for true parallelism
    with ProcessPoolExecutor(max_workers=args.workers, initializer=worker_init, initargs=(args.device,)) as executor:
        futures = {executor.submit(_process_one, f, args.force): f for f in files}
        
        for i, future in enumerate(as_completed(futures), 1):
            curr_t = time.time()
            
            # Update EWMA once per second to smooth out concurrency burst noise
            if curr_t - last_update_t >= 1.0 or i == len(files):
                dt = curr_t - last_update_t
                di = i - last_update_i
                v_instant = di / max(0.001, dt)  # items per second
                
                if v_last is None:
                    # Initialize with global average for the first chunk (solves resume problem)
                    v_last = i / max(0.001, curr_t - start_time)
                else:
                    v_last = alpha * v_instant + (1 - alpha) * v_last
                    
                last_update_t = curr_t
                last_update_i = i

            if v_last is None or i < warmup_items:
                eta_str = "Estimating..."
                speed_str = "..."
            else:
                speed_str = f"{v_last:.2f} it/s"
                if v_last > 0:
                    eta_sec = (len(files) - i) / v_last
                    m, s = divmod(int(eta_sec), 60)
                    h, m = divmod(m, 60)
                    eta_str = f"ETA {h}h{m}m" if h > 0 else f"ETA {m}m{s}s"
                else:
                    eta_str = "ETA --"

            try:
                res = future.result()
                if res.startswith("OK"):
                    success += 1
                elif res.startswith("SKIP"):
                    skipped += 1
                else:
                    errors += 1
                print(f"[{i}/{len(files)}] {os.path.basename(futures[future])}: {res} ({speed_str}, {eta_str})")
            except Exception as exc:
                errors += 1
                print(f"[{i}/{len(files)}] {os.path.basename(futures[future])} generated an exception: {exc}")

    elapsed = time.time() - start_time
    print("\n=== Summary ===")
    print(f"Processed: {len(files)}")
    print(f"Success: {success}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")
    print(f"Total time: {elapsed:.1f}s (Avg {elapsed/max(1, success):.1f}s/file)")

if __name__ == "__main__":
    main()
