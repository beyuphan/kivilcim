import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx

from kivilcim import validate
from kivilcim.fixed import cell_of
from kivilcim.validate import (
    ControlSample,
    FireSample,
    HistoryUnavailable,
    OpenMeteoHistory,
    Reading,
    analyze,
    build_report,
    control_samples,
    describe_auc,
    describe_correlation,
    fire_samples,
    stratified_by_month,
    thin,
)

UTC = timezone.utc
DAY0 = date(2026, 7, 10)
LAT, LON = 39.9, 32.85  # near Ankara, so the zone counts as inside Turkey


@dataclass
class D:
    latitude: float
    longitude: float
    acquired_at: datetime
    frp: float | None = 10.0
    confidence_level: str = "nominal"
    instrument: str = "VIIRS"


def at(day_offset: int, hour: int = 10) -> datetime:
    return datetime.combine(DAY0 + timedelta(days=day_offset), datetime.min.time(), UTC) + timedelta(hours=hour)


def cluster(day_offset, n=6, frp=10.0, lat=LAT, lon=LON):
    return [D(lat + 0.01 * i, lon, at(day_offset), frp) for i in range(n)]


# ---- fire samples -----------------------------------------------------------


def test_a_zone_with_enough_detections_becomes_a_sample_with_its_next_day():
    rows = cluster(0, 6, 10.0) + cluster(1, 3, 4.0)
    (sample,) = fire_samples(rows, set(), DAY0, DAY0)
    assert sample.day == DAY0
    assert sample.detections == 6
    assert sample.today_frp == 60
    assert sample.next_frp == 12 and sample.next_count == 3
    assert sample.log_change < 0  # weaker the next day


def test_a_fire_that_is_gone_the_next_day_has_no_next_detections():
    (sample,) = fire_samples(cluster(0), set(), DAY0, DAY0)
    assert sample.next_count == 0 and sample.next_frp == 0
    assert sample.log_change == pytest.approx(-4.1, abs=0.1)  # log(1/61)


def test_small_zones_low_confidence_and_fixed_cells_are_left_out():
    small = cluster(0, 4)
    low = [D(d.latitude, d.longitude, d.acquired_at, d.frp, "low") for d in cluster(0, 8, lat=37.0, lon=35.3)]
    fixed = cluster(0, 8, lat=40.5, lon=33.0)
    rows = small + low + fixed
    cells = {cell_of(d.latitude, d.longitude) for d in fixed}
    assert fire_samples(rows, cells, DAY0, DAY0) == []


def test_the_next_day_is_measured_in_the_area_around_the_zone():
    near = D(LAT + 0.1, LON, at(1), 30.0)  # 0.1 degrees away: inside the padding
    far = D(LAT + 1.0, LON, at(1), 99.0)  # a different place
    (sample,) = fire_samples(cluster(0) + [near, far], set(), DAY0, DAY0)
    assert sample.next_count == 1 and sample.next_frp == 30


def test_days_outside_the_range_give_no_samples():
    rows = cluster(0) + cluster(5)
    assert [s.day for s in fire_samples(rows, set(), DAY0 + timedelta(days=4), DAY0 + timedelta(days=6))] == [DAY0 + timedelta(days=5)]
    assert fire_samples(rows, set(), DAY0 + timedelta(days=1), DAY0 + timedelta(days=3)) == []


def make_sample(day_offset, lat=LAT, lon=LON, today=50.0, nxt=40.0, count=3):
    return FireSample(DAY0 + timedelta(days=day_offset), lat, lon, 8, today, nxt, count)


def test_thinning_keeps_one_sample_per_place_per_four_days_the_strongest():
    samples = [make_sample(0, today=20), make_sample(1, today=90), make_sample(2, today=10), make_sample(6, today=5)]
    kept = thin(samples)
    # Days 0-3 are one stretch; the earliest one in it is kept, then day 6 starts a new stretch.
    assert [s.day for s in kept] == [DAY0, DAY0 + timedelta(days=6)]


