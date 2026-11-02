"""REST API: /health, /status and /fires.

Start with: uvicorn kivilcim.api:create_app --factory
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from kivilcim.config import get_settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.geo import bounding_box, haversine_km
from kivilcim.models import FireDetection, IngestRun

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
) -> FastAPI:
    if session_factory is None:
        engine = make_engine(get_settings().database_url)
        init_db(engine)
        session_factory = make_session_factory(engine)
    clock = now or (lambda: datetime.now(timezone.utc))

    app = FastAPI(
        title="Kıvılcım",
        description=f"Wildfire detections from NASA FIRMS satellite data. {DISCLAIMER}",
    )

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

    return app
