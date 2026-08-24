"""Converts one floor's room rectangles (floor_layout.layout_floor's output -
the SAME coordinate data blueprint_svg.py already draws to PNG) into a real,
genuine AutoCAD-format (.dxf) file via ezdxf.

Why this exists: see CLAUDE.md's "Build a House feature" section, "Real
DXF/AutoCAD-format export" entry. Short version - a user tried several
self-hosted image-generation AI models hoping for "AutoCAD format" output and
got meaningless pixel noise every time, because an image-diffusion model has
no concept of geometry, only pixels; a real AutoCAD file is coordinate data.
This module deliberately involves NO AI model at all - it just serializes
numbers this pipeline already computes (the exact same rectangles rendered as
a PNG blueprint) into the DXF format, so the output is both a genuine CAD
file AND 100% dimensionally accurate (same source of truth as the PNG, no
separate estimate that could disagree with it).

Pure and deterministic, like blueprint_svg.py - no I/O, nothing to mock in
tests. Geometry only (room outlines + labels + a plot boundary + a title
line) - it does not attempt to replicate blueprint_svg.py's furniture/door/
window symbols, since those are presentation heuristics for a picture, not
information a real AutoCAD user needs duplicated in a DXF room outline.
"""

import io

import ezdxf
from ezdxf.enums import TextEntityAlignment

# DXF's $INSUNITS header field - only the two units this app's dimension
# inputs ever use (see static/index.html's unit <select>). Falls back to
# "unitless" (0) for anything else rather than guessing.
_INSUNITS_BY_UNIT = {"ft": 2, "m": 6}

_ROOM_LABEL_HEIGHT_RATIO = 0.12  # room name text height, relative to the room's shorter side
_ROOM_LABEL_MIN_HEIGHT = 0.15
_ROOM_LABEL_MAX_HEIGHT = 0.4
_TITLE_TEXT_HEIGHT_RATIO = 0.03  # relative to the plot's longer side


def render_floor_blueprint_dxf(floor_number: int, rects: list[dict], dimensions: dict) -> bytes:
    """rects: output of floor_layout.layout_floor - [{"name","x","y","w","h"}, ...]
    in the same real-world unit as dimensions. Returns DXF file bytes
    (UTF-8 encoded ASCII DXF, R2010 format - broadly compatible with modern
    AutoCAD and every major CAD viewer)."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit", "") or ""

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = _INSUNITS_BY_UNIT.get(unit, 0)

    doc.layers.add("WALLS", color=7)
    doc.layers.add("ROOMS", color=8)
    doc.layers.add("TEXT", color=7)
    doc.layers.add("TITLE", color=7)

    msp = doc.modelspace()

    # Exterior plot boundary - the ground truth rectangle every room rect
    # tiles exactly (layout_floor() always fills [0,length] x [0,width]).
    msp.add_lwpolyline(
        [(0, 0), (length, 0), (length, width), (0, width)],
        close=True,
        dxfattribs={"layer": "WALLS", "lineweight": 50},
    )

    title_height = max(_TITLE_TEXT_HEIGHT_RATIO * max(length, width), 0.2)
    title = msp.add_text(
        f"FLOOR {floor_number} - {length:g}x{width:g}{unit}",
        dxfattribs={"layer": "TITLE", "height": title_height},
    )
    title.set_placement((0, width + title_height * 1.5), align=TextEntityAlignment.BOTTOM_LEFT)

    for rect in rects:
        _draw_room(msp, rect, unit)

    buffer = io.StringIO()
    doc.write(buffer)
    return buffer.getvalue().encode("utf-8")


def _draw_room(msp, rect: dict, unit: str) -> None:
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    msp.add_lwpolyline(
        [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
        close=True,
        dxfattribs={"layer": "ROOMS", "lineweight": 25},
    )

    label_height = max(_ROOM_LABEL_MIN_HEIGHT, min(_ROOM_LABEL_MAX_HEIGHT, _ROOM_LABEL_HEIGHT_RATIO * min(w, h)))
    cx, cy = x + w / 2, y + h / 2

    name_text = msp.add_text(rect["name"], dxfattribs={"layer": "TEXT", "height": label_height})
    name_text.set_placement((cx, cy + label_height * 0.6), align=TextEntityAlignment.MIDDLE_CENTER)

    dims_text = msp.add_text(
        f"{w:.1f}x{h:.1f}{unit}",
        dxfattribs={"layer": "TEXT", "height": label_height * 0.75},
    )
    dims_text.set_placement((cx, cy - label_height * 0.6), align=TextEntityAlignment.MIDDLE_CENTER)
