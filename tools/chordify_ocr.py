"""
Chordify 截圖和弦辨識器（逐格 OCR 版）
========================================
用已知的格子大小把截圖切成 N×M 個小格，對每格獨立 OCR。
小圖 OCR 準確度遠高於大圖整體 OCR。

格子大小由 GUI 的「框選一格」取得，存在 capture_config.json。

用法:
  python chordify_ocr.py "../data/test_songs/Lv1/Dancing Queen.png"
  python chordify_ocr.py "../data/test_songs/Lv1/Dancing Queen.png" --cell 115x56
"""

import sys
import os
import re
import json
import numpy as np
from pathlib import Path
from PIL import Image
import easyocr

TOOLS_DIR = Path(__file__).parent
CONFIG_FILE = TOOLS_DIR / "capture_config.json"

# ---------------------------------------------------------------------------
# OCR 修正表
# ---------------------------------------------------------------------------

_OCR_FIX = {
    "DA": "D/A", "DIA": "D/A", "DJA": "D/A", "D1A": "D/A", "DlA": "D/A",
    "DFA": "D/F#", "DIF:": "D/F#", "DIF#": "D/F#", "DF:": "D/F#", "DIF;": "D/F#",
    "EJA": "E/A", "EIA": "E/A", "E1A": "E/A",
    "AE": "A/E", "AIE": "A/E", "AJE": "A/E",
    "E/G:": "E/G#", "E/G;": "E/G#", "E/Ga": "E/G#", "EIG:": "E/G#",
    "Fim": "F#m", "Frm": "F#m", "Fzm": "F#m", "Fsm": "F#m", "Fum": "F#m",
    "Fim7": "F#m7", "Frm7": "F#m7", "Fzm7": "F#m7", "Frm?": "F#m7",
    'Frm"': "F#m7", "Fim?": "F#m7", "Fzm?": "F#m7", "Fum?": "F#m7",
    "Fxm": "F#m", "Fxm7": "F#m7",
    "B7/D:": "B7/D#", "B7/D;": "B7/D#", "B7 /D:": "B7/D#",
    "B7/Di": "B7/D#", "B?/D:": "B7/D#", "B? /D:": "B7/D#",
    "Bmi/E": "Bm7/E", "Bme/E": "Bm7/E", 'Bm"/E': "Bm7/E",
    "Bm?/E": "Bm7/E", "Bm'/E": "Bm7/E", 'Bm" /E': "Bm7/E",
    "Bm7 /E": "Bm7/E", "Bm?": "Bm7", 'Bm"': "Bm7", "Bmz": "Bm7",
    "C;7": "C#7", "Cz7": "C#7", "C:7": "C#7", "C47": "C#7", "Ci7": "C#7",
    "E?": "E7", "F": "F#m",  # Chordify 中獨立 F 通常是 F#m 被截斷
    # 逐格 OCR 新增
    "EFA": "E/A", "EA": "E/A", "EGA": "E/G#", "EG:": "E/G#",
    "D/F": "D/F#", "DF": "D/F#", "DF:": "D/F#", "D/Fi": "D/F#",
    "B7/D": "B7/D#", "B7D": "B7/D#",
    "Ca7": "C#7", "Cs7": "C#7",
    "Fm": "F#m", "Fm7": "F#m7",
}


def fix_chord(raw: str) -> str:
    raw = raw.strip().rstrip(':;.,?')
    if raw in _OCR_FIX:
        return _OCR_FIX[raw]
    # 通用替換
    fixed = raw.replace("min", "m").replace("MIN", "m")
    fixed = fixed.replace("Maj", "maj").replace("MAJ", "maj")
    if fixed and fixed[0].islower():
        fixed = fixed[0].upper() + fixed[1:]
    if fixed in _OCR_FIX:
        return _OCR_FIX[fixed]
    return fixed


def is_valid_chord(text: str) -> bool:
    """判斷是否為有效和弦名"""
    if not text or text[0] not in "ABCDEFG":
        return False
    return bool(re.match(
        r'^[A-G][#b]?(m|maj|dim|aug|sus[24]?|add)?(7|9|6|11|13)?(/[A-G][#b]?)?$',
        text
    ))


# ---------------------------------------------------------------------------
# 逐格 OCR
# ---------------------------------------------------------------------------

