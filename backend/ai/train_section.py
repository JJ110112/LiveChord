import os
import glob
import json
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from pathlib import Path
from tqdm import tqdm

from section_model import BiLSTMSectionTagger, NUM_CLASSES, NUM_FEATURES, SECTION_TO_ID
from section_detect import _extract_midi_features, _estimate_bpm_and_window, NOTE_TO_SEMI, chord_to_degree, _chord_complexity, _classify_pop, _classify_simple
import re
import math

class SectionDataset(Dataset):
    def __init__(self, data_dir="V:/data", max_songs=40000):
        self.data_dir = Path(data_dir)
        self.chords_dir = self.data_dir / "chords"
        # Sharded layout: <chords_dir>/<bucket>/<hash>.json
        self.files = glob.glob(str(self.chords_dir / "*" / "*.json"))[:max_songs]
        if not self.files:
            self.files = glob.glob(str(self.chords_dir / "*.json"))[:max_songs]
        self.samples = []
        
        print(">> Generating training dataset... (Extracting V4 heuristic features & labels)")
        # For efficiency in this demo script, we process a limited subset sequentially
        # In a real cluster environment, we would parallelize this.
        for f in tqdm(self.files):
            try:
                with open(f, 'r', encoding='utf-8') as js_file:
                    data = json.load(js_file)
            except Exception:
                continue
                
            chords = data.get("chords", [])
            key = data.get("key", "C")
            song_hash = Path(f).stem
            
            if not chords or len(chords) < 8:
                continue
                
            features, tags = self._process_song(chords, key, song_hash)
            if features is not None and len(features) > 0:
                self.samples.append({"features": features, "tags": tags})

    def _process_song(self, chords, key, song_hash):
        key_semi = NOTE_TO_SEMI.get(key.rstrip("m"), 0)
        total_dur = chords[-1].get("end", chords[-1]["time"] + 4)
        
        unique_all = {c["chord"] for c in chords if c.get("chord") and c["chord"] != "N"}
        is_simple = len(unique_all) <= 5 and total_dur < 150
        
        melody_all, bass_all = _extract_midi_features(song_hash, str(self.data_dir))
        bpm, WINDOW = _estimate_bpm_and_window(chords, is_simple)
        
        n_windows = max(1, int(math.ceil(total_dur / WINDOW)))
        windows_data = []
        
        for i in range(n_windows):
            start = i * WINDOW
            end = min((i + 1) * WINDOW, total_dur)
            w_chords = [c for c in chords if start <= c["time"] < end]
            
            if not w_chords:
                # Pad empty window to maintain timeline
                windows_data.append({
                    "start": start, "end": end,
                    "degrees": [], "unique": set(), "complexity": 0, "density": 0,
                    "fingerprint": tuple(), "type": "intro",
                    "melody_density": 0, "melody_avg_pitch": 0, "bass_density": 0
                })
                continue
                
            raw_degrees = [chord_to_degree(c["chord"], key_semi) for c in w_chords]
            degrees = [re.sub(r'[0-9].*', '', d).replace("maj", "") for d in raw_degrees if d]
            
            condensed = [degrees[0]] if degrees else []
            for d in degrees[1:]:
                if d != condensed[-1]:
                    condensed.append(d)
                    
            w_mel = [m for m in melody_all if start <= m["time"] < end]
            w_bass = [b for b in bass_all if start <= b["time"] < end]
            mel_density = len(w_mel)
            mel_pitch = sum(m["pitch"] for m in w_mel) / mel_density if mel_density > 0 else 0
            
            windows_data.append({
                "start": start, "end": end,
                "degrees": degrees,
                "unique": set(degrees),
                "complexity": sum(_chord_complexity(c["chord"]) for c in w_chords) / max(len(w_chords), 1),
                "density": len(w_chords),
                "fingerprint": tuple(condensed),
                "type": "verse", # Will be overwritten by V4 heuristic
                "melody_density": mel_density,
                "melody_avg_pitch": mel_pitch,
                "bass_density": len(w_bass)
            })
            
        if is_simple:
            _classify_simple(windows_data)
        else:
            _classify_pop(windows_data)
            
        feature_sequence = []
        tag_sequence = []
        for w in windows_data:
            # NUM_FEATURES = 6
            feat = [
                w["density"],
                w["complexity"],
                len(w["unique"]),
                w["melody_density"],
                w["melody_avg_pitch"],
                w["bass_density"]
            ]
            feature_sequence.append(feat)
            t_id = SECTION_TO_ID.get(w["type"], SECTION_TO_ID["verse"])
            tag_sequence.append(t_id)
            
        if len(feature_sequence) == 0:
            return None, None
            
        return torch.tensor(feature_sequence, dtype=torch.float32), torch.tensor(tag_sequence, dtype=torch.long)

    def __len__(self):
        return len(self.samples)
        
    def __getitem__(self, idx):
        return self.samples[idx]

def collate_fn(batch):
    # Padding sequences for BiLSTM
    # sequences: list of (L, Features)
    # tags: list of (L,)
    features = [item["features"] for item in batch]
    tags = [item["tags"] for item in batch]
    
    # Pad sequences to max length in batch
    padded_features = nn.utils.rnn.pad_sequence(features, batch_first=True, padding_value=0.0)
    padded_tags = nn.utils.rnn.pad_sequence(tags, batch_first=True, padding_value=-1) # -1 goes to ignore_index
    
    return padded_features, padded_tags

def train():
    import sys
    # Hyperparameters
    batch_size = 32
    num_epochs = 5
    lr = 0.001
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Limits for speed. A true run would use max_songs=40000
    dataset = SectionDataset(max_songs=5000) 
    if len(dataset) == 0:
        print("No valid songs found for training.")
        return
        
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, collate_fn=collate_fn)
    
    model = BiLSTMSectionTagger().to(device)
    criterion = nn.CrossEntropyLoss(ignore_index=-1)
    optimizer = optim.Adam(model.parameters(), lr=lr)
    
    print("\n[Start Training BiLSTM Section Tagger]")
    for epoch in range(num_epochs):
        model.train()
        total_loss = 0
        correct = 0
        total_preds = 0
        
        pbar = tqdm(loader, desc=f"Epoch {epoch+1}/{num_epochs}")
        for x, y in pbar:
            x, y = x.to(device), y.to(device)
            
            optimizer.zero_grad()
            logits = model(x) # (B, Seq, NumClasses)
            
            # Flatten to compute loss
            logits_flat = logits.view(-1, NUM_CLASSES)
            y_flat = y.view(-1)
            
            loss = criterion(logits_flat, y_flat)
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            
            # Accuracy calc (excluding padded indices)
            preds = torch.argmax(logits_flat, dim=1)
            mask = y_flat != -1
            correct += (preds[mask] == y_flat[mask]).sum().item()
            total_preds += mask.sum().item()
            
            pbar.set_postfix({"loss": f"{loss.item():.4f}", "acc": f"{correct/max(total_preds, 1):.2f}"})
            
    print("\nTraining Complete! Saving section_detector.pth...")
    
    models_dir = Path("V:/data/models")
    models_dir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), models_dir / "section_detector.pth")
    print("Done!")

if __name__ == "__main__":
    train()
