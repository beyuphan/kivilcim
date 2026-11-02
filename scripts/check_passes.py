"""Compare predicted satellite positions with the real detections in the database.

For each recent detection, the satellite that made it must have been within its swath of that point
at the observation time. This prints how far from the satellite's ground point the detections were,
so the half swath widths in `kivilcim.passes` can be checked (or set) from data.

    python scripts/check_passes.py [--hours 48]

Uses current orbit elements, so only look at recent detections (a few days).
"""

import argparse
import math
from datetime import datetime, timedelta, timezone

import httpx
from sgp4.api import Satrec
from sqlalchemy import select

from kivilcim.config import get_settings
from kivilcim.db import make_engine, make_session_factory
from kivilcim.models import FireDetection
from kivilcim.passes import BY_CODE, SATELLITES, _unit, fetch_elements, ground_distance_km


def percentile(values: list[float]):
    return lambda q: values[min(len(values) - 1, math.floor(q * len(values)))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=48)
    args = parser.parse_args()

    settings = get_settings()
    session_factory = make_session_factory(make_engine(settings.database_url))
    with httpx.Client() as client:
        elements = fetch_elements(client)
    satrecs = {norad: Satrec.twoline2rv(line1, line2) for norad, (_, line1, line2) in elements.items()}

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    with session_factory() as session:
        rows = session.scalars(select(FireDetection).where(FireDetection.acquired_at >= since)).all()

    distances: dict[str, list[float]] = {}
    for row in rows:
        sat = BY_CODE.get(row.satellite)
        if sat is None or sat.norad not in satrecs:
            continue
        d = ground_distance_km(satrecs[sat.norad], row.acquired_at, _unit(row.latitude, row.longitude))
        if d is not None:
            distances.setdefault(sat.code, []).append(d)

    print(f"{len(rows)} detections in the last {args.hours} hours")
    for sat in SATELLITES:
        values = sorted(distances.get(sat.code, []))
        if not values:
            print(f"{sat.name:10} no detections")
            continue
        pick = percentile(values)
        inside = sum(v <= sat.half_swath_km for v in values) / len(values)
        print(
            f"{sat.name:10} n={len(values):5}  median {pick(0.5):6.0f} km  p95 {pick(0.95):6.0f}  "
            f"p99 {pick(0.99):6.0f}  max {values[-1]:6.0f}  inside {sat.half_swath_km:.0f} km: {inside:.1%}"
        )


if __name__ == "__main__":
    main()
