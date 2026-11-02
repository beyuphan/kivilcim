"""Turkey province and district center points for the location picker.

The source is a CSV of district boundaries exported from OpenStreetMap data
(semicolon separated, decimal comma, UTF-8 with BOM). This module validates it and
writes a compact JSON that the web page loads.

Usage: python -m kivilcim.districts data/turkiye-ilce-sinirlari_tm36.csv src/kivilcim/static/ilceler.json
"""

import csv
import json
import sys
from pathlib import Path

ATTRIBUTION = "© OpenStreetMap katkıcıları, ODbL 1.0"
# Loose box around Turkey, used only to catch wrong columns or a wrong coordinate system.
LAT_RANGE = (35.5, 42.5)
LON_RANGE = (25.5, 45.5)
EXPECTED_PROVINCES = 81


def _number(text: str) -> float:
    return float(text.strip().replace(",", "."))


def load_provinces(csv_path: Path) -> list[dict]:
    """Read the CSV into provinces with their districts. Raises ValueError on bad data."""
    provinces: dict[str, dict] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        for line_no, row in enumerate(csv.DictReader(handle, delimiter=";"), start=2):
            try:
                code, province, district = row["il_kodu"].strip(), row["il_adi"].strip(), row["ilce_adi"].strip()
                lat, lon = _number(row["merkez_enlem"]), _number(row["merkez_boylam"])
                area = _number(row["alan_km2"])
            except (KeyError, ValueError, AttributeError) as exc:
                raise ValueError(f"line {line_no}: unreadable row ({exc})") from exc
            if not (code and province and district):
                raise ValueError(f"line {line_no}: missing province or district name")
            if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
                raise ValueError(f"line {line_no}: {province}/{district} is outside Turkey ({lat}, {lon})")
            if area <= 0:
                raise ValueError(f"line {line_no}: {province}/{district} has a non-positive area")

            entry = provinces.setdefault(code, {"code": code, "name": province, "districts": []})
            if entry["name"] != province:
                raise ValueError(f"line {line_no}: province code {code} has two names")
            if any(d["name"] == district for d in entry["districts"]):
                raise ValueError(f"line {line_no}: duplicate district {province}/{district}")
            entry["districts"].append({"name": district, "lat": lat, "lon": lon, "area": area})

    if len(provinces) != EXPECTED_PROVINCES:
        raise ValueError(f"expected {EXPECTED_PROVINCES} provinces, found {len(provinces)}")
    return [provinces[code] for code in sorted(provinces)]


def build_payload(provinces: list[dict]) -> dict:
    """Compact JSON. A province center is the area-weighted mean of its district centers."""
    out = []
    for province in provinces:
        total = sum(d["area"] for d in province["districts"])
        lat = sum(d["lat"] * d["area"] for d in province["districts"]) / total
        lon = sum(d["lon"] * d["area"] for d in province["districts"]) / total
        out.append(
            {
                "code": province["code"],
                "name": province["name"],
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "districts": [
                    {"name": d["name"], "lat": round(d["lat"], 4), "lon": round(d["lon"], 4)}
                    for d in sorted(province["districts"], key=lambda d: d["name"])
                ],
            }
        )
    return {
        "attribution": ATTRIBUTION,
        "note": "Approximate center points, not official boundaries.",
        "provinces": out,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    payload = build_payload(load_provinces(Path(argv[1])))
    Path(argv[2]).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    districts = sum(len(p["districts"]) for p in payload["provinces"])
    print(f"{len(payload['provinces'])} provinces, {districts} districts -> {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
