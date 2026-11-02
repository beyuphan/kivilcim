"""Parsing and normalization of NASA FIRMS area CSV responses."""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

from kivilcim.models import FireDetection

REQUIRED_COLUMNS = {
    "latitude",
    "longitude",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
}


class FirmsError(Exception):
    """The response is not a valid FIRMS CSV (for example an error message or a bad key)."""


@dataclass
class ParseResult:
    detections: list[FireDetection] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)  # (line number, reason)


def confidence_level(raw: str) -> str:
    """Normalize FIRMS confidence to low / nominal / high.

    VIIRS gives a category (l / n / h). MODIS gives a number from 0 to 100,
    classified like NASA does: 0-29 low, 30-79 nominal, 80-100 high.
    """
    value = raw.strip().lower()
    categories = {"l": "low", "n": "nominal", "h": "high"}
    if value in categories:
        return categories[value]
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"unknown confidence value: {raw!r}") from None
    if not 0 <= number <= 100:
        raise ValueError(f"confidence out of range: {raw!r}")
    if number < 30:
        return "low"
    if number < 80:
        return "nominal"
    return "high"


def parse_acquired_at(acq_date: str, acq_time: str) -> datetime:
    """Combine FIRMS acq_date (YYYY-MM-DD) and acq_time (HHMM, UTC, leading zeros may be missing)."""
    digits = acq_time.strip()
    if not digits.isdigit() or len(digits) > 4:
        raise ValueError(f"invalid acq_time: {acq_time!r}")
    digits = digits.zfill(4)
    hour, minute = int(digits[:2]), int(digits[2:])
    day = datetime.strptime(acq_date.strip(), "%Y-%m-%d")
    return day.replace(hour=hour, minute=minute, tzinfo=timezone.utc)


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def parse_csv(text: str, source: str, ingested_at: datetime) -> ParseResult:
    """Parse a FIRMS area CSV into FireDetection rows.

    Bad rows are skipped and reported in `skipped`; they never abort the whole batch.
    An empty body or a header-only body gives an empty result. A body that is not
    a FIRMS CSV at all (such as an error message) raises FirmsError.
    """
    result = ParseResult()
    if not text.strip():
        return result

    reader = csv.DictReader(io.StringIO(text.strip()))
    columns = set(reader.fieldnames or [])
    if not REQUIRED_COLUMNS <= columns:
        snippet = text.strip().splitlines()[0][:200]
        raise FirmsError(f"unexpected FIRMS response: {snippet}")

    # VIIRS and MODIS name the brightness column differently.
    brightness_column = "bright_ti4" if "bright_ti4" in columns else "brightness"

    for row in reader:
        line = reader.line_num
        try:
            result.detections.append(
                FireDetection(
                    source=source,
                    satellite=row["satellite"].strip(),
                    instrument=row["instrument"].strip(),
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    acquired_at=parse_acquired_at(row["acq_date"], row["acq_time"]),
                    confidence_raw=row["confidence"].strip(),
                    confidence_level=confidence_level(row["confidence"]),
                    brightness=_optional_float(row.get(brightness_column)),
                    frp=_optional_float(row.get("frp")),
                    ingested_at=ingested_at,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            result.skipped.append((line, str(exc)))
    return result
