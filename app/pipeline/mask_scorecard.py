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
from app.pipeline.house_planner import built_area_cap, program_bounds
from app.pipeline.layout_metrics import measure_floor
from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    max_area_for_room,
    to_plot_unit,
)

CIRCULATION_NOT_MEASURED = "NOT MEASURED YET (Phase 4)"
# Phase 4b: circulation is measured (door_graph.circulation_report) and
# scored as its own check. Kept OUT of the legacy-6 / all-8 totals so those
# numbers stay comparable with earlier phases; a third total includes it.

# Check ids, in report order. "circulation" is printed but never scored.
# LEGACY_CHECKS are the six Phase 2/3 checks (the "X/210" number); the big-
# plot pass adds bedroom aspect and the explicit stair-size cap.
LEGACY_CHECKS = ("coverage", "yards", "sizes", "hard", "parts", "stair")
NEW_CHECKS = ("aspect", "stair_cap")
SCORED_CHECKS = LEGACY_CHECKS + NEW_CHECKS
CHECK_TITLES = {
    "coverage": "1. Footprint scales with plot (coverage within [~0.5x band target, band max])",
    "yards": "2. Balanced yards on small/medium plots (sides <= 2x, front/rear sane)",
    "sizes": "3. No oversized rooms (<=1.10x max; stair/garage <=1.5x), living>=dining, bath<=80sqft, dining<=180sqft",
    "hard": "4. Zero hard violations (incl. daylight, narrow, door_too_narrow)",
    "parts": "5. Parts per room <= 2 (informational until Phase 3, still scored)",
    "stair": "7. Single-floor houses have no staircase",
    "aspect": f"9. Bedrooms: largest part <= {rules.BEDROOM_ASPECT_HARD}:1 (new; first baseline)",
    "stair_cap": f"10. Staircase <= {rules.STAIR_MAX_AREA_FACTOR}x its minimum area (new; first baseline)",
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


def building_ceiling(
    floors_rooms: list[list[dict]], unit: str, garage_cars, tier: str = "standard",
    band_name: str | None = None,
) -> tuple[float, float]:
    """(program_min, program_ceiling) for the whole building - same rule the
    planner uses: ceiling governed by the ground floor, never below the
    largest floor minimum. With `band_name` (planner mode) the ceiling is
    also limited by the estate built-area cap (per floor), like the planner."""
    bounds = [program_bounds(r, unit, garage_cars, i + 1, tier=tier) for i, r in enumerate(floors_rooms)]
    pmin = max(b[0] for b in bounds)
    ceiling = max(bounds[0][1], pmin)
    if band_name is not None:
        cap = built_area_cap(floors_rooms, band_name, unit)
        if cap:
            ceiling = max(min(ceiling, cap / max(len(floors_rooms), 1)), pmin)
    return pmin, ceiling


def score_floor(
    rects: list[dict],
    plot: dict,
    facing: str,
    floors_rooms: list[list[dict]],
    floor_number: int,
    garage_cars: int | None = None,
    max_overrides: dict | None = None,
    remainder_names: list | None = None,
    planner: bool = False,
) -> dict:
    """Scores one rendered floor. `floors_rooms` is the REQUESTED program for
    every floor (used for the program ceiling); `max_overrides` maps room
    name -> max area (planner tier maxima and remainder sinks).
    `remainder_names` lists the planner's remainder rooms (Terrace,
    Verandah) whose size is intentionally the leftover - listed, not counted
    as oversized; None = every override key (the Phase 2/3 behaviour).
    `planner` = score with the planner's tier-aware ceiling/caps (the
    flag-off report passes False and is unchanged)."""
    unit = plot.get("unit") or "ft"
    length, width = float(plot["length"]), float(plot["width"])
    total = len(floors_rooms)
    m = measure_floor(rects, plot, total_floors=total, floor_number=floor_number,
                      garage_cars=garage_cars, max_overrides=max_overrides, facing=facing)

    exterior = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
    net_area = max(length - 2 * exterior, 0.0) * max(width - 2 * exterior, 0.0)
    band = rules.plot_band(net_area, unit)
    tier = rules.size_tier(band.name) if planner else "standard"
    adjust = rules.floor_count_coverage_adjust(total)
    fp_l = length - m["yard_left"] - m["yard_right"]
    fp_w = width - m["yard_top"] - m["yard_bottom"]
    fp_area = max(fp_l, 0.0) * max(fp_w, 0.0)
    cov = fp_area / net_area if net_area else 0.0
    _pmin, ceiling = building_ceiling(
        floors_rooms, unit, garage_cars, tier, band.name if planner else None
    )
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
        "tier": tier,
        "flag_low": cov * 100 < LOW_COVERAGE_FLAG_PCT,
        "open_area": round(sum(_area(r) for r in rects if classify_room_category(str(r.get("name") or "")) == "porch"), 1),
        "footprint_area": round(fp_area, 1),
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
    overrides = set(remainder_names if remainder_names is not None else (max_overrides or {}).keys())
    bath_cap = rules.BATH_CAP_SQFT_BY_TIER.get(tier, 80.0)
    oversized = [n for n in m["oversized"] if n not in overrides]
    big_baths, big_dining, big_pinned = [], [], []
    floor_area = fp_area
    dining_waived = floor_area >= _sqft(rules.HIERARCHY_RULES["dining_cap_waived_floor_sqft"], unit)
    for r in rects:
        name = str(r.get("name") or "")
        cat = classify_room_category(name)
        a = _area(r)
        if cat == "bathroom" and a > _sqft(bath_cap, unit) * _CAP_TOL:
            big_baths.append(f"{name}={a:.0f}")
        if cat in ("staircase", "garage"):
            pinned_max = (max_overrides or {}).get(name) if cat == "garage" else None
            pinned_max = pinned_max or max_area_for_room(name, unit, garage_cars)
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
    res["aspect"] = {"pass": not m["bedroom_aspect_fail"], "offenders": list(m["bedroom_aspect_fail"])}
    stair_detail = [f"{r['name']}={_area(r):.0f}" for r in rects if str(r.get("name") or "") in m["stair_over_cap"]]
    res["stair_cap"] = {"pass": not m["stair_over_cap"], "offenders": stair_detail}
    if m.get("unreachable") is None:
        res["circulation"] = {"pass": True, "measured": False}
    else:
        bad = {
            "unreachable": list(m["unreachable"]),
            "pass_through_private": list(m["pass_through_private"]),
            "entry_is_garage": bool(m["entry_is_garage"]),
            "entry_missing": bool(m["entry_missing"]),
        }
        res["circulation"] = {
            "pass": not (bad["unreachable"] or bad["pass_through_private"] or bad["entry_is_garage"] or bad["entry_missing"]),
            "measured": True, **bad,
            "dead_end_hall_m": m["dead_end_hall_len"], "root": m.get("circulation_root"),
        }
    res["clean"] = all(res[c]["pass"] for c in SCORED_CHECKS)
    res["clean_all"] = res["clean"] and res["circulation"]["pass"]
    res["clean_legacy"] = all(res[c]["pass"] for c in LEGACY_CHECKS)
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


PATH_KINDS = ("rectified", "reference_reconnect", "reference_raw")
# Only these two mean "the Phase 3 rectified candidate lost and we fell back
# to Phase 2's pick"; circ_* kinds are Phase 4b circulation variants.
FALLBACK_PATH_KINDS = ("reference_reconnect", "reference_raw")
# Planner-only extra-candidate paths that exist to WORK AROUND a problem in the
# main growth path rather than fix it (user decision: accepted as workarounds,
# not the cure). How often they win is reported; only suite_guard is ratcheted.
WORKAROUND_PATH_KINDS = ("suite_guard", "circ_arms", "circ_stair")


def workaround_path_floors(paths: dict) -> dict[str, list[str]]:
    """{kind: [floor keys]} for each WORKAROUND_PATH_KINDS entry. A path counts
    for a kind when it equals it or starts with "<kind>+" (e.g. "circ_arms+stair"
    is a circ_arms pick; a floor that is circ_stair only counts for circ_stair)."""
    out: dict[str, list[str]] = {k: [] for k in WORKAROUND_PATH_KINDS}
    for key, path in paths.items():
        path = path or ""
        for kind in WORKAROUND_PATH_KINDS:
            if path == kind or path.startswith(kind + "+"):
                out[kind].append(key)
    return out


def suite_guard_count(paths: dict) -> int:
    return len(workaround_path_floors(paths)["suite_guard"])


def format_scorecard(results: dict, label: str, capacity: dict | None = None, paths: dict | None = None) -> str:
    """results: {key: score_floor() dict}. `paths` ({key: layout_path},
    planner mode) adds the Phase-3 fallback-rate line. Returns the
    printable report."""
    lines = [f"=== MASK SCORECARD [{label}] ===", f"floors scored: {len(results)}", ""]
    keys = list(results)
    total_pass = 0
    new_pass = 0
    for check in SCORED_CHECKS:
        failing = [k for k in keys if not results[k][check]["pass"]]
        n_pass = len(keys) - len(failing)
        if check in LEGACY_CHECKS:
            total_pass += n_pass
        else:
            new_pass = new_pass + n_pass
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
            elif check in ("aspect", "stair_cap"):
                detail = ",".join(r["offenders"])
            else:
                detail = "staircase on a single-floor house"
            lines.append(f"    FAIL {k}: {detail}")
    measured = [k for k in keys if results[k].get("circulation", {}).get("measured")]
    if not measured:
        lines.append(f"6. Circulation (reachability): {CIRCULATION_NOT_MEASURED}")
    else:
        failing = [k for k in measured if not results[k]["circulation"]["pass"]]
        lines.append(f"6. Circulation (reachable from entry; no pass-through bedroom/bath; entry not garage/missing; "
                     f"porch/verandah/terrace excluded): {len(measured) - len(failing)}/{len(keys)} floors pass")
        for k in failing:
            c = results[k]["circulation"]
            bits = []
            if c["unreachable"]:
                bits.append("unreachable " + ",".join(c["unreachable"]))
            if c["pass_through_private"]:
                bits.append("pass-through " + ",".join(c["pass_through_private"]))
            if c["entry_is_garage"]:
                bits.append("ENTRY IS GARAGE")
            if c["entry_missing"]:
                bits.append("entry missing (only the garage touches the facing edge)")
            lines.append(f"    FAIL {k}: {'; '.join(bits)} [from {c['root']}]")
        dead = [(k, results[k]["circulation"]["dead_end_hall_m"]) for k in measured
                if (results[k]["circulation"]["dead_end_hall_m"] or 0) > 1.5]
        lines.append(f"   (soft, informational) dead-end hallway arm > 1.5 m: {len(dead)} floors"
                     + ("" if not dead else ": " + ", ".join(f"{k}={v:.1f}m" for k, v in dead)))
        unr = sum(len(results[k]["circulation"]["unreachable"]) for k in measured)
        pt = sum(len(results[k]["circulation"]["pass_through_private"]) for k in measured)
        eg = sum(1 for k in measured if results[k]["circulation"]["entry_is_garage"])
        em = sum(1 for k in measured if results[k]["circulation"]["entry_missing"])
        lines.append(f"   totals: unreachable rooms {unr}, pass-through rooms {pt}, "
                     f"entry-is-garage floors {eg}, entry-missing floors {em}")
    fb = [(k, results[k]["metrics"]) for k in keys if results[k]["metrics"].get("front_door_fallback") is not None]
    if fb:
        hit = [(k, m["front_door_room"]) for k, m in fb if m["front_door_fallback"]]
        cats: dict[str, int] = {}
        for _k, name in hit:
            c = classify_room_category(name or "")
            cats[c] = cats.get(c, 0) + 1
        lines.append(f"   (ratcheted) front door via fallback (not the foyer/entry): {len(hit)}/{len(keys)} floors"
                     + ("" if not hit else " by room category " + ", ".join(f"{c} {n}" for c, n in sorted(cats.items()))
                        + ": " + "; ".join(f"{k}={n}" for k, n in hit)))
    orphan = [(k, results[k]["metrics"].get("orphaned_ensuites")) for k in keys]
    if any(v is not None for _k, v in orphan):
        hit = [(k, v) for k, v in orphan if v]
        lines.append(f"   (soft, ratcheted) orphaned ensuites (ensuite sharing no wall with its own bedroom; "
                     f"opens onto the hallway instead): {sum(len(v) for _k, v in hit)} rooms on {len(hit)}/{len(keys)} floors"
                     + ("" if not hit else ": " + "; ".join(f"{k}={','.join(v)}" for k, v in hit)))
    lines.append("")
    lines.append("Coverage detail (big plots; FLAG = coverage < 12%):")
    for k in keys:
        if any(p in k for p in ("150x150", "150x200", "300x300")):
            c = results[k]["coverage"]
            flag = "  <-- FLAG <12%" if c["flag_low"] else ""
            lines.append(f"    {k}: coverage {c['coverage_pct']}%  ceiling used {c['ceiling_used_pct']}%"
                         f"  footprint {c.get('footprint_area', 0):.0f}  open(porch/terrace) {c.get('open_area', 0):.0f}"
                         f"  tier {c.get('tier', 'standard')}{flag}")
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
    if paths:
        # Count EVERY path kind that actually occurs (Phase 4b added circ_*
        # kinds that the old fixed three-kind line silently skipped, so it
        # summed to 25 of 35). The per-kind counts must add up to the number
        # of floors - fail loudly if a floor has no recorded path or the
        # counts drift, instead of printing a misleading line.
        seen: dict[str, int] = {}
        for k in keys:
            seen[paths.get(k) or "MISSING"] = seen.get(paths.get(k) or "MISSING", 0) + 1
        if sum(seen.values()) != len(keys) or "MISSING" in seen:
            raise AssertionError(
                f"engine path counts {seen} do not account for all {len(keys)} floors "
                "(a floor has no recorded layout_path)"
            )
        ordered = [p for p in PATH_KINDS if p in seen] + sorted(p for p in seen if p not in PATH_KINDS)
        fallback = sum(seen.get(p, 0) for p in FALLBACK_PATH_KINDS)
        lines.append("")
        lines.append(f"Engine path (all {len(keys)} floors): "
                     + " / ".join(f"{p} {seen[p]}" for p in ordered)
                     + f"  [sum {sum(seen.values())}]")
        wk = workaround_path_floors({k: paths.get(k) for k in keys})
        lines.append(
            f"suite_guard picks: {len(wk['suite_guard'])}/{len(keys)} floors"
            + (f" ({', '.join(wk['suite_guard'])})" if wk["suite_guard"] else "")
            + "  [workaround, ratcheted: may not increase]"
        )
        for kind in ("circ_arms", "circ_stair"):
            lines.append(
                f"{kind} picks: {len(wk[kind])}/{len(keys)} floors"
                + (f" ({', '.join(wk[kind])})" if wk[kind] else "") + "  [workaround, reported only]"
            )
        lines.append(f"Floors using the Phase 2 fallback ({', '.join(FALLBACK_PATH_KINDS)}): {fallback}/{len(keys)}")
    clean = [k for k in keys if results[k]["clean"]]
    clean_legacy = [k for k in keys if results[k].get("clean_legacy", results[k]["clean"])]
    lines.append("")
    lines.append(f"OVERALL (6 legacy checks): {total_pass} of {len(keys) * len(LEGACY_CHECKS)} floor-checks pass; "
                 f"{len(clean_legacy)} of {len(keys)} floors fully clean on them")
    lines.append(f"OVERALL (all {len(SCORED_CHECKS)} checks incl. new aspect/stair cap): "
                 f"{total_pass + new_pass} of {len(keys) * len(SCORED_CHECKS)} floor-checks pass; "
                 f"{len(clean)} of {len(keys)} floors fully clean (pass ALL scored checks)")
    if any(results[k].get("circulation", {}).get("measured") for k in keys):
        circ_pass = sum(1 for k in keys if results[k]["circulation"]["pass"])
        clean_all = [k for k in keys if results[k].get("clean_all")]
        lines.append(f"OVERALL (all {len(SCORED_CHECKS) + 1} checks incl. circulation): "
                     f"{total_pass + new_pass + circ_pass} of {len(keys) * (len(SCORED_CHECKS) + 1)} floor-checks pass; "
                     f"{len(clean_all)} of {len(keys)} floors fully clean")
    return "\n".join(lines)
