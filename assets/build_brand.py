"""Regenerate brand images from assets/icon.svg.

Needs rsvg-convert (librsvg) and Pillow; the logo text uses the Inter font.
Usage: python assets/build_brand.py [path/to/Inter-Bold.otf] [path/to/Inter-Medium.otf]
"""

from pathlib import Path
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "custom_components" / "ha_trv_thermostat" / "brand"
FONT_BOLD = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/opentype/inter/Inter-Bold.otf"
FONT_TEXT = sys.argv[2] if len(sys.argv) > 2 else "/usr/share/fonts/opentype/inter/Inter-Medium.otf"


def square_icon() -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.png"
        subprocess.run(
            [
                "rsvg-convert",
                "-w",
                "1024",
                "-h",
                "1024",
                str(ROOT / "assets" / "icon.svg"),
                "-o",
                str(raw),
            ],
            check=True,
        )
        im = Image.open(raw).convert("RGBA")
        im.load()
    crop = im.crop(im.getbbox())  # trim
    w, h = crop.size
    side = max(w, h)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.alpha_composite(crop, ((side - w) // 2, (side - h) // 2))
    return sq


def logo(sq: Image.Image, height: int, color: tuple[int, int, int, int]) -> Image.Image:
    icon = sq.resize((height, height), Image.LANCZOS)
    bold = ImageFont.truetype(FONT_BOLD, int(height * 0.30))
    text = ImageFont.truetype(FONT_TEXT, int(height * 0.30))
    gap = int(height * 0.10)
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    t1, t2 = "TRV ", "Thermostat"
    w1, w2 = measure.textlength(t1, font=bold), measure.textlength(t2, font=text)
    canvas = Image.new("RGBA", (height + gap + int(w1 + w2) + 4, height), (0, 0, 0, 0))
    canvas.alpha_composite(icon, (0, 0))
    draw = ImageDraw.Draw(canvas)
    asc, desc = bold.getmetrics()
    y = (height - (asc + desc)) // 2 + int(height * 0.02)
    draw.text((height + gap, y), t1, font=bold, fill=color)
    draw.text((height + gap + w1, y), t2, font=text, fill=color)
    canvas = canvas.crop(canvas.getbbox())
    if canvas.size[1] != height:
        canvas = canvas.resize(
            (round(canvas.size[0] * height / canvas.size[1]), height), Image.LANCZOS
        )
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sq = square_icon()
    for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
        sq.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
    for height, suffix in ((256, ""), (512, "@2x")):
        logo(sq, height, (43, 47, 51, 255)).save(OUT / f"logo{suffix}.png", optimize=True)
        logo(sq, height, (240, 243, 245, 255)).save(OUT / f"dark_logo{suffix}.png", optimize=True)
    print("written to", OUT)


if __name__ == "__main__":
    main()
