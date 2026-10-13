"""Wall-time measurement of the mask engine for ONE realistic dense 3-floor
request, planner flag ON vs OFF. Measurement only - changes nothing.

Run:  python scripts/mask_timing.py
Writes scripts/output/quality/timing.txt (gitignored).

Planner ON goes through floor_layout_mask.layout_building (one BuildingPlan,
every floor laid out inside it, re-planned at most twice when a bonus room is
dropped) - exactly what generate_house.py does. Per-floor numbers come from
floor_layout_mask.LAST_FLOOR_DIAGNOSTICS (candidates grown / rectified,
best-of-N runs incl. bonus-drop trials). Planner OFF calls
layout_floor_with_warnings per floor (a fixed 4-candidate best-of-N).
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import settings  # noqa: E402
import app.pipeline.floor_layout_mask as mask  # noqa: E402
from tests.mask_quality_matrix import DENSE_2F, TOP_3F, UPPER_2F  # noqa: E402

OUT = os.path.join(ROOT, "scripts", "output", "quality", "timing.txt")

CASES = [
    ("90x90_dense_3f", {"length": 90.0, "width": 90.0, "unit": "ft"}, [DENSE_2F, UPPER_2F, TOP_3F], 2, "south"),
    ("60x80_dense_3f", {"length": 60.0, "width": 80.0, "unit": "ft"}, [DENSE_2F, UPPER_2F, TOP_3F], 2, "south"),
    ("150x150_dense_3f (spacious tier)", {"length": 150.0, "width": 150.0, "unit": "ft"},
     [DENSE_2F, UPPER_2F, TOP_3F], 2, "south"),
]


def _planner_on(plot, floors, cars, facing):
    rows = []
    orig = mask.layout_floor_with_warnings

    def timed(rooms, dims, garage_cars=None, facing=None, plan=None):
        t0 = time.perf_counter()
        out = orig(rooms, dims, garage_cars, facing, plan)
        rows.append((plan.floor_number if plan is not None else 0, time.perf_counter() - t0,
                     dict(mask.LAST_FLOOR_DIAGNOSTICS)))
        return out

    mask.layout_floor_with_warnings = timed
    try:
        t0 = time.perf_counter()
        mask.layout_building(floors, plot, facing, cars, len(floors))
        total = time.perf_counter() - t0
    finally:
        mask.layout_floor_with_warnings = orig
    return total, rows


def _planner_off(plot, floors, cars, facing):
    rows = []
    t0 = time.perf_counter()
    for i, rooms in enumerate(floors, 1):
        t1 = time.perf_counter()
        mask.layout_floor_with_warnings(rooms, plot, cars, facing)
        rows.append((i, time.perf_counter() - t1))
    return time.perf_counter() - t0, rows


def main():
    lines = ["Mask engine timing - one request per case (wall clock, this machine).", ""]
    previous = settings.house_planner_enabled
    try:
        for name, plot, floors, cars, facing in CASES:
            settings.house_planner_enabled = True
            total_on, rows_on = _planner_on(plot, floors, cars, facing)
            settings.house_planner_enabled = False
            total_off, rows_off = _planner_off(plot, floors, cars, facing)
            lines.append(f"== {name} ({len(floors)} floors, garage {cars}) ==")
            lines.append(f"planner ON : total {total_on:.2f}s for {len(rows_on)} floor layouts "
                         f"(incl. re-plans when a bonus room is dropped)")
            for floor_n, secs, d in rows_on:
                grown = d.get("grown", 0)
                per = secs / grown if grown else 0.0
                lines.append(
                    f"    floor {floor_n}: {secs:.2f}s  path={d.get('path')}  best-of-N runs={d.get('runs')}  "
                    f"candidates grown={grown} (rectified={d.get('rectified')})  ~{per * 1000:.0f} ms/candidate"
                )
            lines.append(f"planner OFF: total {total_off:.2f}s (4 candidates/floor, no rectification)")
            for floor_n, secs in rows_off:
                lines.append(f"    floor {floor_n}: {secs:.2f}s  ~{secs / 4 * 1000:.0f} ms/candidate")
            lines.append("")
    finally:
        settings.house_planner_enabled = previous
    text = "\n".join(lines)
    print(text)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
