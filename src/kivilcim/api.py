"""REST API: /health, /status and /fires.

Start with: uvicorn kivilcim.api:create_app --factory
"""

import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
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
from kivilcim.events import Event, find_events
from kivilcim.fixed import cell_of, find_fixed_cells
from kivilcim.geo import bounding_box, haversine_km
from kivilcim.models import FireDetection, IngestRun, WeatherSnapshot
from kivilcim.risk import compute_risk
from kivilcim.weather import (
    ATTRIBUTION,
    CacheStatus,
    WeatherUnavailable,
    get_weather,
    get_weather_many,
    round_coordinate,
)
from kivilcim.zones import find_zones

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
    # Detections in a cell that detects on a large share of all days: a factory, refinery, flare
    # or landfill is likely. Flagged and listed apart, never dropped.
    recurring_sources: list[FireOut]
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


EVENTS_NOTE = (
    "Events are satellite detections grouped automatically by strength, place and time. "
    "They are not confirmed incidents, and some may be crop burning or other heat sources "
    "rather than forest fires."
)


class EventOut(BaseModel):
    id: str
    start: date
    end: date
    peak_day: date
    days: int
    detections: int
    total_frp_mw: float
    max_frp_mw: float
    center_lat: float
    center_lon: float
    bbox: list[float]  # min_lat, min_lon, max_lat, max_lon
    extent_km: float  # diagonal of the bbox
    district: str  # nearest district center, approximate
    province: str
    in_turkey: bool


class EventsResponse(BaseModel):
    count: int
    events: list[EventOut]
    note: str
    disclaimer: str


class EventDetection(BaseModel):
    latitude: float
    longitude: float
    acquired_at: datetime
    frp: float | None
    confidence_level: str
    source: str


class EventDay(BaseModel):
    date: date
    detections: int
    total_frp_mw: float


class EventDetailResponse(BaseModel):
    event: EventOut
    window_start: date
    window_end: date
    days: list[EventDay]
    detections: list[EventDetection]
    note: str
    disclaimer: str


MAX_EVENT_DETECTIONS = 5000


def event_out(event: Event) -> EventOut:
    return EventOut(
        id=event.id,
        start=event.start,
        end=event.end,
        peak_day=event.peak_day,
        days=event.days,
        detections=event.detections,
        total_frp_mw=event.total_frp,
        max_frp_mw=event.max_frp,
        center_lat=event.center_lat,
        center_lon=event.center_lon,
        bbox=[event.min_lat, event.min_lon, event.max_lat, event.max_lon],
        extent_km=event.extent_km,
        district=event.district,
        province=event.province,
        in_turkey=event.in_turkey,
    )


class ZoneOut(BaseModel):
    id: str
    latitude: float
    longitude: float
    detections: int
    total_frp_mw: float
    max_frp_mw: float
    latest_observation_at: datetime
    age_minutes: float
    bbox: list[float]  # min_lat, min_lon, max_lat, max_lon
    radius_km: float  # for drawing
    district: str  # nearest district center, approximate
    province: str
    risk: RiskOut | None  # None when the weather could not be obtained
    weather: WeatherOut | None


