"""FIRMS ingest job: fetch, validate, deduplicate, store.

Run with `python -m kivilcim.ingest`. Meant to be called by an external scheduler.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from kivilcim.config import Settings, get_settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.firms import FirmsError, parse_csv
from kivilcim.models import FireDetection, IngestRun

FIRMS_AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# west, south, east, north: a box around Turkey (neighboring countries are included).
TURKEY_BBOX = (26.0, 35.8, 45.0, 42.2)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "<KEY>") if secret else text


MAX_DAY_RANGE = 5  # FIRMS rejects a larger window with HTTP 400


def fetch_area_csv(
    client: httpx.Client,
    map_key: str,
    source: str,
    day_range: int,
    bbox: tuple[float, float, float, float] = TURKEY_BBOX,
    start_date: date | None = None,
) -> str:
    """Fetch detections for the last `day_range` days, or for `day_range` days from `start_date`."""
    area = ",".join(str(v) for v in bbox)
    url = f"{FIRMS_AREA_URL}/{map_key}/{source}/{area}/{day_range}"
    if start_date:
        url += f"/{start_date.isoformat()}"
    response = client.get(url)
    if response.status_code != 200:
        raise FirmsError(f"HTTP {response.status_code}")
    return response.text


def store_detections(session: Session, detections: list[FireDetection]) -> int:
    """Insert detections, ignoring ones already stored. Returns how many were new.

    Uses SQLite's ON CONFLICT DO NOTHING on the unique key, so repeated and
    overlapping runs never create duplicates.
    """
    if not detections:
        return 0
    rows = [
        {
            "source": d.source,
            "satellite": d.satellite,
            "instrument": d.instrument,
            "latitude": d.latitude,
            "longitude": d.longitude,
            "acquired_at": d.acquired_at,
            "confidence_raw": d.confidence_raw,
            "confidence_level": d.confidence_level,
            "brightness": d.brightness,
            "frp": d.frp,
            "ingested_at": d.ingested_at,
        }
        for d in detections
    ]
    statement = sqlite_insert(FireDetection).on_conflict_do_nothing(
        index_elements=["source", "latitude", "longitude", "acquired_at"]
    )
    before = session.query(FireDetection).count()
    session.execute(statement, rows)
    return session.query(FireDetection).count() - before


def ingest_source(
    session_factory: sessionmaker[Session],
    client: httpx.Client,
    map_key: str,
    source: str,
    day_range: int,
    now: datetime | None = None,
) -> IngestRun:
    """Ingest one FIRMS product and record the outcome in ingest_runs.

    A failure marks the run as failed and leaves already stored detections untouched.
    """
    started_at = now or datetime.now(timezone.utc)
    with session_factory() as session:
        run = IngestRun(source=source, started_at=started_at, status="running")
        session.add(run)
        session.commit()

        try:
            text = fetch_area_csv(client, map_key, source, day_range)
            parsed = parse_csv(text, source, ingested_at=started_at)
            run.record_count = store_detections(session, parsed.detections)
            run.status = "success"
            if parsed.skipped:
                run.error = f"{len(parsed.skipped)} malformed rows skipped"
        except Exception as exc:  # noqa: BLE001 - any failure must be recorded, never crash the job
            session.rollback()
            run.status = "failed"
            run.error = _redact(f"{type(exc).__name__}: {exc}", map_key)[:500]
        run.finished_at = datetime.now(timezone.utc) if now is None else now
        session.commit()
        return run


def backfill(
    session_factory: sessionmaker[Session],
    client: httpx.Client,
    map_key: str,
    source: str,
    start: date,
    end: date,
    now: datetime | None = None,
    on_window: Callable[[date, int, int, int, int], None] | None = None,
) -> int:
    """Load past detections for [start, end] in 5 day windows. Returns how many were new.

    Backfills are not recorded in ingest_runs: that table tracks the live ingest and feeds
    the freshness shown to users, which a one-off history load must not affect.
    Use an archive product (such as VIIRS_SNPP_SP) for older dates; NRT products only keep
    roughly the last three months.
    """
    if end < start:
        raise ValueError("end is before start")
    ingested_at = now or datetime.now(timezone.utc)
    added = 0
    day = start
    while day <= end:
        days = min(MAX_DAY_RANGE, (end - day).days + 1)
        text = fetch_area_csv(client, map_key, source, days, start_date=day)
        parsed = parse_csv(text, source, ingested_at=ingested_at)
        with session_factory() as session:
            new = store_detections(session, parsed.detections)
            session.commit()
        added += new
        if on_window:
            on_window(day, days, len(parsed.detections), new, len(parsed.skipped))
        day += timedelta(days=days)
    return added


def run_ingest(
    session_factory: sessionmaker[Session],
    client: httpx.Client,
    settings: Settings,
    now: datetime | None = None,
    day_range: int | None = None,
) -> list[IngestRun]:
    """Ingest every configured source. One failing source does not stop the others.

    `day_range` overrides the configured window, for catching up after downtime.
    """
    if not settings.firms_map_key:
        raise ValueError("FIRMS_MAP_KEY is not set")
    days = min(MAX_DAY_RANGE, day_range or settings.firms_day_range)
    return [
        ingest_source(session_factory, client, settings.firms_map_key, source, days, now)
        for source in settings.source_list
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch FIRMS detections. With no options, runs the live ingest. "
        "With --source, --from and --to, loads past detections (a backfill)."
    )
    parser.add_argument("--source", help="FIRMS product for a backfill, e.g. VIIRS_SNPP_SP or VIIRS_SNPP_NRT")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="last day, YYYY-MM-DD")
    args = parser.parse_args(argv)
    backfilling = (args.source, args.start, args.end)
    if any(backfilling) and not all(backfilling):
        parser.error("--source, --from and --to must be given together")

    settings = get_settings()
    if not settings.firms_map_key:
        print("FIRMS_MAP_KEY is not set. Put it in .env (see .env.example).", file=sys.stderr)
        return 2

    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)

    if args.source:
        def report(day: date, days: int, fetched: int, new: int, skipped: int) -> None:
            note = f", {skipped} malformed rows skipped" if skipped else ""
            print(f"{args.source} {day} (+{days - 1} days): {fetched} fetched, {new} new{note}")

        with httpx.Client(timeout=120) as client:
            try:
                total = backfill(factory, client, settings.firms_map_key, args.source, args.start, args.end,
                                 on_window=report)
            except (FirmsError, ValueError) as exc:
                print(f"backfill stopped: {_redact(str(exc), settings.firms_map_key)}", file=sys.stderr)
                return 1
        print(f"done: {total} new detections")
        return 0

    with httpx.Client(timeout=60) as client:
        runs = run_ingest(factory, client, settings)

    for run in runs:
        note = f" ({run.error})" if run.error else ""
        print(f"{run.source}: {run.status}, {run.record_count} new detections{note}")
    return 1 if any(run.status == "failed" for run in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
