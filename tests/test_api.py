from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from kivilcim.api import create_app
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import FireDetection, IngestRun

UTC = timezone.utc
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def client(session_factory):
    return TestClient(create_app(session_factory, now=lambda: NOW))


def add_fire(session_factory, lat, lon, hours_ago=1.0, level="nominal", source="VIIRS_SNPP_NRT", raw="n"):
    with session_factory() as s:
        s.add(
            FireDetection(
                source=source,
                satellite="N",
                instrument="VIIRS",
                latitude=lat,
                longitude=lon,
                acquired_at=NOW - timedelta(hours=hours_ago),
                confidence_raw=raw,
                confidence_level=level,
                brightness=330.0,
                frp=5.0,
                ingested_at=NOW,
            )
        )
        s.commit()


def add_run(session_factory, source="VIIRS_SNPP_NRT", hours_ago=0.5, status="success", error=None):
    when = NOW - timedelta(hours=hours_ago)
    with session_factory() as s:
        s.add(
            IngestRun(
                source=source,
                started_at=when,
                finished_at=when,
                status=status,
                record_count=1,
                error=error,
            )
        )
        s.commit()


def get_fires(client, **params):
    params = {"lat": 39.0, "lon": 35.0, **params}
    return client.get("/fires", params=params)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_page_is_served_with_the_disclaimer(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Resmi bir uyarı sistemi değildir" in response.text
    assert "112" in response.text
    assert "AFAD" in response.text


def test_district_data_is_served_as_static_json(client):
    response = client.get("/static/ilceler.json")
    assert response.status_code == 200
    data = response.json()
    assert len(data["provinces"]) == 81
    assert "OpenStreetMap" in data["attribution"]


# ---- /weather and /risk -----------------------------------------------------

OPEN_METEO = {"host": "api.open-meteo.com", "path": "/v1/forecast"}


def open_meteo_payload() -> dict:
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


def weather_client(session_factory, **settings):
    app = create_app(
        session_factory,
        now=lambda: NOW,
        http_client=httpx.Client(),
        settings=Settings(_env_file=None, **settings),
    )
    return TestClient(app)


@respx.mock
def test_weather_returns_fields_attribution_and_cache_status(session_factory):
    respx.route(**OPEN_METEO).respond(200, json=open_meteo_payload())
    client = weather_client(session_factory)

    first = client.get("/weather", params={"lat": 39.93, "lon": 32.86}).json()
    second = client.get("/weather", params={"lat": 39.93, "lon": 32.86}).json()

    weather = first["weather"]
    assert (weather["latitude"], weather["longitude"]) == (39.9, 32.9)
    assert weather["temperature_c"] == 33.5
    assert weather["humidity_pct"] == 22
    assert weather["wind_speed_kmh"] == 28.4
    assert weather["precip_72h_mm"] == 0
    assert weather["observed_at"].startswith("2026-07-01T12:00:00")
    assert first["weather"]["cache_status"] == "fresh"
    assert second["weather"]["cache_status"] == "cached"
    assert "Open-Meteo" in first["attribution"]
    assert "112" in first["disclaimer"]


@respx.mock
def test_risk_returns_an_experimental_level_with_reasons(session_factory):
    respx.route(**OPEN_METEO).respond(200, json=open_meteo_payload())
    body = weather_client(session_factory).get("/risk", params={"lat": 39.9, "lon": 32.9}).json()

    risk = body["risk"]
    assert risk["score"] == 80.9
    assert risk["level"] == "extreme"
    assert risk["drivers"] == ["humidity", "rain", "temperature"]
    assert risk["experimental"] is True
    assert risk["index_version"] == "v2"
    assert "not the probability" in risk["note"]
    assert body["weather"]["temperature_c"] == 33.5
    assert "Open-Meteo" in body["attribution"]


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 10, "lon": 32},
        {"lat": 39, "lon": 100},
        {"lat": 35.4, "lon": 32},
        {"lat": 42.6, "lon": 32},
        {"lat": 39, "lon": 25.4},
        {"lat": 39, "lon": 45.6},
        {"lat": "abc", "lon": 32},
    ],
)
@pytest.mark.parametrize("path", ["/weather", "/risk"])
def test_weather_and_risk_reject_points_outside_turkey(session_factory, path, params):
    # No mock is registered: if the request were not rejected first it would try the network.
    assert weather_client(session_factory).get(path, params=params).status_code == 422