def test_thinning_keeps_different_places():
    samples = [make_sample(0), make_sample(0, lat=37.0, lon=35.3)]
    assert len(thin(samples)) == 2


def test_stratifying_by_month_caps_the_total_and_spreads_it():
    samples = [make_sample(i) for i in range(0, 40)]  # 10 July to 18 August: two months
    chosen = stratified_by_month(samples, 10, seed=1)
    assert len(chosen) == 10
    assert {s.day.month for s in chosen} == {7, 8}
    assert chosen == stratified_by_month(samples, 10, seed=1)  # reproducible
    assert chosen != stratified_by_month(samples, 10, seed=2)


def test_stratifying_returns_everything_when_under_the_limit():
    samples = [make_sample(i) for i in range(5)]
    assert stratified_by_month(samples, 10, seed=1) == samples


# ---- controls ---------------------------------------------------------------


def test_controls_are_places_with_no_detection_nearby_that_day():
    places = [(LAT, LON), (37.0, 35.3), (41.0, 29.0)]
    rows = cluster(0)  # fire at the first place on day 0
    controls = control_samples(rows, [DAY0] * 12, places, seed=1)
    assert len(controls) == 12
    assert all((c.lat, c.lon) != (LAT, LON) for c in controls)
    assert controls == control_samples(rows, [DAY0] * 12, places, seed=1)


def test_controls_may_come_from_any_instrument_or_confidence():
    rows = [D(LAT, LON, at(0), 5.0, "low", "MODIS")]
    controls = control_samples(rows, [DAY0] * 5, [(LAT, LON), (37.0, 35.3)], seed=2)
    assert all((c.lat, c.lon) == (37.0, 35.3) for c in controls)


def test_no_control_is_made_when_every_place_has_a_detection_nearby():
    assert control_samples(cluster(0), [DAY0], [(LAT, LON)], seed=1, tries=10) == []


# ---- the weather history client ---------------------------------------------

ARCHIVE = {"host": "archive-api.open-meteo.com", "path": "/v1/archive"}


def history_item(day: date, temp=33.0, rh=22, wind=28.0, rain_hours=None):
    """Hourly data for the 4 days ending on `day`."""
    start = datetime.combine(day - timedelta(days=3), datetime.min.time())
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(96)]
    rain = [0.0] * 96
    for hours_before_noon, amount in (rain_hours or {}).items():
        rain[72 + 12 - hours_before_noon] = amount
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [temp] * 96,
            "relative_humidity_2m": [rh] * 96,
            "wind_speed_10m": [wind] * 96,
            "precipitation": rain,
        }
    }


def make_history(tmp_path=None, **kwargs):
    sleeps = []
    client = httpx.Client()
    history = OpenMeteoHistory(
        client, cache_path=(tmp_path / "c.json") if tmp_path else None, sleep=sleeps.append, **kwargs
    )
    return history, sleeps


@respx.mock
def test_a_reading_is_the_conditions_at_noon_and_the_rain_before_it():
    route = respx.route(**ARCHIVE).respond(200, json=history_item(DAY0, rain_hours={1: 2.0, 70: 3.0, 73: 9.0}))
    history, _ = make_history()
    result = history.readings({DAY0: [(36.79, 31.44)]})

    reading = result[(DAY0, 36.8, 31.4)]  # coordinates are rounded to 0.1
    assert (reading.temperature, reading.humidity, reading.wind) == (33.0, 22.0, 28.0)
    assert reading.rain72 == 5.0  # 2 mm an hour before noon and 3 mm 70 hours before; 73 hours is outside
    request = route.calls[0].request.url.params
    assert request["start_date"] == "2026-07-07" and request["end_date"] == "2026-07-10"
    assert request["latitude"] == "36.8" and request["longitude"] == "31.4"


