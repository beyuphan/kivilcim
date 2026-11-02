from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from kivilcim.api import create_app
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
