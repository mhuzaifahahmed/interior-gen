"""Honest mask-engine SCORECARD (mask planner, Phase 2 step 1).

Scores the mask engine's ACTUAL output against ABSOLUTE targets - one check
per problem the user reported - not "no worse than baseline". This is a
REPORT, never a pass/fail gate: scripts/mask_quality_report.py --scorecard
prints it. Pure - no I/O.

A floor counts as clean only if it passes EVERY scored check (no weighting
that could hide a failure). Circulation/reachability is NOT measured yet
(Phase 4) and is printed as such - never faked.
"""

from app.pipeline import planning_rules as rules
from app.pipeline.house_planner import program_bounds
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    max_area_for_room,
    to_plot_unit,
)

CIRCULATION_NOT_MEASURED = "NOT MEASURED YET (Phase 4)"

# Check ids, in report order. "circulation" is printed but never scored.
SCORED_CHECKS = ("coverage", "yards", "sizes", "hard", "parts", "stair")
CHECK_TITLES = {
    "coverage": "1. Footprint scales with plot (coverage within [~0.5x band target, band max])",
    "yards": "2. Balanced yards on small/medium plots (sides <= 2x, front/rear sane)",
    "sizes": "3. No oversized rooms (<=1.10x max; stair/garage <=1.5x), living>=dining, bath<=80sqft, dining<=180sqft",
    "hard": "4. Zero hard violations (incl. daylight, narrow, door_too_narrow)",
    "parts": "5. Parts per room <= 2 (informational until Phase 3, still scored)",
    "stair": "7. Single-floor houses have no staircase",
}
LOW_COVERAGE_FLAG_PCT = 12.0
_SMALL_MEDIUM_BANDS = ("XS", "S", "M")
_YARD_TOL_FT = 2.0
_SIDE_RATIO = 2.0
_FRONT_REAR_RATIO = 4.0
_CAP_TOL = 1.02  # grid-cell rounding on the 80/180 sq ft caps


def _ft(value_ft: float, unit: str) -> float:
    return to_plot_unit(value_ft / 3.280839895, unit)


def _sqft(value_sqft: float, unit: str) -> float:
    f = to_plot_unit(1.0 / 3.280839895, unit)
    return value_sqft * f * f


def _parts(rect: dict) -> list[dict]:
    parts = rect.get("parts")
    if parts:
        return [p for p in parts if p.get("w", 0) > 0 and p.get("h", 0) > 0]
    return [{"x": rect["x"], "y": rect["y"], "w": rect["w"], "h": rect["h"]}]


def _area(rect: dict) -> float:
    return sum(p["w"] * p["h"] for p in _parts(rect))


def building_ceiling(floors_rooms: list[list[dict]], unit: str, garage_cars) -> tuple[float, float]:
    """(program_min, program_ceiling) for the whole building - same rule the
    planner uses: ceiling governed by the ground floor, never below the
    largest floor minimum."""
    bounds = [program_bounds(r, unit, garage_cars, i + 1) for i, r in enumerate(floors_rooms)]
    pmin = max(b[0] for b in bounds)
    return pmin, max(bounds[0][1], pmin)


