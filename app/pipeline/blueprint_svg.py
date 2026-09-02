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

from app.pipeline.floor_layout import _zone_key
from app.pipeline.room_specs import classify_room_category

# ---- Canvas layout ----
TARGET_PLOT_LONGEST_SIDE_PX = 780
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
FURNITURE_COLOR = INK_SOFT  # softer than WALL_COLOR - visually secondary to structure


def render_floor_blueprint(floor_number: int, rects: list[dict], dimensions: dict, total_floors: int = 1) -> bytes:
    """rects: output of floor_layout.layout_floor - [{"name","x","y","w","h"}, ...]
    in the same real-world unit as dimensions. total_floors: the building's
    total floor count. When > 1, generate_house.py has already injected a
    REAL "Staircase" room into `rects` (see floor_layout.py's circulation
    zone) - this function just draws the UP/DN stair symbol INSIDE that
    room like any other furniture (see _furnish_staircase()), it doesn't
    pick where the room goes. A single-storey building has no such room and
    nothing is drawn. Returns PNG bytes."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit", "") or "units"
    scale = TARGET_PLOT_LONGEST_SIDE_PX / max(length, width, 1)

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

    # Drawn across the WHOLE canvas (not just the plot box) - room fills
    # cover the plot area completely (layout_floor always tiles it edge to
    # edge), so a plot-only grid would be entirely invisible. Extending it
    # into the margins gives a "drafting sheet" backdrop that's actually seen.
    _draw_grid(draw, img_w, img_h, plot_x0, plot_y0, plot_x1, plot_y1, scale)

    # Solid poché wall base - fill the whole plot footprint (extended
    # outward for the exterior wall) black, THEN carve each room's
    # interior back out of it. See module docstring for why this single
    # fill+carve technique replaces all the old separate wall-line drawing.
    _draw_wall_base(draw, plot_x0, plot_y0, plot_x1, plot_y1)
    for rect in rects:
        _carve_room(draw, _room_bbox(rect, plot_x0, plot_y0, scale))

    # The staircase's UP/DN direction (None on a single-storey building,
    # where there's no "Staircase" room to begin with) - threaded into the
    # furniture pass so _furnish_staircase() can draw the right arrow/label
    # inside whichever rect is actually named "Staircase" (see
    # floor_layout.py/generate_house.py for how that room gets reserved).
    stair_direction = ("UP" if floor_number < total_floors else "DN") if total_floors > 1 else None
    for rect in rects:
        _draw_furniture(draw, rect, plot_x0, plot_y0, scale, small_font, stair_direction)

    door_len_px = max(10.0, min(26.0, 2.6 * scale))
    has_hallway = any(r["name"] == "Hallway" for r in rects)
    for a, b in combinations(rects, 2):
        edge = _shared_edge(a, b)
        if edge is None:
            continue
        if has_hallway and _should_suppress_direct_door(a, b):
            continue
        _draw_door(draw, edge, plot_x0, plot_y0, scale, door_len_px)

    for rect in rects:
        _draw_windows(draw, rect, length, width, plot_x0, plot_y0, scale)

    for rect in rects:
        _draw_room_label(draw, rect, plot_x0, plot_y0, scale, unit, label_font, small_font)

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


def _carve_room(draw: ImageDraw.ImageDraw, bbox: tuple[float, float, float, float]) -> None:
    """Carves one room's interior back out of the solid wall base, inset by
    WALL_HALF_THICKNESS_PX on every side - whatever black remains between
    this and neighboring rooms' own carve-outs is the poché wall."""
    x0, y0, x1, y1 = bbox
    inset = WALL_HALF_THICKNESS_PX
    if x1 - x0 <= 2 * inset or y1 - y0 <= 2 * inset:
        return  # room too small to carve a real interior - leave it solid wall rather than invert
    draw.rectangle([x0 + inset, y0 + inset, x1 - inset, y1 - inset], fill=ROOM_FILL)


# ---- Doors ----

_EDGE_TOL = 1e-4
_MIN_SHARED_EDGE_UNITS = 0.3  # ignore corner-touching pairs with no real shared wall


def _shared_edge(a: dict, b: dict) -> dict | None:
    """Returns the shared-wall segment between two adjacent room rects, or
    None if they don't share a meaningful wall. orientation "vertical" means
    the wall itself runs vertically (a's right edge touches b's left, or vice
    versa); "horizontal" means the wall runs horizontally."""
    if abs((a["x"] + a["w"]) - b["x"]) < _EDGE_TOL or abs((b["x"] + b["w"]) - a["x"]) < _EDGE_TOL:
        x = a["x"] + a["w"] if abs((a["x"] + a["w"]) - b["x"]) < _EDGE_TOL else a["x"]
        y_start, y_end = max(a["y"], b["y"]), min(a["y"] + a["h"], b["y"] + b["h"])
        if y_end - y_start > _MIN_SHARED_EDGE_UNITS:
            return {"orientation": "vertical", "pos": x, "start": y_start, "end": y_end}

    if abs((a["y"] + a["h"]) - b["y"]) < _EDGE_TOL or abs((b["y"] + b["h"]) - a["y"]) < _EDGE_TOL:
        y = a["y"] + a["h"] if abs((a["y"] + a["h"]) - b["y"]) < _EDGE_TOL else a["y"]
        x_start, x_end = max(a["x"], b["x"]), min(a["x"] + a["w"], b["x"] + b["w"])
        if x_end - x_start > _MIN_SHARED_EDGE_UNITS:
            return {"orientation": "horizontal", "pos": y, "start": x_start, "end": x_end}

    return None


