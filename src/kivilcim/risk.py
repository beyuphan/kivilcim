"""Fire danger index v2: a transparent, rule-based score from current weather.

This is experimental. The weights and ramps below are a reasoned starting point, not a
calibrated model; Stage 4 of the plan checks them against past fires. It describes how
favorable the weather is for fires, not whether a fire will happen.

Structure: dryness (humidity, wind and a dry spell) is a weighted average from 0 to 1, and
temperature is a multiplier. Cold weather therefore keeps the score low even when the air
is dry and windy, which v1 got wrong (it simply added temperature to the sum).
"""

from dataclasses import dataclass

INDEX_VERSION = "v2"

# How much each dryness factor counts in the weighted average (only their ratios matter).
WEIGHTS = {"humidity": 0.35, "wind": 0.25, "rain": 0.20}

# Each factor is scaled to 0..1 with a linear ramp between two values (0 at `start`, 1 at `end`).
RAMPS = {
    "temperature": (5.0, 30.0),  # degrees C: 0 at or below 5, 1 at or above 30; the multiplier
    "humidity": (70.0, 15.0),  # relative humidity %: danger grows as the air gets drier
    "wind": (0.0, 50.0),  # km/h
    "rain": (10.0, 0.0),  # mm over the last 72 h: danger grows as it stays dry
}

# A factor counts as a driver of the level when its scaled value reaches this.
DRIVER_THRESHOLD = 0.6
# Temperature is only named as a driver when the air is also dry enough to matter.
MIN_DRYNESS_FOR_TEMPERATURE_DRIVER = 0.4

# Upper bounds (exclusive) of each level on the 0-100 score.
LEVELS = [(20, "low"), (40, "moderate"), (60, "high"), (80, "very_high")]
TOP_LEVEL = "extreme"


@dataclass(frozen=True)
class RiskResult:
    score: float
    level: str
    drivers: list[str]  # factors that push the level up, strongest first
    contributions: dict[str, float]  # points each dryness factor accounts for; they add up to the score
    index_version: str = INDEX_VERSION


def _ramp(value: float, start: float, end: float) -> float:
    scaled = (value - start) / (end - start)
    return max(0.0, min(1.0, scaled))


def level_for(score: float) -> str:
    for upper, name in LEVELS:
        if score < upper:
            return name
    return TOP_LEVEL


def compute_risk(
    temperature_c: float, humidity_pct: float, wind_kmh: float, precip_72h_mm: float
) -> RiskResult:
    values = {
        "temperature": temperature_c,
        "humidity": humidity_pct,
        "wind": wind_kmh,
        "rain": precip_72h_mm,
    }
    scaled = {name: _ramp(value, *RAMPS[name]) for name, value in values.items()}

    weighted = {name: WEIGHTS[name] * scaled[name] for name in WEIGHTS}
    dryness = sum(weighted.values()) / sum(WEIGHTS.values())
    multiplier = scaled["temperature"]
    score = round(100 * dryness * multiplier, 1)

    # Split the score between the dryness factors in proportion to their weighted share.
    total = sum(weighted.values())
    contributions = {name: round(score * weighted[name] / total, 1) if total else 0.0 for name in WEIGHTS}

    drivers = sorted(
        (name for name in WEIGHTS if scaled[name] >= DRIVER_THRESHOLD),
        key=lambda name: contributions[name],
        reverse=True,
    )
    if multiplier >= DRIVER_THRESHOLD and dryness >= MIN_DRYNESS_FOR_TEMPERATURE_DRIVER:
        drivers.append("temperature")
    return RiskResult(score=score, level=level_for(score), drivers=drivers, contributions=contributions)
