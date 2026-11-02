"""What a shared link looks like and what search engines read: cards, previews, province pages, embed."""

import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from kivilcim import cards
from kivilcim.api import create_app
from kivilcim.cards import mosaic, render_card, short_day, zoom_for
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import FireDetection
from kivilcim.seo import head_tags, province_by_slug, provinces, slugify

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def png(color, size=256, alpha=255):
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), (*color, alpha)).save(buffer, "PNG")
    return buffer.getvalue()


def image(day="2026-09-05", source="Sentinel-2", quality="clear", zoom=12):
    return {
        "date": day, "source": source, "resolution_m": 30, "quality": quality, "max_native_zoom": zoom,
        "tile_url": "https://tiles.example/{z}/{y}/{x}.png",
    }


# ---- the picture ------------------------------------------------------------


def test_zoom_follows_the_size_of_the_fire_and_the_images_own_limit():
    assert zoom_for(8, 12) == 12 and zoom_for(25, 12) == 11 and zoom_for(60, 12) == 10
    assert zoom_for(8, 9) == 9  # a coarse image cannot be zoomed past its own detail


def test_a_mosaic_is_cut_to_size_and_missing_tiles_stay_gray():
    seen = []

    def fetch(url):
        seen.append(url)
        return png((200, 30, 30)) if "/2" in url.split("tiles.example")[1][:3] else None

    picture = mosaic(fetch, "https://tiles.example/{z}/{y}/{x}.png", 10, 38.0, 35.0, 600, 630)
    assert picture.size == (600, 630)
    assert 4 <= len(seen) <= 12 and all(u.startswith("https://tiles.example/10/") for u in seen)


def test_the_card_is_a_1200_by_630_jpeg_with_both_sides_drawn():
    def fetch(url):
        return png((40, 160, 60)) if "/10/" in url or "/11/" in url or "/12/" in url else None

    data = render_card(fetch, {"before": image(), "after": image("2026-09-20", "Landsat")}, 38.0, 35.0, "Çankaya, Ankara", "1-3 Eylül 2026 · 3 gün", 10)
    picture = Image.open(io.BytesIO(data))
    assert picture.format == "JPEG" and picture.size == (1200, 630)
    # The green ground shows on both halves, and a white line divides them.
    assert picture.getpixel((300, 200))[1] > 100 and picture.getpixel((900, 200))[1] > 100
    assert min(picture.getpixel((600, 200))) > 200


def test_a_card_with_one_side_missing_still_renders():
    data = render_card(lambda _: png((90, 90, 200)), {"before": image(), "after": None}, 38.0, 35.0, "Yer", "tarih", 10)
    assert Image.open(io.BytesIO(data)).size == (1200, 630)


def test_the_font_has_the_turkish_letters():
    assert cards.FONT.is_file()
    font = cards._font(30)
    for letter in "ğüşıöçİĞÜŞÖÇ":
        assert font.getmask(letter).getbbox() is not None, letter  # a missing glyph draws nothing


def test_days_are_written_in_turkish_months():
    assert short_day("2026-09-05") == "5 Eyl" and short_day("2026-10-01T10:00:00Z") == "1 Eki"


# ---- the pages' previews ----------------------------------------------------


def test_names_become_plain_slugs():
    assert slugify("Şanlıurfa") == "sanliurfa" and slugify("Kahramanmaraş") == "kahramanmaras"
    assert slugify("İstanbul") == "istanbul" and slugify("Afyonkarahisar") == "afyonkarahisar"
    assert len(provinces()) == 81 and len({p["slug"] for p in provinces()}) == 81
    assert province_by_slug("sanliurfa")["name"] == "Şanlıurfa" and province_by_slug("nowhere") is None


def test_preview_tags_are_escaped():
    tags = head_tags("https://k.example", "/x?a=1&b=2", 'A "quoted" <title>', "d & e", "https://k.example/c.jpg")
    assert "&quot;quoted&quot;" in tags and "<title>" not in tags and "&amp;" in tags
    assert 'name="twitter:card" content="summary_large_image"' in tags and "og:image" in tags


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 's.db'}")
    init_db(engine)
    return make_session_factory(engine)


