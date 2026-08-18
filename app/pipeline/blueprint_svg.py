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
"""

import math
from io import BytesIO
from itertools import combinations

from PIL import Image, ImageDraw, ImageFont

# ---- Canvas layout ----
TARGET_PLOT_LONGEST_SIDE_PX = 780
MARGIN_LEFT_PX = 150   # room for the left dimension line + labels
MARGIN_TOP_PX = 130    # room for the title text + top dimension line
MARGIN_RIGHT_PX = 60
MARGIN_BOTTOM_PX = 170  # room for the scale bar + title block

DIM_GUTTER_PX = 44      # gap between the plot edge and its nearest dimension line
DIM_OVERALL_OFFSET_PX = 30  # extra gap out to the "overall" dimension line
DIM_TICK_PX = 8

WALL_EXTERIOR_GAP_PX = 9   # gap between the two parallel exterior wall lines
WALL_INTERIOR_GAP_PX = 4   # gap between the two parallel interior wall lines

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
    total floor count - a staircase symbol is drawn (in the largest room,
    a heuristic placement like doors/windows above) only when > 1, since a
    single-storey building has nothing to connect. Returns PNG bytes."""
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

    for rect in rects:
        _draw_room(draw, _room_bbox(rect, plot_x0, plot_y0, scale))

    for rect in rects:
        _draw_furniture(draw, rect, plot_x0, plot_y0, scale)

    if total_floors > 1:
        direction = "UP" if floor_number < total_floors else "DN"
        _draw_staircase(draw, rects, plot_x0, plot_y0, scale, small_font, direction)

    door_len_px = max(10.0, min(26.0, 2.6 * scale))
    for a, b in combinations(rects, 2):
        edge = _shared_edge(a, b)
        if edge is not None:
            _draw_door(draw, edge, plot_x0, plot_y0, scale, door_len_px)

    _draw_exterior_wall(draw, plot_x0, plot_y0, plot_x1, plot_y1)

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


