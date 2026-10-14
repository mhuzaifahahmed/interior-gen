"""Mask-engine quality ratchet (Phase 0). Compares today's metrics for every
matrix floor to tests/data/mask_quality_baseline.json. Calls the mask module
directly, so it is independent of settings.house_layout_engine / conftest's
rect pin. Update the baseline only via:
    python scripts/mask_quality_report.py --update-baseline

Running the tests (speed tiers):
  * Per change (minutes):   ./.venv/Scripts/python.exe -m pytest -m fast -q
    ~10 representative gate cases (both planner modes) + the pure unit-test
    files for the mask engine / planner / door graph / scorer / quality /
    room specs / planning rules. Same assertions as the full gate, so it
    still fails when a ratcheted metric worsens.
  * Whole gate:             ./.venv/Scripts/python.exe -m pytest tests/test_mask_quality_gate.py -q
  * Before each commit:     ./.venv/Scripts/python.exe -m pytest -q   (full suite)
  * Parallel matrix report: python scripts/mask_quality_report.py --scorecard --planner both --no-render --jobs 4
"""
import json
import os

import pytest

from app.config import settings
from app.pipeline.floor_layout_mask import layout_building, layout_floor_with_warnings
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.planning_rules import plot_band
from app.pipeline.room_specs import EXTERIOR_WALL_THICKNESS_M, to_plot_unit
from tests.mask_quality_matrix import iter_floors, planner_building

_BASELINE_PATH = os.path.join(os.path.dirname(__file__), "data", "mask_quality_baseline.json")
with open(_BASELINE_PATH, encoding="utf-8") as _f:
    _BASELINE = json.load(_f)
# Planner-on (Settings.house_planner_enabled) ratchet, Phase 2. Update only via
#   python scripts/mask_quality_report.py --planner on --update-baseline
_PLANNER_BASELINE_PATH = os.path.join(os.path.dirname(__file__), "data", "mask_quality_baseline_planner.json")
with open(_PLANNER_BASELINE_PATH, encoding="utf-8") as _f:
    _PLANNER_BASELINE = json.load(_f)

# Step 4: flag-off narrow-bedroom ratchet values (first measurement, Step 4 /
# HEAD c1140ba layouts). Kept apart from mask_quality_baseline.json so that
# frozen flag-off baseline does not change. {floor key: [bedroom names]}.
_NARROW_FLAG_OFF_PATH = os.path.join(os.path.dirname(__file__), "data", "mask_narrow_bedrooms_flag_off.json")
with open(_NARROW_FLAG_OFF_PATH, encoding="utf-8") as _f:
    _NARROW_FLAG_OFF = json.load(_f)

_ITEMS = list(iter_floors())

# Representative subset run by `pytest -m fast` (see docstring). Covers every
# plot band, 1-3 floors, garage/dense programs, and non-south facings.
_FAST_CASE_IDS = {
    "30x40_small_1f", "40x60_typical_1f_south", "50x50_2f", "60x80_dense_garage_1f",
    "60x80_3f_south", "90x40_typical_1f", "150x150_dense_garage_2f",
    "150x200_2f_reported", "300x300_typical_1f", "40x60_typical_1f_east",
}


def _fast(case):
    return [pytest.mark.fast] if case["id"] in _FAST_CASE_IDS else []
_COVERAGE_TOLERANCE_PCT = 0.5

# Circulation fields (Phase 4b, door_graph.circulation_report); ratchet
# only when set in BOTH (a baseline from before Phase 4b has them as None).
_LATER_PHASE_FIELDS = ("unreachable", "pass_through_private", "entry_is_garage", "entry_missing")
# Planner-on absolute targets (Phase 4b): reached on every matrix floor, so
# they are held at 0, not just "no worse". pass_through_private is NOT at 0
# yet (see the Phase 4b report) and is only ratcheted.
_PLANNER_ZERO_FIELDS = ("unreachable", "entry_is_garage", "entry_missing")


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
    marks = _fast(case)
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
        garage_cars=case["garage_cars"], facing=case["facing"],
    )
    _assert_no_regression(case, cur, _BASELINE[key])
    assert len(cur["narrow_bedrooms"]) <= len(_NARROW_FLAG_OFF[key]), (
        "narrow_bedrooms", cur["narrow_bedrooms"], _NARROW_FLAG_OFF[key])


def _planner_layout(case, floor_n, rooms):
    # The whole building is laid out once per session (see
    # mask_quality_matrix.planner_building), not once per floor / per test.
    plan, results = planner_building(case)
    _used, rects, _w = results[floor_n - 1]
    return measure_floor(
        rects, case["plot"], total_floors=len(case["floors"]), floor_number=floor_n,
        garage_cars=case["garage_cars"], max_overrides=dict(plan.for_floor(floor_n).room_max) or None,
        facing=case["facing"],
    )


