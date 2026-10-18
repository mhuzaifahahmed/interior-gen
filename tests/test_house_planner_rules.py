"""Planner rule: same-category balance (BALANCE_RATIO). Pure planner tests."""
import pytest

from app.pipeline import planning_rules as rules
from app.pipeline.house_planner import _balance_same_category, _entry, plan_building
from tests.mask_quality_matrix import CASES

FT = "ft"


def _entries(names):
    return [_entry(n, FT, None) for n in names]


def test_balance_ratio_constant():
    assert rules.BALANCE_RATIO == 1.5


def test_unbalanced_bedrooms_are_evened_out_keeping_total():
    entries = _entries(["Bedroom 2", "Bedroom 3", "Bedroom 4"])
    maxs = [e["max"] for e in entries]
    alloc = [maxs[0], entries[1]["min"], entries[2]["min"] * 1.1]
    total = sum(alloc)
    assert max(alloc) > rules.BALANCE_RATIO * min(alloc)
    _balance_same_category(entries, alloc, maxs)
    assert max(alloc) <= rules.BALANCE_RATIO * min(alloc) + 1e-9
    assert sum(alloc) == pytest.approx(total)
    assert all(e["min"] - 1e-9 <= a <= m + 1e-9 for e, a, m in zip(entries, alloc, maxs))


def test_balance_never_exceeds_max():
    entries = _entries(["Bedroom 2", "Bedroom 3"])
    maxs = [e["max"] for e in entries]
    alloc = [maxs[0], entries[1]["min"]]
    _balance_same_category(entries, alloc, maxs)
    assert all(a <= m + 1e-9 for a, m in zip(alloc, maxs))


def test_master_bedroom_is_its_own_group():
    entries = _entries(["Master Bedroom", "Bedroom 2"])
    maxs = [e["max"] for e in entries]
    alloc = [maxs[0], entries[1]["min"]]
    before = list(alloc)
    _balance_same_category(entries, alloc, maxs)
    assert alloc == before  # a master is not forced toward a regular bedroom


def test_pinned_categories_are_exempt():
    entries = _entries(["Staircase", "Hallway"])
    alloc = [10.0, 40.0]
    _balance_same_category(entries, alloc, [e["max"] for e in entries])
    assert alloc == [10.0, 40.0]


def test_group_that_cannot_balance_is_left_alone():
    entries = _entries(["Bedroom 2", "Bedroom 3"])
    maxs = [entries[0]["min"] * 1.1, entries[1]["max"]]  # first room capped near its minimum
    alloc = [entries[0]["min"], entries[1]["max"]]
    before = list(alloc)
    _balance_same_category(entries, alloc, maxs)
    assert alloc == before


def test_planner_bedroom_targets_within_ratio_on_matrix():
    for case in CASES:
        plan = plan_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"]))
        for f in plan.floors:
            beds = [a for n, a in f.room_targets.items() if n.startswith("Bedroom")]
            if len(beds) > 1:
                assert max(beds) <= rules.BALANCE_RATIO * min(beds) + 1e-6, (case["id"], f.floor_number)
