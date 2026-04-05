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

def run_hybrid_batch(data_dir: str = '../data'):
    """
    Finds all songs that have chords but no hybrid midis yet, and processes them.
    This is extremely slow (Demucs + Basic Pitch), so it runs as a separate background job.
    """
    separator = StemSeparator()
    transcriber = AudioToMidiTranscriber()
    sanitizer = MidiSanitizer()
    
    # We look for all chord sets
    chord_files = glob.glob(os.path.join(data_dir, '**', 'chords.json'), recursive=True)
    logger.info(f"Found {len(chord_files)} songs with chords.")
    
    processed = 0
    start_time = time.time()
    
    for chord_file in chord_files:
        song_dir = Path(chord_file).parent
        # Try to find original audio
        audio_files = list(song_dir.glob("*.mp3")) + list(song_dir.glob("*.wav"))
        if not audio_files:
            continue
            
        audio_path = audio_files[0]
        sanitized_bass_path = song_dir / "sanitized_bass.mid"
        
        # Skip if already processed
        if sanitized_bass_path.exists():
            continue
            
        logger.info(f"Processing Hybrid extraction for: {audio_path.name}")
        
        # 1. Stem Separation
        stems = separator.separate(str(audio_path))
        if not stems or 'bass' not in stems:
            logger.error("Failed to separate bass stem.")
            continue
            
        # 2. Transcription
        raw_bass_mid = transcriber.transcribe(stems['bass'], 'bass')
        if not raw_bass_mid:
            logger.error("Failed to transcribe bass stem.")
            continue
            
        # 3. Sanitization
        with open(chord_file, 'r', encoding='utf-8') as f:
            try:
                chords_data = json.load(f)
            except:
                logger.error(f"Failed to parse chords for {chord_file}")
                continue
                
        success = sanitizer.sanitize_bass(raw_bass_mid, chords_data, str(sanitized_bass_path))
        if success:
            logger.info(f"Successfully generated hybrid bass skeleton for {song_dir.name}")
            processed += 1
            
    elapsed = time.time() - start_time
    logger.info(f"Hybrid Batch completed. Processed {processed} songs in {elapsed:.1f}s.")

if __name__ == "__main__":
    run_hybrid_batch()
