from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, UniqueConstraint
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


class OrbitElements(Base):
    """The latest two-line orbit elements of a fire-detecting satellite, from CelesTrak."""

    __tablename__ = "orbit_elements"

    norad: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    line1: Mapped[str] = mapped_column(String(80))
    line2: Mapped[str] = mapped_column(String(80))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)


class AlertSubscription(Base):
    """A place a person asked the Telegram bot to watch. Only the chat id and a place rounded to about a
    kilometer are kept; the person can delete it with /durdur."""

    __tablename__ = "alert_subscriptions"
    __table_args__ = (Index("ix_alert_subscriptions_chat", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(60))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_km: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)  # the last time the person wrote to the bot
    last_alert_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AlertSent(Base):
    """An alert that went out, so the same fire is not announced again at once. `sub_id` -1 is the channel."""

    __tablename__ = "alert_sent"

    sub_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cell: Mapped[str] = mapped_column(String(80), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime)


class BotState(Base):
    """Small facts the bot must remember: the last message it read, the last time it announced."""

    __tablename__ = "bot_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(String(64))


class EventImagery(Base):
    """The before and after satellite images chosen for an event, kept so the search is done once."""

    __tablename__ = "event_imagery"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ttl_seconds: Mapped[int] = mapped_column(Integer)  # how long the choice may be reused
    payload: Mapped[str] = mapped_column(String)  # JSON


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
