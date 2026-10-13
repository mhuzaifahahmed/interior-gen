"""Phase 3 (mask planner) shape discipline: fewest-rectangle decomposition,
rectification of wrapped rooms, reconnection of stranded pieces, and the
opt-in `wrapped`/`notch` scorer checks. Everything here is planner-path only;
the flag-off path must not touch any of it."""

import random

import pytest

from app.config import settings
import app.pipeline.floor_layout_mask as M
from app.pipeline.layout_quality import score_floor_layout, secondary_part_ok, shape_violations
from app.pipeline.room_specs import classify_room_category, min_width_for_room, to_plot_unit


def _rect_cells(c0, r0, w, h):
    return {(c, r) for r in range(r0, r0 + h) for c in range(c0, c0 + w)}


def _exact_partition(cells, parts):
    covered = []
    for (c, r, w, h) in parts:
        covered.extend(_rect_cells(c, r, w, h))
    return len(covered) == len(set(covered)) and set(covered) == set(cells)


# ---- decomposition ----


def test_decompose_fast_matches_legacy_row_major_exactly():
    rng = random.Random(7)
    for _ in range(500):
        w, h, p = rng.randint(1, 12), rng.randint(1, 12), rng.random()
        cells = {(c, r) for c in range(w) for r in range(h) if rng.random() < p}
        if cells:
            assert M._decompose_fast(cells) == M._decompose(cells)


def test_planner_decompose_takes_fewer_parts_on_a_vertical_L():
    # A comb whose teeth hang down: row-major greedy needs 3 rects (a full
    # top row band, then one per tooth row run); column-major needs 2.
    cells = _rect_cells(0, 0, 3, 10) | _rect_cells(3, 0, 4, 2)
    legacy = M._decompose(cells)
    best = M._decompose_planner(cells)
    assert len(best) <= len(legacy)
    assert len(best) == 2
    assert _exact_partition(cells, best)
    l_shape = _rect_cells(0, 0, 2, 8) | _rect_cells(2, 6, 5, 2)  # L: vertical bar + foot
    assert len(M._decompose_planner(l_shape)) == 2
    assert _exact_partition(l_shape, M._decompose_planner(l_shape))


def test_planner_decompose_staircase_and_u_shapes_are_exact():
    stair = set()
    for k in range(6):
        stair |= _rect_cells(0, k, k + 1, 1)
    u_shape = _rect_cells(0, 0, 2, 6) | _rect_cells(5, 0, 2, 6) | _rect_cells(2, 4, 3, 2)
    for cells in (stair, u_shape):
        parts = M._decompose_planner(cells)
        assert _exact_partition(cells, parts)
        assert len(parts) <= len(M._decompose(cells))
    assert len(M._decompose_planner(u_shape)) == 3


def test_rectangle_is_one_part_and_max_rect_finds_it():
    cells = _rect_cells(2, 3, 5, 4)
    assert M._decompose_planner(cells) == [(2, 3, 5, 4)]
    assert M._max_rect(cells | {(0, 0)}) == (2, 3, 5, 4)


def test_components_and_reflex_corners():
    l_shape = _rect_cells(0, 0, 2, 4) | _rect_cells(2, 2, 3, 2)
    assert M._components(l_shape) == 1
    assert M._reflex_corners(l_shape) == 1
    assert M._components(l_shape | {(10, 10)}) == 2


# ---- rectification ----


def _targets(names, unit="ft"):
    from app.pipeline.room_specs import min_area_for_room

    return [
        {"name": n, "category": classify_room_category(n), "min_area": min_area_for_room(n, unit, None)}
        for n in names
    ]


def _owner_grid(cols, rows, rooms):
    owner = [[-1] * cols for _ in range(rows)]
    for i, cells in enumerate(rooms):
        for (c, r) in cells:
            owner[r][c] = i
    return owner


