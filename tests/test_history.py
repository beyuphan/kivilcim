from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kivilcim.api import create_app
from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.events import NOAA20_FROM, NOAA21_FROM, find_events, satellites_on
from kivilcim.history import BUILT_KEY, live_from, materialize, stored_events
from kivilcim.models import BotState, FireDetection, FireEventRow

UTC = timezone.utc
TODAY = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'h.db'}")
    init_db(engine)
    return make_session_factory(engine)


def fire(session_factory, day, lat=39.9, lon=32.85, days=3, weak=24, strong=150.0, satellite="N"):
    """A strong detection plus `weak` weak ones on each of `days` days from `day`."""
    with session_factory() as s:
        for n in range(days):
            when = datetime(day.year, day.month, day.day, 10, tzinfo=UTC) + timedelta(days=n)
            points = [(lat, lon, strong)] + [(lat + 0.03 * (i % 4 + 1), lon + 0.03 * (i // 4), 20.0) for i in range(weak)]
            for plat, plon, frp in points:
                s.add(
                    FireDetection(
                        source="VIIRS_SNPP_SP", satellite=satellite, instrument="VIIRS", latitude=plat, longitude=plon,
                        acquired_at=when, confidence_raw="n", confidence_level="nominal", brightness=330.0, frp=frp,
                        ingested_at=TODAY,
                    )
                )
        s.commit()


class D:
    def __init__(self, lat, lon, when, frp, level="nominal"):
        self.latitude, self.longitude, self.acquired_at, self.frp, self.confidence_level = lat, lon, when, frp, level


def small_fire(year):
    """A fire that a single satellite sees: 15 detections of 40 MW on 2 days, one of them strong."""
    rows = [D(39.9 + 0.01 * i, 32.85, datetime(year, 8, 1 + i % 2, 10, tzinfo=UTC), 40.0) for i in range(14)]
    rows.append(D(39.9, 32.86, datetime(year, 8, 1, 10, tzinfo=UTC), 150.0))
    return rows


# ---- thresholds follow the satellites that flew -----------------------------


def test_the_number_of_satellites_follows_their_launch_dates():
    assert satellites_on(date(2017, 12, 31)) == 1
    assert satellites_on(NOAA20_FROM) == 2 and satellites_on(date(2022, 12, 31)) == 2
    assert satellites_on(NOAA21_FROM) == 3 and satellites_on(date(2026, 7, 1)) == 3


def test_a_fire_seen_by_one_satellite_counts_in_an_early_year_but_not_in_a_recent_one():
    # 15 detections and 700 MW pass a one-satellite threshold (15 and 500) but not the three-satellite one (45, 1500).
    assert len(find_events(small_fire(2016), scale_by_satellites=True)) == 1
    assert find_events(small_fire(2026), scale_by_satellites=True) == []
    assert find_events(small_fire(2016)) == []  # without scaling the present thresholds apply


# ---- storing the old events --------------------------------------------------


def test_events_older_than_the_live_window_are_stored_once_year_by_year(session_factory):
    fire(session_factory, date(2021, 8, 1), days=4)
    fire(session_factory, date(2022, 7, 10), lat=37.0, lon=35.3)
    fire(session_factory, date(2026, 10, 1))  # recent: stays in the live window
    assert live_from(TODAY.date()) == date(2026, 7, 1)
    stored = materialize(session_factory, TODAY.date())
    with session_factory() as s:
        rows = s.scalars(select(FireEventRow).order_by(FireEventRow.start)).all()
        assert [r.start for r in rows] == [date(2021, 8, 1), date(2022, 7, 10)] and stored == 2
        assert s.get(BotState, BUILT_KEY).value == "2026-07-01"
    assert [(e.district, e.province) for e in stored_events(session_factory())][-1][1] == "Ankara"


def test_materializing_again_changes_nothing_and_a_new_month_adds_only_what_left_the_window(session_factory):
    fire(session_factory, date(2025, 8, 5))
    assert materialize(session_factory, date(2026, 6, 15)) == 1
    assert materialize(session_factory, date(2026, 6, 20)) == 0  # nothing new has left the window
    fire(session_factory, date(2026, 4, 3), lat=37.0, lon=35.3)
    # Still inside the live window (it starts on 1 April), so not stored yet...
    assert materialize(session_factory, date(2026, 8, 20)) == 0
    # ...and once the window starts on 1 May, the April fire has left it and is stored.
    assert materialize(session_factory, date(2026, 9, 10)) == 1
    with session_factory() as s:
        assert sorted(r.start for r in s.scalars(select(FireEventRow))) == [date(2025, 8, 5), date(2026, 4, 3)]


def test_a_fire_across_new_year_is_stored_once(session_factory):
    fire(session_factory, date(2020, 12, 30), days=4)
    materialize(session_factory, TODAY.date())
    with session_factory() as s:
        assert len(s.scalars(select(FireEventRow)).all()) == 1


def test_forcing_a_rebuild_replaces_what_was_stored(session_factory):
    fire(session_factory, date(2021, 8, 1))
    materialize(session_factory, TODAY.date())
    with session_factory() as s:
        s.add(FireEventRow(id="old", start=date(2000, 1, 1), end=date(2000, 1, 1), peak_day=date(2000, 1, 1), days=1, detections=1,
                           total_frp=1, max_frp=1, center_lat=1, center_lon=1, min_lat=1, max_lat=1, min_lon=1, max_lon=1,
                           extent_km=1, district="x", province="y", in_turkey=True))
        s.commit()
    materialize(session_factory, TODAY.date(), force=True)
    with session_factory() as s:
        assert [r.id for r in s.scalars(select(FireEventRow))] != ["old"] and len(s.scalars(select(FireEventRow)).all()) == 1


def test_without_detections_nothing_is_stored(session_factory):
    assert materialize(session_factory, TODAY.date()) == 0


# ---- the API reads both ------------------------------------------------------


def make_client(session_factory):
    app = create_app(session_factory, now=lambda: TODAY, http_client=httpx.Client(), settings=Settings(_env_file=None))
    return TestClient(app)


def test_the_events_endpoint_lists_old_and_recent_fires_together_and_can_sort_by_size(session_factory):
    fire(session_factory, date(2021, 8, 1), days=4, strong=300.0)  # the biggest, years ago
    fire(session_factory, date(2026, 10, 1), lat=37.0, lon=35.3)
    client = make_client(session_factory)
    client.app.state.build_history()
    body = client.get("/events").json()
    assert [e["start"][:4] for e in body["events"]] == ["2026", "2021"]  # the newest first
    largest = client.get("/events", params={"sort": "largest"}).json()["events"]
    assert largest[0]["start"] == "2021-08-01" and largest[0]["total_frp_mw"] > largest[1]["total_frp_mw"]
    assert client.get("/events", params={"sort": "nonsense"}).status_code == 422
    # Opening an old fire works: its detections are still there.
    old_id = largest[0]["id"]
    assert client.get(f"/events/{old_id}").json()["detections"]


def test_the_list_is_up_to_date_after_the_history_is_stored(session_factory):
    fire(session_factory, date(2021, 8, 1))
    client = make_client(session_factory)
    assert client.get("/events").json()["count"] == 0  # not stored yet and outside the live window
    client.app.state.build_history()
    assert client.get("/events").json()["count"] == 1


def test_a_year_of_old_detections_is_not_read_into_the_recent_search(session_factory, monkeypatch):
    import kivilcim.history as history_module

    fire(session_factory, date(2021, 8, 1))
    fire(session_factory, date(2026, 10, 1))
    seen = []
    real = history_module.light_rows
    monkeypatch.setattr(history_module, "light_rows", lambda s, a, b: seen.append((a, b)) or real(s, a, b))
    make_client(session_factory).get("/events")
    assert all(a >= date(2026, 6, 1) for a, _ in seen)  # only the live window and its edge
