"""
Chordify 截圖和弦辨識器
========================
從 Chordify chord overview 截圖中，精確提取每一拍的和弦序列。
格式: A,,,D/A,,,A,,,,... (逗號=延續上一個和弦的空拍)

要求 100% 正確率 → OCR + 嚴格後處理修正 + 人工校驗

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

# ---------------------------------------------------------------------------
# Chordify OCR 錯誤修正表（EasyOCR 常見誤讀）
# ---------------------------------------------------------------------------

OCR_FIXES = {
    # 斜線和弦 D/A, E/A, D/F#, E/G# 等常被誤讀
    "DIA": "D/A", "D/A": "D/A", "D|A": "D/A",
    "DJA": "D/A", "D1A": "D/A", "DlA": "D/A",
    "EJA": "E/A", "EIA": "E/A", "E|A": "E/A", "E1A": "E/A",
    "AE": "A/E", "A/E": "A/E", "AIE": "A/E", "AJE": "A/E",
    "DIF:": "D/F#", "DIF#": "D/F#", "D/F:": "D/F#", "DIF;": "D/F#",
    "DF:": "D/F#", "D/Fs": "D/F#", "D/F;": "D/F#",
    "E/G:": "E/G#", "E/G;": "E/G#", "EIG:": "E/G#",
    "E/Ga": "E/G#", "E/G#": "E/G#",
    "B7/D:": "B7/D#", "B7/D;": "B7/D#", "B7 /D:": "B7/D#",
    "B?/D:": "B7/D#", "B? /D:": "B7/D#",
    "Bm7/E": "Bm7/E", 'Bm"/E': "Bm7/E", "Bm?/E": "Bm7/E",
    "Bm'/E": "Bm7/E", 'Bm7 /E': "Bm7/E", "Bm? /E": "Bm7/E",
    'Bm" /E': "Bm7/E",
    # #/b 符號誤讀
    "C;7": "C#7", "Cz7": "C#7", "C:7": "C#7", "C#7": "C#7", "C47": "C#7",
    "Czz": "C#7", "C77": "C#7",
    "Frm7": "F#m7", "Frm?": "F#m7", 'Frm"': "F#m7", "F#m7": "F#m7",
    "Fzm7": "F#m7", "Fzm?": "F#m7", "Fim7": "F#m7", "Fim?": "F#m7",
    "Fzm": "F#m", "Frm": "F#m", "Fim": "F#m",
    "F#m": "F#m", "Fsm": "F#m",
    # 簡單和弦
    "Bm7": "Bm7", "Bm?": "Bm7", 'Bm"': "Bm7", "Bmz": "Bm7",
    "D9": "D9", "Ds": "D9", "D°": "D9",
    "E7": "E7", "E?": "E7",
    # 清理尾部標點
}

# 有效的和弦模式
VALID_CHORD = re.compile(
    r'^[A-G][#b]?'
    r'(m|maj|dim|aug|sus[24]?|add)?'
    r'(7|9|6|11|13)?'
    r'(/[A-G][#b]?)?$'
)


def fix_chord_name(raw: str) -> str:
    """修正 OCR 誤讀的和弦名稱"""
    raw = raw.strip()
    if not raw:
        return ""

    # 先查修正表（精確匹配）
    if raw in OCR_FIXES:
        return OCR_FIXES[raw]

    # 去除尾部標點
    raw = raw.rstrip(':;.,?!"\'')

    # 再查一次
    if raw in OCR_FIXES:
        return OCR_FIXES[raw]

    # 嘗試模式匹配修正
    # J/I/1/| → / （斜線和弦）
    fixed = re.sub(r'([A-G][#b]?(?:m|maj|dim|aug|7|9)?)([JI1|])([A-G])', r'\1/\3', raw)
    if fixed != raw and VALID_CHORD.match(fixed):
        return fixed

    # : ; → # （升記號）
    fixed = raw.replace(':', '#').replace(';', '#')
    if VALID_CHORD.match(fixed):
        return fixed

    # ? " → 7
    fixed = raw.replace('?', '7').replace('"', '7').replace("'", '7')
    if VALID_CHORD.match(fixed):
        return fixed

    # 如果原始就合法
    if VALID_CHORD.match(raw):
        return raw

    return raw  # 無法修正，原樣回傳


# ---------------------------------------------------------------------------
# 網格分析
# ---------------------------------------------------------------------------

def analyze_grid(img: Image.Image) -> dict:
    """
    分析 Chordify chord overview 截圖的網格結構。
    回傳 {rows: int, cols: int, cell_width, cell_height, grid_top, grid_left}
    """
    arr = np.array(img.convert("L"))  # 灰階
    h, w = arr.shape

    # 找水平格線：整行偏暗的 y 座標
    row_mean = np.mean(arr, axis=1)
    # 格線是比背景暗的細線
    threshold = np.percentile(row_mean, 25)

    h_lines = []
    in_line = False
    line_start = 0
    for y in range(h):
        if row_mean[y] < threshold:
            if not in_line:
                line_start = y
                in_line = True
        else:
            if in_line:
                h_lines.append((line_start + y) // 2)
                in_line = False

    # 找垂直格線
    col_mean = np.mean(arr, axis=0)
    threshold_v = np.percentile(col_mean, 25)

    v_lines = []
    in_line = False
    for x in range(w):
        if col_mean[x] < threshold_v:
            if not in_line:
                line_start = x
                in_line = True
        else:
            if in_line:
                v_lines.append((line_start + x) // 2)
                in_line = False

    # 過濾太近的線（合併 < 10px 的）
    def merge_lines(lines, min_gap=20):
        if not lines:
            return []
        merged = [lines[0]]
        for l in lines[1:]:
            if l - merged[-1] >= min_gap:
                merged.append(l)
        return merged

    h_lines = merge_lines(h_lines)
    v_lines = merge_lines(v_lines)

    # 估算格子大小
    if len(h_lines) > 1:
        row_gaps = [h_lines[i+1] - h_lines[i] for i in range(len(h_lines)-1)]
        cell_height = int(np.median(row_gaps))
    else:
        cell_height = h // 20

    if len(v_lines) > 1:
        col_gaps = [v_lines[i+1] - v_lines[i] for i in range(len(v_lines)-1)]
        cell_width = int(np.median(col_gaps))
    else:
        cell_width = w // 8

    return {
        "h_lines": h_lines, "v_lines": v_lines,
        "cell_width": cell_width, "cell_height": cell_height,
        "rows": len(h_lines) - 1 if len(h_lines) > 1 else 0,
        "cols": len(v_lines) - 1 if len(v_lines) > 1 else 0,
    }


# ---------------------------------------------------------------------------
# 主要辨識函式
# ---------------------------------------------------------------------------

def extract_chords_from_screenshot(image_path: str, verbose: bool = True) -> list:
    """
    從 Chordify chord overview 截圖提取和弦序列。

    回傳格式: ["A", "", "", "D/A", "", "", "A", "", "", "", ...]
    空字串 = 延續上一拍
    """
    img = Image.open(image_path).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape

    if verbose:
        print(f"  圖片: {w}x{h}")

    # OCR
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    results = reader.readtext(arr, paragraph=False)

    if verbose:
        print(f"  OCR 偵測到 {len(results)} 個文字元素")

    # 提取和弦 + 位置
    raw_chords = []
    for bbox, text, conf in results:
        text = text.strip()
        if not text or len(text) > 12:
            continue

        # 首字必須是 A-G
        if text[0] not in "ABCDEFG":
            continue

        cx = (bbox[0][0] + bbox[2][0]) / 2
        cy = (bbox[0][1] + bbox[2][1]) / 2

        # 修正和弦名稱
        fixed = fix_chord_name(text)

        raw_chords.append({
            "raw": text, "fixed": fixed,
            "x": cx, "y": cy, "conf": conf,
        })

    if verbose:
        print(f"  和弦候選: {len(raw_chords)} 個")

    # 分析網格
    grid = analyze_grid(img)
    cell_h = grid["cell_height"]
    cell_w = grid["cell_width"]

    if verbose:
        print(f"  網格: {grid['rows']} rows x {grid['cols']} cols, cell={cell_w}x{cell_h}")

    # 按行分群（Y 座標接近的歸同一行）
    raw_chords.sort(key=lambda c: c["y"])
    rows = []
    current_row = []
    row_y = -999

    for c in raw_chords:
        if abs(c["y"] - row_y) > cell_h * 0.5:
            if current_row:
                rows.append(sorted(current_row, key=lambda c: c["x"]))
            current_row = [c]
            row_y = c["y"]
        else:
            current_row.append(c)
    if current_row:
        rows.append(sorted(current_row, key=lambda c: c["x"]))

    if verbose:
        print(f"  行數: {len(rows)}")

    # Chordify 格式：每行有 8 拍（2 小節 × 4 拍）
    # 和弦出現在某拍的開始，後續空拍延續
    BEATS_PER_ROW = 8

    # 建立完整序列
    full_sequence = []

    for row_idx, row in enumerate(rows):
        if verbose:
            chords_in_row = [f"{c['fixed']}(x={c['x']:.0f})" for c in row]
            print(f"    行 {row_idx}: {', '.join(chords_in_row)}")

        # 計算此行的 x 範圍
        if grid["v_lines"] and len(grid["v_lines"]) >= 2:
            row_left = grid["v_lines"][0]
            row_right = grid["v_lines"][-1]
        else:
            row_left = 0
            row_right = w

        row_width = row_right - row_left
        beat_width = row_width / BEATS_PER_ROW

        # 將每個和弦分配到對應的拍
        beat_chords = [""] * BEATS_PER_ROW
        for c in row:
            # 計算此和弦在第幾拍
            beat_idx = int((c["x"] - row_left) / beat_width)
            beat_idx = max(0, min(BEATS_PER_ROW - 1, beat_idx))
            beat_chords[beat_idx] = c["fixed"]

        full_sequence.extend(beat_chords)

    if verbose:
        print(f"\n  總拍數: {len(full_sequence)}")
        # 顯示序列（用逗號分隔）
        display = []
        for c in full_sequence:
            display.append(c if c else ",")
        print(f"  序列: {','.join(display)}")

    return full_sequence


def sequence_to_compact(seq: list) -> str:
    """轉為 A,,,D/A,,,A,,,, 格式"""
    parts = []
    for c in seq:
        parts.append(c if c else "")
    return ",".join(parts)


def save_results(seq: list, song_name: str, output_dir: str):
    """儲存結果"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # .chords.txt — 逐拍序列
    txt_path = output_dir / f"{song_name}.chords.txt"
    compact = sequence_to_compact(seq)
    txt_path.write_text(compact, encoding="utf-8")
    print(f"\n  ✓ 已儲存: {txt_path}")

    # 統計
    unique = set(c for c in seq if c)
    total_beats = len(seq)
    chord_beats = sum(1 for c in seq if c)
    print(f"    拍數: {total_beats}, 和弦變化: {chord_beats} 次")
    print(f"    使用的和弦: {sorted(unique)}")

    return compact


