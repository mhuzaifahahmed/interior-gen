"""Tests for the grid-mask region-growing layout engine
(app/pipeline/floor_layout_mask.py) - the real, GUARANTEED invariants of
this engine, not a claim that every room program comes out violation-free
(it doesn't yet - see the module's own "Known simplifications" and the
plan file's milestone roadmap). What IS guaranteed and tested here:
exact tiling (no holes/overlaps - a hole would render as an unexplained
solid black poché block), determinism, correct facing/egress behavior by
construction, and that the engine never crashes on a range of real room
programs."""

import pytest

from app.pipeline.floor_layout import footprint_bbox, room_parts
from app.pipeline.floor_layout_mask import (
    AUTO_YARD_PLOT_THRESHOLD_M,
    MAX_PLOT_COVERAGE,
    MIN_FRONT_SETBACK_M,
    _mask_footprint_dimensions,
    layout_floor,
    layout_floor_with_warnings,
)
from app.pipeline.room_specs import classify_room_category, min_area_for_room, to_plot_unit

# Deliberately NOT a clean divisor of the engine's own internal grid cell
# size (e.g. 0.1 divides evenly into a common 1.5ft cell size, which makes
# round() numerically unstable exactly AT shared part boundaries - a real
# false-positive "hole" found during development that was pure sampling-
# grid aliasing, not an actual gap, confirmed by re-checking with several
# other sample sizes that all agreed there was no real hole). A deliberately
# irregular value avoids ever landing exactly on a boundary.
_SAMPLE_CELL = 0.1234567


def _tiles_exactly(rects: list[dict]) -> tuple[bool, bool]:
    """Returns (no_holes, no_overlaps) by sampling the footprint bbox at a
    fine grid and checking every sampled point is covered by exactly one
    part."""
    fp = footprint_bbox(rects)
    if fp is None:
        return True, True
    fp_x0, fp_y0, fp_x1, fp_y1 = fp
    covered: dict[tuple[int, int], int] = {}
    for rect in rects:
        for part in room_parts(rect):
            x0, y0 = part["x"], part["y"]
            x1, y1 = x0 + part["w"], y0 + part["h"]
            xi0 = round(x0 / _SAMPLE_CELL)
            xi1 = round(x1 / _SAMPLE_CELL)
            yi0 = round(y0 / _SAMPLE_CELL)
            yi1 = round(y1 / _SAMPLE_CELL)
            for xi in range(xi0, xi1):
                for yi in range(yi0, yi1):
                    key = (xi, yi)
                    covered[key] = covered.get(key, 0) + 1

    no_overlaps = all(count == 1 for count in covered.values())

    expected_cells = round((fp_x1 - fp_x0) / _SAMPLE_CELL) * round((fp_y1 - fp_y0) / _SAMPLE_CELL)
    no_holes = len(covered) == expected_cells
    return no_holes, no_overlaps


_SIX_ROOM_PROGRAM = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 2},
    {"name": "Dining Room", "area": 2},
    {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1.5},
    {"name": "Bathroom 2", "area": 0.8},
]


def test_layout_floor_mask_tiles_exactly_with_no_holes_or_overlaps():
    rects = layout_floor(_SIX_ROOM_PROGRAM, {"length": 60, "width": 50, "unit": "ft"}, facing="south")
    no_holes, no_overlaps = _tiles_exactly(rects)
    assert no_holes
    assert no_overlaps


def test_layout_floor_mask_tiles_exactly_across_all_four_facings():
    for facing in ("north", "south", "east", "west"):
        rects = layout_floor(_SIX_ROOM_PROGRAM, {"length": 60, "width": 50, "unit": "ft"}, facing=facing)
        no_holes, no_overlaps = _tiles_exactly(rects)
        assert no_holes, f"holes found for facing={facing}"
        assert no_overlaps, f"overlaps found for facing={facing}"


def test_layout_floor_mask_is_deterministic():
    dims = {"length": 60, "width": 50, "unit": "ft"}
    first = layout_floor(_SIX_ROOM_PROGRAM, dims, facing="south")
    second = layout_floor(_SIX_ROOM_PROGRAM, dims, facing="south")
    assert first == second


def test_layout_floor_mask_every_room_present_and_has_at_least_one_part():
    rects = layout_floor(_SIX_ROOM_PROGRAM, {"length": 60, "width": 50, "unit": "ft"}, facing="south")
    names = {r["name"] for r in rects}
    for room in _SIX_ROOM_PROGRAM:
        assert room["name"] in names
    for rect in rects:
        parts = room_parts(rect)
        assert len(parts) >= 1
        for part in parts:
            assert part["w"] > 0
            assert part["h"] > 0


