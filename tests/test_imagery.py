import json
import struct
import zlib
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from kivilcim import imagery
from kivilcim.api import create_app
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.imagery import (
    HLS_L30,
    HLS_S30,
    VIIRS,
    Candidate,
    choose,
    decode_png,
    find_before_after,
    measure,
    probe_sharp,
    probe_viirs,
    tile_bbox,
    tile_for,
    tile_template,
)
from kivilcim.models import FireDetection

UTC = timezone.utc
GIBS = {"host": "gibs.earthdata.nasa.gov"}


# ---- building PNGs for the tests -------------------------------------------


def _filter_row(kind: int, row: bytes, prev: bytes, bpp: int) -> bytes:
    out = bytearray()
    for i, value in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if kind == 0:
            predictor = 0
        elif kind == 1:
            predictor = a
        elif kind == 2:
            predictor = b
        elif kind == 3:
            predictor = (a + b) >> 1
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            predictor = a if pa <= pb and pa <= pc else b if pb <= pc else c
        out.append((value - predictor) & 255)
    return bytes([kind]) + bytes(out)


def make_png(width, height, pixel, color=6, filters=(0,), palette=None, trns=None, depth=8, interlace=0) -> bytes:
    """pixel(x, y) -> a tuple with the channels of `color` (1, 2, 3 or 4 values)."""
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    raw, prev = b"", bytes(width * channels)
    for y in range(height):
        row = bytes(v for x in range(width) for v in pixel(x, y))
        raw += _filter_row(filters[y % len(filters)], row, prev, channels)
        prev = row

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
    if palette:
        out += chunk(b"PLTE", palette)
    if trns:
        out += chunk(b"tRNS", trns)
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def solid(width=8, height=8, rgba=(120, 110, 90, 255)) -> bytes:
    return make_png(width, height, lambda x, y: rgba)


CLEAR = solid(rgba=(110, 100, 80, 255))  # brownish ground
CLOUDY = solid(rgba=(240, 240, 240, 255))  # white
BLANK = solid(rgba=(0, 0, 0, 0))  # transparent: no data


# ---- tiles ------------------------------------------------------------------


@pytest.mark.parametrize(
    "lat, lon, zoom, expected",
    [(0.0, 0.0, 1, (1, 1)), (60.0, -179.9, 1, (0, 0)), (-80.0, 179.9, 1, (1, 1)), (41.0, 29.0, 0, (0, 0))],
)
def test_tile_for_known_points(lat, lon, zoom, expected):
    assert tile_for(lat, lon, zoom) == expected


def test_tile_for_is_clamped_to_the_grid():
    assert tile_for(89.9, 180.0, 3) == (7, 0)
    assert tile_for(-89.9, -180.0, 3) == (0, 7)


def test_a_tile_contains_the_point_it_was_found_for():
    x, y = tile_for(37.45, 35.45, 10)
    left, bottom, right, top = tile_bbox(10, x, y)
    import math

    mx = 35.45 * 20037508.342789244 / 180
    my = math.log(math.tan((90 + 37.45) * math.pi / 360)) * 6378137
    assert left <= mx <= right and bottom <= my <= top


def test_the_world_tile_covers_the_whole_projection():
    left, bottom, right, top = tile_bbox(0, 0, 0)
    assert (left, bottom, right, top) == pytest.approx((-20037508.34, -20037508.34, 20037508.34, 20037508.34))
    assert tile_bbox(1, 1, 0)[0] == pytest.approx(0)


def test_tile_template_keeps_the_placeholders_for_the_map():
    url = tile_template(HLS_S30, date(2026, 9, 6))
    assert "/HLS_S30_Nadir_BRDF_Adjusted_Reflectance/default/2026-09-06/GoogleMapsCompatible_Level12/" in url
    assert url.endswith("/{z}/{y}/{x}.png")
    assert tile_template(VIIRS, date(2026, 9, 6)).endswith("Level9/{z}/{y}/{x}.jpg")


# ---- reading PNGs -----------------------------------------------------------


