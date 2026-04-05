import os
import subprocess
import logging
import shutil
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class StemSeparator:
    """
    Uses Demucs to separate audio into stems (bass, drums, other, vocals).
    """
    def __init__(self, output_dir: str = 'separated'):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Using the fastest default model 'htdemucs' (or 'htdemucs_ft' for fine-tuned)
        self.model_name = 'htdemucs'

    def separate(self, audio_path: str) -> Optional[Dict[str, str]]:
        """
        Separates the given audio file and returns paths to the stems.
        Returns:
            Dict mapping stem names ('bass', 'vocals', 'drums', 'other') to their file paths,
            or None if separation failed.
        """
        audio_file = Path(audio_path)
        if not audio_file.exists():
            logger.error(f"Audio file not found: {audio_path}")
            return None

        logger.info(f"Starting Demucs source separation for: {audio_file.name}")
        
        # Demucs CLI command: python -m demucs -n htdemucs -o <output_dir> <audio_path>
        cmd = [
            "python", "-m", "demucs",
            "-n", self.model_name,
            "-o", str(self.output_dir),
            str(audio_file)
        ]

        try:
            # We run it synchronously as it's meant to be used by the background worker.
            process = subprocess.run(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True,
                check=True
            )
            logger.info("Separation completed successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Demucs separation failed: {e.stderr}")
            return None

        # Demucs saves files to output_dir / model_name / song_name / stem.wav
        # The song_name is usually the file name without extension
        song_name = audio_file.stem
        stem_dir = self.output_dir / self.model_name / song_name

        if not stem_dir.exists():
            logger.error(f"Expected output directory not found: {stem_dir}")
            return None

        stems = {}
        for target in ['bass', 'vocals', 'drums', 'other']:
            target_path = stem_dir / f"{target}.wav"
            if target_path.exists():
                stems[target] = str(target_path)
            else:
                logger.warning(f"Stem not found: {target_path}")

        return stems

if __name__ == "__main__":
    # Simple test stub
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1:
        separator = StemSeparator()
        result = separator.separate(sys.argv[1])
        print("Result:", result)
