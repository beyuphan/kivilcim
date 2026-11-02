import pytest

from kivilcim.risk import INDEX_VERSION, WEIGHTS, compute_risk, level_for


def test_weights_add_up_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


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
    # temperature 0.5, humidity 30/55, wind 0.5, rain 1.0 -> 10 + 19.09 + 12.5 + 20
    result = compute_risk(temperature_c=25, humidity_pct=40, wind_kmh=25, precip_72h_mm=0)
    assert result.score == 61.6
    assert result.level == "very_high"


def test_hot_dry_windy_spell_is_flagged_with_its_drivers():
    result = compute_risk(temperature_c=33.5, humidity_pct=22, wind_kmh=28.4, precip_72h_mm=0)
    assert result.score == 80.4
    assert result.level == "extreme"
    assert result.drivers == ["humidity", "rain", "temperature"]  # strongest first; wind is only 0.57


def test_rain_lowers_the_score():
    dry = compute_risk(30, 30, 20, 0)
    wet = compute_risk(30, 30, 20, 8)
    assert wet.score < dry.score
    assert "rain" in dry.drivers
    assert "rain" not in wet.drivers


def test_contributions_add_up_to_the_score():
    result = compute_risk(28, 35, 22, 1.5)
    assert sum(result.contributions.values()) == pytest.approx(result.score, abs=0.2)


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