@pytest.mark.parametrize("case,floor_n,rooms,key", [pytest.param(*i, id=i[3], marks=_fast(i[0])) for i in _ITEMS])
def test_mask_quality_planner_does_not_regress(monkeypatch, case, floor_n, rooms, key):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    assert key in _PLANNER_BASELINE, (
        f"{key} missing from planner baseline - run "
        "`python scripts/mask_quality_report.py --planner on --update-baseline`"
    )
    cur = _planner_layout(case, floor_n, rooms)
    _assert_no_regression(case, cur, _PLANNER_BASELINE[key])
    for field in _PLANNER_ZERO_FIELDS:
        assert cur[field] == [], (field, cur[field])


# The two Phase-1 regressions Phase 2 was meant to clear, judged with the
# planner ON against the flag-OFF baseline numbers. Phase 3 (shape
# discipline) clears 60x80 dense (planner-on hard 2 vs baseline 5); 150x150
# dense floor1 is still not cleared - that one stays strict xfail so it flips
# to a failure the moment a later phase clears it.
# Big-plot pass (spacious tier on 150x150): 150x150 dense floor1 is now
# cleared too (planner-on hard 2 vs flag-off baseline hard 4; oversized
# 2 vs 3), so the strict xfail flipped and was removed.
_PLANNER_STILL_REGRESSED: set = set()


def _clear_param(key):
    marks = _fast({"id": key.split("/")[0]})
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
    # Big-plot pass metrics (no prior baseline): ratcheted from the first
    # baseline that records them.
    # Step 3 (door rules): orphaned ensuites, ratcheted from the first
    # baseline that records them.
    # Step 4: narrow bedrooms (aspect > 2.5:1 or hard `narrow`), ratcheted
    # although the check stays non-hard; planner baseline records it, the
    # flag-off values live in _NARROW_FLAG_OFF (that baseline is frozen).
    for field in ("bedroom_aspect_fail", "stair_over_cap", "orphaned_ensuites", "narrow_bedrooms"):
        if field in base:
            assert len(cur[field]) <= len(base[field]), (field, cur[field], base[field])
    for field in _LATER_PHASE_FIELDS:
        if cur[field] is not None and base.get(field) is not None:
            assert len(cur[field]) <= len(base[field]), field
    # Step 3: the front door may not move from the foyer to a fallback room
    # (ratcheted from the first baseline that records it; None above floor 1).
    if cur.get("front_door_fallback") is not None and base.get("front_door_fallback") is not None:
        assert cur["front_door_fallback"] <= base["front_door_fallback"], (
            "front_door_fallback", cur["front_door_room"], base.get("front_door_room"))

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


# ---- suite_guard ratchet (Step 3b) ----
# suite_guard is a planner-only EXTRA candidate path accepted as a WORKAROUND
# for orphaned ensuites, not the cure. The number of floors whose chosen path
# is suite_guard may not increase vs tests/data/mask_quality_baseline_planner
# .json ("layout_path" per floor). It may go down: then lower the baseline by
# re-running `mask_quality_report.py --planner on --update-baseline` review-
# ing the diff.
def _suite_guard_floors(case_ids):
    from app.pipeline.mask_scorecard import workaround_path_floors

    paths = {}
    for case, floor_n, _rooms, key in _ITEMS:
        if case["id"] in case_ids:
            plan, _results = planner_building(case)
            paths[key] = plan.for_floor(floor_n).layout_path
    return workaround_path_floors(paths)["suite_guard"], paths


def _baseline_suite_guard(case_ids):
    return [k for k, v in _PLANNER_BASELINE.items()
            if v.get("layout_path") is not None and k.split("/")[0] in case_ids
            and (v["layout_path"] == "suite_guard" or v["layout_path"].startswith("suite_guard+"))]


@pytest.mark.fast
def test_suite_guard_picks_do_not_increase_fast_subset(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    cur, _ = _suite_guard_floors(_FAST_CASE_IDS)
    base = _baseline_suite_guard(_FAST_CASE_IDS)
    assert len(cur) <= len(base), ("suite_guard picks increased on the fast subset", cur, base)


def test_suite_guard_picks_do_not_increase_whole_matrix(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    all_ids = {c["id"] for c, *_ in _ITEMS}
    cur, paths = _suite_guard_floors(all_ids)
    base = _baseline_suite_guard(all_ids)
    assert all(_PLANNER_BASELINE[k].get("layout_path") for k in paths), "baseline lacks layout_path"
    assert len(cur) <= len(base), ("suite_guard picks increased", len(cur), len(base), cur, base)
