from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.weather import (
    WeatherUnavailable,
    calls_today,
    get_weather,
    get_weather_many,
    parse_forecast,
    round_coordinate,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
TTL = 45
ROUTE = {"host": "api.open-meteo.com", "path": "/v1/forecast"}


def make_payload(precip: dict[str, float] | None = None, **current) -> dict:
    """An Open-Meteo-shaped response: hourly values from 28 June 00:00 to 1 July 23:00 (96 hours)."""
    start = datetime(2026, 6, 28, 0, 0)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(96)]
    amounts = [(precip or {}).get(t, 0.0) for t in times]
    reading = {
        "time": "2026-07-01T12:00",
        "interval": 900,
        "temperature_2m": 33.5,
        "relative_humidity_2m": 22,
        "wind_speed_10m": 28.4,
        "wind_direction_10m": 240,
    }
    reading.update(current)
    return {"current": reading, "hourly": {"time": times, "precipitation": amounts}}


@pytest.fixture
def session():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


def fetch(session, client, lat=39.9, lon=32.9, now=NOW, quota=100):
    return get_weather(session, client, lat, lon, now, TTL, quota)


# ---- parsing ----------------------------------------------------------------


def test_parse_sums_only_the_72_hours_up_to_now():
    payload = make_payload(
        {
            "2026-07-01T11:00": 5.0,  # inside the window
            "2026-06-28T13:00": 2.0,  # inside, near the start
            "2026-06-28T12:00": 3.0,  # exactly 72 h ago: outside
            "2026-07-01T18:00": 7.0,  # in the future: outside
        }
    )
    data = parse_forecast(payload)
    assert data["precip_recent"] == 7.0
    assert data["observed_at"] == NOW
    assert data["temperature"] == 33.5
    assert data["humidity"] == 22
    assert data["wind_speed"] == 28.4
    assert data["wind_direction"] == 240


def test_parse_skips_missing_precipitation_values():
    payload = make_payload({"2026-07-01T11:00": 4.0})
    payload["hourly"]["precipitation"][10] = None
    assert parse_forecast(payload)["precip_recent"] == 4.0


@pytest.mark.parametrize("field", ["temperature_2m", "relative_humidity_2m", "wind_speed_10m"])
def test_parse_rejects_a_missing_current_value(field):
    with pytest.raises(WeatherUnavailable):
        parse_forecast(make_payload(**{field: None}))


@pytest.mark.parametrize(
    "payload",
    [{}, {"current": {}}, {"current": {"time": "x"}, "hourly": {}}, "not a dict"],
)
def test_parse_rejects_malformed_payloads(payload):
    with pytest.raises(WeatherUnavailable):
        parse_forecast(payload)


def test_parse_rejects_a_response_with_no_precipitation_in_the_window():
    payload = make_payload()
    payload["hourly"]["precipitation"] = [None] * 96
    with pytest.raises(WeatherUnavailable):
        parse_forecast(payload)


def test_coordinates_round_to_a_tenth_of_a_degree():
    assert round_coordinate(39.93) == 39.9
    assert round_coordinate(39.96) == 40.0


# ---- several points at once -------------------------------------------------


def many(session, client, points, now=NOW, quota=100):
    return get_weather_many(session, client, points, now, TTL, quota)


def batch_payload(n, **current):
    return [make_payload(**current) for _ in range(n)]


@respx.mock
def test_several_points_are_fetched_in_one_request_and_counted_per_point(session, client):
    route = respx.route(**ROUTE).respond(200, json=batch_payload(3))
    result = many(session, client, [(39.9, 32.9), (41.0, 29.0), (36.4, 36.2)])

    assert route.call_count == 1
    assert {r.status for r in result.values()} == {"fresh"}
    assert set(result) == {(39.9, 32.9), (41.0, 29.0), (36.4, 36.2)}
    assert calls_today(session, NOW) == 3
    assert "latitude=39.9%2C41.0%2C36.4" in str(route.calls[0].request.url)


@respx.mock
def test_points_that_round_to_the_same_cell_are_fetched_once(session, client):
    route = respx.route(**ROUTE).respond(200, json=batch_payload(1))
    result = many(session, client, [(39.93, 32.88), (39.86, 32.94)])
    assert list(result) == [(39.9, 32.9)]
    assert calls_today(session, NOW) == 1
    assert route.call_count == 1


@respx.mock
def test_cached_points_are_not_fetched_again(session, client):
    route = respx.route(**ROUTE).respond(200, json=batch_payload(2))
    many(session, client, [(39.9, 32.9), (41.0, 29.0)])
    assert route.call_count == 1

    route.respond(200, json=batch_payload(1))
    result = many(session, client, [(39.9, 32.9), (36.4, 36.2)], now=NOW + timedelta(minutes=10))

    assert result[(39.9, 32.9)].status == "cached"
    assert result[(36.4, 36.2)].status == "fresh"
    assert route.call_count == 2  # one more request, for the new point only
    assert calls_today(session, NOW) == 3  # 2 + only the new point


@respx.mock
def test_a_quota_that_runs_out_serves_the_first_points_and_the_rest_from_cache(session, client):
    route = respx.route(**ROUTE).respond(200, json=batch_payload(1))
    many(session, client, [(36.4, 36.2)])  # cached earlier, and it used one call
    assert route.call_count == 1

    later = NOW + timedelta(hours=2)
    result = many(session, client, [(39.9, 32.9), (41.0, 29.0), (36.4, 36.2)], now=later, quota=2)

    # One call was already used, so only one more is allowed: the first missing point gets it.
    # The old cached point is also due, but there is no quota left for it, so it is served stale.
    assert result[(39.9, 32.9)].status == "fresh"
    assert result[(41.0, 29.0)] is None
    assert result[(36.4, 36.2)].status == "stale"
    assert route.call_count == 2