def test_rectify_unwraps_a_bedroom_wrapped_around_its_bathroom():
    cols, rows = 24, 14
    bath = _rect_cells(0, 4, 5, 5)  # enclosed on three sides by the bedroom
    bed = _rect_cells(0, 0, 13, 14) - bath
    bed2 = _rect_cells(13, 0, 11, 14)
    rooms = [set(bed), set(bath), set(bed2)]
    targets = _targets(["Bedroom 1", "Bathroom 1", "Bedroom 2"])
    owner = _owner_grid(cols, rows, rooms)
    ctx = M._ShapeCtx(cols, rows, 1.0, 1.0, "ft", targets)
    before_total = set().union(*rooms)
    assert len(M._decompose_planner(rooms[0])) == 3
    had_daylight = ctx.info(rooms[0])[2]
    min_cells = [1, 1, 1]
    max_cells = [400, 25, 400]  # bathroom already AT its max: needs a swap/re-split
    M._rectify_rooms(owner, rooms, targets, [150, 25, 150], min_cells, max_cells, None, 0, ctx, {})
    # Exact tiling: same cells, every cell owned exactly once, owner grid agrees.
    assert set().union(*rooms) == before_total
    assert sum(len(r) for r in rooms) == len(before_total)
    for i, cells in enumerate(rooms):
        assert all(owner[r][c] == i for (c, r) in cells)
    assert len(M._decompose_planner(rooms[0])) <= 2
    assert len(rooms[1]) == 25 and len(M._decompose_planner(rooms[1])) == 1
    assert ctx.info(rooms[0])[2] >= min(had_daylight, ctx.need_daylight)
    width = min_width_for_room("Bedroom 1", "ft")
    _c, _r, pw, ph = max(M._decompose_planner(rooms[0]), key=lambda p: p[2] * p[3])
    assert min(pw, ph) >= width * 0.98


def test_rectify_never_moves_hallway_cells_nor_shrinks_below_min():
    cols, rows = 20, 12
    hall = _rect_cells(9, 0, 2, 12)
    a = _rect_cells(0, 0, 9, 12) | _rect_cells(11, 0, 2, 3)  # an arm stranded past the hallway
    b = _rect_cells(11, 0, 9, 12) - _rect_cells(11, 0, 2, 3)
    rooms = [set(a), set(b)]
    targets = _targets(["Bedroom 1", "Bedroom 2"])
    owner = _owner_grid(cols, rows, rooms)
    for (c, r) in hall:
        owner[r][c] = -2
    ctx = M._ShapeCtx(cols, rows, 1.0, 1.0, "ft", targets)
    floor_a = len(a) - 6  # the arm may go, nothing more
    M._rectify_rooms(owner, rooms, targets, [100, 100], [floor_a, 1], [500, 500], None, 0, ctx, {})
    assert all(owner[r][c] == -2 for (c, r) in hall)
    assert not (rooms[0] | rooms[1]) & hall
    assert len(rooms[0]) >= floor_a
    assert M._components(rooms[0]) == 1 and M._components(rooms[1]) == 1


def test_reconnect_gives_a_stranded_piece_to_a_neighbour():
    cols, rows = 20, 10
    stray = _rect_cells(15, 0, 2, 2)
    a = _rect_cells(0, 0, 10, 10) | stray
    b = _rect_cells(10, 0, 10, 10) - stray
    rooms = [set(a), set(b)]
    targets = _targets(["Bedroom 1", "Bedroom 2"])
    owner = _owner_grid(cols, rows, rooms)
    ctx = M._ShapeCtx(cols, rows, 1.0, 1.0, "ft", targets)
    stats: dict = {}
    M._reconnect_rooms(owner, rooms, targets, [100, 100], [500, 500], [0, 0], [rows - 1, rows - 1], set(), ctx, stats)
    assert rooms[0] == _rect_cells(0, 0, 10, 10)
    assert rooms[1] == _rect_cells(10, 0, 10, 10)
    assert stats["reconnected_cells"] == 4


# ---- scorer (opt-in) ----


