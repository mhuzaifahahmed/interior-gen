"""Phase D0 diagnostic + renderer - public-room bowling-alley shape.

Dumps the mask engine's internal owner grid (ASCII) for the winning
candidate AND renders the real PNG via the dispatcher, reporting Living/
Dining aspect ratios. Matches the CURRENT clean engine API (the older
scripts/phase_d_trace.py references the discarded stalled attempt's API and
no longer runs).

Run: ./.venv/Scripts/python.exe scripts/phase_d0_render.py [trace|render]
Outputs: scripts/output/phase_d0/*.png (gitignored).
"""
import os
import sys

os.environ["HOUSE_LAYOUT_ENGINE"] = "mask"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.pipeline.floor_layout_mask as m  # noqa: E402
from app.pipeline.floor_layout import _zone_key  # noqa: E402
from app.pipeline.layout_engine import layout_floor_with_warnings  # noqa: E402
from app.pipeline.blueprint_svg import render_floor_blueprint  # noqa: E402
from app.pipeline.layout_quality import score_floor_layout  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "phase_d0")
os.makedirs(OUT_DIR, exist_ok=True)


def _grid_for(targets, dimensions, spine_width_delta, cross_link):
    """Reconstruct the owner grid for one candidate (copy of _grow_floor's
    setup, current API) for ASCII dumping."""
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
    band = m._public_band_rows(targets, public_idx, cols, rows, cell_area)
    zones = [_zone_key(t["name"]) for t in targets]
    spine_cells = set()
    if has_spine:
        spine_cells = m._reserve_spine(cols, rows, cell_w, cell_h, unit, spine_width_delta, cross_link, len(targets), band)
    owner = [[-1 for _ in range(cols)] for _ in range(rows)]
    for (c, r) in spine_cells:
        owner[r][c] = -2
    target_cells = [max(1, round(t["preferred_area"] / cell_area)) for t in targets]
    min_cells = [max(1, round(t["min_area"] / cell_area)) for t in targets]
    max_cells = [max(min_cells[i], round(targets[i]["max_area"] / cell_area)) for i in range(len(targets))]
    seeds = m._seed_rooms(targets, public_idx, circulation_idx, private_idx, cols, rows, spine_cells, min_cells, band)
    cells_by_room = [set() for _ in targets]
    for i, seed in enumerate(seeds):
        if seed is not None:
            cells_by_room[i].add(seed)
            owner[seed[1]][seed[0]] = i
    m._grow_rooms(owner, cells_by_room, seeds, cols, rows, target_cells, min_cells, max_cells, zones, band)
    m._fill_leftovers(owner, cells_by_room, target_cells, max_cells, cols, rows, spine_cells, zones, band)
    m._ensure_minimum_presence(cells_by_room, min_cells, owner)
    print(f"   band={band}")
    return owner, cols, rows, seeds, public_idx


def trace_case(label, rooms, dimensions, garage_cars, facing):
    print(f"\n{'='*72}\n{label}  dims={dimensions} facing={facing}\n{'='*72}")
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
    print(f"footprint core={core['length']:.1f}x{core['width']:.1f}  winning "
          f"spine_width_delta={best_meta[0]} cross_link={best_meta[1]} score={best_score:.0f}")

    owner, cols, rows, seeds, public_idx = _grid_for(targets, core, *best_meta)
    glyph = {i: (chr(ord("A") + i) if i < 26 else chr(ord("a") + i - 26)) for i in range(len(targets))}
    print(f"grid {cols}x{rows} (row 0 = FRONT/low-y). '#'=spine '.'=unclaimed")
    print("  legend:", "  ".join(f"{glyph[i]}={t['name']}" for i, t in enumerate(targets)))
    print("  public_idx:", [glyph[i] for i in public_idx])
    print("  seeds:", {glyph[i]: seeds[i] for i in range(len(targets)) if seeds[i]})
    for r in range(rows):
        line = []
        for c in range(cols):
            v = owner[r][c]
            line.append("." if v == -1 else ("#" if v == -2 else glyph[v]))
        print("   " + "".join(line))

    print("\nrooms (core coords, pre-facing):")
    for r in best:
        parts = r.get("parts") or [{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}]
        area = sum(p["w"] * p["h"] for p in parts)
        asp = max(max(p["w"], p["h"]) / max(min(p["w"], p["h"]), 0.01) for p in parts)
        pstr = "  ".join(f"[{p['w']:.1f}x{p['h']:.1f}]" for p in parts)
        print(f"  {r['name']:<16} zone={_zone_key(r['name'])} bbox {r['w']:.1f}x{r['h']:.1f} "
              f"area={area:.0f} aspect={asp:.1f} parts={len(parts)}: {pstr}")


