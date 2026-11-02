from datetime import datetime, timezone
from pathlib import Path

import pytest

from kivilcim.firms import FirmsError, confidence_level, parse_acquired_at, parse_csv

UTC = timezone.utc
FIXTURES = Path(__file__).parent / "fixtures"
INGESTED_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

VIIRS_HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight\n"
)


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_viirs_fixture():
    result = parse_csv(load("viirs_snpp.csv"), "VIIRS_SNPP_NRT", INGESTED_AT)
    assert result.skipped == []
    first, second, third = result.detections
    assert first.source == "VIIRS_SNPP_NRT"
    assert first.satellite == "N"
    assert first.instrument == "VIIRS"
    assert first.latitude == 38.12345
    assert first.longitude == 27.45678
    assert first.acquired_at == datetime(2026, 7, 1, 10, 30, tzinfo=UTC)
    assert first.confidence_raw == "n"
    assert first.confidence_level == "nominal"
    assert first.brightness == 331.2
    assert first.frp == 4.2
    assert first.ingested_at == INGESTED_AT
    assert second.confidence_level == "high"
    assert third.confidence_level == "low"


def test_acq_time_without_leading_zeros():
    result = parse_csv(load("viirs_snpp.csv"), "VIIRS_SNPP_NRT", INGESTED_AT)
    assert result.detections[1].acquired_at == datetime(2026, 7, 1, 2, 24, tzinfo=UTC)


def test_parse_modis_fixture_uses_numeric_confidence():
    result = parse_csv(load("modis.csv"), "MODIS_NRT", INGESTED_AT)
    assert result.skipped == []
    levels = [d.confidence_level for d in result.detections]
    raws = [d.confidence_raw for d in result.detections]
    assert levels == ["high", "nominal", "low"]
    assert raws == ["85", "55", "12"]
    assert result.detections[0].instrument == "MODIS"
    assert result.detections[0].brightness == 320.5


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("l", "low"),
        ("N", "nominal"),
        ("h", "high"),
        ("0", "low"),
        ("29", "low"),
        ("30", "nominal"),
        ("79", "nominal"),
        ("80", "high"),
        ("100", "high"),
    ],
)
def test_confidence_level(raw, expected):
    assert confidence_level(raw) == expected


@pytest.mark.parametrize("raw", ["", "x", "101", "-1"])
def test_confidence_level_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        confidence_level(raw)


@pytest.mark.parametrize("acq_time", ["", "99", "12345", "ab12"])
def test_parse_acquired_at_rejects_bad_time(acq_time):
    with pytest.raises(ValueError):
        parse_acquired_at("2026-07-01", acq_time)


def test_parse_acquired_at_rejects_impossible_time():
    with pytest.raises(ValueError):
        parse_acquired_at("2026-07-01", "2561")


@pytest.mark.parametrize("body", ["", "   \n", VIIRS_HEADER])
def test_empty_or_header_only_gives_empty_result(body):
    result = parse_csv(body, "VIIRS_SNPP_NRT", INGESTED_AT)
    assert result.detections == []
    assert result.skipped == []


@pytest.mark.parametrize(
    "body",
    ["Invalid MAP_KEY.", "Exceeding allowed transaction limit", "<html>Bad gateway</html>"],
)
def test_error_text_raises(body):
    with pytest.raises(FirmsError):
        parse_csv(body, "VIIRS_SNPP_NRT", INGESTED_AT)


def test_bad_rows_are_skipped_not_fatal():
    body = (
        VIIRS_HEADER
        + "38.1,27.4,331.2,0.4,0.3,2026-07-01,1030,N,VIIRS,n,2.0NRT,295.1,4.2,D\n"
        + "notanumber,27.4,331.2,0.4,0.3,2026-07-01,1030,N,VIIRS,n,2.0NRT,295.1,4.2,D\n"
        + "38.2,27.5,331.2,0.4,0.3,2026-13-45,1030,N,VIIRS,n,2.0NRT,295.1,4.2,D\n"
        + "38.3,27.6,331.2,0.4,0.3,2026-07-01,1030,N,VIIRS,zz,2.0NRT,295.1,4.2,D\n"
        + "38.4,27.7,331.2,0.4,0.3,2026-07-01,1030,N,VIIRS,h,2.0NRT,295.1,4.2,D\n"
    )
    result = parse_csv(body, "VIIRS_SNPP_NRT", INGESTED_AT)
    assert [d.latitude for d in result.detections] == [38.1, 38.4]
    assert [line for line, _ in result.skipped] == [3, 4, 5]


def test_missing_optional_numbers_become_none():
    body = VIIRS_HEADER + "38.1,27.4,,0.4,0.3,2026-07-01,1030,N,VIIRS,n,2.0NRT,295.1,,D\n"
    result = parse_csv(body, "VIIRS_SNPP_NRT", INGESTED_AT)
    assert result.skipped == []
    assert result.detections[0].brightness is None
    assert result.detections[0].frp is None
