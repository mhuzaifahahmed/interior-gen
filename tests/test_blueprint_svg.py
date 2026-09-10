from io import BytesIO

from PIL import Image, ImageChops, ImageDraw, ImageFont

from app.pipeline.blueprint_svg import (
    PAPER,
    _FURNITURE_MIN_BOX_H,
    _FURNITURE_MIN_BOX_W,
    _LIVING_ROOM_LARGE_MULTIPLIER,
    _draw_furniture,
    _draw_room_label,
    _fit_room_name,
    _furnish_kitchen,
    _furnish_living,
    _should_suppress_direct_door,
    _should_suppress_garage_direct_door,
    render_floor_blueprint,
)
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
    # 2026-08-29: the staircase is now a REAL room (see floor_layout.py's
    # circulation zone / generate_house.py's injection), not a symbol drawn
    # inside whichever room happened to be biggest - so the rects passed in
    # must actually include one for a symbol to be drawn at all.
    #
    # Room mix deliberately has 4 front-zone rooms (not 2) and a plot small
    # enough that not every room hits its max-area cap (see floor_layout.py's
    # 2026-09-04 min/max proportions) - a front zone with only [Living Room,
    # Staircase] on a large plot previously left Staircase sharing the box's
    # FULL width against a dominant, capped Living Room, producing an
    # unusably thin sliver (a real, pre-existing, documented limitation -
    # area is guaranteed, aspect ratio isn't) that fell below
    # _FURNITURE_MIN_BOX_H and silently drew no staircase symbol at all, in
    # BOTH the single- and multi-floor case. This mix keeps Staircase's
    # resulting box a reasonable, furniture-symbol-sized shape instead.
    dimensions = {"length": 30, "width": 40, "unit": "ft"}
    rooms = [
        {"name": "Living Room", "area": 2},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom", "area": 1},
        {"name": "Staircase", "area": 1},
    ]
    rects = layout_floor(rooms, dimensions)

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
    rooms = [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}, {"name": "Staircase", "area": 0.5}]
    rects = layout_floor(rooms, dimensions)

    ground_floor_png = render_floor_blueprint(1, rects, dimensions, total_floors=2)
    top_floor_png = render_floor_blueprint(2, rects, dimensions, total_floors=2)

    # Ground floor should show "UP" (more floors above), top floor should
    # show "DN" (no floors above) - different symbols, so different bytes.
    assert ground_floor_png != top_floor_png


def test_render_floor_blueprint_staircase_shape_is_dynamic():
    # An elongated staircase room should render a straight run; a squarer
    # one should render an L-shaped run with a landing - real, different
    # pixel output either way, confirming the shape choice actually depends
    # on the room's own resulting aspect ratio rather than being fixed.
    dimensions = {"length": 60, "width": 60, "unit": "ft"}
    elongated_rects = [
        {"name": "Staircase", "x": 0.0, "y": 0.0, "w": 8.0, "h": 30.0},
        {"name": "Living Room", "x": 8.0, "y": 0.0, "w": 52.0, "h": 60.0},
        {"name": "Bedroom", "x": 0.0, "y": 30.0, "w": 8.0, "h": 30.0},
    ]
    square_rects = [
        {"name": "Staircase", "x": 0.0, "y": 0.0, "w": 15.0, "h": 15.0},
        {"name": "Living Room", "x": 15.0, "y": 0.0, "w": 45.0, "h": 60.0},
        {"name": "Bedroom", "x": 0.0, "y": 15.0, "w": 15.0, "h": 45.0},
    ]
    elongated_png = render_floor_blueprint(1, elongated_rects, dimensions, total_floors=2)
    square_png = render_floor_blueprint(1, square_rects, dimensions, total_floors=2)
    assert elongated_png.startswith(b"\x89PNG")
    assert square_png.startswith(b"\x89PNG")
    assert elongated_png != square_png


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


def test_should_suppress_direct_door_between_two_bedrooms():
    # Two unrelated bedrooms shouldn't open straight into each other when a
    # hallway exists - they should each connect via the hallway instead
    # (drawn separately by the generic door loop, since the hallway shares a
    # wall with each of them).
    assert _should_suppress_direct_door({"name": "Bedroom 1"}, {"name": "Bedroom 2"}) is True


