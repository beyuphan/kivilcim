from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.firms import FirmsError
from kivilcim.ingest import backfill, ingest_source, main, run_ingest
from kivilcim.models import FireDetection, IngestRun

UTC = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures"
KEY = "SECRETKEY123"
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

VIIRS_URL = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_SNPP_NRT/26.0,35.8,45.0,42.2/2"
MODIS_URL = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/MODIS_NRT/26.0,35.8,45.0,42.2/2"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


def counts(session_factory):
    with session_factory() as s:
        return s.query(FireDetection).count(), s.query(IngestRun).count()


@respx.mock
def test_successful_ingest_stores_detections_and_records_run(session_factory, client):
    respx.get(VIIRS_URL).respond(200, text=load("viirs_snpp.csv"))
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    assert run.status == "success"
    assert run.record_count == 3
    assert run.error is None
    assert run.finished_at is not None
    assert counts(session_factory) == (3, 1)


@respx.mock
def test_running_twice_does_not_duplicate(session_factory, client):
    respx.get(VIIRS_URL).respond(200, text=load("viirs_snpp.csv"))
    first = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)
    second = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    assert first.record_count == 3
    assert second.record_count == 0
    assert second.status == "success"
    assert counts(session_factory) == (3, 2)


@respx.mock
def test_overlapping_window_only_adds_new_rows(session_factory, client):
    lines = load("viirs_snpp.csv").splitlines()
    route = respx.get(VIIRS_URL)
    route.respond(200, text="\n".join(lines[:3]))  # header + 2 rows
    ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    route.respond(200, text="\n".join(lines))  # same 2 rows + 1 new
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    assert run.record_count == 1
    assert counts(session_factory)[0] == 3


@respx.mock
def test_empty_response_is_success_with_zero_records(session_factory, client):
    respx.get(VIIRS_URL).respond(200, text="")
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)
    assert run.status == "success"
    assert run.record_count == 0


@respx.mock
def test_malformed_rows_are_skipped_and_noted(session_factory, client):
    header = load("viirs_snpp.csv").splitlines()[0]
    body = (
        header
        + "\n38.1,27.4,331.2,0.4,0.3,2026-07-01,1030,N,VIIRS,n,2.0NRT,295.1,4.2,D"
        + "\nnotanumber,27.4,331.2,0.4,0.3,2026-07-01,1030,N,VIIRS,n,2.0NRT,295.1,4.2,D\n"
    )
    respx.get(VIIRS_URL).respond(200, text=body)
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    assert run.status == "success"
    assert run.record_count == 1
    assert run.error == "1 malformed rows skipped"


@respx.mock
def test_firms_error_text_marks_run_failed_and_keeps_old_data(session_factory, client):
    route = respx.get(VIIRS_URL)
    route.respond(200, text=load("viirs_snpp.csv"))
    ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    route.respond(200, text="Exceeding allowed transaction limit")
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    assert run.status == "failed"
    assert "FirmsError" in run.error
    assert run.record_count == 0
    assert counts(session_factory) == (3, 2)


@respx.mock
def test_http_error_marks_run_failed(session_factory, client):
    respx.get(VIIRS_URL).respond(500, text="boom")
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)
    assert run.status == "failed"
    assert "HTTP 500" in run.error
    assert counts(session_factory) == (0, 1)


@respx.mock
def test_network_error_marks_run_failed_without_leaking_key(session_factory, client):
    respx.get(VIIRS_URL).mock(side_effect=httpx.ConnectError(f"cannot connect to {VIIRS_URL}"))
    run = ingest_source(session_factory, client, KEY, "VIIRS_SNPP_NRT", 2, now=NOW)

    assert run.status == "failed"
    assert KEY not in run.error
    assert "<KEY>" in run.error


@respx.mock
def test_one_failing_source_does_not_stop_the_others(session_factory, client):
    respx.get(VIIRS_URL).respond(500, text="boom")
    respx.get(MODIS_URL).respond(200, text=load("modis.csv"))
    settings = Settings(_env_file=None, firms_map_key=KEY, firms_sources="VIIRS_SNPP_NRT,MODIS_NRT")

    runs = run_ingest(session_factory, client, settings, now=NOW)

    assert [(r.source, r.status) for r in runs] == [
        ("VIIRS_SNPP_NRT", "failed"),
        ("MODIS_NRT", "success"),
    ]
    assert counts(session_factory) == (3, 2)


