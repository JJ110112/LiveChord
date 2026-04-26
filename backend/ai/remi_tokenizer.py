import math

# Standard 12 pitches for chords/keys
PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
CHORD_TYPES = ["maj", "min", "dim", "aug", "7", "maj7", "min7", "sus4", "sus2", "N"]

class RemiTokenizer:
    """
    Multi-track REMI Tokenizer for Arrangement AI.
    Converts MIDI + Beat + Chord data into a 1D token sequence.
    """
    def __init__(self):
        self.vocab2id = {}
        self.id2vocab = {}
        self._build_vocab()
        
    def _add_token(self, token: str):
        if token not in self.vocab2id:
            idx = len(self.vocab2id)
            self.vocab2id[token] = idx
            self.id2vocab[idx] = token

    def _build_vocab(self):
        # Special Tokens
        self._add_token("[PAD]")
        self._add_token("[BOS]")
        self._add_token("[EOS]")
        self._add_token("[MASK]")
        
        # Structure Tokens
        self._add_token("[BAR]")
        for i in range(16): # 1/16 grid, 16 positions per bar (4/4 time signature assumed)
            self._add_token(f"[POS_{i}]")
            
        # Rhythm Embeddings
        self._add_token("[BEAT_STRONG]") # e.g. downbeat, or beats 1, 3
        self._add_token("[BEAT_WEAK]")   # e.g. beats 2, 4 or offbeats
            
        # Track / Role Tokens
        for track in ["MELODY", "BASS", "SAX", "FLUTE", "OCARINA", "DRUM", "CHORDS"]:
            self._add_token(f"[TRK_{track}]")
            
        # Target Conditioning Tokens (Prefix Roles)
        for role in ["BASS", "SAX", "FLUTE", "OCARINA", "DRUM"]:
            self._add_token(f"[TARGET_{role}]")
            
        self._add_token("<GENERATE>")
            
        # Chord Tokens
        for root in PITCH_CLASSES:
            for c_type in CHORD_TYPES:
                self._add_token(f"[CH_{root}:{c_type}]")
                
        # Pitch & Duration Tokens
        for pitch in range(21, 109): # Standard piano range A0 to C8
            self._add_token(f"[PITCH_{pitch}]")
            
        for dur in range(1, 65): # 1 to 64 ticks (in 16th notes resolution, up to 4 bars)
            self._add_token(f"[DUR_{dur}]")
            
        self.vocab_size = len(self.vocab2id)
        
    def encode(self, tokens: list[str]) -> list[int]:
        return [self.vocab2id.get(tok, self.vocab2id["[PAD]"]) for tok in tokens]
        
    def decode(self, ids: list[int]) -> list[str]:
        return [self.id2vocab.get(idx, "[PAD]") for idx in ids]
        
    @staticmethod
    def quantize_time(time_seconds: float, beat_duration: float, grid_size: int = 4) -> int:
        """
        Quantize continuous time into 1/16 grid positions relative to the beat.
        grid_size = 4 means 4 positions per beat (16th notes).
        """
        tick_duration = beat_duration / grid_size
        return min(max(round(time_seconds / tick_duration), 0), 15)

    @staticmethod
    def normalize_key(pitch: int, current_key: str, target_key: str = "C") -> int:
        """
        Transposes a pitch from current_key to target_key (C major or A minor).
        Returns the new pitch.
        """
        # Very simplified transpose logic
        try:
            current_root = current_key.replace('m', '')
            target_root = target_key.replace('m', '')
            if current_root not in PITCH_CLASSES or target_root not in PITCH_CLASSES:
                return pitch
                
            c_idx = PITCH_CLASSES.index(current_root)
            t_idx = PITCH_CLASSES.index(target_root)
            diff = t_idx - c_idx
            
            # Keep transposition within a tight range to avoid extremely high/low notes
            if diff > 6: diff -= 12
            if diff < -6: diff += 12
            
            new_pitch = pitch + diff
            # Clamp to piano range
            return max(21, min(108, new_pitch))
        except:
            return pitch

    def get_chord_notes(self, chord_token: str) -> list[int]:
        """
        Given a chord token like [CH_C:maj], returns a list of allowed pitch classes (0-11).
        Used for Decoding Constraints / Logits Masking.
        """
        if not chord_token.startswith("[CH_"):
            return list(range(12)) # Allow all
            
        content = chord_token[4:-1]
        try:
            root_str, c_type = content.split(":")
            root_pc = PITCH_CLASSES.index(root_str)
        except ValueError:
            return list(range(12))
            
        # Simplified chord intervals (semitones from root)
        intervals = {
            "maj": [0, 4, 7],
            "min": [0, 3, 7],
            "dim": [0, 3, 6],
            "aug": [0, 4, 8],
            "7": [0, 4, 7, 10],
            "maj7": [0, 4, 7, 11],
            "min7": [0, 3, 7, 10],
            "sus4": [0, 5, 7],
            "sus2": [0, 2, 7],
            "N": [] # No chord
        }
        
        chord_intervals = intervals.get(c_type, [0, 4, 7])
        return [(root_pc + interval) % 12 for interval in chord_intervals]

if __name__ == "__main__":
    tokenizer = RemiTokenizer()
    print(f"REMI Tokenizer initialized. Vocab size: {tokenizer.vocab_size}")
    # Quick Test
    test_tokens = ["[BOS]", "[BAR]", "[POS_0]", "[CH_C:maj]", "[TRK_BASS]", "[PITCH_36]", "[DUR_4]", "[EOS]"]
    ids = tokenizer.encode(test_tokens)
    print(f"Encode test: {ids}")
    print(f"Decode test: {tokenizer.decode(ids)}")
    
    # Test normalization
    print(f"Pitch 60 (C) in G major transposed to C major: {RemiTokenizer.normalize_key(60, 'G', 'C')}") # Should be 55 (G->C is -5)