@respx.mock
def test_several_places_on_one_day_share_a_request_and_a_list_answer_is_matched_in_order():
    items = [history_item(DAY0, temp=10.0), history_item(DAY0, temp=20.0), history_item(DAY0, temp=30.0)]
    route = respx.route(**ARCHIVE).respond(200, json=items)
    history, sleeps = make_history()
    result = history.readings({DAY0: [(36.0, 30.0), (37.0, 31.0), (38.0, 32.0)]})

    assert route.call_count == 1
    assert [result[(DAY0, lat, lon)].temperature for lat, lon in [(36.0, 30.0), (37.0, 31.0), (38.0, 32.0)]] == [10.0, 20.0, 30.0]
    assert sleeps == [8.0]  # paused once after the request, to stay under the per-minute limit


@respx.mock
def test_large_batches_are_split():
    route = respx.route(**ARCHIVE).mock(
        side_effect=lambda request: httpx.Response(
            200, json=[history_item(DAY0)] * len(request.url.params["latitude"].split(","))
        )
    )
    history, _ = make_history(batch_size=50)
    places = [(36.0 + 0.1 * i, 30.0) for i in range(120)]
    result = history.readings({DAY0: places})
    assert route.call_count == 3 and len(result) == 120


@respx.mock
def test_cached_readings_are_not_requested_again_and_survive_a_restart(tmp_path):
    route = respx.route(**ARCHIVE).respond(200, json=history_item(DAY0))
    first, _ = make_history(tmp_path)
    first.readings({DAY0: [(36.0, 30.0)]})
    assert route.call_count == 1

    second, _ = make_history(tmp_path)  # a new object reads the file
    result = second.readings({DAY0: [(36.0, 30.0)]})
    assert route.call_count == 1
    assert result[(DAY0, 36.0, 30.0)].temperature == 33.0
    assert json.loads((tmp_path / "c.json").read_text())  # the file has content


@respx.mock
def test_a_missing_value_is_remembered_as_missing():
    item = history_item(DAY0)
    item["hourly"]["temperature_2m"][84] = None  # the noon value
    route = respx.route(**ARCHIVE).respond(200, json=item)
    history, _ = make_history()
    assert history.readings({DAY0: [(36.0, 30.0)]})[(DAY0, 36.0, 30.0)] is None
    history.readings({DAY0: [(36.0, 30.0)]})
    assert route.call_count == 1  # not asked again


@respx.mock
def test_an_overloaded_service_is_retried_with_growing_pauses():
    route = respx.route(**ARCHIVE)
    route.side_effect = [
        httpx.Response(503, json={"error": True, "reason": "The service is overloaded"}),
        httpx.Response(503, text="again"),
        httpx.Response(200, json=history_item(DAY0)),
    ]
    history, sleeps = make_history()
    result = history.readings({DAY0: [(36.0, 30.0)]})
    assert result[(DAY0, 36.0, 30.0)] is not None
    assert route.call_count == 3
    assert sleeps[:2] == [5.0, 10.0]  # waited longer each time before retrying


@respx.mock
def test_it_gives_up_after_the_allowed_attempts():
    route = respx.route(**ARCHIVE).respond(503, text="busy")
    history, _ = make_history(max_attempts=3)
    with pytest.raises(HistoryUnavailable, match="3 attempts"):
        history.readings({DAY0: [(36.0, 30.0)]})
    assert route.call_count == 3


@respx.mock
def test_a_bad_request_is_not_retried():
    route = respx.route(**ARCHIVE).respond(400, json={"error": True, "reason": "bad date"})
    history, _ = make_history()
    with pytest.raises(HistoryUnavailable, match="HTTP 400"):
        history.readings({DAY0: [(36.0, 30.0)]})
    assert route.call_count == 1


@respx.mock
def test_network_errors_are_retried():
    route = respx.route(**ARCHIVE)
    route.side_effect = [httpx.ConnectError("down"), httpx.Response(200, json=history_item(DAY0))]
    history, sleeps = make_history()
    assert history.readings({DAY0: [(36.0, 30.0)]})
    assert sleeps[0] == 5.0


