"""Render Kairos home-screen / PWA icons from the brand mark.

The mark is the same crescent/eclipse used by the frontend loader: a cream
disc on the deep-navy background, with an offset background disc cutting it
into a crescent. Centered with padding so it stays inside the maskable safe
zone (central 80%). Run from the repo root with the venv:

    .venv/bin/python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

BG = (13, 19, 33)        # #0D1321
CREAM = (236, 230, 214)  # #ECE6D6
WEB = Path(__file__).resolve().parent.parent / "web"

# Each output: (filename, pixel size)
SIZES = [
    ("icon-512.png", 512),
    ("icon-192.png", 192),
    ("apple-touch-icon.png", 180),
    ("icon-32.png", 32),
]


def render(size: int) -> Image.Image:
    """Supersample 4x then downscale for smooth, anti-aliased edges."""
    s = size * 4
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    # cream disc, slightly left of center
    cx, cy, r = 0.47 * s, 0.50 * s, 0.30 * s
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CREAM)
    # background disc, offset up-right -> carves the crescent
    ox, oy, orr = 0.59 * s, 0.44 * s, 0.275 * s
    d.ellipse([ox - orr, oy - orr, ox + orr, oy + orr], fill=BG)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    for name, size in SIZES:
        render(size).save(WEB / name)
        print(f"wrote {WEB / name} ({size}x{size})")


if __name__ == "__main__":
    main()
