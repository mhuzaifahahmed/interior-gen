from app.config import settings
from app.pipeline.feasibility import (
    _geometrically_placeable_area,
    _realistic_other_count,
    check_feasibility,
    min_area_for_room_count,
    room_count_requirements_table,
)
from app.pipeline.room_specs import EXTERIOR_WALL_THICKNESS_M, to_plot_unit


def _ext(unit="ft"):
    """Real exterior-wall offset (2026-10 net-envelope refinement) -
    check_feasibility() now compares against the real NET buildable
    envelope, (length - 2*ext) x (width - 2*ext), not the raw gross plot."""
    return to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)


def test_check_feasibility_returns_feasible_for_a_generous_plot():
    rooms = [
        {"name": "Living Room", "area": 2},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom 1", "area": 1.5},
        {"name": "Bathroom", "area": 0.5},
    ]
    result = check_feasibility(rooms, {"length": 60, "width": 60, "unit": "ft"}, total_floors=1)
    assert result["verdict"] == "feasible"
    assert result["explanation"] is None


def test_check_feasibility_returns_not_feasible_for_a_tiny_plot_with_many_rooms():
    rooms = [
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bedroom 3", "area": 1},
        {"name": "Bathroom 1", "area": 1},
        {"name": "Bathroom 2", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Living Room", "area": 1},
        {"name": "Garage", "area": 1},
    ]
    result = check_feasibility(rooms, {"length": 15, "width": 15, "unit": "ft"}, total_floors=2)
    assert result["verdict"] == "not_feasible"
    assert result["explanation"] is not None
    assert "sq ft" in result["explanation"]


def test_check_feasibility_returns_tight_near_the_boundary():
    rooms = [
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bathroom", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Living Room", "area": 1},
    ]
    # Derive required_area from a generously-sized plot first (still returned
    # even when feasible), then construct a plot whose area sits inside the
    # "tight" band (required_area, required_area / 0.85] - avoids hardcoding
    # a plot size that would silently go stale if the room-size constants
    # ever change.
    width = 30
    ext = _ext()
    probe = check_feasibility(rooms, {"length": 1000, "width": width, "unit": "ft"}, total_floors=1)
    required_area = probe["required_area"]
    target_net_available = required_area * 1.05
    net_width = width - 2 * ext
    net_length = target_net_available / net_width
    length = net_length + 2 * ext

    result = check_feasibility(rooms, {"length": length, "width": width, "unit": "ft"}, total_floors=1)
    assert result["verdict"] == "tight"
    assert result["explanation"] is not None


def test_check_feasibility_accounts_for_garage_car_count():
    rooms = [{"name": "Garage", "area": 1}, {"name": "Living Room", "area": 1}]
    one_car = check_feasibility(rooms, {"length": 20, "width": 20, "unit": "ft"}, total_floors=1, garage_cars=1)
    three_car = check_feasibility(rooms, {"length": 20, "width": 20, "unit": "ft"}, total_floors=1, garage_cars=3)
    assert three_car["required_area"] > one_car["required_area"]


def test_check_feasibility_adds_staircase_overhead_for_multi_floor():
    rooms = [{"name": "Bedroom", "area": 1}]
    single_floor = check_feasibility(rooms, {"length": 30, "width": 30, "unit": "ft"}, total_floors=1)
    multi_floor = check_feasibility(rooms, {"length": 30, "width": 30, "unit": "ft"}, total_floors=2)
    assert multi_floor["required_area"] > single_floor["required_area"]


def test_check_feasibility_handles_no_rooms():
    result = check_feasibility([], {"length": 40, "width": 60, "unit": "ft"}, total_floors=1)
    assert result["verdict"] == "feasible"


# ---- min_area_for_room_count() / room_count_requirements_table() - real- ----
# ---- time "does N bedrooms fit my plot" guidance for the Build a House  ----
# ---- form (2026-09-30, explicit user request).                         ----


def test_min_area_for_room_count_increases_with_more_bedrooms():
    zero = min_area_for_room_count("bedroom", 0, 1, total_floors=1, unit="ft")
    five = min_area_for_room_count("bedroom", 5, 1, total_floors=1, unit="ft")
    assert five > zero


