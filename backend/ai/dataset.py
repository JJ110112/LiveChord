import os
import glob
import torch
from torch.utils.data import Dataset
from pathlib import Path

try:
    import mido
except ImportError:
    print("mido is required for the dataset. Install with: pip install mido")

try:
    from backend.ai.remi_tokenizer import RemiTokenizer
except ImportError:
    from remi_tokenizer import RemiTokenizer

class MidiArrangementDataset(Dataset):
    """
    Dataset for loading MIDI files and converting them into REMI tokens.
    """
    def __init__(self, data_dir: str, tokenizer: RemiTokenizer, max_seq_len: int = 1024):
        self.tokenizer = tokenizer
        self.max_seq_len = max_seq_len
        self.data_dir = data_dir
        
        # Load all midi files
        self.files = []
        for ext in ["*.mid", "*.midi"]:
            self.files.extend(glob.glob(os.path.join(data_dir, "**", ext), recursive=True))
            
        print(f"Dataset initialized with {len(self.files)} files.")

    def _parse_midi(self, file_path: str) -> list[str]:
        """
        Generic parser translating MIDI to REMI tokens.
        Note: This is a basic simplified version. A production version would need
        beat tracking (.beat.json) and accurate chord recognition algorithms.
        """
        try:
            mid = mido.MidiFile(file_path)
        except Exception:
            return []
            
        tokens = ["[BOS]"]
        
        # Sort all note_on events by absolute time
        events = []
        current_time = 0
        for track in mid.tracks:
            track_time = 0
            for msg in track:
                track_time += msg.time
                if msg.type == 'note_on' and msg.velocity > 0:
                    events.append({
                        "time": track_time,
                        "pitch": msg.note,
                        "channel": msg.channel
                    })
                    
        events.sort(key=lambda x: x["time"])
        
        ticks_per_beat = mid.ticks_per_beat
        ticks_per_bar = ticks_per_beat * 4 # Assume 4/4 time
        ticks_per_16th = ticks_per_beat / 4
        
        current_bar = -1
        
        for ev in events:
            bar = int(ev["time"] // ticks_per_bar)
            if bar > current_bar:
                tokens.append("[BAR]")
                # Dummy chord for now, since we don't have real chord extraction
                tokens.append("[CH_C:maj]") 
                current_bar = bar
                
            pos = int((ev["time"] % ticks_per_bar) // ticks_per_16th)
            pos = min(pos, 15)
            tokens.append(f"[POS_{pos}]")
            
            # Map channel to track role (simplified)
            if ev["channel"] == 9:
                tokens.append("[TRK_DRUM]")
            elif ev["channel"] == 2:
                tokens.append("[TRK_BASS]")
            else:
                tokens.append("[TRK_MELODY]")
                
            tokens.append(f"[PITCH_{ev['pitch']}]")
            # Default duration of 4 (one beat) for simplicity
            tokens.append("[DUR_4]") 
            
            if len(tokens) >= self.max_seq_len - 1:
                break
                
        tokens.append("[EOS]")
        return tokens

    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx):
        file_path = self.files[idx]
        tokens = self._parse_midi(file_path)
        
        # Fallback if empty or failed parsing
        if len(tokens) < 10:
            tokens = ["[BOS]", "[EOS]"]
            
        # Encode string tokens to IDs
        ids = self.tokenizer.encode(tokens)
        
        # Truncate if necessary
        if len(ids) > self.max_seq_len:
            ids = ids[:self.max_seq_len]
            
        return torch.tensor(ids, dtype=torch.long)

def collate_fn(batch):
    """
    Collate function for DataLoader.
    Pads the sequences and prepares src and target for autoregressive modeling.
    """
    pad_id = 0 # [PAD] is always index 0 in our RemiTokenizer
    
    # Pad sequences to the max length in this batch
    padded_batch = torch.nn.utils.rnn.pad_sequence(batch, batch_first=False, padding_value=pad_id)
    
    # For GPT style autoregressive training:
    # Source is all tokens except the last one
    # Target is all tokens except the first one (shifted by 1)
    src = padded_batch[:-1, :]
    target = padded_batch[1:, :]
    
    return src, target