BACKFILL_BASE = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{KEY}/VIIRS_SNPP_SP/26.0,35.8,45.0,42.2"


@respx.mock
def test_backfill_splits_the_range_into_five_day_windows(session_factory, client):
    windows = [
        respx.get(f"{BACKFILL_BASE}/5/2026-06-01").respond(200, text=load("viirs_snpp.csv")),
        respx.get(f"{BACKFILL_BASE}/5/2026-06-06").respond(200, text=""),
        respx.get(f"{BACKFILL_BASE}/2/2026-06-11").respond(200, text=""),
    ]
    seen = []
    added = backfill(
        session_factory, client, KEY, "VIIRS_SNPP_SP", date(2026, 6, 1), date(2026, 6, 12), now=NOW,
        on_window=lambda *args: seen.append(args),
    )

    assert added == 3
    assert [w.call_count for w in windows] == [1, 1, 1]
    assert [(s[0], s[1]) for s in seen] == [(date(2026, 6, 1), 5), (date(2026, 6, 6), 5), (date(2026, 6, 11), 2)]
    assert seen[0][2:] == (3, 3, 0)  # fetched, new, skipped


@respx.mock
def test_backfill_twice_adds_nothing_the_second_time(session_factory, client):
    respx.get(f"{BACKFILL_BASE}/3/2026-06-01").respond(200, text=load("viirs_snpp.csv"))
    args = (session_factory, client, KEY, "VIIRS_SNPP_SP", date(2026, 6, 1), date(2026, 6, 3))
    assert backfill(*args, now=NOW) == 3
    assert backfill(*args, now=NOW) == 0
    assert counts(session_factory)[0] == 3


@respx.mock
def test_backfill_is_not_recorded_in_ingest_runs(session_factory, client):
    # ingest_runs feeds the freshness shown to users; a history load must not touch it.
    respx.get(f"{BACKFILL_BASE}/1/2026-06-01").respond(200, text=load("viirs_snpp.csv"))
    backfill(session_factory, client, KEY, "VIIRS_SNPP_SP", date(2026, 6, 1), date(2026, 6, 1), now=NOW)
    assert counts(session_factory) == (3, 0)


@respx.mock
def test_backfill_stops_on_a_firms_error_and_keeps_earlier_windows(session_factory, client):
    respx.get(f"{BACKFILL_BASE}/5/2026-06-01").respond(200, text=load("viirs_snpp.csv"))
    respx.get(f"{BACKFILL_BASE}/5/2026-06-06").respond(200, text="Exceeding allowed transaction limit")
    with pytest.raises(FirmsError):
        backfill(session_factory, client, KEY, "VIIRS_SNPP_SP", date(2026, 6, 1), date(2026, 6, 10), now=NOW)
    assert counts(session_factory)[0] == 3


@respx.mock
def test_backfill_http_error_raises(session_factory, client):
    respx.get(f"{BACKFILL_BASE}/1/2026-06-01").respond(400, text="bad")
    with pytest.raises(FirmsError, match="HTTP 400"):
        backfill(session_factory, client, KEY, "VIIRS_SNPP_SP", date(2026, 6, 1), date(2026, 6, 1), now=NOW)


def test_backfill_rejects_an_end_before_the_start(session_factory, client):
    with pytest.raises(ValueError, match="before start"):
        backfill(session_factory, client, KEY, "VIIRS_SNPP_SP", date(2026, 6, 5), date(2026, 6, 1))


@pytest.mark.parametrize(
    "argv",
    [["--source", "VIIRS_SNPP_SP"], ["--from", "2026-06-01", "--to", "2026-06-05"], ["--from", "not-a-date"]],
)
def test_main_requires_all_backfill_options_together(argv):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_run_ingest_requires_key(session_factory, client):
    settings = Settings(_env_file=None, firms_map_key="")
    with pytest.raises(ValueError, match="FIRMS_MAP_KEY"):
        run_ingest(session_factory, client, settings)
