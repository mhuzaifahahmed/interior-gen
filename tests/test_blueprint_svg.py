from io import BytesIO

from PIL import Image

from app.pipeline.blueprint_svg import render_floor_blueprint
from app.pipeline.floor_layout import layout_floor


def test_render_floor_blueprint_returns_decodable_png():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}], dimensions)

    png_bytes = render_floor_blueprint(1, rects, dimensions)

    assert png_bytes.startswith(b"\x89PNG")
    image = Image.open(BytesIO(png_bytes))
    assert image.format == "PNG"
    assert image.width > 0 and image.height > 0


def test_render_floor_blueprint_scales_image_to_plot_proportions():
    # A long, narrow plot should produce a wider-than-tall image (accounting
    # for the fixed title band added on top).
    dimensions = {"length": 100, "width": 20, "unit": "ft"}
    rects = layout_floor([{"name": "Room", "area": 1}], dimensions)
    png_bytes = render_floor_blueprint(1, rects, dimensions)
    image = Image.open(BytesIO(png_bytes))
    assert image.width > image.height


def test_render_floor_blueprint_handles_no_rooms():
    png_bytes = render_floor_blueprint(1, [], {"length": 40, "width": 60, "unit": "ft"})
    assert png_bytes.startswith(b"\x89PNG")