def score_floor(
    rects: list[dict],
    plot: dict,
    facing: str,
    floors_rooms: list[list[dict]],
    floor_number: int,
    garage_cars: int | None = None,
    max_overrides: dict | None = None,
) -> dict:
    """Scores one rendered floor. `floors_rooms` is the REQUESTED program for
    every floor (used for the program ceiling); `max_overrides` names
    planner-added remainder rooms (Terrace) whose size is intentionally the
    leftover - they are listed, not counted as oversized."""
    unit = plot.get("unit") or "ft"
    length, width = float(plot["length"]), float(plot["width"])
    total = len(floors_rooms)
    m = measure_floor(rects, plot, total_floors=total, floor_number=floor_number,
                      garage_cars=garage_cars, max_overrides=max_overrides)

    exterior = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
    net_area = max(length - 2 * exterior, 0.0) * max(width - 2 * exterior, 0.0)
    band = rules.plot_band(net_area, unit)
    adjust = rules.floor_count_coverage_adjust(total)
    fp_l = length - m["yard_left"] - m["yard_right"]
    fp_w = width - m["yard_top"] - m["yard_bottom"]
    fp_area = max(fp_l, 0.0) * max(fp_w, 0.0)
    cov = fp_area / net_area if net_area else 0.0
    _pmin, ceiling = building_ceiling(floors_rooms, unit, garage_cars)
    ceiling_cov = ceiling / net_area if net_area else 0.0
    low = 0.5 * band.target_cov * adjust
    low_eff = min(low, 0.9 * ceiling_cov)
    high = band.max_cov
    res: dict = {"metrics": m}
    res["coverage"] = {
        "pass": low_eff - 0.005 <= cov <= high + 0.005,
        "coverage_pct": round(cov * 100, 1),
        "ceiling_used_pct": round(fp_area / ceiling * 100, 1) if ceiling else 0.0,
        "range_pct": (round(low_eff * 100, 1), round(high * 100, 1)),
        "band": band.name,
        "flag_low": cov * 100 < LOW_COVERAGE_FLAG_PCT,
    }

    # Yards: map plot-side yards to front/rear/side by facing.
    sides = {"top": m["yard_top"], "bottom": m["yard_bottom"], "left": m["yard_left"], "right": m["yard_right"]}
    front_key = {"north": "top", "south": "bottom", "east": "right", "west": "left"}[facing]
    rear_key = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}[front_key]
    cross = [sides[k] for k in sides if k not in (front_key, rear_key)]
    front, rear = sides[front_key], sides[rear_key]
    tol = _ft(_YARD_TOL_FT, unit)
    side_ok = max(cross) <= _SIDE_RATIO * min(cross) + tol
    fr_ok = (max(front, rear) <= _FRONT_REAR_RATIO * min(front, rear) + tol)
    applies = band.name in _SMALL_MEDIUM_BANDS
    res["yards"] = {
        "pass": (side_ok and fr_ok) if applies else True,
        "applies": applies,
        "front": round(front, 1), "rear": round(rear, 1),
        "sides": [round(c, 1) for c in cross],
    }

    # Sizes.
    overrides = set((max_overrides or {}).keys())
    oversized = [n for n in m["oversized"] if n not in overrides]
    big_baths, big_dining, big_pinned = [], [], []
    floor_area = fp_area
    dining_waived = floor_area >= _sqft(rules.HIERARCHY_RULES["dining_cap_waived_floor_sqft"], unit)
    for r in rects:
        name = str(r.get("name") or "")
        cat = classify_room_category(name)
        a = _area(r)
        if cat == "bathroom" and a > _sqft(80, unit) * _CAP_TOL:
            big_baths.append(f"{name}={a:.0f}")
        if cat in ("staircase", "garage"):
            pinned_max = max_area_for_room(name, unit, garage_cars)
            if a > 1.5 * pinned_max:
                big_pinned.append(f"{name}={a:.0f}")
        if cat == "dining" and not dining_waived and a > _sqft(rules.HIERARCHY_RULES["dining_cap_sqft"], unit) * _CAP_TOL:
            big_dining.append(f"{name}={a:.0f}")
    remainder = [f"{r['name']}={_area(r):.0f}" for r in rects if str(r.get("name") or "") in overrides]
    res["sizes"] = {
        "pass": not oversized and not m["hierarchy_inverted"] and not big_baths and not big_dining and not big_pinned,
        "oversized": oversized,
        "inverted": m["hierarchy_inverted"],
        "living": m["living_area"], "dining": m["dining_area"],
        "big_baths": big_baths, "big_dining": big_dining, "big_pinned": big_pinned,
        "remainder_rooms": remainder,
    }

    res["hard"] = {
        "pass": m["hard_count"] == 0 and not m["daylight_fail_hard"],
        "hard_kinds": m["hard_kinds"], "hard_count": m["hard_count"],
        "daylight_fail_hard": m["daylight_fail_hard"],
    }

    room_parts = {str(r.get("name") or ""): len(_parts(r)) for r in rects if str(r.get("name") or "").lower() != "hallway"}
    max_parts = max(room_parts.values(), default=0)
    res["parts"] = {
        "pass": max_parts <= rules.SHAPE_RULES["max_parts"],
        "max_parts": max_parts,
        "offenders": [f"{n}={p}" for n, p in room_parts.items() if p > rules.SHAPE_RULES["max_parts"]],
    }
    res["stair"] = {"pass": not m["single_floor_stair"]}
    res["circulation"] = CIRCULATION_NOT_MEASURED
    res["clean"] = all(res[c]["pass"] for c in SCORED_CHECKS)
    return res


