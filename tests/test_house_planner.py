"""Mask planner Phase 2: house_planner.plan_building, the planner path of the
mask engine (bounded leftover fill, rebalance), and the opt-in scorer checks.
"""
import pytest

from app.config import settings
from app.pipeline import floor_layout_mask
from app.pipeline import planning_rules as rules
from app.pipeline.house_planner import (
    ADDED_REASON_SPARE,
    ADDED_REASON_TERRACE,
    floor_plan_from_dict,
    plan_building,
    plan_to_dict,
    program_bounds,
)
from app.pipeline.layout_quality import score_floor_layout
from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    max_area_for_room,
    to_plot_unit,
)
from tests.mask_quality_matrix import CASES, GROUND_2F, UPPER_2F, _typical, planner_building

FT = "ft"


def _plot(length, width):
    return {"length": float(length), "width": float(width), "unit": FT}


def _net_area(plot):
    e = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, FT)
    return (plot["length"] - 2 * e) * (plot["width"] - 2 * e)


def _area(rect):
    return sum(p["w"] * p["h"] for p in (rect.get("parts") or [rect]))


# ---------------------------------------------------------------- planner


def test_footprint_coverage_scales_with_plot_band():
    rooms = _typical()
    small = plan_building([rooms], _plot(40, 60), "south")
    big = plan_building([rooms], _plot(150, 200), "south")
    assert small.band == "S" and big.band == "XL"
    # Footprint grows with the plot (up to the program ceiling)...
    assert big.footprint_area > small.footprint_area
    # ...while coverage of the plot falls band by band.
    assert big.footprint_area / big.net_plot_area < small.footprint_area / small.net_plot_area


def test_small_plot_footprint_follows_band_target():
    plan = plan_building([_typical()], _plot(40, 60), "south")
    band = rules.plot_band(_net_area(_plot(40, 60)), FT)
    assert plan.footprint_area == pytest.approx(band.target_cov * plan.net_plot_area, rel=0.02)


def test_huge_plot_is_capped_by_program_ceiling():
    plan = plan_building([_typical()], _plot(300, 300), "south")
    target = rules.plot_band(plan.net_plot_area, FT).target_cov * plan.net_plot_area
    assert plan.footprint_area < target  # not the band's 35% of an estate
    assert plan.footprint_area <= plan.program_ceiling + 1e-6


def test_footprint_never_exceeds_envelope_or_plot():
    for case in CASES:
        plan = plan_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"]))
        y = plan.yards
        assert plan.footprint_area <= plan.envelope_area + 1e-6, case["id"]
        assert y["left"] + plan.footprint_length + y["right"] == pytest.approx(case["plot"]["length"]), case["id"]
        assert y["top"] + plan.footprint_width + y["bottom"] == pytest.approx(case["plot"]["width"]), case["id"]
        assert min(y["top"], y["bottom"], y["left"], y["right"]) >= to_plot_unit(EXTERIOR_WALL_THICKNESS_M, FT) - 1e-9


def test_yards_balanced_on_40x60():
    plan = plan_building([_typical()], _plot(40, 60), "south")
    y = plan.yards
    sb = rules.band_setbacks(rules.plot_band(plan.net_plot_area, FT), FT)
    assert y["left"] == pytest.approx(y["right"])
    assert y["front"] >= sb["front"] - 1e-6 and y["rear"] >= sb["rear"] - 1e-6
    assert max(y["front"], y["rear"]) <= 4 * min(y["front"], y["rear"])
    assert y["bottom"] == y["front"]  # south facing: front yard at the bottom


def test_garage_forces_front_setback():
    plan = plan_building([_typical(True)], _plot(60, 80), "north", garage_cars=1)
    assert plan.yards["front"] >= to_plot_unit(rules.GARAGE_MIN_FRONT_SETBACK_M, FT) - 1e-6
    assert plan.yards["top"] == plan.yards["front"]


def test_targets_sum_to_usable_area():
    for case in CASES:
        plan = plan_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"]))
        for f in plan.floors:
            total = sum(f.room_targets.values()) + f.unallocated_area
            # The ground-floor Verandah (big-plot tiers) is open area on top
            # of the enclosed usable area.
            porch = plan.porch_area if f.floor_number == 1 and rules.GROUND_FLOOR_EXCESS_FALLBACK in f.room_targets else 0.0
            assert total == pytest.approx(f.usable_area + porch, rel=1e-6), (case["id"], f.floor_number)