def test_layout_floor_mask_bbox_equals_true_bbox_of_parts():
    """The invariant floor_layout.room_parts()/footprint_bbox() depend on:
    a room's own top-level x/y/w/h must always equal the real bounding box
    of its parts, even for a multi-part L/U room."""
    rects = layout_floor(_SIX_ROOM_PROGRAM, {"length": 60, "width": 50, "unit": "ft"}, facing="south")
    for rect in rects:
        parts = room_parts(rect)
        min_x = min(p["x"] for p in parts)
        min_y = min(p["y"] for p in parts)
        max_x = max(p["x"] + p["w"] for p in parts)
        max_y = max(p["y"] + p["h"] for p in parts)
        assert abs(rect["x"] - min_x) < 1e-6
        assert abs(rect["y"] - min_y) < 1e-6
        assert abs(rect["w"] - (max_x - min_x)) < 1e-6
        assert abs(rect["h"] - (max_y - min_y)) < 1e-6


def test_layout_floor_mask_egress_rooms_touch_the_exterior_boundary():
    """The real, headline guarantee this engine was built for: a bedroom/
    living room's seed cell is placed ON the exterior boundary and growth
    is always connected, so at least one of its parts must touch the real
    footprint boundary - this is what fixes the original reported bug (a
    landlocked master bedroom with no possible window wall)."""
    rects = layout_floor(_SIX_ROOM_PROGRAM, {"length": 60, "width": 50, "unit": "ft"}, facing="south")
    fp = footprint_bbox(rects)
    fp_x0, fp_y0, fp_x1, fp_y1 = fp
    tol = 1e-6
    for rect in rects:
        if classify_room_category(rect["name"]) not in ("bedroom", "living"):
            continue
        touches = any(
            abs(p["x"] - fp_x0) < tol
            or abs(p["y"] - fp_y0) < tol
            or abs((p["x"] + p["w"]) - fp_x1) < tol
            or abs((p["y"] + p["h"]) - fp_y1) < tol
            for p in room_parts(rect)
        )
        assert touches, f"{rect['name']} has no part touching the exterior boundary"


def test_layout_floor_mask_public_zone_lands_on_the_facing_edge():
    """A plain sanity check that facing is actually honored: the public
    rooms (living/kitchen/dining/entry) should sit on the LOW-y edge for
    north, HIGH-y for south, etc. - checked via Entry specifically, since
    it's always placed in the public zone."""
    dims = {"length": 60, "width": 50, "unit": "ft"}
    for facing, edge_check in (
        ("north", lambda fp, r: abs(r["y"] - fp[1]) < 1.0),
        ("south", lambda fp, r: abs((r["y"] + r["h"]) - fp[3]) < 1.0),
        ("west", lambda fp, r: abs(r["x"] - fp[0]) < 1.0),
        ("east", lambda fp, r: abs((r["x"] + r["w"]) - fp[2]) < 1.0),
    ):
        rects = layout_floor(_SIX_ROOM_PROGRAM, dims, facing=facing)
        fp = footprint_bbox(rects)
        entry = next(r for r in rects if r["name"] == "Entry")
        parts = room_parts(entry)
        assert any(edge_check(fp, p) for p in parts), f"Entry not on facing edge for facing={facing}"


def test_layout_floor_mask_handles_a_minimal_single_room():
    rects = layout_floor([{"name": "Studio", "area": 1}], {"length": 20, "width": 20, "unit": "ft"})
    assert len(rects) == 1
    no_holes, no_overlaps = _tiles_exactly(rects)
    assert no_holes
    assert no_overlaps


def test_layout_floor_mask_handles_a_sparse_two_room_program():
    """A sparse program (below _MIN_ROOMS_FOR_SPINE, no circulation spine
    reserved at all) is a real, documented edge case - this just confirms
    it still tiles exactly and doesn't crash, not that it's violation-free
    (see floor_layout_mask.py's own "Known simplifications")."""
    rooms = [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}]
    rects = layout_floor(rooms, {"length": 30, "width": 25, "unit": "ft"}, facing="south")
    no_holes, no_overlaps = _tiles_exactly(rects)
    assert no_holes
    assert no_overlaps


