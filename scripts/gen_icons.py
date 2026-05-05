"""Render PNG icons matching favicon.svg (cyan bg + white LC text).

Outputs:
  frontend/img/livechord-icon-192.png   — PWA standard icon
  frontend/img/livechord-icon-512.png   — high-res PWA icon (replaces existing)
  frontend/img/livechord-icon-512-maskable.png  — Android maskable (safe zone padded)

Why a script instead of one-shot conversion: Inkscape/ImageMagick aren't
guaranteed on dev/NUC; Pillow is already a backend dep. Re-run after any
favicon redesign so the manifest icons stay in sync.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "frontend" / "img"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CYAN = (33, 150, 243, 255)      # matches favicon.svg #2196F3 (Material blue, --accent)
WHITE = (255, 255, 255, 255)


def _load_bold_font(size: int) -> ImageFont.FreeTypeFont:
    # Pick the heaviest sans-serif Windows ships with — fall back gracefully.
    candidates = [
        "arialbd.ttf",         # Arial Bold (almost always present on Windows)
        "ariblk.ttf",          # Arial Black (heavier)
        "segoeuib.ttf",        # Segoe UI Bold
        "DejaVuSans-Bold.ttf",  # Linux fallback
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(size: int, *, maskable: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if maskable:
        # Android maskable icons get cropped into a circle/squircle; the safe
        # zone is the inner ~80% (radius 40%). Fill the entire canvas so the
        # OS mask never reveals transparency, and shrink the LC text to fit
        # the safe zone.
        draw.rectangle((0, 0, size, size), fill=CYAN)
        text_size = int(size * 0.40)        # keep LC inside the safe zone
        text_y_offset = int(size * 0.06)
    else:
        # Standard icon: rounded square (rx=14 in 64-unit viewBox → rx=14/64).
        radius = int(size * 14 / 64)
        # PIL needs a separate alpha mask for rounded corners.
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
        bg = Image.new("RGBA", (size, size), CYAN)
        img = Image.composite(bg, img, mask)
        draw = ImageDraw.Draw(img)
        text_size = int(size * 34 / 64)     # matches SVG font-size:34 in 64 viewBox
        text_y_offset = int(size * 0.04)    # nudges LC slightly down to look optically centred

    font = _load_bold_font(text_size)
    # Centre LC at (size/2, size/2 + nudge). PIL anchor "mm" = middle-middle.
    draw.text((size / 2, size / 2 + text_y_offset), "LC", fill=WHITE, font=font, anchor="mm")
    return img


def main() -> None:
    targets = [
        (192, OUT_DIR / "livechord-icon-192.png", False),
        (512, OUT_DIR / "livechord-icon-512.png", False),
        (512, OUT_DIR / "livechord-icon-512-maskable.png", True),
    ]
    for size, path, maskable in targets:
        img = render(size, maskable=maskable)
        img.save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size}{'  maskable' if maskable else ''})")


if __name__ == "__main__":
    main()
