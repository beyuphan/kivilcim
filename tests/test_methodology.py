"""The methodology page states numbers that also live in the code. These tests keep them in step."""

import pytest
from fastapi.testclient import TestClient

from kivilcim.api import FRESH_HOURS, WARNING_HOURS, create_app
from kivilcim.db import init_db, make_engine, make_session_factory
from kivilcim.events import ATTACH_DAYS_AFTER, ATTACH_DAYS_BEFORE, MAX_DAYS, MIN_DETECTIONS, MIN_MAX_FRP, MIN_TOTAL_FRP
from kivilcim.fixed import MIN_HISTORY_DAYS, RECURRING_SHARE
from kivilcim.risk import INDEX_VERSION, LEVELS, RAMPS, WEIGHTS
from kivilcim.firms import confidence_level


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    engine = make_engine(f"sqlite:///{tmp_path_factory.mktemp('m') / 'm.db'}")
    init_db(engine)
    client = TestClient(create_app(make_session_factory(engine)))
    response = client.get("/methodology")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return " ".join(response.text.split())  # line breaks in the HTML must not matter


def comma(x: float) -> str:
    """Turkish decimal comma, no trailing zeros."""
    return f"{x:g}".replace(".", ",")


def test_it_carries_the_disclaimer_and_the_emergency_numbers(page):
    assert "resmi bir uyarı sistemi değildir" in page.lower()
    assert "112" in page and "AFAD" in page


def test_the_index_version_and_weights_match_the_code(page):
    assert f"<code>{INDEX_VERSION}</code>" in page
    assert comma(WEIGHTS["humidity"]) in page
    assert comma(WEIGHTS["wind"]) in page
    assert comma(WEIGHTS["rain"]) in page


def test_the_ramps_match_the_code(page):
    hum_start, hum_end = RAMPS["humidity"]
    assert f"%{hum_start:g}'te 0, %{hum_end:g}'te 1" in page
    wind_start, wind_end = RAMPS["wind"]
    assert f"{wind_start:g} km/sa'te 0, {wind_end:g} km/sa'te 1" in page
    temp_start, temp_end = RAMPS["temperature"]
    assert f"{temp_start:g} °C ve altında 0, {temp_end:g} °C ve üstünde 1" in page
    rain_start, _ = RAMPS["rain"]
    assert f"{rain_start:g} mm yağışta 0" in page


def test_the_level_thresholds_match_the_code(page):
    names = {"low": "düşük", "moderate": "orta", "high": "yüksek", "very_high": "çok yüksek"}
    for upper, level in LEVELS:
        assert f"{upper}" in page and names[level] in page
    assert "üstü aşırı" in page


def test_the_freshness_thresholds_match_the_code(page):
    assert f"{FRESH_HOURS} saatin altı güncel" in page
    assert f"{FRESH_HOURS}-{WARNING_HOURS} saat gecikmiş" in page
    assert f"{WARNING_HOURS} saatten fazlası eski" in page


def test_the_event_thresholds_match_the_code(page):
    assert f"en az {MIN_MAX_FRP:g} MW" in page
    assert f"en az {MIN_DETECTIONS} tespit" in page
    assert f"toplam {MIN_TOTAL_FRP:g} MW" in page
    assert f"en fazla {MAX_DAYS} gün" in page
    assert f"{ATTACH_DAYS_BEFORE} gün öncesinden {ATTACH_DAYS_AFTER} gün sonrasına" in page


def test_the_fixed_source_rule_matches_the_code(page):
    assert f"%{RECURRING_SHARE * 100:g}" in page
    assert f"en az {MIN_HISTORY_DAYS} günlük geçmiş" in page


def test_the_modis_confidence_bands_match_the_code(page):
    # 0-29 low, 30-79 nominal, 80-100 high
    assert (confidence_level("29"), confidence_level("30"), confidence_level("79"), confidence_level("80")) == (
        "low", "nominal", "nominal", "high",
    )
    assert "0-29 düşük, 30-79 orta, 80-100 yüksek" in page


def test_it_names_the_data_sources_and_their_licenses(page):
    for text in ("NASA FIRMS", "Open-Meteo", "CC BY 4.0", "OpenStreetMap", "ODbL", "MIT"):
        assert text in page


def test_it_says_what_the_site_does_not_do(page):
    assert "yangın olasılığı" in page and "güvenli olduğunu söylemez" in page
    assert "ev yangını" in page  # small fires are not seen
    assert "Gizlilik" in page and "çerez" in page


def test_the_validation_section_reads_its_results_from_a_file(page):
    assert "/static/validation.json" in page
