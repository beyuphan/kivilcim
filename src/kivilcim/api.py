"""REST API: /health, /status and /fires.

Start with: uvicorn kivilcim.api:create_app --factory
"""

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from kivilcim.config import Settings, get_settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.geo import bounding_box, haversine_km
from kivilcim.models import FireDetection, IngestRun, WeatherSnapshot
from kivilcim.risk import compute_risk
from kivilcim.weather import ATTRIBUTION, CacheStatus, WeatherUnavailable, get_weather

STATIC_DIR = Path(__file__).parent / "static"

DISCLAIMER = (
    "Kıvılcım is not an official warning system. Satellite detections can be delayed "
    "and can include false positives. In an emergency call 112 and contact AFAD."
)

# Provisional freshness thresholds, applied to the last successful ingest (see PLAN.md).
FRESH_HOURS = 3
WARNING_HOURS = 6

MAX_RADIUS_KM = 500
MAX_SINCE_HOURS = 168

# Weather and risk queries are limited to a loose box around Turkey, so nobody can use the
# service to scan the world and use up the weather API's call quota.
TURKEY_LAT = (35.5, 42.5)
TURKEY_LON = (25.5, 45.5)

Freshness = Literal["fresh", "warning", "stale", "unknown"]


class DataAsOf(BaseModel):
    last_ingest_at: datetime | None
    latest_observation_at: datetime | None
    freshness: Freshness


class SourceStatus(BaseModel):
    source: str
    last_success_at: datetime | None
    last_run_at: datetime
    last_run_status: str
    last_error: str | None
    delay_minutes: float | None
    latest_observation_at: datetime | None
    detection_count: int


class StatusResponse(BaseModel):
    data_as_of: DataAsOf
    sources: list[SourceStatus]
    disclaimer: str


class FireOut(BaseModel):
    source: str
    satellite: str
    instrument: str
    latitude: float
    longitude: float
    acquired_at: datetime
    age_minutes: float
    confidence_level: str
    confidence_raw: str
    distance_km: float
    brightness: float | None
    frp: float | None


class FiresQuery(BaseModel):
    lat: float
    lon: float
    radius_km: float
    since_hours: int


class FiresResponse(BaseModel):
    query: FiresQuery
    count: int
    fires: list[FireOut]  # nominal and high confidence
    low_confidence: list[FireOut]  # shown separately, never mixed in
    data_as_of: DataAsOf
    disclaimer: str


class WeatherOut(BaseModel):
    latitude: float  # the rounded point the weather is for
    longitude: float
    observed_at: datetime
    fetched_at: datetime
    temperature_c: float
    humidity_pct: float
    wind_speed_kmh: float
    wind_direction_deg: float | None
    precip_72h_mm: float
    cache_status: CacheStatus


class WeatherResponse(BaseModel):
    weather: WeatherOut
    attribution: str
    disclaimer: str


class RiskOut(BaseModel):
    score: float
    level: str
    drivers: list[str]
    contributions: dict[str, float]
    index_version: str
    experimental: bool
    note: str


class RiskResponse(BaseModel):
    risk: RiskOut
    weather: WeatherOut
    attribution: str
    disclaimer: str


RISK_NOTE = (
    "Experimental fire danger level from current weather. It shows how favorable the weather "
    "is for fires, not the probability that a fire will happen."
)


def weather_out(snapshot: WeatherSnapshot, status: CacheStatus) -> WeatherOut:
    return WeatherOut(
        latitude=snapshot.latitude,
        longitude=snapshot.longitude,
        observed_at=snapshot.observed_at,
        fetched_at=snapshot.fetched_at,
        temperature_c=snapshot.temperature,
        humidity_pct=snapshot.humidity,
        wind_speed_kmh=snapshot.wind_speed,
        wind_direction_deg=snapshot.wind_direction,
        precip_72h_mm=snapshot.precip_recent,
        cache_status=status,
    )


def freshness_of(last_ingest_at: datetime | None, now: datetime) -> Freshness:
    if last_ingest_at is None:
        return "unknown"
    age = now - last_ingest_at
    if age < timedelta(hours=FRESH_HOURS):
        return "fresh"
    if age < timedelta(hours=WARNING_HOURS):
        return "warning"
    return "stale"


def source_statuses(session: Session, now: datetime) -> list[SourceStatus]:
    statuses = []
    sources = session.scalars(select(IngestRun.source).distinct().order_by(IngestRun.source))
    for source in sources.all():
        last_run = session.scalars(
            select(IngestRun).where(IngestRun.source == source).order_by(IngestRun.id.desc()).limit(1)
        ).one()
        last_success = session.scalar(
            select(func.max(IngestRun.finished_at)).where(
                IngestRun.source == source, IngestRun.status == "success"
            )
        )
        latest_obs, count = session.execute(
            select(func.max(FireDetection.acquired_at), func.count(FireDetection.id)).where(
                FireDetection.source == source
            )
        ).one()
        statuses.append(
            SourceStatus(
                source=source,
                last_success_at=last_success,
                last_run_at=last_run.started_at,
                last_run_status=last_run.status,
                last_error=last_run.error,
                delay_minutes=(
                    round((now - last_success).total_seconds() / 60, 1) if last_success else None
                ),
                latest_observation_at=latest_obs,
                detection_count=count,
            )
        )
    return statuses