def add_fire(session_factory, lat, lon, hours_ago=2.0, frp=40.0):
    with session_factory() as s:
        s.add(
            FireDetection(
                source="VIIRS_SNPP_NRT", satellite="N", instrument="VIIRS", latitude=lat, longitude=lon,
                acquired_at=NOW - timedelta(hours=hours_ago), confidence_raw="n", confidence_level="nominal",
                brightness=330.0, frp=frp, ingested_at=NOW,
            )
        )
        s.commit()


def make_client(session_factory, **settings):
    app = create_app(session_factory, now=lambda: NOW, http_client=httpx.Client(), settings=Settings(_env_file=None, **settings))
    return TestClient(app)


def seed_event(session_factory):
    with session_factory() as s:
        for n in range(3):
            when = datetime(2026, 9, 7 + n, 10, tzinfo=UTC)
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


def test_the_home_page_has_a_title_a_description_and_a_preview(session_factory):
    html = make_client(session_factory, public_url="https://kivilcim.example").get("/").text
    assert "<title>Kıvılcım" in html and 'rel="canonical" href="https://kivilcim.example/"' in html
    assert 'property="og:image" content="https://kivilcim.example/static/icons/icon-512.png"' in html
    assert html.count("<title>") == 1 and html.count('name="description"') == 1


def test_a_place_link_is_previewed_with_its_name(session_factory):
    html = make_client(session_factory).get("/", params={"lat": 39.9, "lon": 32.8, "name": 'Çankaya "merkez"'}).text
    assert "Çankaya &quot;merkez&quot; · yakındaki yangınlar" in html and "<title>Çankaya &quot;merkez&quot;" in html.replace("&#x27;", "'")


@respx.mock
def test_a_link_to_a_fire_carries_its_card_as_the_preview(session_factory):
    seed_event(session_factory)
    client = make_client(session_factory, public_url="https://kivilcim.example")
    event = client.get("/events").json()["events"][0]
    html = client.get(f"/history/{event['id']}").text
    assert f'content="https://kivilcim.example/events/{event["id"]}/card.jpg"' in html
    assert event["district"] in html and "öncesi ve sonrası uydu görüntüsü" in html


def test_a_link_to_an_unknown_fire_is_still_a_page(session_factory):
    response = make_client(session_factory).get("/history/nope")
    assert response.status_code == 200 and "Geçmiş yangın" in response.text


def test_a_check_link_names_the_card_with_the_dates(session_factory):
    html = make_client(session_factory, public_url="https://k.example").get(
        "/check", params={"lat": 37.45, "lon": 35.45, "start": "2026-09-07", "end": "2026-09-09", "name": "Köy"}
    ).text
    assert "https://k.example/imagery/place/card.jpg?lat=37.45&amp;lon=35.45&amp;start=2026-09-07&amp;end=2026-09-09&amp;name=K%C3%B6y" in html


# ---- the cards over HTTP ----------------------------------------------------

GIBS = {"host": "gibs.earthdata.nasa.gov"}


def archive(request):
    url = str(request.url)
    if "/wms/" in url:
        return httpx.Response(200, content=png((200, 200, 200)))
    day = url.split("/default/")[1].split("/")[0]
    return httpx.Response(200, content=png((60, 140, 70)) if day in {"2026-09-05", "2026-09-13"} else png((0, 0, 0), alpha=0))


@respx.mock
def test_a_fire_has_a_shareable_card(session_factory):
    respx.route(**GIBS).mock(side_effect=archive)
    seed_event(session_factory)
    client = make_client(session_factory)
    event = client.get("/events").json()["events"][0]
    response = client.get(f"/events/{event['id']}/card.jpg")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert "max-age" in response.headers["cache-control"]
    assert Image.open(io.BytesIO(response.content)).size == (1200, 630)


