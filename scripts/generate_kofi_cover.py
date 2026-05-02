"""Generate the LiveChord Ko-fi cover image (1200x400, 3:1).

Outputs `data/kofi_cover.png`. Run with `python scripts/generate_kofi_cover.py`.
Pure PIL — no external assets, system fonts only.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "kofi_cover.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

W, H = 1200, 400

# ---- Palette (matches the LiveChord default dark theme) -----------------
BG_TOP = (13, 17, 23)        # #0d1117 — base dark navy
BG_BOT = (24, 34, 50)        # subtle gradient toward bluer
ACCENT = (33, 150, 243)      # #2196f3 — brand blue
ACCENT_DIM = (102, 187, 246) # lighter cyan-blue for highlights
TEXT = (255, 255, 255)
TEXT_DIM = (163, 179, 204)
PIANO_WHITE = (240, 240, 240)
PIANO_BLACK = (28, 28, 28)
CHORD_HI = (255, 195, 64)    # warm yellow for "active" chord block
CHORD_DIM = (33, 150, 243)   # blue blocks for waterfall illusion


def gradient_bg() -> Image.Image:
    img = Image.new("RGB", (W, H), BG_TOP)
    px = img.load()
    for y in range(H):
        t = y / H
        r = int(BG_TOP[0] * (1 - t) + BG_BOT[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOT[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOT[2] * t)
        for x in range(W):
            px[x, y] = (r, g, b)
    return img


def font_for(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Pick a system font that handles Latin (CJK rarely needed on banner)."""
    candidates = []
    if bold:
        candidates += [
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            "/System/Library/Fonts/SFCompactDisplay-Bold.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    candidates += [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/System/Library/Fonts/SFCompactDisplay-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_piano(draw: ImageDraw.ImageDraw, x0: int, y0: int, w: int, h: int) -> None:
    """A minimalist 14-key piano strip stretching horizontally."""
    n_white = 14
    key_w = w / n_white
    # White keys
    for i in range(n_white):
        x1 = x0 + i * key_w
        x2 = x0 + (i + 1) * key_w
        draw.rectangle([x1, y0, x2 - 1, y0 + h], fill=PIANO_WHITE)
    # Black keys — pattern follows real piano (skip after E and B)
    black_offsets = [0.7, 1.7, 3.7, 4.7, 5.7, 7.7, 8.7, 10.7, 11.7, 12.7]
    bk_w = key_w * 0.62
    bk_h = h * 0.6
    for off in black_offsets:
        cx = x0 + off * key_w + key_w * 0.5
        draw.rectangle([cx - bk_w / 2, y0, cx + bk_w / 2, y0 + bk_h], fill=PIANO_BLACK)


def draw_waterfall_blocks(draw: ImageDraw.ImageDraw, x0: int, y0: int, area_w: int, area_h: int) -> None:
    """Synthwave-style falling note blocks above the keyboard for depth."""
    # Vertical bars at varying heights/positions, suggesting a chord progression
    bars = [
        # (x_offset_ratio, top_offset_ratio, width_ratio, height_ratio, color, alpha)
        (0.10, 0.65, 0.045, 0.30, CHORD_DIM, 220),
        (0.18, 0.40, 0.045, 0.55, CHORD_DIM, 220),
        (0.27, 0.30, 0.045, 0.65, CHORD_DIM, 220),
        (0.42, 0.25, 0.05,  0.70, CHORD_HI,  250),  # highlight column
        (0.52, 0.45, 0.045, 0.50, CHORD_DIM, 220),
        (0.62, 0.55, 0.045, 0.40, CHORD_DIM, 220),
        (0.74, 0.20, 0.045, 0.75, CHORD_DIM, 220),
        (0.84, 0.45, 0.045, 0.50, CHORD_DIM, 220),
    ]
    for ox, oy, wr, hr, color, alpha in bars:
        bx = x0 + area_w * ox
        by = y0 + area_h * oy
        bw = area_w * wr
        bh = area_h * hr
        col = (*color, alpha)
        # Rounded corners via a tiny mask
        bar = Image.new("RGBA", (max(1, int(bw)), max(1, int(bh))), col)
        return_img.paste(bar, (int(bx), int(by)), bar)


def main() -> None:
    img = gradient_bg().convert("RGBA")

    # Soft accent glow on the right (stage-light effect)
    glow_w = 700
    glow = Image.new("RGBA", (glow_w, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for r in range(glow_w // 2, 0, -10):
        alpha = max(0, int(50 - r * 0.06))
        gd.ellipse(
            [glow_w // 2 - r, H // 2 - r // 2, glow_w // 2 + r, H // 2 + r // 2],
            fill=(33, 150, 243, alpha),
        )
    img.alpha_composite(glow, (W - glow_w + 80, 0))

    # Right-half visual: piano + falling chord blocks
    visual_x0 = 600
    visual_w = W - visual_x0 - 40
    piano_h = 70
    piano_y0 = H - piano_h - 40
    blocks_y0 = 60
    blocks_h = piano_y0 - blocks_y0 - 6

    global return_img
    return_img = img  # closure for draw_waterfall_blocks
    draw = ImageDraw.Draw(img)
    draw_waterfall_blocks(draw, visual_x0, blocks_y0, visual_w, blocks_h)
    draw_piano(draw, visual_x0, piano_y0, visual_w, piano_h)

    # Left-half text
    f_brand = font_for(108, bold=True)
    f_tag = font_for(28)
    f_sub = font_for(22)

    title = "LiveChord"
    tag = "AI chord analysis for musicians"
    sub = "Piano · Guitar · Ukulele · Accordion"

    pad = 60
    draw.text((pad, 100), title, font=f_brand, fill=ACCENT)
    draw.text((pad, 230), tag, font=f_tag, fill=TEXT)
    draw.text((pad, 280), sub, font=f_sub, fill=TEXT_DIM)

    # Tiny domain mark in bottom-left for branding
    f_url = font_for(18)
    draw.text((pad, H - 50), "livechord.org", font=f_url, fill=ACCENT_DIM)

    img.convert("RGB").save(OUT, "PNG", optimize=True)
    print(f"Wrote {OUT}  ({W}x{H}, {OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
