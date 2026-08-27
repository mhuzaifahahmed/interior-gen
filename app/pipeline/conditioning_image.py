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
"""

from io import BytesIO

from PIL import Image, ImageDraw

CANVAS_SIZE = 1024
# Margin reserved on each side so the plot never touches the canvas edge -
# mirrors the notebook's own create_plot_boundary() margin in spirit (not
# pixel-for-pixel, see module docstring), giving both images a similar visual
# scale/framing.
BOX_MARGIN_PX = 160
LINE_WIDTH_PX = 3


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


def render_conditioning_edge_map(
    rects: list[dict], dimensions: dict, canvas_size: int = CANVAS_SIZE
) -> bytes:
    """rects: layout_floor() output ([{"name","x","y","w","h"}, ...] in real
    length/width units). dimensions: {"length","width","unit"}.

    Returns PNG bytes: a black canvas with the plot boundary + every room's
    wall outline drawn as thin white lines - a clean Canny-style edge map.
    Wall centerlines only - no poché, no furniture, no text (Phase 2 composites
    real labels onto the model's own returned image afterward instead).
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
    draw.rectangle([x0, y0, x1, y1], outline=255, width=LINE_WIDTH_PX)

    for rect in rects:
        rx0, ry0 = to_canvas(rect["x"], rect["y"])
        rx1, ry1 = to_canvas(rect["x"] + rect["w"], rect["y"] + rect["h"])
        draw.rectangle([rx0, ry0, rx1, ry1], outline=255, width=LINE_WIDTH_PX)

    buffer = BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()