def test_layout_floor_mask_handles_a_dense_multi_suite_program():
    """A dense, 14-room, multi-suite program on a large plot - the kind of
    program that stress-tested this engine the hardest during development.
    Confirms it completes, tiles exactly, and every room gets real area -
    not a claim of zero soft/hard layout_quality violations."""
    rooms = _SIX_ROOM_PROGRAM + [
        {"name": "Bedroom 3", "area": 1.5},
        {"name": "Bathroom 3", "area": 0.8},
        {"name": "Bedroom 4", "area": 1.5},
        {"name": "Bedroom 5", "area": 1.5},
        {"name": "Bedroom 6", "area": 1.5},
        {"name": "Laundry", "area": 0.5},
    ]
    rects = layout_floor(rooms, {"length": 150, "width": 150, "unit": "ft"}, facing="south")
    names = {r["name"] for r in rects}
    for room in rooms:
        assert room["name"] in names
    no_holes, no_overlaps = _tiles_exactly(rects)
    assert no_holes
    assert no_overlaps


def test_layout_floor_mask_deliberately_large_open_room_has_real_area():
    """A deliberately large, open-plan living room (the structural-column
    stress case) - confirms the engine handles a heavily weighted single
    room without crashing and gives it real, substantial area."""
    rooms = [
        {"name": "Living Room", "area": 6},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom", "area": 1.5},
    ]
    rects = layout_floor(rooms, {"length": 70, "width": 30, "unit": "ft"}, facing="south")
    living = next(r for r in rects if r["name"] == "Living Room")
    total_area = sum(p["w"] * p["h"] for p in room_parts(living))
    assert total_area > 150  # a real, substantial room, not a sliver


def test_layout_floor_with_warnings_mask_returns_a_list_of_violations():
    rects, warnings = layout_floor_with_warnings(
        _SIX_ROOM_PROGRAM, {"length": 60, "width": 50, "unit": "ft"}, facing="south"
    )
    assert isinstance(rects, list)
    assert isinstance(warnings, list)


def test_layout_floor_mask_empty_rooms_returns_empty():
    rects = layout_floor([], {"length": 40, "width": 40, "unit": "ft"})
    assert rects == []


# ---------------------------------------------------------------------------
# Phase A2 regression tests (2026-10) - the exact two bugs found during
# Phase A's visual baseline inspection (future-plans/
# mask-adoption-phase-a-findings.md), fixed in floor_layout_mask.py's
# _fill_leftovers()/_seed_rooms()/_grow_rooms(). Both reproduce
# deterministically with the EXACT room lists/facings recorded in that
# findings doc - kept here verbatim so a future regression is caught by a
# real, known-bad case, not a synthetic one.
# ---------------------------------------------------------------------------

