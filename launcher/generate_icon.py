"""Generate the multi-resolution Windows icon and web PNG from the SVG design.

The geometry is kept in sync with review_ui/static/dubsync-icon.svg. Pillow is
already installed in the DubSync virtual environment; no new package is needed.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SIZE = 1024
SCALE = SIZE / 128


def pt(x: float, y: float) -> tuple[int, int]:
    return round(x * SCALE), round(y * SCALE)


def bezier(start, control1, control2, end, steps=48):
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append((
            u**3 * start[0] + 3 * u * u * t * control1[0]
            + 3 * u * t * t * control2[0] + t**3 * end[0],
            u**3 * start[1] + 3 * u * u * t * control1[1]
            + 3 * u * t * t * control2[1] + t**3 * end[1],
        ))
    return points


def render() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (*pt(4, 4), *pt(124, 124)), radius=round(26 * SCALE), fill=255
    )
    background = Image.new("RGBA", (SIZE, SIZE))
    pixels = background.load()
    for y in range(SIZE):
        for x in range(SIZE):
            blend = min(1, max(0, (x + y) / (2 * SIZE)))
            pixels[x, y] = (
                round(24 * (1 - blend) + 9 * blend),
                round(52 * (1 - blend) + 21 * blend),
                round(81 * (1 - blend) + 34 * blend), 255,
            )
    image.paste(background, (0, 0), mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (*pt(4, 4), *pt(124, 124)), radius=round(26 * SCALE),
        outline="#315879", width=round(2 * SCALE),
    )

    outline = [(35, 31), (52, 31)]
    outline += bezier((52, 31), (80, 31), (97, 43), (97, 64))[1:]
    outline += bezier((97, 64), (97, 85), (80, 97), (52, 97))[1:]
    outline += [(35, 97), (35, 31)]
    scaled = [pt(x, y) for x, y in outline]
    stroke = round(11 * SCALE)
    draw.line(scaled, fill="#39a7ff", width=stroke, joint="curve")
    for x, y in (outline[0], outline[-1]):
        cx, cy = pt(x, y)
        draw.ellipse((cx - stroke // 2, cy - stroke // 2,
                      cx + stroke // 2, cy + stroke // 2), fill="#39a7ff")
    draw.polygon([pt(55, 46.5), pt(55, 81.5), pt(82, 64)], fill="#f1fbff")
    cx, cy = pt(103, 32)
    radius = round(5 * SCALE)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 fill="#38d7c2")
    return image


def main() -> None:
    image = render()
    static = ROOT / "review_ui" / "static"
    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        static / "dubsync-icon.png"
    )
    image.save(
        ROOT / "launcher" / "dubsync.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
               (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
