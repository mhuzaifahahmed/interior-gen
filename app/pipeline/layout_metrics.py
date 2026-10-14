"""Pure per-floor quality metrics for the mask-planner quality gate (Phase 0).
Measurement only - no I/O, never changes layout behavior. Consumed by
scripts/mask_quality_report.py and tests/test_mask_quality_gate.py."""

from app.pipeline.layout_quality import bedroom_aspect, daylight_failures, score_floor_layout, stair_over_cap
from app.pipeline.planning_rules import BEDROOM_ASPECT_HARD
from app.pipeline.room_specs import (
    classify_room_category,
    max_area_for_room,
    min_area_for_room,
    to_plot_unit,
)

_NO_MAX_CATEGORIES = ("hallway", "staircase", "garage")


def _parts(rect: dict) -> list[dict]:
    parts = rect.get("parts")
    if parts:
        return [p for p in parts if p.get("w", 0) > 0 and p.get("h", 0) > 0]
    return [{"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": rect["h"]}]


def _bbox(rects: list[dict]) -> dict:
    ps = [p for r in rects for p in _parts(r)]
    return {
        "x0": min(p["x"] for p in ps),
        "y0": min(p["y"] for p in ps),
        "x1": max(p["x"] + p["w"] for p in ps),
        "y1": max(p["y"] + p["h"] for p in ps),
    }


def _overlap_area(a: dict, b: dict) -> float:
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return ox * oy if ox > 1e-6 and oy > 1e-6 else 0.0


def measure_floor(
    rects: list[dict],
    plot_dims: dict,
    footprint: dict | None = None,
    total_floors: int = 1,
    floor_number: int = 1,
    garage_cars: int | None = None,
    max_overrides: dict | None = None,
    facing: str | None = None,
) -> dict:
    """`facing` (Phase 4b) enables the circulation metrics (reachability
    from the entry over the real door graph - door_graph.circulation_report);
    without it they stay None, exactly as before.
    `max_overrides` (name -> max area) replaces room_specs' max for the
    named rooms - used only for planner-added remainder rooms (an upper-floor
    Terrace sized to the leftover area). None (default) = unchanged."""
    unit = plot_dims.get("unit") or "ft"
    length, width = float(plot_dims["length"]), float(plot_dims["width"])
    fp = footprint or _bbox(rects)
    fp_area = (fp["x1"] - fp["x0"]) * (fp["y1"] - fp["y0"])

    # Scored against the FOOTPRINT's own size (rects live in plot coords, offset
    # by yards; the scorer's coverage check compares against the box it is given).
    fp_dims = {"length": fp["x1"] - fp["x0"], "width": fp["y1"] - fp["y0"], "unit": unit}
    score = score_floor_layout(rects, fp_dims, garage_cars)
    hard = [v for v in score.violations if v.hard]
    soft = [v for v in score.violations if not v.hard]

    all_parts = [p for r in rects for p in _parts(r)]
    covered = sum(p["w"] * p["h"] for p in all_parts)
    overlap = 0.0
    for a in range(len(all_parts)):
        for b in range(a + 1, len(all_parts)):
            overlap += _overlap_area(all_parts[a], all_parts[b])

    dl_hard, dl_soft = daylight_failures(rects, fp, unit)

    oversized, undersized, ratios = [], [], {}
    areas_by_cat: dict[str, list[float]] = {}
    max_parts, max_secondary = 0, 0.0
    for r in rects:
        name = str(r.get("name") or "")
        cat = classify_room_category(name)
        ps = _parts(r)
        area = sum(p["w"] * p["h"] for p in ps)
        areas_by_cat.setdefault(cat, []).append(area)
        max_parts = max(max_parts, len(ps))
        if name.lower() != "hallway" and area > 0 and len(ps) > 1:
            largest = max(p["w"] * p["h"] for p in ps)
            max_secondary = max(max_secondary, (area - largest) / area)
        if cat in _NO_MAX_CATEGORIES:
            continue
        mx = (max_overrides or {}).get(name) or max_area_for_room(name, unit, garage_cars)
        mn = min_area_for_room(name, unit, garage_cars)
        ratios[name] = round(area / mx, 3) if mx else 0.0
        if area > 1.10 * mx:
            oversized.append(name)
        if area < 0.98 * mn:
            undersized.append(name)

    # Big-plot pass: bedrooms whose largest part is longer than
    # BEDROOM_ASPECT_HARD:1, and staircases over STAIR_MAX_AREA_FACTOR x min.
    bedroom_aspect_fail = [
        str(r.get("name") or "") for r in rects
        if classify_room_category(str(r.get("name") or "")) == "bedroom" and bedroom_aspect(r) > BEDROOM_ASPECT_HARD
    ]
    stair_fail = [
        str(r.get("name") or "") for r in rects
        if classify_room_category(str(r.get("name") or "")) == "staircase" and stair_over_cap(r, unit)
    ]

    living = max(areas_by_cat.get("living", [0.0]))
    dining = max(areas_by_cat.get("dining", [0.0]))
    inverted = bool(areas_by_cat.get("living") and areas_by_cat.get("dining") and living < dining)

    return {
        "coverage_pct": round(fp_area / (length * width) * 100, 2),
        "yard_top": round(fp["y0"], 2),
        "yard_bottom": round(width - fp["y1"], 2),
        "yard_left": round(fp["x0"], 2),
        "yard_right": round(length - fp["x1"], 2),
        "hard_count": len(hard),
        "soft_count": len(soft),
        "hard_kinds": sorted({v.kind for v in hard}),
        "coverage_gap_fraction": round(1 - covered / fp_area, 5) if fp_area else 0.0,
        "overlap_area": round(overlap, 4),
        "daylight_fail_hard": dl_hard,
        "daylight_fail_soft": dl_soft,
        "oversized": oversized,
        "undersized": undersized,
        "area_ratio_to_max": ratios,
        "living_area": round(living, 1),
        "dining_area": round(dining, 1),
        "hierarchy_inverted": inverted,
        "max_parts": max_parts,
        "max_secondary_fraction": round(max_secondary, 3),
        "single_floor_stair": bool(
            total_floors == 1
            and any(classify_room_category(str(r.get("name") or "")) == "staircase" for r in rects)
        ),
        "bedroom_aspect_fail": bedroom_aspect_fail,
        "stair_over_cap": stair_fail,
        **_circulation(rects, plot_dims, facing, floor_number, unit),
    }


def _circulation(rects, plot_dims, facing, floor_number, unit) -> dict:
    if facing is None:
        return {"unreachable": None, "pass_through_private": None, "entry_is_garage": None,
                "entry_missing": None, "dead_end_hall_len": None}
    from app.pipeline.door_graph import circulation_report

    rep = circulation_report(rects, plot_dims, facing, floor_number)
    metre = to_plot_unit(1.0, unit)
    return {
        "unreachable": rep["unreachable"],
        "pass_through_private": rep["pass_through_private"],
        # Lists (not booleans) so the gate's len()-based ratchet applies.
        "entry_is_garage": ["__floor__"] if rep["entry_is_garage"] else [],
        "entry_missing": ["__floor__"] if rep["entry_missing"] else [],
        "dead_end_hall_len": round(rep["dead_end_hall_len"] / metre, 2),  # metres
        "circulation_root": f"{rep['root']} ({rep['root_how']})",
    }
