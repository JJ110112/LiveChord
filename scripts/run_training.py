import os
import sys
import argparse

# Ensure we can import backend modules from the root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.ai.neural_arranger import train_arranger

def main():
    parser = argparse.ArgumentParser(description="Train Neural Arranger Phase 2")
    parser.add_argument("--data_dir", type=str, default=r"F:\MIDI-Library", help="Directory containing MIDI files")
    parser.add_argument("--epochs", type=int, default=50, help="Number of epochs to train")
    parser.add_argument("--batch_size", type=int, default=8, help="Batch size")
    parser.add_argument("--device", type=str, default="cuda", help="Device to train on (cuda/cpu)")
    
    args = parser.parse_args()
    
    print(f"=== Starting Neural Arranger Training ===")
    print(f"Data Dir: {args.data_dir}")
    print(f"Epochs: {args.epochs}")
    print(f"Batch Size: {args.batch_size}")
    print(f"Device: {args.device}")
    print("=========================================\n")
    
    train_arranger(args.data_dir, epochs=args.epochs, batch_size=args.batch_size, device=args.device)

if __name__ == "__main__":
    main()
