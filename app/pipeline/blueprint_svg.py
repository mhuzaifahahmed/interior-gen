"""Draws one floor's room layout (from floor_layout.layout_floor) as a labeled,
scaled, CAD-style top-down PNG blueprint. Named blueprint_svg for the concept
it represents, but deliberately implemented with Pillow (PIL.ImageDraw), not
real SVG - see CLAUDE.md's "Build a House feature" section for why: Pillow is
already a dependency, draws straight to PNG (what both the frontend <img> and
the gpt-image-1 edit call need), and avoids a new native-library dependency
(cairosvg/Cairo) that's a real install risk on Windows, for a browser-crispness
benefit this v1 (no client-side interactivity) doesn't need.

v2 upgraded the flat two-color treemap from v1 into a genuine CAD-look
drawing per real user feedback ("a good AutoCAD map with okayish
measurements... but a good representation") - double-line exterior/interior
walls, door swings, window marks, dimension lines with real measurements,
a scale bar, a north arrow, and a title block. Everything is still computed
deterministically from the real plot dimensions and layout_floor()'s
rectangles - no image model involved, so the numbers stay honest even though
door/window placement is a simple heuristic, not a construction-grade layout.

v5 adds furniture symbols (per room-type keyword match) and a staircase
symbol for multi-floor buildings. This directly REPLACES a brief attempt to
get "professional presentation" via an AI image model redrawing this same
blueprint (app/pipeline/cad_prompts.py, since deleted) - a real generation
showed the image model hallucinating malformed dimension/area text (e.g.
"18.4 x 522.59 ft") when asked to render technical content, confirming a
well-documented image-model weakness. The fix is architectural, not a
prompt tweak: closing the "looks basic" gap by extending THIS deterministic
renderer instead, so geometry/dimensions/labels can never be hallucinated -
no image model is involved in producing the floor-plan image at all. See
app/pipeline/generate_house.py's HOUSE_PROMPT_VERSION docstring for the
full history of what was tried.

v6 (2026-08-25) replaces the double-THIN-LINE wall model with real solid
POCHÉ (filled-black) walls - a real, live-compared user finding: a friend-
hosted AI model's output looked more "professionally drafted" than this
renderer's despite being dimensionally inaccurate and text-garbled, and
solid poché was identified as the single biggest visual signature actually
missing (every real architectural drawing fills walls solid; this one drew
them as two thin outlines). Technique: fill the whole plot footprint solid
black first (_draw_wall_base), then carve each room's interior back out of
that base, inset by WALL_HALF_THICKNESS_PX (_carve_room) - whatever black
remains between two carved-out interiors automatically reads as a correct,
solid partition wall, and the band left around the whole plot automatically
reads as the exterior wall, with NO separate line-drawing/alignment logic
needed for either (this is what guarantees every wall junction lines up
exactly, unlike hand-drawing each wall as a separate stroke). Interior
partition walls come out twice as thick as the exterior wall by this
technique alone (two rooms each contribute one inset), which happens to
match a real architectural convention (exterior walls often thinner than
load-bearing interior ones in a light-frame residential build) - not
deliberately engineered, but not wrong either. Doors/windows now cut an
opening straight through the solid poché band (same hinge+leaf+arc door
symbol and window-glazing-line convention as before, just carved through
black instead of erasing a double-line gap).
"""

import math
from io import BytesIO
from itertools import combinations

from PIL import Image, ImageDraw, ImageFont

from app.pipeline.door_graph import (
    EDGE_TOL,
    FACING_ORDER,
    GARAGE_SUPPRESSED_NEIGHBORS,
    MIN_SHARED_EDGE_UNITS,
    build_door_graph,
    compute_hallway_adjacent,
    front_door_opening,
    orphaned_ensuites,
    room_door_walls,
    shared_edge,
    shared_edge_between_rooms,
    should_suppress_bathroom_public_door,
    should_suppress_direct_door,
    should_suppress_garage_direct_door,
)
from app.pipeline.floor_layout import _zone_key, footprint_bbox, room_parts  # noqa: F401 (_zone_key re-exported)
from app.pipeline.room_specs import (
    FRONT_DOOR_WIDTH_M,
    classify_room_category,
    column_fractions_for_span,
    door_width_for_wall,
    to_plot_unit,
)

# ---- Canvas layout ----
# 2026-10 (Part 3 of the "practical layouts" refinement, real user report:
# "even though we have to zoom in... make the output image understandable") -
# doubled from the original 780. A single fixed resolution meant a dense
# room program (many small rooms, e.g. several bedrooms+bathrooms) or a
# small-ish plot rendered its washrooms/labels too tiny to read even with
# the font auto-shrink/wrap already in place (_fit_room_name()) - there's a
# floor below which text simply can't shrink any further and stay legible.
# This is the FLOOR every render uses at minimum - see
# _adaptive_target_longest_side_px() below for how a render can go HIGHER
# still when even this floor isn't enough for a specific room program.
TARGET_PLOT_LONGEST_SIDE_PX = 1500
# A real, hard ceiling on how far the adaptive scaling below is allowed to
# grow the canvas - without one, an extreme room program (very many very
# small rooms) could make the PNG unreasonably large (slow to generate/
# upload/display). 3200px is already roughly 4x the original 780px default -
# comfortably legible for any realistic room count.
MAX_TARGET_PLOT_LONGEST_SIDE_PX = 3200
# A room's own SHORTER real-world dimension should render at least this many
# pixels, or its label/furniture start losing legibility regardless of the
# font-shrink logic already in place - the actual threshold this module's
# adaptive scaling targets.
_LEGIBLE_ROOM_MIN_PX = 90

MARGIN_LEFT_PX = 150   # room for the left dimension line + labels
MARGIN_TOP_PX = 130    # room for the title text + top dimension line
MARGIN_RIGHT_PX = 60
MARGIN_BOTTOM_PX = 170  # room for the scale bar + title block

DIM_GUTTER_PX = 44      # gap between the plot edge and its nearest dimension line
DIM_OVERALL_OFFSET_PX = 30  # extra gap out to the "overall" dimension line
DIM_TICK_PX = 8

# Solid poché wall model (v6, see module docstring) - each room's interior
# is carved this far in from its outer rect. Two adjacent rooms each carve
# their own inset, so the partition between them ends up 2x this thick;
# the exterior wall (only one room contributing, plus the outward
# extension below) ends up thinner - see module docstring for why that's
# an acceptable, not-deliberately-engineered side effect of the technique.
WALL_HALF_THICKNESS_PX = 5
# How far the solid wall base extends OUTWARD beyond the plot boundary,
# on top of the WALL_HALF_THICKNESS_PX every room already carves back from
# that same boundary - together these give the exterior wall a real,
# visible thickness (9px total, matching this renderer's pre-v6 exterior
# wall gap for visual continuity) instead of being paper-thin.
WALL_EXTERIOR_EXTRA_PX = 4
# Extra clearance furniture/staircase symbols keep from a room's true
# (post-poché) interior edge, so nothing visually touches or crosses into
# the solid wall band.
INTERIOR_CLEARANCE_PX = WALL_HALF_THICKNESS_PX + 2

# ---- Palette (Sage & Linen, matching the site's tokens) ----
PAPER = (250, 246, 238)        # background wash, ~#faf6ee
ROOM_FILL = (243, 228, 205)    # soft warm fill, ~#F3E4CD primary-container
GRID_LINE = (226, 217, 195)    # faint grid, ~#e2d9c3 outline-variant
INK = (51, 54, 46)             # ~#33362e on-surface
INK_SOFT = (110, 108, 92)      # ~#6e6c5c on-surface-variant
WALL_COLOR = INK
DIM_LINE_COLOR = INK_SOFT
WINDOW_COLOR = (143, 175, 158)  # ~#8faf9e tertiary sage

# A real, absolute cap on window length (2026-09-19, real user report: on a
# large house the proportional-only sizing below produced windows dozens of
# feet wide - big enough to read as an unexplained gap/extra entrance in the
# exterior wall rather than a window). A real residential window is a few
# feet wide regardless of how big the room behind it is - capping here keeps
# every window recognizably window-sized at any plot scale, while the
# proportional/min-length logic below still applies for genuinely small
# rooms whose exterior edge is shorter than this cap. Matches
# blueprint_dxf.py's own WINDOW_WIDTH_M (~4ft) so the PNG blueprint and the
# real .dxf export never disagree about how wide a window looks.
WINDOW_MAX_LENGTH_M = 1.2

# The living room gets a real, larger "picture window" cap instead of the
# generic residential window size above (2026-09-19, real user request) -
# a wide living-room window/window-wall is a genuine, common architectural
# feature, not an oversight the generic cap should also apply to.
WINDOW_MAX_LENGTH_LIVING_M = 2.4
FURNITURE_COLOR = INK_SOFT  # softer than WALL_COLOR - visually secondary to structure


def _adaptive_target_longest_side_px(rects: list[dict], length: float, width: float) -> float:
    """Returns the real pixel budget THIS specific floor's render should use -
    TARGET_PLOT_LONGEST_SIDE_PX (the floor/minimum) unless this room program
    has at least one room whose shorter real-world side would render below
    _LEGIBLE_ROOM_MIN_PX at that floor value, in which case the budget grows
    (proportionally, so that smallest room clears the legibility floor
    exactly) up to MAX_TARGET_PLOT_LONGEST_SIDE_PX.

    Same "whether a real-world size overflows/disappears depends on the
    WHOLE PLOT's scale, not the room's real feet alone" lesson this module
    already applies elsewhere (_fit_room_name(), the garage/staircase
    furniture-visibility thresholds) - just applied once, up front, to the
    canvas size itself instead of per-element afterward."""
    longest_side = max(length, width, 1.0)
    base_scale = TARGET_PLOT_LONGEST_SIDE_PX / longest_side
    governing_dims = [min(r["w"], r["h"]) for r in rects if r.get("w", 0) > 0 and r.get("h", 0) > 0]
    if not governing_dims:
        return TARGET_PLOT_LONGEST_SIDE_PX
    smallest_governing = min(governing_dims)
    projected_px = smallest_governing * base_scale
    if projected_px >= _LEGIBLE_ROOM_MIN_PX or projected_px <= 0:
        return TARGET_PLOT_LONGEST_SIDE_PX
    needed = TARGET_PLOT_LONGEST_SIDE_PX * (_LEGIBLE_ROOM_MIN_PX / projected_px)
    return min(needed, MAX_TARGET_PLOT_LONGEST_SIDE_PX)


