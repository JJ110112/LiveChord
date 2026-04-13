"""
Jazzify Transformer Reharmonizer (Seq2Seq)
基於 PyTorch Transformer 架構的 AI 和弦重配引擎
"""

import math
import torch
import torch.nn as nn
from typing import List, Dict

class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = 5000):
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
        x: Tensor, shape [seq_len, batch_size, embedding_dim]
        """
        x = x + self.pe[:x.size(0)]
        return self.dropout(x)

class TransformerReharmonizer(nn.Module):
    def __init__(self, vocab_size: int, d_model: int = 256, nhead: int = 8, 
                 num_encoder_layers: int = 4, num_decoder_layers: int = 4, 
                 dim_feedforward: int = 1024, dropout: float = 0.1):
        super().__init__()
        self.model_type = 'Transformer'
        self.d_model = d_model
        
        self.embedding = nn.Embedding(vocab_size, d_model)
        self.pos_encoder = PositionalEncoding(d_model, dropout)
        
        self.transformer = nn.Transformer(
            d_model=d_model,
            nhead=nhead,
            num_encoder_layers=num_encoder_layers,
            num_decoder_layers=num_decoder_layers,
            dim_feedforward=dim_feedforward,
            dropout=dropout
        )
        
        self.generator = nn.Linear(d_model, vocab_size)
    
    def generate_square_subsequent_mask(self, sz: int) -> torch.Tensor:
        """Generate a square mask for the sequence. The masked positions are filled with float('-inf')."""
        return nn.Transformer.generate_square_subsequent_mask(sz)
        
    def forward(self, src: torch.Tensor, tgt: torch.Tensor, 
                src_mask: torch.Tensor = None, tgt_mask: torch.Tensor = None,
                src_padding_mask: torch.Tensor = None, tgt_padding_mask: torch.Tensor = None):
        """
        src: [src_seq_len, batch_size]
        tgt: [tgt_seq_len, batch_size]
        """
        src_emb = self.pos_encoder(self.embedding(src) * math.sqrt(self.d_model))
        tgt_emb = self.pos_encoder(self.embedding(tgt) * math.sqrt(self.d_model))
        
        # Transformer default expects [seq_len, batch, dim]
        outs = self.transformer(src_emb, tgt_emb, src_mask, tgt_mask, None,
                                src_padding_mask, tgt_padding_mask)
                                
        return self.generator(outs)

    def encode(self, src: torch.Tensor, src_mask: torch.Tensor):
        return self.transformer.encoder(self.pos_encoder(self.embedding(src) * math.sqrt(self.d_model)), src_mask)

    def decode(self, tgt: torch.Tensor, memory: torch.Tensor, tgt_mask: torch.Tensor):
        return self.transformer.decoder(self.pos_encoder(self.embedding(tgt) * math.sqrt(self.d_model)), memory, tgt_mask)


def create_mask(src, tgt, pad_idx=0):
    src_seq_len = src.shape[0]
    tgt_seq_len = tgt.shape[0]

    tgt_mask = nn.Transformer.generate_square_subsequent_mask(tgt_seq_len).to(src.device)
    src_mask = torch.zeros((src_seq_len, src_seq_len)).type(torch.bool).to(src.device)

    src_padding_mask = (src == pad_idx).transpose(0, 1)
    tgt_padding_mask = (tgt == pad_idx).transpose(0, 1)
    
    return src_mask, tgt_mask, src_padding_mask, tgt_padding_mask

# ---- Inference Helper ----

def greedy_decode(model: nn.Module, src: torch.Tensor, src_mask: torch.Tensor, 
                  max_len: int, start_symbol: int, end_symbol: int, device: torch.device):
    """
    Auto-regressive inference using greedy decoding
    """
    src = src.to(device)
    src_mask = src_mask.to(device)

    memory = model.encode(src, src_mask)
    ys = torch.ones(1, 1).fill_(start_symbol).type(torch.long).to(device)
    
    # Store probability outputs to be piped into Viterbi if desired
    probs_history = []
    
    for i in range(max_len - 1):
        memory = memory.to(device)
        tgt_mask = (nn.Transformer.generate_square_subsequent_mask(ys.size(0))
                    .type(torch.bool)).to(device)
        
        out = model.decode(ys, memory, tgt_mask)
        out = out.transpose(0, 1)
        prob = model.generator(out[:, -1])
        
        probs_history.append(torch.softmax(prob, dim=-1).squeeze(0).cpu().numpy())
        
        _, next_word = torch.max(prob, dim=1)
        next_word = next_word.item()

        ys = torch.cat([ys, torch.ones(1, 1).type_as(src.data).fill_(next_word)], dim=0)
        
        if next_word == end_symbol:
            break
            
    return ys, probs_history