def load_cell_size() -> tuple:
    """從 config 載入格子大小"""
    if CONFIG_FILE.is_file():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cs = cfg.get("cell_size")
        if cs:
            return tuple(cs)
    return None


def extract_chords_grid(image_path: str, cell_w: int = None, cell_h: int = None,
                        verbose: bool = True) -> list:
    """
    用格子大小切圖，逐格 OCR 辨識和弦。

    回傳: ["A", "", "", "D/A", "", "", ...] (空字串 = 延續)
    """
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape

    # 載入格子大小
    if cell_w is None or cell_h is None:
        saved = load_cell_size()
        if saved:
            cell_w, cell_h = saved
        else:
            print("  ⚠ 未設定格子大小，請在 GUI 中「框選一格」")
            return []

    if verbose:
        print(f"  圖片: {w}×{h}")
        print(f"  格子: {cell_w}×{cell_h} px")

    # 計算行列數
    n_cols = w // cell_w
    n_rows = h // cell_h
    if verbose:
        print(f"  網格: {n_rows} 行 × {n_cols} 列 = {n_rows * n_cols} 格")

    reader = easyocr.Reader(['en'], gpu=False, verbose=False)

    sequence = []
    row_results = []

    for row in range(n_rows):
        row_chords = []
        for col in range(n_cols):
            x1 = col * cell_w
            y1 = row * cell_h
            x2 = min(x1 + cell_w, w)
            y2 = min(y1 + cell_h, h)

            # 切出小格
            cell_img = img.crop((x1, y1, x2, y2))

            # 先檢查是否有內容（灰階差異度）
            cell_arr = np.array(cell_img.convert("L"))
            contrast = np.std(cell_arr)

            if contrast < 8:
                # 幾乎純色（空格或格線）
                row_chords.append("")
                continue

            # 放大 3 倍再 OCR（小圖需要放大）
            cell_big = cell_img.resize((cell_img.width * 3, cell_img.height * 3), Image.LANCZOS)
            results = reader.readtext(
                np.array(cell_big),
                allowlist='ABCDEFGabcdefgm#b0123456789/dimaugsusmaj',
                paragraph=False
            )

            chord = ""
            for _, text, conf in results:
                text = text.strip()
                if text and conf > 0.2 and text[0] in "ABCDEFGabcdefg":
                    chord = fix_chord(text)
                    break

            row_chords.append(chord)

        # 顯示此行結果
        if verbose:
            display = []
            for c in row_chords:
                display.append(f"{c:8s}" if c else "   ·    ")
            print(f"  行{row+1:2d}: {' '.join(display)}")

        row_results.append(row_chords)
        sequence.extend(row_chords)

    if verbose:
        unique = sorted(set(c for c in sequence if c))
        chord_count = sum(1 for c in sequence if c)
        print(f"\n  總拍數: {len(sequence)}, 和弦變化: {chord_count}")
        print(f"  和弦種類: {', '.join(unique)}")

    return sequence


def sequence_to_compact(seq: list) -> str:
    return ",".join(seq)


def save_results(seq: list, song_name: str, output_dir: str):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    txt_path = output_dir / f"{song_name}.chords.txt"
    compact = sequence_to_compact(seq)
    txt_path.write_text(compact, encoding="utf-8")
    print(f"\n  ✓ 已儲存: {txt_path}")

    unique = sorted(set(c for c in seq if c))
    print(f"    拍數: {len(seq)}, 和弦種類: {len(unique)}")
    print(f"    和弦: {', '.join(unique)}")
    return compact


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("用法: python chordify_ocr.py <screenshot.png> [--cell WxH]")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.isfile(image_path):
        print(f"  找不到: {image_path}")
        sys.exit(1)

    # 解析 --cell 參數
    cell_w = cell_h = None
    for i, arg in enumerate(sys.argv):
        if arg == "--cell" and i + 1 < len(sys.argv):
            try:
                parts = sys.argv[i + 1].lower().split("x")
                cell_w, cell_h = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                print("  格式錯誤，使用: --cell 115x56")

    song_name = Path(image_path).stem
    output_dir = Path(image_path).parent

    print("=" * 60)
    print(f"  Chordify 逐格和弦辨識: {song_name}")
    print("=" * 60)

    seq = extract_chords_grid(image_path, cell_w, cell_h)
    if seq:
        save_results(seq, song_name, str(output_dir))


if __name__ == "__main__":
    main()
