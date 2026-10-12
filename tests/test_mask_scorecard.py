"""Unit tests for the honest mask scorecard (mask_scorecard.py) on synthetic
inputs. The scorecard RUN over the matrix is a report
(scripts/mask_quality_report.py --scorecard), never a pass/fail test."""
from app.pipeline.mask_scorecard import (
    CIRCULATION_NOT_MEASURED,
    SCORED_CHECKS,
    format_scorecard,
    score_floor,
)

PLOT = {"length": 40.0, "width": 60.0, "unit": "ft"}


def _r(name, x, y, w, h, parts=None):
    d = {"name": name, "x": x, "y": y, "w": w, "h": h}
    if parts:
        d["parts"] = parts
    return d


def _house(x0=4.0, y0=6.0, w=32.0, h=46.0, dining=(12, 10), bath=(6, 8)):
    """A tiled footprint at (x0, y0): living/dining/kitchen front, bed/bath back."""
    lw = w - dining[0]
    return [
        _r("Living Room", x0, y0, lw, 20),
        _r("Dining Room", x0 + lw, y0, dining[0], dining[1]),
        _r("Kitchen", x0 + lw, y0 + dining[1], dining[0], 20 - dining[1]),
        _r("Bedroom 1", x0, y0 + 20, w - bath[0], h - 20),
        _r("Bathroom 1", x0 + w - bath[0], y0 + 20, bath[0], bath[1]),
        _r("Hallway", x0 + w - bath[0], y0 + 20 + bath[1], bath[0], h - 20 - bath[1]),
    ]


FLOORS = [[{"name": n} for n in ("Living Room", "Dining Room", "Kitchen", "Bedroom 1", "Bathroom 1")]]


def test_balanced_yards_pass_and_lopsided_fail():
    good = score_floor(_house(4, 6), PLOT, "south", FLOORS, 1)
    assert good["yards"]["applies"] and good["yards"]["pass"]
    # Rear yard ~0, front yard large (the reported 40x60 symptom).
    bad = score_floor(_house(4, 0.75), PLOT, "south", FLOORS, 1)
    assert not bad["yards"]["pass"]


def test_coverage_check_uses_band_range():
    res = score_floor(_house(), PLOT, "south", FLOORS, 1)
    cov = res["coverage"]
    lo, hi = cov["range_pct"]
    assert lo <= cov["coverage_pct"] <= hi and cov["pass"]
    tiny = score_floor(_house(15, 20, w=10, h=21), PLOT, "south", FLOORS, 1)
    assert not tiny["coverage"]["pass"]


def test_sizes_flags_inverted_hierarchy_big_bath_and_dining():
    res = score_floor(_house(dining=(20, 14), bath=(10, 9)), PLOT, "south", FLOORS, 1)
    s = res["sizes"]
    assert not s["pass"]
    assert s["big_baths"] and s["big_dining"]
    assert s["inverted"]


def test_remainder_rooms_are_listed_not_oversized():
    rects = _house()
    rects[3]["name"] = "Terrace"
    res = score_floor(rects, PLOT, "south", FLOORS, 1, max_overrides={"Terrace": 600.0})
    assert "Terrace" not in res["sizes"]["oversized"]
    assert res["sizes"]["remainder_rooms"]


def test_parts_and_stair_checks():
    rects = _house()
    rects[3]["parts"] = [{"x": 4, "y": 26, "w": 10, "h": 10}, {"x": 14, "y": 26, "w": 5, "h": 5},
                         {"x": 19, "y": 26, "w": 2, "h": 2}]
    res = score_floor(rects, PLOT, "south", FLOORS, 1)
    assert not res["parts"]["pass"] and res["parts"]["max_parts"] == 3
    rects = _house()
    rects.append(_r("Staircase", 0.75, 0.75, 1, 1))
    assert not score_floor(rects, PLOT, "south", FLOORS, 1)["stair"]["pass"]


def test_clean_requires_every_check_and_circulation_is_not_faked():
    res = score_floor(_house(), PLOT, "south", FLOORS, 1)
    assert res["circulation"] == CIRCULATION_NOT_MEASURED
    assert res["clean"] == all(res[c]["pass"] for c in SCORED_CHECKS)
    text = format_scorecard({"x/floor1": res}, "t", {1: {"bedrooms": 0, "bathrooms": 0}})
    assert CIRCULATION_NOT_MEASURED in text
    assert "max bedrooms 0" in text
    assert "floors fully clean" in text
