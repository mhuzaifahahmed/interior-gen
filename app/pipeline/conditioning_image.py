"""Pure, deterministic ControlNet conditioning-image renderer for the
friend-hosted Kaggle SDXL+ControlNet floor-plan model
(future-plans/concept-layout-controlnet-conditioning.md, Phase 1).

The model's real flaw isn't its aesthetic - it's that its own
create_plot_boundary() only draws the plot's OUTER rectangle as the
ControlNet conditioning image, so ControlNet only constrains the outer edge
and the model hallucinates every interior wall at random, ignoring the
user's actual room program entirely.

This repo already computes the exact, correct room rectangles
(app/pipeline/floor_layout.py's layout_floor()). render_conditioning_edge_map()
turns those into a clean white-lines-on-black edge map - exactly what a Canny
ControlNet expects - so the notebook can trace OUR real geometry instead of
inventing its own.

Kept pure (no I/O, nothing to mock) and deliberately self-consistent rather
than mirroring the notebook's own create_plot_boundary() placement math pixel
-for-pixel: the notebook decodes and uses whatever image we send verbatim (its
own boundary-drawing function only remains as ITS fallback for callers that
send no conditioning image at all), so the only real constraint is that THIS
renderer and this repo's own label-compositor (kaggle_autocad.py's Phase 2)
agree on the same placement math - which they do, since both import
plot_to_canvas_box() from here.

FURNITURE HINTS (2026-09-11): real, live-observed problem - even with real
wall geometry driving ControlNet, the model has no way to know which room box
is which room (the edge map before this change was pure wall lines, no
per-room signal at all), so it placed furniture by guessing from shape/
position alone. A live generation showed this guess land wrong (a dining
table drawn inside the Living Room's footprint, Living Room itself left
empty) even though bedrooms/bathroom/kitchen happened to guess correctly that
run - not a reliable result, and not something any further notebook prompt
tuning can fix (the notebook only ever sees pixels, never room identity).

Since we already know each rect's real room type (classify_room_category(),
the same classifier floor_layout.py's own zoning and blueprint_svg.py's
furniture dispatch already use), _draw_furniture_hint() draws a simple
outline shape for that room's category directly INTO this same white-lines-
on-black edge map, positioned inside that room's own rectangle. ControlNet
then treats these hints exactly like wall edges and forces the model to trace
them - so furniture position becomes GUARANTEED correct (same room type
disambiguation blueprint_svg.py already relies on), while the model still
supplies the actual rendering/stylization (line weight, shading, drafting
aesthetic) on top of the traced shape. Deliberately simple primitives
(rectangles/circles/lines, not detailed icons) - Canny only needs a
traceable edge, not a realistic drawing; over-detailing here would just add
more spurious edges for the model to (mis)interpret. Categories with no
established furniture convention (foyer, study, closet, "default") are left
with no hint at all, matching blueprint_svg.py's own restraint for those
room types.
"""

from io import BytesIO

from PIL import Image, ImageDraw

from app.pipeline.blueprint_svg import _front_door_opening
from app.pipeline.room_specs import classify_room_category

CANVAS_SIZE = 1024
# Margin reserved on each side so the plot never touches the canvas edge -
# mirrors the notebook's own create_plot_boundary() margin in spirit (not
# pixel-for-pixel, see module docstring), giving both images a similar visual
# scale/framing.
BOX_MARGIN_PX = 160
LINE_WIDTH_PX = 3
FURNITURE_LINE_WIDTH_PX = 2
_FURNITURE_MARGIN_FRACTION = 0.12
# Below this, a room's own canvas footprint is too small for a legible hint -
# same "don't draw a doomed symbol" restraint as blueprint_svg.py's own
# _FURNITURE_MIN_BOX_W/_H gate, just in canvas-pixel terms instead of the
# print-resolution pixels that module uses.
_MIN_ROOM_PX_FOR_FURNITURE = 40


