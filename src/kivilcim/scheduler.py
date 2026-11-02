"""A background timer for the live ingest, so one process can serve the site and keep it fresh.

An external scheduler (cron, a task scheduler) running `python -m kivilcim.ingest` works just as
well; this is for running everything with a single command.
"""

import logging
import math
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from kivilcim.config import Settings
from kivilcim.ingest import MAX_DAY_RANGE, run_ingest
from kivilcim.models import IngestRun

log = logging.getLogger("kivilcim.scheduler")


class IngestScheduler:
    """Runs `job` once right away and then every `interval_seconds`, on a background thread.

    An exception in the job is logged and does not stop the timer. `stop()` returns promptly even
    while the timer is waiting.
    """

    def __init__(self, job: Callable[[], None], interval_seconds: float, name: str = "ingest") -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._job = job
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"kivilcim-{name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._job()
            except Exception:  # noqa: BLE001 - a failed run must never end the timer
                log.exception("scheduled ingest failed")
            self._stop.wait(self._interval)


def catchup_day_range(
    last_successes: Sequence[datetime | None], now: datetime, default: int, cap: int = MAX_DAY_RANGE
) -> tuple[int, bool]:
    """How many days back a run should ask for, and whether the gap was larger than FIRMS allows.

    Normally `default`. If the process was not running for a while, the window grows to cover the
    time since the oldest last success (plus a day of overlap), up to `cap`. The overlap is
    harmless because stored detections are deduplicated.
    """
    known = [t for t in last_successes if t is not None]
    if not known:
        return default, False
    gap_days = (now - min(known)).total_seconds() / 86400
    needed = math.ceil(gap_days) + 1
    return min(cap, max(default, needed)), needed > cap


def last_successes(session: Session, sources: Sequence[str]) -> list[datetime | None]:
    return [
        session.scalar(
            select(func.max(IngestRun.finished_at)).where(IngestRun.source == source, IngestRun.status == "success")
        )
        for source in sources
    ]


def make_ingest_job(
    session_factory: sessionmaker[Session], settings: Settings, client: httpx.Client
) -> Callable[[], None]:
    """A job that ingests every configured source, widening the window after downtime."""

    def job() -> None:
        now = datetime.now(timezone.utc)
        with session_factory() as session:
            previous = last_successes(session, settings.source_list)
        day_range, too_old = catchup_day_range(previous, now, settings.firms_day_range)
        if too_old:
            log.warning(
                "the last successful ingest is more than %d days old; FIRMS gives at most that many "
                "days per request, so older detections need `python -m kivilcim.ingest --source ... --from ... --to ...`",
                MAX_DAY_RANGE,
            )
        for run in run_ingest(session_factory, client, settings, day_range=day_range):
            if run.status == "success":
                log.info("%s: %d new detections", run.source, run.record_count)
            else:
                log.warning("%s: failed (%s)", run.source, run.error)

    return job
