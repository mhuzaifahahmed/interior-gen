"""Converts one floor's room rectangles (floor_layout.layout_floor's output -
the SAME coordinate data blueprint_svg.py already draws to PNG) into a real,
genuine AutoCAD-format (.dxf) file via ezdxf.

Why this exists: see CLAUDE.md's "Build a House feature" section, "Real
DXF/AutoCAD-format export" entry. Short version - a user tried several
self-hosted image-generation AI models hoping for "AutoCAD format" output and
got meaningless pixel noise every time, because an image-diffusion model has
no concept of geometry, only pixels; a real AutoCAD file is coordinate data.
This module deliberately involves NO AI model at all - it just serializes
numbers this pipeline already computes into the DXF format, so the output is
both a genuine CAD file AND 100% dimensionally accurate (same source of
truth as the PNG, no separate estimate that could disagree with it).

v2 (2026-09-02, real structured export): v1 only drew bare room rectangles +
labels - real user feedback: "its just making the boxes to the side and
telling us hey your autocad is done see there is no structure or
intelligence involved." Fair - a real DXF should carry actual wall/door/
window geometry, not just an outline. The geometry itself already existed
(blueprint_svg.py computes wall thickness, door positions, and window
positions for the PNG) - this version SERIALIZES that same geometry into
real DXF entities instead of throwing it away, reusing blueprint_svg.py's own
`_shared_edge()`/`_should_suppress_direct_door()` so the PNG and the DXF
never disagree about which rooms connect directly vs. via the hallway.

Walls are real-thickness LWPOLYLINEs (ezdxf's `const_width` - a genuine "wide
polyline" that opens with real thickness in any CAD viewer), NOT filled
poché (unlike blueprint_svg.py's PNG rendering) - CAD users want editable
LINE geometry they can select/modify per wall, not a filled black region.
Doors are cut as real gaps in the wall polylines (not just a symbol drawn on
top of a continuous wall) with a leaf line + swing arc; windows are real gaps
on exterior walls with a glazing line. Furniture is still deliberately NOT
included (a presentation heuristic for the PNG, not information a real DXF
consumer needs duplicated - unchanged from v1's stated scope).

Pure and deterministic, like blueprint_svg.py - no I/O, nothing to mock in
tests.
"""

import io
from itertools import combinations

import ezdxf
from ezdxf.enums import TextEntityAlignment

from app.pipeline.blueprint_svg import _shared_edge, _should_suppress_direct_door
from app.pipeline.room_specs import to_plot_unit

# DXF's $INSUNITS header field - only the two units this app's dimension
# inputs ever use (see static/index.html's unit <select>). Falls back to
# "unitless" (0) for anything else rather than guessing.
_INSUNITS_BY_UNIT = {"ft": 2, "m": 6}

_ROOM_LABEL_HEIGHT_RATIO = 0.12  # room name text height, relative to the room's shorter side
_ROOM_LABEL_MIN_HEIGHT = 0.15
_ROOM_LABEL_MAX_HEIGHT = 0.4
_TITLE_TEXT_HEIGHT_RATIO = 0.03  # relative to the plot's longer side

# Real-world wall/opening sizes (meters), converted to the plot's own unit
# via to_plot_unit() - same convention as room_specs.py's minimum room
# dimensions, so this file never needs to know which unit a given house
# project happens to use.
WALL_THICKNESS_M = 0.15  # a standard residential interior wall (~6in)
DOOR_WIDTH_M = 0.9  # a standard interior door leaf (~3ft)
WINDOW_WIDTH_M = 1.2  # a reasonable default window width (~4ft)

_EDGE_TOL = 1e-4


