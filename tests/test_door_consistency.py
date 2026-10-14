"""Step 3 Task C: one layout, one door list.

For the same rects / plot / facing / floor number, the PNG blueprint, the DXF
export and the AI Concept Layout door overlay must draw the SAME set of doors:
the same interior room pairs {frozenset(room_a, room_b)} and the same
front-door room. Door POSITIONS may differ (the DXF centres a gap at the
shared-wall midpoint, the PNG / Concept Layout at 30% along it - documented
cosmetic difference); the SET may not.

Doors are read back from what each renderer actually DRAWS (not from
door_graph, the shared source being tested): PNG via monkeypatched
_draw_door/_draw_front_door (recording only doors the renderer does not skip),
DXF by parsing the DOORS layer (each leaf LINE paired with the ARC centred on
its hinge), Concept Layout from kaggle_autocad._door_specs_for_floor's specs
(canvas fractions mapped back to plot units). Every door is mapped to its
room pair by an independent nearest-shared-edge geometry mapper.
"""
import io
import math

import ezdxf
import pytest

from app.config import settings
from app.pipeline import blueprint_dxf, blueprint_svg
from app.pipeline.conditioning_image import CANVAS_SIZE, plot_to_canvas_box
from app.pipeline.floor_layout import footprint_bbox, room_parts
from app.pipeline.floor_layout_mask import layout_floor_with_warnings
from app.providers import kaggle_autocad
from tests.mask_quality_matrix import iter_floors, planner_building

_TOL = 1e-3  # plot units (ft); positions are exact floats or canvas round-trips
_MAP_TOL = 0.05  # max distance from a drawn door centre to its shared wall

_FAST_CASES = {
    "40x60_typical_garage_1f", "60x80_dense_garage_1f", "60x80_3f_south",
    "30x40_sparse_1f", "150x150_dense_garage_2f",
}
_ITEMS = list(iter_floors())


def _params():
    out = []
    for planner in (False, True):
        for case, floor_n, rooms, key in _ITEMS:
            marks = [pytest.mark.fast] if case["id"] in _FAST_CASES else []
            out.append(pytest.param(case, floor_n, rooms, planner, id=f"{'on' if planner else 'off'}:{key}", marks=marks))
    return out


def _layout(monkeypatch, case, floor_n, rooms, planner):
    if planner:
        monkeypatch.setattr(settings, "house_planner_enabled", True)
        _plan, results = planner_building(case)
        return results[floor_n - 1][1]
    monkeypatch.setattr(settings, "house_planner_enabled", False)
    rects, _ = layout_floor_with_warnings(rooms, case["plot"], garage_cars=case["garage_cars"], facing=case["facing"])
    return rects


# ---- geometry mapper (independent of door_graph) ----

def _segments(rects):
    """Every shared wall between two rooms' parts:
    [(orientation, pos, start, end, name_a, name_b)]."""
    segs = []
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            for pa in room_parts(a):
                for pb in room_parts(b):
                    for p, q in ((pa, pb), (pb, pa)):
                        if abs(p["x"] + p["w"] - q["x"]) < _TOL:
                            s, e = max(p["y"], q["y"]), min(p["y"] + p["h"], q["y"] + q["h"])
                            if e - s > _TOL:
                                segs.append(("vertical", q["x"], s, e, a["name"], b["name"]))
                        if abs(p["y"] + p["h"] - q["y"]) < _TOL:
                            s, e = max(p["x"], q["x"]), min(p["x"] + p["w"], q["x"] + q["w"])
                            if e - s > _TOL:
                                segs.append(("horizontal", q["y"], s, e, a["name"], b["name"]))
    return segs


def _map_pair(segs, orientation, pos, t):
    best, best_d = None, math.inf
    for o, p, s, e, na, nb in segs:
        if o != orientation:
            continue
        along = 0.0 if s <= t <= e else min(abs(t - s), abs(t - e))
        d = math.hypot(abs(p - pos), along)
        if d < best_d:
            best, best_d = frozenset((na, nb)), d
    assert best is not None and best_d <= _MAP_TOL, (orientation, pos, t, best, best_d)
    return best


