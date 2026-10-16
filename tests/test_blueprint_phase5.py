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
