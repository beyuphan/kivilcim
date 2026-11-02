import random

import pytest

from kivilcim.geo import bounding_box, haversine_km


def test_zero_distance():
    assert haversine_km(39.9, 32.8, 39.9, 32.8) == 0


def test_istanbul_to_ankara():
    distance = haversine_km(41.0082, 28.9784, 39.9334, 32.8597)
    assert distance == pytest.approx(349, abs=3)


def test_distance_is_symmetric():
    assert haversine_km(10, 20, 30, 40) == pytest.approx(haversine_km(30, 40, 10, 20))


def test_one_degree_of_latitude_is_about_111_km():
    assert haversine_km(0, 0, 1, 0) == pytest.approx(111.2, abs=0.2)


@pytest.mark.parametrize(
    "lat, lon, radius",
    [(39.0, 35.0, 50), (39.0, 35.0, 500), (89.5, 10.0, 100), (0.0, 179.9, 100), (-45.0, -179.5, 300)],
)
def test_bounding_box_contains_every_point_inside_the_circle(lat, lon, radius):
    min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius)
    rng = random.Random(1)
    checked = 0
    for _ in range(5000):
        plat = max(-90, min(90, lat + rng.uniform(-10, 10)))
        plon = lon + rng.uniform(-15, 15)
        plon = (plon + 180) % 360 - 180
        if haversine_km(lat, lon, plat, plon) <= radius:
            checked += 1
            assert min_lat <= plat <= max_lat
            assert min_lon <= plon <= max_lon
    assert checked > 0


def test_bounding_box_is_tight_in_the_middle_latitudes():
    min_lat, max_lat, min_lon, max_lon = bounding_box(39.0, 35.0, 50)
    assert max_lat - min_lat < 1.0
    assert max_lon - min_lon < 1.2