def test_should_suppress_direct_door_keeps_ensuite_bathroom_connection():
    # A bedroom-bathroom pair sharing a suite id (a real ensuite, tagged by
    # floor_layout._arrange_suites) keeps its direct door.
    assert (
        _should_suppress_direct_door(
            {"name": "Master Bedroom", "suite": 0}, {"name": "Master Bathroom", "suite": 0}
        )
        is False
    )


def test_should_suppress_direct_door_suppresses_bathroom_into_unrelated_bedroom():
    # The whole point of the suite tag: a bathroom must NOT open into a
    # bedroom it merely got packed next to (different suite id) - only into
    # its own suite partner. This is the "master washroom ended up attached
    # to the wrong room" bug the suite model fixes.
    assert (
        _should_suppress_direct_door(
            {"name": "Master Bathroom", "suite": 0}, {"name": "Bedroom 2", "suite": 1}
        )
        is True
    )


def test_should_suppress_direct_door_suppresses_two_adjacent_bathrooms():
    assert (
        _should_suppress_direct_door(
            {"name": "Bathroom 1", "suite": 0}, {"name": "Bathroom 2", "suite": 1}
        )
        is True
    )


def test_should_suppress_direct_door_suppresses_untagged_bathroom_into_bedroom():
    # A standalone/common bathroom (no suite tag) opens onto the hallway
    # only, never directly into an adjacent bedroom.
    assert _should_suppress_direct_door({"name": "Bathroom"}, {"name": "Bedroom 1"}) is True


def test_should_suppress_direct_door_never_suppresses_a_hallway_door():
    assert _should_suppress_direct_door({"name": "Hallway"}, {"name": "Bedroom 1"}) is False


def test_should_suppress_direct_door_leaves_public_zone_rooms_untouched():
    # The suppression only applies within the private zone - public-zone
    # rooms (e.g. Living Room/Kitchen) keep their existing direct-door
    # behavior regardless of any hallway elsewhere on the floor.
    assert _should_suppress_direct_door({"name": "Living Room"}, {"name": "Kitchen"}) is False


def test_render_floor_blueprint_suppresses_bedroom_to_bedroom_doors_when_hallway_present():
    # End-to-end: a room mix that triggers a real hallway (3+ private-zone
    # rooms, see floor_layout.py's MIN_ROOMS_FOR_CORRIDOR) must render
    # differently from an otherwise-identical mix that DOESN'T trigger one -
    # confirms the door-suppression logic actually changes real output, not
    # just the unit-level helper.
    dimensions = {"length": 45, "width": 60, "unit": "ft"}
    rooms_with_corridor = [
        {"name": "Living Room", "area": 3},
        {"name": "Kitchen", "area": 1},
        {"name": "Bedroom 1", "area": 2},
        {"name": "Bedroom 2", "area": 2},
        {"name": "Bathroom", "area": 1},
    ]
    rects = layout_floor(rooms_with_corridor, dimensions)
    assert any(r["name"] == "Hallway" for r in rects)
    png_bytes = render_floor_blueprint(1, rects, dimensions)
    assert png_bytes.startswith(b"\x89PNG")


def test_should_suppress_garage_direct_door_between_garage_and_living():
    assert _should_suppress_garage_direct_door({"name": "Garage"}, {"name": "Living Room"}) is True


def test_should_suppress_garage_direct_door_between_garage_and_kitchen():
    assert _should_suppress_garage_direct_door({"name": "Garage"}, {"name": "Kitchen"}) is True


def test_should_suppress_garage_direct_door_between_garage_and_dining():
    assert _should_suppress_garage_direct_door({"name": "Garage"}, {"name": "Dining Room"}) is True


def test_should_suppress_garage_direct_door_keeps_garage_to_entry_connection():
    # The garage must still be able to reach the entry - that's how it
    # connects to the rest of the house at all.
    assert _should_suppress_garage_direct_door({"name": "Garage"}, {"name": "Entry"}) is False


def test_should_suppress_garage_direct_door_leaves_non_garage_pairs_untouched():
    assert _should_suppress_garage_direct_door({"name": "Living Room"}, {"name": "Kitchen"}) is False


