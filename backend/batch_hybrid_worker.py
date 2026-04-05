import os
import glob
import logging
import json
from pathlib import Path
import time
from ai.stem_separator import StemSeparator
from ai.audio_to_midi_transcriber import AudioToMidiTranscriber
from ai.midi_sanitizer import MidiSanitizer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HybridWorker")

def run_hybrid_batch():
    """
    Finds all songs that have chords but no hybrid midis yet, and processes them.
    This is extremely slow (Demucs + Basic Pitch), so it runs as a separate background job.
    """
    # Always use the NAS data directory so LiveChord server can access the output
    data_dir = Path('W:/data')
        
    hybrid_bass_dir = data_dir / "hybrid_bass"
    hybrid_melody_dir = data_dir / "hybrid_melody"
    hybrid_bass_dir.mkdir(exist_ok=True)
    hybrid_melody_dir.mkdir(exist_ok=True)
        
    separator = StemSeparator()
    transcriber = AudioToMidiTranscriber()
    sanitizer = MidiSanitizer()
    
    # We look for all chord set files (hash.json)
    chord_files = list((data_dir / "chords").glob("*.json"))
    logger.info(f"Found {len(chord_files)} songs with chords.")
    
    # Need config context for resolve_path
    import sys
    sys.path.append(str(Path(__file__).parent))
    from config import resolve_path
    
    processed = 0
    start_time = time.time()
    
    for chord_path in chord_files:
        song_hash = chord_path.stem
        sanitized_bass_path = hybrid_bass_dir / f"{song_hash}.mid"
        sanitized_melody_path = hybrid_melody_dir / f"{song_hash}.mid"

        # Skip if both already processed
        if sanitized_bass_path.exists() and sanitized_melody_path.exists():
            continue
            
        with open(chord_path, 'r', encoding='utf-8') as f:
            try:
                chords_data = json.load(f)
            except:
                logger.error(f"Failed to parse chords for {chord_path}")
                continue
                
        rel_audio_path = chords_data.get("path")
        if not rel_audio_path:
            continue
            
        # Resolve absolute path to the audio file on NAS / Virtual drive
        audio_full_path = resolve_path(rel_audio_path)
        if not audio_full_path or not os.path.exists(audio_full_path):
            # 可能是移除了檔案或者尚未掛載
            continue
            
        logger.info(f"Processing Hybrid extraction for: {Path(audio_full_path).name}")
        
        # 1. Stem Separation (produces bass, vocals, drums, other)
        stems = separator.separate(audio_full_path)
        if not stems:
            logger.error(f"Failed to separate stems for {audio_full_path}.")
            continue

        chords_list = chords_data.get("chords", [])

        # 2a. Bass: Transcribe + Sanitize
        if not sanitized_bass_path.exists() and 'bass' in stems:
            raw_bass_mid = transcriber.transcribe(stems['bass'], 'bass')
            if raw_bass_mid:
                if sanitizer.sanitize_bass(raw_bass_mid, chords_list, str(sanitized_bass_path)):
                    logger.info(f"Generated hybrid bass for {song_hash}")

        # 2b. Melody (vocals): Transcribe + Sanitize
        if not sanitized_melody_path.exists() and 'vocals' in stems:
            raw_vocal_mid = transcriber.transcribe(stems['vocals'], 'melody')
            if raw_vocal_mid:
                if sanitizer.sanitize_melody(raw_vocal_mid, chords_list, str(sanitized_melody_path)):
                    logger.info(f"Generated hybrid melody for {song_hash}")

        if sanitized_bass_path.exists() and sanitized_melody_path.exists():
            processed += 1
            
    elapsed = time.time() - start_time
    logger.info(f"Hybrid Batch completed. Processed {processed} songs in {elapsed:.1f}s.")

if __name__ == "__main__":
    run_hybrid_batch()
