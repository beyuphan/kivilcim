"""The wind logic of the page is plain geometry in a JavaScript module, checked here with Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).parent.parent / "src" / "kivilcim" / "static" / "js" / "wind.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

SCRIPT = """
const w = await import(process.argv[1]);
const place = { lat: 39, lon: 35 };
const sw = { lat: 38.9, lon: 34.9 };
const out = {
  bearing: w.compassName(w.bearing(39, 35, 38.9, 34.9)),
  toward: w.windRelation(sw, place, 225, 25).relation,   // wind from the fire's side
  away: w.windRelation(sw, place, 45, 25).relation,
  across: w.windRelation(sw, place, 135, 25).relation,
  calm: w.windRelation(sw, place, 225, 3).relation,
  missing: w.windRelation(sw, place, null, null).relation,
  wrap: w.angleDiff(350, 10),
  from: w.fromName(225),
  near: w.describe("toward", 8).level,
  far: w.describe("toward", 20).level,
};
console.log(JSON.stringify(out));
"""


def test_the_wind_is_compared_with_the_direction_of_the_fire():
    result = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, MODULE.as_uri()], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "bearing": "güneybatı",
        "toward": "toward",
        "away": "away",
        "across": "across",
        "calm": "calm",
        "missing": "calm",
        "wrap": 20,
        "from": "güneybatıdan",
        "near": "very_high",
        "far": "high",
    }
