"""Fire danger index v1: a transparent, rule-based score from current weather.

This is experimental. The weights and ramps below are a reasoned starting point, not a
calibrated model; Stage 4 of the plan checks them against past fires. It describes how
favorable the weather is for fires, not whether a fire will happen.
"""

from dataclasses import dataclass

INDEX_VERSION = "v1"

# How much each factor contributes to the 0-100 score. They add up to 1.
WEIGHTS = {"humidity": 0.35, "wind": 0.25, "temperature": 0.20, "rain": 0.20}

# Each factor is scaled to 0..1 with a linear ramp between two values (0 at `start`, 1 at `end`).
RAMPS = {
    "temperature": (10.0, 40.0),  # degrees C: danger grows as it gets hotter
    "humidity": (70.0, 15.0),  # relative humidity %: danger grows as the air gets drier
    "wind": (0.0, 50.0),  # km/h
    "rain": (10.0, 0.0),  # mm over the last 72 h: danger grows as it stays dry
}

# A factor counts as a driver of the level when its scaled value reaches this.
DRIVER_THRESHOLD = 0.6

# Upper bounds (exclusive) of each level on the 0-100 score.
LEVELS = [(20, "low"), (40, "moderate"), (60, "high"), (80, "very_high")]
TOP_LEVEL = "extreme"


@dataclass(frozen=True)
class RiskResult:
    score: float
    level: str
    drivers: list[str]  # factors that push the level up, strongest first
    contributions: dict[str, float]  # points each factor adds to the score
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
    contributions = {name: round(100 * WEIGHTS[name] * scaled[name], 1) for name in WEIGHTS}
    score = round(sum(100 * WEIGHTS[name] * scaled[name] for name in WEIGHTS), 1)
    drivers = sorted(
        (name for name in scaled if scaled[name] >= DRIVER_THRESHOLD),
        key=lambda name: contributions[name],
        reverse=True,
    )
    return RiskResult(score=score, level=level_for(score), drivers=drivers, contributions=contributions)
