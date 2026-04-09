import os
import re
import subprocess
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class StemSeparator:
    """
    Uses Demucs to separate audio into stems (bass, drums, other, vocals).
    """
    def __init__(self, output_dir: str = None):
        if output_dir is None:
            output_dir = os.environ.get('LIVECHORD_SEPARATED_DIR', 'W:/data/separated')
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

        # Demucs 用 audio_file.stem 作為輸出子目錄名稱。
        # Windows 不允許目錄名以 . 或空白結尾（例如「奇跡を望むなら...」），
        # 也不允許含有 <>:"/\|?* 等字元。
        # 如果檔名有這些問題，先複製到安全名稱的暫存檔再呼叫 Demucs。
        safe_stem = re.sub(r'[<>:"/\\|?*]', '_', audio_file.stem).rstrip('. ')
        tmp_copy = None
        input_file = audio_file
        if safe_stem != audio_file.stem:
            tmp_dir = self.output_dir / "_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_copy = tmp_dir / (safe_stem + audio_file.suffix)
            shutil.copy2(str(audio_file), str(tmp_copy))
            input_file = tmp_copy
            logger.info(f"  Unsafe filename, using temp copy: {safe_stem}")

        # Use the patched demucs runner that bypasses torchaudio.save and uses soundfile directly
        patched_runner = Path(__file__).parent / "run_demucs_patched.py"
        cmd = [
            "python", str(patched_runner),
            "-n", self.model_name,
            "-o", str(self.output_dir),
            str(input_file)
        ]

        # Set up environment variables to fix Windows-specific bugs:
        # 3. PYTHONIOENCODING=utf-8 ensures stdout directly uses utf-8
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["TORCHAUDIO_BACKEND"] = "soundfile"
        
        try:
            # We run it synchronously as it's meant to be used by the background worker.
            process = subprocess.run(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True,
                check=True,
                env=env,
                encoding='utf-8' # Force read output as utf-8
            )
            logger.info("Separation completed successfully.")
        except subprocess.CalledProcessError as e:
            # Only show the last meaningful error line, not tqdm progress bars
            stderr = (e.stderr or "").strip()
            err_lines = [l for l in stderr.splitlines() if l.strip() and not l.strip().startswith(('%', '|', '\r'))]
            last_err = err_lines[-1] if err_lines else stderr[-200:]
            logger.error(f"Demucs failed: {last_err}")
            return None

        # 清理暫存複製
        if tmp_copy and tmp_copy.exists():
            tmp_copy.unlink(missing_ok=True)

        # Demucs saves files to output_dir / model_name / song_name / stem.wav
        # song_name 使用 input_file.stem（可能是安全化後的名稱）
        song_name = input_file.stem
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