def test_min_area_for_room_count_increases_with_more_bathrooms():
    zero = min_area_for_room_count("bathroom", 0, 1, total_floors=1, unit="ft")
    five = min_area_for_room_count("bathroom", 5, 1, total_floors=1, unit="ft")
    assert five > zero


def test_min_area_for_room_count_includes_staircase_overhead_for_multi_floor():
    single = min_area_for_room_count("bedroom", 2, 1, total_floors=1, unit="ft")
    multi = min_area_for_room_count("bedroom", 2, 1, total_floors=2, unit="ft")
    assert multi > single


def test_min_area_for_room_count_agrees_with_check_feasibility():
    # The real guarantee: this must never disagree with the actual hard
    # gate about what "fits" means - both use the identical underlying
    # math, so a plot sized exactly at min_area_for_room_count()'s own
    # number must come back "feasible" or "tight", never "not_feasible".
    required = min_area_for_room_count("bedroom", 3, 1, total_floors=2, unit="ft")
    net_side = required**0.5
    side = net_side + 2 * _ext()
    rooms = [
        {"name": "Living Room", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Dining Room", "area": 1},
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bedroom 3", "area": 1},
        {"name": "Bathroom 1", "area": 1},
        {"name": "Staircase", "area": 1},
    ]
    result = check_feasibility(rooms, {"length": side, "width": side, "unit": "ft"}, total_floors=2)
    assert result["verdict"] in ("feasible", "tight")


def test_room_count_requirements_table_covers_the_full_range():
    # 2026-10 refinement: BEDROOM_COUNT_RANGE/BATHROOM_COUNT_RANGE (0-8/0-6)
    # are now only an upper CEILING, not a guaranteed table length - each
    # count is also checked GEOMETRICALLY (does the real layout engine
    # actually produce a placeable layout, not just "the areas sum to less
    # than the plot"), and the table stops at the first count where not even
    # the most generous search margin finds one. A table that only ever
    # advertised an arbitrary ceiling, disconnected from real practicality,
    # was a direct contributor to the "50x50 fits 15 bedrooms" bug - so the
    # real guarantees here are: non-empty, starts at 0, never exceeds the
    # ceiling, every entry has a real positive area, and the areas are
    # MONOTONIC (more rooms never need less space - a real bug found and
    # fixed during this refinement, where the geometry search could
    # otherwise report a smaller area for a bigger count purely as an
    # artifact of where a suite-pairing split happened to land).
    table = room_count_requirements_table(total_floors=1, unit="ft")
    assert table["bedrooms"][0]["count"] == 0
    assert table["bathrooms"][0]["count"] == 0
    assert 1 <= len(table["bedrooms"]) <= 9
    assert 1 <= len(table["bathrooms"]) <= 7
    for rows in (table["bedrooms"], table["bathrooms"]):
        assert [row["count"] for row in rows] == list(range(len(rows)))
        areas = [row["min_area"] for row in rows]
        assert areas == sorted(areas)
        for row in rows:
            assert row["min_area"] > 0
            assert row["min_side"] > 0


def test_room_count_requirements_table_min_side_is_sqrt_of_min_area():
    table = room_count_requirements_table(total_floors=1, unit="ft")
    row = table["bedrooms"][-1]
    assert abs(row["min_side"] - row["min_area"] ** 0.5) < 0.1


def test_headline_regression_50x50_plot_no_longer_fits_15_bedrooms():
    # The exact real, reported bug: a 50x50ft (2,500 sq ft) single floor's
    # capacity hint said "fits up to 8 bedrooms and 6 bathrooms" (an earlier
    # pass of this same refinement had already brought it down from an
    # original 15/10, but that version only checked AREA, never whether the
    # rooms could actually be PLACED as real, usable shapes). The honest,
    # geometry-verified math must report a number close to the real-world
    # norm for a house this size (~3-4 bedrooms/~2-3 bathrooms per published
    # house-plan data - see the plan this refinement was built from), not
    # just "smaller than before."
    table = room_count_requirements_table(total_floors=1, unit="ft")
    area = 50 * 50

    def max_fit(rows):
        best = 0
        for row in rows:
            if row["min_area"] <= area:
                best = row["count"]
            else:
                break
        return best

    bedrooms_fit = max_fit(table["bedrooms"])
    bathrooms_fit = max_fit(table["bathrooms"])
    assert 1 <= bedrooms_fit <= 5
    assert 1 <= bathrooms_fit <= 4


# ---- Realistic bedroom<->bathroom coupling + the real geometry-placeability ----
# ---- search (2026-10, Part 2 of the "practical layouts" refinement) ----


def test_realistic_other_count_scales_bathrooms_with_bedrooms():
    # Roughly 1 bathroom per 2 bedrooms, never fewer than 1 - replaces the
    # old flat baseline of 1 (which meant testing "does 8 bedrooms fit" only
    # ever reserved space for ONE bathroom alongside them).
    assert _realistic_other_count("bedroom", 0) == 1
    assert _realistic_other_count("bedroom", 1) == 1
    assert _realistic_other_count("bedroom", 2) == 1
    assert _realistic_other_count("bedroom", 3) == 2
    assert _realistic_other_count("bedroom", 4) == 2
    assert _realistic_other_count("bedroom", 8) == 4


def test_realistic_other_count_scales_bedrooms_with_bathrooms():
    assert _realistic_other_count("bathroom", 0) == 1
    assert _realistic_other_count("bathroom", 1) == 2
    assert _realistic_other_count("bathroom", 3) == 6


def test_min_area_for_room_count_reserves_a_real_foyer():
    # 2026-10: a real Entry/foyer is now always reserved alongside Living/
    # Kitchen/Dining - previously omitted entirely, understating the real
    # fixed cost of a house's always-present rooms. Confirmed indirectly:
    # the reported area for 0 bedrooms must exceed just Living+Kitchen+
    # Dining's own combined preferred area (a real foyer adds real area on
    # top of those three).
    from app.pipeline.room_specs import preferred_area_for_room

    area = min_area_for_room_count("bedroom", 0, 1, total_floors=1, unit="ft")
    public_only = sum(preferred_area_for_room(n, "ft") for n in ("Living Room", "Kitchen", "Dining Room"))
    assert area > public_only


def test_geometrically_placeable_area_finds_a_working_size_for_a_modest_count():
    # A small, reasonable room count must find SOME working margin (the
    # function searches _GEOMETRY_CHECK_MARGINS and returns the smallest
    # that works) - never None for a case this modest.
    other = _realistic_other_count("bedroom", 1)
    required = min_area_for_room_count("bedroom", 1, other, total_floors=1, unit="ft")
    placeable_area = _geometrically_placeable_area("bedroom", 1, other, 1, "ft", required)
    assert placeable_area is not None
    assert placeable_area >= required


def test_room_count_requirements_table_areas_are_strictly_monotonic_across_floor_counts():
    # A 2-floor table's ground-floor program carries extra overhead (a real
    # Staircase room) on top of the 1-floor table's - every entry should be
    # at least as large, and the headline bug's own fix (foyer + realistic
    # bathroom coupling + geometric verification) must never regress back
    # to a non-monotonic table (a real bug found and fixed during this
    # refinement - see room_count_requirements_table()'s own docstring).
    one_floor = room_count_requirements_table(total_floors=1, unit="ft")
    two_floor = room_count_requirements_table(total_floors=2, unit="ft")
    for rows in (one_floor["bedrooms"], one_floor["bathrooms"], two_floor["bedrooms"], two_floor["bathrooms"]):
        areas = [row["min_area"] for row in rows]
        assert areas == sorted(areas)


# ---- lru_cache is NOT engine-aware - a real staleness risk flagged in the ----
# ---- mask-engine-adoption plan (Phase B), proven + guarded here ----


def test_room_count_requirements_table_cache_is_engine_aware_when_cleared(monkeypatch):
    """room_count_requirements_table()'s @functools.lru_cache key is purely
    (total_floors, unit) - settings.house_layout_engine is NOT part of the
    key, even though the function's body calls the layout-engine dispatcher
    (via _geometrically_placeable_area -> layout_floor_with_warnings) and
    its result genuinely depends on which engine is active. So toggling the
    engine mid-process (exactly what a test monkeypatch does) and calling
    this function again with the SAME (total_floors, unit) args returns a
    STALE table computed under the OLD engine, not a fresh one - unless the
    caller explicitly clears the cache right after switching.

    This test proves both halves directly: (1) the staleness is real -
    without clearing, the identical cached dict object is returned even
    after switching engines; (2) calling .cache_clear() right after the
    switch actually fixes it - a fresh table is computed.

    conftest.py's autouse _default_house_layout_engine fixture clears this
    cache at both setup and teardown specifically to bound this leakage to
    within a single test's own body - it can't help WITHIN a test that
    itself toggles the engine, which is exactly why this test (and any
    future test doing the same) must call .cache_clear() itself between the
    two calls, as done below."""
    room_count_requirements_table.cache_clear()
    rect_table = room_count_requirements_table(total_floors=1, unit="ft")
    assert settings.house_layout_engine == "rect"  # conftest's autouse fixture

    monkeypatch.setattr(settings, "house_layout_engine", "mask")
    # Without a cache_clear(), the SAME cached object comes back even though
    # the engine just changed - this is the real staleness bug.
    stale_table = room_count_requirements_table(total_floors=1, unit="ft")
    assert stale_table is rect_table

    room_count_requirements_table.cache_clear()
    fresh_mask_table = room_count_requirements_table(total_floors=1, unit="ft")
    assert fresh_mask_table is not rect_table

    # Leave the cache clean so a later test in this same process never
    # inherits this test's mask-flavored (1, "ft") entry - mirrors what the
    # conftest fixture's own teardown already does, belt-and-suspenders.
    room_count_requirements_table.cache_clear()


def test_single_floor_never_counts_a_staircase_room():
    from app.pipeline.feasibility import check_feasibility

    dims = {"length": 40, "width": 40, "unit": "ft"}
    base = [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}]
    with_stair = base + [{"name": "Staircase", "area": 1}]
    a = check_feasibility(base, dims, 1)
    b = check_feasibility(with_stair, dims, 1)
    assert a["required_area"] == b["required_area"]
    # ...but a 2-floor house does pay for it.
    assert check_feasibility(base, dims, 2)["required_area"] > a["required_area"]