def render_case(label, rooms, dimensions, garage_cars, facing, floor_number=1, total_floors=1):
    rects, warnings = layout_floor_with_warnings(rooms, dimensions, garage_cars=garage_cars, facing=facing)
    png = render_floor_blueprint(floor_number, rects, dimensions, total_floors, facing)
    out = os.path.join(OUT_DIR, f"{label}.png")
    with open(out, "wb") as f:
        f.write(png)
    hard = [(w.room, w.detail) for w in warnings if w.hard]
    print(f"\n=== {label} ({facing}) -> {out}")

    def metrics_of(name):
        for r in rects:
            if r["name"].lower() == name.lower():
                parts = r.get("parts") or [{"w": r["w"], "h": r["h"]}]
                bbox_asp = max(r["w"], r["h"]) / max(min(r["w"], r["h"]), 0.01)
                area = sum(p["w"] * p["h"] for p in parts)
                fill = area / max(r["w"] * r["h"], 0.01)
                part_asp = max(max(p["w"], p["h"]) / max(min(p["w"], p["h"]), 0.01) for p in parts)
                return bbox_asp, fill, part_asp, area
        return None, None, None, None
    for nm in ("Living Room", "Dining Room"):
        bbox_asp, fill, part_asp, area = metrics_of(nm)
        if bbox_asp is not None:
            print(f"    {nm:<14} bbox_aspect={bbox_asp:.2f} fill={fill:.2f} worst_part_aspect={part_asp:.1f} area={area:.0f}")
    print(f"    hard violations: {len(hard)}")
    return rects, warnings


CASE1_GROUND = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Staircase", "area": 1},
]
CASE2 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2}, {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
CASE3 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 1}, {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bathroom 1", "area": 1},
]
CASE4 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bedroom 4", "area": 1}, {"name": "Bathroom 1", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
D150 = {"length": 150.0, "width": 150.0, "unit": "ft"}
D5060 = {"length": 50.0, "width": 60.0, "unit": "ft"}
D3040 = {"length": 30.0, "width": 40.0, "unit": "ft"}
D9040 = {"length": 90.0, "width": 40.0, "unit": "ft"}

mode = sys.argv[1] if len(sys.argv) > 1 else "both"

if mode in ("trace", "both"):
    trace_case("CASE2 50x60 south", CASE2, D5060, None, "south")
    trace_case("CASE3 30x40 south", CASE3, D3040, None, "south")
    trace_case("CASE4 90x40 south", CASE4, D9040, None, "south")
    trace_case("CASE1 150x150 south", CASE1_GROUND, D150, 1, "south")

if mode in ("render", "both"):
    render_case("case1_150x150_south", CASE1_GROUND, D150, 1, "south", 1, 2)
    render_case("case2_50x60_south", CASE2, D5060, None, "south")
    render_case("case3_30x40_south", CASE3, D3040, None, "south")
    render_case("case4_90x40_south", CASE4, D9040, None, "south")
    for f in ("north", "east", "west"):
        render_case(f"case5_50x60_{f}", CASE2, D5060, None, f)