_BUG_A_ROOM_LIST = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2},
    {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1},
    {"name": "Bedroom 3", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
_BUG_A_DIMENSIONS = {"length": 50.0, "width": 60.0, "unit": "ft"}


def _total_area(rect: dict) -> float:
    return sum(p["w"] * p["h"] for p in room_parts(rect))


def test_layout_floor_mask_bug_a_black_hole_full_coverage_south():
    """Phase A2 Bug A - the 50x60/3-bed/2-bath/LKD/entry room list used to
    render a literal unexplained solid black hole inside the house's own
    footprint (an unclaimed grid cell trapped by the cross-link circulation
    spine, with no real room neighbor - see floor_layout_mask.py's
    _fill_leftovers() docstring). Asserts the real, exact SUM of every
    returned room's own parts equals the footprint bbox's own area - this
    is the same invariant layout_quality.py's new coverage check guards in
    the real pipeline (see test_score_floor_layout_flags_an_artificially_
    introduced_coverage_gap below), computed directly here rather than via
    the sampling-grid _tiles_exactly() helper - a real false-positive
    "hole" from pure sampling-grid aliasing at this exact geometry was
    found while writing this test (confirmed by comparing against this
    exact-arithmetic check, which agreed there was no real gap) - the
    module docstring above already flags that sampling aliasing risk for
    _tiles_exactly(), so an exact area-sum comparison is the more reliable
    choice for a hole-sensitive regression test specifically."""
    for facing in ("south", "north", "east", "west"):
        rects = layout_floor(_BUG_A_ROOM_LIST, _BUG_A_DIMENSIONS, facing=facing)
        fp = footprint_bbox(rects)
        assert fp is not None
        fp_area = (fp[2] - fp[0]) * (fp[3] - fp[1])
        covered = sum(_total_area(r) for r in rects)
        assert covered == pytest.approx(fp_area, rel=1e-6), (
            f"facing={facing}: covered={covered:.2f} vs footprint={fp_area:.2f} "
            "- a region inside the footprint was never claimed by any room"
        )
        no_holes, no_overlaps = _tiles_exactly(rects)
        assert no_overlaps, f"facing={facing}"


def test_layout_floor_mask_bug_b_master_bedroom_not_collapsed_east_west():
    """Phase A2 Bug B - the SAME room list collapsed Master Bedroom to a
    ~1.1ft-wide, ~20 sq ft single-rectangle sliver (aspect ~17, real min
    area 135 sq ft) specifically under east/west facing - a room boxed in
    by an already-grown neighbor on one side and the circulation spine on
    the other, with zero free cells left for either whole-strip growth or
    the free-cell arm fallback to claim (see _seed_rooms()'s and
    _grow_rooms()'s own "Bug B" docstrings). Asserts Master Bedroom's real
    TOTAL area (summed across parts, not just its bbox) now reaches a
    substantial fraction of its own real minimum - the actual guarantee
    Bug B was about (a room's usable area/shape collapsing to near-
    nothing), not a blanket ban on any room ever having a thin attached
    leftover part (that's a separate, already-documented, accepted M1
    engine limitation - see _fill_leftovers()'s own docstring - not what
    this bug was)."""
    for facing in ("east", "west"):
        rects = layout_floor(_BUG_A_ROOM_LIST, _BUG_A_DIMENSIONS, facing=facing)
        master = next(r for r in rects if r["name"] == "Master Bedroom")
        min_area = min_area_for_room("Master Bedroom", "ft")
        area = _total_area(master)
        assert area >= min_area * 0.9, (
            f"facing={facing}: Master Bedroom real area {area:.1f} vs min {min_area:.1f} "
            "looks collapsed"
        )


def test_layout_floor_mask_bug_b_upper_floor_bedroom_not_collapsed():
    """Phase A2 Bug B, second reproduction - the 150x150/2-floor case's
    UPPER floor (bedrooms + staircase + sitting area, no facing swap
    involved) produced a 'Bedroom 5' whose real area (59.5 sq ft, summed
    across its parts) was well under its own 100 sq ft minimum, with a
    governing width of just 0.8ft from a thin leftover-glued part - even
    though its bbox alone looked reasonable (12.9x9.1ft). Asserts its real
    total area now reaches a substantial fraction of its own minimum."""
    dimensions = {"length": 150.0, "width": 150.0, "unit": "ft"}
    rooms = [
        {"name": "Bedroom 4", "area": 1},
        {"name": "Bathroom 3", "area": 1},
        {"name": "Bedroom 5", "area": 1},
        {"name": "Bathroom 4", "area": 1},
        {"name": "Staircase", "area": 1},
        {"name": "Sitting Area", "area": 2},
    ]
    rects = layout_floor(rooms, dimensions, facing="south")
    bedroom5 = next(r for r in rects if r["name"] == "Bedroom 5")
    min_area = min_area_for_room("Bedroom 5", "ft")
    area = _total_area(bedroom5)
    assert area >= min_area * 0.9, f"Bedroom 5 real area {area:.1f} vs min {min_area:.1f} looks collapsed"


def test_score_floor_layout_flags_an_artificially_introduced_coverage_gap():
    """Phase A2.6 - closes the detection gap the Phase A findings called
    out directly: score_floor_layout() never noticed the black-hole bug at
    all, since no RETURNED rect was individually wrong - the bug was an
    UNCLAIMED cell no rect ever accounted for. Simulates that exact shape
    (a valid layout with one room's area artificially shrunk, as if a
    region had gone unclaimed) and confirms the scorer's new coverage
    check (layout_quality._coverage_violation) now catches it as a hard
    violation - a real regression guard for the FIX's own detection half,
    not just the engine fix itself."""
    from app.pipeline.layout_quality import score_floor_layout

    dimensions = {"length": 50.0, "width": 60.0, "unit": "ft"}
    rects = layout_floor(_BUG_A_ROOM_LIST, dimensions, facing="south")
    assert rects
    # Shrink one room's first part well below its real extent - simulates
    # a chunk of the footprint going unclaimed/unexplained.
    rects[0] = {**rects[0], "w": rects[0]["w"] * 0.5, "parts": None}
    rects[0].pop("parts", None)
    scored = score_floor_layout(rects, dimensions, None)
    coverage_violations = [v for v in scored.violations if v.kind == "coverage_gap"]
    assert coverage_violations, "shrinking a room's area should leave an unexplained coverage gap"
    assert all(v.hard for v in coverage_violations)


# ---------------------------------------------------------------------------
# Phase C0 (2026-10) - auto site-planning: a plot-coverage cap + a real
# front setback on a genuinely large plot, both strict no-ops on a small/
# right-sized plot (or a naturally small room program). See floor_layout_
# mask.py's own module-level constants docstring for the full rationale
# (values DECIDED in the plan's §1.5 "RESOLVED DECISIONS").
# ---------------------------------------------------------------------------

_DENSE_BEDROOM_PROGRAM = [{"name": f"Bedroom {i}", "area": 2} for i in range(80)]


def test_mask_footprint_dimensions_coverage_cap_binds_on_a_large_plot_with_a_dense_program():
    unit = "ft"
    net = to_plot_unit(AUTO_YARD_PLOT_THRESHOLD_M, unit) + 60  # comfortably over threshold
    length, width = _mask_footprint_dimensions(_DENSE_BEDROOM_PROGRAM, net, net, unit, None)
    coverage = (length * width) / (net * net)
    assert coverage <= MAX_PLOT_COVERAGE + 1e-6


def test_mask_footprint_dimensions_coverage_cap_is_a_no_op_below_the_threshold():
    # The IDENTICAL dense room program that gets capped above - on a plot
    # just under AUTO_YARD_PLOT_THRESHOLD_M, the cap must never fire at
    # all, matching this engine's behavior before Phase C0 existed.
    unit = "ft"
    net = to_plot_unit(AUTO_YARD_PLOT_THRESHOLD_M, unit) - 5
    length, width = _mask_footprint_dimensions(_DENSE_BEDROOM_PROGRAM, net, net, unit, None)
    assert length == pytest.approx(net)
    assert width == pytest.approx(net)


def test_mask_footprint_dimensions_coverage_cap_is_a_no_op_for_a_sparse_program():
    # A naturally small room program on a large plot - the cap only ever
    # TIGHTENS an already-larger target (min()), so a program that never
    # needed 60% of the plot in the first place must come out unaffected
    # (well under the cap, not pinned exactly at it).
    unit = "ft"
    net = to_plot_unit(AUTO_YARD_PLOT_THRESHOLD_M, unit) + 60
    length, width = _mask_footprint_dimensions(_SIX_ROOM_PROGRAM, net, net, unit, None)
    coverage = (length * width) / (net * net)
    assert coverage < MAX_PLOT_COVERAGE


_FRONT_SETBACK_PROGRAM = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1}, {"name": "Dining Room", "area": 2},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2}, {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bathroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
]


