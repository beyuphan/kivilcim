"""Small geographic helpers for radius queries."""

import math

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE_LAT = math.pi * EARTH_RADIUS_KM / 180


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """A box (min_lat, max_lat, min_lon, max_lon) that contains the whole circle.

    Used as a cheap pre-filter before exact distances are computed. Near the poles or
    across the antimeridian the longitude range falls back to the full range, which is
    always safe because the exact distance check still follows.
    """
    dlat = radius_km / KM_PER_DEGREE_LAT
    min_lat, max_lat = max(-90.0, lat - dlat), min(90.0, lat + dlat)

    cos_lat = math.cos(math.radians(lat))
    if cos_lat < 1e-6 or min_lat <= -90.0 or max_lat >= 90.0:
        return min_lat, max_lat, -180.0, 180.0
    dlon = radius_km / (KM_PER_DEGREE_LAT * cos_lat)
    min_lon, max_lon = lon - dlon, lon + dlon
    if min_lon < -180.0 or max_lon > 180.0:
        return min_lat, max_lat, -180.0, 180.0
    return min_lat, max_lat, min_lon, max_lon
