"""
和弦時序 Tokenizer：將 JSON 轉為帶有時間標籤（Event-based）的 Token 陣列
適用於 Transformer Seq2Seq 模型訓練
"""

import json
import re
from pathlib import Path
from collections import Counter
try:
    from .preprocess import transpose_chord, detect_key_from_chords, parse_chord_name, NOTE_TO_SEMI
except ImportError:
    from preprocess import transpose_chord, detect_key_from_chords, parse_chord_name, NOTE_TO_SEMI

# 正規化時間區隔 (秒)
# 為了避免詞彙庫過度膨脹，我們將持續時間量化為 0.5s 的倍數
TIME_RESOLUTION = 0.5
MAX_DURATION = 8.0 # 超過 8 秒的長和弦會被截斷為 DUR_8.0

def _quantize_duration(dur_seconds: float) -> str:
    """將秒數量化為 DUR_X.X 的字串"""
    dur = round(dur_seconds / TIME_RESOLUTION) * TIME_RESOLUTION
    dur = max(TIME_RESOLUTION, min(MAX_DURATION, dur))
    return f"DUR_{dur:.1f}"

def tokenize_song(chords_list: list, key_str: str | None = None) -> list:
    """
    將單首歌的和弦列表轉化為時序 Token 陣列
    輸入範例: [{"time": 0.0, "end": 2.0, "chord": "Cmaj7"}, ...]
    輸出範例: ["Cmaj7", "DUR_2.0", "Am7", "DUR_2.0", ...]

    key_str: 選用的調性字串（例如 "C", "Gm"）。若提供，使用它決定 C 化移調量；
    否則回退到 detect_key_from_chords 的啟發式判斷。
    """
    chord_names = [c["chord"] for c in chords_list if c.get("chord") and c["chord"] not in ("N", "X", "NC", "")]
    if len(chord_names) < 4:
        return []

    # 決定原曲 Key 半音 (C=0, C#=1...)：優先使用呼叫者提供的 key_str
    key_semi = None
    if key_str:
        root = key_str.rstrip("m")
        key_semi = NOTE_TO_SEMI.get(root)
    if key_semi is None:
        key_semi = detect_key_from_chords(chord_names)
    
    # 決定移調多少半音，強制歸一化至 C 大調 (根音位移 -key_semi)
    shift = -key_semi

    tokens = []
    
    # 走訪每個和弦並組合時序
    for c in chords_list:
        chord_str = c.get("chord")
        if not chord_str or chord_str in ("N", "X", "NC", ""):
            continue
            
        dur = c["end"] - c["time"]
        if dur <= 0:
            continue
            
        transposed = transpose_chord(chord_str, shift)
        dur_token = _quantize_duration(dur)
        
        # 只在有變化的時候紀錄 (但同一個和弦如果分開兩段 json，合併 duration)
        if len(tokens) >= 2 and tokens[-2] == transposed:
            # 嘗試把上一個 dur_token 拆開並加上去
            prev_dur_str = tokens[-1].replace("DUR_", "")
            try:
                prev_dur = float(prev_dur_str)
                new_dur = prev_dur + dur
                tokens[-1] = _quantize_duration(new_dur)
            except ValueError:
                tokens.append(transposed)
                tokens.append(dur_token)
        else:
            tokens.append(transposed)
            tokens.append(dur_token)
            
    return tokens

