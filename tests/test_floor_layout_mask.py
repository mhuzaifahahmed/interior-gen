"""Tests for the grid-mask region-growing layout engine
(app/pipeline/floor_layout_mask.py) - the real, GUARANTEED invariants of
this engine, not a claim that every room program comes out violation-free
(it doesn't yet - see the module's own "Known simplifications" and the
plan file's milestone roadmap). What IS guaranteed and tested here:
exact tiling (no holes/overlaps - a hole would render as an unexplained
solid black poché block), determinism, correct facing/egress behavior by
construction, and that the engine never crashes on a range of real room
programs."""

from app.pipeline.floor_layout import footprint_bbox, room_parts
from app.pipeline.floor_layout_mask import layout_floor, layout_floor_with_warnings
from app.pipeline.room_specs import classify_room_category

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