@respx.mock
def test_weather_and_risk_accept_the_edge_of_the_allowed_box(session_factory):
    respx.route(**OPEN_METEO).respond(200, json=open_meteo_payload())
    client = weather_client(session_factory)
    assert client.get("/risk", params={"lat": 35.5, "lon": 25.5}).status_code == 200
    assert client.get("/risk", params={"lat": 42.5, "lon": 45.5}).status_code == 200


@respx.mock
def test_upstream_failure_gives_a_generic_503(session_factory):
    respx.route(**OPEN_METEO).respond(500, text="internal secret detail")
    response = weather_client(session_factory).get("/risk", params={"lat": 39.9, "lon": 32.9})

    assert response.status_code == 503
    assert response.json() == {"detail": "Weather data is temporarily unavailable."}
    assert "secret" not in response.text


@respx.mock
def test_quota_stops_new_calls_and_the_cache_keeps_serving(session_factory):
    route = respx.route(**OPEN_METEO).respond(200, json=open_meteo_payload())
    client = weather_client(session_factory, open_meteo_daily_quota=1)

    assert client.get("/weather", params={"lat": 39.9, "lon": 32.9}).status_code == 200
    # Same area: cached. A different area: quota spent and nothing cached -> 503.
    assert client.get("/weather", params={"lat": 39.9, "lon": 32.9}).json()["weather"]["cache_status"] == "cached"
    assert client.get("/weather", params={"lat": 41.0, "lon": 29.0}).status_code == 503
    assert route.call_count == 1


# ---- /fires -----------------------------------------------------------------


def test_fires_returns_nearby_detection_with_distance_and_age(client, session_factory):
    add_fire(session_factory, 39.1, 35.0, hours_ago=2)
    body = get_fires(client, radius_km=50).json()

    assert body["count"] == 1
    fire = body["fires"][0]
    assert fire["distance_km"] == pytest.approx(11.12, abs=0.05)
    assert fire["age_minutes"] == 120
    assert fire["acquired_at"].startswith("2026-07-01T10:00:00")
    assert fire["confidence_level"] == "nominal"
    assert fire["source"] == "VIIRS_SNPP_NRT"


def test_fires_excludes_detections_outside_the_radius(client, session_factory):
    add_fire(session_factory, 39.1, 35.0)  # ~11 km
    add_fire(session_factory, 40.0, 35.0)  # ~111 km
    body = get_fires(client, radius_km=50).json()
    assert body["count"] == 1


def test_fires_excludes_old_detections(client, session_factory):
    add_fire(session_factory, 39.1, 35.0, hours_ago=2)
    add_fire(session_factory, 39.1, 35.01, hours_ago=30)
    assert get_fires(client, radius_km=50, since_hours=24).json()["count"] == 1
    assert get_fires(client, radius_km=50, since_hours=48).json()["count"] == 2


def test_fires_are_sorted_by_distance(client, session_factory):
    add_fire(session_factory, 39.3, 35.0)
    add_fire(session_factory, 39.05, 35.0)
    add_fire(session_factory, 39.15, 35.0)
    distances = [f["distance_km"] for f in get_fires(client, radius_km=100).json()["fires"]]
    assert distances == sorted(distances)
    assert len(distances) == 3


def test_low_confidence_is_listed_separately(client, session_factory):
    add_fire(session_factory, 39.1, 35.0, level="high", raw="h")
    add_fire(session_factory, 39.1, 35.01, level="low", raw="l")
    body = get_fires(client, radius_km=50).json()
    assert [f["confidence_level"] for f in body["fires"]] == ["high"]
    assert [f["confidence_level"] for f in body["low_confidence"]] == ["low"]
    assert body["count"] == 2


def test_fires_with_no_detections_is_an_honest_empty_answer(client):
    body = get_fires(client).json()
    assert body["count"] == 0
    assert body["fires"] == []
    assert body["data_as_of"]["freshness"] == "unknown"
    assert body["data_as_of"]["latest_observation_at"] is None
    assert "112" in body["disclaimer"]


def test_fires_response_carries_query_and_disclaimer(client):
    body = get_fires(client, radius_km=10, since_hours=6).json()
    assert body["query"] == {"lat": 39.0, "lon": 35.0, "radius_km": 10.0, "since_hours": 6}
    assert "not an official warning system" in body["disclaimer"]