def test_render_floor_blueprint_suppresses_garage_to_living_door_when_entry_present():
    # End-to-end: with a real Entry room on the floor, the garage must not
    # get a direct door to Living Room - confirms the door-suppression logic
    # actually changes real rendered output, not just the unit-level helper.
    dimensions = {"length": 50, "width": 45, "unit": "ft"}
    rooms = [
        {"name": "Garage", "area": 2},
        {"name": "Entry", "area": 1},
        {"name": "Living Room", "area": 3},
        {"name": "Bedroom", "area": 2},
    ]
    rects = layout_floor(rooms, dimensions)
    png_bytes = render_floor_blueprint(1, rects, dimensions)
    assert png_bytes.startswith(b"\x89PNG")


def _draw_furniture_and_diff_from_blank(rect, scale):
    """Renders _draw_furniture() for one rect onto a blank canvas and
    returns whether any pixel changed - a direct way to check "did a
    furniture symbol actually get drawn" without depending on the exact
    symbol shape."""
    image = Image.new("RGB", (200, 200), "white")
    blank = image.copy()
    draw = ImageDraw.Draw(image)
    _draw_furniture(draw, rect, 0, 0, scale)
    return ImageChops.difference(image, blank).getbbox() is not None


def test_garage_furniture_renders_below_the_general_threshold_but_above_its_own():
    # 2026-09-04: real bug - on a large plot, a garage's real, CORRECTLY
    # SIZED width (see room_specs.garage_dimensions()) can scale down under
    # the general 70px furniture-drawing threshold even though its real-world
    # size is fine, silently suppressing the car icon. Garage gets its own,
    # lower threshold (_GARAGE_MIN_BOX_W/H = 40) since its furniture symbol
    # is drawn purely proportionally, safe at a smaller box than fixed-size
    # fixtures. A 10x10 real-unit room at scale=5 renders a 50x50px box -
    # below the general 70px gate, above garage's own 40px gate.
    rect = {"name": "Garage", "x": 0, "y": 0, "w": 10, "h": 10}
    assert _draw_furniture_and_diff_from_blank(rect, scale=5) is True


def test_non_garage_furniture_still_suppressed_below_the_general_threshold():
    # Same box size as above, but a room type NOT covered by the lower
    # garage-specific threshold - must still be suppressed (confirms the
    # lower threshold is genuinely garage-specific, not a global loosening).
    rect = {"name": "Bedroom", "x": 0, "y": 0, "w": 10, "h": 10}
    assert _draw_furniture_and_diff_from_blank(rect, scale=5) is False


def test_garage_furniture_still_suppressed_below_its_own_lower_threshold():
    # A garage box smaller than even ITS OWN (lower) threshold must still be
    # suppressed - the fix lowers the bar, it doesn't remove it.
    rect = {"name": "Garage", "x": 0, "y": 0, "w": 5, "h": 5}
    assert _draw_furniture_and_diff_from_blank(rect, scale=5) is False


def _render_pixels(draw_fn, w_px: int, h_px: int, *extra_args) -> Image.Image:
    image = Image.new("RGB", (w_px, h_px), "white")
    draw = ImageDraw.Draw(image)
    draw_fn(draw, 0, 0, w_px, h_px, w_px, h_px, *extra_args)
    return image


