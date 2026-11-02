from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes.

    SQLite drops timezone info, so values are stored as UTC and returned as aware UTC.
    Naive datetimes are rejected so a local time can never be stored by accident.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use a timezone-aware UTC datetime")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class FireDetection(Base):
    __tablename__ = "fire_detections"
    __table_args__ = (
        UniqueConstraint("source", "latitude", "longitude", "acquired_at", name="uq_fire_detection"),
        Index("ix_fire_detections_acquired_at", "acquired_at"),
        Index("ix_fire_detections_location_time", "latitude", "longitude", "acquired_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # FIRMS product, e.g. VIIRS_SNPP_NRT
    satellite: Mapped[str] = mapped_column(String(16))
    instrument: Mapped[str] = mapped_column(String(16))  # MODIS / VIIRS
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime)
    confidence_raw: Mapped[str] = mapped_column(String(16))
    confidence_level: Mapped[str] = mapped_column(String(16))  # low / nominal / high
    brightness: Mapped[float | None] = mapped_column(Float, nullable=True)
    frp: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WeatherSnapshot(Base):
    """Cached Open-Meteo weather for a coordinate rounded to ~0.1 degree."""

    __tablename__ = "weather_snapshots"
    __table_args__ = (UniqueConstraint("latitude", "longitude", name="uq_weather_location"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)  # time of the "current" reading
    temperature: Mapped[float] = mapped_column(Float)  # degrees C
    humidity: Mapped[float] = mapped_column(Float)  # %
    wind_speed: Mapped[float] = mapped_column(Float)  # km/h
    wind_direction: Mapped[float | None] = mapped_column(Float, nullable=True)  # degrees
    precip_recent: Mapped[float] = mapped_column(Float)  # mm over the last 72 h


class ApiUsage(Base):
    """Calls made to an external API per UTC day, to stay under its free quota."""

    __tablename__ = "api_usage"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # running / success / failed
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
