"""Phase A baseline renderer - render the mask engine as-is across the
baseline matrix described in future-plans/mask-engine-adoption-and-layout-refinement.md.

Run with: ./.venv/Scripts/python.exe scripts/phase_a_render.py
Outputs land in scripts/output/phase_a/ (gitignored).

Read-only against app/ - this script does not modify any engine code.
"""
import os
import sys

os.environ["HOUSE_LAYOUT_ENGINE"] = "mask"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

assert settings.house_layout_engine == "mask", f"engine did not flip: {settings.house_layout_engine}"

from app.pipeline.layout_engine import layout_floor_with_warnings  # noqa: E402
from app.pipeline.blueprint_svg import render_floor_blueprint  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "phase_a")
os.makedirs(OUT_DIR, exist_ok=True)


def run_case(label, rooms, dimensions, garage_cars, facing, floor_number=1, total_floors=1):
    rects, warnings = layout_floor_with_warnings(
        rooms, dimensions, garage_cars=garage_cars, facing=facing
    )
    hard = [(w.room, w.detail) for w in warnings if w.hard]
    png = render_floor_blueprint(floor_number, rects, dimensions, total_floors, facing)
    out_path = os.path.join(OUT_DIR, f"{label}.png")
    with open(out_path, "wb") as f:
        f.write(png)
    print(f"\n=== {label} ===")
    print(f"dimensions={dimensions} garage_cars={garage_cars} facing={facing} floor={floor_number}/{total_floors}")
    print(f"rects ({len(rects)}):")
    for r in rects:
        area = r["w"] * r["h"]
        ratio = max(r["w"], r["h"]) / max(min(r["w"], r["h"]), 0.01)
        print(
            f"  {r['name']:<20} x={r['x']:.1f} y={r['y']:.1f} w={r['w']:.1f} h={r['h']:.1f} "
            f"area={area:.1f} aspect={ratio:.2f}"
        )
    print(f"HARD VIOLATIONS: {hard if hard else 'none'}")
    print(f"-> {out_path}")
    return rects, warnings


# ---------------------------------------------------------------------------
# Case 1: exact reported case - 150x150 ft, south, 2-floor, ground floor
# ---------------------------------------------------------------------------
dims_150 = {"length": 150.0, "width": 150.0, "unit": "ft"}
ground_rooms_150 = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2},
    {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1},
    {"name": "Bedroom 3", "area": 1},
    {"name": "Staircase", "area": 1},
]
run_case("case1_150x150_south_floor1", ground_rooms_150, dims_150, garage_cars=1, facing="south",
         floor_number=1, total_floors=2)

upper_rooms_150 = [
    {"name": "Bedroom 4", "area": 1},
    {"name": "Bathroom 3", "area": 1},
    {"name": "Bedroom 5", "area": 1},
    {"name": "Bathroom 4", "area": 1},
    {"name": "Staircase", "area": 1},
    {"name": "Sitting Area", "area": 2},
]
run_case("case1_150x150_south_floor2", upper_rooms_150, dims_150, garage_cars=None, facing="south",
         floor_number=2, total_floors=2)

# ---------------------------------------------------------------------------
# Case 2: medium - 50x60 ft, south, 1-floor, 3 bed/2 bath + LKD + entry
# ---------------------------------------------------------------------------
dims_50x60 = {"length": 50.0, "width": 60.0, "unit": "ft"}
rooms_50x60 = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2},
    {"name": "Entry", "area": 1},
    {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1},
    {"name": "Bedroom 2", "area": 1},
    {"name": "Bedroom 3", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
run_case("case2_50x60_south", rooms_50x60, dims_50x60, garage_cars=None, facing="south")

# ---------------------------------------------------------------------------
# Case 3: small/tight - 30x40 ft, south, 1-floor, 2 bed/1 bath + LKD
# ---------------------------------------------------------------------------
dims_30x40 = {"length": 30.0, "width": 40.0, "unit": "ft"}
rooms_30x40 = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 1},
    {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 1", "area": 1},
]
run_case("case3_30x40_south", rooms_30x40, dims_30x40, garage_cars=None, facing="south")

# ---------------------------------------------------------------------------
# Case 4: large wide - 90x40 ft, south, 1-floor, 4 bed/2 bath + LKD
# ---------------------------------------------------------------------------
dims_90x40 = {"length": 90.0, "width": 40.0, "unit": "ft"}
rooms_90x40 = [
    {"name": "Living Room", "area": 3},
    {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2},
    {"name": "Bedroom 1", "area": 2},
    {"name": "Bedroom 2", "area": 1},
    {"name": "Bedroom 3", "area": 1},
    {"name": "Bedroom 4", "area": 1},
    {"name": "Bathroom 1", "area": 1},
    {"name": "Bathroom 2", "area": 1},
]
run_case("case4_90x40_south", rooms_90x40, dims_90x40, garage_cars=None, facing="south")

# ---------------------------------------------------------------------------
# Case 5: facing sweep on case 2's room list (50x60)
# ---------------------------------------------------------------------------
for facing in ("north", "east", "west"):
    run_case(f"case5_50x60_{facing}", rooms_50x60, dims_50x60, garage_cars=None, facing=facing)

print("\nAll Phase A cases rendered.")
