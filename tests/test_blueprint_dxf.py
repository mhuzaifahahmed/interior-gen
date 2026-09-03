import io

import ezdxf

from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf
from app.pipeline.floor_layout import layout_floor


def _parse(dxf_bytes: bytes):
    return ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8")))


def test_render_floor_blueprint_dxf_returns_valid_parseable_dxf():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}], dimensions)

    dxf_bytes = render_floor_blueprint_dxf(1, rects, dimensions)

    assert isinstance(dxf_bytes, bytes)
    doc = _parse(dxf_bytes)  # raises if not a valid DXF document
    assert doc.dxfversion == "AC1024"  # R2010


def test_render_floor_blueprint_dxf_walls_have_real_thickness():
    # v2 (2026-09-02): real structured export - every WALLS-layer polyline
    # must carry a genuine const_width (a real "wide polyline" in any CAD
    # viewer), not a thin unweighted outline pretending to be a wall.
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}], dimensions)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()
    wall_polylines = [p for p in msp.query("LWPOLYLINE") if p.dxf.layer == "WALLS"]

    assert len(wall_polylines) > 0
    for pl in wall_polylines:
        # const_width is ezdxf's real, document-level "wide polyline" DXF
        # attribute - what actually makes AutoCAD/any CAD viewer render this
        # with real thickness. Per-vertex widths from get_points() are a
        # SEPARATE, unrelated field that stays 0 when const_width is used.
        assert pl.dxf.const_width > 0


def test_render_floor_blueprint_dxf_exterior_walls_cover_the_full_boundary():
    # Every room touching the plot boundary contributes its own exterior
    # wall segment(s) - together they must cover the entire perimeter (minus
    # any window gaps), not leave the exterior undrawn.
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor(
        [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}, {"name": "Kitchen", "area": 1}],
        dimensions,
    )

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()
    wall_polylines = [p for p in msp.query("LWPOLYLINE") if p.dxf.layer == "WALLS"]

    # At least one wall segment must touch each of the 4 plot boundary lines
    # (x=0, x=length, y=0, y=width).
    touches_left = touches_right = touches_top = touches_bottom = False
    for pl in wall_polylines:
        xs = [round(p[0], 3) for p in pl.get_points()]
        ys = [round(p[1], 3) for p in pl.get_points()]
        if 0.0 in xs:
            touches_left = True
        if round(40.0, 3) in xs:
            touches_right = True
        if 0.0 in ys:
            touches_top = True
        if round(60.0, 3) in ys:
            touches_bottom = True

    assert touches_left and touches_right and touches_top and touches_bottom


def test_render_floor_blueprint_dxf_draws_a_door_between_adjacent_rooms():
    # Two rooms with no hallway (below MIN_ROOMS_FOR_CORRIDOR) must connect
    # directly - a real door symbol (line + arc, both on the DOORS layer).
    dimensions = {"length": 30, "width": 20, "unit": "ft"}
    rects = layout_floor([{"name": "Room A", "area": 1}, {"name": "Room B", "area": 1}], dimensions)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()

    assert any(e.dxf.layer == "DOORS" for e in msp.query("LINE"))
    assert any(e.dxf.layer == "DOORS" for e in msp.query("ARC"))


def test_render_floor_blueprint_dxf_suppresses_door_between_two_bedrooms_with_hallway():
    # Same real circulation-corridor rule as blueprint_svg.py - two adjacent
    # non-bathroom private rooms must NOT get a direct door when a hallway
    # exists; they connect via the hallway instead (still a real door, just
    # to the Hallway rect, not to each other).
    dimensions = {"length": 45, "width": 60, "unit": "ft"}
    rooms = [
        {"name": "Living Room", "area": 3},
        {"name": "Kitchen", "area": 1.5},
        {"name": "Dining Room", "area": 1.2},
        {"name": "Bedroom 1", "area": 2},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Bedroom 3", "area": 2},
    ]
    rects = layout_floor(rooms, dimensions)
    assert any(r["name"] == "Hallway" for r in rects)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()
    door_arcs = [a for a in msp.query("ARC") if a.dxf.layer == "DOORS"]

    # No door should sit on the shared wall between Bedroom 1/2 or Bedroom
    # 2/3 (both non-bathroom private rooms) - they must connect via the
    # hallway instead. Public-zone rooms (Kitchen/Dining Room etc.) are
    # unaffected by this rule and may legitimately have their own direct
    # doors elsewhere on the floor.
    bedroom1 = next(r for r in rects if r["name"] == "Bedroom 1")
    bedroom2 = next(r for r in rects if r["name"] == "Bedroom 2")
    bedroom3 = next(r for r in rects if r["name"] == "Bedroom 3")
    suppressed_x_positions = {round(bedroom1["x"] + bedroom1["w"], 3), round(bedroom2["x"] + bedroom2["w"], 3)}

    for arc in door_arcs:
        hx = round(arc.dxf.center.x, 3)
        hy = round(arc.dxf.center.y, 3)
        in_bedroom_row_y = bedroom1["y"] - 1e-3 <= hy <= bedroom1["y"] + bedroom1["h"] + 1e-3
        assert not (hx in suppressed_x_positions and in_bedroom_row_y), (
            f"door at ({hx},{hy}) sits directly between two bedrooms - should connect via the hallway instead"
        )


def test_render_floor_blueprint_dxf_draws_windows_on_exterior_walls():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 1}], dimensions)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()
    window_lines = [line for line in msp.query("LINE") if line.dxf.layer == "WINDOWS"]

    assert len(window_lines) > 0


def test_render_floor_blueprint_dxf_room_labels_include_name_and_dimensions():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Sunroom", "area": 1}], dimensions)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()
    texts = [t.dxf.text for t in msp.query("TEXT")]

    assert "Sunroom" in texts
    room = rects[0]
    assert any(f"{room['w']:.1f}x{room['h']:.1f}ft" in t for t in texts)


def test_render_floor_blueprint_dxf_handles_no_rooms():
    dxf_bytes = render_floor_blueprint_dxf(1, [], {"length": 40, "width": 60, "unit": "ft"})
    doc = _parse(dxf_bytes)
    msp = doc.modelspace()
    # No rooms means no wall geometry to derive (walls are now derived from
    # real room edges, not a separate unconditional boundary) - only the
    # title text remains.
    assert len(list(msp.query("LWPOLYLINE"))) == 0
    assert len(list(msp.query("TEXT"))) == 1


def test_render_floor_blueprint_dxf_insunits_reflects_the_stated_unit():
    ft_doc = _parse(render_floor_blueprint_dxf(1, [], {"length": 10, "width": 10, "unit": "ft"}))
    m_doc = _parse(render_floor_blueprint_dxf(1, [], {"length": 10, "width": 10, "unit": "m"}))

    assert ft_doc.header["$INSUNITS"] == 2
    assert m_doc.header["$INSUNITS"] == 6