@respx.mock
def test_progress_is_called_for_each_day():
    respx.route(**ARCHIVE).respond(200, json=history_item(DAY0))
    history, _ = make_history()
    seen = []
    history.readings({DAY0: [(36.0, 30.0)], DAY0 + timedelta(days=1): []}, progress=seen.append)
    assert seen == ["2026-07-10", "2026-07-11"]


def test_an_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="provider"):
        OpenMeteoHistory(httpx.Client(), provider="nope")


def test_the_forecast_provider_uses_the_forecast_api():
    assert OpenMeteoHistory.URLS["forecast"].startswith("https://api.open-meteo.com/")
    assert "archive" in OpenMeteoHistory.URLS["archive"]


@respx.mock
def test_the_reading_scores_with_the_live_formula():
    respx.route(**ARCHIVE).respond(200, json=history_item(DAY0, temp=33.5, rh=22, wind=28.4))
    history, _ = make_history()
    reading = history.readings({DAY0: [(36.0, 30.0)]})[(DAY0, 36.0, 30.0)]
    assert reading.score() == 80.9  # the same numbers as the API's hot, dry, windy example


# ---- the analysis -----------------------------------------------------------


def synthetic(n, related: bool, seed=5):
    """Fire cases with an index and an outcome. When `related`, a higher index means the fire grows."""
    rng = random.Random(seed)
    fires, readings = [], {}
    for i in range(n):
        day = DAY0 + timedelta(days=i % 60)
        lat = 36.0 + (i % 40) * 0.1
        hot = rng.random()
        change_today = 50.0
        nxt = change_today * (0.3 + 1.7 * hot) if related else change_today * (0.3 + 1.7 * rng.random())
        fires.append(FireSample(day, lat, 30.0, 8, change_today, nxt, 3 if nxt > 20 else 0))
        readings[(day, round(lat, 1), 30.0)] = Reading(10 + 30 * hot, 70 - 55 * hot, 5 + 45 * hot, 0.0)
    return fires, readings


def controls_for(n, cooler: bool, seed=6):
    rng = random.Random(seed)
    controls, readings = [], {}
    for i in range(n):
        day = DAY0 + timedelta(days=i % 60)
        lat = 40.0 + (i % 40) * 0.05
        controls.append(ControlSample(day, lat, 33.0))
        hot = rng.random() * (0.5 if cooler else 1.0)
        readings[(day, round(lat, 1), 33.0)] = Reading(10 + 30 * hot, 70 - 55 * hot, 5 + 45 * hot, 0.0)
    return controls, readings


def test_a_real_relation_is_found_and_reported():
    fires, readings = synthetic(200, related=True)
    result = analyze(fires, [], readings, rounds=200)
    rho = result["details"]["rho"]
    assert rho["value"] > 0.5 and rho["low"] > 0
    first = result["tests"][0]
    assert first["n"] == 200 and "pozitif" in first["comment"]
    bands = result["details"]["bands"]
    assert [b["name"] for b in bands] == ["en düşük üçte bir", "orta üçte bir", "en yüksek üçte bir"]
    assert bands[0]["median_change"] < bands[2]["median_change"]  # a higher index went with more growth
    assert bands[0]["index_range"][1] <= bands[1]["index_range"][0] + 1e-9


def test_no_relation_is_reported_as_no_relation():
    fires, readings = synthetic(200, related=False)
    result = analyze(fires, [], readings, rounds=200)
    rho = result["details"]["rho"]
    assert rho["low"] <= 0 <= rho["high"]
    assert "anlamlı bir ilişki bulunamadı" in result["tests"][0]["comment"]