def _draw_bed(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    margin = min(w, h) * _FURNITURE_MARGIN_FRACTION
    bx0, by0 = x0 + margin, y0 + margin
    bx1, by1 = x0 + w * 0.62, y0 + h * 0.72
    draw.rectangle([bx0, by0, bx1, by1], outline=255, width=FURNITURE_LINE_WIDTH_PX)
    pillow_y = by0 + (by1 - by0) * 0.18
    draw.line(
        [bx0 + (bx1 - bx0) * 0.12, pillow_y, bx1 - (bx1 - bx0) * 0.12, pillow_y],
        fill=255,
        width=FURNITURE_LINE_WIDTH_PX,
    )
    wardrobe_w, wardrobe_h = w * 0.2, h * 0.14
    draw.rectangle(
        [x1 - margin - wardrobe_w, y1 - margin - wardrobe_h, x1 - margin, y1 - margin],
        outline=255,
        width=FURNITURE_LINE_WIDTH_PX,
    )


def _draw_living(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    margin = min(w, h) * _FURNITURE_MARGIN_FRACTION
    draw.rectangle(
        [x0 + margin, y0 + margin, x0 + w * 0.55, y0 + h * 0.22], outline=255, width=FURNITURE_LINE_WIDTH_PX
    )
    draw.rectangle(
        [x0 + margin, y0 + margin, x0 + w * 0.22, y0 + h * 0.55], outline=255, width=FURNITURE_LINE_WIDTH_PX
    )
    cx, cy = x0 + w * 0.5, y0 + h * 0.55
    tw, th = w * 0.16, h * 0.12
    draw.rectangle([cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2], outline=255, width=FURNITURE_LINE_WIDTH_PX)


def _draw_dining(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    cx, cy = x0 + w / 2, y0 + h / 2
    tw, th = w * 0.4, h * 0.28
    draw.rectangle([cx - tw / 2, cy - th / 2, cx + tw / 2, cy + th / 2], outline=255, width=FURNITURE_LINE_WIDTH_PX)
    chair = min(w, h) * 0.08
    for ox, oy in ((-tw / 2 - chair, 0.0), (tw / 2 + chair, 0.0), (0.0, -th / 2 - chair), (0.0, th / 2 + chair)):
        chx, chy = cx + ox, cy + oy
        draw.rectangle(
            [chx - chair / 2, chy - chair / 2, chx + chair / 2, chy + chair / 2],
            outline=255,
            width=FURNITURE_LINE_WIDTH_PX,
        )


def _draw_kitchen(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    margin = min(w, h) * _FURNITURE_MARGIN_FRACTION
    counter_h = h * 0.18
    draw.rectangle(
        [x0 + margin, y0 + margin, x1 - margin, y0 + margin + counter_h], outline=255, width=FURNITURE_LINE_WIDTH_PX
    )
    burner_r = min(w, h) * 0.04
    cy = y0 + margin + counter_h / 2
    span = (x1 - margin) - (x0 + margin)
    for i in range(3):
        cx = x0 + margin + span * (i + 1) / 4
        draw.ellipse([cx - burner_r, cy - burner_r, cx + burner_r, cy + burner_r], outline=255, width=FURNITURE_LINE_WIDTH_PX)


def _draw_bathroom(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    margin = min(w, h) * _FURNITURE_MARGIN_FRACTION
    draw.rectangle(
        [x0 + margin, y0 + margin, x0 + margin + w * 0.35, y0 + margin + h * 0.25],
        outline=255,
        width=FURNITURE_LINE_WIDTH_PX,
    )
    toilet = min(w, h) * 0.16
    draw.ellipse(
        [x1 - margin - toilet, y1 - margin - toilet * 1.3, x1 - margin, y1 - margin],
        outline=255,
        width=FURNITURE_LINE_WIDTH_PX,
    )
    sink = min(w, h) * 0.14
    draw.ellipse([x0 + margin, y1 - margin - sink, x0 + margin + sink, y1 - margin], outline=255, width=FURNITURE_LINE_WIDTH_PX)


def _draw_garage(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    margin = min(x1 - x0, y1 - y0) * 0.1
    draw.rectangle([x0 + margin, y0 + margin, x1 - margin, y1 - margin], outline=255, width=FURNITURE_LINE_WIDTH_PX)


def _draw_laundry(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    margin = min(w, h) * _FURNITURE_MARGIN_FRACTION
    size = min(w, h) * 0.3
    draw.rectangle([x0 + margin, y0 + margin, x0 + margin + size, y0 + margin + size], outline=255, width=FURNITURE_LINE_WIDTH_PX)
    draw.ellipse(
        [x0 + margin + size * 0.2, y0 + margin + size * 0.2, x0 + margin + size * 0.8, y0 + margin + size * 0.8],
        outline=255,
        width=FURNITURE_LINE_WIDTH_PX,
    )
    draw.rectangle(
        [x0 + margin + size * 1.2, y0 + margin, x0 + margin + size * 2.2, y0 + margin + size],
        outline=255,
        width=FURNITURE_LINE_WIDTH_PX,
    )


def _draw_staircase(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float) -> None:
    w, h = x1 - x0, y1 - y0
    steps = 6
    for i in range(1, steps):
        y = y0 + h * i / steps
        draw.line([x0 + w * 0.1, y, x1 - w * 0.1, y], fill=255, width=FURNITURE_LINE_WIDTH_PX)


_FURNITURE_DRAWERS = {
    "bedroom": _draw_bed,
    "living": _draw_living,
    "dining": _draw_dining,
    "kitchen": _draw_kitchen,
    "bathroom": _draw_bathroom,
    "garage": _draw_garage,
    "laundry": _draw_laundry,
    "staircase": _draw_staircase,
}


def _draw_furniture_hint(draw: ImageDraw.ImageDraw, rx0: float, ry0: float, rx1: float, ry1: float, category: str) -> None:
    if min(rx1 - rx0, ry1 - ry0) < _MIN_ROOM_PX_FOR_FURNITURE:
        return
    drawer = _FURNITURE_DRAWERS.get(category)
    if drawer:
        drawer(draw, rx0, ry0, rx1, ry1)


def plot_to_canvas_box(
    length: float, width: float, canvas_size: int = CANVAS_SIZE, margin: int = BOX_MARGIN_PX
) -> tuple[float, float, float, float]:
    """Fit a real length x width plot into a centered box on a square canvas,
    preserving aspect ratio. Returns (x0, y0, box_w, box_h) in canvas pixels.

    Uses layout_floor()'s own convention directly (rect "x" spans [0, length],
    rect "y" spans [0, width]) so box_w scales with length and box_h scales
    with width at one uniform pixel-per-unit factor - required so a room
    rectangle's aspect ratio in real units is preserved on canvas.
    """
    length = max(float(length), 1.0)
    width = max(float(width), 1.0)
    max_box = canvas_size - margin

    if length >= width:
        box_w = float(max_box)
        box_h = max_box * (width / length)
    else:
        box_h = float(max_box)
        box_w = max_box * (length / width)

    x0 = (canvas_size - box_w) / 2
    y0 = (canvas_size - box_h) / 2
    return x0, y0, box_w, box_h


def _draw_plot_boundary(
    draw: ImageDraw.ImageDraw,
    x0: float, y0: float, x1: float, y1: float,
    length: float, width: float,
    scale_x: float, scale_y: float,
    front_door_edge: dict | None,
) -> None:
    """Draws the plot's outer boundary as 4 separate line segments instead
    of one closed rectangle, so a gap can be cut for the front entrance
    (2026-09-19) when `facing` was given - reuses blueprint_svg.
    _front_door_opening() directly, so this edge map, the PNG blueprint,
    and the real DXF export can never disagree about where the entrance
    is. Without this gap the AI Concept Layout model would trace a fully
    closed boundary and never see the entrance at all, regardless of what
    the deterministic renderers show."""

    def gap_on(orientation: str, pos: float) -> tuple[float, float] | None:
        if front_door_edge is None:
            return None
        if front_door_edge["orientation"] != orientation or abs(front_door_edge["pos"] - pos) > 1e-4:
            return None
        return front_door_edge["start"], front_door_edge["end"]

    top_gap = gap_on("horizontal", 0.0)
    if top_gap is None:
        draw.line([(x0, y0), (x1, y0)], fill=255, width=LINE_WIDTH_PX)
    else:
        gx0, gx1 = x0 + top_gap[0] * scale_x, x0 + top_gap[1] * scale_x
        draw.line([(x0, y0), (gx0, y0)], fill=255, width=LINE_WIDTH_PX)
        draw.line([(gx1, y0), (x1, y0)], fill=255, width=LINE_WIDTH_PX)

    bottom_gap = gap_on("horizontal", width)
    if bottom_gap is None:
        draw.line([(x0, y1), (x1, y1)], fill=255, width=LINE_WIDTH_PX)
    else:
        gx0, gx1 = x0 + bottom_gap[0] * scale_x, x0 + bottom_gap[1] * scale_x
        draw.line([(x0, y1), (gx0, y1)], fill=255, width=LINE_WIDTH_PX)
        draw.line([(gx1, y1), (x1, y1)], fill=255, width=LINE_WIDTH_PX)

    left_gap = gap_on("vertical", 0.0)
    if left_gap is None:
        draw.line([(x0, y0), (x0, y1)], fill=255, width=LINE_WIDTH_PX)
    else:
        gy0, gy1 = y0 + left_gap[0] * scale_y, y0 + left_gap[1] * scale_y
        draw.line([(x0, y0), (x0, gy0)], fill=255, width=LINE_WIDTH_PX)
        draw.line([(x0, gy1), (x0, y1)], fill=255, width=LINE_WIDTH_PX)

    right_gap = gap_on("vertical", length)
    if right_gap is None:
        draw.line([(x1, y0), (x1, y1)], fill=255, width=LINE_WIDTH_PX)
    else:
        gy0, gy1 = y0 + right_gap[0] * scale_y, y0 + right_gap[1] * scale_y
        draw.line([(x1, y0), (x1, gy0)], fill=255, width=LINE_WIDTH_PX)
        draw.line([(x1, gy1), (x1, y1)], fill=255, width=LINE_WIDTH_PX)


def render_conditioning_edge_map(
    rects: list[dict], dimensions: dict, canvas_size: int = CANVAS_SIZE, facing: str | None = None
) -> bytes:
    """rects: layout_floor() output ([{"name","x","y","w","h"}, ...] in real
    length/width units). dimensions: {"length","width","unit"}. facing
    (2026-09-19): the same value passed to layout_floor() for this
    room_layout - when given, cuts a real gap for the front entrance in the
    boundary edge map (see _draw_plot_boundary()). None/unrecognized draws
    a fully closed boundary, same as before this param existed.

    Returns PNG bytes: a black canvas with the plot boundary + every room's
    wall outline drawn as thin white lines, plus a simple per-room-type
    furniture outline hint (see _draw_furniture_hint()/module docstring's
    "FURNITURE HINTS" section) - still a clean Canny-style edge map, no
    poché, no text (Phase 2 composites real labels onto the model's own
    returned image afterward instead).
    """
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)

    canvas = Image.new("L", (canvas_size, canvas_size), 0)
    draw = ImageDraw.Draw(canvas)

    x0, y0, box_w, box_h = plot_to_canvas_box(length, width, canvas_size)
    scale_x = box_w / length if length else 0.0
    scale_y = box_h / width if width else 0.0

    def to_canvas(px: float, py: float) -> tuple[float, float]:
        return (x0 + px * scale_x, y0 + py * scale_y)

    x1, y1 = to_canvas(length, width)
    front_door_edge = _front_door_opening(rects, length, width, facing)
    _draw_plot_boundary(draw, x0, y0, x1, y1, length, width, scale_x, scale_y, front_door_edge)

    for rect in rects:
        rx0, ry0 = to_canvas(rect["x"], rect["y"])
        rx1, ry1 = to_canvas(rect["x"] + rect["w"], rect["y"] + rect["h"])
        draw.rectangle([rx0, ry0, rx1, ry1], outline=255, width=LINE_WIDTH_PX)
        category = classify_room_category(rect.get("name") or "")
        _draw_furniture_hint(draw, rx0, ry0, rx1, ry1, category)

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()