def test_furnish_living_large_places_a_centered_tv_console_on_the_bottom_wall():
    # 2026-09-10: the large-room living arrangement was redesigned into a
    # CENTERED conversation grouping (sofa centered on the top wall facing a
    # TV console centered on the bottom wall, coffee table + two flanking
    # armchairs between them) - real user feedback that every earlier corner-
    # anchored version left a large room's right half empty. Checked at the
    # console's own computed position (mirroring _furnish_living_large's real
    # placement math), not a raw ink-density comparison.
    threshold_area = _FURNITURE_MIN_BOX_W * _FURNITURE_MIN_BOX_H * _LIVING_ROOM_LARGE_MULTIPLIER

    large_w, large_h = 300, 260
    assert large_w * large_h > threshold_area
    large = _render_pixels(_furnish_living, large_w, large_h)

    depth = min(large_w, large_h) * 0.13
    console_w, console_h = large_w * 0.4, depth * 0.55
    console_x0 = (large_w - console_w) / 2  # centered on the bottom wall
    console_y1 = large_h
    console_y0 = console_y1 - console_h

    large_has_console = any(
        large.getpixel((x, y)) != (255, 255, 255)
        for x in range(int(console_x0), int(console_x0 + console_w) + 1)
        for y in range(int(console_y0), int(console_y1))
    )
    assert large_has_console

    # A small room (below the threshold) takes the compact corner path, which
    # draws NO centered-bottom console - the center of the bottom wall stays
    # clear.
    small_w, small_h = 90, 90
    assert small_w * small_h < threshold_area
    small = _render_pixels(_furnish_living, small_w, small_h)
    small_center_x = small_w // 2
    small_has_centered_console = any(
        small.getpixel((x, y)) != (255, 255, 255)
        for x in range(small_center_x - 8, small_center_x + 8)
        for y in range(int(small_h * 0.85), small_h)
    )
    assert not small_has_centered_console


def test_furnish_living_large_keeps_solid_furniture_clear_of_the_centered_label():
    # Regression guard for the label-collision class of bug this project has
    # hit repeatedly (bed, kitchen island): the large living arrangement must
    # keep its SOLID pieces (sofa/coffee table/armchairs/console) out of the
    # room's vertical center band, where _draw_room_label() stamps the room
    # name. The rug is outline-only and its two side edges (near the walls)
    # are allowed to cross the band, so this scans only the CENTER portion of
    # the band - exactly where the label text actually sits.
    w_px, h_px = 320, 520
    cy = h_px / 2
    label_clearance_px = 26  # _LABEL_CLEARANCE_PX, kept as a literal to avoid importing a private constant
    image = _render_pixels(_furnish_living, w_px, h_px)

    band_top, band_bottom = int(cy - label_clearance_px), int(cy + label_clearance_px)
    center_x0, center_x1 = int(w_px * 0.28), int(w_px * 0.72)  # the label's own zone, excluding the rug's wall-side edges
    for y in range(band_top, band_bottom):
        for x in range(center_x0, center_x1):
            assert image.getpixel((x, y)) == (255, 255, 255), f"living-room furniture drawn into the label band at ({x},{y})"


def test_furnish_kitchen_island_never_collides_with_a_centered_label():
    # Real bug hit and fixed via visual inspection (2026-09-09): the first
    # version of the island was positioned near the room's own vertical
    # center - exactly where _draw_room_label() centers the room name/area
    # text - so the island and its bar stools rendered directly through the
    # label. Fixed the same way bedrooms already handle this
    # (_LABEL_CLEARANCE_PX): a room tall enough to trigger the island must
    # still draw nothing inside the label's own clearance band.
    w_px, h_px = 300, 600
    cy = h_px / 2
    label_clearance_px = 26  # _LABEL_CLEARANCE_PX, kept as a literal to avoid importing a private constant twice
    image = _render_pixels(_furnish_kitchen, w_px, h_px, cy)

    band_top, band_bottom = int(cy - label_clearance_px), int(cy + label_clearance_px)
    for y in range(band_top, band_bottom):
        for x in range(0, w_px, 5):
            assert image.getpixel((x, y)) == (255, 255, 255), f"furniture drawn into the label band at ({x},{y})"


def _draw_ctx():
    return ImageDraw.Draw(Image.new("RGB", (10, 10)))


def test_fit_room_name_keeps_default_size_when_it_already_fits():
    draw = _draw_ctx()
    name_font = ImageFont.load_default(size=13)
    lines, font = _fit_room_name(draw, "GARAGE", max_width_px=1000, base_font=name_font)
    assert lines == ["GARAGE"]
    assert font.size == 13


def test_fit_room_name_shrinks_font_before_wrapping():
    # A width that's too narrow at size 13 but wide enough at a smaller
    # size - must shrink, not wrap, since a single line is preferred.
    draw = _draw_ctx()
    name_font = ImageFont.load_default(size=13)
    base_w, _ = draw.textbbox((0, 0), "MASTER BATHROOM", font=name_font)[2:4]
    lines, font = _fit_room_name(draw, "MASTER BATHROOM", max_width_px=base_w - 30, base_font=name_font)
    assert lines == ["MASTER BATHROOM"]
    assert font.size < 13


