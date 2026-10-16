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


def _img(w=600, h=600):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    return img, ImageDraw.Draw(img)


def _inked_bbox(img):
    from PIL import ImageChops
    return ImageChops.difference(img, Image.new("RGB", img.size, (255, 255, 255))).getbbox()


def test_zone_label_is_width_checked_against_its_box():
    font = ImageFont.load_default(size=11)
    img, d = _img()
    box = (100, 100, 150, 130)  # 50 px wide: too narrow for "FRONT YARD" even shrunk
    svg._draw_zone_label(d, box, "FRONT YARD", font)
    bb = _inked_bbox(img)
    assert bb is None or (bb[0] >= box[0] and bb[2] <= box[2])
    img2, d2 = _img()
    wide = (100, 100, 300, 130)
    svg._draw_zone_label(d2, wide, "FRONT YARD", font)
    bb2 = _inked_bbox(img2)
    assert bb2 is not None and bb2[0] >= wide[0] and bb2[2] <= wide[2]


def test_front_yard_label_box_never_overlaps_the_driveway():
    front = (0, 500, 400, 600)
    drive = (150, 500, 250, 600)
    for facing in ("south",):
        lab = svg._front_label_box(front, drive, facing)
        assert not svg._boxes_overlap(lab, drive)
    # east/west facing bands are vertical
    lab = svg._front_label_box((500, 0, 600, 400), (500, 100, 600, 200), "east")
    assert not svg._boxes_overlap(lab, (500, 100, 600, 200))


def test_zone_labels_are_drawn_after_the_driveway_fill(monkeypatch):
    rects = [
        {"name": "Garage", "x": 5.0, "y": 5.0, "w": 12.0, "h": 20.0},
        {"name": "Entry", "x": 17.0, "y": 5.0, "w": 8.0, "h": 20.0},
    ]
    dims = {"length": 60, "width": 80, "unit": "ft"}
    order = []
    real_driveway, real_label = svg._draw_driveway, svg._draw_zone_label

    def rec_driveway(*a, **k):
        r = real_driveway(*a, **k)
        order.append(("driveway", r is not None))
        return r

    def rec_label(draw, box, text, font):
        order.append(("label", text))
        return real_label(draw, box, text, font)

    monkeypatch.setattr(svg, "_draw_driveway", rec_driveway)
    monkeypatch.setattr(svg, "_draw_zone_label", rec_label)
    svg.render_floor_blueprint(1, rects, dims, 1, "south")
    assert ("driveway", True) in order
    first_label = next(i for i, o in enumerate(order) if o[0] == "label")
    assert order.index(("driveway", True)) < first_label
    assert ("label", "DRIVEWAY") in order


def _entrance_edge():
    # south-facing edge on the bottom of a room box spanning x 100..200 px
    return {"orientation": "horizontal", "pos": 20.0, "start": 10.0, "end": 20.0}


def test_entrance_label_stays_inside_the_door_room_and_clear_of_labels():
    font = ImageFont.load_default(size=11)
    img, d = _img()
    room_box = (100.0, 100.0, 300.0, 220.0)  # scale 10 -> edge pos 20 => y=200..: set plot 0,0
    edge = {"orientation": "horizontal", "pos": 21.0, "start": 10.0, "end": 30.0}
    avoid = [(150, 140, 250, 170)]
    box = svg._draw_entrance_label(d, edge, "south", 0.0, 0.0, 10.0, 20.0, font, room_box, avoid, (0, 0, 600, 600))
    assert box is not None
    assert box[0] >= room_box[0] and box[2] <= room_box[2] and box[1] >= room_box[1] and box[3] <= room_box[3]
    assert not svg._boxes_overlap(box, avoid[0])


def test_entrance_label_moves_to_the_front_yard_when_the_room_is_too_small():
    font = ImageFont.load_default(size=11)
    img, d = _img()
    room_box = (100.0, 100.0, 140.0, 210.0)  # 40 px wide: ENTRANCE cannot fit inside
    edge = {"orientation": "horizontal", "pos": 21.0, "start": 10.0, "end": 14.0}
    box = svg._draw_entrance_label(d, edge, "south", 0.0, 0.0, 10.0, 20.0, font, room_box, [], (0, 0, 600, 600))
    assert box is not None
    assert box[1] >= 210 - 1  # below the wall: outside, in the front yard


# ---- Item 5: open-area rooms ----

