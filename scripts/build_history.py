"""Find and store the fire events of earlier years (the past-fires page reads them from the database).

    python scripts/build_history.py            # what is missing, up to the start of the live window
    python scripts/build_history.py --force    # everything again, e.g. after changing the thresholds

The running site does this by itself after each ingest; this is for the first time, after a large
backfill (`scripts/backfill_history.py`), and for rebuilding.
"""

import argparse
import logging
import time
from datetime import datetime, timezone

from kivilcim.config import get_settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.history import live_from, materialize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="redo everything")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    engine = make_engine(get_settings().database_url)
    init_db(engine)
    factory = make_session_factory(engine)
    today = datetime.now(timezone.utc).date()
    started = time.time()
    stored = materialize(factory, today, force=args.force)
    print(f"{stored} fire events stored up to {live_from(today)} in {time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