def _draw_room(draw: ImageDraw.ImageDraw, bbox: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = bbox
    draw.rectangle([x0, y0, x1, y1], fill=ROOM_FILL)
    draw.rectangle([x0, y0, x1, y1], outline=WALL_COLOR, width=1)
    inset = WALL_INTERIOR_GAP_PX
    if x1 - x0 > 2 * inset + 4 and y1 - y0 > 2 * inset + 4:
        draw.rectangle([x0 + inset, y0 + inset, x1 - inset, y1 - inset], outline=WALL_COLOR, width=1)


def _draw_exterior_wall(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    """Double-line exterior wall: the plot boundary itself is the inner face,
    plus a parallel line offset outward - a genuine architectural double-line
    wall, not just a thick single stroke."""
    inset = WALL_EXTERIOR_GAP_PX
    draw.rectangle([x0, y0, x1, y1], outline=WALL_COLOR, width=2)
    draw.rectangle([x0 - inset, y0 - inset, x1 + inset, y1 + inset], outline=WALL_COLOR, width=2)


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


def _draw_door(draw: ImageDraw.ImageDraw, edge: dict, plot_x0: float, plot_y0: float, scale: float, nominal_len_px: float) -> None:
    """Erases a gap in the shared wall and draws a standard door symbol (a
    leaf line + a quarter-circle swing arc) - heuristically placed at the
    segment's midpoint. Not construction-grade, just a legible "there is a
    door here" mark, per the user's own "okayish accuracy" bar."""
    seg_len_px = (edge["end"] - edge["start"]) * scale
    leaf_len_px = min(nominal_len_px, seg_len_px * 0.7)
    if leaf_len_px < 6:
        return

    mid_units = (edge["start"] + edge["end"]) / 2
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
    erase_width = 2 * WALL_INTERIOR_GAP_PX + 6
    draw.line([hinge, gap_end], fill=ROOM_FILL, width=erase_width)
    draw.line([hinge, open_end], fill=WALL_COLOR, width=1)
    draw.arc(
        [hx - leaf_len_px, hy - leaf_len_px, hx + leaf_len_px, hy + leaf_len_px],
        start=0, end=90, fill=WALL_COLOR, width=1,
    )


# ---- Windows ----


def _draw_windows(draw: ImageDraw.ImageDraw, rect: dict, plot_length: float, plot_width: float, plot_x0: float, plot_y0: float, scale: float) -> None:
    """Marks a window on each of the room's edges that lie on the exterior
    plot boundary - a short colored line cut into the exterior double wall,
    centered on that edge."""
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
        erase_width = WALL_EXTERIOR_GAP_PX + 6

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
# purely circulatory room name (entry/foyer/hallway/storage/utility) simply
# gets no furniture rather than a guessed icon - restraint over decoration.

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


def _draw_furniture(draw: ImageDraw.ImageDraw, rect: dict, plot_x0: float, plot_y0: float, scale: float) -> None:
    x0, y0, x1, y1 = _room_bbox(rect, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    if box_w < _FURNITURE_MIN_BOX_W or box_h < _FURNITURE_MIN_BOX_H:
        return

    cy = (y0 + y1) / 2
    pad = max(4.0, min(box_w, box_h) * 0.07)
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
    # entry/foyer/hallway/storage/utility/unrecognized: no furniture symbol


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
    draw.line([(bx0, by0 + bed_h * 0.22), (bx1, by0 + bed_h * 0.22)], fill=FURNITURE_COLOR, width=1)  # pillow line
    nightstand = min(w, h) * 0.12
    if bx1 + nightstand + 4 <= x1:
        draw.rectangle([bx1 + 4, by0, bx1 + 4 + nightstand, by0 + nightstand], outline=FURNITURE_COLOR, width=1)
    wardrobe_w = w * 0.32
    if y0 + h * 0.14 < cy - _LABEL_CLEARANCE_PX:  # skip if it would reach into the label's upper band
        draw.rectangle([x1 - wardrobe_w, y0, x1, y0 + h * 0.14], outline=FURNITURE_COLOR, width=1)


def _furnish_living(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> None:
    depth = min(w, h) * 0.18
    draw.rectangle([x0, y0, x0 + w * 0.55, y0 + depth], outline=FURNITURE_COLOR, width=1)  # sofa back run
    draw.rectangle([x0, y0, x0 + depth, y0 + h * 0.5], outline=FURNITURE_COLOR, width=1)  # sofa side arm
    table = min(w, h) * 0.14
    tx, ty = x0 + w * 0.35, y0 + h * 0.62
    draw.rectangle([tx, ty, tx + table, ty + table], outline=FURNITURE_COLOR, width=1)
    chair = min(w, h) * 0.12
    draw.rectangle([x1 - chair, y1 - chair, x1, y1], outline=FURNITURE_COLOR, width=1)


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
    fridge = depth * 1.4
    draw.rectangle([x0, y1 - fridge, x0 + fridge * 0.7, y1], outline=FURNITURE_COLOR, width=1)


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


# ---- Staircase ----


def _draw_staircase(
    draw: ImageDraw.ImageDraw,
    rects: list[dict],
    plot_x0: float,
    plot_y0: float,
    scale: float,
    font: ImageFont.FreeTypeFont,
    direction: str,
) -> None:
    """Heuristically placed in the largest room's corner - same "legible mark,
    not a construction-grade layout" precedent as doors/windows above, since
    there's no reserved circulation space to place it in exactly."""
    if not rects:
        return
    largest = max(rects, key=lambda r: r["w"] * r["h"])
    x0, y0, x1, y1 = _room_bbox(largest, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    if box_w < 90 or box_h < 90:
        return

    stair_w = min(box_w * 0.28, 70.0)
    stair_h = min(box_h * 0.5, 140.0)
    sx0, sy0 = x1 - stair_w - 8, y1 - stair_h - 8
    sx1, sy1 = sx0 + stair_w, sy0 + stair_h

    draw.rectangle([sx0, sy0, sx1, sy1], outline=WALL_COLOR, width=1)
    steps = max(4, int(stair_h // 14))
    for i in range(1, steps):
        y = sy0 + stair_h * i / steps
        draw.line([(sx0, y), (sx1, y)], fill=WALL_COLOR, width=1)

    ax = (sx0 + sx1) / 2
    if direction == "UP":
        draw.line([(ax, sy1 - 8), (ax, sy0 + 10)], fill=WALL_COLOR, width=2)
        draw.polygon([(ax, sy0 + 4), (ax - 5, sy0 + 12), (ax + 5, sy0 + 12)], fill=WALL_COLOR)
    else:
        draw.line([(ax, sy0 + 8), (ax, sy1 - 10)], fill=WALL_COLOR, width=2)
        draw.polygon([(ax, sy1 - 4), (ax - 5, sy1 - 12), (ax + 5, sy1 - 12)], fill=WALL_COLOR)
    _draw_centered_text(draw, (ax, sy0 - 10), direction, font, WALL_COLOR)


# ---- Labels ----


def _draw_room_label(draw: ImageDraw.ImageDraw, rect: dict, plot_x0: float, plot_y0: float, scale: float, unit: str, name_font: ImageFont.FreeTypeFont, small_font: ImageFont.FreeTypeFont) -> None:
    x0, y0, x1, y1 = _room_bbox(rect, plot_x0, plot_y0, scale)
    box_w, box_h = x1 - x0, y1 - y0
    if box_w < 46 or box_h < 34:
        return  # too small to label legibly

    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    area_units = rect["w"] * rect["h"]
    area_text = f"{rect['w']:.1f}x{rect['h']:.1f} {unit} ({area_units:.0f} sq {unit})"

    name_bbox = draw.textbbox((0, 0), rect["name"], font=name_font)
    name_w, name_h = name_bbox[2] - name_bbox[0], name_bbox[3] - name_bbox[1]
    show_area = box_h >= 50
    area_h = 0
    if show_area:
        area_bbox = draw.textbbox((0, 0), area_text, font=small_font)
        area_w, area_h = area_bbox[2] - area_bbox[0], area_bbox[3] - area_bbox[1]

    name_x, name_y = cx - name_w / 2, cy - (name_h + (area_h + 4 if show_area else 0)) / 2
    _draw_faux_bold_text(draw, (name_x, name_y), rect["name"], name_font, INK)

    if show_area:
        draw.text((cx - area_w / 2, name_y + name_h + 4), area_text, font=small_font, fill=INK_SOFT)


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
