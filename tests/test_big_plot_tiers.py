"""Big-plot pass (planner flag only): size tiers by plot band, band-scaled
bonus rooms, the ground-floor Verandah remainder, the per-bedroom built-area
cap, the opt-in bedroom-aspect / stair-cap scorer checks, their metrics and
scorecard checks, and the Phase-3 fallback-path diagnostic."""
import pytest

from app.config import settings
from app.pipeline import floor_layout_mask
from app.pipeline import planning_rules as rules
from app.pipeline import room_specs as rs
from app.pipeline.house_planner import (
    ADDED_REASON_PORCH,
    _eligible_bonus_rooms,
    built_area_cap,
    plan_building,
)
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.layout_quality import score_floor_layout
from app.pipeline.mask_scorecard import format_scorecard, score_floor
from tests.mask_quality_matrix import SPARSE, UPPER_2F, _typical

FT = "ft"


def _plot(length, width):
    return {"length": float(length), "width": float(width), "unit": FT}


def _area(rect):
    return sum(p["w"] * p["h"] for p in (rect.get("parts") or [rect]))


# ---------------------------------------------------------------- tiers


@pytest.mark.parametrize("band,tier", [("XS", "standard"), ("S", "standard"), ("M", "standard"),
                                       ("L", "spacious"), ("XL", "estate")])
def test_tier_lookup_per_band(band, tier):
    assert rules.size_tier(band) == tier


@pytest.mark.parametrize("plot,tier", [((40, 60), "standard"), ((60, 80), "standard"),
                                       ((150, 150), "spacious"), ((300, 300), "estate")])
def test_plan_carries_tier(plot, tier):
    assert plan_building([_typical()], _plot(*plot), "south").tier == tier


_CATS = [c for c, _ in rs._CATEGORY_KEYWORDS] + ["default"]
_REP = {"powder": "powder room", "dirty_kitchen": "dirty kitchen", "family": "tv room",
        "living": "living room", "default": "zzz unknown", "foyer": "entry"}


@pytest.mark.parametrize("tier", rules.SIZE_TIERS)
@pytest.mark.parametrize("unit", ["ft", "m"])
@pytest.mark.parametrize("category", _CATS + ["master"])
def test_chain_holds_in_every_tier(category, unit, tier):
    name = "Master Bedroom" if category == "master" else _REP.get(category, category)
    mn = rs.min_area_for_room(name, unit)
    pf = rs.preferred_area_for_room(name, unit)
    tg = rules.tier_target_area(name, unit, tier=tier)
    mx = rules.tier_max_area(name, unit, tier=tier)
    eps = 1e-9
    assert mn <= pf + eps and pf <= tg + eps and tg <= mx + eps, (category, tier, mn, pf, tg, mx)


def test_tiers_grow_monotonically_and_standard_is_unchanged():
    for name in ("Living Room", "Master Bedroom", "Bedroom 2", "Bathroom 1", "Kitchen", "Dining Room", "Garage"):
        assert rules.tier_max_area(name, FT) == rs.max_area_for_room(name, FT)
        assert rules.tier_target_area(name, FT) == rs.target_area_for_room(name, FT)
        s, sp, es = (rules.tier_max_area(name, FT, tier=t) for t in rules.SIZE_TIERS)
        assert s <= sp <= es
    # Living grows more than a bedroom; stairs never grow.
    assert rules.tier_scale("Living Room", "estate") > rules.tier_scale("Bedroom 2", "estate")
    assert rules.tier_max_area("Staircase", FT, tier="estate") == rs.max_area_for_room("Staircase", FT)


def test_tier_max_reaches_room_max_only_off_standard():
    small = plan_building([_typical()], _plot(40, 60), "south")
    assert small.tier == "standard" and small.floors[0].room_max == {}
    big = plan_building([_typical()], _plot(150, 150), "south")
    rm = big.floors[0].room_max
    assert rm["Living Room"] == pytest.approx(rules.tier_max_area("Living Room", FT, tier="spacious"))
    assert rm["Living Room"] > rs.max_area_for_room("Living Room", FT)