# ---------------------------------------------------------------------------
# 互動校驗
# ---------------------------------------------------------------------------

def interactive_verify(seq: list) -> list:
    """逐行顯示並允許人工修正"""
    print("\n" + "=" * 60)
    print("  人工校驗（直接 Enter = 確認，輸入修正值 = 替換）")
    print("=" * 60)

    BEATS_PER_ROW = 8
    corrected = list(seq)

    for row_start in range(0, len(seq), BEATS_PER_ROW):
        row = seq[row_start:row_start + BEATS_PER_ROW]
        # 顯示這一行
        display = []
        for i, c in enumerate(row):
            if c:
                display.append(f"[{row_start+i}]{c}")
            else:
                display.append("  ·")

        row_num = row_start // BEATS_PER_ROW + 1
        print(f"\n  行 {row_num:2d}: {'  '.join(display)}")

        fix = input("         修正 (格式: 位置=和弦 如 '3=D/A 5=E', Enter=確認): ").strip()
        if fix:
            for part in fix.split():
                if '=' in part:
                    idx_str, chord = part.split('=', 1)
                    try:
                        idx = int(idx_str)
                        if 0 <= idx < len(corrected):
                            old = corrected[idx]
                            corrected[idx] = chord
                            print(f"           [{idx}] {old} → {chord}")
                    except ValueError:
                        pass

    return corrected


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("用法: python chordify_ocr.py <screenshot.png> [song_name]")
        print("範例: python chordify_ocr.py ../data/test_songs/Lv1/\"Dancing Queen.png\"")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.isfile(image_path):
        print(f"  找不到: {image_path}")
        sys.exit(1)

    song_name = sys.argv[2] if len(sys.argv) > 2 else Path(image_path).stem
    output_dir = Path(image_path).parent

    print("=" * 60)
    print(f"  Chordify 和弦辨識: {song_name}")
    print("=" * 60)

    # 辨識
    seq = extract_chords_from_screenshot(image_path)

    # 顯示結果
    print(f"\n  自動辨識結果:")
    compact = sequence_to_compact(seq)
    print(f"  {compact}")

    # 校驗
    verify = input("\n  進行人工校驗？(y/n, 預設 y): ").strip().lower()
    if verify != 'n':
        seq = interactive_verify(seq)

    # 儲存
    save_results(seq, song_name, str(output_dir))


if __name__ == "__main__":
    main()
