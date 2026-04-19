"""
Chord2Vec: 將和弦序列轉換為語意連續的高維空間向量
利用 SVD 奇異值分解計算共現矩陣 (Co-occurrence Matrix)
"""

import json
import numpy as np
from pathlib import Path

def build_co_occurrence_matrix(sequences, vocab, window_size=2):
    """
    建立共現矩陣
    """
    from scipy.sparse import lil_matrix
    vocab_size = len(vocab)
    # 使用 LIL 矩陣有利於逐項構建
    matrix = lil_matrix((vocab_size, vocab_size), dtype=np.float32)
    
    print(f"Building co-occurrence matrix (window={window_size}, vocab={vocab_size})...")
    total = len(sequences)
    
    for i, seq in enumerate(sequences):
        if i > 0 and i % 5000 == 0:
            print(f"Processed {i}/{total} sequences...")
            
        # 將字串轉換為 index
        indices = [vocab.get(token, vocab.get("<UNK>")) for token in seq]
        
        # Sliding window
        for j, center_idx in enumerate(indices):
            # 限定左右範圍
            start = max(0, j - window_size)
            end = min(len(indices), j + window_size + 1)
            
            for k in range(start, end):
                if j != k:
                    context_idx = indices[k]
                    # 可以根據距離加權，也可以簡單 +1
                    distance = abs(j - k)
                    weight = 1.0 / distance
                    matrix[center_idx, context_idx] += weight
                    
    return matrix.tocsr()

def train_chord2vec(models_dir, dim=32):
    """
    從 dataset 讀取並訓練 SVD，產出 chord_embeddings.npy
    """
    dataset_file = models_dir / "transformer_dataset.json"
    vocab_file = models_dir / "vocab.json"
    
    if not dataset_file.exists() or not vocab_file.exists():
        print(f"Error: Required dataset files not found in {models_dir}")
        return False
        
    print("Loading datasets...")
    with open(dataset_file, "r", encoding="utf-8") as f:
        sequences = json.load(f)
        
    with open(vocab_file, "r", encoding="utf-8") as f:
        vocab = json.load(f)
        
    if dim >= len(vocab) - 1:
        dim = max(2, len(vocab) - 2)
        print(f"Warning: requested dim={dim} is too large for vocab_size={len(vocab)}. Using dim={dim}")
        
    matrix = build_co_occurrence_matrix(sequences, vocab, window_size=2)
    
    print(f"Performing Truncated SVD (dim={dim})...")
    from scipy.sparse.linalg import svds
    # 進行特徵分解 (SVD)
    # k 是我們想要的維度
    U, Sigma, VT = svds(matrix, k=dim)
    
    # 將奇異值 (Sigma) 分配至字詞向量
    # 通常取 U * sqrt(Sigma) 作為 embedding
    word_vecs = U * np.sqrt(Sigma)
    
    # 儲存
    out_file = models_dir / "chord_embeddings.npy"
    np.save(out_file, word_vecs)
    print(f"Saved embeddings to {out_file}")
    
    return True
    
def get_similar_chords(chord_name, top_n=5, models_dir=None):
    """
    推論功能：找出最相似的 N 個和弦
    """
    if not models_dir:
        models_dir = Path("V:/data/models")
        
    vocab_file = models_dir / "vocab.json"
    emb_file = models_dir / "chord_embeddings.npy"
    
    if not emb_file.exists():
        return []
        
    with open(vocab_file, "r", encoding="utf-8") as f:
        vocab = json.load(f)
        
    word_vecs = np.load(emb_file)
    
    if chord_name not in vocab:
        print(f"Chord '{chord_name}' not in vocab.")
        return []
        
    target_idx = vocab[chord_name]
    target_vec = word_vecs[target_idx]
    
    # 計算 Cosine Similarity
    norms = np.linalg.norm(word_vecs, axis=1)
    norms[norms == 0] = 1e-10 # 避免除以零
    
    target_norm = np.linalg.norm(target_vec)
    if target_norm == 0: target_norm = 1e-10
    
    similarities = np.dot(word_vecs, target_vec) / (norms * target_norm)
    
    # 排序
    inv_vocab = {v: k for k, v in vocab.items()}
    best_indices = np.argsort(similarities)[::-1]
    
    results = []
    for idx in best_indices:
        token = inv_vocab[idx]
        # 過濾掉自己, 標籤, 特殊符號, 時長標記
        if token != chord_name and not token.startswith("<") and not token.startswith("DUR_"):
            results.append((token, similarities[idx]))
            if len(results) >= top_n:
                break
                
    return results

# ---- Singleton ----
_model = None
_MODELS_DIR = Path(__file__).parent.parent.parent / "data" / "models"


class Chord2VecModel:
    """Thin wrapper holding vocab + embeddings for stats / inference."""
    def __init__(self, vocab, embeddings):
        self.vocab = vocab
        self.embeddings = embeddings

    def get_stats(self):
        return {
            "vocab_size": len(self.vocab),
            "dim": self.embeddings.shape[1] if self.embeddings.ndim == 2 else 0,
        }


def _train_via_subprocess(models_dir: Path) -> bool:
    """Spawn `python chord2vec.py` to run training in a fresh interpreter.

    The co-occurrence build is a pure-Python nested loop over ~50k sequences
    that holds the GIL tightly; running it inline (even on a worker thread)
    starves every other request thread. A subprocess gets its own GIL so the
    parent server stays responsive while training runs.

    Returns True on success (emb file present after the run).
    """
    import subprocess, sys
    script = Path(__file__).resolve()
    emb_file = models_dir / "chord_embeddings.npy"
    try:
        subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, timeout=1800,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        print(f"chord2vec subprocess training failed: {e}")
    return emb_file.exists()


def get_chord2vec(chords_dir=None):
    """Singleton loader: train (or load cached) chord2vec embeddings."""
    global _model
    if _model is None:
        emb_file = _MODELS_DIR / "chord_embeddings.npy"
        vocab_file = _MODELS_DIR / "vocab.json"
        if not emb_file.exists():
            _train_via_subprocess(_MODELS_DIR)
        if emb_file.exists() and vocab_file.exists():
            with open(vocab_file, "r", encoding="utf-8") as f:
                vocab = json.load(f)
            embeddings = np.load(emb_file)
            _model = Chord2VecModel(vocab, embeddings)
        else:
            raise RuntimeError("chord2vec training produced no output")
    return _model


if __name__ == "__main__":
    import sys
    models_dir = Path("V:/data/models")
    if not models_dir.exists():
        models_dir = Path(__file__).parent.parent.parent / "data" / "models"
        
    print(f"Training Chord2Vec in {models_dir}...")
    success = train_chord2vec(models_dir, dim=32)
    
    if success:
        print("\n--- Model Verification ---")
        targets = ["Dm7", "Cmaj7", "G7", "Em"]
        for t in targets:
            print(f"\nTop 5 similar to {t}:")
            sims = get_similar_chords(t, top_n=5, models_dir=models_dir)
            for chord, sim in sims:
                print(f"  {chord:<8}: {sim:.4f}")
