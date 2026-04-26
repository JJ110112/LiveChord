import os
import sys
import glob
import argparse
import subprocess
from pathlib import Path

def render_fluidsynth(midi_path: str, wav_path: str, soundfont: str = "FluidR3_GM_GS.sf2"):
    """
    Render MIDI to WAV using FluidSynth.
    FluidSynth must be installed and accessible in the system PATH.
    """
    if not os.path.exists(soundfont):
        # Fallback to look for it in the soundfonts directory
        sf_dir = Path(__file__).parent.parent / "soundfonts" / soundfont
        if sf_dir.exists():
            soundfont = str(sf_dir)
        else:
            print(f"WARNING: Soundfont '{soundfont}' not found. Rendering might fail.")

    cmd = [
        "fluidsynth",
        "-ni",
        "-r", "44100",
        "-F", wav_path,
        soundfont,
        midi_path
    ]
    
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        print("ERROR: fluidsynth is not installed or not in PATH.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"ERROR: fluidsynth failed with exit code {e.returncode}")
        return False

def render_keyscape(midi_path: str, wav_path: str):
    """
    Render MIDI to WAV using Keyscape (via a VST Host CLI).
    This is a placeholder interface, as it requires specific VST host software
    like MrsWatson or RenderMan to be configured.
    """
    # Example placeholder command using MrsWatson
    cmd = [
        "mrswatson",
        "--midi-file", midi_path,
        "--plugin", "Keyscape",
        "--output", wav_path
    ]
    
    print(f"Executing Keyscape render command: {' '.join(cmd)}")
    print("NOTE: Keyscape rendering requires a configured VST host. Falling back to FluidSynth for now...")
    # Simulate success or fallback
    return False

def main():
    parser = argparse.ArgumentParser(description="Render MIDI to WAV using FluidSynth or Keyscape")
    parser.add_argument("--input_dir", type=str, required=True, help="Directory containing MIDI files")
    parser.add_argument("--output_dir", type=str, required=True, help="Directory to save rendered WAV files")
    parser.add_argument("--engine", type=str, choices=["fluidsynth", "keyscape"], default="fluidsynth", help="Rendering engine to use")
    parser.add_argument("--soundfont", type=str, default="FluidR3_GM_GS.sf2", help="SoundFont file for FluidSynth")
    
    args = parser.parse_args()
    
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    
    if not in_dir.exists():
        print(f"Input directory does not exist: {in_dir}")
        sys.exit(1)
        
    out_dir.mkdir(parents=True, exist_ok=True)
    
    midi_files = list(in_dir.glob("*.mid")) + list(in_dir.glob("*.midi"))
    
    if not midi_files:
        print(f"No MIDI files found in {in_dir}")
        sys.exit(0)
        
    print(f"Found {len(midi_files)} MIDI files to render.")
    
    success_count = 0
    for midi_file in midi_files:
        wav_file = out_dir / (midi_file.stem + ".wav")
        print(f"Rendering {midi_file.name} -> {wav_file.name} using {args.engine}...")
        
        success = False
        if args.engine == "keyscape":
            success = render_keyscape(str(midi_file), str(wav_file))
            if not success:
                # Fallback to FluidSynth if Keyscape fails
                success = render_fluidsynth(str(midi_file), str(wav_file), args.soundfont)
        else:
            success = render_fluidsynth(str(midi_file), str(wav_file), args.soundfont)
            
        if success:
            success_count += 1
            print("  Done.")
        else:
            print("  Failed.")
            
    print(f"\nRendered {success_count}/{len(midi_files)} files successfully.")

if __name__ == "__main__":
    main()