def data_as_of(session: Session, now: datetime) -> DataAsOf:
    """Overall freshness is judged by the stalest source, so one stuck source is never hidden."""
    statuses = source_statuses(session, now)
    successes = [s.last_success_at for s in statuses]
    last_ingest = min(successes) if successes and all(successes) else None
    latest_obs = session.scalar(select(func.max(FireDetection.acquired_at)))
    return DataAsOf(
        last_ingest_at=last_ingest,
        latest_observation_at=latest_obs,
        freshness=freshness_of(last_ingest, now),
    )


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    now: Callable[[], datetime] | None = None,
    http_client: httpx.Client | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        engine = make_engine(settings.database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    clock = now or (lambda: datetime.now(timezone.utc))
    owns_client = http_client is None
    weather_client = http_client or httpx.Client(timeout=15)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_client:
            weather_client.close()

    app = FastAPI(
        title="Kıvılcım",
        description=f"Wildfire detections from NASA FIRMS satellite data. {DISCLAIMER}",
        lifespan=lifespan,
    )

    def current_weather(session: Session, lat: float, lon: float):
        try:
            return get_weather(
                session,
                weather_client,
                lat,
                lon,
                clock(),
                settings.weather_cache_minutes,
                settings.open_meteo_daily_quota,
            )
        except WeatherUnavailable:
            raise HTTPException(status_code=503, detail="Weather data is temporarily unavailable.")

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", response_model=StatusResponse)
    def status(session: Session = Depends(get_session)) -> StatusResponse:
        current = clock()
        return StatusResponse(
            data_as_of=data_as_of(session, current),
            sources=source_statuses(session, current),
            disclaimer=DISCLAIMER,
        )

    @app.get("/fires", response_model=FiresResponse)
    def fires(
        lat: float = Query(ge=-90, le=90),
        lon: float = Query(ge=-180, le=180),
        radius_km: float = Query(default=25, gt=0, le=MAX_RADIUS_KM),
        since_hours: int = Query(default=24, ge=1, le=MAX_SINCE_HOURS),
        session: Session = Depends(get_session),
    ) -> FiresResponse:
        current = clock()
        min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_km)
        rows = session.scalars(
            select(FireDetection).where(
                FireDetection.latitude.between(min_lat, max_lat),
                FireDetection.longitude.between(min_lon, max_lon),
                FireDetection.acquired_at >= current - timedelta(hours=since_hours),
            )
        ).all()

        found = []
        for row in rows:
            distance = haversine_km(lat, lon, row.latitude, row.longitude)
            if distance <= radius_km:
                found.append(
                    FireOut(
                        source=row.source,
                        satellite=row.satellite,
                        instrument=row.instrument,
                        latitude=row.latitude,
                        longitude=row.longitude,
                        acquired_at=row.acquired_at,
                        age_minutes=round((current - row.acquired_at).total_seconds() / 60, 1),
                        confidence_level=row.confidence_level,
                        confidence_raw=row.confidence_raw,
                        distance_km=round(distance, 2),
                        brightness=row.brightness,
                        frp=row.frp,
                    )
                )
        found.sort(key=lambda f: f.distance_km)

        return FiresResponse(
            query=FiresQuery(lat=lat, lon=lon, radius_km=radius_km, since_hours=since_hours),
            count=len(found),
            fires=[f for f in found if f.confidence_level != "low"],
            low_confidence=[f for f in found if f.confidence_level == "low"],
            data_as_of=data_as_of(session, current),
            disclaimer=DISCLAIMER,
        )

    @app.get("/weather", response_model=WeatherResponse)
    def weather(
        lat: float = Query(ge=TURKEY_LAT[0], le=TURKEY_LAT[1]),
        lon: float = Query(ge=TURKEY_LON[0], le=TURKEY_LON[1]),
        session: Session = Depends(get_session),
    ) -> WeatherResponse:
        result = current_weather(session, lat, lon)
        return WeatherResponse(
            weather=weather_out(result.snapshot, result.status),
            attribution=ATTRIBUTION,
            disclaimer=DISCLAIMER,
        )

    @app.get("/risk", response_model=RiskResponse)
    def risk(
        lat: float = Query(ge=TURKEY_LAT[0], le=TURKEY_LAT[1]),
        lon: float = Query(ge=TURKEY_LON[0], le=TURKEY_LON[1]),
        session: Session = Depends(get_session),
    ) -> RiskResponse:
        result = current_weather(session, lat, lon)
        snap = result.snapshot
        scored = compute_risk(snap.temperature, snap.humidity, snap.wind_speed, snap.precip_recent)
        return RiskResponse(
            risk=RiskOut(
                score=scored.score,
                level=scored.level,
                drivers=scored.drivers,
                contributions=scored.contributions,
                index_version=scored.index_version,
                experimental=True,
                note=RISK_NOTE,
            ),
            weather=weather_out(snap, result.status),
            attribution=ATTRIBUTION,
            disclaimer=DISCLAIMER,
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
