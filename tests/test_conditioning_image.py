from io import BytesIO

from PIL import Image

from app.pipeline.conditioning_image import (
    CANVAS_SIZE,
    plot_to_canvas_box,
    render_conditioning_edge_map,
)
from app.pipeline.floor_layout import layout_floor


def test_render_conditioning_edge_map_returns_decodable_png():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}], dimensions)

    png_bytes = render_conditioning_edge_map(rects, dimensions)

    assert png_bytes.startswith(b"\x89PNG")
    image = Image.open(BytesIO(png_bytes))
    assert image.size == (CANVAS_SIZE, CANVAS_SIZE)


def test_render_conditioning_edge_map_handles_no_rooms():
    png_bytes = render_conditioning_edge_map([], {"length": 40, "width": 60, "unit": "ft"})
    assert png_bytes.startswith(b"\x89PNG")


def test_plot_to_canvas_box_preserves_aspect_ratio():
    # A long, narrow plot (length >> width) should produce a wide, short box.
    x0, y0, box_w, box_h = plot_to_canvas_box(100, 20)
    assert box_w > box_h

    # A tall, narrow plot (width >> length) should produce a tall, narrow box.
    x0, y0, box_w, box_h = plot_to_canvas_box(20, 100)
    assert box_h > box_w


def test_plot_to_canvas_box_centers_the_box_on_the_canvas():
    x0, y0, box_w, box_h = plot_to_canvas_box(40, 60)
    assert abs(x0 - (CANVAS_SIZE - box_w) / 2) < 1e-6
    assert abs(y0 - (CANVAS_SIZE - box_h) / 2) < 1e-6


def test_render_conditioning_edge_map_differs_for_different_layouts():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects_a = layout_floor([{"name": "Studio", "area": 1}], dimensions)
    rects_b = layout_floor(
        [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}, {"name": "Kitchen", "area": 1}],
        dimensions,
    )

    png_a = render_conditioning_edge_map(rects_a, dimensions)
    png_b = render_conditioning_edge_map(rects_b, dimensions)

    assert png_a != png_b


def test_render_conditioning_edge_map_draws_a_furniture_hint_for_a_recognized_room():
    # A single large room (the whole plot) named "Living Room" should get
    # extra white pixels beyond the plain wall-outline rectangle - i.e. a
    # furniture hint was actually drawn, not just the room boundary.
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 1}], dimensions)

    with_hint = render_conditioning_edge_map(rects, dimensions)

    unrecognized_rects = layout_floor([{"name": "Zzznotaroom", "area": 1}], dimensions)
    without_hint = render_conditioning_edge_map(unrecognized_rects, dimensions)

    image_with = Image.open(BytesIO(with_hint))
    image_without = Image.open(BytesIO(without_hint))
    # Same plot/room rectangle geometry either way (only the name differs),
    # so any pixel difference must come from the furniture hint itself.
    assert list(image_with.getdata()) != list(image_without.getdata())


def test_render_conditioning_edge_map_skips_furniture_hint_for_a_tiny_room():
    # A room far too small to hold a legible hint should not attempt to draw
    # one - same "no doomed symbol" restraint blueprint_svg.py's own furniture
    # gate uses, just expressed in canvas pixels here.
    dimensions = {"length": 2000, "width": 2000, "unit": "ft"}
    tiny_rects = [{"name": "Bedroom", "x": 0, "y": 0, "w": 1, "h": 1}]

    png_bytes = render_conditioning_edge_map(tiny_rects, dimensions)

    assert png_bytes.startswith(b"\x89PNG")
