import os
import json
import time
import math
import concurrent.futures
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW

# 引用我們既有的神經網路與模組
from transformer_reharmonizer import TransformerReharmonizer, create_mask
from tokenizer import tokenize_song
from reharmonizer import Reharmonizer

# 設定硬體環境
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def tokens_to_pseudo_timeline(tokens):
    """
    將 ["Cmaj7", "DUR_2.0", "Am7", "DUR_1.5"] 轉回 reharmonizer 吃的格式
    Returns: [{"time":0.0, "end":2.0, "chord":"Cmaj7"}, ...]
    """
    chords_list = []
    current_time = 0.0
    
    # 解析 token: 每兩個是一組 (Chord, DUR)
    for i in range(0, len(tokens) - 1, 2):
        chord_str = tokens[i]
        dur_str = tokens[i+1]
        
        # 防止 parsing 錯誤
        if not dur_str.startswith("DUR_"): continue
        try:
            dur = float(dur_str.replace("DUR_", ""))
        except:
            dur = 2.0
            
        chords_list.append({
            "time": current_time,
            "end": current_time + dur,
            "chord": chord_str
        })
        current_time += dur
        
    return chords_list

def process_single_label(seq):
    """供 ThreadPoolExecutor 執行的單筆離線標記任務"""
    try:
        # 1. 還原成字典
        pseudo_timeline = tokens_to_pseudo_timeline(seq)
        
        # 2. 交給硬邏輯老師重配 (Level 3: 最肥的爵士和絃)
        reharmer = Reharmonizer(level=3)
        res = reharmer.jazzify(pseudo_timeline, key="C", mode="rule-based")
        
        jazz_chords = res.get("chords", [])
        if not jazz_chords:
            return None
            
        # 3. 再 Tokenize 回去
        jazz_tokens = tokenize_song(jazz_chords)
        
        # 避免遇到太詭異的結果導致空掉
        if len(jazz_tokens) < 8:
            return None
            
        return seq, jazz_tokens
    except Exception as e:
        return None

def offline_teacher_labeling(models_dir):
    """
    使用 32 執行緒快速讀取舊有的資料集，產出 Label 解答，儲存快取。
    """
    dataset_file = models_dir / "transformer_dataset.json"
    labels_file = models_dir / "teacher_labels.json"
    
    if labels_file.exists():
        print(f"Teacher labels already exist at {labels_file}. Skipping labeling generation.")
        with open(labels_file, "r", encoding="utf-8") as f:
            return json.load(f)
            
    if not dataset_file.exists():
        raise FileNotFoundError(f"Missing {dataset_file}")

    print(f"Loading {dataset_file}...")
    with open(dataset_file, "r", encoding="utf-8") as f:
        src_sequences = json.load(f)

    print(f"Generating Teacher Labels for {len(src_sequences)} sequences... this will take a while.")
    
    paired_data = [] # [[src, tgt], ...]
    
    start_t = time.time()
    processed_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
        futures = [executor.submit(process_single_label, seq) for seq in src_sequences]
        
        for future in concurrent.futures.as_completed(futures):
            processed_count += 1
            if processed_count % 1000 == 0:
                elapsed_so_far = time.time() - start_t
                speed = processed_count / elapsed_so_far if elapsed_so_far > 0 else 0
                remaining_items = len(src_sequences) - processed_count
                eta_seconds = remaining_items / speed if speed > 0 else 0
                eta_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + eta_seconds))
                print(f"Labeled {processed_count}/{len(src_sequences)} | 預估完成時間 (ETA): {eta_str}", flush=True)
                
            res = future.result()
            if res:
                paired_data.append([res[0], res[1]]) # [src, tgt]
                
    elapsed = time.time() - start_t
    print(f"Labeling complete in {elapsed:.1f}s. Valid pairs: {len(paired_data)}")
    
    with open(labels_file, "w", encoding="utf-8") as f:
        json.dump(paired_data, f)
        
    return paired_data


