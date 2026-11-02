from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from kivilcim.config import Settings
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.ingest import ingest_source, run_ingest
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
    settings = Settings(_env_file=None, firms_map_key=KEY)

    runs = run_ingest(session_factory, client, settings, now=NOW)

    assert [(r.source, r.status) for r in runs] == [
        ("VIIRS_SNPP_NRT", "failed"),
        ("MODIS_NRT", "success"),
    ]
    assert counts(session_factory) == (3, 2)


def test_run_ingest_requires_key(session_factory, client):
    settings = Settings(_env_file=None, firms_map_key="")
    with pytest.raises(ValueError, match="FIRMS_MAP_KEY"):
        run_ingest(session_factory, client, settings)
