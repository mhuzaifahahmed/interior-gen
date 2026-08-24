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


def test_render_floor_blueprint_dxf_has_one_polyline_per_room_plus_boundary():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rooms = [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}, {"name": "Kitchen", "area": 1}]
    rects = layout_floor(rooms, dimensions)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()

    polylines = list(msp.query("LWPOLYLINE"))
    assert len(polylines) == len(rects) + 1  # one per room + the exterior boundary


def test_render_floor_blueprint_dxf_room_geometry_matches_layout_floor_exactly():
    # The whole point of this feature is dimensional accuracy - the DXF
    # geometry must be the SAME numbers layout_floor() computed, not an
    # approximation or a re-derived guess.
    dimensions = {"length": 30, "width": 20, "unit": "ft"}
    rects = layout_floor([{"name": "Room A", "area": 1}, {"name": "Room B", "area": 1}], dimensions)

    doc = _parse(render_floor_blueprint_dxf(1, rects, dimensions))
    msp = doc.modelspace()
    room_polylines = [p for p in msp.query("LWPOLYLINE") if p.dxf.layer == "ROOMS"]

    assert len(room_polylines) == len(rects)
    drawn_bboxes = set()
    for pl in room_polylines:
        points = [(round(p[0], 3), round(p[1], 3)) for p in pl.get_points()]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        drawn_bboxes.add((min(xs), min(ys), max(xs), max(ys)))

    expected_bboxes = {
        (round(r["x"], 3), round(r["y"], 3), round(r["x"] + r["w"], 3), round(r["y"] + r["h"], 3)) for r in rects
    }
    assert drawn_bboxes == expected_bboxes


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
    # Still has the exterior boundary + title text even with zero rooms.
    assert len(list(msp.query("LWPOLYLINE"))) == 1
    assert len(list(msp.query("TEXT"))) == 1


def test_render_floor_blueprint_dxf_insunits_reflects_the_stated_unit():
    ft_doc = _parse(render_floor_blueprint_dxf(1, [], {"length": 10, "width": 10, "unit": "ft"}))
    m_doc = _parse(render_floor_blueprint_dxf(1, [], {"length": 10, "width": 10, "unit": "m"}))

    assert ft_doc.header["$INSUNITS"] == 2
    assert m_doc.header["$INSUNITS"] == 6
