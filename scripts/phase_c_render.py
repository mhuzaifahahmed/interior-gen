"""Phase C scratch renderer - auto site-planning (yard/driveway) verification.

Forces HOUSE_LAYOUT_ENGINE=mask, renders the exact 150x150/south/2-floor
reported case (with yard+driveway) and a small 30x40 right-sized plot (to
confirm C0's "strict no-op on small/right-sized plots" requirement). Saves
PNGs + DXFs to scripts/output/phase_c/ and prints footprint-coverage numbers
so the C0 behavior can be checked numerically, not just visually.

Re-run with: ./.venv/Scripts/python.exe scripts/phase_c_render.py
"""

import os
import sys

os.environ["HOUSE_LAYOUT_ENGINE"] = "mask"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline.floor_layout import footprint_bbox  # noqa: E402
from app.pipeline.layout_engine import layout_floor_with_warnings  # noqa: E402
from app.pipeline.blueprint_svg import render_floor_blueprint  # noqa: E402
from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf  # noqa: E402

OUT_DIR = os.path.join("scripts", "output", "phase_c")
os.makedirs(OUT_DIR, exist_ok=True)


def _report(label, dimensions, rects, warnings):
    fp = footprint_bbox(rects)
    plot_area = dimensions["length"] * dimensions["width"]
    if fp is None:
        print(f"{label}: no footprint (empty rects)")
        return
    fp_area = (fp[2] - fp[0]) * (fp[3] - fp[1])
    print(
        f"{label}: plot={dimensions['length']}x{dimensions['width']}{dimensions['unit']} "
        f"({plot_area:.0f} sq{dimensions['unit']}) footprint_bbox={tuple(round(v, 1) for v in fp)} "
        f"footprint_area={fp_area:.0f} coverage={fp_area / plot_area:.1%} "
        f"hard_violations={[(v.room, v.detail) for v in warnings if v.hard]}"
    )


GROUND_ROOMS = [
    {"name": "Living Room", "area": 3}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 2}, {"name": "Garage", "area": 1},
    {"name": "Entry", "area": 1}, {"name": "Master Bedroom", "area": 2},
    {"name": "Master Bathroom", "area": 1}, {"name": "Bedroom 2", "area": 1},
    {"name": "Bathroom 2", "area": 1}, {"name": "Bedroom 3", "area": 1},
    {"name": "Staircase", "area": 1},
]
UPPER_ROOMS = [
    {"name": "Bedroom 4", "area": 1}, {"name": "Bathroom 3", "area": 1},
    {"name": "Bedroom 5", "area": 1}, {"name": "Bathroom 4", "area": 1},
    {"name": "Staircase", "area": 1}, {"name": "Sitting Area", "area": 2},
]

# 1) The exact reported case: 150x150, south, 2-floor.
dims_big = {"length": 150.0, "width": 150.0, "unit": "ft"}
rects1, warn1 = layout_floor_with_warnings(GROUND_ROOMS, dims_big, garage_cars=1, facing="south")
_report("150x150 south floor1", dims_big, rects1, warn1)
png1 = render_floor_blueprint(1, rects1, dims_big, 2, "south")
open(os.path.join(OUT_DIR, "big_floor1.png"), "wb").write(png1)
dxf1 = render_floor_blueprint_dxf(1, rects1, dims_big, "south")
open(os.path.join(OUT_DIR, "big_floor1.dxf"), "wb").write(dxf1)

rects2, warn2 = layout_floor_with_warnings(UPPER_ROOMS, dims_big, garage_cars=None, facing="south")
_report("150x150 south floor2 (upper)", dims_big, rects2, warn2)
png2 = render_floor_blueprint(2, rects2, dims_big, 2, "south")
open(os.path.join(OUT_DIR, "big_floor2.png"), "wb").write(png2)
dxf2 = render_floor_blueprint_dxf(2, rects2, dims_big, "south")
open(os.path.join(OUT_DIR, "big_floor2.dxf"), "wb").write(dxf2)

# 2) Small/right-sized plot - must be a strict no-op for C0 (footprint ~
# fills the plot, same as before this feature existed).
SMALL_ROOMS = [
    {"name": "Living Room", "area": 2}, {"name": "Kitchen", "area": 1},
    {"name": "Dining Room", "area": 1}, {"name": "Entry", "area": 1},
    {"name": "Bedroom 1", "area": 1.5}, {"name": "Bathroom 1", "area": 1},
    {"name": "Bedroom 2", "area": 1.5},
]
dims_small = {"length": 30.0, "width": 40.0, "unit": "ft"}
rects3, warn3 = layout_floor_with_warnings(SMALL_ROOMS, dims_small, garage_cars=None, facing="south")
_report("30x40 south (small, no-op expected)", dims_small, rects3, warn3)
png3 = render_floor_blueprint(1, rects3, dims_small, 1, "south")
open(os.path.join(OUT_DIR, "small_floor1.png"), "wb").write(png3)
dxf3 = render_floor_blueprint_dxf(1, rects3, dims_small, "south")
open(os.path.join(OUT_DIR, "small_floor1.dxf"), "wb").write(dxf3)

print("\nDone. PNGs/DXFs written to", OUT_DIR)
