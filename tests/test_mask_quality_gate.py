"""Mask-engine quality ratchet (Phase 0). Compares today's metrics for every
matrix floor to tests/data/mask_quality_baseline.json. Calls the mask module
directly, so it is independent of settings.house_layout_engine / conftest's
rect pin. Update the baseline only via:
    python scripts/mask_quality_report.py --update-baseline
"""
import json
import os

import pytest

from app.pipeline.floor_layout_mask import layout_floor_with_warnings
from app.pipeline.layout_metrics import measure_floor
from tests.mask_quality_matrix import iter_floors

_BASELINE_PATH = os.path.join(os.path.dirname(__file__), "data", "mask_quality_baseline.json")
with open(_BASELINE_PATH, encoding="utf-8") as _f:
    _BASELINE = json.load(_f)

_ITEMS = list(iter_floors())

# Circulation fields filled by Phase 4; ratchet only when set in BOTH.
_LATER_PHASE_FIELDS = ("unreachable", "pass_through_private")


@pytest.mark.parametrize("case,floor_n,rooms,key", _ITEMS, ids=[i[3] for i in _ITEMS])
def test_mask_quality_does_not_regress(case, floor_n, rooms, key):
    assert key in _BASELINE, (
        f"{key} missing from baseline - run "
        "`python scripts/mask_quality_report.py --update-baseline`"
    )
    base = _BASELINE[key]
    rects, _ = layout_floor_with_warnings(
        rooms, case["plot"], garage_cars=case["garage_cars"], facing=case["facing"]
    )
    cur = measure_floor(
        rects, case["plot"], total_floors=len(case["floors"]), floor_number=floor_n,
        garage_cars=case["garage_cars"],
    )

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

    # TODO(Phase 1): coverage_pct must stay within the plot-band range from
    # planning_rules.PLOT_BANDS. Informational until then (never fails).
