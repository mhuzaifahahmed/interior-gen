"""Mask-engine quality ratchet (Phase 0). Compares today's metrics for every
matrix floor to tests/data/mask_quality_baseline.json. Calls the mask module
directly, so it is independent of settings.house_layout_engine / conftest's
rect pin. Update the baseline only via:
    python scripts/mask_quality_report.py --update-baseline
"""
import json
import os

import pytest

from app.config import settings
from app.pipeline.floor_layout_mask import layout_building, layout_floor_with_warnings
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.planning_rules import plot_band
from app.pipeline.room_specs import EXTERIOR_WALL_THICKNESS_M, to_plot_unit
from tests.mask_quality_matrix import iter_floors

_BASELINE_PATH = os.path.join(os.path.dirname(__file__), "data", "mask_quality_baseline.json")
with open(_BASELINE_PATH, encoding="utf-8") as _f:
    _BASELINE = json.load(_f)
# Planner-on (Settings.house_planner_enabled) ratchet, Phase 2. Update only via
#   python scripts/mask_quality_report.py --planner on --update-baseline
_PLANNER_BASELINE_PATH = os.path.join(os.path.dirname(__file__), "data", "mask_quality_baseline_planner.json")
with open(_PLANNER_BASELINE_PATH, encoding="utf-8") as _f:
    _PLANNER_BASELINE = json.load(_f)

_ITEMS = list(iter_floors())
_COVERAGE_TOLERANCE_PCT = 0.5

# Circulation fields filled by Phase 4; ratchet only when set in BOTH.
_LATER_PHASE_FIELDS = ("unreachable", "pass_through_private")


# Phase 1 fixed real spec bugs (master preferred < min, foyer max < preferred).
# The mask engine is sensitive to those shared numbers, so two dense cases got
# slightly worse while the aggregate improved (hard 100->92, oversized 54->49,
# undersized 2->0). Marked STRICT xfail on purpose (decided with the user):
# Phase 2 (planner + bounded _fill_leftovers) is the real fix and must clear
# them; strict=True fails the moment they start passing so this can't be
# forgotten. Do NOT re-baseline these.
_PHASE1_KNOWN_REGRESSIONS = {
    "60x80_dense_garage_1f/floor1": "hard violations 6 vs baseline 5 (door_too_narrow + narrow)",
    "150x150_dense_garage_2f/floor1": "Kitchen newly oversized",
}


def _param(item):
    case, floor_n, rooms, key = item
    marks = []
    if key in _PHASE1_KNOWN_REGRESSIONS:
        marks.append(pytest.mark.xfail(strict=True, reason="Phase 1 known regression -> fix in Phase 2: " + _PHASE1_KNOWN_REGRESSIONS[key]))
    return pytest.param(case, floor_n, rooms, key, id=key, marks=marks)


@pytest.mark.parametrize("case,floor_n,rooms,key", [_param(i) for i in _ITEMS])
def test_mask_quality_does_not_regress(case, floor_n, rooms, key):
    assert key in _BASELINE, (
        f"{key} missing from baseline - run "
        "`python scripts/mask_quality_report.py --update-baseline`"
    )
    rects, _ = layout_floor_with_warnings(
        rooms, case["plot"], garage_cars=case["garage_cars"], facing=case["facing"]
    )
    cur = measure_floor(
        rects, case["plot"], total_floors=len(case["floors"]), floor_number=floor_n,
        garage_cars=case["garage_cars"],
    )
    _assert_no_regression(case, cur, _BASELINE[key])


def _planner_layout(case, floor_n, rooms):
    plan, results = layout_building(
        case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"])
    )
    _used, rects, _w = results[floor_n - 1]
    return measure_floor(
        rects, case["plot"], total_floors=len(case["floors"]), floor_number=floor_n,
        garage_cars=case["garage_cars"], max_overrides=dict(plan.for_floor(floor_n).room_max) or None,
    )


@pytest.mark.parametrize("case,floor_n,rooms,key", [pytest.param(*i, id=i[3]) for i in _ITEMS])
def test_mask_quality_planner_does_not_regress(monkeypatch, case, floor_n, rooms, key):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    assert key in _PLANNER_BASELINE, (
        f"{key} missing from planner baseline - run "
        "`python scripts/mask_quality_report.py --planner on --update-baseline`"
    )
    _assert_no_regression(case, _planner_layout(case, floor_n, rooms), _PLANNER_BASELINE[key])


# The two Phase-1 regressions Phase 2 was meant to clear, judged with the
# planner ON against the flag-OFF baseline numbers. Phase 3 (shape
# discipline) clears 60x80 dense (planner-on hard 2 vs baseline 5); 150x150
# dense floor1 is still not cleared - that one stays strict xfail so it flips
# to a failure the moment a later phase clears it.
_PLANNER_STILL_REGRESSED = {"150x150_dense_garage_2f/floor1"}


def _clear_param(key):
    marks = []
    if key in _PLANNER_STILL_REGRESSED:
        marks.append(pytest.mark.xfail(strict=True, reason="planner does not yet clear this Phase-1 regression"))
    return pytest.param(key, id=key, marks=marks)


@pytest.mark.parametrize("key", [_clear_param(k) for k in sorted(_PHASE1_KNOWN_REGRESSIONS)])
def test_planner_clears_phase1_regressions(monkeypatch, key):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    case, floor_n, rooms, _k = next(i for i in _ITEMS if i[3] == key)
    cur = _planner_layout(case, floor_n, rooms)
    base = _BASELINE[key]
    assert cur["hard_count"] <= base["hard_count"], (cur["hard_kinds"], base["hard_kinds"])
    assert len(cur["oversized"]) <= len(base["oversized"]), cur["oversized"]


def _assert_no_regression(case, cur, base):

    # Absolute.
    assert cur["coverage_gap_fraction"] <= 0.01, cur["coverage_gap_fraction"]
    assert cur["overlap_area"] <= 1e-3, cur["overlap_area"]

    # Must-not-worsen.
    assert cur["hard_count"] <= base["hard_count"], (cur["hard_kinds"], base["hard_kinds"])
    assert len(cur["daylight_fail_hard"]) <= len(base["daylight_fail_hard"]), cur["daylight_fail_hard"]
    assert len(cur["oversized"]) <= len(base["oversized"]), cur["oversized"]
    assert int(cur["hierarchy_inverted"]) <= int(base["hierarchy_inverted"])
    assert len(cur["undersized"]) <= len(base["undersized"]), cur["undersized"]
    for field in _LATER_PHASE_FIELDS:
        if cur[field] is not None and base.get(field) is not None:
            assert len(cur[field]) <= len(base[field]), field

    # Coverage must not exceed the plot band's max (NET plot area). Only the
    # UPPER bound is enforced - today's mask coverage is far below target on
    # big plots (the known problem), so the lower bound stays informational.
    # A case whose committed baseline already exceeds its band max (the "before"
    # snapshot) is held to "no worse than baseline" instead of being weakened.
    plot = case["plot"]
    unit = plot.get("unit") or "ft"
    wall = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
    net_area = max(plot["length"] - 2 * wall, 0.0) * max(plot["width"] - 2 * wall, 0.0)
    band_max_pct = plot_band(net_area, unit).max_cov * 100
    allowed = max(band_max_pct, base["coverage_pct"]) + _COVERAGE_TOLERANCE_PCT
    assert cur["coverage_pct"] <= allowed, (cur["coverage_pct"], band_max_pct)
