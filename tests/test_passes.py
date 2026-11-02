from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from kivilcim import passes
from kivilcim.api import create_app
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import OrbitElements
from kivilcim.passes import (
    SATELLITES,
    coverage_summary,
    elements_age,
    fetch_elements,
    find_passes,
    parse_elements,
    refresh_elements,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

# Real elements of the five satellites, fetched from CelesTrak on 2 November 2026.
TLE = {
    37849: ("SUOMI NPP", "1 37849U 11061A   26306.13961205  .00000062  00000+0  50263-4 0  9991", "2 37849  98.8095 246.1414 0000901 320.9229  39.1882 14.19542419778015"),
    43013: ("NOAA 20 (JPSS-1)", "1 43013U 17073A   26306.08306191  .00000054  00000+0  46463-4 0  9999", "2 43013  98.7904 244.3821 0000671 138.5550 221.5677 14.19534300464032"),
    54234: ("NOAA 21 (JPSS-2)", "1 54234U 22150A   26306.11367440  .00000026  00000+0  32874-4 0  9996", "2 54234  98.7184 242.6385 0002267 133.4261 226.7104 14.19556351206116"),
    25994: ("TERRA", "1 25994U 99068A   26306.13560867  .00000209  00000+0  51484-4 0  9990", "2 25994  97.9285 350.0946 0003146 132.7860 253.0818 14.61187506429752"),
    27424: ("AQUA", "1 27424U 02022A   26306.20664023  .00000498  00000+0  10855-3 0  9997", "2 27424  98.4476 278.3862 0000878 100.1888 270.2904 14.62259337303444"),
}
ANKARA = (39.93, 32.86)


def rows(fetched=NOW):
    return {n: OrbitElements(norad=n, name=v[0], line1=v[1], line2=v[2], fetched_at=fetched) for n, v in TLE.items()}


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(passes, "FETCH_PAUSE", 0)


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    init_db(engine)
    return make_session_factory(engine)


# ---- the orbit model --------------------------------------------------------


def solar_hour(when, lon):
    """Local mean solar time in hours at a longitude."""
    return (when.hour + when.minute / 60 + lon / 15) % 24


def test_each_satellite_scans_a_place_a_few_times_a_day():
    found = find_passes(rows(), *ANKARA, NOW - timedelta(hours=24), NOW + timedelta(hours=24))
    for sat in SATELLITES:
        count = sum(p.code == sat.code for p in found)
        assert 4 <= count <= 14, (sat.name, count)  # two days, one or two scans on each side of the Earth
    assert found == sorted(found, key=lambda p: p.closest)


def test_the_sun_synchronous_satellites_pass_at_their_local_times():
    # The VIIRS satellites cross the equator at about 13:30 local solar time going north (day) and
    # 01:30 going south (night). Over 40 degrees north the swath is wide, so allow a few hours.
    found = find_passes(rows(), *ANKARA, NOW - timedelta(hours=24), NOW + timedelta(hours=24))
    for p in found:
        if p.instrument != "VIIRS":
            continue
        hour = solar_hour(p.closest, ANKARA[1])
        day = 10.5 <= hour <= 16.5
        night = hour >= 22.0 or hour <= 4.5
        assert day or night, (p.satellite, p.closest, hour)


def test_terra_passes_in_the_morning_and_aqua_in_the_afternoon():
    found = find_passes(rows(), *ANKARA, NOW - timedelta(hours=24), NOW + timedelta(hours=24))
    terra = [solar_hour(p.closest, ANKARA[1]) for p in found if p.code == "Terra"]
    aqua = [solar_hour(p.closest, ANKARA[1]) for p in found if p.code == "Aqua"]
    assert any(8.0 <= h <= 12.5 for h in terra)  # 10:30 descending
    assert any(12.0 <= h <= 16.5 for h in aqua)  # 13:30 ascending


def test_a_pass_seen_in_the_real_data_is_predicted():
    # On 2 November 2026 Terra detected fires at 07:03 UTC near Sivas (Zara, 39.9 N 37.8 E).
    found = find_passes(rows(), 39.87, 37.79, NOW - timedelta(hours=24), NOW + timedelta(hours=24))
    terra = [p for p in found if p.code == "Terra"]
    assert any(p.start - timedelta(minutes=1) <= datetime(2026, 11, 2, 7, 3, tzinfo=UTC) <= p.end + timedelta(minutes=1) for p in terra)


def test_every_corner_of_turkey_is_scanned_several_times_a_day():
    for lat, lon in [(36.0, 44.5), (42.0, 26.5), (36.2, 29.5), (41.9, 43.5)]:
        found = find_passes(rows(), lat, lon, NOW, NOW + timedelta(hours=24))
        assert len(found) >= 8, (lat, lon, len(found))


def test_the_summary_names_the_last_and_next_scan_and_the_longest_gap():
    found = find_passes(rows(), *ANKARA, NOW - timedelta(hours=24), NOW + timedelta(hours=24))
    summary = coverage_summary(found, NOW)
    assert summary["last"].closest <= NOW < summary["next"].closest
    assert 30 <= summary["longest_gap_minutes"] <= 24 * 60
    assert coverage_summary([], NOW) == {"last": None, "next": None, "longest_gap_minutes": None}


def test_the_age_of_the_elements_is_counted_from_their_own_epoch():
    age = elements_age(rows(), NOW)
    assert timedelta(hours=5) < age < timedelta(days=1)
    assert elements_age({}, NOW) is None


# ---- fetching the elements --------------------------------------------------


def celestrak_answer(request):
    norad = int(request.url.params["CATNR"])
    name, line1, line2 = TLE[norad]
    return httpx.Response(200, text=f"{name}\n{line1}\n{line2}\n")


@respx.mock
def test_elements_are_fetched_parsed_and_stored(session_factory):
    route = respx.route(host="celestrak.org").mock(side_effect=celestrak_answer)
    with httpx.Client() as client, session_factory() as session:
        assert refresh_elements(session, client, NOW) is True
        assert len(passes.load_elements(session)) == 5
        assert route.call_count == 5
        # Fresh enough: the next call does not ask again.
        assert refresh_elements(session, client, NOW + timedelta(hours=1)) is False
        assert route.call_count == 5
        # Old enough: it asks again.
        assert refresh_elements(session, client, NOW + timedelta(hours=13)) is True
        assert route.call_count == 10


@respx.mock
def test_a_refused_request_is_tried_again_and_a_missing_satellite_keeps_its_old_elements(session_factory):
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if int(request.url.params["CATNR"]) == 37849:
            return httpx.Response(500, text="error")
        return celestrak_answer(request)

    respx.route(host="celestrak.org").mock(side_effect=flaky)
    with httpx.Client() as client:
        got = fetch_elements(client)
    assert 37849 not in got and len(got) == 4
    assert calls["n"] == 4 + 3  # three tries for the one that failed


@respx.mock
def test_an_unreachable_service_gives_nothing_and_no_error():
    respx.route(host="celestrak.org").mock(side_effect=httpx.ConnectError("down"))
    with httpx.Client() as client:
        assert fetch_elements(client) == {}


def test_elements_are_parsed_only_from_three_proper_lines():
    name, line1, line2 = TLE[27424]
    assert parse_elements(f"{name}\n{line1}\n{line2}\n") == (name, line1, line2)
    assert parse_elements("Invalid query") is None
    assert parse_elements("") is None


# ---- the API ----------------------------------------------------------------


def make_client(session_factory, stored=True):
    if stored:
        with session_factory() as s:
            for row in rows().values():
                s.merge(row)
            s.commit()
    app = create_app(session_factory, now=lambda: NOW, http_client=httpx.Client(), settings=Settings(_env_file=None))
    return TestClient(app)


def test_the_passes_endpoint_says_when_the_place_was_last_and_will_next_be_scanned(session_factory):
    body = make_client(session_factory).get("/passes", params={"lat": ANKARA[0], "lon": ANKARA[1]}).json()
    assert body["available"] is True and body["reliable"] is True
    assert body["scans_24h"] >= 8
    assert body["last"]["minutes"] > 0 and body["next"]["minutes"] > 0
    assert body["last"]["satellite"] in {s.name for s in SATELLITES}
    assert 0 < body["longest_gap_hours"] <= 24
    assert "three hours" in body["note"]


def test_old_orbit_data_is_marked_unreliable(session_factory):
    with session_factory() as s:
        for row in rows().values():
            row.line1 = row.line1[:18] + "26250.13961205" + row.line1[32:]  # an epoch about 50 days old
            s.merge(row)
        s.commit()
    app = create_app(session_factory, now=lambda: NOW, http_client=httpx.Client(), settings=Settings(_env_file=None))
    body = TestClient(app).get("/passes", params={"lat": 39.9, "lon": 32.8}).json()
    assert body["available"] is True and body["reliable"] is False and body["orbit_data_age_days"] > 14


@respx.mock
def test_the_endpoint_fetches_the_elements_the_first_time(session_factory):
    route = respx.route(host="celestrak.org").mock(side_effect=celestrak_answer)
    body = make_client(session_factory, stored=False).get("/passes", params={"lat": 39.9, "lon": 32.8}).json()
    assert route.call_count == 5 and body["available"] is True


@respx.mock
def test_without_any_orbit_data_it_says_so_instead_of_guessing(session_factory):
    respx.route(host="celestrak.org").mock(side_effect=httpx.ConnectError("down"))
    body = make_client(session_factory, stored=False).get("/passes", params={"lat": 39.9, "lon": 32.8}).json()
    assert body["available"] is False and body["last"] is None and body["scans_24h"] == 0


def test_places_outside_turkey_are_refused(session_factory):
    assert make_client(session_factory).get("/passes", params={"lat": 10, "lon": 10}).status_code == 422