def _map_front(rects, orientation, pos, t):
    fx0, fy0, fx1, fy1 = footprint_bbox(rects)
    hits = []
    for r in rects:
        for p in room_parts(r):
            if orientation == "horizontal":
                on = abs(p["y"] - pos) < _TOL or abs(p["y"] + p["h"] - pos) < _TOL
                span = (p["x"], p["x"] + p["w"])
            else:
                on = abs(p["x"] - pos) < _TOL or abs(p["x"] + p["w"] - pos) < _TOL
                span = (p["y"], p["y"] + p["h"])
            if on and span[0] - _TOL <= t <= span[1] + _TOL:
                hits.append(r["name"])
    assert len(set(hits)) == 1, (orientation, pos, t, hits)
    return hits[0]


def _on_exterior(rects, orientation, pos):
    fx0, fy0, fx1, fy1 = footprint_bbox(rects)
    edges = (fy0, fy1) if orientation == "horizontal" else (fx0, fx1)
    return any(abs(pos - e) < _TOL for e in edges)


def _from_leaf(hinge, leaf_end):
    """(orientation, wall pos, gap centre) from a door's hinge + leaf end: the
    leaf is perpendicular to the wall and as long as the gap, which runs from
    the hinge in the +along direction (every renderer's convention)."""
    (hx, hy), (lx, ly) = hinge, leaf_end
    if abs(ly - hy) < abs(lx - hx):  # horizontal leaf -> vertical wall
        length = abs(lx - hx)
        return "vertical", hx, hy + length / 2
    length = abs(ly - hy)
    return "horizontal", hy, hx + length / 2


def _door_set(rects, doors):
    """doors: [(orientation, pos, t)] -> (set of interior pairs, front room)."""
    segs = _segments(rects)
    pairs, fronts = [], []
    for o, p, t in doors:
        if _on_exterior(rects, o, p):
            fronts.append(_map_front(rects, o, p, t))
        else:
            pairs.append(_map_pair(segs, o, p, t))
    assert len(pairs) == len(set(pairs)), "a room pair got two doors"
    assert len(fronts) <= 1, fronts
    return set(pairs), (fronts[0] if fronts else None)


# ---- per-renderer extraction ----

def png_doors(monkeypatch, rects, plot, total_floors, facing, floor_n):
    doors = []

    def rec_door(draw, edge, plot_x0, plot_y0, scale, nominal_len_px):
        if min(nominal_len_px, (edge["end"] - edge["start"]) * scale * 0.7) < 6:
            return  # the real renderer skips it too
        t = edge["start"] + (edge["end"] - edge["start"]) * blueprint_svg._DOOR_POSITION_FRACTION
        doors.append((edge["orientation"], edge["pos"], t))

    def rec_front(draw, edge, facing_, plot_x0, plot_y0, scale, nominal_len_px, font):
        if min(nominal_len_px, (edge["end"] - edge["start"]) * scale * 0.7) < 6:
            return
        doors.append((edge["orientation"], edge["pos"], (edge["start"] + edge["end"]) / 2))

    monkeypatch.setattr(blueprint_svg, "_draw_door", rec_door)
    monkeypatch.setattr(blueprint_svg, "_draw_front_door", rec_front)
    blueprint_svg.render_floor_blueprint(floor_n, rects, plot, total_floors, facing)
    return _door_set(rects, doors)


def dxf_doors(rects, plot, facing, floor_n):
    data = blueprint_dxf.render_floor_blueprint_dxf(floor_n, rects, plot, facing)
    doc = ezdxf.read(io.StringIO(data.decode("utf-8")))
    msp = doc.modelspace()
    arcs = [(a.dxf.center.x, a.dxf.center.y) for a in msp.query('ARC[layer=="DOORS"]')]
    doors = []
    for line in msp.query('LINE[layer=="DOORS"]'):
        s, e = (line.dxf.start.x, line.dxf.start.y), (line.dxf.end.x, line.dxf.end.y)
        if any(math.hypot(s[0] - c[0], s[1] - c[1]) < 1e-6 for c in arcs):
            hinge, leaf = s, e
        elif any(math.hypot(e[0] - c[0], e[1] - c[1]) < 1e-6 for c in arcs):
            hinge, leaf = e, s
        else:
            raise AssertionError(f"DOORS line without a swing arc at its hinge: {s}->{e}")
        doors.append(_from_leaf(hinge, leaf))
    assert len(arcs) == len(doors), (len(arcs), len(doors))
    return _door_set(rects, doors)


