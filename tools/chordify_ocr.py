"""
Chordify 截圖和弦辨識器（逐格 OCR + 自動格線偵測）
====================================================
用使用者框選的「一列」作為參照，自動偵測粗線(小節)和細線(拍)，
精確計算每格的 x 座標邊界，再逐格 OCR。

用法:
  python chordify_ocr.py "../data/test_songs/Lv1/Dancing Queen.png"
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
    "DFA": "D/F#", "DIF:": "D/F#", "DIF#": "D/F#", "DF:": "D/F#",
    "DIF;": "D/F#", "D/F": "D/F#", "D/Fi": "D/F#",
    "EFA": "E/A", "EA": "E/A", "EJA": "E/A", "EIA": "E/A", "E1A": "E/A",
    "AE": "A/E", "AIE": "A/E", "AJE": "A/E",
    "E/G:": "E/G#", "E/G;": "E/G#", "E/Ga": "E/G#", "EIG:": "E/G#",
    "E/Gi": "E/G#", "E/Gs": "E/G#", "E/Gu": "E/G#",
    "Fim": "F#m", "Frm": "F#m", "Fzm": "F#m", "Fsm": "F#m",
    "Fum": "F#m", "Fxm": "F#m", "Fam": "F#m", "Fm": "F#m",
    "Fim7": "F#m7", "Frm7": "F#m7", "Fzm7": "F#m7", "Frm?": "F#m7",
    'Frm"': "F#m7", "Fim?": "F#m7", "Fzm?": "F#m7", "Fum?": "F#m7",
    "Fxm7": "F#m7", "Fam7": "F#m7", "Fm7": "F#m7",
    "B7/D:": "B7/D#", "B7/D;": "B7/D#", "B7 /D:": "B7/D#",
    "B7/Di": "B7/D#", "B7/D": "B7/D#", "B?/D:": "B7/D#",
    "Bmi/E": "Bm7/E", "Bme/E": "Bm7/E", 'Bm"/E': "Bm7/E",
    "Bm?/E": "Bm7/E", 'Bm" /E': "Bm7/E", "Bm7 /E": "Bm7/E",
    "Bm?": "Bm7", 'Bm"': "Bm7", "Bmz": "Bm7",
    "C;7": "C#7", "Cz7": "C#7", "C:7": "C#7", "C47": "C#7",
    "Ci7": "C#7", "Ca7": "C#7", "Cs7": "C#7",
    "E?": "E7",
}


def fix_chord(raw: str) -> str:
    raw = raw.strip().rstrip(':;.,?')
    if raw in _OCR_FIX:
        return _OCR_FIX[raw]
    fixed = raw.replace("min", "m").replace("MIN", "m")
    fixed = fixed.replace("Maj", "maj").replace("MAJ", "maj")
    if fixed and fixed[0].islower():
        fixed = fixed[0].upper() + fixed[1:]
    if fixed in _OCR_FIX:
        return _OCR_FIX[fixed]
    return fixed


# ---------------------------------------------------------------------------
# 格線偵測（從一列參照行分析）
# ---------------------------------------------------------------------------

def detect_grid_columns(img_arr_gray: np.ndarray, row_h: int, sample_y: int = None,
                        verbose: bool = True) -> list:
    """
    從圖片中偵測垂直格線位置，回傳每格的 (x_start, x_end) 列表。

    策略：取一列的下半部（無文字區域），用亮度驟降找格線，
    區分粗線（小節線）和細線（拍線），回傳格子邊界。
    """
    h, w = img_arr_gray.shape

    # 取一列的下半部無文字區域
    if sample_y is None:
        sample_y = row_h  # 第二列
    y1 = sample_y + int(row_h * 0.5)
    y2 = min(h, sample_y + int(row_h * 0.9))

    if y2 <= y1:
        return []

    strip = img_arr_gray[y1:y2, :]
    col_mean = np.mean(strip.astype(float), axis=0)

    # 平滑
    try:
        from scipy.ndimage import uniform_filter1d
        smooth = uniform_filter1d(col_mean, size=3)
    except ImportError:
        smooth = col_mean

    # 找亮度驟降點
    raw_lines = []
    for x in range(5, w - 5):
        left_avg = np.mean(smooth[max(0, x - 5):x])
        right_avg = np.mean(smooth[x + 1:min(w, x + 6)])
        drop = min(left_avg, right_avg) - smooth[x]
        if drop > 3:
            raw_lines.append((x, drop))

    # 合併相鄰的
    merged = []
    for x, drop in raw_lines:
        if merged and x - merged[-1][0] < 15:
            if drop > merged[-1][1]:
                merged[-1] = (x, drop)
        else:
            merged.append((x, drop))

    if not merged:
        return []

    # 所有分隔線按位置排序
    all_dividers = sorted([x for x, _ in merged])

    # 找格子：相鄰分隔線之間 > 50px 的區域
    cells = []
    pad = 5  # 內縮避開格線
    for i in range(len(all_dividers) - 1):
        x1 = all_dividers[i] + pad
        x2 = all_dividers[i + 1] - pad
        gap = x2 - x1
        if gap > 40:  # 有效格子（排除格線之間的窄縫）
            cells.append((x1, x2))

    if verbose:
        print(f"  偵測到 {len(merged)} 條垂直線, {len(cells)} 個格子")
        widths = [x2 - x1 for x1, x2 in cells]
        if widths:
            print(f"  格寬: min={min(widths)}, max={max(widths)}, avg={sum(widths)//len(widths)}")

    return cells


def detect_grid_rows(img_arr_gray: np.ndarray, verbose: bool = True) -> list:
    """
    偵測水平格線，回傳每行的 (y_start, y_end)。

    Chordify 的水平線很淺，改用「整行平均亮度」找行邊界：
    行內有文字 → 亮度較低，行間空白 → 亮度高。
    """
    h, w = img_arr_gray.shape

    # 計算每一行(y)在中央區域的平均亮度
    center_strip = img_arr_gray[:, w // 4: w * 3 // 4]
    row_mean = np.mean(center_strip.astype(float), axis=1)

    # 平滑
    try:
        from scipy.ndimage import uniform_filter1d
        smooth = uniform_filter1d(row_mean, size=5)
    except ImportError:
        smooth = row_mean

    # 找「亮帶」（行間分隔）— 亮度 > 閾值 的連續區域
    threshold = np.percentile(smooth, 75)
    in_bright = False
    bright_bands = []
    start = 0

    for y in range(h):
        if smooth[y] > threshold:
            if not in_bright:
                start = y
                in_bright = True
        else:
            if in_bright:
                center = (start + y) // 2
                width = y - start
                if width > 3:  # 排除單像素噪音
                    bright_bands.append(center)
                in_bright = False

    if not bright_bands:
        return []

    # 行 = 兩個亮帶之間
    rows = []
    pad_y = 3
    for i in range(len(bright_bands) - 1):
        y1 = bright_bands[i] + pad_y
        y2 = bright_bands[i + 1] - pad_y
        if y2 - y1 > 30:
            rows.append((y1, y2))

    if verbose:
        heights = [y2 - y1 for y1, y2 in rows]
        if heights:
            print(f"  偵測到 {len(rows)} 行, 行高: min={min(heights)}, max={max(heights)}, avg={sum(heights)//len(heights)}")

    return rows


# ---------------------------------------------------------------------------
# 逐格 OCR
# ---------------------------------------------------------------------------

def load_row_ref():
    """從 config 載入列參照（row_height）"""
    if CONFIG_FILE.is_file():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return cfg.get("row_height"), cfg.get("cell_size")
    return None, None


def extract_chords_grid(image_path: str, cell_w: int = None, cell_h: int = None,
                        verbose: bool = True) -> list:
    """
    自動偵測格線，逐格 OCR 辨識和弦。
    """
    img = Image.open(image_path).convert("RGB")
    gray = np.array(img.convert("L"))
    h, w = gray.shape

    if verbose:
        print(f"  圖片: {w}×{h}")

    # 行分隔：用使用者框選的列高（row_height）均分
    # Chordify 水平分隔線太淺，自動偵測不可靠
    row_h = cell_h
    if not row_h:
        cfg_rh = None
        if CONFIG_FILE.is_file():
            cfg_rh = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("row_height")
        row_h = cfg_rh or 112  # 預設

    pad_y = 5  # 上下內縮
    n_rows = h // row_h
    rows = [(r * row_h + pad_y, (r + 1) * row_h - pad_y) for r in range(n_rows)]
    if verbose:
        print(f"  行: {n_rows} 行 (列高={row_h}px)")

    # 偵測列：先嘗試自動格線偵測，若結果不合理則用均分
    cols = detect_grid_columns(gray, row_h, sample_y=rows[1][0] if len(rows) > 1 else rows[0][0],
                               verbose=verbose)

    # 驗證列數是否合理（Chordify 每行 8 拍）
    if len(cols) < 7 or len(cols) > 10:
        # 格線偵測不可靠，改用框選列寬均分 8 拍
        row_w = cell_w or w
        beat_w = row_w / 8
        pad_x = max(3, int(beat_w / 15))
        cols = [(int(c * beat_w + pad_x), int((c + 1) * beat_w - pad_x)) for c in range(8)]
        if verbose:
            print(f"  格線偵測結果不合理，改用均分 8 拍 (beat_w={beat_w:.0f}px)")

    if verbose:
        print(f"  網格: {len(rows)} 行 × {len(cols)} 列 = {len(rows) * len(cols)} 格")

    reader = easyocr.Reader(['en'], gpu=False, verbose=False)

    sequence = []

    for row_idx, (ry1, ry2) in enumerate(rows):
        row_chords = []
        for col_idx, (cx1, cx2) in enumerate(cols):
            # 確保不超出圖片邊界
            cx1 = max(0, min(w, cx1))
            cx2 = max(0, min(w, cx2))
            ry1_c = max(0, min(h, ry1))
            ry2_c = max(0, min(h, ry2))

            if cx2 <= cx1 or ry2_c <= ry1_c:
                row_chords.append("")
                continue

            cell_img = img.crop((cx1, ry1_c, cx2, ry2_c))

            # 對比度檢查
            cell_arr = np.array(cell_img.convert("L"))
            contrast = np.std(cell_arr)
            if contrast < 5:
                row_chords.append("")
                continue

            # 放大 OCR
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

        if verbose:
            display = []
            for c in row_chords:
                display.append(f"{c:8s}" if c else "   ·    ")
            print(f"  行{row_idx + 1:2d}: {' '.join(display)}")

        sequence.extend(row_chords)

    if verbose:
        unique = sorted(set(c for c in sequence if c))
        chord_count = sum(1 for c in sequence if c)
        print(f"\n  總拍數: {len(sequence)}, 和弦變化: {chord_count}")
        print(f"  和弦種類 ({len(unique)}): {', '.join(unique)}")

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
    return compact


def main():
    if len(sys.argv) < 2:
        print("用法: python chordify_ocr.py <screenshot.png>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.isfile(image_path):
        print(f"  找不到: {image_path}")
        sys.exit(1)

    song_name = Path(image_path).stem
    output_dir = Path(image_path).parent

    print("=" * 60)
    print(f"  Chordify 逐格和弦辨識: {song_name}")
    print("=" * 60)

    seq = extract_chords_grid(image_path)
    if seq:
        save_results(seq, song_name, str(output_dir))


if __name__ == "__main__":
    main()
