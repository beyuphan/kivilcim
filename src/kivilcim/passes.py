"""When did a satellite last look at a place, and when will the next one?

A detection map cannot say anything about a place that no satellite looked at. This module predicts,
from public orbit data (two-line elements from CelesTrak), when each fire-detecting satellite's scan
swath covers a point. "No detection near you" then comes with "the last scan was 40 minutes ago".

The model is deliberately simple: the point is covered when it lies within half the swath width of
the satellite's ground point. The half widths were set from the data itself (see
`scripts/check_passes.py`): they are the cross-track distances that contain the real detections.
"""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sgp4.api import Satrec, jday
from sgp4.propagation import gstime
from sqlalchemy import select
from sqlalchemy.orm import Session

from kivilcim.models import OrbitElements

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
EARTH_RADIUS_KM = 6371.0
STEP_SECONDS = 30
FETCH_PAUSE = 1.5  # seconds between tries at CelesTrak, which refuses bursts
MAX_ELEMENTS_AGE_DAYS = 14  # past this the prediction is shown as unreliable
REFRESH_HOURS = 12


@dataclass(frozen=True)
class Satellite:
    code: str  # the `satellite` value in the detections
    name: str
    norad: int
    instrument: str
    half_swath_km: float  # how far from its ground track a point is still scanned


# VIIRS scans 3,040 km wide, MODIS 2,330 km; the usable half widths below are a little smaller because
# the outer edge of a scan is too coarse to detect small fires.
SATELLITES = (
    Satellite("N", "Suomi NPP", 37849, "VIIRS", 1550.0),
    Satellite("N20", "NOAA-20", 43013, "VIIRS", 1550.0),
    Satellite("N21", "NOAA-21", 54234, "VIIRS", 1550.0),
    Satellite("Terra", "Terra", 25994, "MODIS", 1150.0),
    Satellite("Aqua", "Aqua", 27424, "MODIS", 1150.0),
)
BY_CODE = {s.code: s for s in SATELLITES}

_E2 = 0.00669437999014  # squared eccentricity of the Earth, for the geocentric latitude


@dataclass(frozen=True)
class Pass:
    satellite: str  # the display name
    code: str
    instrument: str
    start: datetime
    end: datetime
    closest: datetime  # when the point was nearest the ground track
    cross_km: float  # that nearest distance


# ---- orbit elements ---------------------------------------------------------


def parse_elements(text: str) -> tuple[str, str, str] | None:
    """(name, line1, line2) from the three lines CelesTrak sends, or None if it is not that."""
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
        return None
    return lines[0].strip(), lines[1], lines[2]


def fetch_elements(client: httpx.Client, pause: float | None = None) -> dict[int, tuple[str, str, str]]:
    """The current elements for every satellite. CelesTrak answers one satellite per request and
    sometimes refuses a burst, so each is tried up to three times with a pause. Satellites that could
    not be fetched are left out (the stored elements stay in use)."""
    found: dict[int, tuple[str, str, str]] = {}
    pause = FETCH_PAUSE if pause is None else pause
    for sat in SATELLITES:
        for attempt in range(3):
            if attempt or found:
                time.sleep(pause)
            try:
                response = client.get(CELESTRAK, params={"CATNR": sat.norad, "FORMAT": "tle"}, timeout=15)
            except httpx.HTTPError:
                continue
            parsed = parse_elements(response.text) if response.status_code == 200 else None
            if parsed:
                found[sat.norad] = parsed
                break
    return found


def refresh_elements(session: Session, client: httpx.Client, now: datetime, force: bool = False) -> bool:
    """Fetch new elements when the stored ones are older than REFRESH_HOURS. True if anything was stored."""
    stored = {row.norad: row for row in session.scalars(select(OrbitElements))}
    fresh = len(stored) == len(SATELLITES) and all(
        now - row.fetched_at < timedelta(hours=REFRESH_HOURS) for row in stored.values()
    )
    if fresh and not force:
        return False
    got = fetch_elements(client)
    for norad, (name, line1, line2) in got.items():
        session.merge(OrbitElements(norad=norad, name=name, line1=line1, line2=line2, fetched_at=now))
    session.commit()
    return bool(got)


def load_elements(session: Session) -> dict[int, OrbitElements]:
    return {row.norad: row for row in session.scalars(select(OrbitElements))}


def elements_age(rows: dict[int, OrbitElements], now: datetime) -> timedelta | None:
    """How old the oldest stored elements are, by their own epoch (not by when they were fetched)."""
    ages = [now - _epoch(r.line1) for r in rows.values()]
    return max(ages) if ages else None


def _epoch(line1: str) -> datetime:
    year = int(line1[18:20])
    day = float(line1[20:32])
    year += 2000 if year < 57 else 1900
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)


# ---- geometry ---------------------------------------------------------------


def _unit(lat: float, lon: float) -> tuple[float, float, float]:
    """A point on the ground as a unit vector in the Earth-fixed frame (geocentric latitude)."""
    phi = math.atan((1 - _E2) * math.tan(math.radians(lat)))
    lam = math.radians(lon)
    return math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)


def ground_distance_km(satrec: Satrec, when: datetime, point: tuple[float, float, float]) -> float | None:
    """The distance along the ground from the satellite's ground point to `point`."""
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
    error, position, _ = satrec.sgp4(jd, fr)
    if error != 0:
        return None
    theta = gstime(jd + fr)
    x = position[0] * math.cos(theta) + position[1] * math.sin(theta)
    y = -position[0] * math.sin(theta) + position[1] * math.cos(theta)
    z = position[2]
    norm = math.sqrt(x * x + y * y + z * z)
    dot = (x * point[0] + y * point[1] + z * point[2]) / norm
    return EARTH_RADIUS_KM * math.acos(max(-1.0, min(1.0, dot)))


def find_passes(
    rows: dict[int, OrbitElements], lat: float, lon: float, start: datetime, end: datetime
) -> list[Pass]:
    """Every time one of the satellites scans the point between `start` and `end`, in time order."""
    point = _unit(lat, lon)
    passes: list[Pass] = []
    for sat in SATELLITES:
        row = rows.get(sat.norad)
        if row is None:
            continue
        satrec = Satrec.twoline2rv(row.line1, row.line2)
        inside = False
        begin = best_at = last = start
        best = math.inf
        when = start
        step = timedelta(seconds=STEP_SECONDS)
        while when <= end:
            d = ground_distance_km(satrec, when, point)
            covered = d is not None and d <= sat.half_swath_km
            if covered and not inside:
                inside, begin, best, best_at = True, when, d, when
            elif covered:
                if d < best:
                    best, best_at = d, when
            elif inside:
                passes.append(Pass(sat.name, sat.code, sat.instrument, begin, last, best_at, best))
                inside = False
            last = when
            when += step
        if inside:
            passes.append(Pass(sat.name, sat.code, sat.instrument, begin, last, best_at, best))
    return sorted(passes, key=lambda p: p.closest)


def coverage_summary(passes: list[Pass], now: datetime) -> dict:
    """The last scan, the next scan and the longest stretch without any scan, from a list of passes."""
    past = [p for p in passes if p.closest <= now]
    future = [p for p in passes if p.closest > now]
    gaps = []
    times = [p.closest for p in passes]
    for a, b in zip(times, times[1:], strict=False):
        gaps.append((b - a).total_seconds() / 60)
    return {
        "last": past[-1] if past else None,
        "next": future[0] if future else None,
        "longest_gap_minutes": max(gaps) if gaps else None,
    }
