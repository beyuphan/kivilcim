"""Load years of past VIIRS detections so the past-fires page can show the big fires of earlier seasons.

    python scripts/backfill_history.py                      # 2016 up to the day before the live data
    python scripts/backfill_history.py --from 2021-07-01 --to 2021-09-15 --source VIIRS_SNPP_SP

It runs the normal backfill (5 day windows, duplicates ignored) in pieces of a month, tries a failed
piece a few times and goes on with the next, and prints what it could not load at the end, so it can
be left running and run again later. The archive products of FIRMS start in 2012 (Suomi NPP), 2018
(NOAA-20) and 2023 (NOAA-21); a satellite that did not exist yet simply returns nothing.
"""

import argparse
import calendar
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from sqlalchemy.exc import OperationalError

from kivilcim.config import get_settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.firms import FirmsError
from kivilcim.ingest import backfill

SOURCES = (
    ("VIIRS_SNPP_SP", date(2016, 1, 1)),
    ("VIIRS_NOAA20_SP", date(2018, 4, 1)),
    ("VIIRS_NOAA21_SP", date(2023, 1, 1)),
)


def month_pieces(start: date, end: date):
    day = start
    while day <= end:
        last = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
        yield day, min(last, end)
        day = last + timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="only this product")
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 5, 31))
    parser.add_argument("--done", help="a file that lists the finished pieces, so a rerun skips them")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    failed: list[tuple[str, date, date]] = []
    finished = set()
    if args.done and Path(args.done).exists():
        finished = set(Path(args.done).read_text(encoding="utf-8").split())
    with httpx.Client(timeout=120) as client:
        for source, first in SOURCES:
            if args.source and source != args.source:
                continue
            start = max(first, args.start) if args.start else first
            for a, b in month_pieces(start, args.end):
                if f"{source}:{a:%Y-%m}" in finished:
                    continue
                for attempt in range(5):
                    try:
                        new = backfill(factory, client, settings.firms_map_key, source, a, b)
                        print(f"{source} {a:%Y-%m}: {new} new", flush=True)
                        if args.done:
                            with open(args.done, "a", encoding="utf-8") as done:
                                done.write(f"{source}:{a:%Y-%m}" + chr(10))
                        break
                    except (FirmsError, httpx.HTTPError, OperationalError) as error:
                        print(f"{source} {a:%Y-%m}: try {attempt + 1} failed ({type(error).__name__})", flush=True)
                        time.sleep(60 * (attempt + 1))  # the key allows 5000 transactions in 10 minutes; wait for the window
                else:
                    failed.append((source, a, b))
    print("could not load:", failed or "nothing", flush=True)


if __name__ == "__main__":
    main()
