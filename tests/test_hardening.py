import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from kivilcim.api import create_app
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import FireDetection
from kivilcim.ratelimit import RateLimiter, bucket_for
from kivilcim.security import build_csp, tile_origin

UTC = timezone.utc
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
OPEN_METEO = {"host": "api.open-meteo.com", "path": "/v1/forecast"}
STATIC = Path(__file__).parent.parent / "src" / "kivilcim" / "static"
COPYRIGHT_ESCAPED = chr(92) + "u00a9"  # how JSON writes the copyright sign


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'h.db'}")
    init_db(engine)
    return make_session_factory(engine)


def make_client(session_factory, **settings):
    settings = Settings(_env_file=None, **settings)
    app = create_app(session_factory, now=lambda: NOW, http_client=httpx.Client(), settings=settings)
    return TestClient(app)


# ---- the limiter itself -----------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_requests_up_to_the_limit_pass_and_the_next_is_refused():
    limiter = RateLimiter(Clock())
    assert [limiter.check("a", "general", 3)[0] for _ in range(4)] == [True, True, True, False]


def test_the_window_resets_after_a_minute():
    clock = Clock()
    limiter = RateLimiter(clock)
    for _ in range(3):
        limiter.check("a", "general", 2)
    assert limiter.check("a", "general", 2)[0] is False
    clock.now += 61
    assert limiter.check("a", "general", 2)[0] is True


def test_clients_and_buckets_are_counted_separately():
    limiter = RateLimiter(Clock())
    for _ in range(2):
        limiter.check("a", "general", 2)
    assert limiter.check("a", "general", 2)[0] is False
    assert limiter.check("b", "general", 2)[0] is True
    assert limiter.check("a", "weather", 2)[0] is True


def test_retry_after_counts_down_to_the_end_of_the_window():
    clock = Clock()
    limiter = RateLimiter(clock)
    limiter.check("a", "general", 1)
    clock.now += 20
    allowed, retry_after = limiter.check("a", "general", 1)
    assert allowed is False
    assert 40 <= retry_after <= 41


def test_expired_windows_are_pruned_when_the_table_grows(monkeypatch):
    import kivilcim.ratelimit as ratelimit

    monkeypatch.setattr(ratelimit, "PRUNE_ABOVE", 5)
    clock = Clock()
    limiter = RateLimiter(clock)
    for i in range(5):
        limiter.check(f"old{i}", "general", 10)
    clock.now += 120
    limiter.check("new", "general", 10)
    assert len(limiter._windows) == 1


@pytest.mark.parametrize(
    "path, bucket",
    [
        ("/fires", "general"), ("/status", "general"), ("/events", "general"), ("/events/abc", "general"),
        ("/weather", "weather"), ("/risk", "weather"), ("/risk/zones", "weather"),
        ("/", None), ("/history", None), ("/methodology", None), ("/health", None),
        ("/sw.js", None), ("/manifest.webmanifest", None), ("/static/icons/icon-32.png", None),
        ("/riskfree", "general"),  # a path that only starts with the same letters
    ],
)
def test_bucket_for_paths(path, bucket):
    assert bucket_for(path) == bucket


# ---- the limits on the API --------------------------------------------------


