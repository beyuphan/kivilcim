"""A shareable picture of a fire: the satellite before and after side by side, with the place, the dates
and where the images come from. It is what a link to a fire shows in a chat or a post, and what a
person can save and send.

The picture is built from the same tiles the map shows (NASA GIBS), so it needs no extra data.
"""

import io
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).parent / "fonts" / "Inter.ttf"
WIDTH, HEIGHT = 1200, 630
HALF = WIDTH // 2
TILE = 256
MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
QUALITY = {"clear": "açık hava", "partly_cloudy": "az bulutlu", "cloudy": "bulutlu", "partial": "kısmi görüntü"}

Fetch = Callable[[str], bytes | None]


def short_day(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {MONTHS[d.month - 1]}"


def zoom_for(extent_km: float, max_zoom: int) -> int:
    """How far to zoom so a fire of this size fills a half of the card (a half is about 600 pixels)."""
    wanted = 12 if extent_km <= 14 else 11 if extent_km <= 30 else 10
    return max(5, min(wanted, max_zoom))


def _pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """The position of a point on the whole-world pixel grid at a zoom level."""
    n = 2**zoom * TILE
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def mosaic(fetch: Fetch, template: str, zoom: int, lat: float, lon: float, width: int, height: int) -> Image.Image:
    """The tiles around a point, cut to a width by height picture centered on it. Missing tiles are gray."""
    cx, cy = _pixel(lat, lon, zoom)
    left, top = cx - width / 2, cy - height / 2
    first_x, last_x = int(left // TILE), int((left + width - 1) // TILE)
    first_y, last_y = int(top // TILE), int((top + height - 1) // TILE)
    limit = 2**zoom
    wanted = [(x, y) for y in range(first_y, last_y + 1) for x in range(first_x, last_x + 1) if 0 <= x < limit and 0 <= y < limit]

    def load(xy: tuple[int, int]) -> tuple[tuple[int, int], Image.Image | None]:
        data = fetch(template.replace("{z}", str(zoom)).replace("{y}", str(xy[1])).replace("{x}", str(xy[0])))
        if not data:
            return xy, None
        try:
            return xy, Image.open(io.BytesIO(data)).convert("RGBA")
        except OSError:
            return xy, None

    canvas = Image.new("RGB", (width, height), (70, 74, 82))
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (x, y), tile in pool.map(load, wanted):
            if tile is None:
                continue
            offset = (round(x * TILE - left), round(y * TILE - top))
            canvas.paste(tile, offset, tile)  # transparent parts (no data) stay gray
    return canvas


def _font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT), size)
    try:
        font.set_variation_by_axes([min(max(size, 14), 32), weight])
    except (OSError, AttributeError):  # a font without the axes: the default look is fine
        pass
    return font


def _label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[tuple[str, ImageFont.FreeTypeFont]], right: bool = False) -> None:
    """A dark rounded box with lines of white text, anchored at its left or right top corner."""
    pad = 14
    widths = [draw.textlength(text, font=font) for text, font in lines]
    box_w = int(max(widths)) + 2 * pad
    box_h = sum(font.size + 6 for _, font in lines) + pad
    x, y = xy
    if right:
        x -= box_w
    draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=12, fill=(15, 18, 24, 215))
    cursor = y + pad - 4
    for text, font in lines:
        draw.text((x + pad, cursor), text, font=font, fill=(255, 255, 255))
        cursor += font.size + 6


def render_card(
    fetch: Fetch,
    imagery: dict,
    lat: float,
    lon: float,
    title: str,
    subtitle: str,
    extent_km: float = 10.0,
) -> bytes:
    """The card as JPEG bytes. `imagery` is the answer of the imagery search (before and after)."""
    card = Image.new("RGB", (WIDTH, HEIGHT), (18, 21, 26))
    sides = [("Öncesi", imagery.get("before")), ("Sonrası", imagery.get("after"))]
    for index, (_, image) in enumerate(sides):
        if image is None:
            continue
        zoom = zoom_for(extent_km, image["max_native_zoom"])
        card.paste(mosaic(fetch, image["tile_url"], zoom, lat, lon, HALF, HEIGHT), (index * HALF, 0))

    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    # A dark band at the bottom for the title, fading out upward.
    for i in range(200):
        alpha = int(238 * (i / 199) ** 1.5)
        y = HEIGHT - 200 + i
        draw.line([(0, y), (WIDTH, y)], fill=(10, 12, 16, alpha))
    draw.rectangle((HALF - 2, 0, HALF + 1, HEIGHT), fill=(255, 255, 255, 235))

    small, medium = _font(22, 500), _font(26, 700)
    for index, (name, image) in enumerate(sides):
        anchor = (24, 24) if index == 0 else (WIDTH - 24, 24)
        if image is None:
            _label(draw, anchor, [(name, medium), ("bulutsuz görüntü yok", small)], right=bool(index))
            continue
        detail = f"{image['source']} · {image['resolution_m']} m · {QUALITY.get(image['quality'], image['quality'])}"
        _label(draw, anchor, [(f"{name} · {short_day(image['date'])}", medium), (detail, small)], right=bool(index))

    draw.text((32, HEIGHT - 112), title, font=_font(48, 750), fill=(255, 255, 255))
    draw.text((32, HEIGHT - 52), subtitle, font=_font(26, 450), fill=(226, 230, 236))
    mark = "Kıvılcım · NASA GIBS · resmi kayıt değildir"
    draw.text((WIDTH - 32 - draw.textlength(mark, font=small), HEIGHT - 44), mark, font=small, fill=(226, 230, 236))

    out = Image.alpha_composite(card.convert("RGBA"), layer).convert("RGB")
    buffer = io.BytesIO()
    out.save(buffer, "JPEG", quality=86, optimize=True)
    return buffer.getvalue()