@pytest.mark.parametrize("filters", [(0,), (1,), (2,), (3,), (4,), (0, 1, 2, 3, 4)])
def test_every_filter_type_decodes_back_to_the_same_pixels(filters):
    def pixel(x, y):
        return ((x * 37 + y * 11) % 256, (x * 5 + y * 91) % 256, (x * 13 + y * 7) % 256, 255 - (x + y) % 40)

    width, height, rows = decode_png(make_png(13, 9, pixel, filters=filters))
    assert (width, height) == (13, 9)
    for y in range(height):
        assert rows[y] == bytes(v for x in range(width) for v in pixel(x, y))


def test_rgb_gray_gray_alpha_and_palette_images_are_converted_to_rgba():
    rgb = decode_png(make_png(2, 1, lambda x, y: (10, 20, 30), color=2))
    assert rgb[2][0] == bytes((10, 20, 30, 255) * 2)

    gray = decode_png(make_png(2, 1, lambda x, y: (77,), color=0))
    assert gray[2][0] == bytes((77, 77, 77, 255) * 2)

    gray_alpha = decode_png(make_png(2, 1, lambda x, y: (50, 99), color=4))
    assert gray_alpha[2][0] == bytes((50, 50, 50, 99) * 2)

    palette = bytes((255, 0, 0, 0, 255, 0))
    indexed = decode_png(make_png(2, 1, lambda x, y: (x,), color=3, palette=palette, trns=bytes((0, 128))))
    assert indexed[2][0] == bytes((255, 0, 0, 0, 0, 255, 0, 128))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a png at all",
        b"\x89PNG\r\n\x1a\n",  # a signature and nothing else
        make_png(4, 4, lambda x, y: (1, 2, 3, 4))[:-20],  # cut short
        make_png(4, 4, lambda x, y: (1, 2, 3, 4), interlace=1),
        make_png(4, 4, lambda x, y: (1, 2), depth=16, color=4),
        make_png(4, 4, lambda x, y: (0,), color=3),  # a palette image without a palette
    ],
)
def test_images_that_cannot_be_read_give_none(data):
    assert decode_png(data) is None


def test_a_corrupt_data_stream_gives_none():
    png = bytearray(make_png(4, 4, lambda x, y: (1, 2, 3, 4)))
    start = png.index(b"IDAT") + 4
    png[start : start + 6] = b"garbag"
    assert decode_png(bytes(png)) is None


# ---- measuring --------------------------------------------------------------


def rows_of(pixels, width=4):
    flat = [bytes(p) for p in pixels]
    return [b"".join(flat[i : i + width]) for i in range(0, len(flat), width)]


def test_clear_ground_has_full_data_and_no_cloud():
    assert measure(decode_png(CLEAR)[2]) == (1.0, 0.0)


def test_cloud_is_bright_and_colorless():
    assert measure(decode_png(CLOUDY)[2]) == (1.0, 1.0)
    assert measure(rows_of([(230, 230, 235, 255)] * 4, 2)) == (1.0, 1.0)  # a bluish white is still cloud


@pytest.mark.parametrize(
    "pixel",
    [(200, 170, 90, 255), (60, 120, 60, 255), (180, 140, 120, 255), (120, 120, 120, 255)],  # sand, forest, bare soil, gray
)
def test_ground_colors_are_not_mistaken_for_cloud(pixel):
    assert measure(rows_of([pixel] * 4, 2)) == (1.0, 0.0)


def test_no_data_means_transparent_or_pure_black():
    assert measure(decode_png(BLANK)[2]) == (0.0, 0.0)
    assert measure(rows_of([(0, 0, 0, 255)] * 4, 2)) == (0.0, 0.0)


def test_a_partly_covered_and_partly_cloudy_tile():
    pixels = [(0, 0, 0, 0)] * 4 + [(240, 240, 240, 255)] * 4 + [(100, 90, 70, 255)] * 8
    valid, cloud = measure(rows_of(pixels))
    assert valid == pytest.approx(0.75)
    assert cloud == pytest.approx(4 / 12)


def test_an_empty_image_is_not_valid():
    assert measure([]) == (0.0, 0.0)


# ---- choosing ---------------------------------------------------------------

EVENT = date(2026, 9, 7)


def cand(offset, valid=1.0, cloud=0.0, layer=HLS_S30):
    return Candidate(EVENT + timedelta(days=offset), layer, valid, cloud)


