"""Finds satellite images from before and after a fire, from NASA GIBS (free, no key).

The best open imagery for this is the Harmonized Landsat Sentinel-2 product at 30 m. It exists
only on the days a satellite passes a place, and often has cloud, so for an event the nearest clear
day before it started and after it ended are searched for. A tile is downloaded and measured: how
much of it holds data and how much of that looks like cloud. VIIRS daily imagery (250 m) is the
fallback when no usable 30 m image is found; it is coarse and mostly shows smoke, which is why it
is flagged as low resolution.

Images are only linked to (their tile URLs go to the browser); nothing is stored but the choice.
"""

import math
import struct
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import httpx

ATTRIBUTION = "Imagery: NASA GIBS / ESDIS (Harmonized Landsat Sentinel-2, VIIRS)"

WMTS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{layer}/default/{date}/GoogleMapsCompatible_Level{level}/{{z}}/{{y}}/{{x}}.{ext}"
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi"

SEARCH_DAYS_BEFORE = 21
SEARCH_DAYS_AFTER = 28
MIN_GAP_AFTER = 1  # the "after" image is at least this many days after the last detection
LATENCY_DAYS = 2  # GIBS publishes a day or two late
PROBE_ZOOM = 10  # one tile of about 39 km at the equator; enough to judge a place
MIN_VALID = 0.85  # share of the tile that holds data
CLEAR_CLOUD = 0.10  # at most this much cloud counts as a clear image
USABLE_CLOUD = 0.35  # above this an image is too cloudy to use as a first choice
WORKERS = 8


@dataclass(frozen=True)
class Layer:
    id: str
    label: str
    resolution_m: int
    level: int  # the tile matrix set (GoogleMapsCompatible_Level<N>) and so the deepest zoom
    ext: str


HLS_S30 = Layer("HLS_S30_Nadir_BRDF_Adjusted_Reflectance", "Sentinel-2", 30, 12, "png")
HLS_L30 = Layer("HLS_L30_Nadir_BRDF_Adjusted_Reflectance", "Landsat", 30, 12, "png")
VIIRS = Layer("VIIRS_SNPP_CorrectedReflectance_TrueColor", "VIIRS", 250, 9, "jpg")
SHARP_LAYERS = (HLS_S30, HLS_L30)


@dataclass(frozen=True)
class Candidate:
    day: date
    layer: Layer
    valid: float  # 0..1, the share of the tile that has data
    cloud: float  # 0..1, the share of the data that looks like cloud

    @property
    def quality(self) -> str:
        if self.valid < MIN_VALID:
            return "partial"
        if self.cloud <= CLEAR_CLOUD:
            return "clear"
        return "partly_cloudy" if self.cloud <= USABLE_CLOUD else "cloudy"

    @property
    def score(self) -> float:
        """How much clear ground the tile shows, 0 to 1."""
        return self.valid * (1 - self.cloud)


# ---- tiles ------------------------------------------------------------------


