import torch
import torch.nn as nn
import torch.nn.functional as F

SECTION_CLASSES = ["intro", "verse", "pre_chorus", "chorus", "instrumental", "outro"]
NUM_CLASSES = len(SECTION_CLASSES)
NUM_FEATURES = 6  # dense_chords, complexity, unique, mel_dens, mel_pitch, bass_dens

# Reverse map for easy decoding
ID_TO_SECTION = {i: c for i, c in enumerate(SECTION_CLASSES)}
SECTION_TO_ID = {c: i for i, c in enumerate(SECTION_CLASSES)}

class BiLSTMSectionTagger(nn.Module):
    """
    序列標記模型：透過 Bi-LSTM 來平滑化段落偵測。
    輸入特徵 (Batch, Seq_Len, Features)
    輸出類別 (Batch, Seq_Len, Classes)
    """
    def __init__(self, input_dim=NUM_FEATURES, hidden_dim=64, num_layers=2, output_dim=NUM_CLASSES, dropout=0.2):
        super(BiLSTMSectionTagger, self).__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        
        # 使用 LayerNorm 來穩定輸入特徵 (因為不同 feature scale 差很多，例如 pitch 可能是 60-80, density 可能是 1-10)
        self.layer_norm = nn.LayerNorm(input_dim)
        
        # Bi-LSTM
        self.lstm = nn.LSTM(
            input_dim, 
            hidden_dim, 
            num_layers=num_layers, 
            bidirectional=True, 
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0
        )
        
        # Fully Connected Classifier
        self.fc = nn.Linear(hidden_dim * 2, output_dim)
        
        # Dropout for regularization
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        # x shape: (batch_size, seq_length, input_dim)
        x = self.layer_norm(x)
        lstm_out, _ = self.lstm(x)  # lstm_out shape: (batch_size, seq_length, hidden_dim * 2)
        lstm_out = self.dropout(lstm_out)
        logits = self.fc(lstm_out)  # shape: (batch_size, seq_length, output_dim)
        return logits
    
    @torch.no_grad()
    def predict(self, x):
        """
        給定輸入特徵 (Seq_Len, Features)，回傳預測的 tags 陣列
        """
        if x.dim() == 2:
            x = x.unsqueeze(0)  # (1, seq, features)
        
        logits = self.forward(x)
        preds = torch.argmax(logits, dim=-1) # (1, seq)
        return preds[0].cpu().numpy().tolist()
