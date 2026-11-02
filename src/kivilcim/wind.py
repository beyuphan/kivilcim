"""Is the wind carrying a fire toward a place? The same plain geometry as the page (`static/js/wind.js`),
for messages. It uses the wind measured now and says nothing about where a fire will go: wind changes
within hours, and terrain and fuel matter more than anything here."""

import math

CALM_KMH = 8.0
NAMES = ["kuzey", "kuzeydoğu", "doğu", "güneydoğu", "güney", "güneybatı", "batı", "kuzeybatı"]
FROM = {
    "kuzey": "kuzeyden", "kuzeydoğu": "kuzeydoğudan", "doğu": "doğudan", "güneydoğu": "güneydoğudan",
    "güney": "güneyden", "güneybatı": "güneybatıdan", "batı": "batıdan", "kuzeybatı": "kuzeybatıdan",
}


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """The compass bearing in degrees (0 = north) from the first point to the second."""
    d_lon = math.radians(lon2 - lon1)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(d_lon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    return abs(((a - b + 540) % 360) - 180)


def compass_name(deg: float) -> str:
    return NAMES[round((deg % 360) / 45) % 8]


def from_name(deg: float) -> str:
    return FROM[compass_name(deg)]


def relation(place: tuple[float, float], fire: tuple[float, float], wind_from: float | None, speed_kmh: float | None) -> str:
    """'toward' when the wind blows from the fire to the place (within 45 degrees), 'across' (within 100),
    'away', or 'calm' when it is too weak or unknown to carry anything in a direction."""
    if wind_from is None or speed_kmh is None or speed_kmh < CALM_KMH:
        return "calm"
    diff = angle_diff(wind_from, bearing(place[0], place[1], fire[0], fire[1]))
    return "toward" if diff <= 45 else "across" if diff <= 100 else "away"