def concept_doors(rects, plot, facing, floor_n):
    specs = kaggle_autocad._door_specs_for_floor(rects, plot, facing, floor_n)
    length = float(plot["length"])
    x0c, y0c, box_w, _h = plot_to_canvas_box(length, float(plot["width"]))
    scale = box_w / length

    def to_units(f):
        return ((f[0] * CANVAS_SIZE - x0c) / scale, (f[1] * CANVAS_SIZE - y0c) / scale)

    doors = []
    for spec in specs:
        o, p, t = _from_leaf(to_units(spec["leaf"][0]), to_units(spec["leaf"][1]))
        doors.append((o, p, t))
        if spec.get("label") == "ENTRANCE":
            assert _on_exterior(rects, o, p), "ENTRANCE spec not on the exterior wall"
    return _door_set(rects, doors)


def all_three(monkeypatch, rects, case, floor_n):
    plot, facing, total = case["plot"], case["facing"], len(case["floors"])
    return (
        png_doors(monkeypatch, rects, plot, total, facing, floor_n),
        dxf_doors(rects, plot, facing, floor_n),
        concept_doors(rects, plot, facing, floor_n),
    )


def assert_same(png, dxf, concept):
    assert png[1] == dxf[1] == concept[1], ("front-door room", png[1], dxf[1], concept[1])
    assert png[0] == dxf[0], ("PNG vs DXF", sorted(map(sorted, png[0] ^ dxf[0])))
    assert png[0] == concept[0], ("PNG vs Concept Layout", sorted(map(sorted, png[0] ^ concept[0])))


@pytest.mark.parametrize("case,floor_n,rooms,planner", _params())
def test_png_dxf_and_concept_layout_draw_the_same_doors(monkeypatch, case, floor_n, rooms, planner):
    rects = _layout(monkeypatch, case, floor_n, rooms, planner)
    png, dxf, concept = all_three(monkeypatch, rects, case, floor_n)
    assert png[0], "no interior doors read back - extraction broken"
    if floor_n == 1:
        assert png[1] is not None, "ground floor has no front door"
    else:
        assert png[1] is None
    assert_same(png, dxf, concept)


@pytest.mark.fast
def test_negative_control_a_renderer_dropping_one_door_is_caught(monkeypatch):
    """Proves the test can fail: make the DXF silently skip its first
    interior door symbol (wall drawn solid instead) and the comparison must
    flag the missing pair."""
    case, floor_n, rooms, _key = next(i for i in _ITEMS if i[3] == "60x80_dense_garage_1f/floor1")
    rects = _layout(monkeypatch, case, floor_n, rooms, False)
    png, dxf, concept = all_three(monkeypatch, rects, case, floor_n)
    assert_same(png, dxf, concept)  # sanity: the real renderers agree

    real = blueprint_dxf._draw_interior_wall
    dropped = []

    def drop_first(msp, edge, wall_thickness, door_width):
        if door_width is not None and not dropped:
            dropped.append(edge)
            door_width = None
        real(msp, edge, wall_thickness, door_width)

    monkeypatch.setattr(blueprint_dxf, "_draw_interior_wall", drop_first)
    broken = dxf_doors(rects, case["plot"], case["facing"], floor_n)
    assert dropped and len(broken[0]) == len(dxf[0]) - 1
    with pytest.raises(AssertionError, match="PNG vs DXF"):
        assert_same(png, broken, concept)


@pytest.mark.fast
def test_negative_control_a_moved_front_door_is_caught(monkeypatch):
    case, floor_n, rooms, _key = next(i for i in _ITEMS if i[3] == "40x60_typical_1f_south/floor1")
    rects = _layout(monkeypatch, case, floor_n, rooms, False)
    png, dxf, concept = all_three(monkeypatch, rects, case, floor_n)
    with pytest.raises(AssertionError, match="front-door room"):
        assert_same(png, dxf, (concept[0], "Somewhere Else"))