def tile_for(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """The (x, y) of the web-mercator tile that contains a point."""
    n = 2**zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_bbox(zoom: int, x: int, y: int) -> tuple[float, float, float, float]:
    """The tile's box in web-mercator meters: (min_x, min_y, max_x, max_y)."""
    size = 2 * 20037508.342789244 / 2**zoom
    left = -20037508.342789244 + x * size
    top = 20037508.342789244 - y * size
    return left, top - size, left + size, top


def tile_template(layer: Layer, day: date) -> str:
    """A Leaflet tile URL template ({z}, {y}, {x} left in) for one layer on one day."""
    return WMTS.format(layer=layer.id, date=day.isoformat(), level=layer.level, ext=layer.ext)


# ---- reading a PNG ----------------------------------------------------------


def decode_png(data: bytes) -> tuple[int, int, list[bytes]] | None:
    """Decode an 8-bit PNG to rows of RGBA bytes. Returns None for anything this does not handle.

    Written out here so no imaging library is needed. It supports grayscale, RGB, palette and
    their alpha forms, all non-interlaced at 8 bits, which is what the tile servers send.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, header, palette, transparency, idat = 8, None, None, None, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None or not idat:
        return None
    width, height, depth, color, _, _, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if depth != 8 or interlace != 0 or channels is None or (color == 3 and palette is None):
        return None
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None

    stride = width * channels
    if len(raw) < height * (stride + 1):
        return None
    previous = bytearray(stride)
    rows = []
    for row in range(height):
        start = row * (stride + 1)
        filter_type, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif filter_type == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + predictor) & 255
        elif filter_type != 0:
            return None
        previous = line
        rows.append(bytes(_to_rgba(line, color, width, palette, transparency)))
    return width, height, rows


def _to_rgba(line: bytearray, color: int, width: int, palette: bytes | None, transparency: bytes | None) -> bytearray:
    out = bytearray(width * 4)
    for x in range(width):
        if color == 6:
            out[x * 4 : x * 4 + 4] = line[x * 4 : x * 4 + 4]
        elif color == 2:
            out[x * 4 : x * 4 + 4] = bytes((line[x * 3], line[x * 3 + 1], line[x * 3 + 2], 255))
        elif color == 0:
            out[x * 4 : x * 4 + 4] = bytes((line[x], line[x], line[x], 255))
        elif color == 4:
            out[x * 4 : x * 4 + 4] = bytes((line[x * 2], line[x * 2], line[x * 2], line[x * 2 + 1]))
        else:  # palette
            index = line[x]
            alpha = transparency[index] if transparency and index < len(transparency) else 255
            out[x * 4 : x * 4 + 4] = bytes((palette[index * 3], palette[index * 3 + 1], palette[index * 3 + 2], alpha))
    return out


def measure(rows: list[bytes]) -> tuple[float, float]:
    """(valid, cloud) for a decoded tile.

    Valid pixels have some opacity and are not pure black (the fill outside a satellite swath).
    Cloud pixels are bright and nearly colorless: white or pale gray.
    """
    total = valid = cloud = 0
    for row in rows:
        for i in range(0, len(row), 4):
            r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
            total += 1
            if a < 128 or r + g + b == 0:
                continue
            valid += 1
            if min(r, g, b) >= 175 and max(r, g, b) - min(r, g, b) <= 45:
                cloud += 1
    if total == 0 or valid == 0:
        return 0.0, 0.0
    return valid / total, cloud / valid


# ---- probing GIBS -----------------------------------------------------------


def probe_sharp(client: httpx.Client, layer: Layer, day: date, lat: float, lon: float) -> Candidate:
    """Download one tile of a 30 m layer on one day and measure it. Missing data is valid=0."""
    x, y = tile_for(lat, lon, PROBE_ZOOM)
    url = tile_template(layer, day).format(z=PROBE_ZOOM, y=y, x=x)
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return Candidate(day, layer, 0.0, 0.0)
    decoded = decode_png(response.content) if response.status_code == 200 else None
    if decoded is None:
        return Candidate(day, layer, 0.0, 0.0)
    valid, cloud = measure(decoded[2])
    return Candidate(day, layer, valid, cloud)


def probe_viirs(client: httpx.Client, day: date, lat: float, lon: float) -> Candidate:
    """The same for the daily 250 m layer, asked for as a PNG through the WMS so it can be measured."""
    x, y = tile_for(lat, lon, 8)
    left, bottom, right, top = tile_bbox(8, x, y)
    try:
        response = client.get(
            WMS,
            params={
                "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1", "LAYERS": VIIRS.id, "STYLES": "",
                "SRS": "EPSG:3857", "BBOX": f"{left},{bottom},{right},{top}", "WIDTH": 256, "HEIGHT": 256,
                "FORMAT": "image/png", "TIME": day.isoformat(),
            },
        )
    except httpx.HTTPError:
        return Candidate(day, VIIRS, 0.0, 0.0)
    decoded = decode_png(response.content) if response.status_code == 200 else None
    if decoded is None:
        return Candidate(day, VIIRS, 0.0, 0.0)
    valid, cloud = measure(decoded[2])
    return Candidate(day, VIIRS, valid, cloud)


def choose(candidates: list[Candidate], nearest_to: date) -> Candidate | None:
    """The best candidate: the nearest in time among the usable ones, else the clearest.

    Usable means a full tile with at most USABLE_CLOUD cloud. Among those, a clear image is
    preferred over a partly cloudy one unless the clear one is more than 8 days farther away.
    """
    full = [c for c in candidates if c.valid >= MIN_VALID]
    if not full:
        return None
    usable = [c for c in full if c.cloud <= USABLE_CLOUD]
    if usable:
        def distance(c: Candidate) -> int:
            return abs((c.day - nearest_to).days)

        clear = [c for c in usable if c.quality == "clear"]
        best_clear = min(clear, key=distance) if clear else None
        best_any = min(usable, key=distance)
        if best_clear and distance(best_clear) <= distance(best_any) + 8:
            return best_clear
        return best_any
    return max(full, key=lambda c: (c.score, -abs((c.day - nearest_to).days)))


# ---- one event --------------------------------------------------------------


def _describe(candidate: Candidate | None, label: str) -> dict | None:
    if candidate is None:
        return None
    layer = candidate.layer
    return {
        "label": label,
        "date": candidate.day.isoformat(),
        "layer": layer.id,
        "source": layer.label,
        "resolution_m": layer.resolution_m,
        "max_native_zoom": layer.level,
        "quality": candidate.quality,
        "cloud": round(candidate.cloud, 2),
        "tile_url": tile_template(layer, candidate.day),
    }


def find_before_after(
    lat: float,
    lon: float,
    start: date,
    end: date,
    today: date,
    client: httpx.Client | None = None,
    workers: int = WORKERS,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """The imagery to show for an event: one image from before it began and one from after it ended.

    Each side is searched outward from the event in weekly steps, so a clear image nearby ends the
    search early. Where no 30 m image is usable, the 250 m VIIRS image is used and flagged.
    """
    own = client is None
    http = client or httpx.Client(timeout=15, headers={"User-Agent": "kivilcim-imagery"})
    last_day = today - timedelta(days=LATENCY_DAYS)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:

            def search(days: list[date], nearest_to: date) -> tuple[Candidate | None, list[Candidate]]:
                gathered: list[Candidate] = []
                for start_index in range(0, len(days), 7):
                    week = days[start_index : start_index + 7]
                    jobs = [(layer, d) for d in week for layer in SHARP_LAYERS]
                    gathered += list(pool.map(lambda job: probe_sharp(http, job[0], job[1], lat, lon), jobs))
                    picked = choose(gathered, nearest_to)
                    if picked and picked.quality == "clear":
                        break
                return choose(gathered, nearest_to), gathered

            before_days = [start - timedelta(days=i) for i in range(1, SEARCH_DAYS_BEFORE + 1)]
            after_days = [
                end + timedelta(days=i) for i in range(MIN_GAP_AFTER, SEARCH_DAYS_AFTER + 1)
                if end + timedelta(days=i) <= last_day
            ]
            before, _ = search(before_days, start)
            after, _ = search(after_days, end) if after_days else (None, [])

            def fallback(days: list[date], nearest_to: date) -> Candidate | None:
                nearest = sorted(days, key=lambda d: abs((d - nearest_to).days))[:6]
                found = list(pool.map(lambda d: probe_viirs(http, d, lat, lon), nearest))
                return choose(found, nearest_to)

            before_low = after_low = False
            if before is None or before.quality in ("cloudy", "partial"):
                alt = fallback(before_days, start)
                if alt and (before is None or alt.score > before.score):
                    before, before_low = alt, True
            if after_days and (after is None or after.quality in ("cloudy", "partial")):
                alt = fallback(after_days, end)
                if alt and (after is None or alt.score > after.score):
                    after, after_low = alt, True
    finally:
        if own:
            http.close()

    result = {"before": _describe(before, "Öncesi"), "after": _describe(after, "Sonrası"), "attribution": ATTRIBUTION}
    result["low_resolution"] = before_low or after_low
    return result


def to_dict(candidate: Candidate) -> dict:
    return {**asdict(candidate), "day": candidate.day.isoformat(), "layer": candidate.layer.id}
