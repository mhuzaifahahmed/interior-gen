"""Phase F final verification matrix (mask engine, now the committed default).

Renders the plan's Phase F F1 matrix through the REAL call path - the
dispatcher `app.pipeline.layout_engine.layout_floor_with_warnings` (engine
chosen by settings.house_layout_engine, which is "mask" by default since
Phase E - this script deliberately does NOT set HOUSE_LAYOUT_ENGINE) ->
`app.pipeline.blueprint_svg.render_floor_blueprint`. Prints per-room geometry
plus the checklist metrics (hard violations, plot coverage, front setback,
living>=dining, kitchen<->dining shared wall, master-vs-entry distance,
ensuite attachment, hallway shape, garage width/road edge) so the PNGs can be
inspected against hard numbers.

Run: ./.venv/Scripts/python.exe scripts/phase_f_render.py
Outputs: scripts/output/phase_f/*.png (gitignored).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.pipeline.blueprint_svg import render_floor_blueprint  # noqa: E402
from app.pipeline.floor_layout import footprint_bbox, room_parts  # noqa: E402
from app.pipeline.layout_engine import layout_floor_with_warnings  # noqa: E402
from app.pipeline.room_specs import classify_room_category, garage_dimensions  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "phase_f")
os.makedirs(OUT_DIR, exist_ok=True)


def _shared_edge_len(a: dict, b: dict, tol: float = 0.3) -> float:
    best = 0.0
    for pa in room_parts(a):
        ax0, ay0, ax1, ay1 = pa["x"], pa["y"], pa["x"] + pa["w"], pa["y"] + pa["h"]
        for pb in room_parts(b):
            bx0, by0, bx1, by1 = pb["x"], pb["y"], pb["x"] + pb["w"], pb["y"] + pb["h"]
            if abs(ax1 - bx0) < tol or abs(bx1 - ax0) < tol:
                best = max(best, max(0.0, min(ay1, by1) - max(ay0, by0)))
            if abs(ay1 - by0) < tol or abs(by1 - ay0) < tol:
                best = max(best, max(0.0, min(ax1, bx1) - max(ax0, bx0)))
    return best


def _get(rects, name):
    return next((r for r in rects if r["name"].lower() == name.lower()), None)


def _center(r):
    return (r["x"] + r["w"] / 2.0, r["y"] + r["h"] / 2.0)


def _area(r):
    return sum(p["w"] * p["h"] for p in room_parts(r))


def _aspect(r):
    return max(r["w"], r["h"]) / max(min(r["w"], r["h"]), 0.01)


def _front_setback(fp, dims, facing):
    x0, y0, x1, y1 = fp
    L, W = dims["length"], dims["width"]
    return {"south": W - y1, "north": y0, "east": L - x1, "west": x0}.get(facing, float("nan"))


def render_case(label, rooms, dims, facing, floor_number=1, total_floors=1, garage_cars=None):
    rects, warnings = layout_floor_with_warnings(rooms, dims, garage_cars=garage_cars, facing=facing)
    png = render_floor_blueprint(floor_number, rects, dims, total_floors, facing)
    out = os.path.join(OUT_DIR, f"{label}.png")
    with open(out, "wb") as f:
        f.write(png)

    print(f"\n=== {label}  [{dims['length']:.0f}x{dims['width']:.0f} {facing}, "
          f"floor {floor_number}/{total_floors}, garage_cars={garage_cars}] -> {out}")
    fp = footprint_bbox(rects)
    fp_w, fp_h = fp[2] - fp[0], fp[3] - fp[1]
    plot_area = dims["length"] * dims["width"]
    print(f"    footprint {fp_w:.1f}x{fp_h:.1f} ft = {fp_w * fp_h:.0f} sq ft "
          f"({100 * fp_w * fp_h / plot_area:.0f}% of plot), front setback "
          f"{_front_setback(fp, dims, facing):.1f} ft")
    covered = sum(_area(r) for r in rects)
    print(f"    room-area sum {covered:.0f} vs footprint {fp_w * fp_h:.0f} "
          f"(gap {fp_w * fp_h - covered:+.1f})")
    for r in rects:
        parts = room_parts(r)
        thin = min(min(p["w"], p["h"]) for p in parts)
        print(f"    {r['name']:<16} bbox=({r['x']:.1f},{r['y']:.1f}) {r['w']:.1f}x{r['h']:.1f} "
              f"area={_area(r):.0f} aspect={_aspect(r):.2f} parts={len(parts)} thinnest_part={thin:.1f}")

    lr, dr, k = _get(rects, "Living Room"), _get(rects, "Dining Room"), _get(rects, "Kitchen")
    if lr and dr:
        print(f"    [#3] Living {_area(lr):.0f} vs Dining {_area(dr):.0f} -> "
              f"{'OK living>=dining' if _area(lr) >= _area(dr) else 'INVERTED'}")
    if k and dr:
        print(f"    [#7a] Kitchen<->Dining shared wall {_shared_edge_len(k, dr):.1f} ft")
    lau = _get(rects, "Laundry")
    if lau and k:
        print(f"    [#7b] Laundry<->Kitchen shared wall {_shared_edge_len(lau, k):.1f} ft")
    mb, mbath = _get(rects, "Master Bedroom"), _get(rects, "Master Bathroom")
    anchor = _get(rects, "Entry") or _get(rects, "Garage")
    if mb and anchor:
        ac = _center(anchor)
        dists = {
            r["name"]: math.dist(ac, _center(r))
            for r in rects if classify_room_category(r["name"]) == "bedroom"
        }
        far = max(dists, key=dists.get)
        print(f"    [#5] bedroom distances from {anchor['name']}: "
              + ", ".join(f"{n}={d:.1f}" for n, d in dists.items())
              + f" -> farthest={far}")
    if mb and mbath:
        print(f"    [#5] Master<->ensuite shared wall {_shared_edge_len(mb, mbath):.1f} ft")
    hall = _get(rects, "Hallway")
    if hall:
        print(f"    [#6] Hallway bbox {hall['w']:.1f}x{hall['h']:.1f} area {_area(hall):.0f} "
              f"aspect {_aspect(hall):.2f} parts {len(room_parts(hall))}")
    g = _get(rects, "Garage")
    if g:
        req_w, req_d = garage_dimensions(garage_cars or 1, dims["unit"])
        print(f"    [#8] Garage {g['w']:.1f}x{g['h']:.1f} (req >= {req_w:.1f} wide x {req_d:.1f} deep)")
    hard = [(w.room, w.detail) for w in warnings if w.hard]
    soft = [(w.room, w.detail) for w in warnings if not w.hard]
    print(f"    HARD violations ({len(hard)}): {hard}")
    print(f"    soft violations ({len(soft)}): {soft}")
    return rects, warnings


# --- Programs ---------------------------------------------------------------
CASE1_GROUND = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Staircase", "area": 1},
]
CASE1_UPPER = [
    {"name": "Bedroom 4", "area": 1}, {"name": "Bathroom 3", "area": 1},
    {"name": "Bedroom 5", "area": 1}, {"name": "Bathroom 4", "area": 1},
    {"name": "Staircase", "area": 1}, {"name": "Sitting Area", "area": 2},
]
CASE2 = [  # 3 bed / 2 bath + LKD + Entry
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2}, {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
CASE3 = [  # 2 bed / 1 bath + LKD
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 1}, {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bathroom 1", "area": 1},
]
CASE4 = [  # 4 bed / 2 bath + LKD
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bedroom 4", "area": 1}, {"name": "Bathroom 1", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
# 60x80, 3 floors, 2 bed + 2 bath per floor. Mirrors what run_house_pipeline
# guarantees: ground floor always gets Living/Kitchen/Dining + Entry; upper
# floors get a Sitting Area; every floor of a multi-storey house a Staircase.
CASE5_GROUND = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Entry", "area": 1},
    {"name": "Bedroom 1", "area": 1}, {"name": "Bathroom 1", "area": 1},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bathroom 2", "area": 1},
    {"name": "Staircase", "area": 1},
]


def _case5_upper(floor):
    b = 2 * floor - 1
    return [
        {"name": "Sitting Area", "area": 2},
        {"name": f"Bedroom {b}", "area": 1}, {"name": f"Bathroom {b}", "area": 1},
        {"name": f"Bedroom {b + 1}", "area": 1}, {"name": f"Bathroom {b + 1}", "area": 1},
        {"name": "Staircase", "area": 1},
    ]


CASE6_SPARSE = [
    {"name": "Bedroom 1", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bedroom 3", "area": 1},
]

D150 = {"length": 150.0, "width": 150.0, "unit": "ft"}
D5060 = {"length": 50.0, "width": 60.0, "unit": "ft"}
D3040 = {"length": 30.0, "width": 40.0, "unit": "ft"}
D9040 = {"length": 90.0, "width": 40.0, "unit": "ft"}
D6080 = {"length": 60.0, "width": 80.0, "unit": "ft"}

if __name__ == "__main__":
    print(f"settings.house_layout_engine = {settings.house_layout_engine!r} "
          f"(HOUSE_LAYOUT_ENGINE env = {os.environ.get('HOUSE_LAYOUT_ENGINE')!r})")

    # Row 1: 150x150, 2 floors, south, the exact reported program.
    render_case("f1_150x150_south_floor1", CASE1_GROUND, D150, "south", 1, 2, garage_cars=1)
    render_case("f1_150x150_south_floor2", CASE1_UPPER, D150, "south", 2, 2)
    # Row 2-4: single-floor south cases.
    render_case("f2_50x60_south", CASE2, D5060, "south")
    render_case("f3_30x40_south", CASE3, D3040, "south")
    render_case("f4_90x40_south", CASE4, D9040, "south")
    # Row 5: 60x80, 3 floors, north/east/west, every floor.
    for facing in ("north", "east", "west"):
        render_case(f"f5_60x80_{facing}_floor1", CASE5_GROUND, D6080, facing, 1, 3)
        render_case(f"f5_60x80_{facing}_floor2", _case5_upper(2), D6080, facing, 2, 3)
        render_case(f"f5_60x80_{facing}_floor3", _case5_upper(3), D6080, facing, 3, 3)
    # Row 6: 150x150, 1 floor, south, sparse 3 bedrooms only.
    render_case("f6_150x150_sparse_south", CASE6_SPARSE, D150, "south")