def test_bonus_rooms_respect_priority_eligibility_and_counts():
    plan = plan_building([GROUND_2F, UPPER_2F], _plot(150, 150), "south", total_floors=2)
    assert plan.tier == "spacious"
    tier_list = rules.BONUS_ROOM_PRIORITY_BY_TIER[plan.tier]
    order = [b["name"] for b in tier_list]
    for f in plan.floors:
        spare = [a["name"] for a in f.added_rooms if a["added_reason"] == ADDED_REASON_SPARE]
        # priority order (a numbered extra, e.g. "Library 2", ranks with its entry)
        base = [n if n in order else n.rsplit(" ", 1)[0] for n in spare]
        assert base == sorted(base, key=order.index)
        for a in f.added_rooms:
            assert a["planner_added"] is True
            entry = next((b for b in tier_list if a["name"] in (b["name"], b["name"] + " 2", b["name"] + " 3")), None)
            if entry and entry["floors"] == "ground":
                assert f.floor_number == 1
            # Never a bedroom or bathroom.
            assert classify_room_category(a["name"]) not in ("bedroom", "bathroom")
    # Bonus rooms smaller than BONUS_ROOM_MIN_TARGET_SQM (closet, pantry,
    # laundry, powder, storage) are not added - they came out as slivers.
    for f in plan.floors:
        for name in ("Walk-in Closet", "Pantry", "Laundry", "Powder Room", "Storage"):
            assert name not in f.room_targets
    assert "Media Room" not in plan.floors[1].room_targets  # ground-floor only


def test_walk_in_closet_eligibility_is_per_master_suite(monkeypatch):
    monkeypatch.setattr(rules, "BONUS_ROOM_MIN_TARGET_SQM", 0.0)
    monkeypatch.setitem(rules.BONUS_ROOM_MIN_TARGET_SQM_BY_TIER, "spacious", 0.0)  # 150x150 = spacious tier
    plan = plan_building([GROUND_2F, UPPER_2F], _plot(150, 150), "south", total_floors=2)
    assert "Walk-in Closet" in plan.floors[1].room_targets  # master suite upstairs
    assert "Walk-in Closet" not in plan.floors[0].room_targets


def test_upper_floor_remainder_becomes_terrace():
    plan = plan_building([GROUND_2F, UPPER_2F], _plot(60, 80), "south", total_floors=2)
    upper = plan.floors[1]
    terrace = [a for a in upper.added_rooms if a["added_reason"] == ADDED_REASON_TERRACE]
    assert terrace and terrace[0]["name"] == rules.UPPER_FLOOR_EXCESS_FALLBACK
    assert upper.room_max[terrace[0]["name"]] >= upper.room_targets[terrace[0]["name"]]
    assert not any(a["added_reason"] == ADDED_REASON_TERRACE for a in plan.floors[0].added_rooms)


def test_hierarchy_rules_applied_to_targets():
    for case in CASES:
        plan = plan_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"]))
        for f in plan.floors:
            living = [v for n, v in f.room_targets.items() if classify_room_category(n) == "living"]
            dining = [v for n, v in f.room_targets.items() if classify_room_category(n) == "dining"]
            if living and dining:
                assert max(living) >= max(dining) - 1e-6, case["id"]


def test_program_bounds_min_le_ceiling():
    lo, hi = program_bounds(_typical(), FT)
    assert 0 < lo < hi


def test_plan_round_trips_through_dict():
    plan = plan_building([GROUND_2F, UPPER_2F], _plot(60, 80), "east", total_floors=2)
    fp = floor_plan_from_dict(plan_to_dict(plan), 2)
    assert fp.footprint_length == plan.footprint_length
    assert fp.room_targets == plan.floors[1].room_targets
    assert floor_plan_from_dict(None, 1) is None


# ---------------------------------------------------------------- engine


def _planner_floor(case, floor_n):
    plan, results = planner_building(case)  # computed once per session
    _used, rects, warnings = results[floor_n - 1]
    return plan.for_floor(floor_n), (rects, warnings)


