"""Open-Meteo client with a coordinate-rounded cache and a daily call quota.

Weather data by Open-Meteo.com (CC BY 4.0).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from kivilcim.models import ApiUsage, WeatherSnapshot

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
PROVIDER = "open-meteo"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
PRECIP_WINDOW = timedelta(hours=72)

CacheStatus = Literal["fresh", "cached", "stale"]


class WeatherUnavailable(Exception):
    """No usable weather could be obtained (quota spent or upstream failure, and nothing cached)."""


@dataclass
class WeatherResult:
    snapshot: WeatherSnapshot
    status: CacheStatus  # fresh: just fetched, cached: within TTL, stale: older, served as a fallback


def round_coordinate(value: float) -> float:
    """Rounding to ~0.1 degree (about 11 km) lets nearby requests share one cached fetch."""
    return round(value, 1)


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def parse_forecast(payload: dict) -> dict:
    """Pull the fields we need out of an Open-Meteo forecast response.

    The hourly list also contains future hours, so only the 72 hours up to the
    current reading are summed for recent precipitation.
    """
    try:
        current = payload["current"]
        observed_at = _parse_time(current["time"])
        temperature = current["temperature_2m"]
        humidity = current["relative_humidity_2m"]
        wind_speed = current["wind_speed_10m"]
        if None in (temperature, humidity, wind_speed):
            raise KeyError("missing current value")

        hourly = payload["hourly"]
        window_start = observed_at - PRECIP_WINDOW
        values = [
            amount
            for stamp, amount in zip(hourly["time"], hourly["precipitation"])
            if amount is not None and window_start < _parse_time(stamp) <= observed_at
        ]
        if not values:
            raise KeyError("no precipitation data")
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherUnavailable(f"unexpected Open-Meteo response: {exc}") from exc

    return {
        "observed_at": observed_at,
        "temperature": float(temperature),
        "humidity": float(humidity),
        "wind_speed": float(wind_speed),
        "wind_direction": current.get("wind_direction_10m"),
        "precip_recent": round(sum(values), 2),
    }


def fetch_weather(client: httpx.Client, lat: float, lon: float) -> dict:
    try:
        response = client.get(
            OPEN_METEO_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
                "hourly": "precipitation",
                "past_days": 3,
                "forecast_days": 1,
                "wind_speed_unit": "kmh",
                "timezone": "UTC",
            },
        )
    except httpx.HTTPError as exc:
        raise WeatherUnavailable(f"Open-Meteo request failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise WeatherUnavailable(f"Open-Meteo returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherUnavailable("Open-Meteo returned invalid JSON") from exc
    return parse_forecast(payload)


def calls_today(session: Session, now: datetime) -> int:
    row = session.get(ApiUsage, (now.strftime("%Y-%m-%d"), PROVIDER))
    return row.calls if row else 0


def _count_call(session: Session, now: datetime) -> None:
    statement = sqlite_insert(ApiUsage).values(day=now.strftime("%Y-%m-%d"), provider=PROVIDER, calls=1)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["day", "provider"], set_={"calls": ApiUsage.calls + 1}
        )
    )
    session.commit()


def get_weather(
    session: Session,
    client: httpx.Client,
    lat: float,
    lon: float,
    now: datetime,
    cache_minutes: int,
    daily_quota: int,
) -> WeatherResult:
    """Cached weather for a point, fetching from Open-Meteo only when needed.

    Order of preference: a cached value within the TTL, then a fresh fetch, then an
    older cached value (marked stale) if the quota is spent or Open-Meteo fails.
    """
    rlat, rlon = round_coordinate(lat), round_coordinate(lon)
    cached = session.scalars(
        select(WeatherSnapshot).where(WeatherSnapshot.latitude == rlat, WeatherSnapshot.longitude == rlon)
    ).first()

    if cached and now - cached.fetched_at < timedelta(minutes=cache_minutes):
        return WeatherResult(cached, "cached")

    failure: WeatherUnavailable | None = None
    if calls_today(session, now) >= daily_quota:
        failure = WeatherUnavailable("daily Open-Meteo call quota reached")
    else:
        _count_call(session, now)
        try:
            data = fetch_weather(client, rlat, rlon)
        except WeatherUnavailable as exc:
            failure = exc
        else:
            if cached is None:
                cached = WeatherSnapshot(latitude=rlat, longitude=rlon)
                session.add(cached)
            cached.fetched_at = now
            for name, value in data.items():
                setattr(cached, name, value)
            session.commit()
            return WeatherResult(cached, "fresh")

    if cached:
        return WeatherResult(cached, "stale")
    raise failure