def test_separating_fire_places_from_control_places():
    fires, fire_readings = synthetic(120, related=True)
    controls, control_readings = controls_for(120, cooler=True)
    result = analyze(fires, controls, {**fire_readings, **control_readings}, rounds=200)
    auc_result = result["details"]["auc"]
    assert auc_result["value"] > 0.6 and auc_result["low"] > 0.5
    assert auc_result["fire_mean"] > auc_result["control_mean"]
    assert any(t["name"].startswith("B.") for t in result["tests"])


def test_indistinguishable_places_give_an_auc_near_one_half():
    fires, fire_readings = synthetic(150, related=False)
    controls, control_readings = controls_for(150, cooler=False)
    result = analyze(fires, controls, {**fire_readings, **control_readings}, rounds=200)
    a = result["details"]["auc"]
    assert a["low"] <= 0.5 <= a["high"]
    assert "kanıt yok" in [t for t in result["tests"] if t["name"].startswith("B.")][0]["comment"]


def test_too_few_cases_are_reported_as_not_measurable():
    fires, readings = synthetic(10, related=True)
    result = analyze(fires, [], readings, rounds=50)
    assert result["tests"][0]["result"] == "ölçülemedi"
    assert "rho" not in result["details"]


def test_cases_without_weather_are_dropped():
    fires, readings = synthetic(100, related=True)
    for key in list(readings)[:40]:
        readings[key] = None
    result = analyze(fires, [], readings, rounds=50)
    assert result["details"]["fire_cases"] == 60


def test_the_within_month_average_needs_enough_cases_per_month():
    fires, readings = synthetic(200, related=True)
    assert "within_month_rho" in analyze(fires, [], readings, rounds=50)["details"]


def test_descriptions_follow_the_confidence_interval():
    assert describe_correlation(0.4, -0.1, 0.8) == "anlamlı bir ilişki bulunamadı"
    assert describe_correlation(0.05, 0.01, 0.09) == "çok zayıf pozitif ilişki"
    assert describe_correlation(-0.2, -0.3, -0.1) == "zayıf negatif ilişki"
    assert describe_correlation(0.4, 0.3, 0.5) == "orta pozitif ilişki"
    assert describe_correlation(0.7, 0.6, 0.8) == "güçlü pozitif ilişki"
    assert describe_auc(0.55, 0.45, 0.65) == "yangın olan ve olmayan yerleri ayırdığına dair kanıt yok"
    assert describe_auc(0.58, 0.52, 0.64) == "çok zayıf ayrım gücü"
    assert describe_auc(0.65, 0.6, 0.7) == "zayıf ayrım gücü"
    assert describe_auc(0.75, 0.7, 0.8) == "orta ayrım gücü"
    assert describe_auc(0.9, 0.85, 0.95) == "iyi ayrım gücü"
    assert "ters yönde" in describe_auc(0.35, 0.3, 0.4)


# ---- the report -------------------------------------------------------------


def report_for(related):
    fires, readings = synthetic(200, related=related)
    controls, control_readings = controls_for(200, cooler=related)
    analysis = analyze(fires, controls, {**readings, **control_readings}, rounds=200)
    return build_report(analysis, (date(2026, 6, 1), date(2026, 10, 27)), "archive", datetime(2026, 11, 3, tzinfo=UTC))


def test_the_report_has_what_the_page_reads():
    report = report_for(True)
    assert set(report) >= {"summary", "tests", "caveats", "generated", "index_version", "provider", "details"}
    assert report["generated"] == "2026-11-03" and report["index_version"] == "v2"
    assert all(set(t) == {"name", "result", "n", "comment"} for t in report["tests"])
    assert report["caveats"] and any("ayarlanmadı" in c for c in report["caveats"])
    # The period in the caveat is the one that was analysed, not a fixed phrase.
    assert any("2026-06-01 ile 2026-10-27" in c for c in report["caveats"])
    json.dumps(report, ensure_ascii=False)  # must be serializable


def test_the_summary_says_what_was_found_whichever_way_it_went():
    assert "pozitif" in report_for(True)["summary"]
    assert "anlamlı bir ilişki bulunamadı" in report_for(False)["summary"]