def _room(name, parts):
    xs = [p["x"] for p in parts] + [p["x"] + p["w"] for p in parts]
    ys = [p["y"] for p in parts] + [p["y"] + p["h"] for p in parts]
    room = {"name": name, "x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}
    if len(parts) > 1:
        room["parts"] = parts
    return room


def test_secondary_part_rule():
    primary = {"x": 0, "y": 0, "w": 12, "h": 12}
    notch = {"x": 12, "y": 0, "w": 2, "h": 4}  # 4 <= 0.35*12, area 8/152
    assert secondary_part_ok(primary, notch, 152)
    long_arm = {"x": 12, "y": 0, "w": 2, "h": 10}  # 10 > 4.2
    assert not secondary_part_ok(primary, long_arm, 164)
    detached = {"x": 20, "y": 0, "w": 2, "h": 2}
    assert not secondary_part_ok(primary, detached, 148)


def test_shape_violations_wrapped_and_notch():
    rects = [
        _room("Bedroom 1", [{"x": 0, "y": 0, "w": 12, "h": 12}, {"x": 12, "y": 0, "w": 2, "h": 4}]),
        _room("Bedroom 2", [{"x": 0, "y": 12, "w": 12, "h": 12}, {"x": 12, "y": 12, "w": 6, "h": 12}][:1]
              + [{"x": 12, "y": 12, "w": 3, "h": 10}]),
        _room("Bedroom 3", [{"x": 0, "y": 30, "w": 4, "h": 4}, {"x": 4, "y": 30, "w": 4, "h": 2},
                            {"x": 8, "y": 30, "w": 4, "h": 4}]),
        _room("Hallway", [{"x": 30, "y": 0, "w": 3, "h": 20}, {"x": 33, "y": 0, "w": 10, "h": 3},
                          {"x": 40, "y": 3, "w": 3, "h": 10}]),
    ]
    kinds = {(v.room, v.kind, v.hard) for v in shape_violations(rects)}
    assert ("Bedroom 1", "notch", False) in kinds
    assert ("Bedroom 2", "wrapped", True) in kinds
    assert ("Bedroom 3", "wrapped", True) in kinds
    assert not any(v[0] == "Hallway" for v in kinds)


def test_shape_checks_are_opt_in():
    rects = [_room("Bedroom 1", [{"x": 0, "y": 0, "w": 12, "h": 12}, {"x": 12, "y": 0, "w": 3, "h": 10},
                                 {"x": 12, "y": 10, "w": 1, "h": 2}])]
    dims = {"length": 15.0, "width": 12.0, "unit": "ft"}
    off = score_floor_layout(rects, dims)
    assert not any(v.kind in ("wrapped", "notch") for v in off.violations)
    on = score_floor_layout(rects, dims, planner_checks=True)
    assert any(v.kind == "wrapped" for v in on.violations)


# ---- flag-off isolation ----


def test_flag_off_never_runs_phase3_code(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", False)

    def boom(*_a, **_k):
        raise AssertionError("Phase 3 code ran with the planner flag off")

    for fn in ("_rectify_rooms", "_reconnect_rooms", "_decompose_planner", "_best_pair_resplit"):
        monkeypatch.setattr(M, fn, boom)
    rooms = [{"name": n} for n in ("Entry", "Living Room", "Kitchen", "Dining Room", "Master Bedroom",
                                   "Master Bathroom", "Bedroom 2", "Bathroom 2")]
    M.layout_floor(rooms, {"length": 40.0, "width": 60.0, "unit": "ft"}, facing="south")


def test_planner_layout_tiles_exactly_and_respects_parts(monkeypatch):
    monkeypatch.setattr(settings, "house_planner_enabled", True)
    rooms = [{"name": n} for n in ("Entry", "Living Room", "Kitchen", "Dining Room", "Master Bedroom",
                                   "Master Bathroom", "Bedroom 2", "Bathroom 2", "Bedroom 3")]
    rects = M.layout_floor(rooms, {"length": 40.0, "width": 60.0, "unit": "ft"}, facing="south")
    parts = [p for r in rects for p in (r.get("parts") or [r])]
    xs0, ys0 = min(p["x"] for p in parts), min(p["y"] for p in parts)
    xs1 = max(p["x"] + p["w"] for p in parts)
    ys1 = max(p["y"] + p["h"] for p in parts)
    assert sum(p["w"] * p["h"] for p in parts) == pytest.approx((xs1 - xs0) * (ys1 - ys0), rel=1e-6)
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            assert ox <= 1e-6 or oy <= 1e-6