def render_floor_blueprint_dxf(floor_number: int, rects: list[dict], dimensions: dict) -> bytes:
    """rects: output of floor_layout.layout_floor - [{"name","x","y","w","h"}, ...]
    in the same real-world unit as dimensions. Returns DXF file bytes
    (UTF-8 encoded ASCII DXF, R2010 format - broadly compatible with modern
    AutoCAD and every major CAD viewer)."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit", "") or ""

    wall_thickness = to_plot_unit(WALL_THICKNESS_M, unit)
    door_width = to_plot_unit(DOOR_WIDTH_M, unit)
    window_width = to_plot_unit(WINDOW_WIDTH_M, unit)

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = _INSUNITS_BY_UNIT.get(unit, 0)

    doc.layers.add("WALLS", color=7)
    doc.layers.add("DOORS", color=3)
    doc.layers.add("WINDOWS", color=5)
    doc.layers.add("ROOMS", color=8)
    doc.layers.add("TEXT", color=7)
    doc.layers.add("TITLE", color=7)

    msp = doc.modelspace()

    title_height = max(_TITLE_TEXT_HEIGHT_RATIO * max(length, width), 0.2)
    title = msp.add_text(
        f"FLOOR {floor_number} - {length:g}x{width:g}{unit}",
        dxfattribs={"layer": "TITLE", "height": title_height},
    )
    title.set_placement((0, width + title_height * 1.5), align=TextEntityAlignment.BOTTOM_LEFT)

    # Exterior walls (with real window gaps) - one pass per room, only for
    # edges that actually lie on the plot boundary, so no segment is ever
    # drawn twice.
    for rect in rects:
        _draw_exterior_walls_for_room(msp, rect, length, width, wall_thickness, window_width)

    # Interior walls (with real door gaps where a door isn't suppressed) -
    # one pass per unique adjacent room PAIR (combinations never repeats a
    # pair), so a shared wall is drawn exactly once, not once per room.
    has_hallway = any(r["name"] == "Hallway" for r in rects)
    for a, b in combinations(rects, 2):
        edge = _shared_edge(a, b)
        if edge is None:
            continue
        suppress_door = has_hallway and _should_suppress_direct_door(a, b)
        _draw_interior_wall(msp, edge, wall_thickness, None if suppress_door else door_width)

    for rect in rects:
        _draw_room_text(msp, rect, unit)

    buffer = io.StringIO()
    doc.write(buffer)
    return buffer.getvalue().encode("utf-8")


def _draw_wall_segment(msp, orientation: str, pos: float, start: float, end: float, thickness: float) -> None:
    """Draws one real-thickness wall segment as an ezdxf `const_width`
    LWPOLYLINE (a genuine "wide polyline" - opens with real thickness in any
    CAD viewer, not just a thin line pretending to be a wall)."""
    if end - start <= 1e-6:
        return
    points = [(pos, start), (pos, end)] if orientation == "vertical" else [(start, pos), (end, pos)]
    msp.add_lwpolyline(points, dxfattribs={"layer": "WALLS", "const_width": thickness})


def _draw_gapped_wall(
    msp, orientation: str, pos: float, start: float, end: float, thickness: float, gap_width: float | None
) -> tuple[float, float] | None:
    """Draws a wall segment with an optional real gap (door or window)
    centered on its midpoint - same "heuristic midpoint placement" precedent
    already established by blueprint_svg.py's _draw_door()/_draw_windows().
    Returns the (gap_start, gap_end) actually used, or None if no gap was
    cut (gap_width was None, or the segment was too short for one) - the
    caller uses this to decide whether/where to draw a door or window
    symbol."""
    if gap_width is None or (end - start) <= gap_width + 0.3:
        _draw_wall_segment(msp, orientation, pos, start, end, thickness)
        return None

    mid = (start + end) / 2
    gap_start, gap_end = mid - gap_width / 2, mid + gap_width / 2
    _draw_wall_segment(msp, orientation, pos, start, gap_start, thickness)
    _draw_wall_segment(msp, orientation, pos, gap_end, end, thickness)
    return gap_start, gap_end


def _draw_interior_wall(msp, edge: dict, wall_thickness: float, door_width: float | None) -> None:
    orientation, pos, start, end = edge["orientation"], edge["pos"], edge["start"], edge["end"]
    gap = _draw_gapped_wall(msp, orientation, pos, start, end, wall_thickness, door_width)
    if gap is not None:
        _draw_door_symbol(msp, orientation, pos, gap[0], gap[1])


def _draw_exterior_walls_for_room(
    msp, rect: dict, length: float, width: float, wall_thickness: float, window_width: float
) -> None:
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]

    if abs(x) < _EDGE_TOL:
        _draw_exterior_segment(msp, "vertical", 0.0, y, y + h, wall_thickness, window_width)
    if abs((x + w) - length) < _EDGE_TOL:
        _draw_exterior_segment(msp, "vertical", length, y, y + h, wall_thickness, window_width)
    if abs(y) < _EDGE_TOL:
        _draw_exterior_segment(msp, "horizontal", 0.0, x, x + w, wall_thickness, window_width)
    if abs((y + h) - width) < _EDGE_TOL:
        _draw_exterior_segment(msp, "horizontal", width, x, x + w, wall_thickness, window_width)


def _draw_exterior_segment(
    msp, orientation: str, pos: float, start: float, end: float, wall_thickness: float, window_width: float
) -> None:
    seg_len = end - start
    # Same clamp shape as blueprint_svg.py's _draw_windows() - never wider
    # than 40% of the segment, never so wide it eats the whole wall.
    win_len = min(window_width, seg_len * 0.4, seg_len - 0.2)
    if win_len <= 0.3:
        _draw_wall_segment(msp, orientation, pos, start, end, wall_thickness)
        return

    mid = (start + end) / 2
    gap_start, gap_end = mid - win_len / 2, mid + win_len / 2
    _draw_wall_segment(msp, orientation, pos, start, gap_start, wall_thickness)
    _draw_wall_segment(msp, orientation, pos, gap_end, end, wall_thickness)
    _draw_window_symbol(msp, orientation, pos, gap_start, gap_end)


def _draw_door_symbol(msp, orientation: str, pos: float, gap_start: float, gap_end: float) -> None:
    """A standard door symbol (leaf line + quarter-circle swing arc), same
    "legible mark, not construction-grade" precedent as blueprint_svg.py's
    own _draw_door() - real coordinates here, no pixel/scale conversion
    needed since ezdxf works directly in the plot's own real-world units."""
    leaf_len = gap_end - gap_start
    if orientation == "vertical":
        hinge = (pos, gap_start)
        leaf_end = (pos + leaf_len, gap_start)
    else:
        hinge = (gap_start, pos)
        leaf_end = (gap_start, pos + leaf_len)

    msp.add_line(hinge, leaf_end, dxfattribs={"layer": "DOORS"})
    msp.add_arc(
        center=hinge,
        radius=leaf_len,
        start_angle=0 if orientation == "vertical" else 90,
        end_angle=90 if orientation == "vertical" else 180,
        dxfattribs={"layer": "DOORS"},
    )