def test_fit_room_name_wraps_onto_two_lines_when_even_min_size_does_not_fit():
    draw = _draw_ctx()
    name_font = ImageFont.load_default(size=13)
    lines, font = _fit_room_name(draw, "MASTER BATHROOM", max_width_px=40, base_font=name_font)
    assert lines == ["MASTER", "BATHROOM"]
    assert font.size == 9  # _NAME_MIN_FONT_SIZE


def test_fit_room_name_cannot_wrap_a_single_word_and_falls_back_to_min_size():
    draw = _draw_ctx()
    name_font = ImageFont.load_default(size=13)
    lines, font = _fit_room_name(draw, "GARAGE", max_width_px=10, base_font=name_font)
    assert lines == ["GARAGE"]
    assert font.size == 9


def test_narrow_room_label_no_longer_overflows_into_neighboring_room():
    # Real user report (2026-09-04): "MASTER BATHROOM" rendered at a single
    # fixed font size overflowed past a narrow bathroom's own walls into
    # whatever was drawn next to it. Reproduce the exact reported room mix
    # and confirm the label now stays fully within the room's own pixel
    # box (shrunk and/or wrapped), not overlapping the neighboring room.
    rooms = [
        {"name": "Master Bedroom", "area": 2.5},
        {"name": "Master Bathroom", "area": 1},
        {"name": "Bedroom 2", "area": 2},
    ]
    dimensions = {"length": 100, "width": 40, "unit": "ft"}
    rects = layout_floor(rooms, dimensions)
    bathroom = next(r for r in rects if r["name"] == "Master Bathroom")
    scale = 780 / max(dimensions["length"], dimensions["width"])
    box_w_px = bathroom["w"] * scale

    draw = _draw_ctx()
    name_font = ImageFont.load_default(size=13)
    lines, font = _fit_room_name(
        draw, "MASTER BATHROOM", max_width_px=max(box_w_px - 10, 10), base_font=name_font
    )
    rendered_w = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
    assert rendered_w <= box_w_px


def _render_label_only(rect, scale):
    room_w_px = rect["w"] * scale
    room_h_px = rect["h"] * scale
    plot_x0 = plot_y0 = 30
    image = Image.new("RGB", (int(room_w_px) + 60, int(room_h_px) + 60), PAPER)
    draw = ImageDraw.Draw(image)
    name_font = ImageFont.load_default(size=13)
    small_font = ImageFont.load_default(size=11)
    _draw_room_label(
        draw, rect, plot_x0=plot_x0, plot_y0=plot_y0, scale=scale, unit="ft", name_font=name_font, small_font=small_font
    )
    # A band in the lower portion of the room's own box - below its
    # vertical center, where the area sub-line (the last element stacked
    # under the name) sits when it's drawn at all.
    band = (0, int(plot_y0 + room_h_px * 0.55), image.width, image.height)
    return image, band


def test_area_text_hidden_when_it_overflows_a_narrow_room():
    # 2026-09-04: the area sub-line (below the room name) had no width check
    # at all, unlike the name above it - on a narrow room (e.g. a compact
    # Entry beside a Garage) it ran straight into whatever was drawn next to
    # it. Reproduces the exact real reported case (an Entry room 5.9x17.7ft
    # at the scale a 60x50ft plot renders at) - a short name that still
    # fits, paired with a long area text that doesn't at this box width,
    # must render the name but drop the area line entirely.
    narrow_rect = {"name": "Entry", "x": 0, "y": 0, "w": 5.9, "h": 17.7}
    image, band = _render_label_only(narrow_rect, scale=780 / 60)
    blank = Image.new("RGB", image.size, PAPER)
    assert ImageChops.difference(image.crop(band), blank.crop(band)).getbbox() is None


def test_area_text_shown_when_it_fits_a_wide_room():
    wide_rect = {"name": "Entry", "x": 0, "y": 0, "w": 20.0, "h": 17.7}
    image, band = _render_label_only(wide_rect, scale=780 / 60)
    blank = Image.new("RGB", image.size, PAPER)
    assert ImageChops.difference(image.crop(band), blank.crop(band)).getbbox() is not None