def test_quality_labels():
    assert cand(0).quality == "clear"
    assert cand(0, cloud=0.08).quality == "partly_cloudy"
    assert cand(0, cloud=0.2).quality == "cloudy"
    assert cand(0, valid=0.5).quality == "partial"


def test_the_nearest_clear_image_wins():
    assert choose([cand(-5), cand(-2), cand(-9)], EVENT).day == EVENT - timedelta(days=2)


def test_a_clear_image_beats_a_slightly_nearer_cloudy_one_but_not_a_much_nearer_one():
    near_hazy, clear_2_later, clear_far = cand(-1, cloud=0.08), cand(-4), cand(-20)
    assert choose([near_hazy, clear_2_later], EVENT) == clear_2_later  # 3 days farther is fine
    assert choose([near_hazy, clear_far], EVENT) == near_hazy  # 19 days farther is not


def test_partial_tiles_are_not_used_when_a_full_one_exists():
    assert choose([cand(-1, valid=0.4), cand(-6)], EVENT).day == EVENT - timedelta(days=6)


def test_cloudy_images_are_never_returned():
    # People take cloud for smoke, so a cloudy picture is worse than none.
    assert choose([cand(-1, cloud=0.6), cand(-3, cloud=0.24), cand(-2, cloud=0.9)], EVENT) is None


def test_nothing_usable_gives_none():
    assert choose([], EVENT) is None
    assert choose([cand(-1, valid=0.0), cand(-2, valid=0.3)], EVENT) is None


# ---- probing ----------------------------------------------------------------


@respx.mock
def test_probe_sharp_downloads_the_tile_for_that_layer_and_day():
    route = respx.route(**GIBS).respond(200, content=CLEAR)
    with httpx.Client() as client:
        result = probe_sharp(client, HLS_L30, date(2026, 9, 6), 37.45, 35.45)
    assert (result.valid, result.cloud) == (1.0, 0.0)
    url = str(route.calls[0].request.url)
    assert "HLS_L30_Nadir_BRDF_Adjusted_Reflectance/default/2026-09-06/" in url
    x, y = tile_for(37.45, 35.45, imagery.PROBE_ZOOM)
    assert url.endswith(f"/{imagery.PROBE_ZOOM}/{y}/{x}.png")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=BLANK),
        httpx.Response(200, content=b"<html>nope</html>"),
        httpx.Response(500, text="boom"),
        httpx.Response(404, text="gone"),
    ],
)
@respx.mock
def test_probe_sharp_treats_missing_or_unreadable_tiles_as_no_data(response):
    respx.route(**GIBS).mock(return_value=response)
    with httpx.Client() as client:
        assert probe_sharp(client, HLS_S30, date(2026, 9, 6), 37.0, 35.0).valid == 0.0


@respx.mock
def test_probe_sharp_survives_a_network_error():
    respx.route(**GIBS).mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as client:
        assert probe_sharp(client, HLS_S30, date(2026, 9, 6), 37.0, 35.0).valid == 0.0


@respx.mock
def test_probe_viirs_asks_the_wms_for_a_png_of_the_tile():
    route = respx.route(**GIBS).respond(200, content=CLOUDY)
    with httpx.Client() as client:
        result = probe_viirs(client, date(2026, 9, 6), 37.45, 35.45)
    assert (result.valid, result.cloud) == (1.0, 1.0)
    params = route.calls[0].request.url.params
    assert params["LAYERS"] == VIIRS.id and params["FORMAT"] == "image/png" and params["TIME"] == "2026-09-06"
    assert len(params["BBOX"].split(",")) == 4


# ---- finding the images for an event ----------------------------------------


