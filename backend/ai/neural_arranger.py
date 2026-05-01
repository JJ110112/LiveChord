import torch
import torch.nn as nn
import math
import os

try:
    from backend.ai.remi_tokenizer import RemiTokenizer
except ImportError:
    # Fallback for standalone testing
    from remi_tokenizer import RemiTokenizer

class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = 4096):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

        position = torch.arange(max_len).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2) * (-math.log(10000.0) / d_model))
        pe = torch.zeros(max_len, 1, d_model)
        pe[:, 0, 0::2] = torch.sin(position * div_term)
        pe[:, 0, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: Tensor, shape [seq_len, batch_size, embedding_dim]
        """
        x = x + self.pe[:x.size(0)]
        return self.dropout(x)


class NeuralArranger(nn.Module):
    """
    Decoder-only Transformer for Multi-track REMI AI Arrangement.
    Supports Prefix Role Conditioning.
    """
    def __init__(self, vocab_size: int, d_model: int = 768, nhead: int = 12, 
                 num_layers: int = 12, dim_feedforward: int = 3072, dropout: float = 0.1):
        super().__init__()
        self.model_type = 'TransformerDecoder'
        self.pos_encoder = PositionalEncoding(d_model, dropout)
        
        self.embedding = nn.Embedding(vocab_size, d_model)
        
        # A GPT-style Decoder is essentially an Encoder with a causal mask
        encoder_layers = nn.TransformerEncoderLayer(d_model, nhead, dim_feedforward, dropout, batch_first=False)
        self.transformer_decoder = nn.TransformerEncoder(encoder_layers, num_layers)
        
        self.out_linear = nn.Linear(d_model, vocab_size)
        self.d_model = d_model

    def generate_square_subsequent_mask(self, sz: int) -> torch.Tensor:
        """Generates a causal mask for autoregressive decoding."""
        mask = (torch.triu(torch.ones(sz, sz)) == 1).transpose(0, 1)
        mask = mask.float().masked_fill(mask == 0, float('-inf')).masked_fill(mask == 1, float(0.0))
        return mask

    def forward(self, src: torch.Tensor, src_mask: torch.Tensor = None):
        """
        Args:
            src: Tensor, shape [seq_len, batch_size]
            src_mask: Tensor, causal mask
        """
        if src_mask is None:
            src_mask = self.generate_square_subsequent_mask(len(src)).to(src.device)
            
        src_emb = self.embedding(src) * math.sqrt(self.d_model)
        src_emb = self.pos_encoder(src_emb)
        
        output = self.transformer_decoder(src_emb, src_mask)
        return self.out_linear(output)

    @classmethod
    def load_from_checkpoint(cls, path: str, device: str = 'cpu') -> 'NeuralArranger':
        checkpoint = torch.load(path, map_location=device, weights_only=True)
        model = cls(
            vocab_size=checkpoint.get('vocab_size', 50000),
            d_model=checkpoint.get('d_model', 768),
            nhead=checkpoint.get('nhead', 12),
            num_layers=checkpoint.get('num_layers', 12)
        )
        model.load_state_dict(checkpoint['model_state_dict'])
        model.to(device)
        model.eval()
        return model

    def apply_chord_mask(self, logits: torch.Tensor, current_chord_token: str, tokenizer: RemiTokenizer) -> torch.Tensor:
        """
        Constrained Decoding Engine:
        Forces the model to only pick pitches that belong to the current chord.
        Args:
            logits: Shape [..., vocab_size]
            current_chord_token: e.g. "[CH_C:maj]"
            tokenizer: RemiTokenizer instance
        """
        allowed_pcs = tokenizer.get_chord_notes(current_chord_token)
        
        # Build mask for all vocab
        mask = torch.zeros(tokenizer.vocab_size, device=logits.device, dtype=torch.bool)
        
        # Only mask out PITCH tokens that do not belong to the chord
        for i in range(tokenizer.vocab_size):
            token = tokenizer.id2vocab.get(i, "")
            if token.startswith("[PITCH_"):
                pitch_val = int(token.split("_")[1][:-1])
                pitch_class = pitch_val % 12
                if pitch_class not in allowed_pcs:
                    mask[i] = True # Mark for invalidation
                    
        # Apply mask: Set invalid token logits to -infinity
        logits = logits.masked_fill(mask, float('-inf'))
        return logits

    @torch.no_grad()
    def generate(self, prefix_tokens: list[int], max_len: int, tokenizer: RemiTokenizer, current_chord: str = None, temperature: float = 1.0) -> list[int]:
        """
        Auto-regressive generation with Prefix Conditioning and optional Chord Masking.
        Args:
            prefix_tokens: List of token IDs (e.g. [BOS_ID, TARGET_ROLE_ID])
            max_len: Max tokens to generate
            tokenizer: RemiTokenizer instance
            current_chord: Token string e.g. "[CH_C:maj]" to constrain decoding
        """
        self.eval()
        device = next(self.parameters()).device
        generated = list(prefix_tokens)
        
        for _ in range(max_len):
            src = torch.tensor(generated, dtype=torch.long, device=device).unsqueeze(1) # [seq_len, batch=1]
            logits = self.forward(src)
            next_token_logits = logits[-1, 0, :] # Get last step, batch 0
            
            if current_chord:
                # Apply chord mask to the next token logits
                # Expand dims to match apply_chord_mask expectation [1, 1, vocab_size]
                temp_logits = next_token_logits.unsqueeze(0).unsqueeze(0)
                temp_logits = self.apply_chord_mask(temp_logits, current_chord, tokenizer)
                next_token_logits = temp_logits[0, 0, :]
            
            if temperature > 0:
                next_token_logits = next_token_logits / temperature
                probs = torch.softmax(next_token_logits, dim=-1)
                next_token = torch.multinomial(probs, num_samples=1).item()
            else:
                next_token = torch.argmax(next_token_logits).item()
                
            generated.append(next_token)
            
            # Stop if we generate EOS
            if next_token == tokenizer.vocab2id.get("[EOS]", -1):
                break
                
        return generated


from torch.utils.data import DataLoader
from backend.ai.dataset import MidiArrangementDataset, collate_fn

def train_arranger(data_dir: str, epochs: int = 10, batch_size: int = 4, device: str = 'cuda'):
    """
    Training loop for the neural arranger with AMP (Automatic Mixed Precision)
    """
    tokenizer = RemiTokenizer()
    dataset = MidiArrangementDataset(data_dir, tokenizer, max_seq_len=1024)
    data_loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, collate_fn=collate_fn, num_workers=0)
    
    save_path = os.path.join("data", "models", "neural_arranger_phase2.pt")
    start_epoch = 0
    
    if os.path.exists(save_path):
        print(f"Found existing checkpoint at {save_path}, resuming training...")
        checkpoint = torch.load(save_path, map_location=device, weights_only=False)
        model = NeuralArranger(vocab_size=tokenizer.vocab_size, d_model=768, nhead=12, num_layers=12).to(device)
        model.load_state_dict(checkpoint['model_state_dict'])
        if 'epoch' in checkpoint:
            start_epoch = checkpoint['epoch'] + 1
    else:
        model = NeuralArranger(vocab_size=tokenizer.vocab_size, d_model=768, nhead=12, num_layers=12).to(device)
    
    # Ignore [PAD] token (index 0) in loss calculation
    criterion = nn.CrossEntropyLoss(ignore_index=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0.01)
    
    total_steps = epochs * len(data_loader)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=2e-4, total_steps=total_steps, pct_start=0.1
    )
    
    # Resume optimizer if available
    if os.path.exists(save_path) and 'optimizer_state_dict' in checkpoint:
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        if 'scheduler_state_dict' in checkpoint:
            scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
            
    # bfloat16 does not require a GradScaler, it avoids NaN overflows natively
    scaler = None
    use_amp = 'cuda' in device
    
    print(f"Starting training on {device} from epoch {start_epoch+1} to {epochs}...")
    print(f"Dataset size: {len(dataset)} files. Batches per epoch: {len(data_loader)}")
    model.train()
    
    for epoch in range(start_epoch, epochs):
        total_loss = 0.0
        
        for batch_idx, (src, target) in enumerate(data_loader):
            src, target = src.to(device), target.to(device)
            optimizer.zero_grad()
            
            # Using AMP with bfloat16 for faster, stable training on modern GPUs (like RTX 5080)
            with torch.amp.autocast('cuda', dtype=torch.bfloat16, enabled=use_amp):
                logits = model(src)
                loss = criterion(logits.view(-1, tokenizer.vocab_size), target.view(-1))
                
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
                
            scheduler.step()
            total_loss += loss.item()
            
            if batch_idx % 10 == 0:
                print(f"Epoch {epoch+1}/{epochs} | Batch {batch_idx}/{len(data_loader)} | Loss: {loss.item():.4f}")
                
        avg_loss = total_loss / len(data_loader) if len(data_loader) > 0 else 0
        print(f"=== Epoch {epoch+1}/{epochs} Complete | Average Loss: {avg_loss:.4f} ===")
        
        # Save model checkpoint after every epoch
        os.makedirs(os.path.join("data", "models"), exist_ok=True)
        save_path = os.path.join("data", "models", "neural_arranger_phase2.pt")
        torch.save({
            'epoch': epoch,
            'vocab_size': tokenizer.vocab_size,
            'd_model': 768,
            'nhead': 12,
            'num_layers': 12,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'scheduler_state_dict': scheduler.state_dict(),
        }, save_path)
        print(f"Checkpoint saved to {save_path}")


if __name__ == "__main__":
    tokenizer = RemiTokenizer()
    model = NeuralArranger(vocab_size=tokenizer.vocab_size)
    print(f"Neural Arranger (Decoder-only) initialized. Vocab size: {tokenizer.vocab_size}")
    
    # Test Forward Pass
    dummy_input = torch.randint(0, tokenizer.vocab_size, (10, 2)) # seq_len=10, batch_size=2
    logits = model(dummy_input)
    print(f"Forward pass output shape: {logits.shape} (Expected: [10, 2, {tokenizer.vocab_size}])")
    
    # Test Constrained Decoding Mask
    chord = "[CH_C:maj]" # C E G
    masked_logits = model.apply_chord_mask(logits, chord, tokenizer)
    
    # Verify a non-chord note is masked
    cs_pitch_idx = tokenizer.vocab2id.get("[PITCH_61]", -1) # C#4
    c_pitch_idx = tokenizer.vocab2id.get("[PITCH_60]", -1)  # C4
    if cs_pitch_idx != -1 and c_pitch_idx != -1:
        print(f"C# Logit (Should be -inf): {masked_logits[0, 0, cs_pitch_idx].item()}")
        print(f"C Logit (Should be normal): {masked_logits[0, 0, c_pitch_idx].item()}")

    # Test Prefix Conditioning Generation
    print("\n--- Testing Prefix Conditioning Inference ---")
    # Simulate a prefix for generating a BASS track
    prefix_str = ["[BOS]", "[TARGET_BASS]", "<GENERATE>"]
    prefix_ids = tokenizer.encode(prefix_str)
    print(f"Prefix tokens: {prefix_str} -> {prefix_ids}")
    
    # Generate 10 tokens with C:maj constraint
    generated_ids = model.generate(
        prefix_tokens=prefix_ids, 
        max_len=10, 
        tokenizer=tokenizer, 
        current_chord="[CH_C:maj]",
        temperature=1.0
    )
    
    generated_str = tokenizer.decode(generated_ids)
    print(f"Generated sequence: {generated_str}")
