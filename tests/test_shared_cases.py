"""The cases in shared_cases.json, run against the Python side.

game/tests.html runs the same file against the JavaScript side, so the
pipeline and the game can't quietly disagree about what counts as a match
or how a score is displayed.
"""
import json
from pathlib import Path

import pytest

from aliases import normalise
from scoring import format_ppm, ppm_from_points

CASES = json.loads((Path(__file__).parent / "shared_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("raw, expected", CASES["normalise"])
def test_normalise(raw, expected):
    assert normalise(raw) == expected


@pytest.mark.parametrize("points, expected", CASES["format_ppm"])
def test_format_ppm(points, expected):
    assert format_ppm(ppm_from_points(points)) == expected
