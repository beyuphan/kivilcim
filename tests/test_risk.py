import pytest

from kivilcim.risk import INDEX_VERSION, WEIGHTS, compute_risk, level_for


def test_dryness_weights_add_up_to_less_than_one_because_temperature_is_a_multiplier():
    assert set(WEIGHTS) == {"humidity", "wind", "rain"}
    assert sum(WEIGHTS.values()) == pytest.approx(0.8)


def test_worst_conditions_give_the_maximum_score():
    result = compute_risk(temperature_c=40, humidity_pct=15, wind_kmh=50, precip_72h_mm=0)
    assert result.score == 100
    assert result.level == "extreme"
    assert result.index_version == INDEX_VERSION


def test_calm_cool_wet_conditions_give_the_minimum_score():
    result = compute_risk(temperature_c=10, humidity_pct=70, wind_kmh=0, precip_72h_mm=10)
    assert result.score == 0
    assert result.level == "low"
    assert result.drivers == []


def test_known_mixed_example():
    # dryness = (0.35*30/55 + 0.25*0.5 + 0.20*1) / 0.80 = 0.6449; temperature multiplier = 20/25 = 0.8
    result = compute_risk(temperature_c=25, humidity_pct=40, wind_kmh=25, precip_72h_mm=0)
    assert result.score == 51.6
    assert result.level == "high"


def test_hot_dry_windy_spell_is_flagged_with_its_drivers():
    result = compute_risk(temperature_c=33.5, humidity_pct=22, wind_kmh=28.4, precip_72h_mm=0)
    assert result.score == 80.9
    assert result.level == "extreme"
    # Strongest dryness factors first, then temperature; wind is only 0.57 so it is not a driver.
    assert result.drivers == ["humidity", "rain", "temperature"]


def test_cold_dry_windy_day_is_not_high():
    # The v1 mistake: it called conditions like these "high" in the cold season.
    result = compute_risk(temperature_c=8, humidity_pct=20, wind_kmh=40, precip_72h_mm=0)
    assert result.level == "low"
    assert "temperature" not in result.drivers


def test_cool_autumn_day_with_dry_spell_stays_moderate_at_most():
    # Real readings for Kayseri on 2 Nov 2026 (v1 scored this 48.9, "high").
    result = compute_risk(temperature_c=15.9, humidity_pct=40, wind_kmh=11.8, precip_72h_mm=0)
    assert result.level == "moderate"
    assert result.score == pytest.approx(24.5, abs=0.1)


def test_hot_but_wet_and_humid_is_low():
    result = compute_risk(temperature_c=35, humidity_pct=75, wind_kmh=5, precip_72h_mm=12)
    assert result.level == "low"
    assert result.drivers == []  # the air is too damp for heat alone to count as a driver


def test_temperature_alone_cannot_raise_the_level_much():
    humid = compute_risk(temperature_c=40, humidity_pct=70, wind_kmh=0, precip_72h_mm=10)
    assert humid.score == 0


def test_rain_lowers_the_score():
    dry = compute_risk(30, 30, 20, 0)
    wet = compute_risk(30, 30, 20, 8)
    assert wet.score < dry.score
    assert "rain" in dry.drivers
    assert "rain" not in wet.drivers


def test_contributions_add_up_to_the_score():
    for args in [(28, 35, 22, 1.5), (33.5, 22, 28.4, 0), (15.9, 40, 11.8, 0), (40, 15, 50, 0)]:
        result = compute_risk(*args)
        assert sum(result.contributions.values()) == pytest.approx(result.score, abs=0.2)


def test_zero_score_has_zero_contributions():
    result = compute_risk(temperature_c=3, humidity_pct=20, wind_kmh=40, precip_72h_mm=0)
    assert result.score == 0
    assert all(value == 0 for value in result.contributions.values())


@pytest.mark.parametrize(
    "score, level",
    [(0, "low"), (19.9, "low"), (20, "moderate"), (39.9, "moderate"), (40, "high"),
     (59.9, "high"), (60, "very_high"), (79.9, "very_high"), (80, "extreme"), (100, "extreme")],
)
def test_level_boundaries(score, level):
    assert level_for(score) == level


@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature_c": 60},
        {"temperature_c": -30},
        {"humidity_pct": 0},
        {"humidity_pct": 120},
        {"wind_kmh": 200},
        {"precip_72h_mm": 500},
    ],
)
def test_extreme_values_are_clamped_and_stay_in_range(kwargs):
    base = dict(temperature_c=25, humidity_pct=40, wind_kmh=20, precip_72h_mm=2)
    result = compute_risk(**{**base, **kwargs})
    assert 0 <= result.score <= 100


def sweep(name, values):
    base = dict(temperature_c=25, humidity_pct=40, wind_kmh=20, precip_72h_mm=2)
    return [compute_risk(**{**base, name: v}).score for v in values]


def test_a_worse_input_never_lowers_the_score():
    hotter = sweep("temperature_c", range(-10, 56))
    windier = sweep("wind_kmh", range(0, 121))
    drier_air = sweep("humidity_pct", range(100, -1, -1))
    drier_spell = sweep("precip_72h_mm", [x / 2 for x in range(60, -1, -1)])
    for series in (hotter, windier, drier_air, drier_spell):
        assert series == sorted(series)