def build_transformer_dataset(chords_dir_path: str, vocab_limit=150) -> tuple:
    """
    批次處理大巨量音樂資料夾，產生供 GPT/Seq2Seq 訓練用的 Event-based Token 序列
    Returns:
       sequences (list): [["Cmaj7", "DUR_2.0", "Am7", "DUR_1.5", ...], ...]
       vocab (dict): token_string -> index 映射表
    """
    chords_path = Path(chords_dir_path)
    if not chords_path.is_dir():
        return [], {}

    raw_sequences = []
    token_counts = Counter()

    # 讀取所有 JSON 檔案 (加倍平行處理以大幅加速 SMB 網路 I/O)
    import os
    import concurrent.futures

    files = [f for f in os.listdir(chords_path) if f.endswith(".json")]
    total_files = len(files)
    print(f"Starting threads for {total_files} files...")

    def process_file(fname):
        f = chords_path / fname
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            chords = data.get("chords", [])
            if not chords:
                return None
            tokens = tokenize_song(chords)
            if len(tokens) < 8:
                return None
            return tokens
        except Exception:
            return None

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
        futures = [executor.submit(process_file, fname) for fname in files]
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if completed % 2000 == 0:
                print(f"Tokenizing progress: {completed} / {total_files}...", flush=True)
            res = future.result()
            if res:
                raw_sequences.append(res)
                token_counts.update(res)

    # 把罕見的高階和弦轉為簡單和弦 (降級處理)，DUR_ 時間 token 不動
    # 過濾出最常出現的 前 vocab_limit 個和弦
    # 請注意 token_counts 包含了 DUR_，因此要先分離
    
    chord_tokens = {t: c for t, c in token_counts.items() if not t.startswith("DUR_")}
    dur_tokens = {t: c for t, c in token_counts.items() if t.startswith("DUR_")}
    
    # 取得前 vocab_limit 的安全名單
    safe_chords = set(sorted(chord_tokens.keys(), key=lambda k: chord_tokens[k], reverse=True)[:vocab_limit])
    
    final_sequences = []
    final_counts = Counter()
    
    for seq in raw_sequences:
        cleaned_seq = []
        for token in seq:
            if token.startswith("DUR_"):
                cleaned_seq.append(token)
            else:
                if token in safe_chords:
                    cleaned_seq.append(token)
                else:
                    # 嘗試簡化該罕見和弦 (如 F#m11 -> F#m)
                    # 這裡藉助 parse_chord_name 和 _SIMPLIFY_QUALITY 但需要針對絕對和弦修正
                    m = re.match(r"^([A-G][b#]?)(.*?)(/([A-G][b#]?))?$", token)
                    if m:
                        root = m.group(1)
                        quality = m.group(2)
                        bass = m.group(4)
                        
                        # 找尋簡化品質
                        from .preprocess import _SIMPLIFY_QUALITY
                        simplified_q = _SIMPLIFY_QUALITY.get(quality, quality)
                        
                        new_chord = root + simplified_q
                        if bass:
                            new_chord += "/" + bass
                            
                        # 如果連簡化後都不在安全名單，就砍掉 bass
                        if new_chord not in safe_chords and bass:
                            new_chord = root + simplified_q
                            
                        # 若還是不在名單，就只能以大/小三和弦代替
                        if new_chord not in safe_chords:
                            if "m" in quality or "dim" in quality:
                                new_chord = root + "m"
                            else:
                                new_chord = root
                                
                        cleaned_seq.append(new_chord)
                    else:
                        cleaned_seq.append(token) # parse failed
        final_sequences.append(cleaned_seq)
        final_counts.update(cleaned_seq)
        
    # 建立 Vocab: 包含特殊的 Padding 和 EOS token
    vocab = {"<PAD>": 0, "<SOS>": 1, "<EOS>": 2, "<UNK>": 3}
    for t, _ in final_counts.most_common():
        vocab[t] = len(vocab)
        
    return final_sequences, vocab

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    
    # Check if a custom path is specified via sys.argv, else defaults to W:/data/chords
    if len(sys.argv) > 1:
        data_dir = Path(sys.argv[1])
    else:
        # 預設使用 production 的 W:\data\chords
        data_dir = Path("W:/data/chords")
        if not data_dir.exists():
            data_dir = Path(__file__).parent.parent.parent / "data" / "chords"
            
    models_dir = data_dir.parent / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
            
    print(f"Tokenizing dir: {data_dir}")
    print(f"Models will be saved to: {models_dir}")
    
    # 依 user 建議提升 vocab_limit 到 300 避免過度稀釋高頻彩色和弦
    sequences, vocab = build_transformer_dataset(data_dir, vocab_limit=300)
    print(f"\nTotal Songs Processed: {len(sequences)}")
    print(f"Vocab Size: {len(vocab)}")
    
    if sequences:
        print(f"\nExample Sequence 1:")
        print(" ".join(sequences[0][:20]))
        
        # 落地儲存
        ds_file = models_dir / "transformer_dataset.json"
        vocab_file = models_dir / "vocab.json"
        
        with open(ds_file, "w", encoding="utf-8") as f:
            json.dump(sequences, f)
        with open(vocab_file, "w", encoding="utf-8") as f:
            json.dump(vocab, f, ensure_ascii=False, indent=2)
            
        print(f"\n[Success] Dataset and Vocab saved to {models_dir}")
