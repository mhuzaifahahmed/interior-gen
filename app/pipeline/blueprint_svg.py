"""Draws one floor's room layout (from floor_layout.layout_floor) as a labeled
top-down PNG blueprint. Named blueprint_svg for the concept it represents, but
deliberately implemented with Pillow (PIL.ImageDraw), not real SVG - see
CLAUDE.md's "Build a House feature" section for why: Pillow is already a
dependency, draws straight to PNG (what both the frontend <img> and the
gpt-image-1 edit call need), and avoids a new native-library dependency
(cairosvg/Cairo) that's a real install risk on Windows, for a browser-crispness
benefit this v1 (no client-side interactivity) doesn't need.
"""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

TARGET_LONGEST_SIDE_PX = 700
MARGIN_PX = 50
TITLE_HEIGHT_PX = 50
ROOM_FILL = (235, 242, 250)
ROOM_OUTLINE = (60, 90, 130)
PLOT_OUTLINE = (20, 20, 20)
TEXT_COLOR = (20, 20, 20)


def render_floor_blueprint(floor_number: int, rects: list[dict], dimensions: dict) -> bytes:
    """rects: output of floor_layout.layout_floor - [{"name","x","y","w","h"}, ...]
    in the same real-world unit as dimensions. Returns PNG bytes."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit", "")
    scale = TARGET_LONGEST_SIDE_PX / max(length, width, 1)

    img_w = int(length * scale) + MARGIN_PX * 2
    img_h = int(width * scale) + MARGIN_PX * 2 + TITLE_HEIGHT_PX

    image = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    draw.text((MARGIN_PX, 14), f"Floor {floor_number} - computed layout", fill=TEXT_COLOR, font=font)

    plot_x0, plot_y0 = MARGIN_PX, MARGIN_PX + TITLE_HEIGHT_PX
    plot_x1, plot_y1 = plot_x0 + length * scale, plot_y0 + width * scale
    draw.rectangle([plot_x0, plot_y0, plot_x1, plot_y1], outline=PLOT_OUTLINE, width=3)

    for rect in rects:
        rx0 = plot_x0 + rect["x"] * scale
        ry0 = plot_y0 + rect["y"] * scale
        rx1 = rx0 + rect["w"] * scale
        ry1 = ry0 + rect["h"] * scale
        draw.rectangle([rx0, ry0, rx1, ry1], fill=ROOM_FILL, outline=ROOM_OUTLINE, width=2)

        label = f"{rect['name']}\n{rect['w']:.1f}x{rect['h']:.1f}{unit}"
        draw.multiline_text(
            (rx0 + 6, ry0 + 6), label, fill=TEXT_COLOR, font=font, spacing=2, align="left"
        )

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
