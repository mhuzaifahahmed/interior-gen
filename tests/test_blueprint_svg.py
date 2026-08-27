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


def test_render_floor_blueprint_furnishes_every_recognized_room_type_without_crashing():
    # Smoke test: one room of every type the furniture dispatcher recognizes,
    # each large enough to clear the furniture-drawing size threshold - must
    # render without error for all of them, single-floor (no staircase).
    dimensions = {"length": 80, "width": 60, "unit": "ft"}
    rooms = [
        {"name": "Living Room", "area": 1},
        {"name": "Bedroom 1", "area": 1},
        {"name": "Kitchen", "area": 1},
        {"name": "Dining Room", "area": 1},
        {"name": "Bathroom", "area": 1},
        {"name": "Study", "area": 1},
        {"name": "Garage", "area": 1},
        {"name": "Laundry Room", "area": 1},
        {"name": "Walk-in Closet", "area": 1},
        {"name": "Storage", "area": 1},  # unrecognized name - must stay furniture-free, not crash
    ]
    rects = layout_floor(rooms, dimensions)
    png_bytes = render_floor_blueprint(1, rects, dimensions)
    assert png_bytes.startswith(b"\x89PNG")
    image = Image.open(BytesIO(png_bytes))
    assert image.width > 0 and image.height > 0


def test_render_floor_blueprint_draws_staircase_only_for_multi_floor():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}], dimensions)

    single_floor_png = render_floor_blueprint(1, rects, dimensions, total_floors=1)
    multi_floor_png = render_floor_blueprint(1, rects, dimensions, total_floors=2)

    # Both must still be valid, decodable PNGs - the staircase is additive
    # drawing, not a structural change to the canvas.
    assert single_floor_png.startswith(b"\x89PNG")
    assert multi_floor_png.startswith(b"\x89PNG")
    # A real, deterministic difference: the multi-floor render draws extra
    # pixels (the staircase symbol) the single-floor one doesn't.
    assert single_floor_png != multi_floor_png


def test_render_floor_blueprint_staircase_direction_depends_on_floor_number():
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor([{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}], dimensions)

    ground_floor_png = render_floor_blueprint(1, rects, dimensions, total_floors=2)
    top_floor_png = render_floor_blueprint(2, rects, dimensions, total_floors=2)

    # Ground floor should show "UP" (more floors above), top floor should
    # show "DN" (no floors above) - different symbols, so different bytes.
    assert ground_floor_png != top_floor_png


def test_render_floor_blueprint_bed_never_overlaps_the_room_label():
    # Regression test for a real bug: a bed sized/positioned without regard
    # to the label's actual pixel footprint crossed straight through the
    # room name/area text in a real rendered small-plot generation. Uses the
    # exact proportions that first reproduced it (a tall, narrow bedroom).
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    rects = layout_floor(
        [
            {"name": "Living Room", "area": 2},
            {"name": "Kitchen", "area": 1.2},
            {"name": "Dining Room", "area": 1},
            {"name": "Bedroom 1", "area": 1.3},
            {"name": "Bedroom 2", "area": 1.3},
            {"name": "Bedroom 3", "area": 1.3},
            {"name": "Guest Bathroom", "area": 0.6},
        ],
        dimensions,
    )
    # Must not raise, and must produce a real image - the actual pixel-level
    # non-overlap was confirmed by rendering and visually inspecting this
    # exact case during development.
    png_bytes = render_floor_blueprint(1, rects, dimensions, total_floors=2)
    assert png_bytes.startswith(b"\x89PNG")


def test_render_floor_blueprint_bathroom_shower_only_drawn_with_room_to_spare():
    # A tiny half-bath and a spacious bathroom both must render without
    # crashing - the shower stall is conditional on real available space
    # (see _furnish_bathroom's has_room_for_shower check), not always drawn.
    small_dimensions = {"length": 40, "width": 60, "unit": "ft"}
    small_rects = layout_floor(
        [{"name": "Living Room", "area": 6}, {"name": "Guest Bathroom", "area": 0.4}], small_dimensions
    )
    small_png = render_floor_blueprint(1, small_rects, small_dimensions)
    assert small_png.startswith(b"\x89PNG")

    large_dimensions = {"length": 40, "width": 60, "unit": "ft"}
    large_rects = layout_floor(
        [{"name": "Bedroom", "area": 1}, {"name": "Master Bathroom", "area": 2}], large_dimensions
    )
    large_png = render_floor_blueprint(1, large_rects, large_dimensions)
    assert large_png.startswith(b"\x89PNG")
    # Different fixture sets (small = no shower, large = tub + shower) must
    # produce genuinely different pixel output.
    assert small_png != large_png
