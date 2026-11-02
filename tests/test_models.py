from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.models import FireDetection, IngestRun

UTC = timezone.utc


@pytest.fixture
def session():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def make_detection(**overrides) -> FireDetection:
    values = dict(
        source="VIIRS_SNPP_NRT",
        satellite="N",
        instrument="VIIRS",
        latitude=38.123,
        longitude=27.456,
        acquired_at=datetime(2026, 7, 1, 10, 30, tzinfo=UTC),
        confidence_raw="n",
        confidence_level="nominal",
        brightness=330.5,
        frp=4.2,
        ingested_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )
    values.update(overrides)
    return FireDetection(**values)


def test_detection_roundtrip_returns_aware_utc(session):
    session.add(make_detection())
    session.commit()
    saved = session.query(FireDetection).one()
    assert saved.acquired_at == datetime(2026, 7, 1, 10, 30, tzinfo=UTC)
    assert saved.acquired_at.tzinfo is not None


def test_non_utc_timezone_is_converted_to_utc(session):
    istanbul = timezone(timedelta(hours=3))
    session.add(make_detection(acquired_at=datetime(2026, 7, 1, 13, 30, tzinfo=istanbul)))
    session.commit()
    saved = session.query(FireDetection).one()
    assert saved.acquired_at == datetime(2026, 7, 1, 10, 30, tzinfo=UTC)


def test_naive_datetime_is_rejected(session):
    session.add(make_detection(acquired_at=datetime(2026, 7, 1, 10, 30)))
    with pytest.raises(Exception, match="naive datetime"):
        session.commit()


def test_duplicate_detection_is_rejected(session):
    session.add(make_detection())
    session.commit()
    session.add(make_detection())
    with pytest.raises(IntegrityError):
        session.commit()


def test_same_point_from_different_source_is_allowed(session):
    session.add(make_detection())
    session.add(make_detection(source="MODIS_NRT", instrument="MODIS", satellite="Terra"))
    session.commit()
    assert session.query(FireDetection).count() == 2


def test_ingest_run_defaults(session):
    session.add(
        IngestRun(
            source="VIIRS_SNPP_NRT",
            started_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
            status="running",
        )
    )
    session.commit()
    run = session.query(IngestRun).one()
    assert run.finished_at is None
    assert run.record_count == 0
    assert run.error is None