def test_oversized_check_uses_tier_max_in_planner_mode_only():
    name = "Living Room"
    std_max = rs.max_area_for_room(name, FT)
    tier_max = rules.tier_max_area(name, FT, tier="estate")
    side = (1.3 * std_max) ** 0.5  # oversized vs standard, fine vs estate max
    assert 1.3 * std_max < 1.1 * tier_max
    rects = [{"name": name, "x": 0, "y": 0, "w": side, "h": side}]
    dims = {"length": side, "width": side, "unit": FT}
    std = score_floor_layout(rects, dims, planner_checks=True)
    tiered = score_floor_layout(rects, dims, planner_checks=True, room_max={name: tier_max})
    assert any(v.kind == "oversized" for v in std.violations)
    assert not any(v.kind == "oversized" for v in tiered.violations)
    # Metrics: same override map, same answer; no override (flag-off) = old max.
    plot = {"length": side, "width": side, "unit": FT}
    assert name in measure_floor(rects, plot)["oversized"]
    assert name not in measure_floor(rects, plot, max_overrides={name: tier_max})["oversized"]


# ---------------------------------------------------------------- bonus rooms


def _bed_bath(rooms):
    cats = [rs.classify_room_category(r["name"]) for r in rooms]
    return cats.count("bedroom"), cats.count("bathroom")


@pytest.mark.parametrize("tier", rules.SIZE_TIERS)
def test_bonus_rooms_never_bedrooms_or_bathrooms(tier):
    for entry in rules.BONUS_ROOM_PRIORITY_BY_TIER[tier]:
        assert rs.classify_room_category(entry["name"]) == entry["category"]
        assert entry["category"] not in ("bedroom", "bathroom")
    for f in (1, 2):
        for b in _eligible_bonus_rooms(_typical(), f, tier=tier):
            assert rs.classify_room_category(b["name"]) not in ("bedroom", "bathroom")


def test_bigger_tiers_offer_more_bonus_rooms():
    n = {t: len(_eligible_bonus_rooms(_typical(), 1, tier=t)) for t in rules.SIZE_TIERS}
    assert n["standard"] <= n["spacious"] <= n["estate"]
    assert n["estate"] > n["standard"]


@pytest.mark.parametrize("plot", [(150, 150), (300, 300), (150, 200)])
def test_planner_added_rooms_are_marked_and_keep_bed_bath_counts(plot):
    floors = [_typical(), UPPER_2F]
    plan = plan_building(floors, _plot(*plot), "south")
    for rooms, fp in zip(floors, plan.floors):
        assert all(a["planner_added"] and a["added_reason"] for a in fp.added_rooms)
        used = rooms + fp.added_rooms
        assert _bed_bath(used) == _bed_bath(rooms)


# ---------------------------------------------------------------- porch / verandah


def test_verandah_absorbs_remainder_on_big_plots_only():
    small = plan_building([_typical()], _plot(60, 80), "south")
    assert small.porch_area == 0.0
    assert not any(a["name"] == rules.GROUND_FLOOR_EXCESS_FALLBACK for a in small.floors[0].added_rooms)
    big = plan_building([_typical()], _plot(150, 150), "south")
    assert big.porch_area > 0
    fp = big.floors[0]
    porch = [a for a in fp.added_rooms if a["name"] == rules.GROUND_FLOOR_EXCESS_FALLBACK]
    assert porch and porch[0]["open_area"] and porch[0]["added_reason"] == ADDED_REASON_PORCH
    assert rules.GROUND_FLOOR_EXCESS_FALLBACK in fp.remainder_rooms
    assert rs.classify_room_category(rules.GROUND_FLOOR_EXCESS_FALLBACK) == "porch"
    assert fp.room_targets[rules.GROUND_FLOOR_EXCESS_FALLBACK] >= big.porch_area - 1e-6


