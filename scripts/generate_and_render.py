import os
import sys
import argparse
import torch

try:
    import mido
except ImportError:
    print("mido is required. Install with: pip install mido")
    sys.exit(1)

# Ensure backend imports work
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.ai.remi_tokenizer import RemiTokenizer
from backend.ai.neural_arranger import NeuralArranger

def tokens_to_midi(tokens: list[str], output_path: str, ticks_per_beat=480):
    """
    Translates a sequence of RemiTokenizer string tokens back into a physical MIDI file.
    """
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    
    # 120 BPM tempo by default (500000 microseconds per beat)
    track.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    
    current_channel = 0
    ticks_per_16th = ticks_per_beat // 4
    
    # Simple state tracking for events
    current_time_ticks = 0
    last_event_time_ticks = 0
    current_bar_start_ticks = 0
    
    for token in tokens:
        if token == "[BAR]":
            current_bar_start_ticks += ticks_per_beat * 4 # advance one bar
        elif token.startswith("[POS_"):
            pos = int(token.split("_")[1][:-1])
            current_time_ticks = current_bar_start_ticks + (pos * ticks_per_16th)
        elif token.startswith("[TRK_"):
            # Very basic channel mapping
            trk = token.split("_")[1][:-1]
            if trk == "BASS": current_channel = 2
            elif trk == "DRUM": current_channel = 9
            else: current_channel = 0
        elif token.startswith("[PITCH_"):
            pitch = int(token.split("_")[1][:-1])
            
            # Simple note duration approximation for the E2E test
            delta = current_time_ticks - last_event_time_ticks
            if delta < 0: delta = 0
            
            # note on
            track.append(mido.Message('note_on', note=pitch, velocity=80, time=delta, channel=current_channel))
            # note off (duration of 1/16)
            track.append(mido.Message('note_off', note=pitch, velocity=0, time=ticks_per_16th, channel=current_channel))
            
            last_event_time_ticks = current_time_ticks + ticks_per_16th
            
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    mid.save(output_path)
    print(f"Generated MIDI saved to {output_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", type=str, default="BASS", help="Target role (BASS, MELODY, DRUM, etc)")
    parser.add_argument("--chord", type=str, default="[CH_C:maj]", help="Constrain decoding to this chord")
    parser.add_argument("--out_mid", type=str, default="eval_output/generated.mid", help="Output MIDI file")
    parser.add_argument("--out_wav", type=str, default="eval_output/generated.wav", help="Output WAV file")
    
    args = parser.parse_args()
    
    print(f"=== E2E Generation Pipeline ===")
    
    tokenizer = RemiTokenizer()
    # The actual saved model from training
    model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'models', 'neural_arranger_phase2.pt'))
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    if os.path.exists(model_path):
        print(f"Loading trained model from {model_path}...")
        try:
            model = NeuralArranger.load_from_checkpoint(model_path, device=device)
        except Exception as e:
            print(f"Failed to load checkpoint: {e}. Using untrained model.")
            model = NeuralArranger(vocab_size=tokenizer.vocab_size).to(device)
    else:
        print(f"No trained model found at {model_path}. Using an untrained model (random weights) for demonstration.")
        model = NeuralArranger(vocab_size=tokenizer.vocab_size).to(device)
        
    model.eval()
    
    prefix = ["[BOS]", f"[TARGET_{args.role}]", "<GENERATE>"]
    print(f"Prefix tokens: {prefix}")
    prefix_ids = tokenizer.encode(prefix)
    
    print(f"Generating tokens constrained to {args.chord}...")
    generated_ids = model.generate(
        prefix_tokens=prefix_ids,
        max_len=100, # Generate 100 tokens
        tokenizer=tokenizer,
        current_chord=args.chord,
        temperature=1.0
    )
    
    generated_tokens = tokenizer.decode(generated_ids)
    print(f"Generated {len(generated_tokens)} tokens.")
    
    # Convert to MIDI
    print("Converting tokens to MIDI...")
    tokens_to_midi(generated_tokens, args.out_mid)
    
    # Try rendering to WAV
    print(f"Triggering Phase 3 Renderer...")
    render_script = os.path.join(os.path.dirname(__file__), 'render_midi_to_wav.py')
    if os.path.exists(render_script):
        # render_midi_to_wav.py expects directories, not individual files
        in_dir = os.path.dirname(os.path.abspath(args.out_mid))
        out_dir = os.path.dirname(os.path.abspath(args.out_wav))
        
        ret = os.system(f'python "{render_script}" --input_dir "{in_dir}" --output_dir "{out_dir}"')
        if ret == 0:
            print(f"Success! Renderer processed the directory: {out_dir}")
        else:
            print(f"Renderer exited with code {ret}")
    else:
        print(f"Renderer script not found at {render_script}. Skipping WAV generation.")

if __name__ == "__main__":
    main()
