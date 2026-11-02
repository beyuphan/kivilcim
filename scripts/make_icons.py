"""Draws the app icons (a four-pointed spark on a red background) as PNG files.

Pure Python, no imaging library needed. Run from the project root:

    python scripts/make_icons.py

The generated files are committed, so this only needs to run when the design changes.
"""

import struct
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "kivilcim" / "static" / "icons"

TOP = (220, 38, 38)  # #dc2626
BOTTOM = (127, 29, 29)  # #7f1d1d
WHITE = (255, 255, 255)
SUPERSAMPLE = 3


def star(u: float, v: float, radius: float, sharpness: float = 0.62) -> bool:
    """A four-pointed star: |u|^p + |v|^p <= r^p, with p below 1 so the sides curve inward."""
    return abs(u) ** sharpness + abs(v) ** sharpness <= radius**sharpness


def inside_rounded_square(u: float, v: float, corner: float) -> bool:
    """A square covering [-1, 1] with rounded corners of the given radius."""
    x, y = abs(u), abs(v)
    if x <= 1 - corner or y <= 1 - corner:
        return x <= 1 and y <= 1
    return (x - (1 - corner)) ** 2 + (y - (1 - corner)) ** 2 <= corner**2


def pixel(px: float, py: float, size: int, maskable: bool) -> tuple[int, int, int, int]:
    """RGBA of one sample. `maskable` icons fill the whole square and keep the spark in the safe zone."""
    u = (px / size) * 2 - 1
    v = (py / size) * 2 - 1
    if not maskable and not inside_rounded_square(u, v, 0.22):
        return 0, 0, 0, 0
    scale = 0.7 if maskable else 0.86  # the spark's size relative to the icon
    t = (v + 1) / 2
    base = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
    # The main spark, and a small one up and to the right of it.
    if star(u / scale * 0.95, v / scale * 0.95, 0.78) or star((u - 0.5 * scale) / scale * 0.95, (v + 0.5 * scale) / scale * 0.95, 0.22):
        return (*WHITE, 255)
    return (*base, 255)


def render(size: int, maskable: bool = False) -> bytes:
    """RGBA bytes, antialiased by averaging SUPERSAMPLE x SUPERSAMPLE samples per pixel."""
    n = SUPERSAMPLE
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            r = g = b = a = 0
            for sy in range(n):
                for sx in range(n):
                    pr, pg, pb, pa = pixel(x + (sx + 0.5) / n, y + (sy + 0.5) / n, size, maskable)
                    r, g, b, a = r + pr * pa, g + pg * pa, b + pb * pa, a + pa
            if a:
                row += bytes((round(r / a), round(g / a), round(b / a), round(a / (n * n))))
            else:
                row += bytes(4)
        rows.append(bytes(row))
    return b"".join(b"\x00" + row for row in rows)  # filter type 0 for every scanline


def write_png(path: Path, size: int, maskable: bool = False) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8 bit RGBA
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(size, maskable), 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)
    print(f"{path.name}: {size}x{size}, {len(png)} bytes")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    write_png(OUT / "icon-512.png", 512)
    write_png(OUT / "icon-192.png", 192)
    write_png(OUT / "icon-maskable-512.png", 512, maskable=True)
    write_png(OUT / "apple-touch-icon.png", 180, maskable=True)  # iOS rounds the corners itself
    write_png(OUT / "icon-32.png", 32)
    return 0


if __name__ == "__main__":
    sys.exit(main())