def test_planner_layout_with_verandah_tiles_exactly(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    plot = _plot(150, 150)
    plan, results = floor_layout_mask.layout_building([_typical()], plot, "south", None, 1)
    _used, rects, _w = results[0]
    names = [r["name"] for r in rects]
    assert rules.GROUND_FLOOR_EXCESS_FALLBACK in names
    m = measure_floor(rects, plot)
    assert m["coverage_gap_fraction"] <= 0.01 and m["overlap_area"] <= 1e-3
    covered = sum(_area(r) for r in rects)
    assert covered == pytest.approx(plan.footprint_area, rel=0.01)
    assert plan.floors[0].layout_path in ("rectified", "reference_reconnect", "reference_raw")


# ---------------------------------------------------------------- estate cap


def test_built_area_cap_only_on_large_bands():
    rooms = _typical()  # 3 bedrooms
    assert built_area_cap([rooms], "M", FT) is None
    assert built_area_cap([rooms], "L", FT) == pytest.approx(3 * rules.BUILT_SQFT_PER_BEDROOM_CAP)
    assert built_area_cap([rooms], "XL", FT) == pytest.approx(3 * rules.BUILT_SQFT_PER_BEDROOM_CAP)
    assert built_area_cap([[{"name": "Kitchen"}]], "XL", FT) is None


@pytest.mark.parametrize("plot", [(150, 150), (150, 200), (300, 300)])
def test_built_area_never_exceeds_cap(plot):
    floors = [_typical(), UPPER_2F]
    plan = plan_building(floors, _plot(*plot), "south")
    assert plan.built_area_cap is not None
    assert plan.footprint_area * len(floors) <= plan.built_area_cap + 1e-6


def test_cap_never_pushes_below_program_min():
    plan = plan_building([SPARSE], _plot(300, 300), "south")
    assert plan.footprint_area >= plan.program_min - 1e-6


# ---------------------------------------------------------------- scorer checks


def _bed(w, h):
    return [{"name": "Bedroom 2", "x": 0, "y": 0, "w": w, "h": h}]


def test_bedroom_aspect_is_opt_in_hard_and_soft():
    dims = {"length": 30, "width": 30, "unit": FT}
    long_bed = _bed(10, 27)  # 2.7:1
    plain = score_floor_layout(long_bed, dims)
    assert not any(v.kind == "bedroom_aspect" for v in plain.violations)
    hard = [v for v in score_floor_layout(long_bed, dims, planner_checks=True).violations if v.kind == "bedroom_aspect"]
    assert hard and hard[0].hard
    soft = [v for v in score_floor_layout(_bed(10, 22), dims, planner_checks=True).violations if v.kind == "bedroom_aspect"]
    assert soft and not soft[0].hard
    assert not any(v.kind == "bedroom_aspect" for v in score_floor_layout(_bed(11, 13), dims, planner_checks=True).violations)


def test_stair_cap_is_opt_in_and_uses_the_constant():
    mn = rs.min_area_for_room("Staircase", FT)
    side = (2.5 * mn) ** 0.5
    rects = [{"name": "Staircase", "x": 0, "y": 0, "w": side, "h": side}]
    dims = {"length": side, "width": side, "unit": FT}
    assert not any(v.kind == "stair_oversized" for v in score_floor_layout(rects, dims).violations)
    v = [v for v in score_floor_layout(rects, dims, planner_checks=True).violations if v.kind == "stair_oversized"]
    assert v and v[0].hard
    ok_side = (1.8 * mn) ** 0.5
    ok = [{"name": "Staircase", "x": 0, "y": 0, "w": ok_side, "h": ok_side}]
    assert not any(v.kind == "stair_oversized" for v in score_floor_layout(
        ok, {"length": ok_side, "width": ok_side, "unit": FT}, planner_checks=True).violations)


def test_flag_off_scoring_identical_for_new_checks():
    """Without planner_checks nothing new is reported (rect / flag-off)."""
    dims = {"length": 30, "width": 30, "unit": FT}
    rects = _bed(10, 27) + [{"name": "Staircase", "x": 10, "y": 0, "w": 20, "h": 27}]
    kinds = {v.kind for v in score_floor_layout(rects, dims).violations}
    assert not kinds & {"bedroom_aspect", "stair_oversized"}


def test_metrics_and_scorecard_report_aspect_and_stair():
    mn = rs.min_area_for_room("Staircase", FT)
    rects = [
        {"name": "Bedroom 2", "x": 0, "y": 0, "w": 10, "h": 30},
        {"name": "Staircase", "x": 10, "y": 0, "w": 10, "h": 30},
    ]
    assert 300 > 2 * mn
    plot = {"length": 20, "width": 30, "unit": FT}
    m = measure_floor(rects, plot)
    assert m["bedroom_aspect_fail"] == ["Bedroom 2"]
    assert m["stair_over_cap"] == ["Staircase"]
    res = score_floor(rects, plot, "south", [[{"name": "Bedroom 2"}, {"name": "Staircase"}]], 1)
    assert not res["aspect"]["pass"] and not res["stair_cap"]["pass"]
    assert not res["clean"]
    text = format_scorecard({"x/floor1": res}, "t", None, {"x/floor1": "reference_raw"})
    # Wording since a3d5fc8 (every path kind counted and summed).
    assert "Floors using the Phase 2 fallback (reference_reconnect, reference_raw): 1/1" in text
    assert "reference_raw 1" in text and "[sum 1]" in text
    assert "6 legacy checks" in text


def test_scorecard_remainder_names_and_tier_bath_cap():
    # A 95 sq ft bath fails the standard 80 cap but passes the estate cap
    # (planner mode on an XL plot only).
    rects = [{"name": "Bathroom 1", "x": 100, "y": 100, "w": 9.5, "h": 10}]
    plot = _plot(300, 300)
    floors = [[{"name": "Bathroom 1"}]]
    off = score_floor(rects, plot, "south", floors, 1)
    on = score_floor(rects, plot, "south", floors, 1, planner=True)
    assert off["sizes"]["big_baths"] and not on["sizes"]["big_baths"]
    # remainder_names decides what is excluded, not every override key.
    rects2 = [{"name": "Living Room", "x": 0, "y": 0, "w": 30, "h": 30}]
    res = score_floor(rects2, plot, "south", [[{"name": "Living Room"}]], 1,
                      max_overrides={"Living Room": 100.0}, remainder_names=[], planner=True)
    assert "Living Room" in res["sizes"]["oversized"]


# ---------------------------------------------------------------- diagnostics


def test_fallback_diagnostic_is_recorded(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    plan, _results = floor_layout_mask.layout_building([_typical()], _plot(40, 60), "south", None, 1)
    d = floor_layout_mask.LAST_FLOOR_DIAGNOSTICS
    assert d["path"] in ("rectified", "reference_reconnect", "reference_raw")
    assert d["grown"] >= d["rectified"] >= 1 and d["runs"] >= 1 and d["seconds"] >= 0
    assert plan.floors[0].layout_path == d["path"]


def test_stair_is_not_the_last_resort_sink():
    # One leftover cell bordered by a staircase (zone 1, at max) and a
    # bedroom (zone 2, also at max): the pocket must go to the bedroom.
    owner = [[0, -1, 1]]
    cells = [{(0, 0)}, {(2, 0)}]
    stats: dict = {}
    floor_layout_mask._fill_leftovers_bounded(owner, cells, [1, 1], [1, 1], 3, 1, set(), [1, 2], 0, stats)
    assert owner[0][1] == 1 and cells[0] == {(0, 0)}