_LIVING = {"name": "Living Room", "x": 10.0, "y": 10.0, "w": 20.0, "h": 20.0}
_VERANDAH = {"name": "Verandah", "x": 30.0, "y": 10.0, "w": 14.0, "h": 20.0}


def _open_pair(open_area=True):
    ver = {**_VERANDAH, "planner_added": True, "added_reason": "porch", "open_area": True} if open_area else dict(_VERANDAH)
    return [dict(_LIVING), ver]


def test_open_area_has_no_solid_wall_on_exterior_edges_but_keeps_shared_wall():
    scale = 10.0
    img = _carved(_open_pair(True), scale)
    inset = svg.WALL_HALF_THICKNESS_PX
    # Verandah spans px x 300..440, y 100..300. Exterior right edge (x=440):
    # no wall colour anywhere along the strip band, away from the corners.
    for y in range(130, 280, 7):
        for x in range(440 - inset, 441):
            assert img.getpixel((x, y)) != svg.WALL_COLOR
    # Exterior top edge (y=100) likewise.
    for x in range(330, 420, 7):
        for y in range(100, 100 + inset + 1):
            assert img.getpixel((x, y)) != svg.WALL_COLOR
    # Shared edge with the enclosed Living Room (x=300) keeps its wall.
    assert img.getpixel((300 + 2, 200)) == svg.WALL_COLOR
    assert img.getpixel((300 - 2, 200)) == svg.WALL_COLOR
    # Interior uses the open-area fill, not the room fill.
    assert img.getpixel((370, 200)) == svg.OPEN_AREA_FILL
    # The same layout without the flag has a solid exterior wall.
    solid = _carved(_open_pair(False), scale)
    assert solid.getpixel((440 - 2, 200)) == svg.WALL_COLOR


def test_open_area_dashed_outline_is_drawn_on_the_unwalled_edge():
    img = _carved(_open_pair(True), 10.0)
    ink = svg.INK_SOFT
    hits = sum(1 for y in range(110, 290) if img.getpixel((440, y)) == ink)
    assert hits > 20  # dashes present
    assert hits < 150  # but not a continuous line


def test_open_area_label_says_open_and_planner_flag_reaches_the_renderer():
    ann = svg.annotate_planner_rects(
        [dict(_LIVING), dict(_VERANDAH)],
        [{"name": "Living Room"}, {"name": "Verandah", "planner_added": True, "added_reason": "porch", "open_area": True}],
    )
    assert ann[1]["open_area"] is True and ann[1]["planner_added"] is True
    assert "open_area" not in ann[0]
    img = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(img)
    geom = {**svg._largest_part_view(ann[1]), "_area_override": 280.0, "_multi_part": False}
    lay = svg._compute_label_layout(d, geom, 0, 0, 10.0, "ft", ImageFont.load_default(size=13), ImageFont.load_default(size=11))
    assert "(OPEN)" in " ".join(lay["name_lines"])
    # Un-flagged rects are returned untouched (flag-off identity).
    plain = [dict(_LIVING), dict(_VERANDAH)]
    assert svg.annotate_planner_rects(plain, [{"name": "Living Room"}]) is plain


def test_no_front_door_is_cut_into_an_open_area():
    from app.pipeline.door_graph import build_door_graph
    dims = {"length": 50, "width": 40, "unit": "ft"}
    g = build_door_graph(_open_pair(True), dims, "east", 1)
    assert g.front is None  # the only room on the east edge is the open Verandah


def test_dxf_draws_open_area_dashed_on_its_own_layer_with_no_exterior_wall():
    dims = {"length": 50, "width": 40, "unit": "ft"}
    doc = _parse(render_floor_blueprint_dxf(1, _open_pair(True), dims, "south"))
    assert doc.audit().errors == []
    msp = doc.modelspace()
    open_lines = list(msp.query('LINE[layer=="OPEN"]'))
    assert len(open_lines) >= 2
    assert doc.layers.get("OPEN").dxf.linetype == "DASHED"
    right_x = 44.0
    for e in msp.query("LWPOLYLINE"):
        if e.dxf.layer == "WALLS":
            pts = list(e.get_points("xy"))
            assert not all(abs(px - right_x) < 1e-6 for px, _ in pts)
    texts = [e.dxf.text for e in msp.query("TEXT")]
    assert "Verandah (open)" in texts