@pytest.mark.parametrize(
    "params",
    [
        {"radius_km": 0},
        {"radius_km": -5},
        {"radius_km": 501},
        {"radius_km": "nan"},
        {"since_hours": 0},
        {"since_hours": 169},
        {"lat": 91},
        {"lat": -91},
        {"lon": 181},
        {"lon": -181},
        {"lat": "abc"},
    ],
)
def test_fires_rejects_invalid_parameters(client, params):
    assert get_fires(client, **params).status_code == 422


def test_fires_requires_coordinates(client):
    assert client.get("/fires").status_code == 422
    assert client.get("/fires", params={"lat": 39}).status_code == 422


def test_fires_accepts_boundary_values(client):
    assert get_fires(client, radius_km=500, since_hours=168).status_code == 200
    assert get_fires(client, lat=90, lon=180, radius_km=500).status_code == 200
    assert get_fires(client, lat=-90, lon=-180, radius_km=0.001).status_code == 200


def test_fires_near_antimeridian(client, session_factory):
    add_fire(session_factory, 0.0, -179.95)
    body = get_fires(client, lat=0.0, lon=179.95, radius_km=50).json()
    assert body["count"] == 1


# ---- /status and data_as_of -------------------------------------------------


def test_status_with_no_runs(client):
    body = client.get("/status").json()
    assert body["sources"] == []
    assert body["data_as_of"]["freshness"] == "unknown"
    assert body["data_as_of"]["last_ingest_at"] is None


def test_status_reports_per_source_state(client, session_factory):
    add_run(session_factory, hours_ago=0.5)
    add_fire(session_factory, 39.1, 35.0, hours_ago=4)
    body = client.get("/status").json()

    source = body["sources"][0]
    assert source["source"] == "VIIRS_SNPP_NRT"
    assert source["last_run_status"] == "success"
    assert source["delay_minutes"] == 30
    assert source["detection_count"] == 1
    assert source["latest_observation_at"].startswith("2026-07-01T08:00:00")
    assert body["data_as_of"]["freshness"] == "fresh"
    assert body["data_as_of"]["last_ingest_at"].startswith("2026-07-01T11:30:00")


def test_old_satellite_observation_does_not_make_data_stale(client, session_factory):
    # A long gap between satellite passes is normal; only the ingest age drives freshness.
    add_run(session_factory, hours_ago=0.2)
    add_fire(session_factory, 39.1, 35.0, hours_ago=11)
    data = client.get("/status").json()["data_as_of"]
    assert data["freshness"] == "fresh"
    assert data["latest_observation_at"].startswith("2026-07-01T01:00:00")


@pytest.mark.parametrize(
    "hours_ago, expected",
    [(0.1, "fresh"), (2.9, "fresh"), (3.1, "warning"), (5.9, "warning"), (6.1, "stale"), (50, "stale")],
)
def test_freshness_thresholds(client, session_factory, hours_ago, expected):
    add_run(session_factory, hours_ago=hours_ago)
    assert client.get("/status").json()["data_as_of"]["freshness"] == expected


def test_failed_run_keeps_last_success_and_shows_the_error(client, session_factory):
    add_run(session_factory, hours_ago=7, status="success")
    add_run(session_factory, hours_ago=0.1, status="failed", error="FirmsError: HTTP 500")
    body = client.get("/status").json()

    source = body["sources"][0]
    assert source["last_run_status"] == "failed"
    assert source["last_error"] == "FirmsError: HTTP 500"
    assert source["last_success_at"].startswith("2026-07-01T05:00:00")
    assert body["data_as_of"]["freshness"] == "stale"


def test_overall_freshness_is_judged_by_the_stalest_source(client, session_factory):
    add_run(session_factory, source="VIIRS_SNPP_NRT", hours_ago=0.1)
    add_run(session_factory, source="MODIS_NRT", hours_ago=8)
    data = client.get("/status").json()["data_as_of"]
    assert data["freshness"] == "stale"
    assert data["last_ingest_at"].startswith("2026-07-01T04:00:00")


def test_source_that_never_succeeded_makes_freshness_unknown(client, session_factory):
    add_run(session_factory, source="VIIRS_SNPP_NRT", hours_ago=0.1)
    add_run(session_factory, source="MODIS_NRT", hours_ago=0.1, status="failed", error="boom")
    data = client.get("/status").json()["data_as_of"]
    assert data["freshness"] == "unknown"
    assert data["last_ingest_at"] is None
