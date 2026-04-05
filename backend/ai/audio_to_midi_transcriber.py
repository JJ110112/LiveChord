import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class AudioToMidiTranscriber:
    """
    Uses Spotify's Basic Pitch to transcribe isolated audio stems into MIDI files.
    """
    def __init__(self, output_dir: str = 'transcribed_midi'):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # We lazily import basic_pitch because TensorFlow/Model loading can be heavy
        self._predict_and_save = None

    def _ensure_loaded(self):
        if self._predict_and_save is None:
            logger.info("Loading Basic Pitch model for the first time...")
            try:
                from basic_pitch.inference import predict_and_save
                from basic_pitch import build_icassp_2022_model_path, FilenameSuffix
                self._predict_and_save = predict_and_save
                # Use ONNX model — TF saved_model is broken on this Python 3.11 install
                self._model_path = build_icassp_2022_model_path(FilenameSuffix.onnx)
            except ImportError:
                logger.error("basic-pitch is not installed. Please pip install basic-pitch.")
                raise

    def transcribe(self, stem_path: str, stem_type: str = 'unknown') -> Optional[str]:
        """
        Transcribes the given audio file (stem) to a MIDI file.
        Returns the path to the generated MIDI file, or None on failure.
        """
        self._ensure_loaded()
        audio_file = Path(stem_path)
        
        if not audio_file.exists():
            logger.error(f"Stem file not found: {stem_path}")
            return None

        # Build output path: output_dir / {song_name}_{stem_type}.mid
        # basic-pitch save function typically creates <input_name>_basic_pitch.mid, 
        # but we can rename it later or save it correctly.
        out_path = self.output_dir / f"{audio_file.stem}_{stem_type}.mid"

        logger.info(f"Transcribing {stem_type} stem: {audio_file.name}")
        try:
            # Clean up stale output from previous runs (predict_and_save refuses to overwrite)
            default_outname = self.output_dir / f"{audio_file.stem}_basic_pitch.mid"
            if default_outname.exists():
                default_outname.unlink()

            self._predict_and_save(
                [str(audio_file)],
                str(self.output_dir),
                save_midi=True,
                sonify_midi=False,
                save_model_outputs=False,
                save_notes=False,
                model_or_model_path=self._model_path,
            )
            
            # predict_and_save automatically names the output file as inputfilename_basic_pitch.mid
            default_outname = self.output_dir / f"{audio_file.stem}_basic_pitch.mid"
            if default_outname.exists():
                # Rename to our structured format
                default_outname.replace(out_path)
                logger.info(f"Successfully transcribed to {out_path}")
                return str(out_path)
            else:
                logger.error(f"basic_pitch did not produce the expected MIDI file at {default_outname}")
                return None
                
        except Exception as e:
            logger.error(f"Transcription failed for {stem_path}: {e}")
            return None

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 2:
        transcriber = AudioToMidiTranscriber()
        out = transcriber.transcribe(sys.argv[1], sys.argv[2])
        print("Midi saved to:", out)
