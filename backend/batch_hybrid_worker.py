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
    skipped = 0
    start_time = time.time()
    total_files = len(chord_files)

    # Count how many need processing (not already done)
    pending_files = []
    for cp in chord_files:
        sh = cp.stem
        if (hybrid_bass_dir / f"{sh}.mid").exists() and (hybrid_melody_dir / f"{sh}.mid").exists():
            skipped += 1
        else:
            pending_files.append(cp)
    total_pending = len(pending_files)
    logger.info(f"需處理: {total_pending} 首 (已跳過: {skipped} 首)")

    for idx, chord_path in enumerate(pending_files, 1):
        song_hash = chord_path.stem
        sanitized_bass_path = hybrid_bass_dir / f"{song_hash}.mid"
        sanitized_melody_path = hybrid_melody_dir / f"{song_hash}.mid"

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
            continue

        filename = Path(audio_full_path).name
        pct = idx / total_pending * 100
        elapsed = time.time() - start_time
        if processed > 0:
            avg_sec = elapsed / processed
            remaining_sec = avg_sec * (total_pending - idx)
            eta_str = _format_duration(remaining_sec)
            _ft = time.localtime(time.time() + remaining_sec)
            finish_time = f"{_ft.tm_year}/{_ft.tm_mon:02d}/{_ft.tm_mday:02d} {_ft.tm_hour:02d}:{_ft.tm_min:02d}"
            logger.info(f"[{idx}/{total_pending}] ({pct:.1f}%) {filename} | 剩餘: {eta_str} | 預計完成: {finish_time}")
        else:
            logger.info(f"[{idx}/{total_pending}] ({pct:.1f}%) {filename}")

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
                    logger.info(f"  ✅ Bass MIDI: {song_hash}")

        # 2b. Melody (vocals): Transcribe + Sanitize
        if not sanitized_melody_path.exists() and 'vocals' in stems:
            raw_vocal_mid = transcriber.transcribe(stems['vocals'], 'melody')
            if raw_vocal_mid:
                if sanitizer.sanitize_melody(raw_vocal_mid, chords_list, str(sanitized_melody_path)):
                    logger.info(f"  ✅ Melody MIDI: {song_hash}")

        if sanitized_bass_path.exists() and sanitized_melody_path.exists():
            processed += 1

    elapsed = time.time() - start_time
    logger.info(f"\n==================================================")
    logger.info(f"🎉 Hybrid Batch 完成！")
    logger.info(f"⏳ 總耗時: {_format_duration(elapsed)} ({elapsed:.1f} 秒)")
    logger.info(f"📊 統計: 處理 {processed} 首 | 跳過 {skipped} 首 | 總計 {total_files} 首")
    logger.info(f"==")

if __name__ == "__main__":
    run_hybrid_batch()