def test_the_usage_note_never_claims_more_than_the_numbers_support():
    from kivilcim.validate import usage_note

    supported = {"rho": {"value": 0.4, "low": 0.2, "high": 0.5}, "within_month_rho": {"value": 0.3, "n": 100}}
    assert "ilişki görüldü" in usage_note(supported) and "tahmin olarak sunulmaz" in usage_note(supported)

    # A positive overall relation that vanishes inside each month may only be the season.
    season_only = {"rho": {"value": 0.4, "low": 0.2, "high": 0.5}, "within_month_rho": {"value": 0.02, "n": 100}}
    assert "kanıt bulunamadı" in usage_note(season_only)

    unrelated = {"rho": {"value": -0.1, "low": -0.2, "high": 0.0}, "within_month_rho": {"value": 0.0, "n": 100}}
    assert "kanıt bulunamadı" in usage_note(unrelated) and "tahmini olarak sunulmaz" in usage_note(unrelated)

    assert "yapılamadığı" in usage_note({})


def test_the_report_carries_the_usage_note():
    assert "usage_note" in report_for(True) and report_for(False)["usage_note"]


def test_an_empty_analysis_says_it_could_not_measure():
    report = build_report({"tests": [], "details": {}}, (date(2026, 6, 1), date(2026, 10, 27)), "archive")
    assert "ölçüm yapılamadı" in report["summary"]


# ---- the whole run ----------------------------------------------------------


class StubHistory:
    _provider = "archive"

    def __init__(self, hot):
        self.hot = hot
        self.asked = []

    def readings(self, wanted, progress=None):
        out = {}
        for day, places in wanted.items():
            self.asked.append(day)
            for lat, lon in places:
                out[(day, round(lat, 1), round(lon, 1))] = Reading(32.0, 25.0, 25.0, 0.0) if self.hot else Reading(12.0, 60.0, 5.0, 4.0)
        return out


def test_run_goes_from_detections_to_a_report():
    rows = []
    for offset in range(0, 40, 5):  # a fire every five days, strong enough to count, with a next day
        rows += cluster(offset, 7, 20.0) + cluster(offset + 1, 4, 10.0)
    history = StubHistory(hot=True)
    report = validate.run(rows, history, DAY0, DAY0 + timedelta(days=39), max_fire=50, seed=1)

    assert report["details"]["fire_cases"] >= 5
    assert history.asked  # weather was requested for the sample days
    assert report["provider"] == "archive"


def test_run_ignores_modis_and_low_confidence_detections():
    rows = [D(d.latitude, d.longitude, d.acquired_at, d.frp, "nominal", "MODIS") for d in cluster(0, 9)]
    report = validate.run(rows, StubHistory(True), DAY0, DAY0 + timedelta(days=3), seed=1)
    assert report["details"]["fire_cases"] == 0


def test_main_writes_the_report_and_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'm.db'}")
    from kivilcim.db import init_db, make_engine

    init_db(make_engine(f"sqlite:///{tmp_path / 'm.db'}"))
    monkeypatch.setattr(validate, "run", lambda *a, **k: {"summary": "ok", "tests": [], "caveats": []})
    out = tmp_path / "out.json"
    assert validate.main(["--out", str(out), "--cache", str(tmp_path / "c.json")]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["summary"] == "ok"


def test_main_reports_an_unavailable_service_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'm.db'}")
    from kivilcim.db import init_db, make_engine

    init_db(make_engine(f"sqlite:///{tmp_path / 'm.db'}"))

    def failing(*args, **kwargs):
        raise HistoryUnavailable("the history service did not answer after 6 attempts (HTTP 503)")

    monkeypatch.setattr(validate, "run", failing)
    out = tmp_path / "out.json"
    assert validate.main(["--out", str(out), "--cache", str(tmp_path / "c.json")]) == 1
    assert not out.exists()
    assert "run again later" in capsys.readouterr().err