def test_layout_floor_mask_reserves_a_real_front_setback_on_a_large_plot():
    dims = {"length": 150.0, "width": 150.0, "unit": "ft"}
    rects = layout_floor(_FRONT_SETBACK_PROGRAM, dims, garage_cars=None, facing="south")
    fp = footprint_bbox(rects)
    assert fp is not None
    # South facing - the house's own facing edge is fp[3] (max_y); the real
    # gap back to the plot's own facing edge (150.0) should be a
    # substantial fraction of MIN_FRONT_SETBACK_M, not flush (0 - the
    # pre-C0 behavior).
    setback_ft = dims["width"] - fp[3]
    min_setback_ft = to_plot_unit(MIN_FRONT_SETBACK_M, "ft") * 0.5  # generous tolerance
    assert setback_ft >= min_setback_ft, f"front setback {setback_ft:.1f}ft looks too small (house sits flush)"


def test_layout_floor_mask_front_setback_is_a_no_op_on_a_small_plot():
    dims = {"length": 30.0, "width": 40.0, "unit": "ft"}
    rooms = [
        {"name": "Living Room", "area": 2}, {"name": "Kitchen", "area": 1}, {"name": "Dining Room", "area": 1},
        {"name": "Entry", "area": 1}, {"name": "Bedroom 1", "area": 1.5}, {"name": "Bathroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1.5},
    ]
    rects = layout_floor(rooms, dims, garage_cars=None, facing="south")
    fp = footprint_bbox(rects)
    assert fp is not None
    setback_ft = dims["width"] - fp[3]
    # A right-sized plot leaves only the pre-existing exterior-wall-
    # thickness gap (well under 2ft) - nowhere near a real driveway setback.
    assert setback_ft < 2.0, f"front setback {setback_ft:.1f}ft - expected a near-zero no-op on a small plot"
