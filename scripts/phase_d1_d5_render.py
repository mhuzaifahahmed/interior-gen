"""Phase D1-D5 diagnostic + renderer (mask engine).

Renders + traces the cases relevant to D1 (kitchen<->dining adjacency),
D2 (utility/laundry next to kitchen, ground AND upper floor), D3 (master in
far quiet corner + ensuite), D4 (garage real shape + road edge), D5
(circulation compactness). Adapted from scripts/phase_d0_render.py.

Run: ./.venv/Scripts/python.exe scripts/phase_d1_d5_render.py [trace|render|both]
Outputs: scripts/output/phase_d1_d5/*.png (gitignored).
"""
import os
import sys

os.environ["HOUSE_LAYOUT_ENGINE"] = "mask"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.pipeline.floor_layout_mask as m  # noqa: E402
from app.pipeline.floor_layout import _zone_key, room_parts, footprint_bbox  # noqa: E402
from app.pipeline.layout_engine import layout_floor_with_warnings  # noqa: E402
from app.pipeline.blueprint_svg import render_floor_blueprint  # noqa: E402
from app.pipeline.layout_quality import score_floor_layout  # noqa: E402
from app.pipeline.room_specs import classify_room_category, garage_dimensions  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "phase_d1_d5")
os.makedirs(OUT_DIR, exist_ok=True)


def _shared_edge_len(a: dict, b: dict) -> float:
    """Length of shared boundary (0 if not touching), checking all parts."""
    best = 0.0
    for pa in room_parts(a):
        ax0, ay0, ax1, ay1 = pa["x"], pa["y"], pa["x"] + pa["w"], pa["y"] + pa["h"]
        for pb in room_parts(b):
            bx0, by0, bx1, by1 = pb["x"], pb["y"], pb["x"] + pb["w"], pb["y"] + pb["h"]
            # vertical shared edge
            if abs(ax1 - bx0) < 0.3 or abs(bx1 - ax0) < 0.3:
                overlap = max(0.0, min(ay1, by1) - max(ay0, by0))
                best = max(best, overlap)
            # horizontal shared edge
            if abs(ay1 - by0) < 0.3 or abs(by1 - ay0) < 0.3:
                overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
                best = max(best, overlap)
    return best


def _get(rects, name):
    return next((r for r in rects if r["name"].lower() == name.lower()), None)


def _center(r):
    return (r["x"] + r["w"] / 2.0, r["y"] + r["h"] / 2.0)


def render_case(label, rooms, dimensions, garage_cars, facing, floor_number=1, total_floors=1):
    rects, warnings = layout_floor_with_warnings(
        rooms, dimensions, garage_cars=garage_cars, facing=facing
    )
    png = render_floor_blueprint(floor_number, rects, dimensions, total_floors, facing)
    out = os.path.join(OUT_DIR, f"{label}.png")
    with open(out, "wb") as f:
        f.write(png)
    hard = [(w.room, w.detail) for w in warnings if w.hard]
    print(f"\n=== {label} ({facing}, floor {floor_number}/{total_floors}) -> {out}")
    fp = footprint_bbox(rects)
    print(f"    footprint bbox: {fp}")

    for r in rects:
        parts = room_parts(r)
        area = sum(p["w"] * p["h"] for p in parts)
        asp = max(r["w"], r["h"]) / max(min(r["w"], r["h"]), 0.01)
        cx, cy = _center(r)
        print(f"    {r['name']:<16} bbox={r['x']:.1f},{r['y']:.1f} {r['w']:.1f}x{r['h']:.1f} "
              f"area={area:.0f} aspect={asp:.1f} parts={len(parts)} center=({cx:.1f},{cy:.1f})")

    # D1: kitchen<->dining adjacency
    k, d = _get(rects, "Kitchen"), _get(rects, "Dining Room")
    if k and d:
        print(f"    [D1] Kitchen<->Dining shared edge: {_shared_edge_len(k, d):.1f} ft")
    # D2: utility/laundry <-> kitchen
    for uname in ("Laundry", "Utility", "Utility Room", "Laundry Room"):
        u = _get(rects, uname)
        if u and k:
            print(f"    [D2] {uname}<->Kitchen shared edge: {_shared_edge_len(u, k):.1f} ft")
    # D3: master far from entrance + ensuite adjacency
    mb, mbath, entry = _get(rects, "Master Bedroom"), _get(rects, "Master Bathroom"), _get(rects, "Entry")
    if mb and entry:
        ec, mc = _center(entry), _center(mb)
        dist = ((ec[0] - mc[0]) ** 2 + (ec[1] - mc[1]) ** 2) ** 0.5
        print(f"    [D3] Master<->Entry center dist: {dist:.1f} ft")
        # list all bedroom distances from entry for comparison
        for r in rects:
            if classify_room_category(r["name"]) == "bedroom":
                rc = _center(r)
                dd = ((ec[0] - rc[0]) ** 2 + (ec[1] - rc[1]) ** 2) ** 0.5
                print(f"           {r['name']:<16} dist from entry: {dd:.1f} ft")
    if mb and mbath:
        print(f"    [D3] Master<->MasterBath shared edge: {_shared_edge_len(mb, mbath):.1f} ft")
    # D4: garage shape + road edge
    g = _get(rects, "Garage")
    if g:
        gw = min(g["w"], g["h"])
        gcars = garage_cars or 1
        req_w, req_d = garage_dimensions(gcars, dimensions.get("unit") or "ft")
        print(f"    [D4] Garage {g['w']:.1f}x{g['h']:.1f}; required min width={req_w:.1f} depth={req_d:.1f}")
        # which plot edge does garage touch? facing edge check
        print(f"         garage min(w,h)={gw:.1f}")
    print(f"    hard violations ({len(hard)}): {hard}")
    return rects, warnings


# --- Room programs ---
# D1/D3/D5 base case
CASE_150 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Staircase", "area": 1},
]
CASE_50x60 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2}, {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
# D2: ground floor with utility/laundry
CASE_UTILITY = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Laundry", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
# D2: upper floor with kitchen + laundry (per-floor request path)
CASE_UPPER_KITCHEN = [
    {"name": "Kitchen", "area": 1}, {"name": "Laundry", "area": 1},
    {"name": "Sitting Area", "area": 2}, {"name": "Bedroom 4", "area": 1},
    {"name": "Bedroom 5", "area": 1}, {"name": "Bathroom 4", "area": 1},
    {"name": "Staircase", "area": 1},
]
# D4: garage cases
CASE_GARAGE2 = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]

D150 = {"length": 150.0, "width": 150.0, "unit": "ft"}
D5060 = {"length": 50.0, "width": 60.0, "unit": "ft"}
D6080 = {"length": 60.0, "width": 80.0, "unit": "ft"}

mode = sys.argv[1] if len(sys.argv) > 1 else "render"

if mode in ("render", "both"):
    render_case("d1_150x150_south", CASE_150, D150, 1, "south", 1, 2)
    render_case("d1_50x60_south", CASE_50x60, D5060, None, "south")
    render_case("d2_utility_ground_south", CASE_UTILITY, D6080, None, "south")
    render_case("d2_upper_kitchen_south", CASE_UPPER_KITCHEN, D6080, None, "south", 2, 2)
    render_case("d4_garage2_south", CASE_GARAGE2, D6080, 2, "south")
    render_case("d4_garage1_150_south", CASE_150, D150, 1, "south", 1, 2)
    for f in ("north", "east", "west"):
        render_case(f"d3_50x60_{f}", CASE_50x60, D5060, None, f)
