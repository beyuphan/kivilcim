import json
from pathlib import Path

import pytest

from kivilcim import districts

ROOT = Path(__file__).parent.parent
REAL_CSV = ROOT / "data" / "turkiye-ilce-sinirlari_tm36.csv"
BUILT_JSON = ROOT / "src" / "kivilcim" / "static" / "ilceler.json"

HEADER = "il_kodu;il_adi;ilce_adi;merkez_enlem;merkez_boylam;alan_km2\n"


def write_csv(tmp_path, rows: list[str], header: str = HEADER) -> Path:
    path = tmp_path / "d.csv"
    path.write_text("﻿" + header + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


def load_small(tmp_path, rows):
    # The real province count check is relaxed so small fixtures can be tested.
    path = write_csv(tmp_path, rows)
    original = districts.EXPECTED_PROVINCES
    districts.EXPECTED_PROVINCES = len({r.split(";")[0] for r in rows})
    try:
        return districts.load_provinces(path)
    finally:
        districts.EXPECTED_PROVINCES = original


def test_parses_decimal_comma_semicolons_and_bom(tmp_path):
    provinces = load_small(tmp_path, ["06;Ankara;Çankaya;39,9;32,85;267,5"])
    assert provinces[0]["name"] == "Ankara"
    assert provinces[0]["districts"][0] == {"name": "Çankaya", "lat": 39.9, "lon": 32.85, "area": 267.5}


@pytest.mark.parametrize(
    "row, message",
    [
        ("06;Ankara;X;539,9;32,85;10", "outside Turkey"),
        ("06;Ankara;X;39,9;132,85;10", "outside Turkey"),
        ("06;Ankara;X;abc;32,85;10", "unreadable"),
        ("06;Ankara;X;39,9;32,85;0", "non-positive area"),
        ("06;Ankara;;39,9;32,85;10", "missing"),
    ],
)
def test_rejects_bad_rows(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        load_small(tmp_path, [row])


def test_rejects_duplicate_district(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_small(tmp_path, ["06;Ankara;A;39,9;32,85;10", "06;Ankara;A;39,8;32,8;10"])


def test_rejects_wrong_province_count(tmp_path):
    path = write_csv(tmp_path, ["06;Ankara;A;39,9;32,85;10"])
    with pytest.raises(ValueError, match="expected 81 provinces"):
        districts.load_provinces(path)


def test_province_center_is_area_weighted(tmp_path):
    provinces = load_small(
        tmp_path, ["06;Ankara;Big;40,0;30,0;300", "06;Ankara;Small;38,0;34,0;100"]
    )
    payload = districts.build_payload(provinces)["provinces"][0]
    assert payload["lat"] == 39.5
    assert payload["lon"] == 31.0


def test_real_csv_is_valid_and_complete():
    if not REAL_CSV.exists():
        pytest.skip("source CSV not present")
    provinces = districts.load_provinces(REAL_CSV)
    assert len(provinces) == 81
    assert sum(len(p["districts"]) for p in provinces) == 973


def test_built_json_has_attribution_and_matches_the_source():
    data = json.loads(BUILT_JSON.read_text(encoding="utf-8"))
    assert "OpenStreetMap" in data["attribution"]
    assert "ODbL" in data["attribution"]
    assert len(data["provinces"]) == 81
    names = {p["name"] for p in data["provinces"]}
    assert {"Ankara", "İstanbul", "İzmir", "Hatay", "Van"} <= names
    istanbul = next(p for p in data["provinces"] if p["code"] == "34")
    assert 40.5 < istanbul["lat"] < 41.7 and 27.9 < istanbul["lon"] < 30.0
    assert len(istanbul["districts"]) == 39