def _draw_window_symbol(msp, orientation: str, pos: float, gap_start: float, gap_end: float) -> None:
    """A single glazing line across the window opening, on its own WINDOWS
    layer/color so it reads distinctly from a door or a plain wall gap."""
    if orientation == "vertical":
        msp.add_line((pos, gap_start), (pos, gap_end), dxfattribs={"layer": "WINDOWS"})
    else:
        msp.add_line((gap_start, pos), (gap_end, pos), dxfattribs={"layer": "WINDOWS"})


def _draw_room_text(msp, rect: dict, unit: str) -> None:
    x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
    label_height = max(_ROOM_LABEL_MIN_HEIGHT, min(_ROOM_LABEL_MAX_HEIGHT, _ROOM_LABEL_HEIGHT_RATIO * min(w, h)))
    cx, cy = x + w / 2, y + h / 2

    name_text = msp.add_text(rect["name"], dxfattribs={"layer": "TEXT", "height": label_height})
    name_text.set_placement((cx, cy + label_height * 0.6), align=TextEntityAlignment.MIDDLE_CENTER)

    dims_text = msp.add_text(
        f"{w:.1f}x{h:.1f}{unit}",
        dxfattribs={"layer": "TEXT", "height": label_height * 0.75},
    )
    dims_text.set_placement((cx, cy - label_height * 0.6), align=TextEntityAlignment.MIDDLE_CENTER)