class ZonesResponse(BaseModel):
    since_hours: int
    count: int
    zones: list[ZoneOut]
    data_as_of: DataAsOf
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

    @app.get("/history", include_in_schema=False)
    def history() -> FileResponse:
        return FileResponse(STATIC_DIR / "history.html")

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

        fixed = fixed_cells(session)

        def is_fixed(f: FireOut) -> bool:
            return cell_of(f.latitude, f.longitude) in fixed

        return FiresResponse(
            query=FiresQuery(lat=lat, lon=lon, radius_km=radius_km, since_hours=since_hours),
            count=len(found),
            fires=[f for f in found if f.confidence_level != "low" and not is_fixed(f)],
            low_confidence=[f for f in found if f.confidence_level == "low"],
            recurring_sources=[f for f in found if f.confidence_level != "low" and is_fixed(f)],
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

    @app.get("/risk/zones", response_model=ZonesResponse)
    def risk_zones(
        since_hours: int = Query(default=24, ge=1, le=72),
        session: Session = Depends(get_session),
    ) -> ZonesResponse:
        """Active fire zones across Turkey, each with the weather index at its center.

        Weather for all zones is fetched in one request (and cached), strongest zones first, so
        if the daily call quota runs short it is the weakest zones that go without a value.
        """
        current = clock()
        rows = session.scalars(
            select(FireDetection).where(FireDetection.acquired_at >= current - timedelta(hours=since_hours))
        ).all()
        zones = find_zones(rows, fixed_cells(session))
        weather_by_point = get_weather_many(
            session,
            weather_client,
            [(z.center_lat, z.center_lon) for z in zones],
            current,
            settings.weather_cache_minutes,
            settings.open_meteo_daily_quota,
        )

        out = []
        for zone in zones:
            result = weather_by_point.get((round_coordinate(zone.center_lat), round_coordinate(zone.center_lon)))
            risk = weather = None
            if result is not None:
                snap = result.snapshot
                scored = compute_risk(snap.temperature, snap.humidity, snap.wind_speed, snap.precip_recent)
                weather = weather_out(snap, result.status)
                risk = RiskOut(
                    score=scored.score,
                    level=scored.level,
                    drivers=scored.drivers,
                    contributions=scored.contributions,
                    index_version=scored.index_version,
                    experimental=True,
                    note=RISK_NOTE,
                )
            out.append(
                ZoneOut(
                    id=zone.id,
                    latitude=zone.center_lat,
                    longitude=zone.center_lon,
                    detections=zone.detections,
                    total_frp_mw=zone.total_frp,
                    max_frp_mw=zone.max_frp,
                    latest_observation_at=zone.latest_observation,
                    age_minutes=round((current - zone.latest_observation).total_seconds() / 60, 1),
                    bbox=[zone.min_lat, zone.min_lon, zone.max_lat, zone.max_lon],
                    radius_km=zone.radius_km,
                    district=zone.district,
                    province=zone.province,
                    risk=risk,
                    weather=weather,
                )
            )
        return ZonesResponse(
            since_hours=since_hours,
            count=len(out),
            zones=out,
            data_as_of=data_as_of(session, current),
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

    fixed_lock = threading.Lock()
    fixed_cache: dict = {"key": None, "cells": set()}

    def fixed_cells(session: Session) -> set:
        """Cells that look like fixed heat sources, found from the VIIRS history. Cached until a new
        detection is stored (see all_events for why a count and max id are a safe key)."""
        condition = (FireDetection.instrument == "VIIRS", FireDetection.confidence_level != "low")
        key = tuple(session.execute(select(func.count(FireDetection.id), func.max(FireDetection.id)).where(*condition)).one())
        with fixed_lock:
            if fixed_cache["key"] != key:
                rows = session.execute(
                    select(FireDetection.latitude, FireDetection.longitude, FireDetection.acquired_at).where(*condition)
                ).all()
                fixed_cache.update(
                    key=key, cells=find_fixed_cells((lat, lon, when.date()) for lat, lon, when in rows)
                )
            return fixed_cache["cells"]

    events_lock = threading.Lock()
    events_cache: dict = {"key": None, "events": []}

    def all_events(session: Session) -> list[Event]:
        """Events from VIIRS detections only: MODIS pixels are 1 km, so their heat power is on a
        different scale and would distort the strength thresholds. The result is cached until a
        new detection is stored (rows only ever get added, so a count and max id are a safe key)."""
        key = tuple(
            session.execute(
                select(func.count(FireDetection.id), func.max(FireDetection.id)).where(
                    FireDetection.instrument == "VIIRS"
                )
            ).one()
        )
        with events_lock:
            if events_cache["key"] != key:
                rows = session.scalars(select(FireDetection).where(FireDetection.instrument == "VIIRS")).all()
                events_cache.update(key=key, events=find_events(rows))
            return events_cache["events"]

    @app.get("/events", response_model=EventsResponse)
    def events(
        turkey_only: bool = True,
        since: date | None = None,
        until: date | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
    ) -> EventsResponse:
        found = [
            e
            for e in all_events(session)
            if (e.in_turkey or not turkey_only)
            and (since is None or e.end >= since)
            and (until is None or e.start <= until)
        ][:limit]
        return EventsResponse(
            count=len(found), events=[event_out(e) for e in found], note=EVENTS_NOTE, disclaimer=DISCLAIMER
        )

    @app.get("/events/{event_id}", response_model=EventDetailResponse)
    def event_detail(
        event_id: str,
        days_before: int = Query(default=5, ge=0, le=30),
        days_after: int = Query(default=10, ge=0, le=60),
        padding_deg: float = Query(default=0.3, ge=0, le=2),
        session: Session = Depends(get_session),
    ) -> EventDetailResponse:
        event = next((e for e in all_events(session) if e.id == event_id), None)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")

        window_start = event.start - timedelta(days=days_before)
        window_end = event.end + timedelta(days=days_after)
        rows = session.scalars(
            select(FireDetection)
            .where(
                FireDetection.latitude.between(event.min_lat - padding_deg, event.max_lat + padding_deg),
                FireDetection.longitude.between(event.min_lon - padding_deg, event.max_lon + padding_deg),
                FireDetection.acquired_at >= datetime.combine(window_start, datetime.min.time(), timezone.utc),
                FireDetection.acquired_at
                < datetime.combine(window_end + timedelta(days=1), datetime.min.time(), timezone.utc),
            )
            .order_by(FireDetection.acquired_at)
            .limit(MAX_EVENT_DETECTIONS)
        ).all()

        per_day: dict[date, list[float]] = {}
        for row in rows:
            per_day.setdefault(row.acquired_at.date(), []).append(row.frp or 0.0)
        return EventDetailResponse(
            event=event_out(event),
            window_start=window_start,
            window_end=window_end,
            days=[
                EventDay(date=day, detections=len(values), total_frp_mw=round(sum(values), 1))
                for day, values in sorted(per_day.items())
            ],
            detections=[
                EventDetection(
                    latitude=r.latitude,
                    longitude=r.longitude,
                    acquired_at=r.acquired_at,
                    frp=r.frp,
                    confidence_level=r.confidence_level,
                    source=r.source,
                )
                for r in rows
            ],
            note=EVENTS_NOTE,
            disclaimer=DISCLAIMER,
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