@respx.mock
def test_a_card_is_built_once_and_then_kept(session_factory):
    calls = []

    def counting(request):
        calls.append(str(request.url))
        return archive(request)

    respx.route(**GIBS).mock(side_effect=counting)
    seed_event(session_factory)
    client = make_client(session_factory)
    event_id = client.get("/events").json()["events"][0]["id"]
    first = client.get(f"/events/{event_id}/card.jpg").content
    made = len(calls)
    assert client.get(f"/events/{event_id}/card.jpg").content == first and len(calls) == made


def test_a_card_for_an_unknown_fire_is_not_found(session_factory):
    assert make_client(session_factory).get("/events/nope/card.jpg").status_code == 404


@respx.mock
def test_a_place_check_has_a_card_too_and_bad_dates_are_refused(session_factory):
    respx.route(**GIBS).mock(side_effect=archive)
    client = make_client(session_factory)
    ok = client.get("/imagery/place/card.jpg", params={"lat": 37.45, "lon": 35.45, "start": "2026-09-07", "name": "Evim"})
    assert ok.status_code == 200 and Image.open(io.BytesIO(ok.content)).size == (1200, 630)
    bad = client.get("/imagery/place/card.jpg", params={"lat": 37.45, "lon": 35.45, "start": "2014-01-01"})
    assert bad.status_code == 422


# ---- province pages, sitemap, robots, embed ---------------------------------


def test_a_province_page_has_text_a_search_engine_can_read(session_factory):
    add_fire(session_factory, 37.3, 35.5)  # Adana
    add_fire(session_factory, 37.32, 35.52)
    html = make_client(session_factory).get("/il/adana").text
    assert "<h1>Adana: yangın tespitleri</h1>" in html
    assert "uydular 1 bölgede toplam 2 yangın sıcaklığı tespiti" in html
    assert "112" in html and "Seyhan" in html
    assert "<title>Adana yangın tespitleri (son 24 saat) · Kıvılcım</title>" in html
    body = html.split('id="panel-body"')[1].split("<footer")[0]
    assert "skeleton" not in body and "<h1>" in body  # the text replaced the placeholder


def test_a_quiet_province_says_so_without_claiming_safety(session_factory):
    html = make_client(session_factory).get("/il/sanliurfa").text
    assert "yangın sıcaklığı yok" in html and "yangın olmadığı anlamına gelmez" in html


def test_an_unknown_province_is_not_found(session_factory):
    assert make_client(session_factory).get("/il/atlantis").status_code == 404


def test_the_sitemap_lists_the_pages_and_every_province(session_factory):
    seed_event(session_factory)
    client = make_client(session_factory, public_url="https://k.example")
    root = ET.fromstring(client.get("/sitemap.xml").content)
    urls = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    assert "https://k.example/" in urls and "https://k.example/il/sanliurfa" in urls
    assert sum("/il/" in u for u in urls) == 81 and any("/history/" in u for u in urls)


def test_robots_points_to_the_sitemap_and_keeps_crawlers_off_the_data(session_factory):
    text = make_client(session_factory, public_url="https://k.example").get("/robots.txt").text
    assert "Sitemap: https://k.example/sitemap.xml" in text
    assert "Disallow: /fires" in text and "Allow: /events/*/card.jpg" in text


def test_only_the_embed_page_may_be_shown_in_a_frame(session_factory):
    client = make_client(session_factory)
    embed = client.get("/embed")
    assert embed.status_code == 200 and "frame-ancestors *" in embed.headers["content-security-policy"]
    assert "x-frame-options" not in embed.headers and "noindex" in embed.text
    home = client.get("/")
    assert home.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in home.headers["content-security-policy"]


def test_the_embed_page_loads_only_scripts_from_this_site(session_factory):
    html = make_client(session_factory).get("/embed").text
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag or 'type="application/json"' in tag, tag