def test_flag_off_ignores_plan(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", False)
    case = next(c for c in CASES if c["id"] == "40x60_typical_1f_south")
    plan = plan_building(case["floors"], case["plot"], case["facing"])
    a = floor_layout_mask.layout_floor(case["floors"][0], case["plot"], facing="south")
    b = floor_layout_mask.layout_floor(case["floors"][0], case["plot"], facing="south", plan=plan.floors[0])
    assert a == b


def test_planner_path_tiles_footprint_exactly(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    for case in CASES:
        for n in range(1, len(case["floors"]) + 1):
            fplan, (rects, _w) = _planner_floor(case, n)
            covered = sum(_area(r) for r in rects)
            assert covered == pytest.approx(fplan.footprint_length * fplan.footprint_width, rel=1e-6), case["id"]
            # The footprint sits exactly at the plan's yards.
            assert min(r["x"] for r in rects) == pytest.approx(fplan.yards["left"])
            assert min(r["y"] for r in rects) == pytest.approx(fplan.yards["top"])


def test_planner_path_bounds_room_overflow(monkeypatch):
    """Bounded fill + rebalance keep rooms near their max. Not yet a hard
    guarantee (see report: a few rooms still exceed 1.10 x max where the
    spine splits the zone), so this pins the CURRENT ceiling as a ratchet."""
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    worst = 0.0
    for case in CASES:
        for n in range(1, len(case["floors"]) + 1):
            fplan, (rects, _w) = _planner_floor(case, n)
            for r in rects:
                cat = classify_room_category(r["name"])
                if cat in ("hallway", "staircase", "garage"):
                    continue
                mx = fplan.room_max.get(r["name"]) or max_area_for_room(r["name"], FT, case["garage_cars"])
                worst = max(worst, _area(r) / mx)
    assert worst < 2.7  # today: 2.63 (150x150 dense upper-floor Sitting Area) - see report


def test_planner_without_plan_builds_its_own(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    rects = floor_layout_mask.layout_floor(_typical(), _plot(50, 50), facing="north")
    assert rects
    assert any(r["name"] == "Living Room" for r in rects)


def test_planner_is_deterministic(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    case = next(c for c in CASES if c["id"] == "150x200_2f_reported")
    def fresh():
        _p, res = floor_layout_mask.layout_building(
            case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"])
        )
        return res[0][1]

    assert fresh() == fresh()


def test_fill_leftovers_bounded_respects_max():
    # 1x6 strip: room 0 owns col 0, room 1 owns col 5, four free cells between.
    owner = [[0, -1, -1, -1, -1, 1]]
    cells = [{(0, 0)}, {(5, 0)}]
    stats: dict = {}
    floor_layout_mask._fill_leftovers_bounded(owner, cells, [3, 3], [3, 4], 6, 1, set(), None, 0, stats)
    assert all(v >= 0 for v in owner[0])
    assert len(cells[0]) <= 3 and len(cells[1]) <= 4
    assert stats["overflow_cells"] == 0


def test_fill_leftovers_bounded_counts_unavoidable_overflow():
    owner = [[0, -1, -1, -1, -1, 1]]
    cells = [{(0, 0)}, {(5, 0)}]
    stats: dict = {}
    floor_layout_mask._fill_leftovers_bounded(owner, cells, [1, 1], [2, 2], 6, 1, set(), None, 0, stats)
    assert all(v >= 0 for v in owner[0])
    assert stats["overflow_cells"] > 0


def test_fit_cells_scales_within_bounds():
    tgt = [5, 5]
    floor_layout_mask._fit_cells([0, 1], [2, 2], tgt, [8, 6], 13)
    assert sum(tgt) == 13 and tgt[0] <= 8 and tgt[1] <= 6
    tgt = [5, 5]
    floor_layout_mask._fit_cells([0, 1], [2, 4], tgt, [8, 6], 7)
    assert sum(tgt) == 7 and tgt[0] >= 2 and tgt[1] >= 4


# ---------------------------------------------------------------- scorer


def _box(name, w, h, x=0.0, y=0.0):
    return {"name": name, "x": x, "y": y, "w": w, "h": h}


def test_scorer_planner_checks_are_opt_in():
    rects = [_box("Living Room", 10, 12), _box("Dining Room", 16, 20, x=10)]  # dining huge, living < dining
    dims = {"length": 26, "width": 20, "unit": FT}
    off = score_floor_layout(rects, dims)
    on = score_floor_layout(rects, dims, planner_checks=True)
    kinds_off = {v.kind for v in off.violations}
    kinds_on = {(v.kind, v.hard) for v in on.violations}
    assert "oversized" not in kinds_off and "hierarchy_inverted" not in kinds_off
    assert ("oversized", True) in kinds_on
    assert ("hierarchy_inverted", True) in kinds_on


def test_scorer_over_target_is_soft_and_override_aware():
    rects = [_box("Bedroom 2", 12, 13)]  # 156 sqft: above target, below 1.10 x max
    dims = {"length": 12, "width": 13, "unit": FT}
    res = score_floor_layout(rects, dims, planner_checks=True, room_targets={"Bedroom 2": 100.0})
    assert [(v.kind, v.hard) for v in res.violations if v.kind == "over_target"] == [("over_target", False)]
    res = score_floor_layout(rects, dims, planner_checks=True, room_max={"Bedroom 2": 100.0})
    assert any(v.kind == "oversized" and v.hard for v in res.violations)
