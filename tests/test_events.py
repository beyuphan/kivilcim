from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from kivilcim.events import MIN_DETECTIONS, MIN_TOTAL_FRP, SATELLITES, find_events

UTC = timezone.utc


def find(rows, **kwargs):
    """find_events with the small thresholds these single-satellite fixtures were built for."""
    kwargs.setdefault("min_detections", 15)
    kwargs.setdefault("min_total_frp", 500.0)
    return find_events(rows, **kwargs)
DAY0 = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

# Near Ankara (district centers are in kivilcim/static/ilceler.json)
LAT, LON = 39.9, 32.85


@dataclass
class D:
    latitude: float
    longitude: float
    acquired_at: datetime
    frp: float | None
    confidence_level: str = "nominal"


def day(n: int) -> datetime:
    return DAY0 + timedelta(days=n)


def fire(start_day=0, days=3, lat=LAT, lon=LON, per_day=8, strong=150.0, weak=10.0):
    """A fire: one strong detection per day and several weak ones around it."""
    out = []
    for n in range(start_day, start_day + days):
        out.append(D(lat, lon, day(n), strong))
        for i in range(per_day):
            out.append(D(lat + 0.03 * (i % 4), lon + 0.03 * (i // 4), day(n), weak))
    return out


def test_a_strong_fire_with_weak_neighbors_is_one_event():
    events = find(fire(days=3))
    assert len(events) == 1
    e = events[0]
    assert (e.start, e.end, e.days) == (day(0).date(), day(2).date(), 3)
    assert e.detections == 27
    assert e.max_frp == 150.0
    assert e.total_frp == pytest.approx(3 * (150 + 8 * 10))
    assert e.id.startswith("20260701-")
    assert e.province == "Ankara"
    assert e.in_turkey is True
    assert e.min_lat <= e.center_lat <= e.max_lat
    assert e.min_lon <= e.center_lon <= e.max_lon


def test_center_is_weighted_toward_the_strong_detections():
    rows = [D(39.0, 32.0, day(0), 400.0)] + [D(39.15, 32.15, day(0), 1.0) for _ in range(20)]
    (event,) = find(rows, min_total_frp=100)
    assert event.center_lat < 39.01
    assert event.center_lon < 32.01


def test_many_weak_detections_without_a_strong_one_are_not_an_event():
    # Crop residue burning: lots of detections, none strong.
    rows = [D(LAT + 0.02 * (i % 10), LON + 0.02 * (i // 10), day(i % 4), 30.0) for i in range(120)]
    assert find(rows) == []


def test_low_confidence_detections_are_ignored():
    rows = [D(d.latitude, d.longitude, d.acquired_at, d.frp, "low") for d in fire()]
    assert find(rows) == []


def test_two_distant_fires_are_two_events():
    rows = fire(lat=39.9, lon=32.85) + fire(lat=37.0, lon=35.3, start_day=1)
    events = find(rows)
    assert len(events) == 2
    assert {e.province for e in events} == {"Ankara", "Adana"}


def test_weak_detections_far_in_time_do_not_bridge_into_the_event():
    # A weak, fixed-looking source next to the fire detects every day for two months.
    weak_stream = [D(LAT + 0.1, LON, day(n), 8.0) for n in range(60)]
    (event,) = find(fire(start_day=30, days=2) + weak_stream, min_history_days=1000)
    assert event.days <= 7  # fire days plus the few days around it that are attached
    assert event.start >= day(28).date()
    assert event.end <= day(35).date()


def test_events_longer_than_max_days_are_dropped():
    assert find(fire(days=20), max_days=14) == []
    assert len(find(fire(days=20), max_days=30)) == 1


def test_fixed_sources_are_removed_when_there_is_enough_history():
    always_on = [D(LAT, LON, day(n), 120.0) for n in range(70)]
    # With 70 days of history the cell that detects daily is a fixed source.
    assert find(always_on, min_detections=1, min_total_frp=1, max_days=100) == []


def test_fixed_source_rule_is_skipped_for_short_history():
    short = [D(LAT, LON, day(n), 120.0) for n in range(5)]
    events = find(short, min_detections=1, min_total_frp=1)
    assert len(events) == 1


def test_a_fire_next_to_a_fixed_source_is_still_found():
    always_on = [D(LAT + 1.0, LON, day(n), 120.0) for n in range(70)]
    events = find(always_on + fire(start_day=40, days=3), max_days=14)
    assert len(events) == 1
    assert events[0].start == day(40).date()


@pytest.mark.parametrize(
    "kwargs",
    [{"min_detections": 100}, {"min_total_frp": 10_000}, {"min_max_frp": 500}],
)
def test_thresholds_filter_events(kwargs):
    assert find(fire(days=3), **kwargs) == []


def test_a_point_far_from_any_turkish_district_is_flagged_as_outside():
    (event,) = find(fire(lat=36.3, lon=43.6))  # northern Iraq
    assert event.in_turkey is False


def test_events_are_sorted_most_recent_first():
    rows = fire(start_day=0) + fire(start_day=20, lat=37.0, lon=35.3)
    events = find(rows)
    assert [e.start for e in events] == sorted((e.start for e in events), reverse=True)


def test_missing_frp_values_do_not_crash():
    rows = fire(days=3) + [D(LAT, LON, day(1), None) for _ in range(5)]
    assert len(find(rows)) == 1


def test_no_detections_gives_no_events():
    assert find([]) == []


def test_default_thresholds_scale_with_the_number_of_satellites():
    assert SATELLITES == 3
    assert MIN_DETECTIONS == 15 * SATELLITES
    assert MIN_TOTAL_FRP == 500.0 * SATELLITES
    # A fire one satellite sees well enough (27 detections, 690 MW) is below the three-satellite bar.
    assert find_events(fire(days=3)) == []


def test_extent_is_the_diagonal_of_the_box_around_the_detections():
    rows = [D(39.0, 32.0, day(0), 400.0), D(39.2, 32.2, day(0), 20.0)] + [D(39.1, 32.1, day(0), 20.0)] * 20
    (event,) = find(rows)
    # 0.2 degrees of latitude is about 22 km, and 0.2 degrees of longitude about 17 km at 39 N.
    assert event.extent_km == pytest.approx(28, abs=2)
