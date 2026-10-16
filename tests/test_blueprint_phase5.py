"""Phase 5 (rendering and labels) tests for blueprint_svg.py / blueprint_dxf.py."""
import io

import ezdxf
from PIL import Image, ImageDraw, ImageFont

from app.pipeline import blueprint_svg as svg
from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf


def _parse(b):
    return ezdxf.read(io.StringIO(b.decode("utf-8")))


L_ROOM = {
    "name": "Living Room", "x": 10.0, "y": 10.0, "w": 30.0, "h": 24.0,
    "parts": [
        {"x": 10.0, "y": 10.0, "w": 30.0, "h": 12.0},
        {"x": 10.0, "y": 22.0, "w": 14.0, "h": 12.0},
    ],
}
BOX_ROOM = {"name": "Bedroom", "x": 40.0, "y": 10.0, "w": 20.0, "h": 24.0}


def _layout(rect, scale=12.0):
    img = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default(size=13)
    s = ImageFont.load_default(size=11)
    geom = {
        **svg._largest_part_view(rect),
        "_area_override": sum(p["w"] * p["h"] for p in svg.room_parts(rect)),
        "_multi_part": len(svg.room_parts(rect)) > 1,
    }
    return svg._compute_label_layout(d, geom, 0, 0, scale, "ft", f, s)


def test_single_part_label_prints_true_dimensions_and_area():
    lay = _layout(BOX_ROOM)
    assert lay["area_text"] == "20.0x24.0 ft (480 sq ft)"


def test_multi_part_label_prints_area_only():
    lay = _layout(L_ROOM)
    assert lay["area_text"] == f"{30 * 12 + 14 * 12:.0f} sq ft"
    assert "x" not in lay["area_text"]


def test_dxf_label_text_matches_single_and_multi_part():
    dims = {"length": 80, "width": 50, "unit": "ft"}
    doc = _parse(render_floor_blueprint_dxf(1, [L_ROOM, BOX_ROOM], dims, None))
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert "20.0x24.0ft" in texts
    assert "528 sq ft" in texts
    assert not any(t.startswith("30.0x12.0") for t in texts)


def _carved(rects, scale=10.0):
    img = Image.new("RGB", (500, 500), svg.PAPER)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 500, 500], fill=svg.WALL_COLOR)
    svg._carve_rooms(d, rects, 0.0, 0.0, scale)
    return img


def test_concave_corner_bite_is_filled_for_an_l_shaped_room():
    img = _carved([L_ROOM])
    inset = svg.WALL_HALF_THICKNESS_PX
    # Concave vertex at (24, 22) ft -> px (240, 220); notch is x>24, y>22, so
    # the diagonal square is x in [240-inset, 240], y in [220-inset, 220].
    cx, cy = 240 - inset / 2, 220 - inset / 2
    assert img.getpixel((int(cx), int(cy))) == svg.WALL_COLOR
    # room interior well inside both parts stays fill
    assert img.getpixel((150, 150)) == svg.ROOM_FILL
    assert img.getpixel((150, 300)) == svg.ROOM_FILL


def test_convex_corners_and_plain_rooms_are_unchanged_by_the_concave_pass():
    # Plain rectangle: no concave pass, interior corner pixel just inside the
    # wall strips stays room fill.
    img = _carved([BOX_ROOM])
    inset = svg.WALL_HALF_THICKNESS_PX
    assert img.getpixel((int(400 + inset + 2), int(100 + inset + 2))) == svg.ROOM_FILL
    # Two parts that tile (shared same-room boundary) leave no wall inside.
    l_img = _carved([L_ROOM])
    assert l_img.getpixel((150, 220)) == svg.ROOM_FILL  # shared boundary between the two parts


def _stub_layout():
    # Footprint 5..45 x 4..30 inside a 50x40 ft plot. Top wall parts include
    # tiny cell-size slivers (0.9 / 1.0 ft) that must not appear in the chain.
    return [
        {"name": "A", "x": 5.0, "y": 4.0, "w": 0.9, "h": 10.0},
        {"name": "B", "x": 5.9, "y": 4.0, "w": 1.0, "h": 10.0},
        {"name": "C", "x": 6.9, "y": 4.0, "w": 20.0, "h": 8.0,
         "parts": [{"x": 6.9, "y": 4.0, "w": 20.0, "h": 8.0}, {"x": 6.9, "y": 12.0, "w": 9.0, "h": 18.0}]},
        {"name": "D", "x": 26.9, "y": 4.0, "w": 18.1, "h": 26.0},
        {"name": "E", "x": 15.9, "y": 12.0, "w": 11.0, "h": 18.0},  # interior: bbox edges must not leak in
    ]


def test_dimension_chain_has_no_segment_under_0_6_m_and_sums_to_the_plot():
    rects = _stub_layout()
    fp = (5.0, 4.0, 45.0, 30.0)
    min_ft = 0.6 / 0.3048
    for axis, extent in (("x", 50.0), ("y", 40.0)):
        b = svg.dimension_chain_boundaries(rects, fp, axis, extent, "ft")
        segs = [y - x for x, y in zip(b, b[1:])]
        assert b[0] == 0.0 and b[-1] == extent
        assert abs(sum(segs) - extent) < 1e-6
        assert all(s >= min_ft - 1e-9 for s in segs), segs


def test_dimension_chain_uses_only_exterior_wall_parts_and_yards():
    b = svg.dimension_chain_boundaries(_stub_layout(), (5.0, 4.0, 45.0, 30.0), "x", 50.0, "ft")
    assert 0.0 in b and 45.0 in b and 50.0 in b  # right yard setback kept (left sliver merged)
    assert 15.9 not in b and 26.9 in b  # interior room E's edge is not on the top wall


def test_dimension_chain_renders_on_multi_part_layout():
    dims = {"length": 50, "width": 40, "unit": "ft"}
    png = svg.render_floor_blueprint(1, _stub_layout(), dims, 1, "south")
    assert png[:4] == b"\x89PNG"
