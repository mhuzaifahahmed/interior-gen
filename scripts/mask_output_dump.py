"""Deterministic dump of mask-engine output for byte-comparison across code
versions (used to prove a speedup changes nothing).

  python scripts/mask_output_dump.py OUT.json [--no-capacity] [--early-stop]

Dumps, for planner OFF and ON: every matrix floor's rects + warnings (plus
the BuildingPlan floor info for ON) and the capacity table
(feasibility.room_count_requirements_table for 1-3 floors, ft and m, with
the mask engine active). Compare two dumps with `cmp`.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import settings  # noqa: E402
from app.pipeline.floor_layout_mask import layout_building, layout_floor_with_warnings  # noqa: E402
from tests.mask_quality_matrix import CASES  # noqa: E402


def dump(capacity=True):
    out = {}
    prev = (settings.house_planner_enabled, settings.house_layout_engine)
    settings.house_layout_engine = "mask"
    try:
        for planner in (False, True):
            settings.house_planner_enabled = planner
            mode = {}
            for c in CASES:
                n = len(c["floors"])
                if planner:
                    plan, res = layout_building(c["floors"], c["plot"], c["facing"], c["garage_cars"], n)
                    for i, (used, rects, warns) in enumerate(res, start=1):
                        mode[f"{c['id']}/floor{i}"] = {
                            "rects": rects, "warnings": warns, "used": used,
                            "room_max": dict(plan.for_floor(i).room_max),
                            "remainder": list(plan.for_floor(i).remainder_rooms),
                            "path": plan.for_floor(i).layout_path,
                        }
                else:
                    for i, rooms in enumerate(c["floors"], start=1):
                        rects, warns = layout_floor_with_warnings(
                            rooms, c["plot"], garage_cars=c["garage_cars"], facing=c["facing"])
                        mode[f"{c['id']}/floor{i}"] = {"rects": rects, "warnings": warns}
            out["planner_on" if planner else "planner_off"] = mode
            if capacity:
                from app.pipeline.feasibility import room_count_requirements_table as t
                t.cache_clear()
                out[("planner_on" if planner else "planner_off") + "_capacity"] = {
                    f"{f}{u}": t(f, u) for f in (1, 2, 3) for u in ("ft", "m")}
    finally:
        settings.house_planner_enabled, settings.house_layout_engine = prev
    return out


if __name__ == "__main__":
    path = sys.argv[1]
    if "--early-stop" in sys.argv:  # experiment toggle (see floor_layout_mask.EARLY_STOP_ON_CLEAN)
        import app.pipeline.floor_layout_mask as _m
        _m.EARLY_STOP_ON_CLEAN = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dump("--no-capacity" not in sys.argv), f, indent=1, sort_keys=True, default=repr)