def capacity_summary() -> dict:
    """Max bedroom/bathroom counts the capacity table advertises for 1 and 2
    floors (ft). Uses whatever engine/planner setting is active."""
    from app.pipeline.feasibility import room_count_requirements_table

    out = {}
    for floors in (1, 2):
        room_count_requirements_table.cache_clear()
        t = room_count_requirements_table(floors, "ft")
        out[floors] = {
            "bedrooms": max((e["count"] for e in t["bedrooms"]), default=0),
            "bathrooms": max((e["count"] for e in t["bathrooms"]), default=0),
        }
    room_count_requirements_table.cache_clear()
    return out


def format_scorecard(results: dict, label: str, capacity: dict | None = None) -> str:
    """results: {key: score_floor() dict}. Returns the printable report."""
    lines = [f"=== MASK SCORECARD [{label}] ===", f"floors scored: {len(results)}", ""]
    keys = list(results)
    total_pass = 0
    for check in SCORED_CHECKS:
        failing = [k for k in keys if not results[k][check]["pass"]]
        n_pass = len(keys) - len(failing)
        total_pass += n_pass
        lines.append(f"{CHECK_TITLES[check]}: {n_pass}/{len(keys)} floors pass")
        for k in failing:
            r = results[k][check]
            if check == "coverage":
                detail = f"cov {r['coverage_pct']}% not in {r['range_pct']} (band {r['band']}, ceiling used {r['ceiling_used_pct']}%)"
            elif check == "yards":
                detail = f"front {r['front']} rear {r['rear']} sides {r['sides']}"
            elif check == "sizes":
                bits = []
                if r["oversized"]:
                    bits.append("oversized " + ",".join(r["oversized"]))
                if r["inverted"]:
                    bits.append(f"living {r['living']} < dining {r['dining']}")
                if r["big_baths"]:
                    bits.append("bath>80 " + ",".join(r["big_baths"]))
                if r["big_dining"]:
                    bits.append("dining>180 " + ",".join(r["big_dining"]))
                if r["big_pinned"]:
                    bits.append("stair/garage>1.5x size " + ",".join(r["big_pinned"]))
                detail = "; ".join(bits)
            elif check == "hard":
                detail = f"{r['hard_count']} hard {r['hard_kinds']} daylight {r['daylight_fail_hard']}"
            elif check == "parts":
                detail = f"max {r['max_parts']}: " + ",".join(r["offenders"])
            else:
                detail = "staircase on a single-floor house"
            lines.append(f"    FAIL {k}: {detail}")
    lines.append(f"6. Circulation (reachability): {CIRCULATION_NOT_MEASURED}")
    lines.append("")
    lines.append("Coverage detail (big plots; FLAG = coverage < 12%):")
    for k in keys:
        if any(p in k for p in ("150x150", "150x200", "300x300")):
            c = results[k]["coverage"]
            flag = "  <-- FLAG <12%" if c["flag_low"] else ""
            lines.append(f"    {k}: coverage {c['coverage_pct']}%  ceiling used {c['ceiling_used_pct']}%{flag}")
    remainder = [(k, results[k]["sizes"]["remainder_rooms"]) for k in keys if results[k]["sizes"]["remainder_rooms"]]
    if remainder:
        lines.append("Planner remainder rooms (sized to leftover area, excluded from 'oversized'):")
        for k, rs in remainder:
            lines.append(f"    {k}: {', '.join(rs)}")
    if capacity is not None:
        lines.append("")
        lines.append("8. Capacity table (feasibility.room_count_requirements_table, ft):")
        for floors, c in capacity.items():
            lines.append(f"    {floors} floor(s): max bedrooms {c['bedrooms']}, max bathrooms {c['bathrooms']}")
    clean = [k for k in keys if results[k]["clean"]]
    lines.append("")
    lines.append(f"OVERALL: {total_pass} of {len(keys) * len(SCORED_CHECKS)} floor-checks pass; "
                 f"{len(clean)} of {len(keys)} floors fully clean (pass ALL scored checks)")
    return "\n".join(lines)