def test_the_general_limit_returns_429_with_retry_after(session_factory):
    client = make_client(session_factory, rate_limit_per_minute=3)
    codes = [client.get("/status").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    refused = client.get("/status")
    assert int(refused.headers["retry-after"]) >= 1
    assert "slow down" in refused.json()["detail"]


def test_pages_static_files_and_health_are_never_limited(session_factory):
    client = make_client(session_factory, rate_limit_per_minute=1, rate_limit_weather_per_minute=1)
    for path in ("/", "/history", "/methodology", "/health", "/sw.js", "/static/pwa.js"):
        assert all(client.get(path).status_code == 200 for _ in range(4)), path


@respx.mock
def test_the_weather_limit_is_separate_and_stricter(session_factory):
    respx.route(**OPEN_METEO).respond(500, text="x")
    client = make_client(session_factory, rate_limit_per_minute=100, rate_limit_weather_per_minute=2)
    codes = [client.get("/risk", params={"lat": 39.9, "lon": 32.9}).status_code for _ in range(4)]
    assert codes == [503, 503, 429, 429]  # 503: no weather; the third is refused before it asks
    assert client.get("/status").status_code == 200  # the general bucket is untouched


def test_a_limit_of_zero_turns_it_off(session_factory):
    client = make_client(session_factory, rate_limit_per_minute=0)
    assert all(client.get("/status").status_code == 200 for _ in range(20))


def test_the_forwarded_address_is_ignored_unless_a_proxy_is_trusted(session_factory):
    client = make_client(session_factory, rate_limit_per_minute=2)
    for i in range(3):
        client.get("/status", headers={"X-Forwarded-For": f"10.0.0.{i}"})
    # All three came from the same real client, so the third was refused.
    assert client.get("/status", headers={"X-Forwarded-For": "10.9.9.9"}).status_code == 429


def test_with_a_trusted_proxy_each_forwarded_address_has_its_own_budget(session_factory):
    client = make_client(session_factory, rate_limit_per_minute=2, trust_proxy=True)
    for _ in range(3):
        client.get("/status", headers={"X-Forwarded-For": "10.0.0.1, 172.16.0.1"})
    assert client.get("/status", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.get("/status", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


# ---- headers ----------------------------------------------------------------


def test_every_response_has_the_basic_security_headers(session_factory):
    client = make_client(session_factory)
    for path in ("/health", "/", "/status"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"]
        assert "geolocation=(self)" in headers["permissions-policy"]


def test_pages_get_a_content_security_policy_and_the_api_does_not(session_factory):
    client = make_client(session_factory)
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "https://tile.openstreetmap.org" in csp
    assert "content-security-policy" not in client.get("/status").headers
    assert "content-security-policy" not in client.get("/docs").headers  # Swagger loads its own assets


def test_a_rejected_request_still_carries_the_security_headers(session_factory):
    client = make_client(session_factory, rate_limit_per_minute=1)
    client.get("/status")
    refused = client.get("/status")
    assert refused.status_code == 429
    assert refused.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "tile_url, origin",
    [
        ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "https://tile.openstreetmap.org"),
        ("https://{s}.tiles.example.com/{z}/{x}/{y}.png", "https://*.tiles.example.com"),
        ("not a url", ""),
    ],
)
def test_the_csp_allows_only_the_configured_tile_host(tile_url, origin):
    assert tile_origin(tile_url) == origin
    csp = build_csp(tile_url)
    assert ("img-src " in csp) and (origin in csp if origin else True)
    assert "https://tile.openstreetmap.org" in csp or origin != "https://tile.openstreetmap.org"


# ---- the map settings -------------------------------------------------------


def test_the_map_settings_are_filled_into_both_map_pages(session_factory):
    client = make_client(
        session_factory,
        tile_url="https://tiles.example.com/{z}/{x}/{y}.png",
        tile_attribution='© Example "Maps"',
    )
    for path in ("/", "/history"):
        html = client.get(path).text
        assert "__TILE_URL__" not in html and "__TILE_ATTRIBUTION__" not in html
        assert '"https://tiles.example.com/{z}/{x}/{y}.png"' in html
        # JSON-escaped: the quotes cannot end the string, and the copyright sign is written as
        # backslash, u, 00a9 (built here so no editor or tool turns it back into the sign).
        assert '"' + COPYRIGHT_ESCAPED + ' Example ' + chr(92) + '"Maps' + chr(92) + '""' in html
    assert "https://tiles.example.com" in client.get("/").headers["content-security-policy"]


def test_a_setting_cannot_close_the_script_element(session_factory):
    static = (STATIC / "index.html").read_text(encoding="utf-8")
    client = make_client(session_factory, tile_attribution="x</script><script>alert(1)</script>")
    html = client.get("/").text
    # Closing tags must not appear that the template does not already contain.
    assert html.count("</script>") == static.count("</script>")
    assert "<" + chr(92) + "/script>" in html  # the value's closing tags were escaped instead


def test_the_default_map_is_openstreetmap(session_factory):
    html = make_client(session_factory).get("/").text
    assert '"https://tile.openstreetmap.org/{z}/{x}/{y}.png"' in html
    assert '"' + COPYRIGHT_ESCAPED + ' OpenStreetMap"' in html


# ---- sharing, size and caching ----------------------------------------------


def test_the_read_only_api_can_be_called_from_other_sites(session_factory):
    client = make_client(session_factory)
    response = client.get("/status", headers={"Origin": "https://example.org"})
    assert response.headers["access-control-allow-origin"] == "*"
    preflight = client.options(
        "/fires", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "GET"}
    )
    assert preflight.status_code == 200 and "GET" in preflight.headers["access-control-allow-methods"]
    denied = client.options(
        "/fires", headers={"Origin": "https://example.org", "Access-Control-Request-Method": "POST"}
    )
    assert denied.status_code == 400  # only GET is allowed


def test_cors_can_be_limited_to_listed_origins(session_factory):
    client = make_client(session_factory, cors_origins="https://a.example, https://b.example")
    assert client.get("/status", headers={"Origin": "https://a.example"}).headers["access-control-allow-origin"] == "https://a.example"
    assert "access-control-allow-origin" not in client.get("/status", headers={"Origin": "https://evil.example"}).headers


def test_large_responses_are_compressed(session_factory):
    with session_factory() as s:
        for i in range(300):
            s.add(
                FireDetection(
                    source="VIIRS_SNPP_NRT", satellite="N", instrument="VIIRS", latitude=39.0 + i * 0.001,
                    longitude=32.0, acquired_at=NOW - timedelta(hours=1), confidence_raw="n",
                    confidence_level="nominal", brightness=330.0, frp=5.0, ingested_at=NOW,
                )
            )
        s.commit()
    client = make_client(session_factory)
    response = client.get("/fires", params={"lat": 39.1, "lon": 32.0, "radius_km": 100, "since_hours": 6})
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["count"] == 300  # and it still decodes


def test_events_and_zones_may_be_cached_briefly_but_fires_and_status_may_not(session_factory):
    client = make_client(session_factory)
    assert client.get("/events").headers["cache-control"] == "public, max-age=60"
    assert client.get("/risk/zones").headers["cache-control"] == "public, max-age=60"
    assert "max-age" not in client.get("/fires", params={"lat": 39, "lon": 32}).headers.get("cache-control", "")
    assert "max-age" not in client.get("/status").headers.get("cache-control", "")
    assert client.get("/").headers["cache-control"] == "no-cache"


# ---- quota transparency -----------------------------------------------------


def open_meteo_payload() -> dict:
    """A forecast response with the readings a /weather request needs."""
    start = datetime(2026, 6, 28, 0, 0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(96)]
    return {
        "current": {
            "time": "2026-07-01T12:00",
            "temperature_2m": 33.5,
            "relative_humidity_2m": 22,
            "wind_speed_10m": 28.4,
            "wind_direction_10m": 240,
        },
        "hourly": {"time": times, "precipitation": [0.0] * 96},
    }


@respx.mock
def test_status_reports_the_weather_calls_used_today(session_factory):
    respx.route(**OPEN_METEO).respond(200, json=open_meteo_payload())
    client = make_client(session_factory, open_meteo_daily_quota=50)
    assert client.get("/status").json()["weather_calls_today"] == 0
    client.get("/weather", params={"lat": 39.9, "lon": 32.9})
    status = client.get("/status").json()
    assert status["weather_calls_today"] == 1 and status["weather_daily_quota"] == 50


# ---- the app shell and its files --------------------------------------------


def test_the_shell_fills_in_the_version_and_the_map_settings_as_data(session_factory):
    from kivilcim import __version__

    html = make_client(session_factory).get("/").text
    assert "__VERSION__" not in html and "__CONFIG__" not in html
    assert f"/static/js/app.js?v={__version__}" in html
    start = html.index('id="kv-config">') + len('id="kv-config">')
    config = json.loads(html[start : html.index("</script>", start)])
    assert config["tileUrl"].startswith("https://") and config["version"] == __version__


def test_pages_allow_only_scripts_from_this_site(session_factory):
    client = make_client(session_factory)
    for path in ("/", "/history", "/history/x", "/methodology"):
        csp = client.get(path).headers["content-security-policy"]
        script_src = next(part for part in csp.split("; ") if part.startswith("script-src"))
        assert script_src == "script-src 'self'", path
        assert "unpkg.com" not in csp


def test_pages_have_no_inline_scripts_and_load_nothing_from_other_hosts(session_factory):
    client = make_client(session_factory)
    for path in ("/", "/methodology"):
        html = client.get(path).text
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, tag
        assert "unpkg.com" not in html and "cdn" not in html.lower()


def test_vendored_and_icon_files_are_cached_for_long_and_the_rest_must_be_checked(session_factory):
    client = make_client(session_factory)
    assert "max-age=604800" in client.get("/static/vendor/leaflet/leaflet.js").headers["cache-control"]
    assert "max-age=604800" in client.get("/static/icons/icon-192.png").headers["cache-control"]
    for path in ("/static/css/app.css", "/static/js/app.js", "/static/ilceler.json"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_every_module_the_app_imports_exists(session_factory):
    client = make_client(session_factory)
    js = STATIC / "js"
    for file in js.glob("*.js"):
        for name in re.findall(r'from "\./([\w-]+\.js)"', file.read_text(encoding="utf-8")):
            assert (js / name).is_file(), f"{file.name} imports {name}"
            assert client.get(f"/static/js/{name}").status_code == 200


def test_the_csp_lets_pages_load_the_cloudless_satellite_tiles_and_nothing_else_new(session_factory):
    csp = make_client(session_factory).get("/").headers["content-security-policy"]
    img = next(part for part in csp.split("; ") if part.startswith("img-src"))
    assert "https://tiles.maps.eox.at" in img and "https://gibs.earthdata.nasa.gov" in img
    assert "script-src 'self'" in csp and "connect-src 'self'" in csp


def test_every_page_loads_the_night_look_after_the_structure(session_factory):
    client = make_client(session_factory)
    for path in ("/", "/methodology", "/report", "/embed"):
        html = client.get(path).text
        assert html.index("/static/css/app.css") < html.index("/static/css/night.css"), path
    assert client.get("/static/css/night.css").status_code == 200