def test_capacity_table_is_not_zero_when_the_layout_probe_never_reaches_zero_violations(monkeypatch):
    # Regression (demo bug 2026-10-17): the geometry probe needs a layout with ZERO
    # hard violations from the ACTIVE engine. The mask engine (live default) almost
    # never gets there, so the probe failed at ONE room and the table reported
    # "0 bedrooms / 0 bathrooms fit" for every plot - the dropdown offered only "1".
    import app.pipeline.feasibility as feas
    from app.config import settings

    monkeypatch.setattr(settings, "house_layout_engine", "mask")
    monkeypatch.setattr(feas, "_geometrically_placeable_area", lambda *a, **k: None)
    feas.room_count_requirements_table.cache_clear()
    try:
        t = feas.room_count_requirements_table(2, "ft")
    finally:
        feas.room_count_requirements_table.cache_clear()

    assert max(e["count"] for e in t["bedrooms"]) >= 6
    assert max(e["count"] for e in t["bathrooms"]) >= 4
    # Area still grows with the count, so a small plot is still limited.
    areas = [e["min_area"] for e in t["bedrooms"]]
    assert areas == sorted(areas) and areas[-1] > areas[1]


def test_capacity_table_still_stops_at_a_real_geometric_limit(monkeypatch):
    # The fallback must NOT hide a genuine limit: if the probe works for small
    # counts and fails for a larger one, the table still stops there.
    import app.pipeline.feasibility as feas

    def probe(category, count, other, floors, unit, area):
        return area * 1.2 if count <= 3 else None

    monkeypatch.setattr(feas, "_geometrically_placeable_area", probe)
    feas.room_count_requirements_table.cache_clear()
    try:
        t = feas.room_count_requirements_table(1, "ft")
    finally:
        feas.room_count_requirements_table.cache_clear()
    assert max(e["count"] for e in t["bedrooms"]) == 3
    assert max(e["count"] for e in t["bathrooms"]) == 3
