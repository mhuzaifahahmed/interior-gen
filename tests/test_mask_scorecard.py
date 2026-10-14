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


def test_clean_requires_every_check_and_circulation_is_measured():
    res = score_floor(_house(), PLOT, "south", FLOORS, 1)
    circ = res["circulation"]
    assert circ["measured"] is True
    # South facing: the front door opens into the Living Room (bottom edge);
    # Bedroom 1 is reached only through... the Living Room (public) - fine.
    assert circ["entry_is_garage"] is False and circ["entry_missing"] is False
    assert res["clean"] == all(res[c]["pass"] for c in SCORED_CHECKS)
    assert res["clean_all"] == (res["clean"] and circ["pass"])
    text = format_scorecard({"x/floor1": res}, "t", {1: {"bedrooms": 0, "bathrooms": 0}})
    assert CIRCULATION_NOT_MEASURED not in text
    assert "6. Circulation" in text and "incl. circulation" in text
    assert "max bedrooms 0" in text
    assert "floors fully clean" in text


def test_circulation_check_reports_a_pass_through_bedroom():
    # Bedroom 2 is reachable only through Bedroom 1 (no hallway touches it).
    rects = [
        _r("Living Room", 4, 6, 32, 20),
        _r("Bedroom 1", 4, 26, 16, 26),
        _r("Bedroom 2", 20, 26, 16, 26),
    ]
    floors = [[{"name": n} for n in ("Living Room", "Bedroom 1", "Bedroom 2")]]
    res = score_floor(rects, PLOT, "north", floors, 1)
    # Living touches both bedrooms here, so they are fine; now cut Bedroom 2 off.
    rects[2] = _r("Bedroom 2", 20, 26, 16, 26)
    rects[0] = _r("Living Room", 4, 6, 16, 20)
    rects.append(_r("Kitchen", 20, 6, 16, 20))
    res = score_floor(rects, PLOT, "north", floors, 1)
    assert res["circulation"]["pass"] is True  # kitchen is public, Bedroom 2 via kitchen
    rects[3] = _r("Study Bedroom", 20, 6, 16, 20)  # a bedroom in front of Bedroom 2
    res = score_floor(rects, PLOT, "north", floors, 1)
    assert "Bedroom 2" in res["circulation"]["pass_through_private"]
    assert not res["circulation"]["pass"]


def _scored(n):
    return {f"case{i}/floor1": score_floor(_house(4, 6), PLOT, "south", FLOORS, 1) for i in range(n)}


def test_engine_path_counts_cover_every_kind_and_sum_to_the_floor_count():
    # Regression: Phase 4b added circ_* path kinds the old fixed 3-kind line
    # skipped, so the printed counts summed to 25 of 35.
    results = _scored(6)
    keys = list(results)
    paths = {
        keys[0]: "rectified", keys[1]: "rectified", keys[2]: "circ_stair",
        keys[3]: "circ_arms", keys[4]: "circ_arms+stair", keys[5]: "reference_raw",
    }
    text = format_scorecard(results, "planner_on", paths=paths)
    assert "rectified 2" in text and "circ_stair 1" in text and "circ_arms 1" in text
    assert "circ_arms+stair 1" in text and "reference_raw 1" in text
    assert "[sum 6]" in text
    # Only the two reference kinds count as the Phase 2 fallback.
    assert "fallback (reference_reconnect, reference_raw): 1/6" in text


def test_engine_path_counts_fail_loudly_when_a_floor_has_no_path():
    import pytest
    results = _scored(3)
    keys = list(results)
    with pytest.raises(AssertionError, match="do not account for all 3 floors"):
        format_scorecard(results, "planner_on", paths={keys[0]: "rectified", keys[1]: "rectified"})


def test_workaround_path_floors_counts_each_kind():
    from app.pipeline.mask_scorecard import suite_guard_count, workaround_path_floors

    paths = {"a": "suite_guard", "b": "rectified", "c": "circ_arms", "d": "circ_arms+stair",
             "e": "circ_stair", "f": "suite_guard", "g": None, "h": "reference_raw"}
    w = workaround_path_floors(paths)
    assert w["suite_guard"] == ["a", "f"]
    assert w["circ_arms"] == ["c", "d"]
    assert w["circ_stair"] == ["e"]
    assert suite_guard_count(paths) == 2
    assert suite_guard_count({}) == 0


def test_scorecard_prints_suite_guard_picks_line_with_case_ids():
    results = _scored(4)
    keys = list(results)
    paths = {keys[0]: "suite_guard", keys[1]: "rectified", keys[2]: "suite_guard", keys[3]: "circ_arms"}
    text = format_scorecard(results, "planner_on", paths=paths)
    assert f"suite_guard picks: 2/4 floors ({keys[0]}, {keys[2]})" in text
    assert "circ_arms picks: 1/4 floors" in text and "circ_stair picks: 0/4 floors" in text
    none = format_scorecard(results, "planner_on", paths={k: "rectified" for k in keys})
    assert "suite_guard picks: 0/4 floors  [workaround" in none