def archive(clear_days=(), cloudy_days=(), viirs_days=None, requests=None):
    """A GIBS stand-in: HLS tiles exist only on the given days, VIIRS tiles on `viirs_days`."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if requests is not None:
            requests.append(url)
        if "/wms/" in url:
            day = request.url.params["TIME"]
            return httpx.Response(200, content=CLEAR if viirs_days is None or day in viirs_days else BLANK)
        day = url.split("/default/")[1].split("/")[0]
        if day in clear_days:
            return httpx.Response(200, content=CLEAR)
        if day in cloudy_days:
            return httpx.Response(200, content=CLOUDY)
        return httpx.Response(200, content=BLANK)

    return handler


def find(**kwargs):
    start, end = date(2026, 9, 7), date(2026, 9, 10)
    with httpx.Client() as client:
        return find_before_after(37.45, 35.45, start, end, today=date(2026, 11, 2), client=client, **kwargs)


@respx.mock
def test_the_nearest_clear_days_on_each_side_are_found():
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-04", "2026-09-06", "2026-09-13"}))
    result = find()
    assert result["before"]["date"] == "2026-09-06" and result["after"]["date"] == "2026-09-13"
    assert result["before"]["label"] == "Öncesi" and result["after"]["label"] == "Sonrası"
    assert result["before"]["quality"] == "clear" and result["before"]["resolution_m"] == 30
    assert result["before"]["source"] in ("Sentinel-2", "Landsat")
    assert result["before"]["tile_url"].endswith("/{z}/{y}/{x}.png")
    assert result["low_resolution"] is False and "NASA GIBS" in result["attribution"]


@respx.mock
def test_days_inside_the_event_and_just_after_it_are_never_used():
    # The event ran 7 to 10 September. The "before" starts the day before the first detection; the
    # "after" waits three days after the last, because a fire that just went out still smokes.
    respx.route(**GIBS).mock(
        side_effect=archive(
            clear_days={"2026-09-07", "2026-09-08", "2026-09-10", "2026-09-05", "2026-09-11", "2026-09-12", "2026-09-14"},
            viirs_days=set(),
        )
    )
    result = find()
    assert result["before"]["date"] == "2026-09-05" and result["after"]["date"] == "2026-09-14"


@respx.mock
def test_the_after_image_waits_for_a_fire_that_burned_on():
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-13", "2026-09-17"}, viirs_days=set()))
    # Fires were still seen there until 14 September, so the image is taken from 17 September on.
    result = find(quiet_from=date(2026, 9, 14))
    assert result["after"]["date"] == "2026-09-17"


@respx.mock
def test_too_soon_after_a_fire_there_is_no_after_image_yet():
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-10-31"}))
    with httpx.Client() as client:
        result = find_before_after(37.45, 35.45, date(2026, 10, 25), date(2026, 10, 31), today=date(2026, 11, 2), client=client)
    assert result["after"] is None and result["waiting"] is True


@respx.mock
def test_other_usable_days_are_offered_next_to_the_chosen_one():
    respx.route(**GIBS).mock(
        side_effect=archive(clear_days={"2026-09-06"}, cloudy_days={"2026-09-04", "2026-09-02"}, viirs_days=set())
    )
    result = find()
    days = [o["date"] for o in result["before_options"]]
    assert "2026-09-06" in days and days == sorted(days)
    assert all(o["resolution_m"] == 30 and o["label"] == "Öncesi" for o in result["before_options"])
    assert result["after_options"] == [] or all(o["label"] == "Sonrası" for o in result["after_options"])


@respx.mock
def test_a_service_that_failed_to_answer_is_reported_as_incomplete():
    respx.route(**GIBS).mock(side_effect=httpx.ConnectError("down"))
    assert find()["incomplete"] is True


@respx.mock
def test_a_failed_request_is_tried_again():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=CLEAR)

    respx.route(**GIBS).mock(side_effect=flaky)
    with httpx.Client() as client:
        result = probe_sharp(client, HLS_S30, date(2026, 9, 6), 37.0, 35.0)
    assert result.valid == 1.0 and result.failed is False and calls["n"] == 2


@respx.mock
def test_the_coarse_fallback_looks_at_four_satellites():
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(viirs_days={"2026-09-06"}, requests=requests))
    find()
    layers = {httpx.URL(u).params["LAYERS"] for u in requests if "/wms/" in u}
    assert layers == {layer.id for layer in imagery.COARSE_LAYERS}


@respx.mock
def test_a_nearby_clear_image_ends_the_search_early():
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-06", "2026-09-14"}, requests=requests))
    find()
    # One week on each side, both satellites: 2 sides x 7 days x 2 layers = 28 at the most.
    assert len(requests) <= 28


@respx.mock
def test_the_search_widens_week_by_week_when_nothing_clear_is_close():
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-08-24", "2026-09-30"}, requests=requests))
    result = find()
    assert result["before"]["date"] == "2026-08-24" and result["after"]["date"] == "2026-09-30"
    assert len(requests) > 28


@respx.mock
def test_the_after_search_stops_at_the_latest_published_day():
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(requests=requests))
    with httpx.Client() as client:
        find_before_after(37.45, 35.45, date(2026, 10, 25), date(2026, 10, 30), today=date(2026, 11, 2), client=client)
    days = {u.split("/default/")[1].split("/")[0] for u in requests if "/default/" in u}
    assert max(days) <= "2026-10-31"  # today minus the two days GIBS is late; nothing newer is asked for


@respx.mock
def test_a_cloudy_day_is_skipped_for_a_clear_one_farther_away():
    respx.route(**GIBS).mock(side_effect=archive(cloudy_days={"2026-09-06", "2026-09-12"}, clear_days={"2026-08-30", "2026-09-20"}, viirs_days=set()))
    result = find()
    assert result["before"]["date"] == "2026-08-30" and result["before"]["quality"] == "clear"
    assert result["after"]["date"] == "2026-09-20"


@respx.mock
def test_when_every_image_is_cloudy_that_side_is_left_empty():
    respx.route(**GIBS).mock(side_effect=archive(cloudy_days={f"2026-09-{d:02d}" for d in range(1, 30)}, viirs_days=set()))
    result = find()
    assert result["before"] is None and result["after"] is None


@respx.mock
def test_the_coarse_daily_image_is_the_fallback_and_is_flagged():
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-13"}, viirs_days={"2026-09-06"}))
    result = find()
    assert result["before"]["source"] == "VIIRS" and result["before"]["resolution_m"] == 250
    assert result["before"]["tile_url"].endswith(".jpg")
    assert result["low_resolution"] is True
    assert result["after"]["resolution_m"] == 30


@respx.mock
def test_no_images_at_all_gives_none_on_both_sides():
    respx.route(**GIBS).mock(side_effect=archive(viirs_days=set()))
    result = find()
    assert result["before"] is None and result["after"] is None and result["low_resolution"] is False


@respx.mock
def test_an_unreachable_service_gives_no_images_and_no_error():
    respx.route(**GIBS).mock(side_effect=httpx.ConnectError("down"))
    result = find()
    assert result["before"] is None and result["after"] is None


@respx.mock
def test_the_search_makes_its_own_client_when_none_is_given():
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-06", "2026-09-13"}))
    result = find_before_after(37.45, 35.45, date(2026, 9, 7), date(2026, 9, 10), today=date(2026, 11, 2))
    assert result["before"]["date"] == "2026-09-06"


# ---- the API ----------------------------------------------------------------


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'i.db'}")
    init_db(engine)
    return make_session_factory(engine)


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def seed_event(session_factory, day=date(2026, 9, 7)):
    with session_factory() as s:
        for n in range(3):
            when = datetime.combine(day + timedelta(days=n), datetime.min.time(), UTC) + timedelta(hours=10)
            points = [(37.45, 35.45, 150.0)] + [(37.45 + 0.03 * (i % 4 + 1), 35.45 + 0.03 * (i // 4), 20.0) for i in range(24)]
            for lat, lon, frp in points:
                s.add(
                    FireDetection(
                        source="VIIRS_SNPP_SP", satellite="N", instrument="VIIRS", latitude=lat, longitude=lon,
                        acquired_at=when, confidence_raw="n", confidence_level="nominal", brightness=330.0,
                        frp=frp, ingested_at=NOW,
                    )
                )
        s.commit()


def make_client(session_factory):
    return TestClient(create_app(session_factory, now=lambda: NOW, settings=Settings(_env_file=None)))


@respx.mock
def test_the_imagery_endpoint_returns_the_chosen_images(session_factory):
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}))
    seed_event(session_factory)
    client = make_client(session_factory)
    event_id = client.get("/events").json()["events"][0]["id"]
    body = client.get(f"/events/{event_id}/imagery").json()

    assert body["event_id"] == event_id
    assert body["before"]["date"] == "2026-09-05" and body["after"]["date"] == "2026-09-13"
    assert body["before"]["tile_url"].startswith("https://gibs.earthdata.nasa.gov/")
    assert "clear satellite images" in body["note"] and "112" in body["disclaimer"]
    assert body["low_resolution"] is False


@respx.mock
def test_a_final_choice_is_stored_and_not_searched_again(session_factory):
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}, requests=requests))
    seed_event(session_factory)
    client = make_client(session_factory)
    event_id = client.get("/events").json()["events"][0]["id"]

    first = client.get(f"/events/{event_id}/imagery").json()
    made = len(requests)
    second = client.get(f"/events/{event_id}/imagery").json()
    assert made > 0 and len(requests) == made  # the second answer came from the database
    assert first == second
    # And it survives a new app on the same database.
    assert make_client(session_factory).get(f"/events/{event_id}/imagery").json() == first
    assert len(requests) == made


@respx.mock
def test_an_incomplete_choice_is_looked_at_again_later_but_not_at_once(session_factory):
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05"}, viirs_days=set(), requests=requests))
    seed_event(session_factory)
    event_id = make_client(session_factory).get("/events").json()["events"][0]["id"]

    clock = {"now": NOW}
    client = TestClient(create_app(session_factory, now=lambda: clock["now"], settings=Settings(_env_file=None)))
    first = client.get(f"/events/{event_id}/imagery").json()
    assert first["before"] and first["after"] is None
    made = len(requests)

    client.get(f"/events/{event_id}/imagery")
    assert len(requests) == made  # within the retry period nothing new is asked

    clock["now"] = NOW + timedelta(hours=7)
    client.get(f"/events/{event_id}/imagery")
    assert len(requests) > made  # after the retry period the search runs again


@respx.mock
def test_an_unreachable_imagery_service_is_retried_soon_not_in_hours(session_factory):
    respx.route(**GIBS).mock(side_effect=httpx.ConnectError("down"))
    seed_event(session_factory)
    event_id = make_client(session_factory).get("/events").json()["events"][0]["id"]
    clock = {"now": NOW}
    client = TestClient(create_app(session_factory, now=lambda: clock["now"], settings=Settings(_env_file=None)))
    body = client.get(f"/events/{event_id}/imagery").json()
    assert body["before"] is None and body["after"] is None

    from kivilcim.models import EventImagery

    with session_factory() as s:
        row = s.get(EventImagery, event_id)
        assert row.ttl_seconds == 15 * 60
        assert json.loads(row.payload)["before"] is None


def test_imagery_for_an_unknown_event_is_404(session_factory):
    assert make_client(session_factory).get("/events/20260101-1_1/imagery").status_code == 404


@respx.mock
def test_the_imagery_search_has_its_own_stricter_limit(session_factory):
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}))
    seed_event(session_factory)
    settings = Settings(_env_file=None, rate_limit_imagery_per_minute=2)
    client = TestClient(create_app(session_factory, now=lambda: NOW, settings=settings))
    event_id = client.get("/events").json()["events"][0]["id"]
    codes = [client.get(f"/events/{event_id}/imagery").status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]
    assert client.get("/events").status_code == 200  # other routes are untouched


def test_the_imagery_path_belongs_to_the_imagery_bucket():
    from kivilcim.ratelimit import bucket_for

    assert bucket_for("/events/20260907-374_355/imagery") == "imagery"
    assert bucket_for("/events/20260907-374_355") == "general"
    assert bucket_for("/events") == "general"


@respx.mock
def test_a_fire_still_seen_after_the_event_moves_the_after_image_later(session_factory):
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13", "2026-09-20"}, viirs_days=set()))
    seed_event(session_factory)
    with session_factory() as s:  # one more weak detection next to the event, eight days after it ended
        s.add(
            FireDetection(
                source="VIIRS_SNPP_SP", satellite="N", instrument="VIIRS", latitude=37.47, longitude=35.46,
                acquired_at=datetime(2026, 9, 17, 10, tzinfo=UTC), confidence_raw="n", confidence_level="nominal",
                brightness=330.0, frp=8.0, ingested_at=NOW,
            )
        )
        s.commit()
    client = make_client(session_factory)
    event_id = client.get("/events").json()["events"][0]["id"]
    body = client.get(f"/events/{event_id}/imagery").json()
    assert body["after"]["date"] == "2026-09-20"  # 17 September plus three days, not 13 September
    assert [o["date"] for o in body["after_options"]] == ["2026-09-20"] or body["after"] in body["after_options"]


@respx.mock
def test_the_images_of_big_fires_can_be_searched_ahead_of_time(session_factory):
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}, requests=requests))
    seed_event(session_factory)
    client = make_client(session_factory)
    client.app.state.warm_imagery()
    made = len(requests)
    assert made > 0
    event_id = client.get("/events").json()["events"][0]["id"]
    assert client.get(f"/events/{event_id}/imagery").json()["before"]["date"] == "2026-09-05"
    assert len(requests) == made  # answered from what was stored


@respx.mock
def test_a_choice_stored_by_an_older_version_is_searched_again(session_factory):
    from kivilcim.models import EventImagery

    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}, requests=requests))
    seed_event(session_factory)
    client = make_client(session_factory)
    event_id = client.get("/events").json()["events"][0]["id"]
    old = {"before": None, "after": None, "attribution": "x", "low_resolution": False}
    with session_factory() as s:
        s.add(EventImagery(event_id=event_id, computed_at=NOW, ttl_seconds=10**9, payload=json.dumps(old)))
        s.commit()
    body = client.get(f"/events/{event_id}/imagery").json()
    assert requests and body["before"]["date"] == "2026-09-05"


# ---- checking any place -----------------------------------------------------


@respx.mock
def test_any_place_can_be_checked_for_the_images_before_and_after_a_fire(session_factory):
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}))
    client = make_client(session_factory)
    body = client.get("/imagery/place", params={"lat": 37.4537, "lon": 35.4549, "start": "2026-09-07", "end": "2026-09-09"}).json()
    assert body["before"]["date"] == "2026-09-05" and body["after"]["date"] == "2026-09-13"
    assert body["event_id"].startswith("p37.45_35.45_")  # coordinates are rounded to about a kilometer


@respx.mock
def test_a_check_is_stored_and_not_searched_again(session_factory):
    requests = []
    respx.route(**GIBS).mock(side_effect=archive(clear_days={"2026-09-05", "2026-09-13"}, requests=requests))
    client = make_client(session_factory)
    params = {"lat": 37.45, "lon": 35.45, "start": "2026-09-07", "end": "2026-09-09"}
    first = client.get("/imagery/place", params=params).json()
    made = len(requests)
    assert client.get("/imagery/place", params={**params, "lat": 37.4549}).json() == first  # same kilometer
    assert len(requests) == made


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 37.45, "lon": 35.45, "start": "2015-01-01"},  # before the archive
        {"lat": 37.45, "lon": 35.45, "start": "2026-12-01"},  # in the future
        {"lat": 37.45, "lon": 35.45, "start": "2026-09-09", "end": "2026-09-07"},  # backwards
        {"lat": 37.45, "lon": 35.45, "start": "2026-01-01", "end": "2026-09-01"},  # far too long
        {"lat": 10.0, "lon": 35.45, "start": "2026-09-07"},  # outside Turkey
    ],
)
def test_checks_with_unusable_places_or_dates_are_refused(session_factory, params):
    assert make_client(session_factory).get("/imagery/place", params=params).status_code == 422


def test_the_latest_fire_near_a_place_suggests_the_dates(session_factory):
    seed_event(session_factory)  # 7 to 9 September at 37.45, 35.45
    client = TestClient(create_app(session_factory, now=lambda: datetime(2026, 9, 20, tzinfo=UTC), settings=Settings(_env_file=None)))
    body = client.get("/imagery/suggest", params={"lat": 37.46, "lon": 35.46}).json()
    assert body["found"] is True and body["start"] == "2026-09-07" and body["end"] == "2026-09-09"
    assert body["detections"] > 0 and body["max_frp_mw"] == 150.0
    far = client.get("/imagery/suggest", params={"lat": 39.9, "lon": 32.85}).json()
    assert far["found"] is False


def test_the_check_page_is_the_app_shell(session_factory):
    client = make_client(session_factory)
    page, home = client.get("/check").text, client.get("/").text
    assert page[page.index("<body>"):] == home[home.index("<body>"):]