def _should_suppress_direct_door(a: dict, b: dict) -> bool:
    """Real circulation corridor (see floor_layout.py's module docstring):
    when a Hallway rect exists on this floor, a direct door between two
    adjacent private-zone rooms is suppressed UNLESS at least one of them is
    a bathroom - two bedrooms (or other private rooms) shouldn't open
    straight into each other, they should each open onto the hallway
    instead (which the generic door loop above already draws for free,
    since the hallway shares a wall with every room it serves). A
    bedroom-bathroom pair keeps its direct door (a realistic ensuite),
    same "master bedroom next to its own ensuite bathroom" adjacency
    floor_layout.py's _pack_row() already preserves. Only called when
    has_hallway is True - a private zone with no corridor (too few rooms,
    or not enough space) keeps the original "any shared edge gets a door"
    behavior unconditionally, since direct adjacency is its only way in."""
    if a["name"] == "Hallway" or b["name"] == "Hallway":
        return False
    if _zone_key(a["name"]) != 2 or _zone_key(b["name"]) != 2:
        return False
    return classify_room_category(a["name"]) != "bathroom" and classify_room_category(b["name"]) != "bathroom"


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


# ---- Windows ----


def _draw_windows(draw: ImageDraw.ImageDraw, rect: dict, plot_length: float, plot_width: float, plot_x0: float, plot_y0: float, scale: float) -> None:
    """Marks a window on each of the room's edges that lie on the exterior
    plot boundary - cuts straight through the solid exterior wall band (back
    to PAPER, i.e. genuinely open to the outside, not just a color change)
    with a colored glazing line centered on the cut."""
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    edges = []
    if abs(x) < _EDGE_TOL:
        edges.append(("left", y, y + h))
    if abs((x + w) - plot_length) < _EDGE_TOL:
        edges.append(("right", y, y + h))
    if abs(y) < _EDGE_TOL:
        edges.append(("top", x, x + w))
    if abs((y + h) - plot_width) < _EDGE_TOL:
        edges.append(("bottom", x, x + w))

    for side, start_units, end_units in edges:
        seg_len_units = end_units - start_units
        win_len_units = min(seg_len_units * 0.4, seg_len_units - 0.2)
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
            px = plot_x0 + (0.0 if side == "left" else plot_length) * scale
            py0, py1 = plot_y0 + w_start_px * scale, plot_y0 + w_end_px * scale
            draw.line([(px, py0), (px, py1)], fill=PAPER, width=erase_width)
            draw.line([(px, py0), (px, py1)], fill=WINDOW_COLOR, width=3)
        else:
            py = plot_y0 + (0.0 if side == "top" else plot_width) * scale
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


