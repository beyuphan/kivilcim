"""The spread view counts the satellite's 375 m pixels in which fire was seen. That arithmetic is checked with Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).parent.parent / "src" / "kivilcim" / "static" / "js" / "spread.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

SCRIPT = """
const { buildFootprint } = await import(process.argv[1]);
const det = (lat, lon, day, frp) => ({ latitude: lat, longitude: lon, acquired_at: `2021-08-0${day}T10:00:00Z`, frp });
const detail = {
  event: { start: "2021-08-01", end: "2021-08-03", center_lat: 37.0, min_lat: 36.9, max_lat: 37.1, min_lon: 31.0, max_lon: 31.2 },
  detections: [
    det(37.0, 31.1, 1, 50),        // the first pixel, day 1
    det(37.0, 31.1005, 1, 60),     // the same pixel seen again by another satellite: counted once
    det(37.0, 31.1, 2, 70),        // the same pixel on day 2: it is not new area
    det(37.01, 31.1, 2, 20),       // a new pixel on day 2
    det(37.02, 31.1, 3, 20),       // a new pixel on day 3
    det(37.5, 31.1, 2, 99),        // outside the fire's box: ignored
    det(37.0, 31.1, 9, 99),        // on a day after the fire: ignored
  ],
};
const fp = buildFootprint(detail);
console.log(JSON.stringify({
  cells: fp.cells.length, count: fp.count,
  added: fp.added.map((v) => +(v / 0.140625).toFixed(3)),
  cumulative: fp.cumulative.map((v) => +(v / 0.140625).toFixed(3)),
  firstFrp: fp.first.frp, strongest: fp.strongest.frp, label: fp.dayLabel(2),
}));
"""


def test_each_pixel_is_counted_once_on_the_day_it_was_first_seen():
    result = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, MODULE.as_uri()], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["cells"] == 3 and out["count"] == 3
    assert out["added"] == [1, 1, 1] and out["cumulative"] == [1, 2, 3]  # one 375 m pixel is 0.1406 square km
    assert out["firstFrp"] == 60 and out["strongest"] == 70 and out["label"] == "2021-08-03"  # the 99 MW ones are outside the fire
