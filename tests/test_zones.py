from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from kivilcim.fixed import cell_of
from kivilcim.zones import MAX_RADIUS_KM, MIN_RADIUS_KM, find_zones

UTC = timezone.utc
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


@dataclass
class D:
    latitude: float
    longitude: float
    frp: float | None = 10.0
    confidence_level: str = "nominal"
    acquired_at: datetime = NOW


def test_neighboring_cells_form_one_zone():
    rows = [D(39.90, 32.80), D(39.95, 32.90), D(40.00, 32.90)]  # cells touch each other
    (zone,) = find_zones(rows)
    assert zone.detections == 3
    assert zone.total_frp == 30
    assert zone.province == "Ankara"


def test_distant_places_are_separate_zones_strongest_first():
    rows = [D(39.9, 32.8, 10.0)] * 2 + [D(37.0, 35.3, 80.0)]
    zones = find_zones(rows)
    assert [z.province for z in zones] == ["Adana", "Ankara"]
    assert [z.total_frp for z in zones] == [80.0, 20.0]


def test_a_gap_of_one_empty_cell_separates_zones():
    rows = [D(39.9, 32.8), D(39.9, 33.0)]  # two cells apart
    assert len(find_zones(rows)) == 2


def test_low_confidence_detections_are_left_out():
    assert find_zones([D(39.9, 32.8, confidence_level="low")]) == []


def test_fixed_cells_are_left_out():
    rows = [D(36.70, 36.20), D(39.90, 32.80)]
    zones = find_zones(rows, fixed_cells={cell_of(36.70, 36.20)})
    assert [z.province for z in zones] == ["Ankara"]


def test_places_outside_turkey_are_left_out():
    assert find_zones([D(36.3, 43.6)]) == []  # northern Iraq


def test_the_center_is_weighted_by_heat_power():
    rows = [D(39.90, 32.80, 90.0), D(39.95, 32.85, 10.0)]
    (zone,) = find_zones(rows)
    assert zone.center_lat == pytest.approx(39.905, abs=0.001)
    assert zone.center_lon == pytest.approx(32.805, abs=0.001)


def test_zero_heat_power_falls_back_to_the_plain_mean():
    rows = [D(39.90, 32.80, 0.0), D(39.92, 32.82, None)]
    (zone,) = find_zones(rows)
    assert zone.center_lat == pytest.approx(39.91)
    assert zone.max_frp == 0


def test_latest_observation_and_strength():
    rows = [
        D(39.9, 32.8, 5.0, acquired_at=NOW - timedelta(hours=5)),
        D(39.9, 32.82, 60.0, acquired_at=NOW - timedelta(hours=1)),
    ]
    (zone,) = find_zones(rows)
    assert zone.latest_observation == NOW - timedelta(hours=1)
    assert zone.max_frp == 60.0
    assert zone.id == "{}_{}".format(*cell_of(39.9, 32.82))


def test_the_drawing_radius_has_a_floor_and_a_ceiling():
    (small,) = find_zones([D(39.9, 32.8)])
    assert small.radius_km == MIN_RADIUS_KM
    line = [D(39.9 + 0.1 * i, 32.8) for i in range(30)]  # a 300 km chain of touching cells
    assert find_zones(line)[0].radius_km == MAX_RADIUS_KM


def test_the_number_of_zones_is_capped_and_the_strongest_kept():
    rows = [D(37.0 + 0.5 * (i % 10), 30.0 + 0.5 * (i // 10), float(i + 1)) for i in range(40)]
    zones = find_zones(rows, max_zones=5)
    assert len(zones) == 5
    assert [z.total_frp for z in zones] == [40.0, 39.0, 38.0, 37.0, 36.0]


def test_no_detections_gives_no_zones():
    assert find_zones([]) == []
