"""Fire events across years, without reading years of detections on every request.

Recent events (the last `LIVE_DAYS` days, from the first of the month) are found from the detections each
time new ones arrive. Older events are found once, year by year, and kept in the `fire_events` table; they
cannot change any more. `materialize` brings the table up to the start of the live window, and runs again
after each ingest, so a month that leaves the live window is stored as it leaves.
"""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from kivilcim.events import Event, find_events
from kivilcim.models import BotState, FireDetection, FireEventRow

log = logging.getLogger("kivilcim.history")

LIVE_DAYS = 120
EDGE_DAYS = 21  # detections read past the edges of a period, so an event across the edge is not cut
BUILT_KEY = "events_built_until"


def live_from(today: date) -> date:
    """The first day of the live window: the first of the month, LIVE_DAYS ago."""
    earlier = today - timedelta(days=LIVE_DAYS)
    return earlier.replace(day=1)


def _utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def light_rows(session: Session, start: date, end: date):
    """The few columns events need, for VIIRS detections from `start` up to (not including) `end`."""
    return session.execute(
        select(
            FireDetection.latitude,
            FireDetection.longitude,
            FireDetection.acquired_at,
            FireDetection.frp,
            FireDetection.confidence_level,
        ).where(
            FireDetection.instrument == "VIIRS",
            FireDetection.acquired_at >= _utc(start),
            FireDetection.acquired_at < _utc(end),
        )
    ).all()


def to_row(e: Event) -> FireEventRow:
    return FireEventRow(
        id=e.id, start=e.start, end=e.end, peak_day=e.peak_day, days=e.days, detections=e.detections,
        total_frp=e.total_frp, max_frp=e.max_frp, center_lat=e.center_lat, center_lon=e.center_lon,
        min_lat=e.min_lat, max_lat=e.max_lat, min_lon=e.min_lon, max_lon=e.max_lon, extent_km=e.extent_km,
        district=e.district, province=e.province, in_turkey=e.in_turkey,
    )


def from_row(r: FireEventRow) -> Event:
    return Event(
        id=r.id, start=r.start, end=r.end, peak_day=r.peak_day, days=r.days, detections=r.detections,
        total_frp=r.total_frp, max_frp=r.max_frp, center_lat=r.center_lat, center_lon=r.center_lon,
        min_lat=r.min_lat, max_lat=r.max_lat, min_lon=r.min_lon, max_lon=r.max_lon, extent_km=r.extent_km,
        district=r.district, province=r.province, in_turkey=r.in_turkey,
    )


def live_events(session: Session, today: date) -> list[Event]:
    """Events of the live window, found from the detections themselves. Thresholds are the present
    ones (three satellites): the window is recent, so all three are there."""
    start = live_from(today)
    found = find_events(light_rows(session, start - timedelta(days=EDGE_DAYS), today + timedelta(days=365)), scale_by_satellites=True)
    return [e for e in found if e.start >= start]


def stored_events(session: Session) -> list[Event]:
    return [from_row(r) for r in session.scalars(select(FireEventRow).order_by(FireEventRow.start.desc()))]


def stored_key(session: Session) -> tuple:
    return tuple(session.execute(select(func.count(FireEventRow.id), func.max(FireEventRow.start))).one())


def materialize(session_factory: sessionmaker[Session], today: date, force: bool = False) -> int:
    """Find and store the events older than the live window. Works one period at a time (a year), from where
    the last run stopped, and returns how many events were stored. `force` redoes everything."""
    stop = live_from(today)
    with session_factory() as session:
        first = session.scalar(select(func.min(FireDetection.acquired_at)).where(FireDetection.instrument == "VIIRS"))
        if first is None:
            return 0
        built = session.get(BotState, BUILT_KEY)
        begin = first.date() if force or built is None else date.fromisoformat(built.value)
        if begin >= stop:
            return 0
        if force:
            session.execute(delete(FireEventRow))
            session.commit()

    stored = 0
    # Periods are whole years, so a fire season is never cut; the last one ends at the live window.
    year_start = date(begin.year, 1, 1)
    while year_start < stop:
        year_end = min(date(year_start.year + 1, 1, 1), stop)
        period_start = max(year_start, begin)
        with session_factory() as session:
            rows = light_rows(session, year_start - timedelta(days=EDGE_DAYS), year_end + timedelta(days=EDGE_DAYS))
            found = [
                e for e in find_events(rows, scale_by_satellites=True) if period_start <= e.start < year_end
            ]
            session.execute(delete(FireEventRow).where(FireEventRow.start >= period_start, FireEventRow.start < year_end))
            session.add_all(to_row(e) for e in found)
            session.merge(BotState(key=BUILT_KEY, value=year_end.isoformat()))
            session.commit()
            stored += len(found)
        log.info("fire events of %s to %s: %d", period_start, year_end, len(found))
        year_start = date(year_start.year + 1, 1, 1)
    return stored
