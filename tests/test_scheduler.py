import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import IngestRun
from kivilcim.scheduler import IngestScheduler, catchup_day_range, make_ingest_job

UTC = timezone.utc
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


# ---- the timer --------------------------------------------------------------


def test_the_job_runs_at_once_and_then_repeatedly():
    reached = threading.Event()
    calls = []

    def job():
        calls.append(1)
        if len(calls) >= 3:
            reached.set()

    scheduler = IngestScheduler(job, interval_seconds=0.01)
    scheduler.start()
    assert reached.wait(5), "the job did not repeat"
    scheduler.stop()
    assert len(calls) >= 3


def test_a_failing_job_does_not_stop_the_timer():
    reached = threading.Event()
    calls = []

    def job():
        calls.append(1)
        if len(calls) >= 3:
            reached.set()
        raise RuntimeError("boom")

    scheduler = IngestScheduler(job, interval_seconds=0.01)
    scheduler.start()
    assert reached.wait(5)
    scheduler.stop()


def test_stop_returns_promptly_even_during_a_long_wait():
    started = threading.Event()
    scheduler = IngestScheduler(started.set, interval_seconds=3600)
    scheduler.start()
    assert started.wait(5)
    began = datetime.now()
    scheduler.stop()
    assert (datetime.now() - began).total_seconds() < 2
    assert not scheduler._thread.is_alive()


def test_the_job_stops_running_after_stop():
    calls = []
    scheduler = IngestScheduler(lambda: calls.append(1), interval_seconds=0.01)
    scheduler.start()
    scheduler.stop()
    seen = len(calls)
    threading.Event().wait(0.1)
    assert len(calls) == seen


@pytest.mark.parametrize("interval", [0, -5])
def test_a_non_positive_interval_is_rejected(interval):
    with pytest.raises(ValueError):
        IngestScheduler(lambda: None, interval_seconds=interval)


# ---- catching up after downtime ---------------------------------------------


@pytest.mark.parametrize(
    "hours_ago, expected, flagged",
    [
        (0.5, 2, False),  # normal: the default window
        (30, 3, False),  # 1.25 days: round up to 2, plus a day of overlap
        (3 * 24, 4, False),  # exactly 3 days: 3 plus a day of overlap
        (3 * 24 + 1, 5, False),  # a little over 3 days: 4 plus a day of overlap, the most FIRMS allows
        (4 * 24 + 5, 5, True),  # needs 6 days, but FIRMS gives 5: capped and flagged
        (10 * 24, 5, True),
    ],
)
def test_catchup_window(hours_ago, expected, flagged):
    days, too_old = catchup_day_range([NOW - timedelta(hours=hours_ago)], NOW, default=2)
    assert (days, too_old) == (expected, flagged)


def test_catchup_uses_the_oldest_last_success():
    days, _ = catchup_day_range([NOW - timedelta(hours=1), NOW - timedelta(days=3)], NOW, default=2)
    assert days == 4


def test_catchup_with_no_history_uses_the_default():
    assert catchup_day_range([], NOW, default=2) == (2, False)
    assert catchup_day_range([None, None], NOW, default=2) == (2, False)


def test_catchup_never_goes_below_the_default():
    assert catchup_day_range([NOW], NOW, default=4)[0] == 4


# ---- the ingest job ---------------------------------------------------------

KEY = "SECRETKEY123"
CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,"
    "version,bright_ti5,frp,daynight\n"
    "38.1,27.4,331.2,0.4,0.3,2026-07-10,1030,N,VIIRS,n,2.0NRT,295.1,4.2,D\n"
)


@respx.mock
def test_the_job_asks_for_a_wider_window_after_downtime(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 's.db'}")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        when = datetime.now(UTC) - timedelta(days=3)
        s.add(IngestRun(source="VIIRS_SNPP_NRT", started_at=when, finished_at=when, status="success", record_count=1))
        s.commit()

    settings = Settings(_env_file=None, firms_map_key=KEY, firms_sources="VIIRS_SNPP_NRT")
    route = respx.route(host="firms.modaps.eosdis.nasa.gov").respond(200, text=CSV)
    with httpx.Client() as client:
        make_ingest_job(factory, settings, client)()

    assert route.call_count == 1
    assert route.calls[0].request.url.path.endswith("/5")  # 3 days of downtime -> ask for 5 days


@respx.mock
def test_the_job_uses_the_default_window_when_up_to_date(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 's.db'}")
    init_db(engine)
    factory = make_session_factory(engine)
    settings = Settings(_env_file=None, firms_map_key=KEY, firms_sources="VIIRS_SNPP_NRT")
    route = respx.route(host="firms.modaps.eosdis.nasa.gov").respond(200, text=CSV)
    with httpx.Client() as client:
        make_ingest_job(factory, settings, client)()
    assert route.calls[0].request.url.path.endswith("/2")