@respx.mock
def test_an_upstream_failure_falls_back_to_stale_or_none(session, client):
    respx.route(**ROUTE).respond(200, json=batch_payload(1))
    many(session, client, [(36.4, 36.2)])

    respx.route(**ROUTE).respond(500, text="boom")
    result = many(session, client, [(36.4, 36.2), (39.9, 32.9)], now=NOW + timedelta(hours=2))
    assert result[(36.4, 36.2)].status == "stale"
    assert result[(39.9, 32.9)] is None


@respx.mock
def test_one_unusable_point_does_not_spoil_the_others(session, client):
    payload = batch_payload(2)
    payload[1]["current"]["temperature_2m"] = None
    respx.route(**ROUTE).respond(200, json=payload)
    result = many(session, client, [(39.9, 32.9), (41.0, 29.0)])
    assert result[(39.9, 32.9)].status == "fresh"
    assert result[(41.0, 29.0)] is None


@respx.mock
def test_a_response_with_the_wrong_number_of_locations_is_rejected(session, client):
    respx.route(**ROUTE).respond(200, json=batch_payload(1))
    result = many(session, client, [(39.9, 32.9), (41.0, 29.0)])
    assert all(r is None for r in result.values())


@respx.mock
def test_large_requests_are_split_into_batches(session, client):
    route = respx.route(**ROUTE).mock(
        side_effect=lambda request: httpx.Response(200, json=batch_payload(len(request.url.params["latitude"].split(","))))
    )
    points = [(36.0 + 0.1 * i, 30.0) for i in range(120)]
    result = many(session, client, points, quota=1000)
    assert route.call_count == 3  # 50 + 50 + 20
    assert len(result) == 120 and all(r.status == "fresh" for r in result.values())


# ---- cache and quota --------------------------------------------------------


@respx.mock
def test_first_request_fetches_and_stores(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    result = fetch(session, client)

    assert result.status == "fresh"
    assert result.snapshot.latitude == 39.9
    assert result.snapshot.temperature == 33.5
    assert route.call_count == 1
    assert calls_today(session, NOW) == 1


@respx.mock
def test_request_within_the_ttl_is_served_from_cache(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client)
    again = fetch(session, client, now=NOW + timedelta(minutes=TTL - 1))

    assert again.status == "cached"
    assert route.call_count == 1
    assert calls_today(session, NOW) == 1


@respx.mock
def test_nearby_coordinates_share_one_cached_fetch(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client, lat=39.93, lon=32.88)
    again = fetch(session, client, lat=39.86, lon=32.94)  # both round to 39.9 / 32.9
    assert again.status == "cached"
    assert route.call_count == 1


@respx.mock
def test_a_different_area_is_fetched_separately(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client, lat=39.9, lon=32.9)
    fetch(session, client, lat=41.0, lon=29.0)
    assert route.call_count == 2


@respx.mock
def test_request_after_the_ttl_refreshes(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client)
    route.respond(200, json=make_payload(temperature_2m=20.0))
    later = fetch(session, client, now=NOW + timedelta(minutes=TTL + 1))

    assert later.status == "fresh"
    assert later.snapshot.temperature == 20.0
    assert route.call_count == 2


@respx.mock
def test_quota_reached_serves_stale_cache_without_calling_out(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client, quota=1)
    later = fetch(session, client, now=NOW + timedelta(hours=2), quota=1)

    assert later.status == "stale"
    assert route.call_count == 1


@respx.mock
def test_quota_reached_with_nothing_cached_is_unavailable(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client, lat=39.9, lon=32.9, quota=1)
    with pytest.raises(WeatherUnavailable, match="quota"):
        fetch(session, client, lat=41.0, lon=29.0, quota=1)
    assert route.call_count == 1


@respx.mock
def test_quota_resets_on_a_new_utc_day(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client, quota=1)
    tomorrow = NOW + timedelta(days=1)
    result = fetch(session, client, now=tomorrow, quota=1)

    assert result.status == "fresh"
    assert route.call_count == 2
    assert calls_today(session, tomorrow) == 1


@respx.mock
def test_upstream_error_falls_back_to_stale_cache(session, client):
    route = respx.route(**ROUTE).respond(200, json=make_payload())
    fetch(session, client)
    route.respond(500, text="boom")
    result = fetch(session, client, now=NOW + timedelta(hours=2))

    assert result.status == "stale"
    assert calls_today(session, NOW) == 2  # the failed attempt still counts against the quota


@respx.mock
def test_upstream_error_with_nothing_cached_is_unavailable(session, client):
    respx.route(**ROUTE).respond(429, text="too many")
    with pytest.raises(WeatherUnavailable, match="HTTP 429"):
        fetch(session, client)


@respx.mock
def test_network_error_is_unavailable(session, client):
    respx.route(**ROUTE).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(WeatherUnavailable, match="request failed"):
        fetch(session, client)


@respx.mock
def test_invalid_json_is_unavailable(session, client):
    respx.route(**ROUTE).respond(200, text="<html>not json</html>")
    with pytest.raises(WeatherUnavailable, match="invalid JSON"):
        fetch(session, client)
