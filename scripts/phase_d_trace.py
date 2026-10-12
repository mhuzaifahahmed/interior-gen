"""Phase D diagnostic - dump the mask engine's internal owner grid as ASCII
art for the winning candidate, so we can SEE why public rooms come out as
bowling-alley slivers / why the hallway is a full-depth tunnel.

Run: ./.venv/Scripts/python.exe scripts/phase_d_trace.py
"""
import os
import sys

os.environ["HOUSE_LAYOUT_ENGINE"] = "mask"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.pipeline.floor_layout_mask as m  # noqa: E402
from app.pipeline.floor_layout import _zone_key  # noqa: E402
from app.pipeline.layout_quality import score_floor_layout  # noqa: E402


def trace_case(label, rooms, dimensions, garage_cars, facing):
    """Reconstruct the best candidate's owner grid and print it."""
    print(f"\n{'='*70}\n{label}  dims={dimensions} facing={facing}\n{'='*70}")
    real_length = float(dimensions.get("length") or 1)
    real_width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit") or "ft"
    exterior = m.to_plot_unit(m.EXTERIOR_WALL_THICKNESS_M, unit)
    net_length = max(real_length - 2 * exterior, 1.0)
    net_width = max(real_width - 2 * exterior, 1.0)
    fl, fw = m._mask_footprint_dimensions(rooms, net_length, net_width, unit, garage_cars)
    if facing in ("east", "west"):
        core = {"length": fw, "width": fl, "unit": unit}
    else:
        core = {"length": fl, "width": fw, "unit": unit}
    ordered = m._order_rooms(rooms)
    targets = m._room_targets(ordered, unit, garage_cars)

    best = None
    best_score = None
    best_meta = None
    for wd in m._CANDIDATE_SPINE_WIDTH_DELTAS:
        for cl in m._CANDIDATE_CROSS_LINK:
            cand = m._grow_floor(targets, core, wd, cl)
            scored = score_floor_layout(cand, core, garage_cars)
            if best_score is None or scored.score > best_score:
                best_score = scored.score
                best = cand
                best_meta = (wd, cl)
    print(f"footprint core={core['length']:.1f}x{core['width']:.1f}  winning candidate "
          f"spine_width_delta={best_meta[0]} cross_link={best_meta[1]} score={best_score:.0f}")

    # Rebuild the owner grid for the winning candidate so we can print it.
    wd, cl = best_meta
    _dump_grid(targets, core, wd, cl)

    print("\nrooms (core coords, pre-facing):")
    for r in best:
        parts = r.get("parts") or [{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}]
        pstr = "  ".join(f"[{p['w']:.1f}x{p['h']:.1f}]" for p in parts)
        area = sum(p["w"] * p["h"] for p in parts)
        print(f"  {r['name']:<16} zone={_zone_key(r['name'])} bbox {r['w']:.1f}x{r['h']:.1f} "
              f"area={area:.0f} parts={len(parts)}: {pstr}")


def _dump_grid(targets, dimensions, spine_width_delta, cross_link):
    """Re-run the grid build (copy of _grow_floor's setup) and print owner."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit") or "ft"
    cell_size = max(max(length, width, 1.0) / m._TARGET_CELLS_LONG_SIDE, 0.01)
    cols = m._clamp(round(length / cell_size), m._MIN_CELLS_PER_AXIS, m._MAX_CELLS_PER_AXIS)
    rows = m._clamp(round(width / cell_size), m._MIN_CELLS_PER_AXIS, m._MAX_CELLS_PER_AXIS)
    cell_w = length / cols
    cell_h = width / rows
    cell_area = cell_w * cell_h

    public_idx = [i for i, t in enumerate(targets) if _zone_key(t["name"]) == 0]
    circulation_idx = [i for i, t in enumerate(targets) if _zone_key(t["name"]) == 1]
    private_idx = [i for i, t in enumerate(targets) if _zone_key(t["name"]) == 2]
    has_spine = len(private_idx) >= m._MIN_ROOMS_FOR_SPINE
    band = 0
    if has_spine and public_idx and private_idx:
        band = m._public_band_rows(targets, public_idx, cols, rows, cell_w, cell_h)
    spine_cells = set()
    if has_spine:
        spine_cells = m._reserve_spine(cols, rows, band, cell_w, cell_h, unit, spine_width_delta, cross_link, len(targets))
    owner = [[-1 for _ in range(cols)] for _ in range(rows)]
    for (c, r) in spine_cells:
        owner[r][c] = -2
    if band > 0:
        pub = set(public_idx)
        row_lo = [0 if i in pub else band for i in range(len(targets))]
        row_hi = [band - 1 if i in pub else rows - 1 for i in range(len(targets))]
    else:
        row_lo = [0] * len(targets)
        row_hi = [rows - 1] * len(targets)
    target_cells = [max(1, round(t["preferred_area"] / cell_area)) for t in targets]
    min_cells = [max(1, round(t["min_area"] / cell_area)) for t in targets]
    max_cells = [max(min_cells[i], round(targets[i]["max_area"] / cell_area)) for i in range(len(targets))]
    seeds = m._seed_rooms(targets, public_idx, circulation_idx, private_idx, cols, rows, band, spine_cells, min_cells, row_lo, row_hi)
    cells_by_room = [set() for _ in targets]
    for i, seed in enumerate(seeds):
        if seed is not None:
            cells_by_room[i].add(seed)
            owner[seed[1]][seed[0]] = i
    m._grow_rooms(owner, cells_by_room, seeds, cols, rows, target_cells, min_cells, max_cells, row_lo, row_hi)
    m._fill_leftovers(owner, cells_by_room, target_cells, max_cells, cols, rows, spine_cells)
    m._ensure_minimum_presence(cells_by_room, min_cells, owner)
    print(f"   band={band}")

    glyph = {}
    for i, t in enumerate(targets):
        glyph[i] = chr(ord("A") + i) if i < 26 else chr(ord("a") + i - 26)
    print(f"\ngrid {cols}x{rows} (row 0 = FRONT/low-y). '#'=spine '.'=unclaimed")
    legend = "  ".join(f"{glyph[i]}={t['name']}" for i, t in enumerate(targets))
    print("  legend:", legend)
    print("  seeds:", {glyph[i]: seeds[i] for i in range(len(targets)) if seeds[i]})
    for r in range(rows):
        line = []
        for c in range(cols):
            v = owner[r][c]
            if v == -1:
                line.append(".")
            elif v == -2:
                line.append("#")
            else:
                line.append(glyph[v])
        print("   " + "".join(line))


CASE2 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2}, {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
trace_case("CASE2 50x60 south", CASE2, {"length": 50.0, "width": 60.0, "unit": "ft"}, None, "south")

CASE3 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 1}, {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bathroom 1", "area": 1},
]
trace_case("CASE3 30x40 south", CASE3, {"length": 30.0, "width": 40.0, "unit": "ft"}, None, "south")

CASE4 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bedroom 4", "area": 1}, {"name": "Bathroom 1", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
trace_case("CASE4 90x40 south", CASE4, {"length": 90.0, "width": 40.0, "unit": "ft"}, None, "south")