class PopJazzDataset(Dataset):
    def __init__(self, paired_data, vocab, max_len=128):
        self.data = paired_data
        self.vocab = vocab
        self.max_len = max_len
        self.pad_idx = vocab.get("<PAD>", 0)
        self.sos_idx = vocab.get("<SOS>", 1)
        self.eos_idx = vocab.get("<EOS>", 2)
        self.unk_idx = vocab.get("<UNK>", 3)
        
    def __len__(self):
        return len(self.data)
        
    def __getitem__(self, idx):
        src_tokens, tgt_tokens = self.data[idx]
        
        # Convert to indices
        src_indices = [self.vocab.get(t, self.unk_idx) for t in src_tokens]
        tgt_indices = [self.sos_idx] + [self.vocab.get(t, self.unk_idx) for t in tgt_tokens] + [self.eos_idx]
        
        # Truncate
        if len(src_indices) > self.max_len:
            src_indices = src_indices[:self.max_len]
        if len(tgt_indices) > self.max_len:
            tgt_indices = tgt_indices[:self.max_len-1] + [self.eos_idx]
            
        # Pad
        src_pad_len = self.max_len - len(src_indices)
        tgt_pad_len = self.max_len - len(tgt_indices)
        
        src_tensor = torch.tensor(src_indices + [self.pad_idx] * src_pad_len, dtype=torch.long)
        tgt_tensor = torch.tensor(tgt_indices + [self.pad_idx] * tgt_pad_len, dtype=torch.long)
        
        return src_tensor, tgt_tensor


def train_transformer(models_dir, epochs=5, batch_size=32, lr=0.0001):
    # 1. 取得資料
    vocab_file = models_dir / "vocab.json"
    with open(vocab_file, "r", encoding="utf-8") as f:
        vocab = json.load(f)
        
    paired_data = offline_teacher_labeling(models_dir)
    
    # 2. 初始化 Dataset 與 DataLoader
    print("Preparing DataLoader...")
    dataset = PopJazzDataset(paired_data, vocab, max_len=128)
    # 若記憶體允許，可拆出 10% 做 Validation
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True, pin_memory=True)
    
    # 3. 初始化 Transformer 模型
    print(f"Initializing Model on {DEVICE}...")
    vocab_size = len(vocab)
    model = TransformerReharmonizer(
        vocab_size=vocab_size,
        d_model=256,
        nhead=8,
        num_encoder_layers=4,
        num_decoder_layers=4,
        dim_feedforward=512,
        dropout=0.1
    ).to(DEVICE)
    
    pad_idx = vocab.get("<PAD>", 0)
    criterion = nn.CrossEntropyLoss(ignore_index=pad_idx)
    optimizer = AdamW(model.parameters(), lr=lr)
    
    # 4. Training Loop
    print("\nStarting Training...")
    global_start_time = time.time()
    total_batches_per_epoch = len(dataloader)
    total_steps_all = epochs * total_batches_per_epoch
    current_global_step = 0
    
    for epoch in range(epochs):
        model.train()
        total_loss = 0
        
        for batch_idx, (src, tgt) in enumerate(dataloader):
            current_global_step += 1
            src = src.transpose(0, 1).to(DEVICE) # shape: [seq_len, batch]
            tgt = tgt.transpose(0, 1).to(DEVICE) # shape: [seq_len, batch]
            
            # Transformer 訓練的 tgt_in 是除去未來的一個 token, tgt_out 是除去 <SOS> 往後平移
            tgt_in = tgt[:-1, :]
            tgt_out = tgt[1:, :]
            
            src_mask, tgt_mask, src_padding_mask, tgt_padding_mask = create_mask(src, tgt_in, pad_idx)
            
            optimizer.zero_grad()
            
            logits = model(src, tgt_in, src_mask, tgt_mask, src_padding_mask, tgt_padding_mask)
            
            # 重塑供 CrossEntropyLoss 評分
            loss = criterion(logits.reshape(-1, logits.shape[-1]), tgt_out.reshape(-1))
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            
            if batch_idx > 0 and batch_idx % 50 == 0:
                elapsed_time = time.time() - global_start_time
                speed = current_global_step / elapsed_time if elapsed_time > 0 else 0
                remaining_steps = total_steps_all - current_global_step
                eta_seconds = remaining_steps / speed if speed > 0 else 0
                eta_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() + eta_seconds))
                print(f"Epoch: {epoch+1}/{epochs} | Batch: {batch_idx}/{total_batches_per_epoch} | Loss: {loss.item():.4f} | ETA: {eta_str}")
                
        avg_loss = total_loss / len(dataloader)
        perplexity = math.exp(avg_loss) if avg_loss < 20 else float('inf')
        print(f"==== Epoch {epoch+1} Completed | Avg Loss: {avg_loss:.4f} | Perplexity: {perplexity:.4f} ====\n")
        
        # Save checkpoints
        ckpt_path = models_dir / f"transformer_jazzify_ep{epoch+1}.pth"
        torch.save(model.state_dict(), ckpt_path)
        
    print("Training finished! Final model saved.")
    final_path = models_dir / "transformer_jazzify.pth"
    torch.save(model.state_dict(), final_path)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    
    models_dir = Path("W:/data/models")
    if not models_dir.exists():
        models_dir = Path(__file__).parent.parent.parent / "data" / "models"
        
    train_transformer(models_dir, epochs=5, batch_size=64)