def render_floor_blueprint(
    floor_number: int, rects: list[dict], dimensions: dict, total_floors: int = 1, facing: str | None = None
) -> bytes:
    """rects: output of floor_layout.layout_floor - [{"name","x","y","w","h"}, ...]
    in the same real-world unit as dimensions. total_floors: the building's
    total floor count. When > 1, generate_house.py has already injected a
    REAL "Staircase" room into `rects` (see floor_layout.py's circulation
    zone) - this function just draws the UP/DN stair symbol INSIDE that
    room like any other furniture (see _furnish_staircase()), it doesn't
    pick where the room goes. A single-storey building has no such room and
    nothing is drawn.

    facing (2026-09-19): the same "north"/"south"/"east"/"west" value
    layout_floor() was given, used ONLY to draw a real front-entrance
    opening on the matching exterior edge (see _front_door_opening()) - it
    does not affect placement here at all (that already happened inside
    layout_floor()). None/unrecognized draws no entrance, matching the
    pre-2026-09-19 behavior exactly (backward-compatible for any caller
    that doesn't pass it). Returns PNG bytes."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit", "") or "units"
    target_longest_side_px = _adaptive_target_longest_side_px(rects, length, width)
    scale = target_longest_side_px / max(length, width, 1)

    plot_w_px = length * scale
    plot_h_px = width * scale
    img_w = int(MARGIN_LEFT_PX + plot_w_px + MARGIN_RIGHT_PX)
    img_h = int(MARGIN_TOP_PX + plot_h_px + MARGIN_BOTTOM_PX)

    image = Image.new("RGB", (img_w, img_h), PAPER)
    draw = ImageDraw.Draw(image)

    title_font = ImageFont.load_default(size=20)
    label_font = ImageFont.load_default(size=13)
    small_font = ImageFont.load_default(size=11)

    plot_x0, plot_y0 = float(MARGIN_LEFT_PX), float(MARGIN_TOP_PX)
    plot_x1, plot_y1 = plot_x0 + plot_w_px, plot_y0 + plot_h_px

    # Smart canvas sizing (2026-10, Part 1) - the house's own exterior wall
    # may sit INSIDE the plot (not flush against it) whenever floor_layout.py
    # shrank the footprint to a realistic size on an oversized plot, leaving
    # real yard around it. footprint_bbox() (real units) is this house's
    # actual outer edge - every exterior-boundary decision below (poché
    # fill, windows, the front door) uses this, not the bare plot bounds,
    # so a real yard renders as open ground instead of a wrongly-walled
    # extension of the house. None/empty falls back to the full plot (the
    # pre-existing behavior, and numerically identical to it whenever the
    # footprint already fills the plot).
    fp = footprint_bbox(rects) or (0.0, 0.0, length, width)
    house_x0 = plot_x0 + fp[0] * scale
    house_y0 = plot_y0 + fp[1] * scale
    house_x1 = plot_x0 + fp[2] * scale
    house_y1 = plot_y0 + fp[3] * scale

    # Drawn across the WHOLE canvas (not just the plot box) - room fills
    # cover the plot area completely (layout_floor always tiles it edge to
    # edge), so a plot-only grid would be entirely invisible. Extending it
    # into the margins gives a "drafting sheet" backdrop that's actually seen.
    _draw_grid(draw, img_w, img_h, plot_x0, plot_y0, plot_x1, plot_y1, scale)

    # A real yard (house footprint smaller than the plot) gets a thin
    # property-line outline at the TRUE plot boundary, so the lot reads as
    # a real, bounded piece of land rather than ambiguous canvas margin -
    # omitted when there's no yard (house already flush with the plot) to
    # avoid drawing a redundant line directly on/near the wall itself.
    if house_x0 > plot_x0 + 0.5 or house_y0 > plot_y0 + 0.5 or house_x1 < plot_x1 - 0.5 or house_y1 < plot_y1 - 0.5:
        _draw_property_line(draw, plot_x0, plot_y0, plot_x1, plot_y1)

    # Auto site-planning: labeled yard zones + driveway apron (Phase C,
    # 2026-10) - ground floor ONLY (§1.5 "RESOLVED DECISIONS" #3: an upper
    # floor has no real exterior ground-level yard of its own, so it keeps
    # the plain open-space look above - just the property line, nothing
    # more). Drawn after the grid/property line but before the house's own
    # solid poché walls, matching the plan's own C1 ordering.
    if floor_number == 1:
        _draw_yard(draw, plot_x0, plot_y0, plot_x1, plot_y1, house_x0, house_y0, house_x1, house_y1, facing, small_font)
        _draw_driveway(draw, rects, fp, facing, plot_x0, plot_y0, plot_x1, plot_y1, scale, small_font)

    # Solid poché wall base - fill the HOUSE's own footprint (extended
    # outward for the exterior wall) black, THEN carve each room's
    # interior back out of it. See module docstring for why this single
    # fill+carve technique replaces all the old separate wall-line drawing.
    # Scoped to the house footprint, not the full plot, so any real yard
    # around an oversized plot renders as open ground, not a solid black
    # wall block.
    _draw_wall_base(draw, house_x0, house_y0, house_x1, house_y1)
    # Parts-aware (2026-10, grid-mask engine), carved via _carve_rooms() -
    # see that function's own docstring for the real "phantom blank box"
    # bug (Phase F, 2026-10-12) this two-pass technique fixes: two parts of
    # the SAME room that genuinely tile together (confirmed by the engine's
    # own exact-tiling invariant) used to each get a uniform wall-thickness
    # inset on EVERY side, including the side shared with their own
    # sibling - leaving a real, unexplained wall (and therefore an
    # unlabeled, door-less, furniture-less blank box) between two parts of
    # one room. A genuine L/U notch (the concave area NONE of a room's own
    # parts occupies) is unaffected either way - it's still correctly left
    # as solid wall unless/until some OTHER room's or the Hallway's own
    # part claims it, exactly as before.
    _carve_rooms(draw, rects, plot_x0, plot_y0, scale)

    # Real door openings (post-suppression), computed BEFORE furniture is
    # drawn - not because drawing order matters for the doors themselves,
    # but because _draw_furniture() needs to know which of each room's
    # walls actually have a door so it can avoid blocking one (see
    # _room_door_walls()). The front entrance (2026-09-19, see
    # _front_door_opening()) is collected into the SAME list so it's
    # treated identically to an interior door for furniture-avoidance
    # purposes, then drawn separately below (it's not between two rooms,
    # so it can't go through the interior _draw_door() loop).
    # Proportional, per-category door widths (2026-10, Part 2 of the
    # Measurements Model refinement) - replaces the single, near-fixed door
    # size every door used to share regardless of which room it led into
    # (a bathroom got the same wide door as a bedroom, visibly eating a
    # short wall). door_width_for_wall() is the one shared source every
    # renderer (this file, blueprint_dxf.py, kaggle_autocad.py) reads, so a
    # bathroom's door stays narrow everywhere consistently.
    # Door decisions come from door_graph.build_door_graph() (Phase 4a pure
    # refactor - this is the loop that used to live inline here: hallway /
    # entry gating, the combinations loop, suppression, ground-floor-only
    # front door). The per-door pixel width stays a rendering concern.
    facing_normalized = (facing or "").strip().lower()
    door_graph = build_door_graph(rects, dimensions, facing_normalized, floor_number)
    interior_door_edges = []
    interior_door_widths_px = []
    for a, b, edge in door_graph.interior:
        interior_door_edges.append(edge)
        cat_a, cat_b = classify_room_category(a["name"]), classify_room_category(b["name"])
        door_m = door_width_for_wall(cat_a, cat_b, unit)
        # Real regression, found and fixed 2026-10: when this per-category
        # sizing replaced the old single shared door_len_px formula, the
        # pixel clamp was accidentally widened from the original [10, 26]
        # to [8, 30] - on a smaller-scale (more px/ft) plot this made every
        # door render 15-30% larger than before, unrelated to and
        # undermining the actual point of per-category sizing (a bathroom
        # door reads narrower than a bedroom's, but both got visually
        # bigger than they used to be). Restored to the original ceiling.
        interior_door_widths_px.append(max(10.0, min(26.0, door_m * scale)))

    # The front entrance only ever exists on the GROUND floor (2026-09-29,
    # real user report: "no entrance on 2nd floor") - gated inside
    # build_door_graph() on floor_number == 1.
    front_door_edge = door_graph.front
    all_door_edges = interior_door_edges + ([front_door_edge] if front_door_edge else [])

    # The staircase's UP/DN direction (None on a single-storey building,
    # where there's no "Staircase" room to begin with) - threaded into the
    # furniture pass so _furnish_staircase() can draw the right arrow/label
    # inside whichever rect is actually named "Staircase" (see
    # floor_layout.py/generate_house.py for how that room gets reserved).
    # Parts-aware (2026-10, grid-mask engine): furniture/columns/labels all
    # anchor on a room's LARGEST part (_largest_part_view()) rather than its
    # bbox - an L/U room's empty notch must never place a bed/column/label
    # over space the room doesn't actually occupy. Byte-identical to before
    # for a plain-rectangle room (its only part IS its bbox).
    stair_direction = ("UP" if floor_number < total_floors else "DN") if total_floors > 1 else None
    for rect in rects:
        geom = _largest_part_view(rect)
        door_walls = _room_door_walls(geom, all_door_edges)
        _draw_furniture(draw, geom, plot_x0, plot_y0, scale, small_font, stair_direction, door_walls)

    # Structural columns (2026-10, Part 3) - any room whose longer clear
    # span exceeds room_specs.MAX_UNSUPPORTED_SPAN_M gets real column marks,
    # evenly spaced along that long axis - a real, engineered-looking
    # signal that a wide-open room can't just float unsupported. Drawn
    # after furniture (never obscured by/obscuring a furniture symbol -
    # most real rooms never trigger this at all) and before doors/labels.
    label_geoms = [
        {
            **_largest_part_view(rect),
            "_area_override": sum(p["w"] * p["h"] for p in room_parts(rect)),
            "_multi_part": len(room_parts(rect)) > 1,
        }
        for rect in rects
    ]
    label_layouts = [
        _compute_label_layout(draw, g, plot_x0, plot_y0, scale, unit, label_font, small_font)
        for g in label_geoms
    ]
    for rect, lay in zip(rects, label_layouts):
        _draw_columns(
            draw, _largest_part_view(rect), plot_x0, plot_y0, scale, unit,
            lay["box"] if lay else None,
        )

    for edge, width_px in zip(interior_door_edges, interior_door_widths_px):
        _draw_door(draw, edge, plot_x0, plot_y0, scale, width_px)
    if front_door_edge:
        # Same restored-ceiling fix as the interior doors above - the front
        # door previously shared the identical [10, 26] clamp as interior
        # doors (there was no separate, wider allowance for it).
        front_door_width_px = max(10.0, min(26.0, to_plot_unit(FRONT_DOOR_WIDTH_M, unit) * scale))
        _draw_front_door(
            draw, front_door_edge, facing_normalized, plot_x0, plot_y0, scale, front_door_width_px, small_font
        )

    # Parts-aware: a window can land on ANY part's own exterior edge, not
    # just the room's bbox (an L/U room's window-arm is often a part other
    # than the largest one - this is the whole point of the grid-mask
    # engine's egress-by-construction design, so windows must check every
    # part, not just one).
    for rect in rects:
        for part in room_parts(rect):
            _draw_windows(draw, {**rect, **part}, fp, plot_x0, plot_y0, scale, unit)

    for label_geom in label_geoms:
        _draw_room_label(draw, label_geom, plot_x0, plot_y0, scale, unit, label_font, small_font)

    _draw_dimensions(draw, rects, length, width, plot_x0, plot_y0, plot_x1, plot_y1, scale, unit, small_font)
    _draw_north_arrow(draw, plot_x1 - 30, plot_y0 + 34, small_font)
    _draw_scale_bar(draw, plot_x0, img_h - 92, scale, unit, small_font)
    _draw_title_block(draw, floor_number, length, width, unit, scale, img_w, img_h, title_font, small_font)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _room_bbox(rect: dict, plot_x0: float, plot_y0: float, scale: float) -> tuple[float, float, float, float]:
    x0 = plot_x0 + rect["x"] * scale
    y0 = plot_y0 + rect["y"] * scale
    return x0, y0, x0 + rect["w"] * scale, y0 + rect["h"] * scale


def _draw_wall_base(draw: ImageDraw.ImageDraw, plot_x0: float, plot_y0: float, plot_x1: float, plot_y1: float) -> None:
    """Fills the whole plot footprint - extended outward by
    WALL_EXTERIOR_EXTRA_PX so the exterior wall has real visible thickness -
    solid black. Every room is then carved back out of this single fill
    (_carve_room), so both the exterior wall and every interior partition
    come from the same technique, always aligned, never drawn as separate
    line segments that could mismatch."""
    ext = WALL_EXTERIOR_EXTRA_PX
    draw.rectangle([plot_x0 - ext, plot_y0 - ext, plot_x1 + ext, plot_y1 + ext], fill=WALL_COLOR)


def _draw_property_line(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    """A thin outline at the real plot boundary (2026-10, Part 1 - smart
    canvas sizing) - drawn ONLY when the house footprint is smaller than the
    plot (see render_floor_blueprint()'s caller), so the real yard reads as
    a bounded lot rather than ambiguous canvas margin. A plain 1px rectangle
    in the same soft ink used for dimension lines - legible, but clearly
    secondary to the solid poché wall it surrounds."""
    draw.rectangle([x0, y0, x1, y1], outline=DIM_LINE_COLOR, width=1)


# ---- Auto site-planning: yard + driveway (Phase C, 2026-10) ----
# C0 (floor_layout_mask.py) can now cap a large plot's footprint and push it
# back from the facing edge, so a real front/rear/side yard genuinely
# exists for the mask engine - these two functions draw that gap instead of
# leaving it blank "drafting sheet" (the single biggest perceived-quality
# gap found during Phase A: a tiny house floating in a sea of empty grid).
# Engine-independent - works identically whenever footprint_bbox(rects) is
# smaller than the plot, whichever engine produced that gap.
YARD_REAR_HATCH_COLOR = (158, 178, 157)   # soft sage, reads as "garden"
YARD_SIDE_HATCH_COLOR = (191, 184, 162)   # soft neutral, reads as "setback"
DRIVEWAY_FILL = (214, 210, 199)           # light paving gray-beige
DRIVEWAY_OUTLINE = (110, 108, 92)         # INK_SOFT

_MIN_ZONE_LABEL_W_PX = 46
_MIN_ZONE_LABEL_H_PX = 16


def _draw_hatch(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, color: tuple, spacing: float = 14) -> None:
    """A plain 45-degree diagonal hatch, clipped to [x0,x1]x[y0,y1] - a
    lightweight stand-in for a real crosshatch fill, used to mark an
    outdoor yard zone as visually distinct from an interior room's solid
    poché fill WITHOUT drawing a filled rectangle (which would read as a
    room, not outdoor ground - see render_floor_blueprint()'s own "do NOT
    fill it like a room or a wall" requirement). A degenerate (near-zero)
    box draws nothing - this is what makes the yard drawing a strict no-op
    on a right-sized plot where the footprint already fills it."""
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    c_min = x0 - y1
    c_max = x1 - y0
    c = c_min
    while c <= c_max:
        candidates = []
        for px, py in ((x0, x0 - c), (x1, x1 - c), (c + y0, y0), (c + y1, y1)):
            if x0 - 0.5 <= px <= x1 + 0.5 and y0 - 0.5 <= py <= y1 + 0.5:
                candidates.append((px, py))
        if len(candidates) >= 2:
            p0 = candidates[0]
            p1 = max(candidates, key=lambda p: (p[0] - p0[0]) ** 2 + (p[1] - p0[1]) ** 2)
            if p1 != p0:
                draw.line([p0, p1], fill=color, width=1)
        c += spacing


def _draw_zone_label(draw: ImageDraw.ImageDraw, box: tuple, text: str, font: ImageFont.FreeTypeFont) -> None:
    """Same restraint as every other label in this module (_draw_room_label
    etc.) - drawn only when the zone is actually big enough to hold it
    legibly; a thin sliver of side yard gets the hatch with no text rather
    than overlapping/illegible text."""
    x0, y0, x1, y1 = box
    if (x1 - x0) < _MIN_ZONE_LABEL_W_PX or (y1 - y0) < _MIN_ZONE_LABEL_H_PX:
        return
    _draw_centered_text(draw, ((x0 + x1) / 2, (y0 + y1) / 2), text, font, INK_SOFT)


def _draw_yard(
    draw: ImageDraw.ImageDraw,
    plot_x0: float, plot_y0: float, plot_x1: float, plot_y1: float,
    house_x0: float, house_y0: float, house_x1: float, house_y1: float,
    facing: str | None,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Phase C1 - labeled outdoor zones in the gap between the house's own
    footprint (house_x0..y1, pixel coords - from footprint_bbox()) and the
    full plot (plot_x0..y1). `facing` decides which band is the FRONT
    (road-side, between the plot's facing edge and the house's matching
    edge - same edge `_front_door_opening()` already uses, so the two can
    never disagree about which side the road is on): the opposite band is
    the REAR GARDEN, and the two remaining bands (spanning only the house's
    own depth, not double-counted into front/rear) are the SIDE YARDS.

    Ground-floor-only treatment is the CALLER's job (render_floor_
    blueprint() only invokes this for floor_number == 1, per the plan's
    §1.5 "RESOLVED DECISIONS" #3 - upper floors keep the plain, unlabeled
    open-space look that already existed before this feature: just the
    property-line outline, nothing more).

    A strict no-op whenever the footprint already fills the plot on a given
    axis (a small/right-sized plot) - each band collapses to a degenerate
    box and _draw_hatch()/_draw_zone_label() both already no-op on that, so
    nothing renders differently there at all."""
    facing = (facing or "").strip().lower()
    if facing not in _FACING_ORDER:
        # No real facing signal - draw nothing rather than guess which side
        # is the road (same "no signal, no mark" restraint this module
        # already uses for the front door - see _front_door_opening()).
        return

    if facing in ("north", "south"):
        if facing == "north":
            front_box = (plot_x0, plot_y0, plot_x1, house_y0)
            rear_box = (plot_x0, house_y1, plot_x1, plot_y1)
        else:
            front_box = (plot_x0, house_y1, plot_x1, plot_y1)
            rear_box = (plot_x0, plot_y0, plot_x1, house_y0)
        left_box = (plot_x0, house_y0, house_x0, house_y1)
        right_box = (house_x1, house_y0, plot_x1, house_y1)
    else:
        if facing == "west":
            front_box = (plot_x0, plot_y0, house_x0, plot_y1)
            rear_box = (house_x1, plot_y0, plot_x1, plot_y1)
        else:
            front_box = (house_x1, plot_y0, plot_x1, plot_y1)
            rear_box = (plot_x0, plot_y0, house_x0, plot_y1)
        left_box = (house_x0, plot_y0, house_x1, house_y0)
        right_box = (house_x0, house_y1, house_x1, plot_y1)

    _draw_hatch(draw, *rear_box, YARD_REAR_HATCH_COLOR, spacing=14)
    _draw_zone_label(draw, rear_box, "GARDEN", font)

    for side_box in (left_box, right_box):
        _draw_hatch(draw, *side_box, YARD_SIDE_HATCH_COLOR, spacing=18)
        _draw_zone_label(draw, side_box, "SIDE YARD", font)

    # The front zone is left UNHATCHED here - the driveway (C2, drawn
    # separately, on top of this same band) is the real feature there; a
    # hatch underneath it would visually compete with the driveway's own
    # paving fill. Still gets a label whenever there's real room (the
    # driveway itself is narrower than the full front band when the garage
    # is narrower than the house, leaving visible plain front-yard beside
    # it).
    _draw_zone_label(draw, front_box, "FRONT YARD", font)


def _draw_driveway(
    draw: ImageDraw.ImageDraw,
    rects: list[dict],
    fp: tuple[float, float, float, float],
    facing: str | None,
    plot_x0: float, plot_y0: float, plot_x1: float, plot_y1: float,
    scale: float,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Phase C2 - a driveway apron from the plot's facing (road) boundary
    to the garage, drawn only when a room classifies as `garage`
    (room_specs.classify_room_category) AND that garage has a real part
    touching the HOUSE's own facing edge (the same edge _front_door_
    opening() checks against) - the normal case, since generate_house.py's
    garage injection already targets the front zone and Phase D4 is where
    the engine's own road-edge bias belongs. A garage that isn't adjacent
    to the facing edge is a known, separate gap (Phase D4's job, not this
    renderer's) - this function simply skips drawing a driveway rather than
    drawing one that doesn't actually connect to anything, which would be
    more misleading than no driveway at all."""
    facing = (facing or "").strip().lower()
    if facing not in _FACING_ORDER:
        return
    garage = next((r for r in rects if classify_room_category(r["name"]) == "garage"), None)
    if garage is None:
        return

    fp_x0, fp_y0, fp_x1, fp_y1 = fp
    if facing == "north":
        touches = lambda p: abs(p["y"] - fp_y0) < _EDGE_TOL
        span = lambda p: (p["x"], p["x"] + p["w"])
    elif facing == "south":
        touches = lambda p: abs((p["y"] + p["h"]) - fp_y1) < _EDGE_TOL
        span = lambda p: (p["x"], p["x"] + p["w"])
    elif facing == "west":
        touches = lambda p: abs(p["x"] - fp_x0) < _EDGE_TOL
        span = lambda p: (p["y"], p["y"] + p["h"])
    else:  # east
        touches = lambda p: abs((p["x"] + p["w"]) - fp_x1) < _EDGE_TOL
        span = lambda p: (p["y"], p["y"] + p["h"])

    touching_parts = [p for p in room_parts(garage) if touches(p)]
    if not touching_parts:
        return
    part = max(touching_parts, key=lambda p: span(p)[1] - span(p)[0])
    start, end = span(part)

    # The band runs from the PLOT's own facing boundary out to the HOUSE's
    # matching edge (fp_*), spanning the garage's own [start,end] on the
    # cross axis - same front-band geometry _draw_yard() uses, just
    # restricted to the garage's own width instead of the full plot width.
    if facing == "north":
        box = (plot_x0 + start * scale, plot_y0, plot_x0 + end * scale, plot_y0 + fp_y0 * scale)
    elif facing == "south":
        box = (plot_x0 + start * scale, plot_y0 + fp_y1 * scale, plot_x0 + end * scale, plot_y1)
    elif facing == "west":
        box = (plot_x0, plot_y0 + start * scale, plot_x0 + fp_x0 * scale, plot_y0 + end * scale)
    else:  # east
        box = (plot_x0 + fp_x1 * scale, plot_y0 + start * scale, plot_x1, plot_y0 + end * scale)

    x0, y0, x1, y1 = box
    if (x1 - x0) < 2 or (y1 - y0) < 2:
        return
    draw.rectangle([x0, y0, x1, y1], fill=DRIVEWAY_FILL, outline=DRIVEWAY_OUTLINE, width=1)
    _draw_zone_label(draw, box, "DRIVEWAY", font)


# Structural columns (2026-10, Part 3) - a small, solid square, slightly
# bigger than the wall's own half-thickness so it reads as a real column
# rather than a stray dot, same print-resolution-pixel convention as the
# furniture symbols below.
COLUMN_HALF_PX = 7


# Rooms that never get columns: small/linear, or labelled so tightly a mark
# would only read as a stain.
_NO_COLUMN_CATEGORIES = frozenset({"hallway", "bathroom", "powder", "closet", "staircase", "foyer"})
LABEL_BOX_PAD_PX = 4
_COLUMN_WALL_MARGIN_PX = COLUMN_HALF_PX * 2 + 6


def _column_centres_px(
    rect: dict, plot_x0: float, plot_y0: float, scale: float, unit: str,
    label_box: tuple[float, float, float, float] | None,
) -> list[tuple[float, float]]:
    """Pixel centres for this room's structural columns. A nominal column
    position that would overlap label_box is shifted along the long axis to
    the nearest clear spot (just outside the label, inside the room, away
    from walls and other columns), or dropped when none exists."""
    if classify_room_category(rect.get("name") or "") in _NO_COLUMN_CATEGORIES:
        return []
    fractions = column_fractions_for_span(rect["w"], rect["h"], unit)
    if not fractions:
        return []
    x0, y0 = plot_x0 + rect["x"] * scale, plot_y0 + rect["y"] * scale
    w_px, h_px = rect["w"] * scale, rect["h"] * scale
    horizontal = w_px >= h_px
    span = w_px if horizontal else h_px
    origin = x0 if horizontal else y0
    half = COLUMN_HALF_PX

    lo = hi = None
    if label_box is not None:
        if horizontal:
            if label_box[1] - half <= y0 + h_px / 2 <= label_box[3] + half:
                lo, hi = label_box[0] - half - 2, label_box[2] + half + 2
        elif label_box[0] - half <= x0 + w_px / 2 <= label_box[2] + half:
            lo, hi = label_box[1] - half - 2, label_box[3] + half + 2

    placed: list[float] = []
    margin = _COLUMN_WALL_MARGIN_PX
    for f in fractions:
        pos = origin + span * f
        if lo is not None and lo <= pos <= hi:
            # Midpoint of each free stretch beside the label (not hugging the
            # text), only when that stretch is wide enough to read as a bay.
            candidates = []
            for a, b in ((origin + margin, lo), (hi, origin + span - margin)):
                if b - a >= 4 * half:
                    candidates.append((a + b) / 2)
            candidates.sort(key=lambda c: abs(c - pos))
            pos = candidates[0] if candidates else None
        if pos is None or any(abs(pos - q) < 2 * half + 4 for q in placed):
            continue
        placed.append(pos)
    if horizontal:
        return [(p, y0 + h_px / 2) for p in placed]
    return [(x0 + w_px / 2, p) for p in placed]


def _draw_columns(
    draw: ImageDraw.ImageDraw, rect: dict, plot_x0: float, plot_y0: float, scale: float, unit: str,
    label_box: tuple[float, float, float, float] | None = None,
) -> None:
    """Draws a filled square at each structural-column position (see
    _column_centres_px()) - keeps clear of the room's label text box."""
    half = COLUMN_HALF_PX
    for cx, cy in _column_centres_px(rect, plot_x0, plot_y0, scale, unit, label_box):
        draw.rectangle([cx - half, cy - half, cx + half, cy + half], fill=WALL_COLOR)


_PART_TOUCH_TOL = 1e-4  # real plot-unit coordinate tolerance, same style as _EDGE_TOL below


def _free_intervals(
    full_lo: float, full_hi: float, covered: list[tuple[float, float]], tol: float = _PART_TOUCH_TOL
) -> list[tuple[float, float]]:
    """Given one axis interval [full_lo, full_hi] (one part's own edge) and
    a list of (lo, hi) sub-intervals already covered by SIBLING parts of
    the SAME room along that edge, returns the remaining FREE sub-intervals
    - the portions of this part's own edge that do NOT border a sibling,
    and therefore still need a real wall-thickness strip redrawn there (see
    _carve_rooms()). Segment-based, not whole-side-based, specifically
    because two parts from the grid-mask engine's decomposition can share
    only PART of an edge, with the rest of that same edge bordering a
    different room/the exterior/a genuine notch - treating the whole side
    as one unit would have wrongly erased the wall on that other portion
    too."""
    if full_hi - full_lo <= tol:
        return []
    covered = sorted(iv for iv in covered if iv[1] - iv[0] > tol)
    free: list[tuple[float, float]] = []
    cursor = full_lo
    for lo, hi in covered:
        lo, hi = max(lo, full_lo), min(hi, full_hi)
        if lo > cursor + tol:
            free.append((cursor, lo))
        cursor = max(cursor, hi)
    if full_hi > cursor + tol:
        free.append((cursor, full_hi))
    return free


def _carve_rooms(
    draw: ImageDraw.ImageDraw, rects: list[dict], plot_x0: float, plot_y0: float, scale: float
) -> None:
    """Carves every room's real occupied footprint back out of the solid
    wall base, in two passes across ALL rooms (2026-10-12, Phase F bug fix -
    see the real "phantom blank box" bug this closes in the call site's own
    comment). A plain single-part room (still the overwhelming majority of
    real output) renders byte-identical to the old uniform-inset
    `_carve_room()` either way - this only changes behavior for a
    multi-part (L/U-shaped) room.

    Pass 1 fills every part of every room at its FULL real size - no inset
    at all. Two parts of the SAME room that genuinely tile together (a
    real, continuous floor - the engine's own "exact tiling" invariant
    guarantees this) merge seamlessly here, since their shared boundary
    gets filled with ROOM_FILL from both sides with nothing left between
    them. Pass 2 then re-draws a real WALL_HALF_THICKNESS_PX-wide wall
    strip along each part's own TRUE outer boundary - computed per
    SUB-SEGMENT via `_free_intervals()`, not per whole side, so a part that
    shares only PART of an edge with a sibling (the rest of that same edge
    bordering a different room, the exterior, or a genuine L/U notch) still
    gets a real wall on the non-shared portion. Two passes across ALL rooms
    (not interleaved per room) is deliberate: each room's own pixel
    territory is disjoint from every other room's, so processing order
    between rooms can never matter, but keeping the two passes strictly
    separated and global removes any doubt about it.

    A genuine L/U notch (the concave area NONE of a room's own parts
    occupies) is untouched by either pass for THIS room - exactly the
    existing, correct behavior (see the call site's comment): it's still
    solid wall unless/until some OTHER room's or the Hallway's own part
    legitimately claims it, in which case THAT room's own pass 1/2 handles
    it correctly."""
    inset = WALL_HALF_THICKNESS_PX

    # Pass 1: full-size fill, every part, every room.
    for rect in rects:
        for part in room_parts(rect):
            x0, y0, x1, y1 = _room_bbox(part, plot_x0, plot_y0, scale)
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                continue
            draw.rectangle([x0, y0, x1, y1], fill=ROOM_FILL)

    # Pass 2: re-stroke each part's own true outer boundary.
    for rect in rects:
        parts = room_parts(rect)
        for part in parts:
            px0, py0 = part["x"], part["y"]
            px1, py1 = px0 + part["w"], py0 + part["h"]
            siblings = [p for p in parts if p is not part]
            bx0, by0, bx1, by1 = _room_bbox(part, plot_x0, plot_y0, scale)
            if bx1 - bx0 <= 2 * inset or by1 - by0 <= 2 * inset:
                # Too small to carve a real interior even with zero inset on
                # every side - leave it solid wall rather than invert, same
                # defensive floor the old uniform-inset carve already had.
                continue

            left_covered = [
                (max(py0, s["y"]), min(py1, s["y"] + s["h"]))
                for s in siblings if abs(s["x"] + s["w"] - px0) < _PART_TOUCH_TOL
            ]
            for lo, hi in _free_intervals(py0, py1, left_covered):
                sy0, sy1 = plot_y0 + lo * scale, plot_y0 + hi * scale
                draw.rectangle([bx0, sy0, bx0 + inset, sy1], fill=WALL_COLOR)

            right_covered = [
                (max(py0, s["y"]), min(py1, s["y"] + s["h"]))
                for s in siblings if abs(s["x"] - px1) < _PART_TOUCH_TOL
            ]
            for lo, hi in _free_intervals(py0, py1, right_covered):
                sy0, sy1 = plot_y0 + lo * scale, plot_y0 + hi * scale
                draw.rectangle([bx1 - inset, sy0, bx1, sy1], fill=WALL_COLOR)

            top_covered = [
                (max(px0, s["x"]), min(px1, s["x"] + s["w"]))
                for s in siblings if abs(s["y"] + s["h"] - py0) < _PART_TOUCH_TOL
            ]
            for lo, hi in _free_intervals(px0, px1, top_covered):
                sx0, sx1 = plot_x0 + lo * scale, plot_x0 + hi * scale
                draw.rectangle([sx0, by0, sx1, by0 + inset], fill=WALL_COLOR)

            bottom_covered = [
                (max(px0, s["x"]), min(px1, s["x"] + s["w"]))
                for s in siblings if abs(s["y"] - py1) < _PART_TOUCH_TOL
            ]
            for lo, hi in _free_intervals(px0, px1, bottom_covered):
                sx0, sx1 = plot_x0 + lo * scale, plot_x0 + hi * scale
                draw.rectangle([sx0, by1 - inset, sx1, by1], fill=WALL_COLOR)

    # Pass 3: concave (inside) corners of a multi-part room's union outline.
    # At such a corner two wall strips (one per edge) meet, but each strip
    # lies inside a different part, so they only touch diagonally and leave
    # a light inset x inset square of room fill missing from the wall corner.
    for rect in rects:
        parts = room_parts(rect)
        if len(parts) > 1:
            _fill_concave_corners(draw, parts, plot_x0, plot_y0, scale, inset)


def _fill_concave_corners(
    draw: ImageDraw.ImageDraw, parts: list[dict], plot_x0: float, plot_y0: float, scale: float, inset: float
) -> None:
    """For every vertex of the parts' union that has exactly three occupied
    quadrants (a concave corner: the fourth quadrant is the notch), fills the
    inset x inset square in the quadrant DIAGONALLY OPPOSITE the notch with
    wall colour. Convex corners (one occupied quadrant), straight edges and
    shared same-room boundaries (two or four occupied quadrants) are left
    alone."""
    eps = 1e-3

    def inside(px: float, py: float) -> bool:
        return any(
            p["x"] - 1e-9 <= px <= p["x"] + p["w"] + 1e-9 and p["y"] - 1e-9 <= py <= p["y"] + p["h"] + 1e-9
            for p in parts
        )

    vertices = set()
    for p in parts:
        for vx in (p["x"], p["x"] + p["w"]):
            for vy in (p["y"], p["y"] + p["h"]):
                vertices.add((round(vx, 6), round(vy, 6)))

    for vx, vy in vertices:
        occupied = {(sx, sy): inside(vx + sx * eps, vy + sy * eps) for sx in (-1, 1) for sy in (-1, 1)}
        if sum(occupied.values()) != 3:
            continue
        notch = next(q for q, occ in occupied.items() if not occ)
        dx, dy = -notch[0], -notch[1]  # direction from the vertex into the opposite quadrant
        cx, cy = plot_x0 + vx * scale, plot_y0 + vy * scale
        ax, ay = cx + dx * inset, cy + dy * inset
        draw.rectangle([min(cx, ax), min(cy, ay), max(cx, ax), max(cy, ay)], fill=WALL_COLOR)


# ---- Doors ----

# Door-decision logic lives in door_graph.py (Phase 4a pure refactor); the old
# private names are re-exported so every existing importer keeps working.
_EDGE_TOL = EDGE_TOL
_MIN_SHARED_EDGE_UNITS = MIN_SHARED_EDGE_UNITS
_FACING_ORDER = FACING_ORDER
_GARAGE_SUPPRESSED_NEIGHBORS = GARAGE_SUPPRESSED_NEIGHBORS
_shared_edge = shared_edge
_shared_edge_between_rooms = shared_edge_between_rooms
_compute_hallway_adjacent = compute_hallway_adjacent
_should_suppress_direct_door = should_suppress_direct_door
_should_suppress_garage_direct_door = should_suppress_garage_direct_door
_should_suppress_bathroom_public_door = should_suppress_bathroom_public_door
_front_door_opening = front_door_opening
_room_door_walls = room_door_walls


def _largest_part_view(rect: dict) -> dict:
    """A rect-like dict (the same {"name","x","y","w","h"[,"suite"]} shape
    every single-part room always had) describing this room's LARGEST
    rectangular part - used by every renderer step that only ever made
    sense for one rectangle (furniture placement, the name/area label,
    structural columns, door-wall detection for furniture-avoidance) so an
    L/U room's empty notch can never cause a bed/label/column to land over
    space the room doesn't actually occupy. A plain-rectangle room (no
    "parts") returns itself unchanged - byte-identical to before."""
    parts = rect.get("parts")
    if not parts:
        return rect
    best = max(parts, key=lambda p: p["w"] * p["h"])
    return {**rect, **best}


# Real bug hit and fixed via visual inspection (2026-09-02): the real
# circulation corridor (see floor_layout.py) packs private-zone rooms in a
# row, each spanning the row's FULL depth - so a shared side-wall between
# two such rooms now spans nearly the room's entire height/width, putting
# the edge's exact midpoint right where the room's own CENTERED label sits
# (_draw_room_label), producing a door swing that visually collides with the
# label text. Biasing the door off the exact 50% midpoint clears that
# collision zone (the label's own clearance band is small relative to a
# full room edge) while remaining a plausible door position on shorter,
# pre-existing (non-corridor) edges too - not worth a more complex
# label-aware placement for a heuristic, "okayish accuracy" mark.
_DOOR_POSITION_FRACTION = 0.3


def _draw_door(draw: ImageDraw.ImageDraw, edge: dict, plot_x0: float, plot_y0: float, scale: float, nominal_len_px: float) -> None:
    """Erases a gap in the shared wall and draws a standard door symbol (a
    leaf line + a quarter-circle swing arc) - heuristically placed off-center
    along the segment (see _DOOR_POSITION_FRACTION). Not construction-grade,
    just a legible "there is a door here" mark, per the user's own "okayish
    accuracy" bar."""
    seg_len_px = (edge["end"] - edge["start"]) * scale
    leaf_len_px = min(nominal_len_px, seg_len_px * 0.7)
    if leaf_len_px < 6:
        return

    mid_units = edge["start"] + (edge["end"] - edge["start"]) * _DOOR_POSITION_FRACTION
    gap_start_units = mid_units - (leaf_len_px / scale) / 2

    if edge["orientation"] == "vertical":
        hx = plot_x0 + edge["pos"] * scale
        hy = plot_y0 + gap_start_units * scale
        gap_end = (hx, hy + leaf_len_px)
        open_end = (hx + leaf_len_px, hy)
    else:
        hx = plot_x0 + gap_start_units * scale
        hy = plot_y0 + edge["pos"] * scale
        gap_end = (hx + leaf_len_px, hy)
        open_end = (hx, hy + leaf_len_px)

    hinge = (hx, hy)
    # Interior partition walls are 2*WALL_HALF_THICKNESS_PX thick (see
    # _carve_room) - erase generously past that so the opening fully clears
    # the poché band regardless of which two rooms' inset happens to meet
    # here, then redraw the leaf/arc symbol on top in WALL_COLOR.
    erase_width = 2 * WALL_HALF_THICKNESS_PX + 6
    draw.line([hinge, gap_end], fill=ROOM_FILL, width=erase_width)
    draw.line([hinge, open_end], fill=WALL_COLOR, width=1)
    draw.arc(
        [hx - leaf_len_px, hy - leaf_len_px, hx + leaf_len_px, hy + leaf_len_px],
        start=0, end=90, fill=WALL_COLOR, width=1,
    )


_FRONT_DOOR_LABEL_OFFSET_PX = 16


def _draw_front_door(
    draw: ImageDraw.ImageDraw,
    edge: dict,
    facing: str,
    plot_x0: float,
    plot_y0: float,
    scale: float,
    nominal_len_px: float,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Cuts a real opening through the EXTERIOR wall at `edge` - unlike
    _draw_door(), which only ever cuts an INTERIOR partition back to
    ROOM_FILL, this erases all the way through to PAPER (the same
    exterior-band clearing _draw_windows() already uses), i.e. genuinely
    open to the outside. Centered within the room's own span on that edge
    (see _front_door_opening()), with the same leaf+arc symbol plus a small
    "ENTRANCE" label on the interior side - the one visible proof the
    chosen facing was actually honored, not just an invisible
    room-placement side effect."""
    seg_len_px = (edge["end"] - edge["start"]) * scale
    leaf_len_px = min(nominal_len_px, seg_len_px * 0.7)
    if leaf_len_px < 6:
        return

    mid_units = (edge["start"] + edge["end"]) / 2
    gap_start_units = mid_units - (leaf_len_px / scale) / 2
    erase_width = WALL_HALF_THICKNESS_PX + WALL_EXTERIOR_EXTRA_PX + 6

    # Real bug caught via visual inspection (2026-09-19): _draw_door()'s
    # fixed swing direction (leaf always opens toward +x/+y) is safe for an
    # INTERIOR door, since both sides are real rooms either way - but for
    # this EXTERIOR door, swinging outward means drawing the leaf/arc into
    # the margin, OUTSIDE the house, for south and east facings (confirmed
    # by rendering all 4 facings and inspecting each one directly, not just
    # reasoned about). The leaf must always swing INWARD, and the arc's
    # start/end angles (PIL: 0=+x, 90=+y, 180=-x, 270=-y) have to follow it
    # to keep sweeping the correct 90-degree quarter between the erase
    # direction and the open leaf.
    # Real bug caught via visual inspection (2026-09-19): a FIXED 16px inward
    # offset wasn't enough clearance - _draw_centered_text() centers the
    # label ON that offset point, so roughly HALF the text still extended
    # back toward (and visually onto/through) the wall band for east/south,
    # where the text rendered on top of near-black WALL_COLOR pixels and
    # read as "cut off" due to lost contrast, not literal clipping. The
    # label's own half-width/half-height (whichever axis is perpendicular
    # to the wall) - not a fixed pixel guess - is what actually determines
    # safe clearance, and it must also clear the arc symbol's own radius
    # (leaf_len_px) so the two don't visually collide either.
    text_w, text_h = _text_dims(draw, "ENTRANCE", font)

    if edge["orientation"] == "vertical":
        hx = plot_x0 + edge["pos"] * scale
        hy = plot_y0 + gap_start_units * scale
        gap_end = (hx, hy + leaf_len_px)
        inward = 1 if facing == "west" else -1
        open_end = (hx + inward * leaf_len_px, hy)
        arc_start, arc_end = (0, 90) if inward == 1 else (90, 180)
        clearance = max(text_w / 2, leaf_len_px) + _FRONT_DOOR_LABEL_OFFSET_PX
        label_xy = (hx + inward * clearance, hy + leaf_len_px / 2)
    else:
        hx = plot_x0 + gap_start_units * scale
        hy = plot_y0 + edge["pos"] * scale
        gap_end = (hx + leaf_len_px, hy)
        inward = 1 if facing == "north" else -1
        open_end = (hx, hy + inward * leaf_len_px)
        arc_start, arc_end = (0, 90) if inward == 1 else (270, 360)
        clearance = max(text_h / 2, leaf_len_px) + _FRONT_DOOR_LABEL_OFFSET_PX
        label_xy = (hx + leaf_len_px / 2, hy + inward * clearance)

    hinge = (hx, hy)
    draw.line([hinge, gap_end], fill=PAPER, width=erase_width)
    draw.line([hinge, open_end], fill=WALL_COLOR, width=1)
    draw.arc(
        [hx - leaf_len_px, hy - leaf_len_px, hx + leaf_len_px, hy + leaf_len_px],
        start=arc_start, end=arc_end, fill=WALL_COLOR, width=1,
    )
    _draw_centered_text(draw, label_xy, "ENTRANCE", font, INK_SOFT)


# ---- Windows ----


def _draw_windows(
    draw: ImageDraw.ImageDraw,
    rect: dict,
    footprint_bbox: tuple[float, float, float, float],
    plot_x0: float,
    plot_y0: float,
    scale: float,
    unit: str = "ft",
) -> None:
    """Marks a window on each of the room's edges that lie on the HOUSE's
    own exterior boundary - cuts straight through the solid exterior wall
    band (back to PAPER, i.e. genuinely open to the outside, not just a
    color change) with a colored glazing line centered on the cut. Length
    is capped at WINDOW_MAX_LENGTH_M regardless of room size (see that
    constant's docstring) - without the cap, a large room's window scaled
    proportionally could become dozens of feet wide, reading as an
    unexplained gap in the wall rather than a window. The living room uses
    the larger WINDOW_MAX_LENGTH_LIVING_M cap instead - a real, wider
    "picture window" is a genuine architectural feature for that room type
    specifically.

    `footprint_bbox` (2026-10, Part 1 "smart canvas sizing" - replaces the
    old `plot_length`/`plot_width` params): (min_x, min_y, max_x, max_y) of
    the house's own exterior wall (see floor_layout.footprint_bbox()) - not
    the bare plot's 0/length/width, since a real yard can leave the house's
    exterior wall sitting well inside the plot. Numerically identical to the
    old "exterior inset from 0/length/width" logic whenever the footprint
    fills the plot."""
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    is_living = classify_room_category(rect.get("name") or "") == "living"
    max_win_units = to_plot_unit(WINDOW_MAX_LENGTH_LIVING_M if is_living else WINDOW_MAX_LENGTH_M, unit)
    fp_x0, fp_y0, fp_x1, fp_y1 = footprint_bbox
    edges = []
    if abs(x - fp_x0) < _EDGE_TOL:
        edges.append(("left", y, y + h))
    if abs((x + w) - fp_x1) < _EDGE_TOL:
        edges.append(("right", y, y + h))
    if abs(y - fp_y0) < _EDGE_TOL:
        edges.append(("top", x, x + w))
    if abs((y + h) - fp_y1) < _EDGE_TOL:
        edges.append(("bottom", x, x + w))

    for side, start_units, end_units in edges:
        seg_len_units = end_units - start_units
        win_len_units = min(seg_len_units * 0.4, seg_len_units - 0.2, max_win_units)
        if win_len_units <= 0.3:
            continue
        mid = (start_units + end_units) / 2
        w_start_px = (mid - win_len_units / 2)
        w_end_px = (mid + win_len_units / 2)
        # Exterior wall band is WALL_HALF_THICKNESS_PX (inward) +
        # WALL_EXTERIOR_EXTRA_PX (outward) thick, centered on the plot
        # boundary line - erase generously past that so the cut fully
        # clears the poché band.
        erase_width = WALL_HALF_THICKNESS_PX + WALL_EXTERIOR_EXTRA_PX + 6

        if side in ("left", "right"):
            px = plot_x0 + (fp_x0 if side == "left" else fp_x1) * scale
            py0, py1 = plot_y0 + w_start_px * scale, plot_y0 + w_end_px * scale
            draw.line([(px, py0), (px, py1)], fill=PAPER, width=erase_width)
            draw.line([(px, py0), (px, py1)], fill=WINDOW_COLOR, width=3)
        else:
            py = plot_y0 + (fp_y0 if side == "top" else fp_y1) * scale
            px0, px1 = plot_x0 + w_start_px * scale, plot_x0 + w_end_px * scale
            draw.line([(px0, py), (px1, py)], fill=PAPER, width=erase_width)
            draw.line([(px0, py), (px1, py)], fill=WINDOW_COLOR, width=3)


# ---- Furniture ----
# Dispatches on a simple keyword match against the room's name (the same
# room-name vocabulary app/providers/gemini.py's ROOM_LAYOUT_PROMPT_TEMPLATE
# and _GROUND_FLOOR_ROOMS/_UPPER_FLOOR_ROOMS already use). Outline-only
# symbols in FURNITURE_COLOR (lighter than WALL_COLOR) so they read as
# secondary to the structure, not competing with it - and are positioned
# toward corners/edges, away from the room label's centered position, per
# the "furniture must not obscure labels" principle. An unrecognized or
# purely circulatory room name (entry/foyer/hallway/storage) simply gets no
# furniture rather than a guessed icon - restraint over decoration. v2
# (Step 2 furniture enrichment) added laundry/utility (washer+dryer) and
# closet/wardrobe/dressing (hanging-rod ticks) as two more recognized types.

_FURNITURE_MIN_BOX_W = 70
_FURNITURE_MIN_BOX_H = 60
# Garage gets its own, lower threshold (2026-09-04, real bug: on a large
# enough plot - longest side over ~99ft - a garage's real, correctly-sized
# width (see room_specs.garage_dimensions(), floor_layout._slice_reserving_
# garage()) can still scale down under _FURNITURE_MIN_BOX_W in PIXELS even
# though its real-world size is fine, silently suppressing the car icon.
# Garages are legitimately long-and-narrow, and _furnish_garage()'s car
# symbol is drawn purely proportionally (car_w = w*0.6, car_h = h*0.7, no
# fixed-pixel offsets) - safe to render at a much smaller box than the
# fixed-size fixtures the general threshold protects (a bed's pillow
# ellipses, a toilet's real-world-proportioned rectangle, etc.).
_GARAGE_MIN_BOX_W = 40
_GARAGE_MIN_BOX_H = 40
# Staircase gets the SAME lower threshold as garage, for the SAME real
# reason (2026-09-29): floor_layout.py's real staircase carve-out (see that
# module's docstring) gives the stair its own real narrow-run width
# (~1.2m/~3.9ft) as one dimension and the row's full depth as the other -
# correctly shaped, but that narrow dimension can still scale down under
# _FURNITURE_MIN_BOX_W in pixels on a large plot even though the real-world
# size is fine (same category of problem the garage fix already solved).
# _furnish_staircase()'s drawing (step lines proportional to w/h, small
# fixed ~8-12px offsets for the arrow/landing) stays legible at this lower
# size, same as the garage's purely-proportional car icon.
_STAIRCASE_MIN_BOX_W = 30
_STAIRCASE_MIN_BOX_H = 30
# Half the room label's rendered block height (name + area lines), plus a
# margin - the label is centered on the room's own cy in _draw_room_label().
# Real value found by measuring an actual rendered label, not guessed: a
# fraction-only margin (e.g. "keep furniture below 58% height") looked safe
# on paper but still collided in a real render, because the clearance needed
# is a roughly FIXED pixel amount (driven by font size), not proportional to
# room size - a tall room's label doesn't get taller. Bottom-anchored
# furniture (the bed) is clamped against this so its top edge can never
# cross into the label's band, confirmed by re-rendering the exact case that
# first showed the overlap.
_LABEL_CLEARANCE_PX = 26

# Real user report (2026-09-04): room labels look "cluttered" on narrow
# rooms - a long name (e.g. "MASTER BATHROOM") rendered at a single fixed
# font size regardless of the room's own pixel width, so on a narrow room it
# visually overflowed past the room's own walls into whatever was drawn
# next to it. Fixed by _fit_room_name() below: shrink the font down to
# _NAME_MIN_FONT_SIZE before wrapping, and only wrap onto two lines if even
# the smallest allowed size still doesn't fit. Deliberately measured in
# PIXELS (the room's actual rendered box width vs. the actual rendered text
# width), not a fixed real-world "X feet" threshold - the same lesson this
# project already learned once for the garage furniture-icon gate
# (_GARAGE_MIN_BOX_W/H): whether a real-world room size overflows its label
# depends on the WHOLE PLOT's scale (TARGET_PLOT_LONGEST_SIDE_PX / longest
# side), not the room's real feet alone - an 8ft-wide room can render as a
# generous 120px box on a small plot or a cramped 40px box on a large one,
# so a feet-only threshold would either over-shrink small plots or
# under-shrink large ones. See CLAUDE.md for the real-feet-equivalent
# figures measured across this project's typical plot scales.
_NAME_MIN_FONT_SIZE = 9
_NAME_LINE_GAP_PX = 2
_LABEL_WIDTH_MARGIN_PX = 10


def _fit_room_name(
    draw: ImageDraw.ImageDraw, name: str, max_width_px: float, base_font: ImageFont.FreeTypeFont
) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Returns (lines, font) for a room name label that must fit within
    max_width_px. Tries the base font size first; if the name is too wide,
    shrinks 1px at a time down to _NAME_MIN_FONT_SIZE. If it STILL doesn't
    fit even at the minimum size, wraps onto two lines by splitting at
    whichever space keeps the two halves' character counts closest to
    balanced (e.g. "MASTER BATHROOM" -> "MASTER" / "BATHROOM") - a
    single-word name has no space to split on and is left as one line at
    the minimum size (in practice this only overflows by a few px for an
    unusually long single word, far less clutter than the original
    single-fixed-size behavior)."""
    size = base_font.size
    font = base_font
    while size > _NAME_MIN_FONT_SIZE:
        bbox = draw.textbbox((0, 0), name, font=font)
        if bbox[2] - bbox[0] <= max_width_px:
            return [name], font
        size -= 1
        font = ImageFont.load_default(size=size)

    min_font = ImageFont.load_default(size=_NAME_MIN_FONT_SIZE)
    bbox = draw.textbbox((0, 0), name, font=min_font)
    if bbox[2] - bbox[0] <= max_width_px or " " not in name:
        return [name], min_font

    words = name.split(" ")
    best_split, best_diff = 1, None
    for i in range(1, len(words)):
        left, right = " ".join(words[:i]), " ".join(words[i:])
        diff = abs(len(left) - len(right))
        if best_diff is None or diff < best_diff:
            best_split, best_diff = i, diff
    return [" ".join(words[:best_split]), " ".join(words[best_split:])], min_font


# Real walls each _furnish_* function's own shapes touch (traced directly
# from that function's geometry, not guessed) - see _draw_furniture()'s own
# comment for why this must be a per-type SET, not a single "top" constant.
_WALL_ANCHORED_FURNITURE_WALLS = {
    "kitchen": {"top", "right"},   # _furnish_kitchen: top counter run + right counter run
    "living": {"top", "left"},     # _furnish_living: sofa back (top) + side arm (left)
    "study": {"top"},              # _furnish_study: desk against the top wall
    "laundry": {"top", "left"},    # _furnish_laundry: units packed into the top-left corner
    "closet": {"top"},             # _furnish_closet: hanging rod along the top wall
}


def _draw_furniture(
    draw: ImageDraw.ImageDraw,
    rect: dict,
    plot_x0: float,
    plot_y0: float,
    scale: float,
    font: ImageFont.FreeTypeFont | None = None,
    stair_direction: str | None = None,
    door_walls: set[str] | None = None,
) -> None:
    door_walls = door_walls or set()
    x0, y0, x1, y1 = _room_bbox(rect, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    name = rect["name"].lower()

    if "garage" in name:
        min_w, min_h = _GARAGE_MIN_BOX_W, _GARAGE_MIN_BOX_H
    elif "stair" in name:
        min_w, min_h = _STAIRCASE_MIN_BOX_W, _STAIRCASE_MIN_BOX_H
    else:
        min_w, min_h = _FURNITURE_MIN_BOX_W, _FURNITURE_MIN_BOX_H
    if box_w < min_w or box_h < min_h:
        return

    cy = (y0 + y1) / 2
    # Minimum pad is INTERIOR_CLEARANCE_PX, not an arbitrary 4px - guarantees
    # furniture never visually touches/crosses the poché wall band carved
    # WALL_HALF_THICKNESS_PX in from this same outer bbox (see _carve_room).
    pad = max(float(INTERIOR_CLEARANCE_PX), min(box_w, box_h) * 0.07)
    ix0, iy0, ix1, iy1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
    iw, ih = ix1 - ix0, iy1 - iy0

    # Real gap fixed 2026-09-19 (user-reported): furniture was drawn with no
    # idea where doors were, so a bed/sofa/table could land directly in
    # front of one. The bed gets a HARD rule (see _furnish_bedroom() - it
    # must never anchor to a door wall); every other wall-anchored piece
    # below gets a simpler, practical rule: skip drawing that room's whole
    # furniture group when ANY wall its own shapes actually touch has a
    # door, rather than trying to relocate individual pieces.
    #
    # _WALL_ANCHORED_FURNITURE_WALLS records the REAL walls each function's
    # own geometry touches (read directly from each _furnish_* function
    # below, not guessed) - a real bug caught via visual inspection: an
    # earlier version of this check only ever looked at "top", but
    # _furnish_living()'s side-arm rectangle also touches "left" and
    # _furnish_kitchen()'s second counter run also touches "right" - a
    # west-facing front door still visibly overlapped the living room's
    # sofa/arm before this was corrected to check both walls. Bathroom/
    # dining/garage/staircase aren't wall-anchored to a single side
    # (already spread across corners or centered), so they're unaffected -
    # a single door rarely fully blocks them the way it would a single
    # wall-hugging piece.
    if "bath" in name or "wc" in name or "washroom" in name or "toilet" in name:
        _furnish_bathroom(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "bed" in name:
        _furnish_bedroom(draw, ix0, iy0, ix1, iy1, iw, ih, cy, door_walls)
    elif "kitchen" in name:
        if not (door_walls & _WALL_ANCHORED_FURNITURE_WALLS["kitchen"]):
            _furnish_kitchen(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "dining" in name:
        _furnish_dining(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif any(k in name for k in ("living", "lounge", "family", "drawing", "sitting")):
        if not (door_walls & _WALL_ANCHORED_FURNITURE_WALLS["living"]):
            _furnish_living(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "study" in name or "office" in name:
        if not (door_walls & _WALL_ANCHORED_FURNITURE_WALLS["study"]):
            _furnish_study(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "garage" in name:
        _furnish_garage(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "laundry" in name or "utility" in name:
        if not (door_walls & _WALL_ANCHORED_FURNITURE_WALLS["laundry"]):
            _furnish_laundry(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "closet" in name or "wardrobe" in name or "dressing" in name:
        if not (door_walls & _WALL_ANCHORED_FURNITURE_WALLS["closet"]):
            _furnish_closet(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "stair" in name and stair_direction and font is not None:
        _furnish_staircase(draw, ix0, iy0, ix1, iy1, iw, ih, stair_direction, font)
    # entry/foyer/hallway/storage/unrecognized: no furniture symbol


def _furnish_bedroom(
    draw: ImageDraw.ImageDraw,
    x0: float, y0: float, x1: float, y1: float, w: float, h: float, cy: float,
    door_walls: set[str] | None = None,
) -> None:
    """Bed placement carries a HARD rule (2026-09-19, real user report: a
    bed was drawn directly in front of a door): the bed must NEVER anchor
    to a wall that has a door opening on it - blocking the one way in/out
    of a bedroom is the worst case of the "furniture blocks the door" bug.

    The bed is bottom-anchored by default (the original, well-tested
    geometry below). If the bottom wall has a door, the WHOLE bed -
    pillows, blanket line, nightstand, wardrobe - flips to anchor against
    the top wall instead, as long as the top wall is itself door-free (a
    straight vertical mirror of the same shapes, so none of the individual
    proportions change). If BOTH bottom and top have doors (a real edge
    case - a room with 3+ doors), there's no flip left that helps, so this
    falls back to the bottom anchor anyway, since drawing no bed at all
    would be worse. Doors on the LEFT/RIGHT walls don't conflict with this
    bed's footprint (already padded off those walls by construction) - a
    known, accepted simplification, not a rotation to all 4 walls."""
    door_walls = door_walls or set()
    bed_w, bed_h = w * 0.42, h * 0.42

    if "bottom" in door_walls and "top" not in door_walls:
        # Mirrored: touched wall is the TOP (y0) instead of the bottom -
        # every offset below is the vertical mirror of the bottom-anchored
        # branch, keeping the "head (pillows/nightstand) against the touched
        # wall, foot (blanket) toward the room interior" convention (fixed
        # 2026-09-19 - both branches previously had this backwards).
        label_ceiling = cy - _LABEL_CLEARANCE_PX
        if y0 + bed_h > label_ceiling:
            bed_h = label_ceiling - y0
        if bed_h < h * 0.2:
            return  # too little vertical room left above the label to draw a legible bed
        bx0, by0, bx1, by1 = x0, y0, x0 + bed_w, y0 + bed_h
        draw.rectangle([bx0, by0, bx1, by1], outline=FURNITURE_COLOR, width=1)
        pillow_h = bed_h * 0.18
        pillow_gap = bed_w * 0.06
        pillow_w = (bed_w - 3 * pillow_gap) / 2
        for i in range(2):
            px0 = bx0 + pillow_gap + i * (pillow_w + pillow_gap)
            draw.rounded_rectangle(
                [px0, by0 + bed_h * 0.06, px0 + pillow_w, by0 + bed_h * 0.06 + pillow_h],
                radius=min(pillow_w, pillow_h) * 0.3, outline=FURNITURE_COLOR, width=1,
            )
        draw.line([(bx0, by0 + bed_h * 0.7), (bx1, by0 + bed_h * 0.7)], fill=FURNITURE_COLOR, width=1)
        nightstand = min(w, h) * 0.12
        if bx1 + nightstand + 4 <= x1:
            draw.rectangle([bx1 + 4, by0, bx1 + 4 + nightstand, by0 + nightstand], outline=FURNITURE_COLOR, width=1)
        wardrobe_w = w * 0.32
        if y1 - h * 0.14 > cy + _LABEL_CLEARANCE_PX:  # skip if it would reach into the label's lower band
            draw.rectangle([x1 - wardrobe_w, y1 - h * 0.14, x1, y1], outline=FURNITURE_COLOR, width=1)
        return

    by0 = y1 - bed_h
    # Real overlap hit and fixed via an actual rendered test (a bed's pillow
    # line crossed straight through the label text) - a proportional-only
    # margin wasn't enough, so this clamps the bed's top edge directly
    # against the label's actual clearance band. See _LABEL_CLEARANCE_PX.
    label_floor = cy + _LABEL_CLEARANCE_PX
    if by0 < label_floor:
        bed_h = y1 - label_floor
    if bed_h < h * 0.2:
        return  # too little vertical room left below the label to draw a legible bed
    bx0, by0, bx1, by1 = x0, y1 - bed_h, x0 + bed_w, y1
    draw.rectangle([bx0, by0, bx1, by1], outline=FURNITURE_COLOR, width=1)
    # Two pillows (rounded rectangles, side by side) instead of a plain
    # pillow line - reads as an actual made bed, not just a labeled box.
    # Head (pillows) against the touched wall (by1, the bottom wall here),
    # foot (blanket, below) toward the room interior (by0) - fixed
    # 2026-09-19, real user report the bed was drawn upside down.
    pillow_h = bed_h * 0.18
    pillow_gap = bed_w * 0.06
    pillow_w = (bed_w - 3 * pillow_gap) / 2
    for i in range(2):
        px0 = bx0 + pillow_gap + i * (pillow_w + pillow_gap)
        draw.rounded_rectangle(
            [px0, by1 - bed_h * 0.06 - pillow_h, px0 + pillow_w, by1 - bed_h * 0.06],
            radius=min(pillow_w, pillow_h) * 0.3, outline=FURNITURE_COLOR, width=1,
        )
    # A folded-back blanket line near the foot of the bed (toward the
    # interior, away from the head/wall).
    draw.line(
        [(bx0, by1 - bed_h * 0.7), (bx1, by1 - bed_h * 0.7)], fill=FURNITURE_COLOR, width=1
    )
    nightstand = min(w, h) * 0.12
    if bx1 + nightstand + 4 <= x1:
        draw.rectangle([bx1 + 4, by1 - nightstand, bx1 + 4 + nightstand, by1], outline=FURNITURE_COLOR, width=1)
    wardrobe_w = w * 0.32
    if y0 + h * 0.14 < cy - _LABEL_CLEARANCE_PX:  # skip if it would reach into the label's upper band
        draw.rectangle([x1 - wardrobe_w, y0, x1, y0 + h * 0.14], outline=FURNITURE_COLOR, width=1)


def _furnish_living(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    depth = min(w, h) * 0.18
    sofa_len = w * 0.55
    draw.rectangle([x0, y0, x0 + sofa_len, y0 + depth], outline=FURNITURE_COLOR, width=1)  # sofa back run
    # Cushion divider ticks along the sofa back - reads as an actual sofa,
    # not just an unmarked bench box.
    cushion_count = max(2, int(sofa_len // (depth * 1.4)))
    for i in range(1, cushion_count):
        cx = x0 + sofa_len * i / cushion_count
        draw.line([(cx, y0 + 2), (cx, y0 + depth - 2)], fill=FURNITURE_COLOR, width=1)
    draw.rectangle([x0, y0, x0 + depth, y0 + h * 0.5], outline=FURNITURE_COLOR, width=1)  # sofa side arm
    table = min(w, h) * 0.14
    tx, ty = x0 + w * 0.35, y0 + h * 0.62
    draw.rectangle([tx, ty, tx + table, ty + table], outline=FURNITURE_COLOR, width=1)
    chair = min(w, h) * 0.15
    draw.rounded_rectangle(
        [x1 - chair, y1 - chair, x1, y1], radius=chair * 0.2, outline=FURNITURE_COLOR, width=1
    )  # armchair, rounded to read distinct from the sofa's square corners


def _furnish_dining(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    tw, th = w * 0.45, h * 0.3
    tx0, ty0 = x0 + (w - tw) / 2, y0 + (h - th) / 2
    tx1, ty1 = tx0 + tw, ty0 + th
    draw.rectangle([tx0, ty0, tx1, ty1], outline=FURNITURE_COLOR, width=1)
    chair = min(w, h) * 0.1
    for cx in (tx0 + tw * 0.25, tx0 + tw * 0.75):
        if ty0 - chair - 3 > y0:
            draw.rectangle([cx - chair / 2, ty0 - chair - 3, cx + chair / 2, ty0 - 3], outline=FURNITURE_COLOR, width=1)
        if ty1 + chair + 3 < y1:
            draw.rectangle([cx - chair / 2, ty1 + 3, cx + chair / 2, ty1 + chair + 3], outline=FURNITURE_COLOR, width=1)


def _furnish_kitchen(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    depth = min(w, h) * 0.16
    draw.rectangle([x0, y0, x1, y0 + depth], outline=FURNITURE_COLOR, width=1)  # counter run along the top wall
    draw.rectangle([x1 - depth, y0, x1, y0 + h * 0.6], outline=FURNITURE_COLOR, width=1)  # counter run along the side wall
    sink_r = depth * 0.3
    scx = x0 + w * 0.3
    draw.ellipse([scx - sink_r, y0 + depth * 0.2, scx + sink_r, y0 + depth * 0.8], outline=FURNITURE_COLOR, width=1)
    # Four stove burners on the counter run, opposite the sink - a plain
    # counter box read as "some furniture-shaped rectangle"; burners make it
    # unmistakably a stove.
    burner_r = depth * 0.14
    stove_cx = x0 + w * 0.62
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        bx = stove_cx + dx * burner_r * 1.6
        by = y0 + depth * 0.5 + dy * burner_r * 1.6
        draw.ellipse([bx - burner_r, by - burner_r, bx + burner_r, by + burner_r], outline=FURNITURE_COLOR, width=1)
    fridge = depth * 1.4
    draw.rectangle([x0, y1 - fridge, x0 + fridge * 0.7, y1], outline=FURNITURE_COLOR, width=1)
    # Fridge door split line - a plain box reads as generic; the split line
    # is the one detail that makes it legible as a fridge specifically.
    draw.line(
        [(x0 + fridge * 0.35, y1 - fridge), (x0 + fridge * 0.35, y1)], fill=FURNITURE_COLOR, width=1
    )


def _furnish_bathroom(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    toilet_w = min(w, h) * 0.24
    draw.ellipse([x0, y1 - toilet_w * 1.3, x0 + toilet_w, y1], outline=FURNITURE_COLOR, width=1)
    draw.rectangle([x0, y1 - toilet_w * 1.6, x0 + toilet_w, y1 - toilet_w * 1.3], outline=FURNITURE_COLOR, width=1)
    basin_w = min(w, h) * 0.3
    draw.rectangle([x1 - basin_w, y0, x1, y0 + basin_w * 0.55], outline=FURNITURE_COLOR, width=1)

    tub = min(w, h) * 0.42
    draw.rectangle([x0, y0, x0 + tub, y0 + tub * 0.6], outline=FURNITURE_COLOR, width=1)
    draw.line([(x0, y0), (x0 + tub, y0 + tub * 0.6)], fill=FURNITURE_COLOR, width=1)
    draw.line([(x0 + tub, y0), (x0, y0 + tub * 0.6)], fill=FURNITURE_COLOR, width=1)

    # A separate stand-up shower stall (square, diagonal drain-pan mark) in
    # the opposite corner, only when there's real room for one alongside
    # the tub/toilet/basin already placed - a cramped half-bath just gets
    # those three, not a squeezed-in fourth fixture that would look wrong
    # at small scale.
    shower_size = min(w, h) * 0.3
    has_room_for_shower = w > tub + shower_size + basin_w * 0.3 and h > tub * 0.6 + shower_size + toilet_w * 1.3
    if has_room_for_shower:
        sx0, sy0 = x1 - shower_size, y1 - shower_size
        draw.rectangle([sx0, sy0, x1, y1], outline=FURNITURE_COLOR, width=1)
        draw.line([(sx0, sy0), (x1, y1)], fill=FURNITURE_COLOR, width=1)


def _furnish_study(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    desk_w, desk_h = w * 0.5, min(w, h) * 0.16
    draw.rectangle([x0, y0, x0 + desk_w, y0 + desk_h], outline=FURNITURE_COLOR, width=1)
    chair = desk_h * 0.9
    cx0 = x0 + desk_w * 0.3
    draw.rectangle([cx0, y0 + desk_h + 4, cx0 + chair, y0 + desk_h + 4 + chair], outline=FURNITURE_COLOR, width=1)


def _furnish_garage(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    car_w, car_h = w * 0.6, h * 0.7
    cx0, cy0 = x0 + (w - car_w) / 2, y0 + (h - car_h) / 2
    draw.rounded_rectangle(
        [cx0, cy0, cx0 + car_w, cy0 + car_h], radius=min(car_w, car_h) * 0.15, outline=FURNITURE_COLOR, width=1
    )


def _furnish_laundry(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    unit = min(w, h) * 0.32
    gap = unit * 0.15
    for i, cx in enumerate((x0 + unit / 2, x0 + unit + gap + unit / 2)):
        cy = y0 + unit / 2
        draw.rectangle([cx - unit / 2, cy - unit / 2, cx + unit / 2, cy + unit / 2], outline=FURNITURE_COLOR, width=1)
        drum_r = unit * 0.32
        draw.ellipse([cx - drum_r, cy - drum_r, cx + drum_r, cy + drum_r], outline=FURNITURE_COLOR, width=1)


def _furnish_closet(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    depth = min(w, h) * 0.22
    draw.rectangle([x0, y0, x1, y0 + depth], outline=FURNITURE_COLOR, width=1)
    rod_y = y0 + depth * 0.55
    draw.line([(x0 + 2, rod_y), (x1 - 2, rod_y)], fill=FURNITURE_COLOR, width=1)  # hanging rod
    hanger_count = max(3, int((x1 - x0) // (depth * 0.9)))
    for i in range(hanger_count):
        hx = x0 + (x1 - x0) * (i + 0.5) / hanger_count
        draw.line([(hx, rod_y), (hx, rod_y + depth * 0.35)], fill=FURNITURE_COLOR, width=1)


# ---- Staircase ----
# A REAL reserved room (2026-08-29), not a decorative mark inside whichever
# room happened to be biggest - see floor_layout.py's circulation zone and
# generate_house.py's per-floor injection. _furnish_staircase() draws INSIDE
# that room's own real bounds, same as every other _furnish_* function -
# it only decides HOW to draw the run, not WHERE the room goes.


def _furnish_staircase(
    draw: ImageDraw.ImageDraw,
    x0: float, y0: float, x1: float, y1: float, w: float, h: float,
    direction: str,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Shape is chosen DYNAMICALLY from the room's own actual (post-layout)
    aspect ratio, not guessed in advance: an elongated rectangle gets a
    straight run; a squarer one gets an L-shaped run with a landing, since a
    straight run wouldn't fit comfortably. direction is "UP" (every floor
    except the top) or "DN" (top floor only)."""
    aspect = w / h if h else 1.0
    elongated = aspect > 1.7 or aspect < 1 / 1.7
    if elongated:
        _draw_straight_stair_run(draw, x0, y0, x1, y1, w, h, direction, font)
    else:
        _draw_l_shaped_stair_run(draw, x0, y0, x1, y1, w, h, direction, font)


def _draw_straight_stair_run(
    draw: ImageDraw.ImageDraw,
    x0: float, y0: float, x1: float, y1: float, w: float, h: float,
    direction: str,
    font: ImageFont.FreeTypeFont,
) -> None:
    horizontal = w >= h
    if horizontal:
        steps = max(4, int(w // 16))
        for i in range(1, steps):
            x = x0 + w * i / steps
            draw.line([(x, y0), (x, y1)], fill=FURNITURE_COLOR, width=1)
        ay = (y0 + y1) / 2
        if direction == "UP":
            draw.line([(x1 - 8, ay), (x0 + 10, ay)], fill=FURNITURE_COLOR, width=2)
            draw.polygon([(x0 + 4, ay), (x0 + 12, ay - 5), (x0 + 12, ay + 5)], fill=FURNITURE_COLOR)
        else:
            draw.line([(x0 + 8, ay), (x1 - 10, ay)], fill=FURNITURE_COLOR, width=2)
            draw.polygon([(x1 - 4, ay), (x1 - 12, ay - 5), (x1 - 12, ay + 5)], fill=FURNITURE_COLOR)
    else:
        steps = max(4, int(h // 16))
        for i in range(1, steps):
            y = y0 + h * i / steps
            draw.line([(x0, y), (x1, y)], fill=FURNITURE_COLOR, width=1)
        ax = (x0 + x1) / 2
        if direction == "UP":
            draw.line([(ax, y1 - 8), (ax, y0 + 10)], fill=FURNITURE_COLOR, width=2)
            draw.polygon([(ax, y0 + 4), (ax - 5, y0 + 12), (ax + 5, y0 + 12)], fill=FURNITURE_COLOR)
        else:
            draw.line([(ax, y0 + 8), (ax, y1 - 10)], fill=FURNITURE_COLOR, width=2)
            draw.polygon([(ax, y1 - 4), (ax - 5, y1 - 12), (ax + 5, y1 - 12)], fill=FURNITURE_COLOR)
    # Corner label, not centered - the room's own name/area label (drawn
    # afterward, centered) would otherwise collide with a centered direction
    # marker.
    draw.text((x0 + 4, y0 + 2), direction, font=font, fill=FURNITURE_COLOR)


def _draw_l_shaped_stair_run(
    draw: ImageDraw.ImageDraw,
    x0: float, y0: float, x1: float, y1: float, w: float, h: float,
    direction: str,
    font: ImageFont.FreeTypeFont,
) -> None:
    """Two short flights meeting at a corner landing - flight 1 runs along
    the top edge to the landing, flight 2 continues down the side to the
    bottom. A legible mark of "this is an L-shaped run", not a
    construction-grade layout, same precedent as doors/windows elsewhere in
    this module."""
    landing = min(w, h) * 0.35
    flight1_x1 = x0 + w - landing
    flight1_steps = max(3, int((flight1_x1 - x0) // 14))
    for i in range(1, flight1_steps):
        x = x0 + (flight1_x1 - x0) * i / flight1_steps
        draw.line([(x, y0), (x, y0 + landing)], fill=FURNITURE_COLOR, width=1)

    flight2_y0 = y0 + landing
    flight2_steps = max(3, int((y1 - flight2_y0) // 14))
    for i in range(1, flight2_steps):
        y = flight2_y0 + (y1 - flight2_y0) * i / flight2_steps
        draw.line([(flight1_x1, y), (x1, y)], fill=FURNITURE_COLOR, width=1)

    draw.rectangle([flight1_x1, y0, x1, y0 + landing], outline=FURNITURE_COLOR, width=1)
    draw.text((x0 + 4, y0 + 2), direction, font=font, fill=FURNITURE_COLOR)


# ---- Labels ----


def _compute_label_layout(draw: ImageDraw.ImageDraw, rect: dict, plot_x0: float, plot_y0: float, scale: float, unit: str, name_font: ImageFont.FreeTypeFont, small_font: ImageFont.FreeTypeFont) -> dict | None:
    """Pure geometry for a room's label (None when the room is too small to
    label). Shared by _draw_room_label() and the column pass so a structural
    column can never be placed under the label text. "box" is the padded
    pixel rectangle (x0, y0, x1, y1) the text occupies."""
    x0, y0, x1, y1 = _room_bbox(rect, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    if box_w < 46 or box_h < 34:
        return None  # too small to label legibly

    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # Parts-aware (2026-10): the label is POSITIONED/sized on the room's
    # largest part, but the printed area is the room's REAL total area
    # ("_area_override" for a multi-part room).
    area_units = rect.get("_area_override", rect["w"] * rect["h"])
    if rect.get("_multi_part"):
        # An L/U-shaped room has no single W x H (the largest part's size
        # next to the summed area would not match) - area only.
        area_text = f"{area_units:.0f} sq {unit}"
    else:
        area_text = f"{rect['w']:.1f}x{rect['h']:.1f} {unit} ({area_units:.0f} sq {unit})"
    display_name = rect["name"].upper()

    max_name_width = max(box_w - _LABEL_WIDTH_MARGIN_PX, 10)
    name_lines, fitted_font = _fit_room_name(draw, display_name, max_name_width, name_font)

    line_dims = [_text_dims(draw, line, fitted_font) for line in name_lines]
    name_w = max((w for w, _ in line_dims), default=0)
    line_h = max((h for _, h in line_dims), default=0)
    name_h = line_h * len(name_lines) + _NAME_LINE_GAP_PX * (len(name_lines) - 1)

    show_area = box_h >= 50
    area_w = area_h = 0
    divider_gap = 6
    if show_area:
        area_w, area_h = _text_dims(draw, area_text, small_font)
        if name_h + divider_gap + area_h > box_h - 8:
            show_area = False
        elif area_w > max_name_width:
            # Purely supplementary line - dropped rather than shrunk.
            show_area = False

    total_h = name_h + (divider_gap + area_h if show_area else 0)
    text_w = max(name_w, area_w if show_area else 0)
    pad = LABEL_BOX_PAD_PX
    return {
        "cx": cx, "cy": cy, "name_lines": name_lines, "fitted_font": fitted_font,
        "line_dims": line_dims, "name_w": name_w, "area_text": area_text,
        "show_area": show_area, "area_w": area_w, "area_h": area_h,
        "divider_gap": divider_gap, "total_h": total_h,
        "box": (cx - text_w / 2 - pad, cy - total_h / 2 - pad, cx + text_w / 2 + pad, cy + total_h / 2 + pad),
    }


def _draw_room_label(draw: ImageDraw.ImageDraw, rect: dict, plot_x0: float, plot_y0: float, scale: float, unit: str, name_font: ImageFont.FreeTypeFont, small_font: ImageFont.FreeTypeFont) -> None:
    lay = _compute_label_layout(draw, rect, plot_x0, plot_y0, scale, unit, name_font, small_font)
    if lay is None:
        return
    cx = lay["cx"]
    y_cursor = lay["cy"] - lay["total_h"] / 2
    for i, line in enumerate(lay["name_lines"]):
        line_w, line_height = lay["line_dims"][i]
        _draw_faux_bold_text(draw, (cx - line_w / 2, y_cursor), line, lay["fitted_font"], INK)
        y_cursor += line_height + (_NAME_LINE_GAP_PX if i < len(lay["name_lines"]) - 1 else 0)

    if lay["show_area"]:
        # A thin divider rule between name and area.
        divider_gap, area_w = lay["divider_gap"], lay["area_w"]
        divider_y = y_cursor + divider_gap / 2
        divider_half_w = max(lay["name_w"], area_w) * 0.32
        draw.line(
            [(cx - divider_half_w, divider_y), (cx + divider_half_w, divider_y)], fill=INK_SOFT, width=1
        )
        draw.text((cx - area_w / 2, divider_y + divider_gap / 2), lay["area_text"], font=small_font, fill=INK_SOFT)


def _text_dims(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[float, float]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_faux_bold_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    x, y = xy
    draw.text((x, y), text, font=font, fill=fill)
    draw.text((x + 1, y), text, font=font, fill=fill)


def _draw_centered_text(draw: ImageDraw.ImageDraw, center_xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    cx, cy = center_xy
    draw.text((cx - w / 2, cy - h / 2), text, font=font, fill=fill)


def _draw_right_aligned_text(draw: ImageDraw.ImageDraw, right_center_xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    rx, cy = right_center_xy
    draw.text((rx - w, cy - h / 2), text, font=font, fill=fill)


# ---- Dimension lines ----


_DIM_MIN_SEGMENT_M = 0.6  # a chain segment shorter than this is merged into a neighbour


def dimension_chain_boundaries(
    rects: list[dict], fp: tuple[float, float, float, float], axis: str, plot_extent: float, unit: str
) -> list[float]:
    """Boundaries (plot units, sorted, always starting at 0 and ending at
    plot_extent) of the exterior dimension chain along one side.

    axis "x" is the top wall (footprint y0), "y" the left wall (footprint x0).
    Only parts whose edge actually lies on that exterior wall contribute their
    extents (an interior room's own bbox edges are cell-size artefacts, not
    dimensions); the yard setback segments [0, fp_lo] and [fp_hi, plot_extent]
    are added so the chain still spans the plot. Segments shorter than
    _DIM_MIN_SEGMENT_M are merged into the shorter neighbour."""
    fx0, fy0, fx1, fy1 = fp
    pts = {0.0, round(plot_extent, 3)}
    if axis == "x":
        pts |= {round(fx0, 3), round(fx1, 3)}
        for r in rects:
            for part in room_parts(r):
                if abs(part["y"] - fy0) < _EDGE_TOL:
                    pts |= {round(part["x"], 3), round(part["x"] + part["w"], 3)}
    else:
        pts |= {round(fy0, 3), round(fy1, 3)}
        for r in rects:
            for part in room_parts(r):
                if abs(part["x"] - fx0) < _EDGE_TOL:
                    pts |= {round(part["y"], 3), round(part["y"] + part["h"], 3)}
    bounds = sorted(p for p in pts if -1e-6 <= p <= plot_extent + 1e-6)
    min_len = to_plot_unit(_DIM_MIN_SEGMENT_M, unit)
    while len(bounds) > 2:
        segs = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
        i = min(range(len(segs)), key=lambda k: segs[k])
        if segs[i] >= min_len:
            break
        # Drop the boundary on the side of the shorter neighbour (the end
        # anchors 0 / plot_extent are never dropped).
        left_ok, right_ok = i > 0, i + 1 < len(bounds) - 1
        if left_ok and right_ok:
            drop = i if segs[i - 1] <= segs[i + 1] else i + 1
        elif left_ok:
            drop = i
        else:
            drop = i + 1
        bounds.pop(drop)
    return bounds


def _draw_dimensions(draw: ImageDraw.ImageDraw, rects: list[dict], length: float, width: float, plot_x0: float, plot_y0: float, plot_x1: float, plot_y1: float, scale: float, unit: str, font: ImageFont.FreeTypeFont) -> None:
    fp = footprint_bbox(rects) or (0.0, 0.0, length, width)
    x_boundaries = dimension_chain_boundaries(rects, fp, "x", length, unit)
    y_boundaries = dimension_chain_boundaries(rects, fp, "y", width, unit)

    # Top: per-segment widths, then one overall length dimension further out.
    dim_y = plot_y0 - DIM_GUTTER_PX
    draw.line([(plot_x0, dim_y), (plot_x1, dim_y)], fill=DIM_LINE_COLOR, width=1)
    for xb in x_boundaries:
        px = plot_x0 + xb * scale
        draw.line([(px, dim_y - DIM_TICK_PX / 2), (px, dim_y + DIM_TICK_PX / 2)], fill=DIM_LINE_COLOR, width=1)
    last_right = -1e9
    for a, b in zip(x_boundaries, x_boundaries[1:]):
        seg = b - a
        text = f"{seg:.1f}"
        tw, _th = _text_dims(draw, text, font)
        cx = plot_x0 + (a + b) / 2 * scale
        if cx - tw / 2 < last_right + 3:
            continue  # would collide with the previous label
        _draw_centered_text(draw, (cx, dim_y - 15), text, font, DIM_LINE_COLOR)
        last_right = cx + tw / 2

    overall_y = dim_y - DIM_OVERALL_OFFSET_PX
    draw.line([(plot_x0, overall_y), (plot_x1, overall_y)], fill=DIM_LINE_COLOR, width=1)
    draw.line([(plot_x0, overall_y - DIM_TICK_PX / 2), (plot_x0, overall_y + DIM_TICK_PX / 2)], fill=DIM_LINE_COLOR, width=1)
    draw.line([(plot_x1, overall_y - DIM_TICK_PX / 2), (plot_x1, overall_y + DIM_TICK_PX / 2)], fill=DIM_LINE_COLOR, width=1)
    _draw_centered_text(draw, ((plot_x0 + plot_x1) / 2, overall_y - 15), f"{length:.1f}{unit}", font, INK)

    # Left: per-segment heights, then one overall width dimension further out.
    dim_x = plot_x0 - DIM_GUTTER_PX
    draw.line([(dim_x, plot_y0), (dim_x, plot_y1)], fill=DIM_LINE_COLOR, width=1)
    for yb in y_boundaries:
        py = plot_y0 + yb * scale
        draw.line([(dim_x - DIM_TICK_PX / 2, py), (dim_x + DIM_TICK_PX / 2, py)], fill=DIM_LINE_COLOR, width=1)
    last_bottom = -1e9
    for a, b in zip(y_boundaries, y_boundaries[1:]):
        seg = b - a
        text = f"{seg:.1f}"
        _tw, th = _text_dims(draw, text, font)
        cy = plot_y0 + (a + b) / 2 * scale
        if cy - th / 2 < last_bottom + 2:
            continue
        _draw_right_aligned_text(draw, (dim_x - 8, cy), text, font, DIM_LINE_COLOR)
        last_bottom = cy + th / 2

    overall_x = dim_x - DIM_OVERALL_OFFSET_PX
    draw.line([(overall_x, plot_y0), (overall_x, plot_y1)], fill=DIM_LINE_COLOR, width=1)
    draw.line([(overall_x - DIM_TICK_PX / 2, plot_y0), (overall_x + DIM_TICK_PX / 2, plot_y0)], fill=DIM_LINE_COLOR, width=1)
    draw.line([(overall_x - DIM_TICK_PX / 2, plot_y1), (overall_x + DIM_TICK_PX / 2, plot_y1)], fill=DIM_LINE_COLOR, width=1)
    _draw_right_aligned_text(draw, (overall_x - 8, (plot_y0 + plot_y1) / 2), f"{width:.1f}{unit}", font, INK)


# ---- North arrow / scale bar / title block ----


def _draw_north_arrow(draw: ImageDraw.ImageDraw, cx: float, cy: float, font: ImageFont.FreeTypeFont) -> None:
    r = 16
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=INK_SOFT, width=1)
    draw.line([(cx, cy + r - 3), (cx, cy - r + 3)], fill=INK, width=2)
    draw.polygon([(cx, cy - r + 3), (cx - 5, cy - r + 11), (cx + 5, cy - r + 11)], fill=INK)
    _draw_centered_text(draw, (cx, cy + r + 12), "N", font, INK)


def _nice_number(value: float) -> float:
    """Rounds value up to a "nice" 1/2/5 x 10^n number - used for both the
    background grid spacing and the scale bar interval, same convention as
    typical chart-axis tick selection."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10 ** exponent)
    if fraction < 1.5:
        nice = 1
    elif fraction < 3:
        nice = 2
    elif fraction < 7:
        nice = 5
    else:
        nice = 10
    return nice * (10 ** exponent)


def _draw_grid(draw: ImageDraw.ImageDraw, img_w: int, img_h: int, plot_x0: float, plot_y0: float, plot_x1: float, plot_y1: float, scale: float) -> None:
    step_units = _nice_number(max((plot_x1 - plot_x0) / scale, (plot_y1 - plot_y0) / scale) / 6)
    step_px = step_units * scale
    if step_px < 6:
        return

    x = plot_x0 % step_px
    while x <= img_w:
        draw.line([(x, 0), (x, img_h)], fill=GRID_LINE, width=1)
        x += step_px

    y = plot_y0 % step_px
    while y <= img_h:
        draw.line([(0, y), (img_w, y)], fill=GRID_LINE, width=1)
        y += step_px


def _draw_scale_bar(draw: ImageDraw.ImageDraw, x0: float, y: float, scale: float, unit: str, font: ImageFont.FreeTypeFont) -> None:
    segments = 4
    step_units = _nice_number(70 / scale)
    bar_w = step_units * scale * segments
    draw.line([(x0, y), (x0 + bar_w, y)], fill=INK, width=2)
    for i in range(segments + 1):
        px = x0 + i * step_units * scale
        draw.line([(px, y - 5), (px, y + 5)], fill=INK, width=2)
        _draw_centered_text(draw, (px, y + 16), f"{i * step_units:.0f}", font, INK)
    _draw_centered_text(draw, (x0 + bar_w / 2, y - 16), f"Scale ({unit})", font, INK_SOFT)


def _draw_title_block(draw: ImageDraw.ImageDraw, floor_number: int, length: float, width: float, unit: str, scale: float, img_w: int, img_h: int, title_font: ImageFont.FreeTypeFont, small_font: ImageFont.FreeTypeFont) -> None:
    # Plain ASCII only - PIL's default bitmap font renders unsupported glyphs
    # (em dash, the approx sign, superscripts) as visible tofu boxes, a real
    # bug hit and fixed while building this: "-", "~", and "sq {unit}" read
    # identically but always render.
    draw.text((MARGIN_LEFT_PX, 14), f"FLOOR {floor_number} - COMPUTED LAYOUT", font=title_font, fill=INK)
    block_y = img_h - 58
    draw.line([(MARGIN_LEFT_PX, block_y - 10), (img_w - MARGIN_RIGHT_PX, block_y - 10)], fill=GRID_LINE, width=1)
    draw.text(
        (MARGIN_LEFT_PX, block_y),
        f"Plot: {length:g} x {width:g} {unit}   |   Scale: 1{unit} ~ {scale:.1f}px",
        font=small_font,
        fill=INK_SOFT,
    )
    draw.text(
        (MARGIN_LEFT_PX, block_y + 18),
        "Concept layout, computed from your stated dimensions - not to construction spec",
        font=small_font,
        fill=INK_SOFT,
    )