def _draw_furniture(
    draw: ImageDraw.ImageDraw,
    rect: dict,
    plot_x0: float,
    plot_y0: float,
    scale: float,
    font: ImageFont.FreeTypeFont | None = None,
    stair_direction: str | None = None,
) -> None:
    x0, y0, x1, y1 = _room_bbox(rect, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    if box_w < _FURNITURE_MIN_BOX_W or box_h < _FURNITURE_MIN_BOX_H:
        return

    cy = (y0 + y1) / 2
    # Minimum pad is INTERIOR_CLEARANCE_PX, not an arbitrary 4px - guarantees
    # furniture never visually touches/crosses the poché wall band carved
    # WALL_HALF_THICKNESS_PX in from this same outer bbox (see _carve_room).
    pad = max(float(INTERIOR_CLEARANCE_PX), min(box_w, box_h) * 0.07)
    ix0, iy0, ix1, iy1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
    iw, ih = ix1 - ix0, iy1 - iy0
    name = rect["name"].lower()

    if "bath" in name or "wc" in name or "washroom" in name or "toilet" in name:
        _furnish_bathroom(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "bed" in name:
        _furnish_bedroom(draw, ix0, iy0, ix1, iy1, iw, ih, cy)
    elif "kitchen" in name:
        _furnish_kitchen(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "dining" in name:
        _furnish_dining(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif any(k in name for k in ("living", "lounge", "family", "drawing")):
        _furnish_living(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "study" in name or "office" in name:
        _furnish_study(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "garage" in name:
        _furnish_garage(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "laundry" in name or "utility" in name:
        _furnish_laundry(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "closet" in name or "wardrobe" in name or "dressing" in name:
        _furnish_closet(draw, ix0, iy0, ix1, iy1, iw, ih)
    elif "stair" in name and stair_direction and font is not None:
        _furnish_staircase(draw, ix0, iy0, ix1, iy1, iw, ih, stair_direction, font)
    # entry/foyer/hallway/storage/unrecognized: no furniture symbol


def _furnish_bedroom(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float, cy: float) -> None:
    bed_w, bed_h = w * 0.42, h * 0.42
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
    pillow_h = bed_h * 0.18
    pillow_gap = bed_w * 0.06
    pillow_w = (bed_w - 3 * pillow_gap) / 2
    for i in range(2):
        px0 = bx0 + pillow_gap + i * (pillow_w + pillow_gap)
        draw.rounded_rectangle(
            [px0, by0 + bed_h * 0.06, px0 + pillow_w, by0 + bed_h * 0.06 + pillow_h],
            radius=min(pillow_w, pillow_h) * 0.3, outline=FURNITURE_COLOR, width=1,
        )
    # A folded-back blanket line near the foot of the bed.
    draw.line(
        [(bx0, by0 + bed_h * 0.7), (bx1, by0 + bed_h * 0.7)], fill=FURNITURE_COLOR, width=1
    )
    nightstand = min(w, h) * 0.12
    if bx1 + nightstand + 4 <= x1:
        draw.rectangle([bx1 + 4, by0, bx1 + 4 + nightstand, by0 + nightstand], outline=FURNITURE_COLOR, width=1)
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


def _draw_room_label(draw: ImageDraw.ImageDraw, rect: dict, plot_x0: float, plot_y0: float, scale: float, unit: str, name_font: ImageFont.FreeTypeFont, small_font: ImageFont.FreeTypeFont) -> None:
    x0, y0, x1, y1 = _room_bbox(rect, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    if box_w < 46 or box_h < 34:
        return  # too small to label legibly

    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    area_units = rect["w"] * rect["h"]
    area_text = f"{rect['w']:.1f}x{rect['h']:.1f} {unit} ({area_units:.0f} sq {unit})"
    # Real architectural floor plans conventionally render the room name in
    # all-caps within its label - a small typographic change (display only,
    # the underlying name/data is untouched) that reads as a genuine title-
    # block entry rather than a plain UI text label.
    display_name = rect["name"].upper()

    name_bbox = draw.textbbox((0, 0), display_name, font=name_font)
    name_w, name_h = name_bbox[2] - name_bbox[0], name_bbox[3] - name_bbox[1]
    show_area = box_h >= 50
    area_w = area_h = 0
    divider_gap = 6
    if show_area:
        area_bbox = draw.textbbox((0, 0), area_text, font=small_font)
        area_w, area_h = area_bbox[2] - area_bbox[0], area_bbox[3] - area_bbox[1]

    total_h = name_h + (divider_gap + area_h if show_area else 0)
    name_x, name_y = cx - name_w / 2, cy - total_h / 2
    _draw_faux_bold_text(draw, (name_x, name_y), display_name, name_font, INK)

    if show_area:
        # A thin divider rule between name and area - the one small touch
        # that separates "two lines of text" from "a real title-block entry".
        divider_y = name_y + name_h + divider_gap / 2
        divider_half_w = max(name_w, area_w) * 0.32
        draw.line(
            [(cx - divider_half_w, divider_y), (cx + divider_half_w, divider_y)], fill=INK_SOFT, width=1
        )
        draw.text((cx - area_w / 2, divider_y + divider_gap / 2), area_text, font=small_font, fill=INK_SOFT)


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


def _draw_dimensions(draw: ImageDraw.ImageDraw, rects: list[dict], length: float, width: float, plot_x0: float, plot_y0: float, plot_x1: float, plot_y1: float, scale: float, unit: str, font: ImageFont.FreeTypeFont) -> None:
    x_boundaries = sorted({round(r["x"], 3) for r in rects} | {round(r["x"] + r["w"], 3) for r in rects} | {0.0, round(length, 3)})
    y_boundaries = sorted({round(r["y"], 3) for r in rects} | {round(r["y"] + r["h"], 3) for r in rects} | {0.0, round(width, 3)})

    # Top: per-segment widths, then one overall length dimension further out.
    dim_y = plot_y0 - DIM_GUTTER_PX
    draw.line([(plot_x0, dim_y), (plot_x1, dim_y)], fill=DIM_LINE_COLOR, width=1)
    for xb in x_boundaries:
        px = plot_x0 + xb * scale
        draw.line([(px, dim_y - DIM_TICK_PX / 2), (px, dim_y + DIM_TICK_PX / 2)], fill=DIM_LINE_COLOR, width=1)
    for a, b in zip(x_boundaries, x_boundaries[1:]):
        seg = b - a
        if seg < 0.2:
            continue
        _draw_centered_text(draw, (plot_x0 + (a + b) / 2 * scale, dim_y - 15), f"{seg:.1f}", font, DIM_LINE_COLOR)

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
    for a, b in zip(y_boundaries, y_boundaries[1:]):
        seg = b - a
        if seg < 0.2:
            continue
        _draw_right_aligned_text(draw, (dim_x - 8, plot_y0 + (a + b) / 2 * scale), f"{seg:.1f}", font, DIM_LINE_COLOR)

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
